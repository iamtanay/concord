"""Thresholds and verdict aggregation. Every tunable lives in CONFIG."""
from __future__ import annotations

from dataclasses import asdict, dataclass

CONFLICT = "conflict"
REVIEW = "review"
CONCORD = "concord"


@dataclass
class Config:
    # Stage 2: retrieval
    dense_k: int = 20
    sparse_k: int = 20
    fused_k: int = 20
    top_n: int = 5
    rerank_min: float = 0.02          # candidates below this rerank score skip Laya entirely

    # Stage 3: Laya gate
    tau_subject: float = 0.70
    tau_contra: float = 0.75
    min_confidence: float = 0.55      # below this, a near-miss pair goes to Review
    review_band: float = 0.15         # within this of both thresholds -> Review (gray zone)

    # Numeric path
    numeric_subject: float = 0.60     # same_subject needed before comparing quantities
    numeric_rel_tol: float = 0.01     # relative tolerance before two quantities "differ"

    # Stage 4: LLM
    llm_min_confidence: float = 0.6   # LLM-reported confidence needed for a hard block


CONFIG = Config()


def config_dict() -> dict:
    return asdict(CONFIG)


def laya_triage(same_subject: float, contradicts: float, confidence: float, numeric: bool) -> str | None:
    """Classify one pair after Laya + the numeric check.

    Returns 'flag' (send to LLM), 'review' (gray zone, soft), or None (clear).
    """
    c = CONFIG
    if same_subject >= c.tau_subject and contradicts >= c.tau_contra:
        return "flag" if confidence >= c.min_confidence else "review"
    if numeric and same_subject >= c.numeric_subject:
        return "flag"
    near_subject = same_subject >= c.tau_subject - c.review_band
    near_contra = contradicts >= c.tau_contra - c.review_band
    if near_subject and near_contra:
        return "review"
    return None


def aggregate(pairs: list[dict]) -> str:
    """One verdict per upload from its surviving pairs (each has a 'severity')."""
    if any(p["severity"] == CONFLICT for p in pairs):
        return CONFLICT
    if any(p["severity"] == REVIEW for p in pairs):
        return REVIEW
    return CONCORD
