#!/usr/bin/env bash
# Phase 0 smoke test — verifies /health and the /analyze-ticket placeholder.
# Used as the "Verify" gate at the end of Step 0.2.
#
# Usage:  ./scripts/smoke_phase0.sh [PORT]
# Default port: 8000 (set PORT=8001 if you have something else on 8000)

set -euo pipefail
PORT="${1:-${PORT:-8000}}"
BASE="http://127.0.0.1:${PORT}"

echo "=== Smoke test against ${BASE} ==="

echo -n "[1/4] GET /health ... "
HEALTH=$(curl -fsS "${BASE}/health")
echo "${HEALTH}"
[[ "${HEALTH}" == '{"status":"ok"}' ]] || { echo "FAIL: /health did not return ok"; exit 1; }

echo -n "[2/4] POST /analyze-ticket (valid input) ... "
RESP=$(curl -fsS -X POST "${BASE}/analyze-ticket" \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"TKT-001","complaint":"hello there"}')
echo "${RESP}"
echo "${RESP}" | python3 -c "import sys, json; d=json.load(sys.stdin); assert d['ticket_id']=='TKT-001', d; assert d['evidence_verdict']=='insufficient_data', d; assert d['case_type']=='other', d; assert d['severity']=='low', d; assert d['department']=='customer_support', d; assert d['human_review_required'] is True, d; assert isinstance(d['customer_reply'], str) and len(d['customer_reply']) > 0, d"
echo "OK — schema shape matches SRS §3.1"

echo -n "[3/4] POST /analyze-ticket (missing ticket_id) ... "
CODE=$(curl -s -o /tmp/qs_resp.json -w "%{http_code}" -X POST "${BASE}/analyze-ticket" \
  -H "Content-Type: application/json" \
  -d '{"complaint":"hello"}')
[[ "${CODE}" == "400" ]] || { echo "FAIL: expected 400, got ${CODE}"; cat /tmp/qs_resp.json; exit 1; }
echo "got 400 as expected"

echo -n "[4/4] POST /analyze-ticket (empty complaint) ... "
CODE=$(curl -s -o /tmp/qs_resp.json -w "%{http_code}" -X POST "${BASE}/analyze-ticket" \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"X","complaint":""}')
[[ "${CODE}" == "422" ]] || { echo "FAIL: expected 422, got ${CODE}"; cat /tmp/qs_resp.json; exit 1; }
echo "got 422 as expected"

echo ""
echo "=== ALL SMOKE CHECKS PASSED ==="