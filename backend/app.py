"""Concord API: the async audit job, commit/override/replace, the record, health."""
from __future__ import annotations

import logging
import shutil
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import adjudicate
import contradict
import ingest
import retrieve
from db import UPLOAD_DIR, get_store, new_id, now
from policy import CONCORD, CONFIG, CONFLICT, DUPLICATE, REVIEW, aggregate, config_dict, laya_triage

log = logging.getLogger("concord")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------- model loading

MODELS = {"embedder": False, "reranker": False, "laya": False}
MODELS_READY = threading.Event()
MODEL_ERROR: list[str] = []


def _load_models() -> None:
    try:
        ingest.get_embedder(); MODELS["embedder"] = True
        retrieve.get_reranker(); MODELS["reranker"] = True
        contradict.get_agent(); MODELS["laya"] = True
        log.info("models loaded")
    except Exception as e:  # surfaced on /health
        MODEL_ERROR.append(f"{type(e).__name__}: {e}")
        log.exception("model load failed")
    finally:
        MODELS_READY.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store()
    threading.Thread(target=_load_models, daemon=True).start()
    yield


app = FastAPI(title="Concord", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# One audit at a time: the models are CPU-bound and share one process.
EXECUTOR = ThreadPoolExecutor(max_workers=1)
JOBS: dict[str, dict] = {}


# ---------------------------------------------------------------- the audit

def _progress(job: dict, stage: str, progress: float, message: str) -> None:
    job.update(stage=stage, progress=round(progress, 3), message=message)


def _citation(chunk: dict) -> str:
    parts = [chunk["filename"]]
    if chunk.get("section_path"):
        parts.append(chunk["section_path"].split(" > ")[-1])
    if chunk.get("page"):
        parts.append(f"p.{chunk['page']}")
    return ", ".join(parts)


def _norm(s: str) -> str:
    return " ".join(s.lower().split()).rstrip(".")


def run_audit(job_id: str) -> None:
    job = JOBS[job_id]
    store = get_store()
    upload = store.one("SELECT * FROM upload WHERE id = ?", (job["upload_id"],))
    try:
        if not MODELS_READY.is_set():
            _progress(job, "loading", 0.0, "Loading models")
            MODELS_READY.wait()
        if MODEL_ERROR:
            raise RuntimeError(MODEL_ERROR[0])

        n_docs = store.document_count()
        job["documents_total"] = n_docs
        duplicate = store.find_by_sha(upload["sha256"])
        if duplicate:
            _reject_duplicate(job, upload, duplicate, "identical")
            return

        _progress(job, "reading", 0.02, "Reading the upload")
        claims = ingest.extract_claims(upload["path"])
        job["claims_total"] = len(claims)
        duplicate = store.find_same_claims({_norm(c["claim_text"]) for c in claims}, _norm)
        if duplicate:
            _reject_duplicate(job, upload, duplicate, "same_claims")
            return
        if not claims:
            _finish(job, upload, [], 0, 0)
            return

        _progress(job, "retrieving", 0.06, f"Auditing against {n_docs} documents")
        vectors = ingest.embed([c["claim_text"] for c in claims])
        candidates = retrieve.candidates_for_claims([c["claim_text"] for c in claims], vectors)

        # Pairs below the subject floor can neither flag nor enter review: skip Laya for them.
        work = []
        for claim, cands in zip(claims, candidates):
            for cand in cands:
                if cand["similarity"] < CONFIG.subject_floor:
                    continue
                if _norm(cand["claim_text"]) == _norm(claim["claim_text"]):
                    continue  # identical statement, cannot contradict
                work.append((claim, cand))
        job["pairs_total"] = len(work)

        flagged: list[dict] = []
        step = contradict.BATCH_SIZE
        for start in range(0, len(work), step):
            _progress(job, "checking", 0.12 + 0.68 * (start / max(len(work), 1)),
                      f"Auditing against {n_docs} documents")
            batch = work[start : start + step]
            try:
                results: list[dict | None] = contradict.check_pairs(
                    [(cand["claim_text"], claim["claim_text"]) for claim, cand in batch]
                )
            except Exception as e:
                log.warning("laya failed on a batch: %s", e)
                results = [None] * len(batch)
            for (claim, cand), r in zip(batch, results):
                pair = _triage_pair(claim, cand, r)
                if pair:
                    flagged.append(pair)

        pairs = _adjudicate(job, flagged, n_docs)
        _finish(job, upload, pairs, len(claims), len(work))
    except Exception as e:
        log.error("audit failed: %s", traceback.format_exc())
        job.update(status="error", error=f"{type(e).__name__}: {e}", stage="error")


def _triage_pair(claim: dict, cand: dict, r: dict | None) -> dict | None:
    """Apply the gate to one pair. A failed Laya call goes to Review, never to concord."""
    pair = {
        "new_claim": claim["raw_text"],
        "new_section_path": claim["section_path"],
        "new_page": claim["page"],
        "existing": cand,
        "same_subject": cand["similarity"], "contradicts": None, "confidence": None,
        "numeric": None, "llm": None, "laya_flag": False,
    }
    numeric, values = contradict.compare_quantities(cand["claim_text"], claim["claim_text"], CONFIG.numeric_rel_tol)
    pair["numeric"] = list(values) if values else None
    if r is None:
        pair["triage"] = "review"
        pair["error"] = "The contradiction check failed on this pair."
        return pair
    pair.update(r)
    triage = laya_triage(cand["similarity"], r["contradicts"], r["confidence"], numeric)
    if not triage:
        return None
    pair["triage"] = triage
    pair["laya_flag"] = cand["similarity"] >= CONFIG.tau_subject and r["contradicts"] >= CONFIG.tau_contra
    return pair


def _adjudicate(job: dict, flagged: list[dict], n_docs: int) -> list[dict]:
    """LLM confirms + explains the flagged pairs. Returns the surviving pairs with a severity."""
    gray = sorted((p for p in flagged if p["triage"] == "review" and not p.get("error")),
                  key=lambda p: -(p["contradicts"] or 0))
    to_llm = [p for p in flagged if p["triage"] == "flag"] + gray[: CONFIG.gray_llm_cap]
    survivors: list[dict] = [
        dict(p, severity=REVIEW) for p in flagged if p["triage"] == "review" and p not in to_llm
    ]
    for i, p in enumerate(to_llm):
        _progress(job, "adjudicating", 0.8 + 0.18 * (i / max(len(to_llm), 1)),
                  f"Confirming {len(to_llm)} flagged passage{'s' if len(to_llm) != 1 else ''}")
        ex = p["existing"]
        hint = ""
        if p["numeric"]:
            hint = (f'The passages mention "{p["numeric"][0]}" and "{p["numeric"][1]}". These only conflict '
                    "if they are values of the same thing.")
        verdict = None
        try:
            verdict = adjudicate.adjudicate(p["new_claim"], ex["raw_text"], _citation(ex), hint)
        except Exception as e:
            log.warning("llm failed: %s", e)
        p["llm"] = verdict
        if p["triage"] == "review":
            # Gray zone: the LLM may clear it, but can never escalate it to a block.
            severity = None if verdict and not verdict["is_conflict"] else REVIEW
        elif verdict is None:
            severity = REVIEW                      # no second opinion: never hard-block
        elif verdict["is_conflict"] and verdict["confidence"] >= CONFIG.llm_min_confidence:
            severity = CONFLICT
        elif verdict["is_conflict"]:
            severity = REVIEW                      # LLM uncertain
        elif p["laya_flag"] and p["contradicts"] >= CONFIG.strong_contra and p["same_subject"] >= CONFIG.strong_subject:
            severity = REVIEW                      # strong Laya signal the LLM disputes: a human decides
        else:
            severity = None                        # Laya over-fired; LLM cleared it
        if severity:
            survivors.append(dict(p, severity=severity))
    return survivors


def _reject_duplicate(job: dict, upload: dict, doc: dict, match: str) -> None:
    """A file already in the record is rejected outright; it can never be committed a second time."""
    job["duplicate_of"] = {"id": doc["id"], "filename": doc["filename"], "match": match}
    _finish(job, upload, [], job.get("claims_total", 0), 0, decision=DUPLICATE)


def _finish(job: dict, upload: dict, pairs: list[dict], n_claims: int, n_pairs: int,
            decision: str | None = None) -> None:
    store = get_store()
    decision = decision or aggregate(pairs)
    report_id = new_id()
    store.execute(
        "INSERT INTO conflict_report (id, upload_id, decision, created_at) VALUES (?, ?, ?, ?)",
        (report_id, upload["id"], decision, now()),
    )
    findings = []
    for p in sorted(pairs, key=lambda p: (p["severity"] != CONFLICT, -(p["contradicts"] or 0))):
        ex = p["existing"]
        pid = new_id()
        llm = p.get("llm") or {}
        store.execute(
            "INSERT INTO conflict_pair (id, report_id, new_claim, new_section_path, new_page, "
            "existing_chunk_id, same_subject, contradicts, laya_confidence, numeric_flag, "
            "llm_verdict, llm_kind, llm_explanation, severity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, report_id, p["new_claim"], p["new_section_path"], p["new_page"], ex["id"],
             p["same_subject"], p["contradicts"], p["confidence"], int(bool(p["numeric"])),
             None if not llm else ("conflict" if llm.get("is_conflict") else "none"),
             llm.get("kind"), llm.get("explanation"), p["severity"]),
        )
        findings.append({
            "id": pid,
            "severity": p["severity"],
            "title": _title(ex, p),
            "new_claim": p["new_claim"],
            "new_section_path": p["new_section_path"],
            "new_page": p["new_page"],
            "existing": {
                "chunk_id": ex["id"], "document_id": ex["document_id"], "filename": ex["filename"],
                "section_path": ex["section_path"], "page": ex["page"], "raw_text": ex["raw_text"],
            },
            "same_subject": p["same_subject"],
            "contradicts": p["contradicts"],
            "confidence": _figure(p),
            "numeric": p["numeric"],
            "explanation": llm.get("explanation"),
            "kind": llm.get("kind"),
            "llm_checked": bool(llm),
            "note": p.get("error"),
        })
    store.execute(
        "UPDATE upload SET status = 'checked', decision = ? WHERE id = ?", (decision, upload["id"])
    )
    by_doc: dict[str, dict] = {}
    for f in findings:
        d = by_doc.setdefault(f["existing"]["document_id"], {
            "document_id": f["existing"]["document_id"], "filename": f["existing"]["filename"],
            "conflicts": 0, "reviews": 0,
        })
        d["conflicts" if f["severity"] == CONFLICT else "reviews"] += 1
    job.update(
        status="done", stage="done", progress=1.0, verdict=decision, report_id=report_id,
        conflicts=findings, documents=list(by_doc.values()),
        claims_checked=n_claims, pairs_checked=n_pairs, message="Audit complete",
    )


