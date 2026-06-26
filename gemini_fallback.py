"""Optional AI enhancement using Google Gemini 2.0 Flash.

Design rules (see JOB_CONTEXT.md):
- This module is OPTIONAL. The rule-based pipeline in `analyzer.py` is the
  source of truth for all structured fields (case_type, verdict, department,
  severity, human_review_required, etc.).
- The AI is used ONLY to lightly polish two short text fields:
  `customer_reply` and `agent_summary`. It can never invent new structured
  facts.
- The AI is wrapped in hard safety constraints. Any response that contains a
  forbidden phrase, a refund/reversal promise, an ask for credentials, or a
  third-party phone number is discarded and the caller falls back to the
  deterministic rule-based text.
- The AI is also wrapped in timeouts and exception handling. Any failure
  (network, auth, rate-limit, timeout) is silently swallowed and the caller
  receives `None`, signalling "use the rule-based text".

Environment variables:
- GEMINI_API_KEY    : required for any AI call to be attempted
- GEMINI_MODEL      : optional, default "gemini-2.0-flash"
- GEMINI_TIMEOUT_S  : optional, default 4.0 seconds

If GEMINI_API_KEY is missing, `is_available()` returns False and the caller
should never invoke this module.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("queuestorm.gemini")

# Gemini 2.0 Flash REST endpoint (generateContent API). This is the stable
# public REST URL and works without any Google SDK install.
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_TIMEOUT_S = 4.0

# Forbidden phrases that mirror the safety checks in verify_samples.py and in
# analyzer.py. If the AI returns a string containing any of these, the whole
# AI output is discarded.
FORBIDDEN_PHRASES = [
    "share your pin",
    "share your otp",
    "share your password",
    "send your otp",
    "send your pin",
    "give me your pin",
    "give me your otp",
    "tell me your pin",
    "tell me your otp",
    "we will refund",
    "we'll refund",
    "we have refunded",
    "we've refunded",
    "we have reversed",
    "we'll reverse",
    "we will reverse",
    "your account will be unblocked",
    "call this number",
    "contact this number",
    "western union",
    "wire transfer",
]


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_api_key: str | None = None
_model: str = DEFAULT_MODEL
_timeout_s: float = DEFAULT_TIMEOUT_S
_initialized: bool = False


def _init_once() -> None:
    """Read env vars once. Safe to call repeatedly."""
    global _api_key, _model, _timeout_s, _initialized
    with _lock:
        if _initialized:
            return
        _api_key = os.getenv("GEMINI_API_KEY") or None
        _model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        try:
            _timeout_s = float(os.getenv("GEMINI_TIMEOUT_S", str(DEFAULT_TIMEOUT_S)))
        except ValueError:
            _timeout_s = DEFAULT_TIMEOUT_S
        _initialized = True
        if _api_key:
            log.info("Gemini fallback enabled (model=%s, timeout=%.1fs)", _model, _timeout_s)
        else:
            log.info("Gemini fallback disabled (no GEMINI_API_KEY)")


def is_available() -> bool:
    """Return True if a Gemini call can be attempted."""
    _init_once()
    return bool(_api_key)


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------

def _is_safe(text: str) -> bool:
    if not text:
        return False
    lc = text.lower()
    for p in FORBIDDEN_PHRASES:
        if p in lc:
            return False
    return True


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

# System instruction is a tight contract: do not ask for credentials, do not
# promise financial action, do not invent transactions, do not change the
# facts in the JSON metadata block.
_DEFAULT_SYSTEM_PROMPT = (
    "You are a safety-first customer support assistant for a Bangladeshi "
    "fintech company. You rewrite two short text fields to be clearer and "
    "more polite in the same language as the customer.\n"
    "\n"
    "HARD RULES (must never be violated):\n"
    "1. NEVER ask the customer for their PIN, OTP, password, or full card number.\n"
    "2. NEVER promise a refund, reversal, recovery, or account unblock. Use "
    "   neutral language like 'any eligible amount will be returned through "
    "   official channels'.\n"
    "3. NEVER instruct the customer to call or contact a specific phone "
    "   number, person, or third party. Only reference official support "
    "   channels.\n"
    "4. NEVER invent transaction IDs, amounts, counterparties, or facts "
    "   that are not present in the JSON metadata provided.\n"
    "5. Keep the same language as the customer's complaint (English or "
    "   Bangla). If a Bangla script is used, keep it in Bangla script.\n"
    "6. Be brief (1-3 sentences) and professional.\n"
    "7. Return ONLY a JSON object with keys 'customer_reply' and "
    "   'agent_summary'. No markdown fences, no commentary, no extra keys."
)


def _load_system_prompt() -> str:
    """Load the editable markdown guardrail prompt if present."""
    prompt_path = Path(__file__).with_name("gemini_fallback.md")
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return _DEFAULT_SYSTEM_PROMPT
    return text or _DEFAULT_SYSTEM_PROMPT


def _build_user_prompt(
    base_reply: str,
    base_summary: str,
    case_type: str,
    severity: str,
    department: str,
    evidence_verdict: str,
    txn_id: str | None,
    language: str,
) -> str:
    txn_str = txn_id if txn_id else "null"
    return (
        "Rewrite the two text fields below for clarity and tone. Keep the "
        "facts identical. Do not change the structured metadata.\n"
        "\n"
        "Metadata (read-only, do not change):\n"
        f"  case_type: {case_type}\n"
        f"  severity: {severity}\n"
        f"  department: {department}\n"
        f"  evidence_verdict: {evidence_verdict}\n"
        f"  relevant_transaction_id: {txn_str}\n"
        f"  language: {language}\n"
        "\n"
        "Field 1: customer_reply (rewrite this):\n"
        f"  {base_reply}\n"
        "\n"
        "Field 2: agent_summary (rewrite this):\n"
        f"  {base_summary}\n"
        "\n"
        "Return a single JSON object: {\"customer_reply\": \"...\", "
        "\"agent_summary\": \"...\"}."
    )


# ---------------------------------------------------------------------------
# Call
# ---------------------------------------------------------------------------

def _post_gemini(payload: dict[str, Any]) -> dict[str, Any] | None:
    _init_once()
    if not _api_key:
        return None
    url = _ENDPOINT.format(model=_model) + f"?key={_api_key}"
    try:
        with httpx.Client(timeout=_timeout_s) as client:
            r = client.post(
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
            )
    except httpx.TimeoutException:
        log.warning("Gemini call timed out after %.1fs", _timeout_s)
        return None
    except httpx.HTTPError as e:
        log.warning("Gemini HTTP error: %s", e)
        return None
    except Exception as e:
        log.warning("Gemini unexpected error: %s", e)
        return None

    if r.status_code != 200:
        log.warning("Gemini non-200: %s %s", r.status_code, r.text[:200])
        return None

    try:
        return r.json()
    except ValueError:
        log.warning("Gemini returned non-JSON body")
        return None


def _extract_text(resp: dict[str, Any]) -> str | None:
    """Pull the model's text out of a Gemini generateContent response."""
    try:
        candidates = resp.get("candidates") or []
        if not candidates:
            return None
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        # Concatenate all text parts (defensive: some responses split parts).
        chunks: list[str] = []
        for p in parts:
            t = p.get("text")
            if isinstance(t, str) and t:
                chunks.append(t)
        text = "".join(chunks).strip()
        return text or None
    except (AttributeError, TypeError, IndexError):
        return None


