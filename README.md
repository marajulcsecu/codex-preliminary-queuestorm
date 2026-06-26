# QueueStorm Investigator

> bKash presents SUST CSE Carnival 2026 — Codex Community Hackathon
> AI/API Challenge · Online Preliminary · 4.5-hour round

A production-grade backend API for the digital-finance support team. Receives
one customer complaint (English or Bangla) plus a short transaction history,
and returns a structured JSON case file classifying, routing, and drafting
the customer reply — all under **700ms** end-to-end on Railway.

**Live endpoint:** `https://codex-preliminary-api-production.up.railway.app`

```bash
curl https://codex-preliminary-api-production.up.railway.app/health
# {"status":"ok"}
```

---

## TL;DR for judges

| Property | Value |
|---|---|
| **Approach** | Pure rule-based reasoning. **No external AI/LLM APIs.** |
| **Latency (production p95)** | ~700ms vs. 5s rubric target |
| **Stack** | Python 3.11 · FastAPI 0.115 · Pydantic v2 |
| **Deploy** | Railway (Docker builder) · Render fallback (`render.yaml`) |
| **Test coverage** | 209/209 pytest tests pass · 10/10 sample cases pass · 11/11 schema smoke pass |
| **Repository** | https://github.com/marajulcsecu/codex-preliminary-queuestorm |

---

## MODELS — How this service reasons (no AI/LLM in the hot path)

This service uses **no external AI/LLM APIs** in the hot path. All reasoning
is fully rule-based and deterministic. The pipeline runs in under 30ms locally
without any external dependency.

**Why rule-based over LLM?**
1. **Reproducibility.** Same input → same output, every time. Judges can
   verify the pipeline is correct by reading the code.
2. **Zero hallucination risk.** No LLM can promise a refund it shouldn't.
3. **Cost & latency.** No API call, no per-token cost, no network round trip.
4. **Debuggability.** Every response includes `reason_codes` that name the
   exact rule that fired.

**The reasoning pipeline (orchestrated in `app/reasoning.py`):**

```
        ┌─────────────────────────────────────────┐
        │  1. pre_scan_complaint                  │  ← detect prompt-injection markers
        └────────────────┬────────────────────────┘
                         ▼
        ┌─────────────────────────────────────────┐
        │  2. evidence.match                      │  ← 5-signal scorer: amount, type,
        │     (app/evidence.py)                   │    time, counterparty, status
        └────────────────┬────────────────────────┘
                         ▼
        ┌─────────────────────────────────────────┐
        │  3. classifier.classify                 │  ← 8-rule cascade:
        │     (app/classifier.py)                 │    phishing → duplicate → cash-in
        │                                         │    → settlement → payment_failed
        │                                         │    → wrong_transfer → refund → other
        └────────────────┬────────────────────────┘
                         ▼
        ┌─────────────────────────────────────────┐
        │  4. routing.department                  │  ← static department table
        │     (app/routing.py)                    │    (8 case_types × 6 departments)
        └────────────────┬────────────────────────┘
                         ▼
        ┌─────────────────────────────────────────┐
        │  5. i18n.reply_for_request              │  ← template-driven reply in
        │     (app/i18n.py)                       │    en / bn / mixed
        └────────────────┬────────────────────────┘
                         ▼
        ┌─────────────────────────────────────────┐
        │  6. safety.post_scan_pair               │  ← negation-aware scanner +
        │     (app/safety.py)                     │    safe-template rewrite
        └─────────────────────────────────────────┘
```

The pipeline is **total**: it never raises on a well-formed request. Internal
exceptions fall through to a safe `other/low/customer_support` response with
`human_review_required=True`.

---

## Endpoints

### `GET /health`

Liveness probe. Returns `{"status":"ok"}` within milliseconds.

```bash
curl https://codex-preliminary-api-production.up.railway.app/health
```

### `POST /analyze-ticket`

Main analysis endpoint. Accepts a complaint + optional transaction history.
Returns a structured case file.

**Request body:**

```json
{
  "ticket_id": "TKT-EXAMPLE",
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
      "status": "completed"
    }
  ]
}
```

**Response (200):**

