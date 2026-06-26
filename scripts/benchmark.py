#!/usr/bin/env python3
"""
Latency benchmark for QueueStorm Investigator.

Hammers the running /analyze-ticket endpoint with a configurable number
of requests and prints latency statistics (mean, p50, p95, p99, max).
This is the gate we run right before submission to make sure we stay
well under the rubric's 5-second p95 target.

Usage:
    python scripts/benchmark.py [PORT] [--requests N] [--warmup N]
    PORT=8765 python scripts/benchmark.py --requests 200 --warmup 20

Defaults: --requests 100, --warmup 10.

Reports:
* requests/sec
* mean latency (ms)
* p50 / p95 / p99 / max latency (ms)
* any non-200 responses
* verdict vs the rubric target (5s p95)

Exit code:
* 0 if p95 < 5s (rubric satisfied)
* 1 if p95 >= 5s (rubric violated)
* 2 if server unreachable
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx


BASE_URL = "http://127.0.0.1:{port}"
RUBRIC_P95_MS = 5000  # rubric: ≤ 5s end-to-end


def _load_payload() -> Dict[str, Any]:
    """A realistic, full-envelope payload — mirrors SAMPLE-01 input.

    We pick a strong-signal case (wrong transfer with matched tx) so the
    pipeline runs every stage: pre_scan, evidence scoring, classifier
    cascade, routing, i18n templating, and post_scan. This is the worst
    case for latency.
    """
    return {
        "ticket_id": "BENCH",
        "complaint": (
            "I sent 5000 taka to +8801719876543 yesterday but it went to the "
            "wrong number. The recipient says they did not receive it. Please "
            "help me get my money back."
        ),
        "language": "en",
        "channel": "in_app_chat",
        "user_type": "customer",
        "campaign_context": "boishakh_bonanza_day_1",
        "transaction_history": [
            {
                "transaction_id": "TXN-BENCH",
                "timestamp": "2026-04-14T14:08:22Z",
                "type": "transfer",
                "amount": 5000,
                "counterparty": "+8801719876543",
                "status": "completed",
            },
            {
                "transaction_id": "TXN-BENCH-OLD",
                "timestamp": "2026-04-10T09:00:00Z",
                "type": "payment",
                "amount": 200,
                "counterparty": "BILLER",
                "status": "completed",
            },
        ],
    }


def _percentile(sorted_values: List[float], pct: float) -> float:
    """Compute the pct-th percentile of a sorted list.

    Linear interpolation between closest ranks. Returns 0.0 for empty input.
    """
    if not sorted_values:
        return 0.0
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    # Nearest-rank with linear interp
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _summarize(latencies_ms: List[float]) -> Dict[str, float]:
    sorted_lat = sorted(latencies_ms)
    return {
        "count": len(sorted_lat),
        "mean_ms": statistics.mean(sorted_lat) if sorted_lat else 0.0,
        "min_ms": sorted_lat[0] if sorted_lat else 0.0,
        "p50_ms": _percentile(sorted_lat, 50),
        "p95_ms": _percentile(sorted_lat, 95),
        "p99_ms": _percentile(sorted_lat, 99),
        "max_ms": sorted_lat[-1] if sorted_lat else 0.0,
    }


def _run_benchmark(port: int, total: int, warmup: int) -> Dict[str, Any]:
    base = BASE_URL.format(port=port)
    payload = _load_payload()

    # Pre-flight: server is reachable.
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(base + "/health")
            r.raise_for_status()
    except Exception as exc:
        print(f"ERROR: cannot reach {base}/health: {exc}", file=sys.stderr)
        sys.exit(2)

    # Warmup — never measured. First request pays for JIT/cache priming.
    if warmup > 0:
        with httpx.Client(timeout=10) as c:
            for _ in range(warmup):
                c.post(base + "/analyze-ticket", json=payload)

    # Measured requests — sequential for honest latency numbers.
    latencies: List[float] = []
    errors: List[str] = []
    with httpx.Client(timeout=10) as c:
        t_start = time.perf_counter()
        for i in range(total):
            t0 = time.perf_counter()
            try:
                r = c.post(base + "/analyze-ticket", json=payload)
            except Exception as exc:
                errors.append(f"#{i}: {exc}")
                continue
            elapsed_ms = (time.perf_counter() - t0) * 1000
            if r.status_code != 200:
                errors.append(f"#{i}: HTTP {r.status_code}: {r.text[:120]}")
                continue
            latencies.append(elapsed_ms)
        wall_clock_s = time.perf_counter() - t_start

    stats = _summarize(latencies)
    stats["rps"] = total / wall_clock_s if wall_clock_s > 0 else 0.0
    stats["errors"] = len(errors)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=10)
    args = parser.parse_args()

    print(f"=== Latency benchmark against port {args.port} ===")
    print(f"    requests: {args.requests}  warmup: {args.warmup}")
    print()

    stats = _run_benchmark(args.port, args.requests, args.warmup)

    print(f"  count     : {stats['count']:>8}")
    print(f"  errors    : {stats['errors']:>8}")
    print(f"  rps       : {stats['rps']:>8.1f}")
    print(f"  mean ms   : {stats['mean_ms']:>8.1f}")
    print(f"  min  ms   : {stats['min_ms']:>8.1f}")
    print(f"  p50  ms   : {stats['p50_ms']:>8.1f}")
    print(f"  p95  ms   : {stats['p95_ms']:>8.1f}")
    print(f"  p99  ms   : {stats['p99_ms']:>8.1f}")
    print(f"  max  ms   : {stats['max_ms']:>8.1f}")
    print()

    if stats["errors"]:
        print(f"WARNING: {stats['errors']} requests errored (see stderr).")
        return 1

    if stats["p95_ms"] >= RUBRIC_P95_MS:
        print(f"FAIL: p95 {stats['p95_ms']:.0f}ms exceeds rubric target {RUBRIC_P95_MS}ms")
        return 1

    headroom = RUBRIC_P95_MS / max(stats["p95_ms"], 1.0)
    print(
        f"OK: p95 {stats['p95_ms']:.0f}ms is {headroom:.1f}x under the rubric target of "
        f"{RUBRIC_P95_MS}ms"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())