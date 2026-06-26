#!/usr/bin/env bash
# Run pytest against a live server. The schema tests assume the server is
# already running on $PORT (default 8765).
#
# Usage:
#   PORT=8765 ./scripts/run_tests.sh
#   # or:
#   ./scripts/run_tests.sh 8765
#
# To start the server in another terminal:
#   .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8765

set -euo pipefail
PORT="${1:-${PORT:-8765}}"
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
    echo "ERROR: .venv not found. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx"
    exit 1
fi

echo "=== Installing pytest + httpx if missing ==="
.venv/bin/pip install --quiet pytest httpx 2>&1 | tail -3 || true

echo ""
echo "=== Checking server on port ${PORT} ==="
if ! curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null; then
    echo "ERROR: no server on port ${PORT}. Start one first:"
    echo "  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"
    exit 1
fi

echo ""
echo "=== Running pytest ==="
PORT="${PORT}" .venv/bin/pytest tests/ -v
