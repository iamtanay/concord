"""Stage 2: candidate generation. Dense + BM25 -> Reciprocal Rank Fusion -> cross-encoder rerank."""
from __future__ import annotations

import os
import threading

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
    """For each new claim, return the top-N existing chunks (with rerank score)."""
    store = get_store()
    exclude = exclude_doc_ids or set()
    fused_lists: list[list[str]] = []
    for claim, vec in zip(claims, embeddings):
        dense = [cid for cid, _ in store.dense_search(vec, CONFIG.dense_k)]
        sparse = [cid for cid, _ in store.bm25_search(claim, CONFIG.sparse_k)]
        fused_lists.append(rrf([dense, sparse])[: CONFIG.fused_k])

    all_ids = sorted({cid for lst in fused_lists for cid in lst})
    chunks = store.get_chunks(all_ids)

    pairs: list[tuple[int, str]] = []
    for i, lst in enumerate(fused_lists):
        for cid in lst:
            ch = chunks.get(cid)
            if ch and ch["document_id"] not in exclude:
                pairs.append((i, cid))

    scores: list[float] = []
    if pairs:
        model = get_reranker()
        scores = model.predict(
            [(claims[i], chunks[cid]["claim_text"]) for i, cid in pairs],
            batch_size=32,
            show_progress_bar=False,
            activation_fn=_sigmoid(),
        ).tolist()

    per_claim: list[list[dict]] = [[] for _ in claims]
    for (i, cid), s in zip(pairs, scores):
        per_claim[i].append({**chunks[cid], "rerank_score": float(s)})
    for lst in per_claim:
        lst.sort(key=lambda c: c["rerank_score"], reverse=True)
        del lst[CONFIG.top_n :]
    return per_claim


def _sigmoid():
    import torch

    return torch.nn.Sigmoid()
