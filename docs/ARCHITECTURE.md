# Architecture — QueueStorm Investigator

> **Round context:** 4.5 hours · Online Preliminary · QueueStorm Investigator · bKash presents SUST CSE Carnival 2026 · Codex Community Hackathon
>
> **Companion:** see `SRS_PRD.md` for product requirements, scoring alignment, and acceptance criteria.

## High-level

```
                              ┌────────────────────────────────────┐
                              │      Judge harness (HTTP)         │
                              └────────────────┬───────────────────┘
                                               │
                                  GET /health  │  POST /analyze-ticket
                                               ▼
                              ┌────────────────────────────────────┐
                              │      FastAPI app (Uvicorn)         │
                              │  ┌──────────────────────────────┐  │
                              │  │   Pydantic schema guard      │  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Safety pre-scan            │  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Evidence matcher           │  │
                              │  │  (amount+time+type+cp+status)│  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Case-type rule cascade     │  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Department router          │  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Reply templater (i18n)     │  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Safety post-scan & rewrite │  │
                              │  └──────────────┬───────────────┘  │
                              │                 ▼                  │
                              │  ┌──────────────────────────────┐  │
                              │  │   Pydantic output guard      │  │
                              │  └──────────────┬───────────────┘  │
                              └─────────────────┼──────────────────┘
                                                ▼
                                  200 OK · structured JSON
```

## Module map

| File | Responsibility | Rubric points served |
|---|---|---|
| `app/main.py` | Route wiring, error handlers, health | All |
| `app/models.py` | Pydantic v2 request/response with `Literal` enums | API Contract (15) |
| `app/safety.py` | Forbidden-phrase scan + safe-rewrite templates | Safety (20) |
| `app/evidence.py` | 5-signal scoring + ambiguity + established-recipient rule | Evidence Reasoning (35) |
| `app/classifier.py` | Rule cascade → case_type + severity + human_review | Evidence Reasoning (35) |
| `app/routing.py` | Static enum table from problem §7.2 | Evidence Reasoning (35) |
| `app/reasoning.py` | Orchestrator that wires modules together | All |
| `app/i18n.py` | English / Bangla / Banglish reply templates | Response Quality (10) + Tie-breaker #6 |

## Data flow (POST /analyze-ticket)

```
1. FastAPI parses JSON into AnalyzeRequest (Pydantic). Bad JSON → 400.
2. safety.pre_scan(complaint) → flags list (mutates nothing).
3. evidence.match(request) → EvidenceMatch(relevant_tx_id, verdict, scores).
4. classifier.classify(request, evidence) → (case_type, severity, reason_codes).
5. routing.department(case_type, severity, user_type) → enum value.
6. i18n.draft_reply(case_type, severity, language, ticket_id, relevant_tx_id)
     → (agent_summary, recommended_next_action, customer_reply).
7. safety.post_scan(customer_reply, recommended_next_action)
     → rewrite on violation; never raises.
8. Pydantic validates output. If invalid (shouldn't happen), safe fallback.
9. Return 200 with the validated response.
```

## Evidence matching algorithm (5-signal scorer)

The scorer uses 5 weighted signals (amount, type, time, counterparty, status plausibility) to pick the most relevant transaction. If the top score is below threshold, or if two transactions tie at the top, the service returns `null` + `insufficient_data` per the ambiguity rule (SAMPLE-08). The established-recipient rule (SAMPLE-02) overrides the verdict to `inconsistent` when the same counterparty appears in ≥2 prior transfers.

```python
def score(tx, complaint_signals):
    score = 0
    if tx.amount == complaint_signals.amount:        score += 30
    if tx.type == complaint_signals.type:            score += 20
    if abs(tx.timestamp - complaint_signals.time) < 24h: score += 20
    if complaint_signals.counterparty in tx.counterparty: score += 15
    if status_is_plausible(tx, complaint_signals):   score += 15
    return score

# 1. Extract signals from complaint text (regex + keyword).
# 2. Score every transaction in history.
# 3. Pick the highest.
# 4. If top score < 30 → insufficient_data, null id.
# 5. If tie at top → insufficient_data, null id, ask for disambiguation.
# 6. Established-recipient rule: if claim is wrong_transfer and same
#    counterparty appears ≥2 prior transfers → verdict = inconsistent.
```

