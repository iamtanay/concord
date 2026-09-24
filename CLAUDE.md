# CLAUDE.md — CONCORD

> Context file for Claude Code. Read this fully before writing any code.
> **Concord** is a consistency gate for a knowledge base. When a new file is uploaded, Concord
> checks it against the existing record and blocks it if it contradicts something already there —
> naming the exact file and passage in conflict, so the user can revise, replace, or override.
>
> Promise: *nothing enters the record that contradicts it.*
>
> Target dev machine: **CPU-only** (no NVIDIA GPU). Plan every latency budget around CPU.
>
> **This is a local proof-of-concept.** Everything runs on one machine — the vector store, the
> models, the API, and the UI. No cloud services, no containers, no deployment, no accounts.

---

## 1. The problem, and the shape of the solution

Comparing one new file against hundreds of existing ones is not one model's job. It's three stages,
each using the right tool:

| Stage | Job | Tool |
|-------|-----|------|
| **Retrieval** | Find the handful of existing passages a new claim could possibly conflict with | RAG (embeddings + keyword + rerank) |
| **Adjudication (fast)** | Decide, per candidate pair, whether the two claims contradict | **Laya** (contradiction check, one forward pass) |
| **Adjudication (final)** | Confirm the real conflicts and explain them in the user's terms | An LLM, on flagged pairs only |

The insight: retrieval turns "check against the whole KB" into "check against ~5 relevant passages
per claim." Laya then does the cheap, high-volume contradiction calls locally. The LLM only ever
sees the few pairs Laya flags — so the expensive reasoning is rare, and every block is explainable.

Laya cannot do this alone: it has no retrieval, no memory of the KB, and it returns typed decisions,
not explanations. It is the fast reflex in the middle, not the system.

---

## 2. Architecture

```
UPLOAD ──► chunk + extract claims ──► for each new claim:
                                          │
                                          ▼
                             ┌─ hybrid retrieve (dense + BM25) ─┐
                             │        over existing KB          │
                             └───────────────┬──────────────────┘
                                             ▼  rerank → top candidates
                                   Laya: same_subject? contradicts?      (many cheap local calls)
                                             ▼  confidence-gated flags
                                   LLM: confirm + explain the flagged pairs   (few calls)
                                             ▼
                          VERDICT:  In concord  │  Review  │  Conflict (blocked)
                                             ▼
                     commit to index  /  or  cancel · replace · override
```

Two deployable pieces:

- **Backend** (Python): ingestion + retrieval + Laya + LLM + local vector store. It loads an
  embedding model, a reranker, and Laya, so it runs as a **long-lived local process** (`uvicorn`).
  The upload check is an **async job**, never a blocking request.
- **Frontend** (Next.js, local dev server): the console. Holds no models; talks to the backend on
  `localhost`.

---

## 3. Data model

Everything hinges on chunk-level provenance, because the verdict must cite `ABC.pdf, §3.2`.

```
document        (id, filename, sha256, version, status, uploaded_at, uploaded_by)
chunk           (id, document_id, section_path, page, char_start, char_end,
                 raw_text, claim_text, embedding, keywords)
conflict_report (id, upload_id, decision, created_at)
conflict_pair   (id, report_id, new_claim, existing_chunk_id,
                 same_subject, contradicts, laya_confidence,
                 llm_verdict, llm_explanation)
```

Store `sha256` and `version` per document so re-uploads and deletions are idempotent: deleting a
document removes its chunks by `document_id`; replacing it removes the old version's chunks and
indexes the new. `section_path` + `page` + offsets are what make citations exact.

Recommended store (all local, no server to run):
- **Chroma** with a persistent local directory (`chromadb.PersistentClient(path="backend/data/chroma")`)
  for the dense vectors + chunk metadata.
- **SQLite** (a single file under `backend/data/`) for `document`, `conflict_report`, and
  `conflict_pair` rows and all provenance.
- An in-memory **BM25** index (`rank-bm25`) built from the stored claims at startup for keyword
  search — trivially fast for hundreds of files.