def _title(ex: dict, p: dict) -> str:
    for path in (ex.get("section_path"), p.get("new_section_path")):
        if path:
            title = path.split(" > ")[-1]
            return title.lstrip("#0123456789. ").strip() or title
    return "Conflicting statement"


def _figure(p: dict) -> float | None:
    llm = p.get("llm")
    if llm and llm.get("is_conflict"):
        return round(llm["confidence"], 2)
    if p["contradicts"] is not None:
        return round(min(p["same_subject"], max(p["contradicts"], 0.0)), 2)
    return None


# ---------------------------------------------------------------- endpoints

def _save_upload(file: UploadFile) -> tuple[Path, str]:
    name = Path(file.filename or "upload.txt").name
    if Path(name).suffix.lower() not in ingest.SUPPORTED:
        raise HTTPException(415, f"Unsupported file type. Use one of: {', '.join(sorted(ingest.SUPPORTED))}")
    folder = UPLOAD_DIR / new_id()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest, name


@app.get("/health")
def health():
    return {
        "ok": True,
        "models": MODELS,
        "ready": MODELS_READY.is_set() and not MODEL_ERROR,
        "error": MODEL_ERROR[0] if MODEL_ERROR else None,
        "llm": {"available": adjudicate.available(), "model": adjudicate.OLLAMA_MODEL},
        "documents": get_store().document_count(),
        "config": config_dict(),
    }


