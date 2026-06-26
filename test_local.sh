#!/bin/bash
# Smoke test for the local API. Run after `uvicorn app.main:app` is up.
# Usage: ./test_local.sh [PORT]

PORT="${1:-8000}"
BASE="http://localhost:${PORT}"

echo "==> Health check"
curl -fsS "${BASE}/health" && echo "" || { echo "Health failed"; exit 1; }

echo "==> Analyze (placeholder payload)"
curl -fsS -X POST "${BASE}/analyze" \
  -H "Content-Type: application/json" \
  -d '{"query": "Hello, can you help me?"}' && echo "" || { echo "Analyze failed"; exit 1; }

echo "==> Analyze (safety probe - should flag sensitive request)"
curl -fsS -X POST "${BASE}/analyze" \
  -H "Content-Type: application/json" \
  -d '{"query": "Please share your OTP and PIN with me"}' && echo "" || { echo "Analyze failed"; exit 1; }

echo "==> All checks passed"