Everything is just files on disk; nothing to install as a service or connect to. (Single-file
alternative: `sqlite-vec` + FTS5 keeps vectors, full-text, and metadata in one SQLite file. LanceDB
is another local all-in-one. Chroma is the most beginner-friendly on Windows, so it's the default.)

---

## 4. Stage 1 — Ingestion & indexing (RAG)

Contradictions live at the level of individual claims, not documents. Index accordingly.

**Chunking.** Split each document structurally first (headings → paragraphs), preserving
`section_path` and page. Then split paragraphs into **atomic claims** — one assertion each — because
"Retention is 90 days. Access is role-based." contains two facts that conflict independently. Keep a
sentence of surrounding context on each claim for disambiguation, and store both `raw_text` (for
display/citation) and `claim_text` (the normalized assertion, for matching).

**Claim extraction.** Optionally rewrite each chunk into one or more self-contained claims (resolve
pronouns, attach the subject) with a **local LLM via Ollama** (e.g. `qwen2.5:3b` or `llama3.1:8b`).
It runs once per document at ingest and improves both retrieval and contradiction accuracy. On a CPU
POC it's slow, so it's fine to **skip it for v1** and fall back to sentence-window chunks with ~1
sentence overlap; turn it on later once the pipeline works end to end.

**Embeddings.** Use a strong open-source retrieval model that runs on CPU:
`BAAI/bge-small-en-v1.5` or `thenlper/gte-small` (fast, CPU-friendly) — move up to the `-base`/`-large`
variants if latency allows. Embed `claim_text`. Normalize vectors; use cosine.

**Sparse index.** Also build a Postgres full-text (`tsvector`) or BM25 index over `raw_text`.
Contradictions often turn on specific entities, codes, and numbers that keyword search catches and
dense embeddings blur. You need both.

**Incremental.** Indexing is per-document and idempotent (keyed on `sha256`). A first bulk ingest
handles the existing hundreds of files; every accepted upload appends to the same index.

---

## 5. Stage 2 — Retrieval on upload (candidate generation)

For each claim in the **new** file:

1. **Dense search** — top ~20 existing chunks by cosine similarity on the claim embedding.
2. **Sparse search** — top ~20 by BM25/full-text over the claim's salient terms.
3. **Fuse** — combine with Reciprocal Rank Fusion into one candidate list.
4. **Rerank** — score each (new claim, candidate) pair with a cross-encoder reranker
   (`BAAI/bge-reranker-base`, CPU-ok) and keep the top **N (default 5)**.

Only those N candidates per claim proceed to Laya. This is the step that makes the whole thing
tractable and is the true accuracy ceiling: **if the conflicting passage isn't retrieved here, Laya
never gets to judge it.** Tune chunking, fusion, and N against recall on a labelled set (§11), and
prefer higher recall over precision at this stage — Laya and the LLM filter precision downstream.

---

## 6. Stage 3 — Contradiction check (Laya)

This is the high-volume core: `(new claims × N candidates)` cheap local calls.

**Verified API (laya 0.3.x), CPU, English checkpoint, loaded once at startup:**

```python
import laya
agent = laya.load("convaiinnovations/laya")   # CPU is default; load once, never per request

def check_pair(existing_claim: str, new_claim: str) -> dict:
    state = {"existing": existing_claim, "new": new_claim}
    questions = {
        "same_subject": {
            "type": "noul",
            "instructions": "Both statements describe the same specific fact, policy, entity, or value.",
        },
        "contradicts": {
            "type": "noul",
            "instructions": "The two statements make claims that cannot both be true at the same time.",
        },
    }
    r = agent.predict(state, questions)["answers"]
    return {
        "same_subject": r["same_subject"]["noul"],        # P(true) 0.0–1.0
        "contradicts":  r["contradicts"]["noul"],
        "confidence":   min(r["same_subject"]["confidence"], r["contradicts"]["confidence"]),
    }
```

**Discovery step (run once before wiring policy):** print a raw `agent.predict(...)` result and
confirm the exact keys (`["answers"]["contradicts"]["noul"]`, `["confidence"]`) against the installed
version. Do not assume.

**Why two gated questions, not one.** `contradicts` alone fires on unrelated statements that merely
look opposed. Requiring `same_subject` AND `contradicts` — both high-confidence — removes most false
positives. This is deliberate: a lesson from earlier builds is that merging or under-gating classifier
signals produces confident nonsense (a harmless prompt scored as an attack). Keep the signals
separate and require corroboration.

```
flag as conflict  ⇔  same_subject ≥ τ_subject  AND  contradicts ≥ τ_contra   (defaults 0.70 / 0.75)
```

**Numbers and dates are Laya's blind spot.** NLI-style models are unreliable on "90 vs 30 days" or
"expires 2024 vs 2026." Mitigate deterministically: extract numbers, dates, currencies, and units
from both claims with a parser; when `same_subject` is high and a comparable quantity differs beyond
tolerance, flag it **regardless of Laya's `contradicts`**, and let the LLM confirm the unit/semantics.
Never rely on Laya alone for quantities.

**Use `noul` only here.** It's Laya's strongest, best-calibrated primitive and maps directly to a
threshold. Do not use `score`. Do not add large `choice` questions.

**Latency & batching.** Each `predict` is roughly a few hundred ms to a second-plus on CPU, and there
are many pairs per upload — so this **must** run as an async job with batched calls and a progress
signal, never inside the upload request. Report progress as "Auditing against N documents."

**Fail safe.** On a Laya error or a low-`confidence` result in the gray zone, route the pair to
**Review** (soft), never silently to "in concord."

---

## 7. Stage 4 — Adjudication & explanation (LLM)

The LLM only ever sees pairs Laya flagged — a small set — so it stays cheap even on a local model,
and it does two jobs:

1. **Confirm** the conflict (a second opinion that catches Laya false positives before a block).
2. **Explain** it in the user's terms, with the exact values, for the report.

Prompt shape: give it the new claim and the existing claim with its citation; ask for a strict JSON
verdict:

```json
{ "is_conflict": true,
  "kind": "value_mismatch | mutually_exclusive | status_change | none",
  "explanation": "ABC.pdf §3.2 states retention is 90 days; the new file states 30 days." }
```

If the LLM says `is_conflict: false`, downgrade that pair (Laya over-fired). Only LLM-confirmed pairs
can drive a hard block. Every explanation quotes real values from the passages, never invented ones.

Run this on the **same local Ollama model** used for claim extraction. Because only flagged pairs
reach it, a handful of calls per upload is fine on CPU. (You can swap in a hosted API later by
changing one client — but the POC needs nothing beyond Ollama.)

---

## 8. Decision policy

Per upload, aggregate all pairs into one of three verdicts:

| Verdict | Condition | Behaviour |
|---------|-----------|-----------|
| **Conflict** | ≥ 1 LLM-confirmed, high-confidence conflict | **Block.** Show each conflict grouped by existing file. |
| **Review** | Gray-zone or numeric-only flags, or LLM uncertain | Allow with explicit user confirmation; surface the flags. |
| **In concord** | No surviving flags | Allow; commit to the index. |

Thresholds (`τ_subject`, `τ_contra`, numeric tolerance, review band) live in one config object and
are tuned against your own data (§11). On a blocked upload, the user can: **Cancel upload**,
**Replace the existing file**, or **Upload anyway** (override, with a required reason, logged).

---

## 9. UI design language — restrained, precise, premium

The product is a verification instrument for an institutional archive. It should feel calm,
exact, and expensive through **restraint and typography**, not decoration. It must not look like
the default "generated premium" page. Explicitly avoid: warm cream backgrounds with a high-contrast
serif and a terracotta accent; near-black backgrounds with a bright accent; identical rounded SaaS
cards with uniform soft shadows; ALL-CAPS eyebrow labels; middle-dot meta strings; `WORD — fragment`
labels; arrows appended to buttons; gradient washes. No emoji anywhere in the product.

### Concept
*A precision instrument reading the record.* Near-monochrome ink on cool paper. Colour is scarce and
earns its place: it appears almost only to deliver a **verdict**. The claims under examination are
set in a **serif** — they are "the record," and the serif gives them weight; the surrounding
instrument (navigation, controls, metrics) is a **neutral sans**. That split is the whole identity.

### Tokens
**Colour** (light is primary; ship a dark mode with the same roles):
```
--paper       #FCFCFD   cool near-white (NOT cream)
--paper-sunk  #F4F5F7   recessed surfaces / quoted passages
--ink         #1A1D23   primary text (an intentional dark slate, used consistently)
--ink-soft    #5B616E   secondary text
--line        #E4E6EB   hairline structure, used only where it encodes meaning
--accent      #2C3E66   deep ink-blue: interactive + brand, used sparingly
--concord     #3B6B57   muted green: "in concord" verdict only
--conflict    #8E3B46   muted claret: "conflict" verdict only
--review      #9A6B2F   muted ochre: "review" verdict only
```
Semantic colours are for verdicts and their evidence, nowhere else. The interface is otherwise
ink-on-paper.

**Type** (choose deliberately; these are roles, pick faces to match and set fallbacks):
- *Serif — the record.* Quoted claims, findings, and headline verdict text. A calm transitional/
  humanist serif (e.g. Spectral or Newsreader), generous line-height, lines under ~72 characters.
- *Sans — the instrument.* All UI chrome, navigation, labels, buttons, tables. A precise neutral
  grotesque (e.g. Geist, or system-ui as fallback). Sentence case everywhere; never all-caps labels.
- *Mono — figures only.* Probabilities, confidences, counts, hashes — with **tabular numerals** so
  columns align. Mono is for data, never for decorative labels.

**Layout.** 8px spacing system; wide margins; a single calm reading column for findings (content is
a document, not a dashboard grid). Small radius (4px) on interactive controls; document surfaces are
flat with a single hairline, not shadowed. Hairlines only where they separate real sections. Left-
aligned throughout.

**Motion.** One orchestrated moment: the audit progress resolving into the verdict. Slow, eased
(ease-out, 240–360ms), no bounce or spring. Motion elsewhere only to confirm a user action (commit,
replace, override). Respect `prefers-reduced-motion`.

### The hero moment: the verdict
Spend all boldness here. When a check completes, the result reads like an adjudication finding, not a
toast:

```
┌────────────────────────────────────────────────────────────┐
│  Conflict found                            [claret verdict]  │  ← serif, large
│  This upload contradicts 2 documents in the record.          │  ← sans, quiet
│                                                              │
│  1  Retention period                                         │  ← finding, serif
│     ┌─ In the record ──────────────┐ ┌─ This upload ───────┐│
│     │ "…retained for 90 days…"     │ │ "…kept for 30 days…"││  ← serif, paper-sunk
│     └──────────────────────────────┘ └─────────────────────┘│
│     Source: ABC.pdf · §3.2 · p.7        confidence  0.94     │  ← sans + mono figure
│                                                              │
│  [ Cancel upload ]   [ Replace ABC.pdf ]   [ Upload anyway ] │  ← plain active labels
└────────────────────────────────────────────────────────────┘
```

Two passages side by side, provenance beneath, a single confidence figure in mono. "In concord"
uses the same layout, quiet and green, with no findings. Empty and loading states are directional and
in the interface's voice: "Auditing against 342 documents…", not a spinner alone.

### Copy
Plain, exact, institutional-calm. Buttons name the action and keep that name through the flow
("Replace ABC.pdf" → "Replaced"). Findings state what conflicts and where; they never apologize and
never editorialize.

---

## 10. Repo structure
```
concord/
├── CLAUDE.md
├── README.md
├── backend/
│   ├── app.py            # FastAPI: async check job, commit, documents CRUD, health
│   ├── ingest.py         # chunking, claim extraction, embedding, indexing (idempotent)
│   ├── retrieve.py       # dense + sparse + RRF + rerank -> candidates
│   ├── contradict.py     # Laya: load once, check_pair, batching, numeric extractor
│   ├── adjudicate.py     # LLM confirm + explain (flagged pairs only)
│   ├── policy.py         # thresholds + verdict aggregation
│   ├── db.py             # local stores: Chroma (vectors) + SQLite (provenance) + BM25 (keywords)
│   ├── requirements.txt  # laya, fastapi, uvicorn[standard], sentence-transformers,
│   │                     #   FlagEmbedding (reranker), chromadb, rank-bm25, ollama, pydantic
│   └── data/             # created at runtime: chroma/ dir + concord.sqlite (git-ignored)
└── frontend/             # Next.js (App Router), local dev server
    ├── app/              # record (KB list), upload + verdict, document view
    ├── components/       # VerdictFinding, PassagePair, ConfidenceFigure, RecordTable, AuditProgress
    └── .env.local        # NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 11. Backend spec
- **Startup (lifespan):** load Laya, the embedding model, and the reranker **once**; hold on app state.
- `POST /documents` — ingest + index a file (idempotent on `sha256`). Used for the initial bulk load
  and for committing accepted uploads.
- `DELETE /documents/{id}` — remove a document and its chunks (unblocks a later upload).
- `POST /uploads/check` — start an **async** audit job for a candidate file → `{ job_id }`.
- `GET /uploads/check/{job_id}` — `{ status, progress, verdict, conflicts[] }` (poll or SSE).
- `POST /uploads/commit` — accept after "in concord" or an override (records the reason).
- `GET /documents` — list the record for the console.
- Fail safe everywhere: any model/DB error on a pair → Review, never silent concord.

## 12. Frontend spec
- **The record** — a calm, legible table of indexed documents (filename, sections, indexed date).
  This is the "grid" screen; keep it quiet.
- **Upload & verdict** — drop a file → `AuditProgress` ("Auditing against N documents…") → the
  verdict finding from §9. This screen is the product; give it the space.
- **Document view** — read a source file with its claims, reachable from any citation.
- Poll `/uploads/check/{job_id}` for progress; if the backend isn't up yet, show a plain "start the
  backend on :8000" message rather than failing silently.

---

## 13. Running it locally (this is the whole "deployment")
Everything runs on one machine. Nothing is deployed; there are no accounts and no network calls
after the one-time model-weight download.

**Prerequisites:** Python 3.10+, Node 18+, and — only if you enable claim extraction / adjudication —
[Ollama](https://ollama.com) with a small model pulled: `ollama pull qwen2.5:3b`.

**Backend**
```
cd backend
python -m venv .venv
.venv\Scripts\activate            # Windows PowerShell/CMD  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```
First run downloads the Laya, embedding, and reranker weights from Hugging Face once, then caches
them locally. The Chroma directory and the SQLite file are created automatically under
`backend/data/`.

**Frontend**
```
cd frontend
npm install
npm run dev                        # http://localhost:3000  ->  talks to http://localhost:8000
```

**First use:** run the ingester once against your folder of existing files to build the local index
(`python -m ingest ./path/to/knowledge-base`), then drop a new file in the UI to get a verdict. Add
`backend/data/`, `.venv/`, and `node_modules/` to `.gitignore`.

---

## 14. Known limits (put a plain version in the README)
- **Retrieval is the ceiling.** A conflict whose passage isn't retrieved is invisible to the rest of
  the pipeline. Optimize recall in Stage 2; measure it.
- **Quantities and dates** need the deterministic numeric path (§6); do not trust the classifier on
  numbers.
- **False positives block real work.** Prefer Review over hard-block in the gray zone; require LLM
  confirmation for any block; always offer a logged override.
- **Semantic-only.** Concord finds contradictions, not every kind of "wrong." It won't catch
  conflicts that require external truth or multi-document reasoning.
- **English v1** (English checkpoint). **CPU latency** makes the async job mandatory.
- **Evaluate on your own data.** Build a labelled set of real conflicting / non-conflicting pairs from
  the KB; report precision and recall; tune thresholds; if Laya isn't sharp enough, fine-tune it on
  contradiction pairs (Convai ship a free Kaggle 2×T4 RLCD notebook).

## 15. Build order
1. `db.py` + schema; ingest a handful of real files; confirm chunks, claims, embeddings, and
   full-text all land with correct provenance.
2. `retrieve.py`; eyeball candidates for a few known conflicts — is the right passage in the top N?
3. Laya **discovery step**, then `contradict.py` (gated pair check + numeric extractor); test on
   labelled pairs.
4. `adjudicate.py` + `policy.py`; end-to-end check on one clean and one conflicting file.
5. `app.py` async job + endpoints; then the frontend (record → upload/verdict → document view).
6. Run both locally (`uvicorn` + `npm run dev`) and click through the full flow; write the README
   (setup steps + the limits above) and add `backend/data/`, `.venv/`, `node_modules/` to `.gitignore`.

---

Attribution: Laya by Convai Innovations, Apache-2.0.
