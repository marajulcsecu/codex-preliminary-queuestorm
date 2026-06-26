"""
Pytest wrapper around scripts/smoke_schema.py.

Run with:
    # First start the server in another terminal:
    uvicorn app.main:app --host 0.0.0.0 --port 8765

    # Then:
    pytest tests/test_schema.py -v

Or set PORT env var to override the default.
"""

from __future__ import annotations

import os

from scripts.smoke_schema import run_all


def test_schema_smoke():
    """Run all 11 schema checks. Fails the suite if any check fails."""
    port = int(os.environ.get("PORT", "8765"))
    passed, failed, results = run_all(port)
    failures = [r for r in results if not r[1]]
    assert failed == 0, (
        f"{failed}/{passed + failed} schema checks failed:\n"
        + "\n".join(f"  - {name}: {detail}" for name, _, detail in failures)
    )


if __name__ == "__main__":
    # Allow direct execution: python tests/test_schema.py
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    passed, failed, _ = run_all(port)
    sys.exit(0 if failed == 0 else 1)