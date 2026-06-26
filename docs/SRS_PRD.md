# SRS / PRD — QueueStorm Investigator

**Event:** bKash presents SUST CSE Carnival 2026 · Codex Community Hackathon · Online Preliminary
**Round duration:** 4.5 hours (7:30 PM – 12:00 AM)
**Deliverable:** AI/API service exposing `POST /analyze-ticket` and `GET /health`
**Stack:** Python 3.11 · FastAPI · Pydantic v2 · rule-based reasoning engine
**Deployment:** Railway (primary) · Render / Dockerfile fallback

---

## 1. Product vision

QueueStorm Investigator is an **internal copilot** for digital-finance support agents. During a 4.5-hour online preliminary round it must read one customer complaint plus a short transaction history and produce a single, schema-perfect JSON response that classifies, routes, and explains the case.

It is **not** a complaint classifier. It is a **complaint investigator** — it must reconcile what the customer says against the data, not match keywords.

It is **not** autonomous. It must never request credentials, never confirm refunds, and must escalate risky or ambiguous cases to a human.

---

## 2. Scoring alignment (rubric weights)

| Category | Weight | How this design addresses it |
|---|---|---|
| Evidence Reasoning | **35** | Layered pipeline: amount+time+counterparty+type match → evidence verdict (consistent / inconsistent / insufficient_data) → taxonomy classification → department routing |
| Safety & Escalation | **20** | Deterministic safety layer with hard-coded bans; pre-generation and post-generation check on `customer_reply` and `recommended_next_action`; safe-language templates |
| API Contract & Schema | **15** | Pydantic v2 with `Literal` enums pinned exactly to spec; FastAPI auto-422 on malformed input |
| Performance & Reliability | **10** | Stateless, in-process, no external LLM dependency in the default hot path; sub-second p95 |
| Response Quality | **10** | Templated `agent_summary`, `recommended_next_action`, `customer_reply` with case-type-specific variants; Bangla/Banglish-aware |
| Deployment & Reproducibility | **5** | Dockerfile (~150 MB), `render.yaml`, `railway.json`, public endpoint |
| Documentation | **5** | README with setup, AI strategy, safety logic, MODELS section, known limitations |

**Tie-breakers targeted:** #1 Safety, #2 Evidence reasoning, #3 Schema, #4 Reliability, #6 Bangla handling.

---

## 3. Functional requirements

### 3.1 Endpoints

#### `GET /health`
- **Required:** yes
- **Response:** `200 OK`, `{"status":"ok"}`
- **Latency budget:** < 100 ms; must respond within 60 s of service start (rubric)
- **Auth:** none

#### `POST /analyze-ticket`
- **Required:** yes
- **Content-Type:** `application/json`
- **Per-request timeout:** ≤ 30 s (rubric enforced)
- **p95 target:** ≤ 5 s

**Request body** (per problem §5):

| Field | Type | Required | Notes |
|---|---|---|---|
| `ticket_id` | string | yes | Echoed in response |
| `complaint` | string | yes | May be en / bn / mixed Banglish |
| `language` | string | no | en / bn / mixed |
| `channel` | string | no | in_app_chat / call_center / email / merchant_portal / field_agent |
| `user_type` | string | no | customer / merchant / agent / unknown |
| `campaign_context` | string | no | Free-form |
| `transaction_history` | array | no | 0–5 entries; may be empty for safety-only cases |
| `metadata` | object | no | Optional context |

**Transaction history entry** (per §5.2):
- `transaction_id` (string)
- `timestamp` (ISO 8601)
- `type` ∈ {transfer, payment, cash_in, cash_out, settlement, refund}
- `amount` (number, BDT)
- `counterparty` (string)
- `status` ∈ {completed, failed, pending, reversed}

**Response body** (per §6, every required field must be present, no extras on the enum fields):

