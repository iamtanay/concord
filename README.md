# Concord

A consistency gate for a knowledge base. When a new file is uploaded, Concord checks it against the
existing record and blocks it if it contradicts something already there, naming the exact file and
passage in conflict. The user can then cancel, replace the conflicting file, or override with a
logged reason.

*Nothing enters the record that contradicts it.*

Everything runs locally on one machine, CPU only: a FastAPI backend (models + local stores) and a
Next.js console.

## How it works

```
upload -> sentence-level claims -> for each claim:
    hybrid retrieval (bge-small dense + BM25, RRF, bge-reranker-base)  -> top 6 existing passages
    subject gate: embedding cosine >= 0.62
    Laya: contradicts >= 0.75          + deterministic numeric/date check
    LLM (Ollama qwen2.5:3b): confirm + explain flagged pairs only
-> verdict: Conflict (blocked) | Review | In concord | Already in the record (rejected)
```

| Verdict | When | What you can do |
|---|---|---|
| **Conflict** | An LLM-confirmed contradiction | Cancel, replace the conflicting file(s), or upload anyway with a reason |
| **Review** | Gray-zone or disputed signals, or no LLM available | Confirm and add, replace, or cancel |
| **In concord** | No surviving flags | Add to record |
| **Already in the record** | Identical file (same sha256), or every claim already stated by one document | Rejected; cannot be committed, even with an override |

Fail-safe: any model error on a pair routes it to Review, never to "in concord". Only an
LLM-confirmed conflict can hard-block.

## Setup

Prerequisites: Python 3.10–3.12, Node 18+, and [Ollama](https://ollama.com) with a small model:

```
ollama pull qwen2.5:3b
```

Without Ollama the pipeline still runs, but flagged pairs go to Review instead of being confirmed.

**Backend**

```
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install --extra-index-url https://download.pytorch.org/whl/cpu torch
pip install -r requirements.txt
uvicorn app:app --port 8000
```

The first run downloads the Laya, embedding, and reranker weights (about 1.5 GB) and caches them.
Models load in the background after startup; `GET /health` reports `"ready": true` when done.
The Chroma directory and SQLite file are created under `backend/data/`.

**Frontend**

```
cd frontend
npm install
npm run dev                        # http://localhost:3000, talks to http://localhost:8000
```

**Seed the record** with your existing files (txt, md, pdf, docx):

```
cd backend
python -m ingest ../samples/kb     # or your own folder
```

## Try it with the samples

`samples/kb/` is a small fictional policy set (retention, access control, expenses, incidents,
remote work). `samples/uploads/` has two test uploads:

- `logging-standard-2026.md`: conflicts with the record (application logs kept 30 days vs 90 days;
  MFA optional vs required). Expect **Conflict found**.
- `onboarding-checklist.md`: unrelated to the record. Expect **In concord**.
- Upload any file from `samples/kb/` again (or a renamed copy): expect **Already in the record**.

An audit takes roughly 10 seconds to 2 minutes on CPU, depending on how many passages need the LLM.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/uploads/check` | Start an async audit of a file, returns `{job_id, upload_id}` |
| `GET` | `/uploads/check/{job_id}` | `{status, stage, progress, verdict, conflicts[], documents[], duplicate_of}` |
| `POST` | `/uploads/commit` | `{upload_id, action: commit\|override\|replace, reason?, replace_document_ids?}` |
| `POST` | `/uploads/{upload_id}/cancel` | Discard an upload |
| `GET` | `/documents`, `/documents/{id}` | The record, and one document with its claims |
| `POST` | `/documents` | Index a file directly (seeding; bypasses the check) |
| `DELETE` | `/documents/{id}` | Remove a document and its chunks |
| `GET` | `/overrides` | Uploads accepted with an override, and their reasons |
| `GET` | `/health` | Model readiness, LLM availability, thresholds |

All thresholds live in `backend/policy.py` (`Config`).

## Discovery notes (laya 0.3.20, English checkpoint)

CLAUDE.md asks for a discovery step before wiring the policy. What it found:

- Result keys are as documented: `answers[q]["noul"]` (P(true)) and `answers[q]["confidence"]`.
  About 0.5 to 0.7 s per pair on CPU; `predict_batch` is used.
- Instructions must name the state keys in backticks (`` `existing` ``, `` `new` ``). With generic
  wording, `contradicts` scored near 0 even on direct negations.
- Laya's `same_subject` tracks agreement, not topic ("90 days" vs "30 days" scored 0.05). AND-ing it
  with `contradicts` removed exactly the conflicts we want, so the subject gate uses embedding cosine
  similarity, and Laya answers only `contradicts`.
- The reranker scores a contradicting passage as irrelevant (0.002), so its ranking is fused with
  the dense and sparse rankings rather than used as the final cut.
- `qwen2.5:3b` rubber-stamps conflicts unless it has to name each passage's subject first; the
  prompt uses a JSON schema with explicit subjects and a few examples.

## Known limits

- **Retrieval is the ceiling.** A conflict whose passage isn't retrieved is invisible downstream.
- **Quantities and dates** use a deterministic extractor; the classifier is not trusted on numbers.
- **Some semantic contradictions slip through.** Laya missed "Reviews are blameless" vs "reviews
  assign individual blame" in testing. The small LLM also misses exceptions to universal rules;
  those are routed to Review when Laya's signal is strong.
- **False positives block real work**, so only LLM-confirmed pairs block, gray-zone pairs go to
  Review, and override is always available (logged with a reason).
- **Semantic only:** no external truth, no multi-document reasoning. **English only.**
- **Evaluate on your own data.** Thresholds were tuned on a small sample set; build a labelled set
  from your knowledge base and retune `policy.py`.

Laya by Convai Innovations, Apache-2.0.
