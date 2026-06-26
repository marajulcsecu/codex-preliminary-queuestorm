"""Verify the service against SUST_Preli_Sample_Cases.json.

Usage:
    python verify_samples.py                  # hits http://127.0.0.1:8000
    python verify_samples.py --base http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent
SAMPLES_PATH = ROOT / "SUST_Preli_Sample_Cases.json"
DEFAULT_LOG_PATH = ROOT / "_verify_samples_responses.jsonl"

REQUIRED_FIELDS = [
    "ticket_id",
    "relevant_transaction_id",
    "evidence_verdict",
    "case_type",
    "severity",
    "department",
    "agent_summary",
    "recommended_next_action",
    "customer_reply",
    "human_review_required",
]
STRICT_FIELDS = [
    "ticket_id",
    "relevant_transaction_id",
    "evidence_verdict",
    "case_type",
    "severity",
    "department",
    "human_review_required",
]
STRING_FIELDS = ["agent_summary", "recommended_next_action", "customer_reply"]

ALLOWED_ENUMS = {
    "evidence_verdict": {"consistent", "inconsistent", "insufficient_data"},
    "case_type": {
        "wrong_transfer",
        "payment_failed",
        "refund_request",
        "duplicate_payment",
        "merchant_settlement_delay",
        "agent_cash_in_issue",
        "phishing_or_social_engineering",
        "other",
    },
    "severity": {"low", "medium", "high", "critical"},
    "department": {
        "customer_support",
        "dispute_resolution",
        "payments_ops",
        "merchant_operations",
        "agent_operations",
        "fraud_risk",
    },
}

CREDENTIAL_REQUEST_RE = re.compile(
    r"\b(?:share|send|give|tell|provide|enter|submit|confirm|verify)\b"
    r".{0,40}\b(?:pin|otp|password|cvv|card number)\b",
    re.IGNORECASE,
)
UNSAFE_PROMISE_RE = re.compile(
    r"\b(?:we|our team|i)\s+(?:will|shall|can|guarantee to)\s+"
    r"(?:refund|reverse|recover|unblock|return your money)\b",
    re.IGNORECASE,
)
UNOFFICIAL_CONTACT_RE = re.compile(
    r"\b(?:call|contact|whatsapp|message)\b.{0,30}(?:\+?\d[\d\s-]{7,}|\b01[3-9]\d{8}\b)",
    re.IGNORECASE,
)


def compare(actual: dict[str, Any], expected: dict[str, Any], *, loose_severity: bool = False) -> list[str]:
    issues: list[str] = []
    if not isinstance(actual, dict):
        return [f"response body must be a JSON object, got {type(actual).__name__}"]

    for field in REQUIRED_FIELDS:
        if field not in actual:
            issues.append(f"{field}: missing required output field")

    for field, allowed in ALLOWED_ENUMS.items():
        if field in actual and actual.get(field) not in allowed:
            issues.append(f"{field}: invalid enum value {actual.get(field)!r}")

    if "human_review_required" in actual and not isinstance(actual.get("human_review_required"), bool):
        issues.append("human_review_required: must be a boolean")

    if "confidence" in actual:
        confidence = actual.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            issues.append(f"confidence: must be a number from 0.0 to 1.0, got {confidence!r}")
        elif not 0.0 <= float(confidence) <= 1.0:
            issues.append(f"confidence: expected 0.0 <= value <= 1.0, got {confidence!r}")

    if "reason_codes" in actual:
        reason_codes = actual.get("reason_codes")
        if not isinstance(reason_codes, list) or not all(isinstance(x, str) and x.strip() for x in reason_codes):
            issues.append("reason_codes: must be a list of non-empty strings")

    for f in STRICT_FIELDS:
        if loose_severity and f == "severity":
            if actual.get("severity") not in {expected.get("severity"), _loose_severity(expected.get("severity"))}:
                issues.append(
                    f"severity: expected {expected.get('severity')!r} (or explicit loose match), got {actual.get('severity')!r}"
                )
            continue
        if f in expected and actual.get(f) != expected.get(f):
            issues.append(f"{f}: expected {expected.get(f)!r}, got {actual.get(f)!r}")

    # String fields: require real, useful text.
    for f in STRING_FIELDS:
        val = actual.get(f)
        if not isinstance(val, str):
            issues.append(f"{f}: expected non-empty string, got {type(val).__name__}")
            continue
        if not val.strip():
            issues.append(f"{f}: empty")
        elif len(val.strip()) < 20:
            issues.append(f"{f}: too short to be useful")

    expected_txn = expected.get("relevant_transaction_id")
    if expected_txn:
        combined_text = " ".join(str(actual.get(f) or "") for f in STRING_FIELDS)
        if expected_txn not in combined_text:
            issues.append(f"text fields: expected transaction id {expected_txn!r} to appear in at least one text field")

    if expected.get("evidence_verdict") == "insufficient_data" and actual.get("relevant_transaction_id") is not None:
        issues.append("relevant_transaction_id: must be null when expected verdict is insufficient_data")

    for field in STRING_FIELDS:
        text = actual.get(field)
        if isinstance(text, str):
            issues.extend(_safety_issues(field, text))
    return issues


def _safety_issues(field: str, text: str) -> list[str]:
    issues: list[str] = []
    if CREDENTIAL_REQUEST_RE.search(text):
        issues.append(f"{field}: appears to request sensitive credentials")
    if UNSAFE_PROMISE_RE.search(text):
        issues.append(f"{field}: appears to promise refund/reversal/recovery/unblock")
    if UNOFFICIAL_CONTACT_RE.search(text):
        issues.append(f"{field}: appears to direct customer to an unofficial contact number")

    lower = text.lower()
    banned_exact = [
        "we have refunded",
        "we've refunded",
        "we have reversed",
        "we've reversed",
        "your account will be unblocked",
        "guaranteed refund",
        "guaranteed reversal",
        "western union",
        "wire transfer",
    ]
    for phrase in banned_exact:
        if phrase in lower:
            issues.append(f"{field}: contains forbidden phrase {phrase!r}")
    return issues


def _loose_severity(s: str) -> str:
    return {
        "low": "medium",
        "medium": "high",
        "high": "critical",
        "critical": "high",
    }.get(s or "", s)


def _write_log(log_path: Path, event: dict[str, Any]) -> None:
    event = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        **event,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--log-file",
        default=str(DEFAULT_LOG_PATH),
        help="Write full request/response debug logs as JSONL. Use empty string to disable.",
    )
    parser.add_argument(
        "--loose-severity",
        action="store_true",
        help="Allow one-level severity drift. By default severity must match the sample exactly.",
    )
    args = parser.parse_args()
    log_path = Path(args.log_file).resolve() if args.log_file else None

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("", encoding="utf-8")

    if not SAMPLES_PATH.exists():
        print(f"!! sample file not found: {SAMPLES_PATH}")
        return 2

    data = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    cases = data.get("cases") or []

    print(f"Hitting {args.base}/analyze-ticket with {len(cases)} sample cases")
    if log_path:
        print(f"Writing full responses to {log_path}")
    print()
    total_issues = 0
    failed = 0

    with httpx.Client(timeout=30.0) as client:
        # 1) health check
        try:
            r = client.get(f"{args.base}/health")
            try:
                health_body: Any = r.json()
            except ValueError:
                health_body = r.text
            if log_path:
                _write_log(log_path, {
                    "event": "health_check",
                    "method": "GET",
                    "url": f"{args.base}/health",
                    "status_code": r.status_code,
                    "response": health_body,
                })
            print(f"GET /health -> {r.status_code} {health_body}")
            if r.status_code != 200 or not isinstance(health_body, dict) or health_body.get("status") != "ok":
                print("!! health check failed")
                return 1
        except Exception as e:
            if log_path:
                _write_log(log_path, {
                    "event": "health_check",
                    "method": "GET",
                    "url": f"{args.base}/health",
                    "error": str(e),
                })
            print(f"!! cannot reach {args.base}: {e}")
            return 1

        for case in cases:
            cid = case.get("id")
            label = case.get("label")
            inp = case.get("input") or {}
            exp = case.get("expected_output") or {}

            try:
                r = client.post(f"{args.base}/analyze-ticket", json=inp)
            except Exception as e:
                if log_path:
                    _write_log(log_path, {
                        "event": "sample_case",
                        "case_id": cid,
                        "label": label,
                        "method": "POST",
                        "url": f"{args.base}/analyze-ticket",
                        "request": inp,
                        "expected": exp,
                        "error": str(e),
                        "issues": ["network error"],
                    })
                print(f"!! {cid} network error: {e}")
                failed += 1
                continue

            if r.status_code != 200:
                if log_path:
                    _write_log(log_path, {
                        "event": "sample_case",
                        "case_id": cid,
                        "label": label,
                        "method": "POST",
                        "url": f"{args.base}/analyze-ticket",
                        "request": inp,
                        "expected": exp,
                        "status_code": r.status_code,
                        "response_text": r.text,
                        "issues": [f"HTTP {r.status_code}"],
                    })
                print(f"!! {cid} HTTP {r.status_code}: {r.text[:200]}")
                failed += 1
                total_issues += 1
                continue

            try:
                actual = r.json()
            except ValueError:
                if log_path:
                    _write_log(log_path, {
                        "event": "sample_case",
                        "case_id": cid,
                        "label": label,
                        "method": "POST",
                        "url": f"{args.base}/analyze-ticket",
                        "request": inp,
                        "expected": exp,
                        "status_code": r.status_code,
                        "response_text": r.text,
                        "issues": ["response was not JSON"],
                    })
                print(f"!! {cid} response was not JSON: {r.text[:200]}")
                failed += 1
                total_issues += 1
                continue

            issues = compare(actual, exp, loose_severity=args.loose_severity)
            status = "OK" if not issues else "FAIL"
            if log_path:
                _write_log(log_path, {
                    "event": "sample_case",
                    "case_id": cid,
                    "label": label,
                    "method": "POST",
                    "url": f"{args.base}/analyze-ticket",
                    "request": inp,
                    "expected": exp,
                    "status_code": r.status_code,
                    "response": actual,
                    "result": status,
                    "issues": issues,
                })
            if issues:
                failed += 1
                total_issues += len(issues)
            print(f"[{status}] {cid} {label}")
            for issue in issues:
                print(f"     - {issue}")
            # Always print the verdict trio for quick eyeballing
            print(
                f"     verdict={actual.get('evidence_verdict')!r} "
                f"case={actual.get('case_type')!r} "
                f"dept={actual.get('department')!r} "
                f"sev={actual.get('severity')!r} "
                f"txn={actual.get('relevant_transaction_id')!r}"
            )
            print()

    print(f"Done. {failed}/{len(cases)} cases failed, {total_issues} total issues.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