| Field | Type | Required | Allowed values |
|---|---|---|---|
| `ticket_id` | string | yes | echoes input |
| `relevant_transaction_id` | string \| null | yes | one of the supplied `transaction_id`s, or `null` |
| `evidence_verdict` | enum | yes | `consistent` / `inconsistent` / `insufficient_data` |
| `case_type` | enum | yes | `wrong_transfer` / `payment_failed` / `refund_request` / `duplicate_payment` / `merchant_settlement_delay` / `agent_cash_in_issue` / `phishing_or_social_engineering` / `other` |
| `severity` | enum | yes | `low` / `medium` / `high` / `critical` |
| `department` | enum | yes | `customer_support` / `dispute_resolution` / `payments_ops` / `merchant_operations` / `agent_operations` / `fraud_risk` |
| `agent_summary` | string | yes | 1–2 sentences |
| `recommended_next_action` | string | yes | operational next step |
| `customer_reply` | string | yes | safe, official-channel-only reply |
| `human_review_required` | bool | yes | true for disputes, suspicious cases, high-value, ambiguous |
| `confidence` | number | no | 0.0–1.0 |
| `reason_codes` | array | no | short string labels |

**HTTP codes:**
- `200` — successful analysis
- `400` — malformed JSON / missing required fields
- `422` — schema valid but semantically invalid (e.g. empty `complaint`)
- `500` — never exposed with stack trace; safe fallback body instead

---

### 3.2 Behavior

For each request the service runs a deterministic pipeline:

```
input JSON
   │
   ▼
Pydantic schema validation ───────────────► 400 if invalid
   │
   ▼
Safety pre-scan (complaint text) ─────────► flags sensitive_request / unauthorized_action / suspicious_3p
   │
   ▼
Evidence matching ────────────────────────► relevant_transaction_id (or null) + evidence_verdict
   │
   ▼
Case-type classifier (rule cascade) ──────► case_type + severity + human_review_required
   │
   ▼
Department routing (table from §7.2) ─────► department
   │
   ▼
Reply & summary templating ───────────────► agent_summary + recommended_next_action + customer_reply
   │
   ▼
Safety post-scan (output fields) ─────────► reject & rewrite on unsafe phrases
   │
   ▼
JSON response (Pydantic re-validated) ────► 200
```

---

### 3.3 Evidence matching (the 35-point core)

**Five signals** (the "5-signal scorer") are extracted and scored; the transaction with the highest composite score is selected. If no transaction scores above threshold, returns `null` and `insufficient_data`.

| Signal | Weight | Description |
|---|---|---|
| Amount match | 30 | Exact amount mentioned in complaint vs `transaction.amount` |
| Type match | 20 | transfer / payment / cash_in / cash_out / settlement / refund |
| Time match | 20 | Complaint time phrase vs `transaction.timestamp` (±24h) |
| Counterparty match | 15 | Phone / merchant ID / agent ID mentioned in complaint |
| Status plausibility | 15 | completed for transfers, pending for cash-in complaints, failed for payment-failed complaints |

**Ambiguity rule (SAMPLE-08):** if ≥2 transactions have equal top score, return `null` + `insufficient_data` + ask for disambiguation.

**Established-recipient rule (SAMPLE-02):** if complaint claims "wrong transfer" but the same counterparty appears ≥2 times in history with `transfer` type, return `evidence_verdict = inconsistent` for the most recent matching transaction.

---

### 3.4 Case-type taxonomy (rule cascade, order matters)

1. **Phishing / social engineering** — keywords in English + Bangla: `otp, pin, password, ওটিপি, পিন, পাসওয়ার্ড, blocked, share, call asking` → `critical`, `fraud_risk`
2. **Duplicate payment** — ≥2 `payment` of identical amount to identical counterparty within 60 s → `high`, `payments_ops`
3. **Agent cash-in issue** — Bangla/English complaint about cash-in not reflected + history has `cash_in` with `status=pending` → `high`, `agent_operations`
4. **Merchant settlement delay** — `user_type=merchant` + complaint about settlement + history has `settlement` with `status=pending` → `medium`, `merchant_operations`
5. **Payment failed** — complaint says failed + history has matching `payment` with `status=failed` → `high`, `payments_ops`
6. **Wrong transfer** — complaint says wrong/mistaken transfer + matching `transfer` exists → `high` (or `medium` if established-recipient rule fires), `dispute_resolution`
7. **Refund request** — explicit refund language (`refund, ফেরত, টাকা ফেরত`) → `low` (customer) / `medium` (contested), `customer_support` or `dispute_resolution`
8. **Other** — fallback

