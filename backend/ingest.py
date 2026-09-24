"""Ingestion: parse -> structural sections -> atomic claims -> embed -> index.

Usage (bulk load of an existing knowledge base):
    python -m ingest ./path/to/knowledge-base
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from pathlib import Path

SUPPORTED = {".txt", ".md", ".markdown", ".pdf", ".docx"}
EMBED_MODEL = os.environ.get("CONCORD_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EXTRACT_CLAIMS = os.environ.get("CONCORD_EXTRACT_CLAIMS", "0") == "1"
MIN_CLAIM_WORDS = 4

_embedder = None
_embed_lock = threading.Lock()


def get_embedder():
    global _embedder
    with _embed_lock:
        if _embedder is None:
            from sentence_transformers import SentenceTransformer

            _embedder = SentenceTransformer(EMBED_MODEL, device="cpu")
        return _embedder


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    vecs = get_embedder().encode(
        texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False
    )
    return vecs.tolist()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- parsing

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_NUM_HEADING = re.compile(r"^((?:\d+\.)*\d+)\.?\s+([A-Z][^.!?]{1,80})$")
_BULLET = re.compile(r"^\s*(?:[-*•]|\(?[a-z0-9]{1,3}[.)])\s+")


def _read_pages(path: Path) -> list[tuple[int | None, list[tuple[str, int | None]]]]:
    """Return [(page, [(line, heading_level_or_None), ...]), ...]."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return [
            (i + 1, [(ln, None) for ln in (p.extract_text() or "").splitlines()])
            for i, p in enumerate(reader.pages)
        ]
    if ext == ".docx":
        import docx

        lines: list[tuple[str, int | None]] = []
        for para in docx.Document(str(path)).paragraphs:
            style = (para.style.name or "").lower() if para.style is not None else ""
            level = None
            if style.startswith("heading"):
                m = re.search(r"\d+", style)
                level = int(m.group()) if m else 1
            elif style == "title":
                level = 1
            lines.append((para.text, level))
            lines.append(("", None))
        return [(None, lines)]
    text = path.read_text(encoding="utf-8", errors="replace")
    return [(None, [(ln, None) for ln in text.splitlines()])]


def _heading(line: str, forced_level: int | None) -> tuple[int, str] | None:
    s = line.strip()
    if not s:
        return None
    if forced_level:
        return forced_level, s
    m = _MD_HEADING.match(s)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _NUM_HEADING.match(s)
    if m and len(s.split()) <= 10:
        return m.group(1).count(".") + 1, s
    return None


def parse_blocks(path: str | Path) -> list[dict]:
    """Split a document structurally into paragraphs with section_path + page."""
    path = Path(path)
    blocks: list[dict] = []
    stack: list[tuple[int, str]] = []
    offset = 0
    for page, lines in _read_pages(path):
        para: list[str] = []
        para_start = offset

        def flush() -> None:
            nonlocal para
            text = " ".join(p.strip() for p in para if p.strip())
            if text:
                blocks.append({
                    "section_path": " > ".join(t for _, t in stack),
                    "page": page,
                    "text": text,
                    "char_start": para_start,
                })
            para = []

        for line, level in lines:
            h = _heading(line, level)
            if h:
                flush()
                lvl, title = h
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                stack.append((lvl, title))
            elif not line.strip():
                flush()
            elif _BULLET.match(line):
                flush()
                para_start = offset
                para.append(_BULLET.sub("", line))
            else:
                if not para:
                    para_start = offset
                para.append(line)
            offset += len(line) + 1
        flush()
    return blocks


# ---------------------------------------------------------------- claims

_ABBREV = re.compile(
    r"\b(?:e\.g|i\.e|etc|vs|Mr|Mrs|Ms|Dr|Inc|Ltd|Co|No|Fig|Sec|approx|St)\.$", re.IGNORECASE
)
_SPLIT = re.compile(r"(?<=[.!?;])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9§])")


def split_sentences(text: str) -> list[tuple[str, int]]:
    """Split into sentences, returning (sentence, offset_in_text)."""
    out: list[tuple[str, int]] = []
    start = 0
    for m in _SPLIT.finditer(text):
        piece = text[start : m.start() + 1]
        if _ABBREV.search(piece.rstrip()):
            continue
        out.append((piece.strip(), start))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append((tail, start))
    return out


def normalize_claim(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip("\"'“” ")
    return s


def _keywords(s: str) -> list[str]:
    from db import tokenize

    seen: list[str] = []
    for t in tokenize(s):
        if t not in seen and (len(t) > 2 or t.isdigit()):
            seen.append(t)
    return seen[:24]


def extract_claims(path: str | Path) -> list[dict]:
    """Atomic, sentence-level claims with provenance and one sentence of context."""
    chunks: list[dict] = []
    for block in parse_blocks(path):
        sents = split_sentences(block["text"])
        for i, (sent, off) in enumerate(sents):
            claim = normalize_claim(sent)
            if len(claim.split()) < MIN_CLAIM_WORDS:
                continue
            prev = sents[i - 1][0] if i > 0 else ""
            nxt = sents[i + 1][0] if i + 1 < len(sents) else ""
            start = block["char_start"] + off
            chunks.append({
                "ordinal": len(chunks),
                "section_path": block["section_path"],
                "page": block["page"],
                "char_start": start,
                "char_end": start + len(sent),
                "raw_text": sent,
                "claim_text": claim,
                "context": " ".join(x for x in (prev, sent, nxt) if x),
                "keywords": _keywords(claim),
            })
    if EXTRACT_CLAIMS:
        _rewrite_claims(chunks)
    return chunks


def _rewrite_claims(chunks: list[dict]) -> None:
    """Optional: make each claim self-contained with a local LLM (slow on CPU)."""
    try:
        from adjudicate import rewrite_claim
    except Exception:
        return
    for c in chunks:
        rewritten = rewrite_claim(c["claim_text"], c["context"], c["section_path"])
        if rewritten:
            c["claim_text"] = rewritten


# ---------------------------------------------------------------- indexing

def ingest_file(path: str | Path, filename: str | None = None, uploaded_by: str = "local") -> tuple[str, bool]:
    """Index one file. Idempotent on sha256. Returns (document_id, created)."""
    from db import get_store

    store = get_store()
    path = Path(path)
    filename = filename or path.name
    digest = sha256_file(path)
    existing = store.find_by_sha(digest)
    if existing:
        return existing["id"], False
    prior = store.find_by_filename(filename)
    version = (prior["version"] + 1) if prior else 1
    chunks = extract_claims(path)
    vectors = embed([c["claim_text"] for c in chunks])
    doc_id = store.add_document(filename, digest, chunks, vectors, uploaded_by, version)
    return doc_id, True


def ingest_folder(folder: str | Path) -> None:
    files = sorted(
        p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED
    )
    if not files:
        print(f"No supported files in {folder} ({', '.join(sorted(SUPPORTED))})")
        return
    print(f"Indexing {len(files)} files from {folder}")
    for p in files:
        doc_id, created = ingest_file(p)
        print(f"  {'indexed ' if created else 'unchanged'}  {p.name}  ({doc_id})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m ingest <folder>")
        sys.exit(2)
    ingest_folder(sys.argv[1])
