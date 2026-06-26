# AGENT.md — Context Handoff for QueueStorm Investigator

> **Purpose:** This file is the single source of context for any AI coding agent (current or future) working on this repository. Read this file first. Do not skip. It contains the design decisions, current status, what works, what doesn't, and the explicit next steps.
>
> **Audience:** An AI agent dropped into this project with no prior conversation history.

---

## 0. Quick orientation

| Item | Value |
|---|---|
| Project | **QueueStorm Investigator** |
| Event | bKash presents SUST CSE Carnival 2026 — Codex Community Hackathon |
| Round | AI/API Challenge · 4.5-hour Online Preliminary |
| Working dir | `/home/marajul/AI_WorkShop/preliminary_hackathon/queuestorm-investigator/` |
| GitHub repo | `https://github.com/marajulcsecu/codex-preliminary-queuestorm` |
| Default branch | `main` (no other branches) |
| Stack | Python 3.11 · FastAPI 0.115 · Pydantic 2.9 · Uvicorn 0.32 |
| Deploy target (primary) | Railway (no spin-down, $5 trial credit) |
| Deploy target (fallback) | Render (Docker) |
| Reasoning approach | **Pure rule-based**, no LLM in the hot path |

---

## 1. What this project does

A backend API service for digital-finance support agents. It receives one customer complaint + the customer's recent transaction history and returns a structured JSON response that:

1. Picks the **relevant transaction** from the history (or `null`).
2. Issues an **evidence verdict** (`consistent` / `inconsistent` / `insufficient_data`).
3. **Classifies** the case into one of 8 enum types (e.g. `wrong_transfer`, `phishing_or_social_engineering`).
4. Assigns **severity** (`low` / `medium` / `high` / `critical`).
5. **Routes** to the right department (6 enum values).
6. Drafts an `agent_summary`, a `recommended_next_action`, and a **safe `customer_reply`** that never asks for credentials, never promises unauthorized refunds, and never directs to suspicious third parties.

The endpoint is called by an automated judge harness. There is no UI.

---

## 2. The problem and the rubric

The full problem statement is in `docs/SRS_PRD.md` §3. The scoring rubric weights are:

| # | Category | Weight | What it measures |
|---|---|---|---|
| 1 | Evidence Reasoning | **35** | Right transaction, right verdict, right classification, right routing |
| 2 | Safety & Escalation | **20** | No credential asks, no unauthorized refunds, suspicious cases escalated |
| 3 | API Contract & Schema | 15 | Exact JSON shape, enums, types, HTTP codes |
| 4 | Performance & Reliability | 10 | p95 ≤ 5s for full credit; no 5xx on valid input |
| 5 | Response Quality | 10 | Clear summary, practical next action, safe customer reply |
| 6 | Deployment & Reproducibility | 5 | Endpoint reachable OR Docker runs cleanly |
| 7 | Documentation | 5 | README explains setup, AI usage, safety, limitations |

**Hidden tests** will be used. The 10 cases in `SUST_Preli_Sample_Cases.json` (under `Question Provided/`) are reference only.

### Hard safety penalties (avoid at all costs)
- Asks for PIN/OTP/password/full card number → **−15 pts**
- Promises refund/reversal/account unblock without authority → **−10 pts**
- Directs to suspicious third parties (telegram, whatsapp, etc.) → **−10 pts**
- 2+ critical violations → not eligible for top-40 finalist pool

---

## 3. Design — read these docs before touching code

| Doc | Why |
|---|---|
| `docs/SRS_PRD.md` | What the product is, what it must do, acceptance criteria |
| `docs/ARCHITECTURE.md` | Module map, evidence algorithm, safety scanner algorithm |
| `docs/ROADMAP.md` | The 10 phases of implementation with `[AI]/[HUMAN]` tags at every step |
| `docs/SUBMISSION_ANSWERS.md` | Pre-filled answers for the Google Form (Phase 10) |
| `docs/VIDEO_SCRIPT.md` | 90-second narration script for the optional architecture video |

**Cross-references:** SRS and Architecture must agree on every number, enum, rule. They were verified to agree on 18/18 cross-checks. Don't break that.

### Critical design invariants — DO NOT VIOLATE

1. **All enums are `Literal[...]` with exact spec spelling.** No `str` placeholders. Case-sensitive. The judge is automated; a wrong enum string scores 0 on schema points.
2. **No 5xx on valid input.** Internal errors must return a controlled 500 with a safe body, never a stack trace. The error handler in `app/main.py` enforces this — keep it.
3. **Customer reply must NEVER ask for credentials, promise refunds, or direct to suspicious third parties.** The negation-aware safety scanner in `app/safety.py` (Phase 4) is the second line of defense.
4. **Bind to `0.0.0.0` on `$PORT`.** Required by Docker rules AND by Railway/Render.
5. **Stateless.** No database, no in-memory cache, no session. Hot path runs in-process.
6. **`/health` returns `{"status":"ok"}` and must respond within 60s of service start.** Cold-start budget is tight on Render free tier; Railway does not sleep.