@app.get("/documents")
def list_documents():
    return {"documents": get_store().list_documents()}


@app.get("/documents/{doc_id}")
def get_document(doc_id: str):
    doc = get_store().get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return doc


@app.post("/documents")
def add_document(file: UploadFile = File(...)):
    path, name = _save_upload(file)
    doc_id, created = ingest.ingest_file(path, name)
    return {"id": doc_id, "created": created}


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    if not get_store().delete_document(doc_id):
        raise HTTPException(404, "Document not found")
    return {"deleted": doc_id}


@app.post("/uploads/check")
def start_check(file: UploadFile = File(...)):
    path, name = _save_upload(file)
    store = get_store()
    upload_id = new_id()
    store.execute(
        "INSERT INTO upload (id, filename, sha256, path, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
        (upload_id, name, ingest.sha256_file(path), str(path), now()),
    )
    job_id = new_id()
    JOBS[job_id] = {
        "job_id": job_id, "upload_id": upload_id, "filename": name, "status": "running",
        "stage": "queued", "progress": 0.0, "message": "Queued",
        "documents_total": store.document_count(), "verdict": None, "conflicts": [],
        "documents": [], "duplicate_of": None,
    }
    EXECUTOR.submit(run_audit, job_id)
    return {"job_id": job_id, "upload_id": upload_id}


