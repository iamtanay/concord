"""Stage 2: candidate generation.

Dense + BM25 -> Reciprocal Rank Fusion -> cross-encoder rerank -> top N.

The reranker is a relevance model: it scores a *contradicting* passage as irrelevant
(e.g. 0.002 for "Business class is permitted..." vs "Business class is never permitted").
So its ranking is fused with the dense and sparse rankings rather than used as the final
filter; a contradiction that is close in embedding space still makes the top N.
"""
from __future__ import annotations

import os
import threading

import numpy as np

from db import get_store
from policy import CONFIG

RERANK_MODEL = os.environ.get("CONCORD_RERANK_MODEL", "BAAI/bge-reranker-base")

_reranker = None
_rerank_lock = threading.Lock()


def get_reranker():
    global _reranker
    with _rerank_lock:
        if _reranker is None:
            from sentence_transformers import CrossEncoder

            _reranker = CrossEncoder(RERANK_MODEL, device="cpu", max_length=256)
        return _reranker


def rrf(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)


def candidates_for_claims(
    claims: list[str], embeddings: list[list[float]], exclude_doc_ids: set[str] | None = None
) -> list[list[dict]]:
    """For each new claim, the top-N existing chunks, each with `similarity` and `rerank_score`."""
    import torch

    store = get_store()
    exclude = exclude_doc_ids or set()
    fused: list[list[str]] = []
    for claim, vec in zip(claims, embeddings):
        dense = [cid for cid, _ in store.dense_search(vec, CONFIG.dense_k)]
        sparse = [cid for cid, _ in store.bm25_search(claim, CONFIG.sparse_k)]
        fused.append(rrf([dense, sparse])[: CONFIG.fused_k])

    all_ids = sorted({cid for lst in fused for cid in lst})
    chunks = {cid: c for cid, c in store.get_chunks(all_ids).items() if c["document_id"] not in exclude}
    vectors = store.get_embeddings(list(chunks))

    pairs = [(i, cid) for i, lst in enumerate(fused) for cid in lst if cid in chunks]
    rerank: list[float] = []
    if pairs:
        rerank = get_reranker().predict(
            [(claims[i], chunks[cid]["claim_text"]) for i, cid in pairs],
            batch_size=32, show_progress_bar=False, activation_fn=torch.nn.Sigmoid(),
        ).tolist()

    scored: list[dict[str, dict]] = [{} for _ in claims]
    for (i, cid), r in zip(pairs, rerank):
        sim = float(np.dot(embeddings[i], vectors[cid])) if cid in vectors else 0.0
        scored[i][cid] = {**chunks[cid], "rerank_score": float(r), "similarity": sim}

    out: list[list[dict]] = []
    for i, cands in enumerate(scored):
        by_sim = sorted(cands, key=lambda c: cands[c]["similarity"], reverse=True)
        by_rerank = sorted(cands, key=lambda c: cands[c]["rerank_score"], reverse=True)
        order = rrf([fused[i], by_sim, by_rerank])
        out.append([cands[c] for c in order if c in cands][: CONFIG.top_n])
    return out