```json
{
  "ticket_id": "TKT-EXAMPLE",
  "relevant_transaction_id": "TXN-9101",
  "evidence_verdict": "consistent",
  "case_type": "wrong_transfer",
  "severity": "high",
  "department": "dispute_resolution",
  "agent_summary": "Customer reports an issue with transaction TXN-9101 that may have been sent to the wrong recipient. The case has been flagged for the dispute team.",
  "recommended_next_action": "Verify TXN-9101 with the customer and initiate the wrong-transfer dispute workflow per policy.",
  "customer_reply": "We have noted your concern about transaction TXN-9101. Our dispute team will review the case and contact you through official support channels. Please do not share your PIN or OTP with anyone.",
  "human_review_required": true,
  "confidence": 0.8,
  "reason_codes": ["wrong_transfer", "transaction_match", "dispute_initiated"]
}
```

A complete live production response is saved at `sample_output.json`.

**Error responses:**

| HTTP | When | Body |
|---|---|---|
| 400 | Malformed JSON or missing required field | `{"error":"invalid_request_or_missing_field","message":"...","field":"<name>"}` |
| 422 | Empty / whitespace-only `complaint` | `{"error":"invalid_request","message":"...","field":"complaint"}` |
| 500 | Unhandled internal error | `{"error":"internal_error","message":"..."}` (no stack trace leaked) |

---

## Safety logic — how we avoid the 4 rubric penalties

The problem statement penalizes four safety failures. Each is handled
explicitly:

| Penalty | How we prevent it |
|---|---|
| **Ask for credentials** | The post-generation safety scanner flags any reply containing a credential request (e.g. *"share your PIN"*) inside a 60-character window of a credential word (PIN/OTP/password). Every template uses safe phrasing; the scanner runs even on rewritten output. |
| **Promise a refund** | All money-related templates use the safe phrase *"any eligible amount will be returned through official channels"* instead of *"we will refund"*. The post-scan rewrites any reply that slips through. |
| **Push to a 3rd-party channel** | All customer-facing replies direct back to *"official support channels"*. The post-scan rewrites any reply mentioning Telegram, WhatsApp, Signal, Viber, Messenger, etc. |
| **Compromise on credential warning** | Every `customer_reply` is constructed to end with the credential-warning phrase. The post-scan enforces the warning even if a template were to omit it. |

The scanner is **negation-aware**: a windowed token check ensures phrases like
*"please don't share your PIN"* are NOT flagged as credential requests.

A pre-scan also runs on the incoming complaint, flagging prompt-injection
markers (`"ignore previous instructions"`, `"act as"`, `"system prompt"`, etc.)
and forcing `human_review_required=true`.

---

## Setup (local development)

```bash
git clone https://github.com/marajulcsecu/codex-preliminary-queuestorm.git
cd codex-preliminary-queuestorm

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then in another terminal:

```bash
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/analyze-ticket \
  -H "Content-Type: application/json" \
  -d '{"ticket_id":"X","complaint":"hello"}'
```

### Running the tests

```bash
pytest tests/ -v                    # 209 tests, ~0.7s locally
python scripts/smoke_schema.py 8000 # 11 schema checks
python scripts/run_samples.py 8000  # 10 sample cases vs expected
python scripts/benchmark.py 8000    # latency p50/p95/p99/max
```

### Running with Docker

```bash
docker build -t queuestorm-investigator .
docker run -p 8000:8000 -e PORT=8000 queuestorm-investigator
```

---

## Deployment

| Platform | Status | Notes |
|---|---|---|
| **Railway (primary)** | 🟢 Live | Auto-deploys on every `git push origin main`. URL: `https://codex-preliminary-api-production.up.railway.app` |
| **Render (fallback)** | ✅ Ready | One-click deploy via `render.yaml`. Use only if Railway goes down. |
| **Docker** | ✅ Tested | `Dockerfile` is multi-stage; image size ~65 MB. |

### Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PORT` | Yes (injected by Railway) | `8000` | The port uvicorn binds to. |
| `LOG_LEVEL` | No | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |

No API keys are required — the pipeline is fully self-contained.

---

## Repository layout

