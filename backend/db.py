"""Local stores: SQLite (provenance), Chroma (dense vectors), BM25 (keywords).

Everything is a file under backend/data/. Nothing runs as a service.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from rank_bm25 import BM25Okapi

DATA_DIR = Path(__file__).resolve().parent / "data"
SQLITE_PATH = DATA_DIR / "concord.sqlite"
CHROMA_DIR = DATA_DIR / "chroma"
UPLOAD_DIR = DATA_DIR / "uploads"

SCHEMA = """
CREATE TABLE IF NOT EXISTS document (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'indexed',
    uploaded_at  TEXT NOT NULL,
    uploaded_by  TEXT NOT NULL DEFAULT 'local'
);
CREATE INDEX IF NOT EXISTS idx_document_sha ON document(sha256);

CREATE TABLE IF NOT EXISTS chunk (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    ordinal       INTEGER NOT NULL,
    section_path  TEXT NOT NULL DEFAULT '',
    page          INTEGER,
    char_start    INTEGER NOT NULL,
    char_end      INTEGER NOT NULL,
    raw_text      TEXT NOT NULL,
    claim_text    TEXT NOT NULL,
    context       TEXT NOT NULL DEFAULT '',
    keywords      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunk(document_id);

CREATE TABLE IF NOT EXISTS upload (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    path         TEXT NOT NULL,
    status       TEXT NOT NULL,          -- pending | checked | committed | cancelled
    decision     TEXT,
    override_reason TEXT,
    document_id  TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_report (
    id          TEXT PRIMARY KEY,
    upload_id   TEXT NOT NULL REFERENCES upload(id),
    decision    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conflict_pair (
    id                TEXT PRIMARY KEY,
    report_id         TEXT NOT NULL REFERENCES conflict_report(id) ON DELETE CASCADE,
    new_claim         TEXT NOT NULL,
    new_section_path  TEXT NOT NULL DEFAULT '',
    new_page          INTEGER,
    existing_chunk_id TEXT NOT NULL,
    same_subject      REAL,
    contradicts       REAL,
    laya_confidence   REAL,
    numeric_flag      INTEGER NOT NULL DEFAULT 0,
    llm_verdict       TEXT,
    llm_kind          TEXT,
    llm_explanation   TEXT,
    severity          TEXT NOT NULL      -- conflict | review
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")
_STOP = set(
    "a an the and or of to in on for by with at from as is are was were be been being this that these "
    "those it its it's shall will must may can should would could not no all any each every our your "
    "their we you they he she which who whom whose than then there here into over under per".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class Store:
    """Thread-safe facade over SQLite + Chroma + an in-memory BM25 index."""

    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.sql = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
        self.sql.row_factory = sqlite3.Row
        self.sql.execute("PRAGMA foreign_keys = ON")
        self.sql.executescript(SCHEMA)
        self.sql.commit()
        self.chroma = chromadb.PersistentClient(path=str(CHROMA_DIR))
        self.claims = self.chroma.get_or_create_collection(
            "claims", metadata={"hnsw:space": "cosine"}
        )
        self._bm25: BM25Okapi | None = None
        self._bm25_ids: list[str] = []
        self.rebuild_bm25()

    # ---------- SQL helpers ----------
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.sql.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self.sql.execute(sql, params)
            self.sql.commit()

    # ---------- documents ----------
    def find_by_sha(self, sha256: str) -> dict | None:
        return self.one("SELECT * FROM document WHERE sha256 = ? AND status = 'indexed'", (sha256,))

    def find_by_filename(self, filename: str) -> dict | None:
        return self.one(
            "SELECT * FROM document WHERE filename = ? AND status = 'indexed' ORDER BY version DESC",
            (filename,),
        )

    def add_document(
        self, filename: str, sha256: str, chunks: list[dict], embeddings: list[list[float]],
        uploaded_by: str = "local", version: int = 1,
    ) -> str:
        doc_id = new_id()
        with self._lock:
            self.sql.execute(
                "INSERT INTO document (id, filename, sha256, version, status, uploaded_at, uploaded_by) "
                "VALUES (?, ?, ?, ?, 'indexed', ?, ?)",
                (doc_id, filename, sha256, version, now(), uploaded_by),
            )
            rows = []
            for c in chunks:
                c["id"] = new_id()
                rows.append((
                    c["id"], doc_id, c["ordinal"], c["section_path"], c.get("page"),
                    c["char_start"], c["char_end"], c["raw_text"], c["claim_text"],
                    c.get("context", ""), json.dumps(c.get("keywords", [])),
                ))
            self.sql.executemany(
                "INSERT INTO chunk (id, document_id, ordinal, section_path, page, char_start, char_end, "
                "raw_text, claim_text, context, keywords) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self.sql.commit()
            if chunks:
                for i in range(0, len(chunks), 500):
                    part = chunks[i : i + 500]
                    self.claims.add(
                        ids=[c["id"] for c in part],
                        embeddings=embeddings[i : i + 500],
                        documents=[c["claim_text"] for c in part],
                        metadatas=[{"document_id": doc_id, "filename": filename} for c in part],
                    )
            self.rebuild_bm25()
        return doc_id

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            doc = self.one("SELECT id FROM document WHERE id = ?", (doc_id,))
            if not doc:
                return False
            self.claims.delete(where={"document_id": doc_id})
            self.sql.execute("DELETE FROM chunk WHERE document_id = ?", (doc_id,))
            self.sql.execute("DELETE FROM document WHERE id = ?", (doc_id,))
            self.sql.commit()
            self.rebuild_bm25()
        return True

    def list_documents(self) -> list[dict]:
        return self.query(
            "SELECT d.*, COUNT(c.id) AS claim_count, COUNT(DISTINCT c.section_path) AS section_count "
            "FROM document d LEFT JOIN chunk c ON c.document_id = d.id "
            "WHERE d.status = 'indexed' GROUP BY d.id ORDER BY d.uploaded_at DESC, d.filename"
        )

    def document_count(self) -> int:
        return self.one("SELECT COUNT(*) AS n FROM document WHERE status = 'indexed'")["n"]

    def get_document(self, doc_id: str) -> dict | None:
        doc = self.one("SELECT * FROM document WHERE id = ?", (doc_id,))
        if doc:
            doc["chunks"] = self.query(
                "SELECT id, ordinal, section_path, page, char_start, char_end, raw_text, claim_text "
                "FROM chunk WHERE document_id = ? ORDER BY ordinal",
                (doc_id,),
            )
        return doc

    def get_chunks(self, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self.query(
            f"SELECT c.*, d.filename FROM chunk c JOIN document d ON d.id = c.document_id "
            f"WHERE c.id IN ({marks})",
            tuple(ids),
        )
        return {r["id"]: r for r in rows}

    # ---------- BM25 ----------
    def rebuild_bm25(self) -> None:
        with self._lock:
            rows = self.sql.execute("SELECT id, raw_text FROM chunk ORDER BY rowid").fetchall()
            self._bm25_ids = [r["id"] for r in rows]
            corpus = [tokenize(r["raw_text"]) or ["_"] for r in rows]
            self._bm25 = BM25Okapi(corpus) if corpus else None

    def bm25_search(self, text: str, k: int) -> list[tuple[str, float]]:
        with self._lock:
            if self._bm25 is None:
                return []
            q = tokenize(text)
            if not q:
                return []
            scores = self._bm25.get_scores(q)
            ids = self._bm25_ids
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(ids[i], float(scores[i])) for i in order if scores[i] > 0]

    def dense_search(self, embedding: list[float], k: int) -> list[tuple[str, float]]:
        n = self.claims.count()
        if n == 0:
            return []
        res = self.claims.query(query_embeddings=[embedding], n_results=min(k, n))
        return [(i, 1.0 - d) for i, d in zip(res["ids"][0], res["distances"][0])]


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store