## Rule cascade (classifier)

```
if phishing_signals(complaint):   # OTP/PIN/PASSWORD in ask context
    return phishing_or_social_engineering, critical, fraud_risk, True

if duplicate_payment(history):   # ≥2 same-amount same-cp payments within 60s
    return duplicate_payment, high, payments_ops, True

if agent_cash_in_signals(complaint) and pending_cash_in(history):
    return agent_cash_in_issue, high, agent_operations, True

if user_type == merchant and settlement_pending(history):
    return merchant_settlement_delay, medium, merchant_operations, False

if payment_failed_signals(complaint) and failed_payment(history):
    return payment_failed, high, payments_ops, False

if wrong_transfer_signals(complaint) and transfer(history):
    if established_recipient(history, counterparty):
        return wrong_transfer, medium, dispute_resolution, True  # inconsistent
    return wrong_transfer, high, dispute_resolution, True

if refund_signals(complaint):
    return refund_request, low, customer_support, False

return other, low, customer_support, False
```

## Safety scanner (post-generation)

**Distinguish requesting vs. warning.** Every sample-case expected reply contains a *defensive warning* like "Please do not share your PIN or OTP" — that is **safe and required** for Response Quality. The scanner must allow warnings and block only requests.

The algorithm uses **windowed token analysis** (not a single regex), which is far more reliable across languages:

```python
import re

NEGATION_PATTERNS = [
    r"\bdo\s+not\b", r"\bdon['’]t\b", r"\bnever\b",
    r"\bplease\s+do\s+not\b", r"\bplease\s+don['’]t\b",
    r"\bdo\s+not\s+share\b", r"\bnever\s+ask\b",
    r"অনুগ্রহ\s+করে", r"করবেন\s+না", r"কখনো\s+না",
    r"ভাগ\s+করবেন\s+না", r"শেয়ার\s+করবেন\s+না",
]
REQUEST_VERBS = [
    r"\bshare\b", r"\bsend\b", r"\btell\b", r"\bgive\b",
    r"\bprovide\b", r"\btype\b", r"\benter\b", r"\bsubmit\b",
    r"দিন", r"দিয়ে", r"বলুন", r"জানান",
]
CREDENTIAL_WORDS = [
    r"\bpin\b", r"\botp\b", r"\bpassword\b", r"\bcvv\b",
    r"\bcard\s*number\b",
    r"পিন", r"ওটিপি", r"পাসওয়ার্ড", r"সিভিভি",
]

# Simple char-window check (60 chars each side).
def _window_has(reply_lower, pattern_list, idx, radius):
    start = max(0, idx - radius)
    end = min(len(reply_lower), idx + radius)
    window = reply_lower[start:end]
    return any(re.search(p, window) for p in pattern_list)

def _find_match(reply_lower, pattern_list):
    """Return list of (pattern, index) for every regex hit."""
    hits = []
    for p in pattern_list:
        for m in re.finditer(p, reply_lower):
            hits.append((p, m.start()))
    return hits

def is_credential_request(reply: str) -> bool:
    """True iff the reply asks the customer for credentials (not warns)."""
    rl = reply.lower()
    req_hits = _find_match(rl, REQUEST_VERBS)
    cred_hits = _find_match(rl, CREDENTIAL_WORDS)
    for _, req_idx in req_hits:
        for _, cred_idx in cred_hits:
            # request verb and credential word both present within 60 chars
            if abs(req_idx - cred_idx) <= 60:
                # AND no negation marker within 80 chars of either side
                if not _window_has(rl, NEGATION_PATTERNS, cred_idx, 80):
                    return True
    return False

FORBIDDEN_PROMISES = re.compile(
    # "we will refund / reverse / unblock / approve / release"
    r"\bwe\s+(?:will|have|shall|are\s+going\s+to)\s+(?:refund|reverse|unblock|approve|release|process\s+the\s+refund)\b"
    # "refund / reversal is approved / processed / completed"
    r"|\b(?:refund|reversal)\s+(?:is|has\s+been|will\s+be)\s+(?:approved|processed|completed|sent)\b"
    # "account [up to 3 words] unblocked / reactivated / recovered" — handles
    # "Account is now unblocked", "Your account has been unblocked", etc.
    r"|\baccount\s+(?:\w+\s+){0,3}unblocked\b"
    r"|\baccount\s+(?:\w+\s+){0,3}reactivated\b"
    r"|\baccount\s+(?:\w+\s+){0,3}recovered\b"
    # money already refunded / credited
    r"|\byour\s+money\s+has\s+been\s+refunded\b"
    r"|\bwe\s+have\s+credited\b",
    re.IGNORECASE,
)

FORBIDDEN_THIRD_PARTIES = re.compile(
    r"\btelegram\b|\bwhatsapp\b|\bwa\.me\b|\bt\.me\b|\bsignal\.org\b",
    re.IGNORECASE,
)

# Personal phone numbers in suspicious contexts: "on whatsapp +880..." or "telegram +1..."
SUSPICIOUS_PHONE_CONTEXT = re.compile(
    r"(?:telegram|whatsapp|t\.me|wa\.me|signal|viber|imessage).{0,40}[\+\d]",
    re.IGNORECASE,
)

def post_scan(reply: str) -> str:
    """Return the safe template if any forbidden pattern matches, else reply unchanged."""
    if (is_credential_request(reply)
        or FORBIDDEN_PROMISES.search(reply)
        or FORBIDDEN_THIRD_PARTIES.search(reply)
        or SUSPICIOUS_PHONE_CONTEXT.search(reply)):
        return SAFE_REPLY_TEMPLATE
    return reply
```