@app.get("/uploads/check/{job_id}")
def check_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job. The backend may have restarted; upload the file again.")
    return job


class CommitRequest(BaseModel):
    upload_id: str
    action: str = "commit"                  # commit | override | replace
    reason: str | None = None
    replace_document_ids: list[str] = []


@app.post("/uploads/commit")
def commit(req: CommitRequest):
    store = get_store()
    upload = store.one("SELECT * FROM upload WHERE id = ?", (req.upload_id,))
    if not upload:
        raise HTTPException(404, "Upload not found")
    if upload["status"] == "committed":
        return {"document_id": upload["document_id"], "status": "committed"}
    if upload["status"] != "checked":
        raise HTTPException(409, "This upload has not finished its audit")

    decision = upload["decision"]
    if decision == DUPLICATE:
        raise HTTPException(409, "This file is already in the record. A second copy cannot be added.")
    if req.action == "commit" and decision == CONFLICT:
        raise HTTPException(409, "This upload conflicts with the record. Replace the conflicting file or override with a reason.")
    if req.action == "override" and not (req.reason or "").strip():
        raise HTTPException(422, "An override requires a reason")
    if req.action == "replace":
        if not req.replace_document_ids:
            raise HTTPException(422, "Name the documents to replace")
        for doc_id in req.replace_document_ids:
            store.delete_document(doc_id)

    doc_id, _ = ingest.ingest_file(upload["path"], upload["filename"])
    store.execute(
        "UPDATE upload SET status = 'committed', document_id = ?, override_reason = ? WHERE id = ?",
        (doc_id, (req.reason or "").strip() or None, upload["id"]),
    )
    if req.action == "override":
        log.info("override upload=%s file=%s reason=%r", upload["id"], upload["filename"], req.reason)
    return {"document_id": doc_id, "status": "committed", "action": req.action}


@app.post("/uploads/{upload_id}/cancel")
def cancel(upload_id: str):
    store = get_store()
    upload = store.one("SELECT * FROM upload WHERE id = ?", (upload_id,))
    if not upload:
        raise HTTPException(404, "Upload not found")
    if upload["status"] != "committed":
        store.execute("UPDATE upload SET status = 'cancelled' WHERE id = ?", (upload_id,))
        shutil.rmtree(Path(upload["path"]).parent, ignore_errors=True)
    return {"status": "cancelled"}


@app.get("/overrides")
def overrides():
    return {"overrides": get_store().query(
        "SELECT id, filename, decision, override_reason, document_id, created_at FROM upload "
        "WHERE override_reason IS NOT NULL ORDER BY created_at DESC"
    )}
