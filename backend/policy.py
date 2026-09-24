"""Thresholds and verdict aggregation. Every tunable lives in CONFIG."""
from __future__ import annotations

from dataclasses import asdict, dataclass

CONFLICT = "conflict"
REVIEW = "review"
CONCORD = "concord"
DUPLICATE = "duplicate"   # the upload is already in the record: rejected, cannot be committed


@dataclass
class Config:
    # Stage 2: retrieval
    dense_k: int = 20
    sparse_k: int = 20
    fused_k: int = 20
    top_n: int = 6

    # Stage 3: the gate. `same_subject` is embedding cosine (see contradict.py for why).
    tau_subject: float = 0.62
    tau_contra: float = 0.75
    subject_band: float = 0.0         # gray zone below tau_subject (unrelated pairs live just below)
    contra_band: float = 0.15         # gray zone below tau_contra
    min_confidence: float = 0.60      # Laya confidence needed to send a flag to the LLM

    # Numeric path
    numeric_subject: float = 0.72     # similarity needed before comparing quantities
    numeric_rel_tol: float = 0.01     # relative tolerance before two quantities "differ"

    # Stage 4: LLM
    llm_min_confidence: float = 0.6   # LLM-reported confidence needed for a hard block
    # If the LLM clears a pair that Laya flagged this strongly, downgrade it to Review rather than
    # clearing it: a small local LLM misses exceptions ("required for all" vs "optional for some").
    gray_llm_cap: int = 6             # gray-zone pairs the LLM may clear per upload (never escalate)
    strong_contra: float = 0.90
    strong_subject: float = 0.68

    @property
    def subject_floor(self) -> float:
        """Pairs below this similarity can neither flag nor enter review; Laya skips them."""
        return min(self.tau_subject - self.subject_band, self.numeric_subject)


CONFIG = Config()


def config_dict() -> dict:
    return {**asdict(CONFIG), "subject_floor": CONFIG.subject_floor}


def laya_triage(
    same_subject: float, contradicts: float, confidence: float, numeric: str | None
) -> str | None:
    """Classify one pair after Laya + the numeric check.

    `numeric` is 'differ', 'agree', or None (no comparable quantities).
    Returns 'flag' (send to LLM), 'review' (gray zone, soft), or None (clear).
    """
    c = CONFIG
    if same_subject >= c.tau_subject and contradicts >= c.tau_contra:
        return "flag" if confidence >= c.min_confidence else "review"
    if numeric == "differ" and same_subject >= c.numeric_subject:
        return "flag"
    if numeric == "agree":
        return None  # the figures match; a gray-zone Laya score is not enough on its own
    if same_subject >= c.tau_subject - c.subject_band and contradicts >= c.tau_contra - c.contra_band:
        return "review"
    return None


def aggregate(pairs: list[dict]) -> str:
    """One verdict per upload from its surviving pairs (each has a 'severity')."""
    if any(p["severity"] == CONFLICT for p in pairs):
        return CONFLICT
    if any(p["severity"] == REVIEW for p in pairs):
        return REVIEW
    return CONCORD
