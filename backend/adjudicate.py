"""Stage 4: confirm + explain Laya-flagged pairs with a local LLM (Ollama).

Only flagged pairs reach this module. If Ollama is unavailable, callers get None
and route the pair to Review; nothing is ever silently cleared.
"""
from __future__ import annotations

import json
import os
import re
import time

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("CONCORD_LLM_MODEL", "qwen2.5:3b")
KINDS = {"value_mismatch", "mutually_exclusive", "status_change", "none"}

_client = None
_available: tuple[float, bool] = (0.0, False)

SYSTEM = """You are an auditor checking a knowledge base for contradictions.
You get passage A (already in the record) and passage B (from a new upload).

1. existing_subject / new_subject: the exact thing each passage makes a claim about, in a few words
   (e.g. "retention period of application logs"). Never use a file name as the subject.
2. same_subject: true only if both passages make a claim about the very same thing.
   Different kinds of data, systems, people, or processes are different subjects.
3. is_conflict: true only if same_subject is true AND both claims cannot be true at the same time.
   A rule for "all employees on every system" conflicts with an exception that makes it optional for
   some of them. Different scopes that do not overlap, or extra detail, are not conflicts.
4. confidence: a number from 0.0 to 1.0.
5. explanation: one sentence, "The record states <A's value or rule>; the new file states <B's value or rule>."
   Quote exact values from the passages. Never invent values.

Examples:
A: "Application logs are retained for 90 days."  B: "Application logs are kept for 30 days."
-> same_subject true, is_conflict true, kind value_mismatch
A: "Backups are kept for 35 days."  B: "Application logs are kept for 30 days."
-> same_subject false (backups vs application logs), is_conflict false, kind none
A: "Remote work is allowed up to three days per week."  B: "Remote work is not permitted."
-> same_subject true, is_conflict true, kind mutually_exclusive
A: "Access is role-based."  B: "Access to the log platform is role-based."
-> same_subject true, is_conflict false, kind none
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "existing_subject": {"type": "string"},
        "new_subject": {"type": "string"},
        "same_subject": {"type": "boolean"},
        "is_conflict": {"type": "boolean"},
        "kind": {"type": "string", "enum": sorted(KINDS)},
        "confidence": {"type": "number"},
        "explanation": {"type": "string"},
    },
    "required": ["existing_subject", "new_subject", "same_subject", "is_conflict", "kind", "confidence", "explanation"],
}


def _get_client():
    global _client
    if _client is None:
        import ollama

        _client = ollama.Client(host=OLLAMA_HOST, timeout=120)
    return _client


def available() -> bool:
    """Is Ollama up with the configured model pulled? Cached for 30s."""
    global _available
    checked_at, ok = _available
    if time.time() - checked_at < 30:
        return ok
    ok = False
    try:
        models = _get_client().list()
        names = [getattr(m, "model", None) or m.get("model") or m.get("name") for m in models["models"]]
        ok = any(n and (n == OLLAMA_MODEL or n.split(":")[0] == OLLAMA_MODEL.split(":")[0]) for n in names)
    except Exception:
        ok = False
    _available = (time.time(), ok)
    return ok


def _chat_json(system: str, user: str, schema: dict | str = "json") -> dict | None:
    try:
        resp = _get_client().chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format=schema,
            options={"temperature": 0, "num_ctx": 2048},
        )
        content = resp["message"]["content"]
        return json.loads(content)
    except Exception:
        return None


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _grounded(explanation: str, *passages: str) -> bool:
    """Every figure in the explanation must appear in one of the passages."""
    source = " ".join(passages)
    return all(n in source for n in _NUM.findall(explanation) if len(n) > 0)


def adjudicate(new_claim: str, existing_claim: str, citation: str, hint: str = "") -> dict | None:
    """Return {is_conflict, kind, confidence, explanation} or None if the LLM is unavailable."""
    if not available():
        return None
    user = f'A: "{existing_claim}"\nB: "{new_claim}"' + (f"\nNote: {hint}" if hint else "")
    out = _chat_json(SYSTEM, user, SCHEMA)
    if not isinstance(out, dict) or "is_conflict" not in out:
        return None
    if out.get("same_subject") is False:
        out["is_conflict"] = False
    kind = str(out.get("kind", "none"))
    explanation = " ".join(str(out.get("explanation", "")).split())
    try:
        confidence = float(out.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if confidence > 1:
        confidence /= 100  # small models sometimes answer on a 0-100 scale
    explanation = re.sub(r"^The record states", f"{citation} states", explanation)
    if not explanation or not _grounded(explanation, new_claim, existing_claim, citation):
        explanation = f'{citation} states "{existing_claim}"; the new file states "{new_claim}".'
    return {
        "is_conflict": bool(out["is_conflict"]),
        "kind": kind if kind in KINDS else "none",
        "confidence": max(0.0, min(1.0, confidence)),
        "explanation": explanation,
        "subjects": [str(out.get("existing_subject", "")), str(out.get("new_subject", ""))],
    }


def rewrite_claim(claim: str, context: str, section: str) -> str | None:
    """Optional ingest step: make a claim self-contained (resolve pronouns, attach subject)."""
    if not available():
        return None
    out = _chat_json(
        "Rewrite the target sentence as one self-contained factual claim. Resolve pronouns and "
        "attach the subject using the section title and context. Keep every value exactly. "
        'Respond with JSON: {"claim": "..."}',
        f"Section: {section}\nContext: {context}\nTarget sentence: {claim}",
    )
    if isinstance(out, dict) and isinstance(out.get("claim"), str) and out["claim"].strip():
        return out["claim"].strip()
    return None