`human_review_required = true` for: phishing, wrong_transfer, agent_cash_in_issue, duplicate_payment, contested refund, established-recipient inconsistent.

---

### 3.5 Safety rules (the 20-point core — hard-coded)

A two-layer guard enforces the four penalties from §8 of the problem statement:

**Layer 1 — pre-generation scan on the input complaint.** Detects "ignore previous instructions", "act as admin", "refund me now", and obvious injection markers. If found, the rule pipeline runs but the customer_reply is forced into the safety template.

**Layer 2 — post-generation scan on `customer_reply` and `recommended_next_action`.** Forbidden phrases:

**Critical distinction — requesting vs. warning.** The scanner must not flag phrases that **warn the customer** about not sharing credentials ("Please do not share your PIN or OTP", "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না", "We never ask for your PIN"). Every sample case's expected customer_reply contains exactly such a warning. Flagging them would force a generic template that loses Response Quality points.

Detection uses **context windows**: the credential word must be within ~6 tokens of a **request verb** (`share`, `send`, `tell`, `give`, `provide`, `type`, `enter`, `দিন`, `দিয়ে`, `বলুন`) **and not within ~8 tokens of a negation/warning marker** (`do not`, `never`, `don't`, `please don't`, `অনুগ্রহ করে`, `করবেন না`, `কখনো না`).

| Forbidden pattern | Reason | Penalty |
|---|---|---|
| Request verb + credential word, with no negation nearby: `share your pin`, `send your otp`, `tell me your password`, `আপনার পিন দিন`, `আপনার ওটিপি বলুন` | Sensitive credentials | −15 |
| `we will refund`, `we have refunded`, `account [is now/has been] unblocked`, `account reactivated`, `your refund has been approved`, `confirmed refund`, `we have credited`, `your money has been refunded` | Unauthorized action | −10 |
| `telegram`, `whatsapp`, `wa.me/`, `t.me/`, third-party personal phone numbers in suspicious contexts | Suspicious 3rd party | −10 |

The scanner rewrites any violation to the safe template. It runs in <1 ms. The defensive warning phrases ("do not share your PIN") are **always preserved** — they are required by the rubric's Response Quality standard.

---

### 3.6 Multilingual strategy

- `language` is taken from input if present, otherwise detected by Unicode range (Bengali block `\u0980–\u09FF` → `bn`, Latin → `en`, mixed → `mixed`).
- Customer reply is rendered in the same language.
- Bangla template fragments:
  - "আমরা আপনার লেনদেন TXN-XXXX বিষয়ে অবগত হয়েছি।"
  - "আমাদের দল এটি যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে।"
  - "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
- Banglish is treated as `mixed` and replied in Banglish-friendly English (short, simple).

---

## 4. Non-functional requirements

| Metric | Target | Why |
|---|---|---|
| Image size | ≤ 200 MB | Rubric <500 MB recommended |
| Cold start → `/health` ready | ≤ 5 s | Rubric 60 s limit |
| POST p95 latency | ≤ 2 s | Rubric full credit ≤5 s |
| POST worst-case | ≤ 10 s | Rubric partial ≤15 s |
| Memory | ≤ 256 MB | Fits Render/Railway free tier |
| CPU | 1 vCPU | Rule-based only |
| GPU | None | Not allowed |
| External API calls in hot path | None by default | Reliability |

---

## 5. Tech stack