**Why this matters:** a naive scanner that bans the substring `"your PIN"` would rewrite every gold-standard reply to a robotic template and cost Response Quality points. With windowed analysis, defensive warnings pass through, genuine credential requests are blocked, refund promises are blocked, and suspicious third-party channels are blocked — matching the rubric intent precisely.

The windowed approach also handles Bangla correctly: `অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না` carries negation tokens within the window, so the warning passes; `আপনার পিন দিয়ে যাচাই করুন` has no negation, so it is rewritten.

## Deployment topology

The service is **stateless** — no database, no session store, no cache layer. Each `/analyze-ticket` request is fully self-contained and reproducible.

```
GitHub repo (public)
       │  push to main
       ▼
Railway (primary)
   Nixpacks auto-detect Python
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   Healthcheck: GET /health (60s timeout)
       │
       ▼
Public URL: https://<team>.up.railway.app

Render (fallback if Railway fails)
   Web Service → Docker → public URL
```

## HTTP response codes

| Code | When |
|---|---|
| 200 | Successful analysis, body conforms to schema |
| 400 | Malformed JSON / missing required fields (caught by Pydantic) |
| 422 | Schema valid but semantically invalid (e.g. empty `complaint`) |
| 500 | Internal error — body is a safe fallback, never a stack trace |

## Why this design wins points

1. **Evidence Reasoning (35):** the 5-signal scorer handles SAMPLE-01 (clean match), SAMPLE-02 (established-recipient inconsistent), SAMPLE-08 (ambiguity → insufficient_data). Tunable weights mean we can adjust without code rewrites.
2. **Safety (20):** two-layer scanner is the simplest possible defense against the four penalties and runs in microseconds.
3. **Schema (15):** `Literal` enums in Pydantic mean a wrong enum value is impossible at runtime — the response model rejects it before serialization.
4. **Reliability (10):** zero external calls in hot path means no timeouts, no quota errors, no flakiness.
5. **Response Quality (10):** i18n templates are hand-tuned for the rubric's "useful for an agent, safe for a customer" standard.
6. **Deployment (5):** one repo, two deploy targets, one Dockerfile, one run command.
7. **Documentation (5):** every required section in README + MODELS.