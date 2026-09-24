"""Stage 3: Laya contradiction check + deterministic numeric path.

Discovery findings (laya 0.3.20, English checkpoint, see README "Discovery notes"):
- Result keys are answers[q]["noul"] (P(true)) and answers[q]["confidence"], as documented.
- Instructions must name the state keys in backticks (`existing`, `new`). Without that the
  model returns ~0.0 for `contradicts` even on direct negations.
- Laya's `same_subject` tracks *agreement*, not topic: it scores ~0.0 on "90 days" vs "30 days".
  AND-ing it with `contradicts` removes exactly the pairs we want. The subject gate therefore uses
  embedding cosine similarity (negation-insensitive) instead; Laya answers `contradicts` only.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

LAYA_MODEL = os.environ.get("CONCORD_LAYA_MODEL", "convaiinnovations/laya")
BATCH_SIZE = 8

QUESTIONS = {
    "contradicts": {
        "type": "noul",
        "instructions": "Do `existing` and `new` contradict each other, so that both cannot be true at the same time?",
    },
}

_agent = None
_agent_lock = threading.Lock()


def get_agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            import laya

            _agent = laya.load(LAYA_MODEL)
        return _agent


def _parse(result: dict) -> dict:
    a = result["answers"]["contradicts"]
    return {"contradicts": float(a["noul"]), "confidence": float(a["confidence"])}


def check_pair(existing_claim: str, new_claim: str) -> dict:
    agent = get_agent()
    with _agent_lock:
        return _parse(agent.predict({"existing": existing_claim, "new": new_claim}, QUESTIONS))


def check_pairs(pairs: list[tuple[str, str]]) -> list[dict]:
    """Batched check over (existing, new) pairs. Raises on model error; callers route to Review."""
    if not pairs:
        return []
    agent = get_agent()
    states = [{"existing": e, "new": n} for e, n in pairs]
    with _agent_lock:
        results = agent.predict_batch(states, QUESTIONS, batch_size=BATCH_SIZE)
    return [_parse(r) for r in results]


# ---------------------------------------------------------------- numeric path

@dataclass(frozen=True)
class Quantity:
    kind: str       # duration | percent | money | date | year | count
    value: float
    text: str


_DURATION_UNITS = {
    "second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24, "day": 1,
    "business day": 1.4, "working day": 1.4, "week": 7, "month": 30.44, "year": 365.25,
}
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100,
}
_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split()
)}
_MONTH_RE = "|".join(sorted({*(_MONTHS), *(m[:3] for m in _MONTHS)}, key=len, reverse=True))

_NUM = r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|" + "|".join(_WORD_NUM) + r")"
RE_DURATION = re.compile(
    _NUM + r"[\s-]*(business days?|working days?|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|yrs?)\b",
    re.I,
)
RE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b)", re.I)
RE_MONEY = re.compile(
    r"(?:([$€£₹])\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand|billion|bn)?\b)"
    r"|(?:\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand|billion|bn)?\s*(USD|EUR|GBP|INR|dollars|euros|pounds|rupees)\b)",
    re.I,
)
RE_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RE_TEXT_DATE = re.compile(
    r"\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_RE + r")\.?,?\s+(\d{4})"
    r"|(" + _MONTH_RE + r")\.?\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?(\d{4}))\b",
    re.I,
)
RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
RE_COUNT = re.compile(r"(?<![\w.$§:])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w%:]|\.\d)")

_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "billion": 1e9, "bn": 1e9}
_UNIT_ALIAS = {"sec": "second", "min": "minute", "hr": "hour", "yr": "year"}


def _num(s: str) -> float:
    s = s.lower()
    return float(_WORD_NUM[s]) if s in _WORD_NUM else float(s.replace(",", ""))


def _month(s: str) -> int:
    return _MONTHS.get(s.lower()) or next(v for k, v in _MONTHS.items() if k.startswith(s.lower()[:3]))


def extract_quantities(text: str) -> list[Quantity]:
    out: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def add(m: re.Match, q: Quantity) -> None:
        a, b = m.span()
        if all(b <= x or a >= y for x, y in taken):
            taken.append((a, b))
            out.append(q)

    for m in RE_ISO_DATE.finditer(text):
        y, mo, d = map(int, m.groups())
        add(m, Quantity("date", y * 10000 + mo * 100 + d, m.group()))
    for m in RE_TEXT_DATE.finditer(text):
        if m.group(3):
            d, mon, y = int(m.group(1)), m.group(2), int(m.group(3))
        else:
            mon, d, y = m.group(4), int(m.group(5) or 0), int(m.group(6))
        add(m, Quantity("date", y * 10000 + _month(mon) * 100 + d, m.group()))
    for m in RE_MONEY.finditer(text):
        v, scale = (_num(m.group(2)), m.group(3)) if m.group(2) else (_num(m.group(4)), m.group(5))
        add(m, Quantity("money", v * _SCALE.get((scale or "").lower(), 1), m.group().strip()))
    for m in RE_PERCENT.finditer(text):
        add(m, Quantity("percent", float(m.group(1)), m.group()))
    for m in RE_DURATION.finditer(text):
        unit = m.group(2).lower()
        unit = unit[:-1] if unit.endswith("s") else unit
        unit = _UNIT_ALIAS.get(unit, unit)
        add(m, Quantity("duration", _num(m.group(1)) * _DURATION_UNITS.get(unit, 1), m.group()))
    for m in RE_YEAR.finditer(text):
        add(m, Quantity("year", float(m.group(1)), m.group()))
    for m in RE_COUNT.finditer(text):
        add(m, Quantity("count", _num(m.group(1)), m.group()))
    return out


def compare_quantities(a: str, b: str, rel_tol: float) -> tuple[str, tuple[str, str] | None]:
    """Compare comparable quantities in two claims.

    Returns ("differ", (a_text, b_text)) if some kind appears in both and no value agrees,
    ("agree", None) if comparable quantities exist and all kinds agree, else ("none", None).
    """
    qa, qb = extract_quantities(a), extract_quantities(b)
    compared = False
    for kind in ("duration", "percent", "money", "date", "year", "count"):
        xs = [q for q in qa if q.kind == kind]
        ys = [q for q in qb if q.kind == kind]
        if not xs or not ys:
            continue
        agrees = any(
            abs(x.value - y.value) <= rel_tol * max(abs(x.value), abs(y.value), 1e-9)
            for x in xs for y in ys
        )
        if not agrees:
            return "differ", (xs[0].text, ys[0].text)
        compared = True
    return ("agree" if compared else "none"), None