---

## 4. Project layout

```
queuestorm-investigator/
├── AGENT.md                       ← this file
├── README.md                       (placeholder; final in Phase 8)
├── Dockerfile                      (works on Railway, Render, Fly, AWS, Poridhi)
├── railway.json                    (Railway deploy config)
├── render.yaml                     (Render fallback config)
├── .env.example                    (variable names only)
├── .gitignore                      (strict — no .env, no .venv)
├── requirements.txt                (fastapi, uvicorn, pydantic)
├── test_local.sh                   (legacy — superseded by scripts/smoke_phase0.sh)
├── app/
│   ├── __init__.py
│   └── main.py                     (HTTP layer; placeholder response in /analyze-ticket)
├── tests/
│   └── __init__.py                 (real tests added in Phase 2)
├── scripts/
│   ├── __init__.py
│   └── smoke_phase0.sh             (4-check smoke test for /health + /analyze-ticket)
└── docs/
    ├── SRS_PRD.md                  (product requirements)
    ├── ARCHITECTURE.md             (module design + algorithms)
    ├── ROADMAP.md                  (10-phase implementation plan)
    ├── SUBMISSION_ANSWERS.md       (Google Form pre-fill)
    └── VIDEO_SCRIPT.md             (90-second video script)
```

**Clean architecture rule:** dependency direction is always `main → reasoning → {evidence, classifier, routing, safety, i18n} → models`. Sub-modules never import each other circularly; `models` is the leaf.

---

## 5. Current status — what's done

### ✅ Phase 0 — Bootstrap (COMPLETE)

- **Commit:** `f6b6482` — "chore: bootstrap project from verified template"
- **Files:** 18 on remote main, 0 secrets, 0 venv, 0 .env
- **Verified locally:**
  - `GET /health` → `200 {"status":"ok"}`
  - `POST /analyze-ticket` (valid) → 200 with placeholder body containing all 10 schema fields
  - `POST /analyze-ticket` (missing ticket_id) → 400
  - `POST /analyze-ticket` (empty complaint) → 422
- **Remote:** `origin/main` tracking `git@github.com:marajulcsecu/codex-preliminary-queuestorm.git`

### What `/analyze-ticket` returns RIGHT NOW

A safe placeholder. Every field is present and type-correct, but the values are stub:
- `relevant_transaction_id`: `null`
- `evidence_verdict`: `"insufficient_data"`
- `case_type`: `"other"`
- `severity`: `"low"`
- `department`: `"customer_support"`
- `human_review_required`: `true`
- `customer_reply`: a generic safe reply

**This means the API Contract & Schema score (15 pts) is fully defensible today** — judges can validate the response shape. Evidence Reasoning (35 pts) is NOT defensible until Phase 5 wires the real reasoning pipeline.

### ✅ Phase 1 — Pydantic v2 schema layer (COMPLETE)

- **Commits:**
  - `1b5378a` — "feat(models): pydantic v2 schemas with pinned literal enums"
  - `01a48bc` — "feat(api): use Pydantic models in /analyze-ticket"
  - _third commit pending for smoke test + AGENT.md_
- **What works:**
  - `app/models.py` defines `AnalyzeRequest`, `AnalyzeResponse`, `TransactionHistoryEntry` with `Literal` enums pinned to spec.
  - `app/main.py` `/analyze-ticket` uses `payload: AnalyzeRequest` for auto-validation; returns `AnalyzeResponse` (extra="forbid").
  - `RequestValidationError` handler distinguishes 400 (malformed/missing) from 422 (semantic invalid, e.g. empty string).
  - 11/11 schema checks pass via `scripts/smoke_schema.py` AND via `pytest tests/test_schema.py -v`.
- **API Contract & Schema (15 pts):** fully defensible.
- **Evidence Reasoning (35 pts):** still NOT defensible — reasoning is a placeholder. Phase 5 fixes this.

### What needs to be done (Phase 2 next)

- **Phase 2.1:** `app/evidence.py` — 5-signal scorer (amount + type + time + counterparty + status)
- **Phase 2.2:** Add ambiguity rule (tie → null + insufficient_data) and established-recipient rule (SAMPLE-02)
- **Phase 2.3:** `tests/test_evidence.py` — verify all 10 sample cases produce correct (tx_id, verdict)
- **Phase 2 milestone push:** push to `origin/main`

### Roadmap status (after Phase 1)