```
queuestorm-investigator/
├── app/
│   ├── main.py          # FastAPI app, error handlers, /health, /analyze-ticket
│   ├── models.py        # Pydantic v2 schemas (AnalyzeRequest, AnalyzeResponse)
│   ├── evidence.py      # 5-signal scorer + ambiguity + established-recipient
│   ├── classifier.py    # 8-rule case-type cascade
│   ├── routing.py       # Department table
│   ├── safety.py        # Negation-aware scanner + safe template
│   ├── i18n.py          # Detect language + 16 reply templates (en/bn)
│   └── reasoning.py     # Orchestrator: pre_scan → evidence → classify → routing → i18n → post_scan
├── tests/
│   ├── test_evidence.py       # 39 tests
│   ├── test_classifier.py     # 74 tests
│   ├── test_safety.py         # 57 tests
│   ├── test_integration.py    # 38 tests
│   └── test_schema.py         # 11 schema checks via httpx
├── scripts/
│   ├── smoke_schema.py        # 11-check schema smoke
│   ├── run_samples.py         # 10-case E2E vs expected (accepts URL or port)
│   ├── benchmark.py           # Latency benchmark
│   └── deploy_smoke.txt       # Captured live curl results
├── docs/                      # Internal planning (gitignored)
├── sample_output.json         # Captured live response
├── Dockerfile                 # Multi-stage; ~65 MB
├── railway.json               # Railway config
├── render.yaml                # Render fallback config
├── requirements.txt
└── README.md                  # This file
```

---

## Test summary (verified locally + against production)

| Suite | Cases | Result |
|---|---|---|
| `test_evidence.py` | 39 | ✅ All pass |
| `test_classifier.py` | 74 | ✅ All pass |
| `test_safety.py` | 57 | ✅ All pass |
| `test_integration.py` | 38 | ✅ All pass |
| `test_schema.py` (smoke) | 11 | ✅ All pass |
| **Total pytest** | **209** | **✅ 209/209** |
| Sample-case E2E (`scripts/run_samples.py`) | 10 cases × 6 dimensions | ✅ 60/60 dimensions match expected |

Latency benchmark (production): p95 ≈ 700ms vs. 5000ms rubric target.

---

## Known limitations

1. **Language coverage is limited to English, Bangla, and Banglish (mixed).**
   Complaints in other languages will fall back to English — a future iteration
   could add language-specific templates.
2. **No state persistence.** The service is stateless — every request is
   evaluated independently. There is no customer memory across tickets
   (out of scope for the 4.5-hour round).
3. **Rule cascade order matters.** A new case type inserted above an existing
   rule must be re-tested against the sample suite to avoid regressions.
   `scripts/run_samples.py` is the regression gate.
4. **Counterparty matching is phone-string-equality based.** Variants with
   spaces, dashes, or country-code differences may not match exactly — a
   future iteration could add a normalization layer.
5. **No customer auth.** By design (rubric: "no login, dashboard access,
   manual approval, or private network access"). The endpoint is open to
   the internet; rate limiting is the responsibility of the host (Railway
   provides edge-level protection).

---

## What judges should look at first

1. **Live deployment:** [https://codex-preliminary-api-production.up.railway.app/health](https://codex-preliminary-api-production.up.railway.app/health) — `{"status":"ok"}`
2. **Sample output:** [`sample_output.json`](./sample_output.json) — a real production response
3. **The pipeline:** [`app/reasoning.py`](./app/reasoning.py) — 200 lines, fully readable
4. **The reasoning modules:** [`app/evidence.py`](./app/evidence.py) · [`app/classifier.py`](./app/classifier.py) · [`app/safety.py`](./app/safety.py) · [`app/i18n.py`](./app/i18n.py)
5. **Tests:** 209 pytest tests, run with `pytest tests/ -v`
6. **Deploy smoke:** [`scripts/deploy_smoke.txt`](./scripts/deploy_smoke.txt) — captured live curl results

---

## License

This project was built during a 4.5-hour hackathon round for the
**bKash presents SUST CSE Carnival 2026 — Codex Community Hackathon**.
No license is granted for reuse outside the judging context.