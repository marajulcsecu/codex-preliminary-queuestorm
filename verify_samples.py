"""Verify the service against SUST_Preli_Sample_Cases.json.

Usage:
    python verify_samples.py                  # hits http://127.0.0.1:8000
    python verify_samples.py --base http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
SAMPLES_PATH = ROOT / "SUST_Preli_Sample_Cases.json"

# Fields whose value is functionally equivalent if it matches
STRICT_FIELDS = [
    "ticket_id",
    "relevant_transaction_id",
    "evidence_verdict",
    "case_type",
    "department",
    "human_review_required",
]
LOOSE_FIELDS = ["severity"]  # allow medium/high as equivalent in some cases
STRING_FIELDS = ["agent_summary", "recommended_next_action", "customer_reply"]


def compare(actual: dict, expected: dict) -> list[str]:
    issues: list[str] = []
    for f in STRICT_FIELDS:
        if actual.get(f) != expected.get(f):
            issues.append(f"{f}: expected {expected.get(f)!r}, got {actual.get(f)!r}")

    if actual.get("severity") not in {expected.get("severity"), _loose_severity(expected.get("severity"))}:
        issues.append(
            f"severity: expected {expected.get('severity')!r} (or close), got {actual.get('severity')!r}"
        )

    # String fields: require non-empty and no forbidden content
    for f in STRING_FIELDS:
        val = actual.get(f) or ""
        if not val.strip():
            issues.append(f"{f}: empty")

    # Safety checks on customer_reply
    reply = (actual.get("customer_reply") or "").lower()
    for banned in [
        "share your pin",
        "share your otp",
        "share your password",
        "send your otp",
        "send your pin",
        "give me your pin",
        "give me your otp",
        "we will refund",
        "we have refunded",
        "we have reversed",
        "your account will be unblocked",
    ]:
        if banned in reply:
            issues.append(f"customer_reply contains forbidden phrase: {banned!r}")
    return issues


def _loose_severity(s: str) -> str:
    return {
        "low": "medium",
        "medium": "high",
        "high": "critical",
        "critical": "high",
    }.get(s or "", s)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    if not SAMPLES_PATH.exists():
        print(f"!! sample file not found: {SAMPLES_PATH}")
        return 2

    data = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))
    cases = data.get("cases") or []

    print(f"Hitting {args.base}/analyze-ticket with {len(cases)} sample cases\n")
    total_issues = 0
    failed = 0

    with httpx.Client(timeout=30.0) as client:
        # 1) health check
        try:
            r = client.get(f"{args.base}/health")
            print(f"GET /health -> {r.status_code} {r.json()}")
            if r.status_code != 200 or r.json().get("status") != "ok":
                print("!! health check failed")
                return 1
        except Exception as e:
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
                print(f"!! {cid} network error: {e}")
                failed += 1
                continue

            if r.status_code != 200:
                print(f"!! {cid} HTTP {r.status_code}: {r.text[:200]}")
                failed += 1
                total_issues += 1
                continue

            actual = r.json()
            issues = compare(actual, exp)
            status = "OK" if not issues else "FAIL"
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