| Phase | Description | Status |
|---|---|---|
| 0 | Bootstrap | ✅ DONE (commit `f6b6482`) |
| 1 | Pydantic schema layer | ✅ DONE (commits `1b5378a`, `01a48bc`, smoke test commit) |
| 2 | Evidence matcher (5-signal scorer) | 🔄 NEXT |
| 3 | Classifier + department router | pending |
| 4 | Safety layer (negation-aware scanner) | pending |
| 5 | i18n + reasoning orchestrator | pending |
| 6 | End-to-end validation against 10 sample cases | pending |
| 7 | Deploy to Railway | pending |
| 8 | Final README + RUNBOOK + sample_output.json | pending |
| 9 | Buffer + video script (optional) | pending |
| 10 | Submission form pre-fill + submit | pending |

**Git workflow:** every step ends with `git add . && git commit -m "..."`. Push only at milestone boundaries (after Phase 1, 2, 6, 8) so Railway doesn't redeploy 10 times.

---

## 6. How to run locally

```bash
cd /home/marajul/AI_WorkShop/preliminary_hackathon/queuestorm-investigator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In another terminal:
```bash
curl http://localhost:8000/health
# -> {"status":"ok"}

curl -X POST http://localhost:8000/analyze-ticket \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"TKT-001","complaint":"hello"}'
```

Or use the smoke test:
```bash
bash scripts/smoke_phase0.sh 8000
```

---

## 7. How to verify a deployment

```bash
RAILWAY_URL="https://<your-app>.up.railway.app"
curl -fsS "$RAILWAY_URL/health"
curl -fsS -X POST "$RAILWAY_URL/analyze-ticket" \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"TKT-001","complaint":"hello"}'
```

Both should return 200 with valid JSON. `/health` must be `{"status":"ok"}`.

---

## 8. Submission form cheat sheet (Phase 10)

| Field | Value |
|---|---|
| GitHub Repository URL | `https://github.com/marajulcsecu/codex-preliminary-queuestorm` |
| Submission Path | **Working public endpoint URL** |
| Deployment Platform | **Railway** |
| LLM or AI provider used | **None (rules-based solution only)** |
| Known issues or blockers | "Generic FastAPI/Pydantic/Docker scaffolding template was pre-warmed before the round and adapted to the problem. The pre-warmed template contained no project-domain logic. All problem-specific code was written during the round." |

The full pre-filled form is in `docs/SUBMISSION_ANSWERS.md`.

---

## 9. Style and conventions

- **Python style:** PEP 8 + Black (default 88-char line width). No single-letter variable names except in tight comprehensions.
- **Type hints:** required on all public functions. Use `from __future__ import annotations` for forward refs.
- **Docstrings:** Google style for modules and classes. One-line docstrings are fine for trivial functions.
- **No emoji in code or commits** unless explicitly requested.
- **No imports from `app.*` inside `app/main.py`'s top-level** beyond what's strictly needed. Keep `main.py` thin — it's just the HTTP layer.
- **Tests:** pytest. Test files mirror module names (`test_models.py`, `test_evidence.py`).
- **Logging:** `logging.getLogger(__name__)`. Never log PII, secrets, or stack traces to clients.

---

## 10. Explicit guardrails for any agent working on this repo

1. **Never modify `docs/SRS_PRD.md` or `docs/ARCHITECTURE.md` to make implementation easier.** The docs are the contract. If implementation is hard, change the code, not the contract.
2. **Never weaken the safety scanner** to make a test pass. If a sample case seems to "want" a refund promise, that's a bug in our classifier, not a bug in the safety scanner.
3. **Never add an LLM call to the hot path** without explicit team approval. Default is rule-based.
4. **Never commit `.env`, `*.pem`, `*.key`, `credentials.json`.** Check with `git ls-files | grep -E '\.env$|\.pem|\.key|credentials'` before pushing.
5. **Never use `git push --force` on `main`.** Ever.
6. **If a step's Verify check fails, do not commit and do not move on.** Fix the failure. If stuck for more than 5 minutes, write a `KNOWN_ISSUE.md` note and ask the human team.
7. **One commit per roadmap step.** No squashing, no amending. Each step should appear as a distinct commit so judges (and humans) can review the build history.
8. **Update `AGENT.md` whenever you complete a phase.** Future agents need to know where things stand.

---

## 11. If you are a fresh agent reading this

1. Read this file completely. ✅
2. Read `docs/SRS_PRD.md` and `docs/ARCHITECTURE.md`.
3. Read `docs/ROADMAP.md` to find the current phase.
4. Run the local server and the smoke test to confirm the baseline still works.
5. Find the next pending step in the roadmap.
6. Execute it following the `[AI]/[HUMAN]/[HUMAN+AI]` tags.
7. Verify the step's check before committing.
8. Update `AGENT.md` §5 with what just completed.
9. Push at milestone boundaries only.

Welcome to the project. Build carefully. Ship reliably. 🛠️