#!/usr/bin/env python3
"""
End-to-end sample-case runner for QueueStorm Investigator.

Posts every case from ``Question Provided/SUST_Preli_Sample_Cases.json``
to a running /analyze-ticket endpoint and prints a side-by-side
comparison table. Useful both as a manual pre-submit check and as a
gate that the team runs after every deploy.

Usage:
    python scripts/run_samples.py [PORT]
    PORT=8000 python scripts/run_samples.py

The script:
* reads the 10 sample cases from the JSON pack (sibling of the project root)
* POSTs each one to http://127.0.0.1:<port>/analyze-ticket
* prints a table: id | case_type | sev | dept | hr | verdict | tx_id
  with PASS / MISMATCH per cell
* exits 0 when every dimension matches the expected output for all 10 cases
* exits 1 otherwise

This is the single most useful one-shot verification in the project.
If this script is green, the reasoning pipeline is healthy.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx


SAMPLE_PATH = (
    Path(__file__).resolve().parents[2]
    / "Question Provided"
    / "SUST_Preli_Sample_Cases.json"
)
BASE_URL = "http://127.0.0.1:{port}"


def _load_cases() -> List[Dict[str, Any]]:
    with SAMPLE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)["cases"]


def _compare(
    resp: Dict[str, Any], exp: Dict[str, Any]
) -> Tuple[int, int, List[str]]:
    """Compare a response body to the expected. Returns (pass, fail, list_of_mismatches)."""
    mismatches: List[str] = []
    for key in (
        "case_type",
        "severity",
        "department",
        "human_review_required",
        "evidence_verdict",
        "relevant_transaction_id",
    ):
        if resp.get(key) != exp.get(key):
            mismatches.append(f"{key}: exp={exp.get(key)!r} got={resp.get(key)!r}")
    return (6 - len(mismatches)), len(mismatches), mismatches


def main(argv: List[str]) -> int:
    """CLI: python scripts/run_samples.py [PORT|URL]

    Accepts either an integer port (talks to 127.0.0.1) or a full URL
    starting with http(s):// (e.g. the Railway public endpoint).
    """
    raw = argv[1] if len(argv) > 1 else "8000"
    if raw.startswith("http://") or raw.startswith("https://"):
        base = raw.rstrip("/")
    else:
        port = int(raw)
        base = BASE_URL.format(port=port)
    cases = _load_cases()

    # Sanity: server is reachable.
    try:
        with httpx.Client(timeout=5) as c:
            h = c.get(base + "/health")
            h.raise_for_status()
    except Exception as exc:
        print(f"ERROR: cannot reach {base}/health: {exc}")
        return 2

    print(f"=== Sample case E2E run against {base} ===\n")

    header = f"{'id':10s} {'case_type':28s} {'sev':8s} {'dept':22s} {'hr':5s} {'verdict':18s} {'tx_id':12s} {'status'}"
    print(header)
    print("-" * len(header))

    total_pass = 0
    total_fail = 0
    total_time_ms = 0.0
    failures: List[Tuple[str, List[str]]] = []

    for case in cases:
        t0 = time.perf_counter()
        with httpx.Client(timeout=10) as c:
            try:
                r = c.post(base + "/analyze-ticket", json=case["input"])
            except Exception as exc:
                print(f"{case['id']:10s} HTTP ERROR: {exc}")
                total_fail += 1
                failures.append((case["id"], [f"http error: {exc}"]))
                continue
        elapsed_ms = (time.perf_counter() - t0) * 1000
        total_time_ms += elapsed_ms

        if r.status_code != 200:
            print(f"{case['id']:10s} HTTP {r.status_code} {r.text[:80]}")
            total_fail += 1
            failures.append((case["id"], [f"http {r.status_code}: {r.text[:200]}"]))
            continue

        resp = r.json()
        exp = case["expected_output"]
        passed, failed, mismatches = _compare(resp, exp)
        total_pass += passed
        total_fail += failed
        if failed:
            failures.append((case["id"], mismatches))

        marker = "PASS" if failed == 0 else f"MISMATCH ({failed})"
        print(
            f"{case['id']:10s} {str(resp.get('case_type')):28s} "
            f"{str(resp.get('severity')):8s} "
            f"{str(resp.get('department')):22s} "
            f"{str(resp.get('human_review_required')):5s} "
            f"{str(resp.get('evidence_verdict')):18s} "
            f"{str(resp.get('relevant_transaction_id')):12s} "
            f"{marker}  ({elapsed_ms:.0f}ms)"
        )

    print()
    print(f"Total: {total_pass} pass, {total_fail} fail across {len(cases)} cases")
    print(f"Total time: {total_time_ms:.0f}ms  "
          f"(avg {total_time_ms / len(cases):.0f}ms per case)")

    if failures:
        print("\n--- Mismatches ---")
        for case_id, mismatches in failures:
            print(f"  {case_id}:")
            for m in mismatches:
                print(f"    - {m}")
        return 1

    print("\nAll 10 sample cases pass on all 6 dimensions.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