def _parse_json_payload(text: str) -> dict[str, Any] | None:
    """Gemini sometimes wraps JSON in ```json fences. Strip and parse."""
    s = text.strip()
    # Strip code fences if present.
    if s.startswith("```"):
        # Remove first line (e.g. ```json) and trailing fence.
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    try:
        import json
        obj = json.loads(s)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if "customer_reply" not in obj or "agent_summary" not in obj:
        return None
    if not isinstance(obj["customer_reply"], str) or not isinstance(obj["agent_summary"], str):
        return None
    return obj


def polish(
    base_reply: str,
    base_summary: str,
    case_type: str,
    severity: str,
    department: str,
    evidence_verdict: str,
    txn_id: str | None,
    language: str,
) -> tuple[str, str] | None:
    """Optionally rewrite `customer_reply` and `agent_summary` using Gemini.

    Returns (customer_reply, agent_summary) on success, or `None` if the AI is
    unavailable, fails, or returns unsafe content. Caller must always have a
    deterministic fallback ready.
    """
    if not is_available():
        return None

    payload = {
        "systemInstruction": {
            "parts": [{"text": _load_system_prompt()}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": _build_user_prompt(
                        base_reply, base_summary, case_type, severity,
                        department, evidence_verdict, txn_id, language,
                    )}
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "topP": 0.9,
            "maxOutputTokens": 512,
        },
        # Conservative safety settings: keep the model's own safety on, but
        # we ALSO re-check output with FORBIDDEN_PHRASES on our side.
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        ],
    }

    resp = _post_gemini(payload)
    if not resp:
        return None

    text = _extract_text(resp)
    if not text:
        return None

    obj = _parse_json_payload(text)
    if not obj:
        log.warning("Gemini returned payload that was not valid JSON")
        return None

    reply = obj["customer_reply"].strip()
    summary = obj["agent_summary"].strip()

    if not _is_safe(reply) or not _is_safe(summary):
        log.warning("Gemini returned unsafe content; discarding AI output")
        return None

    # Light length sanity check — the AI should not bloat the response.
    if len(reply) > 800 or len(summary) > 500:
        log.warning("Gemini returned oversized output; discarding")
        return None

    return reply, summary


# ---------------------------------------------------------------------------
# Manual smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # `python gemini_fallback.py` prints whether the module is usable.
    print("available:", is_available())
    print("model:", _model if _initialized else "(not initialized)")
