"""
End-to-end schema smoke test for the /analyze-ticket endpoint.

This script validates the API contract after every milestone. It exercises
both the happy path and the error paths, then asserts the response shape
and HTTP status codes.

Run modes:
    # 1) Against a live server (default port 8000):
       python scripts/smoke_schema.py [PORT]

    # 2) As a pytest test (auto-discovered under tests/):
       pytest tests/ -v

The two modes share the same test logic via the `_run_checks` helper.

Authoritative source for expected behavior:
    docs/SRS_PRD.md §3.1, docs/ARCHITECTURE.md §Data flow, problem §4.1.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, Dict, List, Tuple

import httpx

BASE_URL = "http://127.0.0.1:{port}"


# -----------------------------------------------------------------------------
# Each check is a (name, callable) pair.
# A check posts a payload and asserts on (status_code, response_body).
# -----------------------------------------------------------------------------

def _check_valid_minimal() -> Tuple[str, bool, str]:
    """Happy path: minimal valid request returns 200 with full schema."""
    payload = {"ticket_id": "TKT-001", "complaint": "hello there"}
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    ok = (
        r.status_code == 200
        and r.json()["ticket_id"] == "TKT-001"
        and r.json()["evidence_verdict"] == "insufficient_data"
        and r.json()["case_type"] == "other"
        and r.json()["severity"] == "low"
        and r.json()["department"] == "customer_support"
        and r.json()["human_review_required"] is True
        and isinstance(r.json()["customer_reply"], str)
        and len(r.json()["customer_reply"]) > 0
    )
    return ("valid_minimal", ok, f"got HTTP {r.status_code}: {r.text[:200]}")


def _check_valid_full_with_history() -> Tuple[str, bool, str]:
    """Happy path: full request with transaction_history returns 200."""
    payload = {
        "ticket_id": "TKT-002",
        "complaint": "I sent 5000 taka to a wrong number",
        "language": "en",
        "channel": "in_app_chat",
        "user_type": "customer",
        "campaign_context": "boishakh_bonanza_day_1",
        "transaction_history": [
            {
                "transaction_id": "TXN-9101",
                "timestamp": "2026-04-14T14:08:22Z",
                "type": "transfer",
                "amount": 5000,
                "counterparty": "+8801719876543",
                "status": "completed",
            }
        ],
    }
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    body = r.json()
    ok = (
        r.status_code == 200
        and body["ticket_id"] == "TKT-002"
        and "customer_reply" in body
    )
    return ("valid_full_with_history", ok, f"got HTTP {r.status_code}")


def _check_bangla() -> Tuple[str, bool, str]:
    """Bangla complaint is accepted (i18n templates land in Phase 5)."""
    payload = {
        "ticket_id": "TKT-BN",
        "complaint": "আমার টাকা আসেনি",
        "language": "bn",
    }
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    ok = r.status_code == 200 and r.json()["ticket_id"] == "TKT-BN"
    return ("bangla_complaint", ok, f"got HTTP {r.status_code}")


def _check_missing_ticket_id() -> Tuple[str, bool, str]:
    """Missing required field -> 400."""
    payload = {"complaint": "hello"}
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    body = r.json()
    ok = (
        r.status_code == 400
        and "ticket_id" in str(body)
        and body.get("error") in {"invalid_request_or_missing_field"}
    )
    return ("missing_ticket_id", ok, f"got HTTP {r.status_code}: {r.text[:200]}")


def _check_empty_complaint() -> Tuple[str, bool, str]:
    """Empty string complaint -> 422 (semantic, not malformed)."""
    payload = {"ticket_id": "X", "complaint": ""}
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    body = r.json()
    ok = (
        r.status_code == 422
        and body.get("error") == "invalid_request"
        and body.get("field") == "complaint"
    )
    return ("empty_complaint", ok, f"got HTTP {r.status_code}: {r.text[:200]}")


def _check_whitespace_complaint() -> Tuple[str, bool, str]:
    """Whitespace-only complaint -> 422 (caught by field_validator)."""
    payload = {"ticket_id": "X", "complaint": "   \n\t  "}
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    ok = r.status_code == 422
    return ("whitespace_complaint", ok, f"got HTTP {r.status_code}")


def _check_malformed_json() -> Tuple[str, bool, str]:
    """Malformed JSON -> 400."""
    with httpx.Client(timeout=10) as c:
        r = c.post(
            BASE_URL.format(port=PORT) + "/analyze-ticket",
            content=b"this is not json",
            headers={"Content-Type": "application/json"},
        )
    ok = r.status_code == 400
    return ("malformed_json", ok, f"got HTTP {r.status_code}")


def _check_empty_body() -> Tuple[str, bool, str]:
    """Empty JSON body -> 400 (missing ticket_id)."""
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json={})
    body = r.json()
    ok = r.status_code == 400 and body.get("field") == "ticket_id"
    return ("empty_body", ok, f"got HTTP {r.status_code}: {r.text[:200]}")


def _check_invalid_enum() -> Tuple[str, bool, str]:
    """Invalid enum value -> 400 (Pydantic enum literal violation)."""
    payload = {"ticket_id": "X", "complaint": "hi", "language": "english"}  # not in {en, bn, mixed}
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    ok = r.status_code == 400
    return ("invalid_language_enum", ok, f"got HTTP {r.status_code}")


def _check_invalid_transaction_type() -> Tuple[str, bool, str]:
    """Invalid transaction type -> 400 (caught by TransactionType Literal)."""
    payload = {
        "ticket_id": "X",
        "complaint": "hi",
        "transaction_history": [
            {
                "transaction_id": "TXN-X",
                "timestamp": "2026-04-14T14:08:22Z",
                "type": "withdrawal",  # not in enum
                "amount": 100,
                "counterparty": "x",
                "status": "completed",
            }
        ],
    }
    with httpx.Client(timeout=10) as c:
        r = c.post(BASE_URL.format(port=PORT) + "/analyze-ticket", json=payload)
    ok = r.status_code == 400
    return ("invalid_transaction_type", ok, f"got HTTP {r.status_code}")


def _check_health() -> Tuple[str, bool, str]:
    """Health endpoint returns ok."""
    with httpx.Client(timeout=10) as c:
        r = c.get(BASE_URL.format(port=PORT) + "/health")
    ok = r.status_code == 200 and r.json() == {"status": "ok"}
    return ("health", ok, f"got HTTP {r.status_code}: {r.text[:200]}")


# Ordered list of checks.
CHECKS: List[Callable[[], Tuple[str, bool, str]]] = [
    _check_health,
    _check_valid_minimal,
    _check_valid_full_with_history,
    _check_bangla,
    _check_missing_ticket_id,
    _check_empty_complaint,
    _check_whitespace_complaint,
    _check_malformed_json,
    _check_empty_body,
    _check_invalid_enum,
    _check_invalid_transaction_type,
]


def run_all(port: int) -> Tuple[int, int, List[Tuple[str, bool, str]]]:
    """Run every check. Returns (passed_count, failed_count, results)."""
    global PORT
    PORT = port
    results = []
    for chk in CHECKS:
        name, ok, detail = chk()
        results.append((name, ok, detail))
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    return passed, failed, results


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"=== Schema smoke test against port {port} ===\n")

    t0 = time.perf_counter()
    passed, failed, results = run_all(port)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Pretty print results
    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {name:<{width}}  {detail if not ok else ''}")

    print()
    print(f"=== {passed} passed, {failed} failed in {elapsed_ms:.0f}ms ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())