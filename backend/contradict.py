"""Stage 3: Laya contradiction check (two gated questions) + deterministic numeric path."""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

LAYA_MODEL = os.environ.get("CONCORD_LAYA_MODEL", "convaiinnovations/laya")

QUESTIONS = {
    "same_subject": {
        "type": "noul",
        "instructions": "Both statements describe the same specific fact, policy, entity, or value.",
    },
    "contradicts": {
        "type": "noul",
        "instructions": "The two statements make claims that cannot both be true at the same time.",
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


def check_pair(existing_claim: str, new_claim: str) -> dict:
    state = {"existing": existing_claim, "new": new_claim}
    with _agent_lock:
        r = get_agent().predict(state, QUESTIONS)["answers"]
    return {
        "same_subject": float(r["same_subject"]["noul"]),
        "contradicts": float(r["contradicts"]["noul"]),
        "confidence": float(min(r["same_subject"]["confidence"], r["contradicts"]["confidence"])),
    }


# ---------------------------------------------------------------- numeric path

@dataclass(frozen=True)
class Quantity:
    kind: str       # duration | percent | money | date | year | count
    value: float
    text: str


_DURATION_UNITS = {
    "second": 1 / 86400, "sec": 1 / 86400, "minute": 1 / 1440, "min": 1 / 1440,
    "hour": 1 / 24, "hr": 1 / 24, "day": 1, "business day": 1.4, "working day": 1.4,
    "week": 7, "month": 30.44, "year": 365.25, "yr": 365.25,
}
_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100,
}
_MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august september october november december".split()
)}
_MONTHS.update({k[:3]: v for k, v in list(_MONTHS.items())})

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|" + "|".join(_WORD_NUM) + r")"
RE_DURATION = re.compile(
    _NUM + r"[\s-]*(business days?|working days?|seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|yrs?)\b",
    re.I,
)
RE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|per cent\b)", re.I)
RE_MONEY = re.compile(
    r"(?:([$€£₹])\s?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand|billion|bn)?)"
    r"|(?:(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand|billion|bn)?\s*(USD|EUR|GBP|INR|dollars|euros|pounds|rupees)\b)",
    re.I,
)
RE_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RE_TEXT_DATE = re.compile(
    r"\b(?:(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(_MONTHS) + r")[a-z]*,?\s+(\d{4})"
    r"|(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?(\d{4}))\b",
    re.I,
)
RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2}|21\d{2})\b")
RE_COUNT = re.compile(r"(?<![\w.$§])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w.%]|\.\d)")

_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "billion": 1e9, "bn": 1e9}


def _num(s: str) -> float:
    s = s.lower()
    return float(_WORD_NUM[s]) if s in _WORD_NUM else float(s.replace(",", ""))


def extract_quantities(text: str) -> list[Quantity]:
    out: list[Quantity] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    def add(m: re.Match, q: Quantity) -> None:
        if free(m.span()):
            taken.append(m.span())
            out.append(q)

    for m in RE_ISO_DATE.finditer(text):
        y, mo, d = map(int, m.groups())
        add(m, Quantity("date", y * 10000 + mo * 100 + d, m.group()))
    for m in RE_TEXT_DATE.finditer(text):
        if m.group(3):
            d, mon, y = int(m.group(1)), m.group(2), int(m.group(3))
        else:
            mon, d, y = m.group(4), int(m.group(5) or 0), int(m.group(6))
        add(m, Quantity("date", y * 10000 + _MONTHS[mon.lower()[:3]] * 100 + d, m.group()))
    for m in RE_MONEY.finditer(text):
        if m.group(2):
            v, scale = _num(m.group(2)), m.group(3)
        else:
            v, scale = _num(m.group(4)), m.group(5)
        v *= _SCALE.get((scale or "").lower(), 1)
        add(m, Quantity("money", v, m.group()))
    for m in RE_PERCENT.finditer(text):
        add(m, Quantity("percent", float(m.group(1)), m.group()))
    for m in RE_DURATION.finditer(text):
        unit = m.group(2).lower().rstrip("s")
        unit = {"sec": "second", "min": "minute", "hr": "hour", "yr": "year"}.get(unit, unit)
        add(m, Quantity("duration", _num(m.group(1)) * _DURATION_UNITS.get(unit, 1), m.group()))
    for m in RE_YEAR.finditer(text):
        add(m, Quantity("year", float(m.group(1)), m.group()))
    for m in RE_COUNT.finditer(text):
        add(m, Quantity("count", _num(m.group(1)), m.group()))
    return out


def numeric_conflict(a: str, b: str, rel_tol: float) -> tuple[str, str] | None:
    """If both claims state a comparable quantity and none of them agree, return the differing pair."""
    qa, qb = extract_quantities(a), extract_quantities(b)
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
            return xs[0].text, ys[0].text
    return None