| Layer | Choice | Reason |
|---|---|---|
| Language | Python 3.11 | Fastest to scaffold, Pydantic maturity |
| Web framework | FastAPI | Auto OpenAPI, async-friendly, easy schema |
| Validation | Pydantic v2 | Strict enum enforcement |
| Server | Uvicorn (standard) | Async, fast, well-supported on Railway/Render |
| Tests | `pytest` + `httpx.AsyncClient` | Local smoke against 10 sample cases |
| AI | None by default; optional OpenAI / Anthropic only if a hidden case requires it | Reliability > flexibility |
| Deployment | Railway (primary, no spin-down) · Render (fallback, free tier) · Docker | Matches rubric priority |
| Repo | GitHub public (or private + organizer handle `bipulhf`) | Required deliverable |

No database. No state. No caching layer. The service is fully stateless.

---

## 6. Project layout

```
queuestorm-investigator/
├── app/
│   ├── __init__.py
│   ├── main.py                # FastAPI app, route definitions
│   ├── models.py              # Pydantic request/response models
│   ├── safety.py              # Pre/post safety scanner
│   ├── evidence.py            # Amount/time/counterparty matching
│   ├── classifier.py          # Case-type rule cascade
│   ├── routing.py             # Department routing table
│   ├── reasoning.py           # Pipeline orchestrator
│   └── i18n.py                # Bangla/Banglish templates
├── tests/
│   ├── test_samples.py        # Validates all 10 sample cases
│   └── test_safety.py         # Validates safety guardrails
├── docs/
│   ├── SRS_PRD.md             # this file
│   └── ARCHITECTURE.md        # architecture + sequence diagrams
├── requirements.txt
├── Dockerfile
├── render.yaml
├── railway.json
├── .env.example
├── .gitignore
├── README.md
├── RUNBOOK.md                 # step-by-step run instructions
└── sample_output.json         # one real response to TKT-001
```

---

## 7. Acceptance criteria

A submission is judged acceptable when:

1. ✅ `GET /health` returns `{"status":"ok"}` within 60 s of start, every time
2. ✅ `POST /analyze-ticket` returns the exact response schema on every one of the 10 sample cases
3. ✅ Output matches the sample `expected_output` on: `relevant_transaction_id`, `evidence_verdict`, `case_type`, `department`, comparable `severity`, and a `customer_reply` that respects §8
4. ✅ No `customer_reply` contains any of the four forbidden phrase categories from §3.5
5. ✅ p95 latency ≤ 2 s on sample cases
6. ✅ Service does not crash on malformed JSON, empty complaint, empty transaction history, or Bangla input
7. ✅ Bangla input produces Bangla customer_reply
8. ✅ Public endpoint URL is reachable and stable during evaluation
9. ✅ README has all required sections (setup, run, sample req/resp, AI usage, safety, MODELS, limitations)
10. ✅ `requirements.txt`, `Dockerfile`, `.env.example`, `RUNBOOK.md`, `sample_output.json` all present

---

## 8. Out of scope

- Frontend / UI (rubric §2: not judged)
- Authentication / login (rubric §6: judge calls directly)
- Persistence / database
- Real payment-system integration
- Real LLM calls in the hot path (optional, off by default)
- i18n beyond English / Bangla / Banglish

---

## 9. Submission form contract

The Google Form requires the following fields. Pre-filled values are produced in Phase 10 of the roadmap (`docs/SUBMISSION_ANSWERS.md`).

| Field | Pre-filled value |
|---|---|
| Team Leader's Phone * | provided by team |
| GitHub Repository URL * | `https://github.com/marajulcsecu/codex-preliminary-queuestorm` |
| Submission Path * | **Working public endpoint URL** |
| Public Endpoint Base URL * | `<RAILWAY_URL>` (generated in Phase 7) |
| Deployment Platform * | **Railway** |
| LLM or AI provider used * | **None (rules-based solution only)** |
| If Other or Hybrid, specify | (blank) |
| Known issues or blockers | notes on pre-warmed scaffold + any honest limits |
| Architecture Video link | optional but recommended (≤90s) |
| Attestation 1 — no real customer data | ✅ tickable |
| Attestation 2 — no real secrets committed | ✅ tickable |
| Attestation 3 — all code written during round | ⚠️ honest declaration required (see roadmap Step 10.3) |
| Attestation 4 — three companion docs read | ✅ tickable |
