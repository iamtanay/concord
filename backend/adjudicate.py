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

SYSTEM = (
    "You are an auditor checking a knowledge base for contradictions. You are given one passage "
    "already in the record and one passage from a new upload. Decide whether they genuinely "
    "contradict: both refer to the same specific fact, policy, entity, or value, and cannot both be "
    "true at the same time. Different scopes, different subjects, or one being more detailed than "
    "the other are NOT contradictions. Respond with JSON only, using exactly these keys:\n"
    '{"is_conflict": true|false, "kind": "value_mismatch"|"mutually_exclusive"|"status_change"|"none", '
    '"confidence": 0.0-1.0, "explanation": "one sentence"}\n'
    "The explanation must cite the source given and quote the exact values from both passages. "
    "Never invent values."
)


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


def _chat_json(system: str, user: str) -> dict | None:
    try:
        resp = _get_client().chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            format="json",
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
    user = (
        f"In the record ({citation}):\n\"{existing_claim}\"\n\n"
        f"New upload:\n\"{new_claim}\"\n"
        + (f"\nNote: {hint}\n" if hint else "")
    )
    out = _chat_json(SYSTEM, user)
    if not isinstance(out, dict) or "is_conflict" not in out:
        return None
    kind = str(out.get("kind", "none"))
    explanation = str(out.get("explanation", "")).strip()
    try:
        confidence = float(out.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if not explanation or not _grounded(explanation, new_claim, existing_claim, citation):
        explanation = f'{citation} states "{existing_claim}"; the new file states "{new_claim}".'
    return {
        "is_conflict": bool(out["is_conflict"]),
        "kind": kind if kind in KINDS else "none",
        "confidence": max(0.0, min(1.0, confidence)),
        "explanation": explanation,
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
