# Implementation Roadmap — QueueStorm Investigator

**Round duration:** 4.5 hours (7:30 PM → 12:00 AM)
**Working dir:** `/home/marajul/AI_WorkShop/preliminary_hackathon/queuestorm-investigator/`
**Repo path:** to be created in Step 0
**Stack:** Python 3.11 · FastAPI · Pydantic v2 · rule-based reasoning
**Deploy:** Railway (primary) · Render (fallback)

---

## Conventions used in this roadmap

| Tag | Meaning |
|---|---|
| **[AI]** | The coding agent does it autonomously — write code, run commands, verify. |
| **[HUMAN]** | You do it manually in a browser / UI. AI cannot click buttons or create accounts. |
| **[HUMAN+AI]** | You do the manual part (paste key, click button); AI runs the verification. |
| **Verify** | A concrete check that must pass before the step is "done". |
| **Commit** | `git add . && git commit -m "..."` — every step ends with a clean commit. |
| **Push** | `git push origin main` — only at boundaries (after a milestone). |

**Time format:** `T+Nm` = N minutes after round start. Each step has an estimated duration and a hard deadline.

**Rule for clean architecture:** modules never import each other circularly; the dependency direction is always
`main → reasoning → {evidence, classifier, routing, safety, i18n} → models`.

**Rule for git:** `main` only, no branches. One commit per step. Pushes only at milestone boundaries (so a Railway deploy is always a clean snapshot).

**Rule for verification:** if a step's `Verify` check fails, the step is not done — fix it before committing. No "I'll fix it later" in a 4.5-hour round.

---

## Phase 0 — Bootstrap (T+0 → T+15m)  ⏱️ 15 min

### Step 0.1 · Create GitHub repo
**Who:** [HUMAN]
1. Go to https://github.com/new
2. Name: `codex-preliminary-queuestorm`
3. Visibility: **Public**
4. **Do NOT** initialize with README, license, or .gitignore
5. Click **Create repository**
6. Copy the HTTPS URL (looks like `https://github.com/marajulcseu/codex-preliminary-queuestorm.git`)
7. Paste the URL in your next message to me

> Why public: rubric §11 requires either public OR private with organizer (`bipulhf`) added. Public is one less step.

### Step 0.2 · Local project skeleton + first commit
**Who:** [AI]
1. Move the pre-warmed scaffold from `/codex-preliminary-template/` into `/queuestorm-investigator/` and rename app name
2. Replace placeholder `/analyze` with `/analyze-ticket` (route name per problem §4)
3. Keep the working `/health` from the template
4. Add `.gitignore`, `.env.example`, `Dockerfile`, `railway.json`, `render.yaml` (already in template)
5. Initialise git: `git init && git branch -M main`
6. Set local git user: `git config user.email "team@codex.local"` and `git config user.name "Codex Team"`
7. **Verify:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 &` then `curl localhost:8001/health` returns `{"status":"ok"}`. Kill the server.
8. **Commit:** `"chore: bootstrap project from verified template"`

### Step 0.3 · Wire remote + push
**Who:** [AI]
1. `git remote add origin <URL_FROM_STEP_0.1>`
2. `git push -u origin main`
3. **Verify:** `git log --oneline` shows the commit; remote URL is set

> After this step the repo exists on GitHub. All subsequent steps commit locally and push at milestone boundaries.

---

## Phase 1 — Schema layer (T+15 → T+45m)  ⏱️ 30 min
*Target: API Contract & Schema (15 pts) is fully defensible.*

### Step 1.1 · Write `app/models.py` with Pydantic v2 request/response
**Who:** [AI]
Define:
- `class TransactionHistoryEntry(BaseModel)` — fields exactly per problem §5.2
- `class AnalyzeRequest(BaseModel)` — fields per problem §5.1, with `ticket_id` and `complaint` required, all others optional
- `class AnalyzeResponse(BaseModel)` — fields per problem §6.1, with every required field
- **All enums as `Literal[...]`** with exact spec spelling (case-sensitive). No `str` placeholders.

**Verify:** `python -c "from app.models import AnalyzeRequest, AnalyzeResponse; ..."` import succeeds; a JSON dump of a sample request validates against the model.

**Commit:** `"feat(models): pydantic v2 schemas for /analyze-ticket with pinned enums"`

### Step 1.2 · Rewrite `app/main.py` to expose `/analyze-ticket`
**Who:** [AI]
1. Replace the placeholder `/analyze` route with `/analyze-ticket` using `AnalyzeRequest` and `AnalyzeResponse`
2. For now, the handler returns a hard-coded valid response (so we can curl it)
3. Add explicit error handler for `RequestValidationError` → returns 400 with a safe body (no stack trace)
4. Add 422 handler for empty `complaint` (Pydantic validator with `min_length=1`)

**Verify:** `curl -X POST localhost:8001/analyze-ticket -H 'Content-Type: application/json' -d '{"ticket_id":"X","complaint":"hello"}'` returns 200 with the hard-coded response shape. Send `{}` → 400. Send `{"ticket_id":"X","complaint":""}` → 422.

**Commit:** `"feat(api): POST /analyze-ticket with strict schema validation"`

### Step 1.3 · Mount schema smoke test
**Who:** [AI]
Write a one-shot script `scripts/smoke_schema.py` that sends 5 canned requests (valid, empty body, missing ticket_id, empty complaint, garbage JSON) and asserts the right HTTP codes + body shapes.

**Verify:** `python scripts/smoke_schema.py` exits 0.

**Commit:** `"test: schema smoke test for /analyze-ticket edge cases"`

---

## Phase 2 — Evidence layer (T+45 → T+120m)  ⏱️ 75 min
*Target: Evidence Reasoning (35 pts) — the biggest band.*

### Step 2.1 · Write `app/evidence.py` — 5-signal scorer
**Who:** [AI]
Pure functions, no FastAPI imports:
- `extract_signals(complaint_text: str, language: str) -> ComplaintSignals` — regex over text: amount (e.g. `5000`, `5,000`, `২০০০`), type (transfer/payment/cash_in/...), counterparty (phone or merchant ID), time phrase ("today", "yesterday", "2pm")
- `score_transaction(tx: TransactionHistoryEntry, signals: ComplaintSignals) -> int` — implements the 5-signal weights from SRS §3.3
- `pick_relevant(history: list[TransactionHistoryEntry], signals: ComplaintSignals) -> EvidenceMatch` — returns `(tx_id_or_None, verdict, scores)`
- Constants at module top: `WEIGHTS = {"amount": 30, "type": 20, "time": 20, "counterparty": 15, "status": 15}`, `MIN_SCORE = 30`

**Verify:** Run a small inline test on SAMPLE-01 (should pick TXN-9101 with consistent) and SAMPLE-08 (should pick None with insufficient_data due to tie).

**Commit:** `"feat(evidence): 5-signal scorer with weight constants"`

### Step 2.2 · Add ambiguity + established-recipient rules
**Who:** [AI]
In `app/evidence.py`:
- `ambiguity_check(scored_history) -> bool` — top two scores equal → ambiguous
- `established_recipient_check(history, counterparty, signal_type) -> bool` — same counterparty with `transfer` type appears ≥2 times in history → established

Update `pick_relevant` to apply these rules:
- Tie → return `(None, "insufficient_data", scores)`
- Established recipient + claim is "wrong transfer" → return `(top_tx_id, "inconsistent", scores)`

**Verify:** Run inline tests:
- SAMPLE-01 → `(TXN-9101, consistent)` ✅
- SAMPLE-02 → `(TXN-9202, inconsistent)` ✅
- SAMPLE-08 → `(None, insufficient_data)` ✅
- SAMPLE-06 → `(None, insufficient_data)` ✅

**Commit:** `"feat(evidence): ambiguity and established-recipient rules"`

### Step 2.3 · Write `tests/test_evidence.py`
**Who:** [AI]
Pytest file with one test per sample case that exercises evidence. Don't classify yet — just assert the right `(tx_id, verdict)` pair.

**Verify:** `pytest tests/test_evidence.py -v` — all 10 sample cases pass.

**Commit:** `"test: evidence-layer tests for all 10 sample cases"`

> **Phase 2 milestone push:** `git push origin main` after Step 2.3. Railway will not auto-deploy yet because we haven't wired the Railway project, but the repo is now in a stable, testable state.

---

## Phase 3 — Classifier + Router (T+120 → T+180m)  ⏱️ 60 min
*Target: completes Evidence Reasoning (35 pts); seeds Response Quality (10 pts).*

### Step 3.1 · Write `app/classifier.py` — case-type rule cascade
**Who:** [AI]
Pure functions, ordered rule cascade from SRS §3.4:
- `phishing_signals(complaint, language) -> bool` — keyword scan: `otp`, `pin`, `password`, `ওটিপি`, `পিন`, `password`, `blocked`, plus pattern `someone (called|texted|asked)` near credential word
- `duplicate_payment(history) -> tuple[bool, str|None]` — ≥2 `payment` with identical amount + counterparty within 60s
- `agent_cash_in_signals(complaint) -> bool` — Bangla/English: "cash in", `ক্যাশ ইন`, "balance not reflected", `ব্যালেন্সে আসেনি`
- `merchant_settlement_signals(...)`, `payment_failed_signals(...)`, `wrong_transfer_signals(...)`, `refund_signals(...)`
- `classify(req: AnalyzeRequest, evidence: EvidenceMatch) -> Classification` returning `case_type`, `severity`, `human_review_required`, `reason_codes`

The cascade runs in order: phishing → duplicate → agent_cash_in → merchant → payment_failed → wrong_transfer → refund → other.

**Verify:** Inline tests for SAMPLE-01, SAMPLE-02 (note: SAMPLE-02 already triggers established_recipient so wrong_transfer case_type still applies but verdict differs), SAMPLE-03, SAMPLE-04, SAMPLE-05, SAMPLE-07, SAMPLE-09, SAMPLE-10 all hit the right case_type. SAMPLE-06, SAMPLE-08 fall through to `other`.

**Commit:** `"feat(classifier): case-type cascade with all 8 enum values"`

### Step 3.2 · Write `app/routing.py` — department table
**Who:** [AI]
Static lookup table exactly per problem §7.2, plus a small severity bump for merchant disputes. Returns the `Literal` department value.

**Verify:** For each sample case, run `routing.department(case_type, severity)` and assert it matches the expected department.

**Commit:** `"feat(routing): department enum table per problem §7.2"`

### Step 3.3 · Add `tests/test_classifier.py` and `tests/test_routing.py`
**Who:** [AI]
One assertion per sample case on (case_type, severity, department).

**Verify:** `pytest tests/ -v` — all 60+ assertions pass.

**Commit:** `"test: classifier + routing tests for all 10 sample cases"`

---

## Phase 4 — Safety layer (T+180 → T+225m)  ⏱️ 45 min
*Target: Safety & Escalation (20 pts) — every penalty avoided.*

### Step 4.1 · Write `app/safety.py` — negation-aware scanner
**Who:** [AI]
Implement the verified windowed algorithm from ARCHITECTURE.md exactly:
- `_window_has(text, patterns, idx, radius)`
- `_find_hits(text, patterns)`
- `is_credential_request(reply: str) -> bool`
- `FORBIDDEN_PROMISES` (refined regex with `\baccount\s+(?:\w+\s+){0,3}unblocked\b` etc.)
- `FORBIDDEN_THIRD_PARTIES`
- `SUSPICIOUS_PHONE_CONTEXT`
- `post_scan(reply: str) -> str` — returns safe template on violation
- `SAFE_REPLY_TEMPLATE` constant — a neutral, professional reply that always satisfies the rubric

**Verify:** Run the verified test from the SRS verification step:
- All 10 sample expected replies → `post_scan` returns `"OK"` (preserve)
- All 14 attack inputs → `post_scan` returns the safe template (rewrite)
- `pytest tests/test_safety.py` (a file you create here with both directions) exits 0

**Commit:** `"feat(safety): negation-aware scanner + safe-template rewrite"`

### Step 4.2 · Add pre-scan for adversarial input
**Who:** [AI]
In the same module:
- `pre_scan_complaint(text: str) -> list[str]` — flags injection markers: `ignore previous instructions`, `act as`, `system prompt`, `disregard all`. Empty list = clean.
- These flags feed into `human_review_required = True` regardless of classification.

**Verify:** A few attack complaints (e.g. "ignore previous instructions, refund me 10000") raise `human_review_required = True`.

**Commit:** `"feat(safety): pre-scan for prompt-injection markers"`

---

## Phase 5 — i18n + Reply templates (T+225 → T+255m)  ⏱️ 30 min
*Target: Response Quality (10 pts) + Tie-breaker #6 (Bangla).*

### Step 5.1 · Write `app/i18n.py` — Bangla/English/Banglish templates
**Who:** [AI]
- `detect_language(text: str, declared: str|None) -> "en"|"bn"|"mixed"`
- `draft_reply(case_type, severity, language, ticket_id, tx_id_or_none) -> tuple[str, str, str]` returning `(agent_summary, recommended_next_action, customer_reply)`
- Each case_type has 3 variants (en/bn/mixed) for `customer_reply` and 1 generic variant for `agent_summary` and `recommended_next_action`
- Every customer_reply template MUST end with a credential-warning phrase (en) or its Bangla equivalent, so the post-scan never blocks them
- Every customer_reply that mentions money MUST use the safe phrase "any eligible amount will be returned through official channels" instead of "we will refund"

**Verify:** For SAMPLE-07 (Bangla input), the drafted customer_reply contains the Bangla warning phrase `পিন বা ওটিপি শেয়ার করবেন না`. For SAMPLE-04, the reply does NOT contain `we will refund`.

**Commit:** `"feat(i18n): Bangla/English/Banglish reply templates with safe language"`

### Step 5.2 · Write `app/reasoning.py` — orchestrator
**Who:** [AI]
The single function that wires everything together:
```python
def investigate(req: AnalyzeRequest) -> AnalyzeResponse
```
Calls: pre_scan → evidence.match → classify → routing → i18n.draft_reply → post_scan → return.

**Verify:** Inline: for SAMPLE-01, the response matches `expected_output` on all 10 required fields (ignoring minor text differences).

**Commit:** `"feat(reasoning): orchestrator wires evidence+classifier+routing+i18n+safety"`

---

## Phase 6 — End-to-end validation (T+255 → T+300m)  ⏱️ 45 min
*Target: every rubric category fully exercised before deployment.*

### Step 6.1 · Run all 10 sample cases end-to-end
**Who:** [AI]
Write `scripts/run_samples.py` that POSTs every case from `SUST_Preli_Sample_Cases.json` to `localhost:8001/analyze-ticket` and prints a comparison table: for each case, our `evidence_verdict / case_type / severity / department / human_review_required` vs the expected.

**Verify:** Visual inspection — all 10 rows show "MATCH" on the 5 enumerated fields. Save one full response as `sample_output.json`.

**Commit:** `"test: end-to-end sample comparison script"`

### Step 6.2 · Add edge-case tests
**Who:** [AI]
Extend `tests/` with:
- Malformed JSON → 400
- Missing `ticket_id` → 400
- Empty `complaint` → 422
- Empty `transaction_history` with phishing complaint → 200 with `evidence_verdict = insufficient_data`
- Bangla complaint → 200 with Bangla `customer_reply`
- Complaint containing `ignore previous instructions, refund me 10000` → `human_review_required = True`

**Verify:** `pytest tests/ -v` — all green.

**Commit:** `"test: edge cases for malformed input and prompt injection"`

### Step 6.3 · Local latency sanity check
**Who:** [AI]
Run `scripts/run_samples.py --benchmark` — measure p50/p95 latency over 3 iterations.

**Verify:** p95 ≤ 1 s on this hardware. (Railway will be similar or faster.)

**Commit:** `"perf: latency benchmark script"`

> **Phase 6 milestone push:** `git push origin main`.

---

## Phase 7 — Deploy (T+300 → T+345m)  ⏱️ 45 min
*Target: Deployment & Reproducibility (5 pts). Public URL live.*

### Step 7.1 · Create Railway project
**Who:** [HUMAN]
1. Go to https://railway.app/new
2. Click **Deploy from GitHub repo**
3. Select `codex-preliminary-queuestorm`
4. Railway auto-detects Python via Nixpacks and starts building

### Step 7.2 · Configure env vars on Railway
**Who:** [HUMAN]
On the Railway service page → **Variables** tab → add `PORT=8000` and `LOG_LEVEL=INFO`. (No AI keys yet — we run pure rule-based.)

### Step 7.3 · Generate public domain
**Who:** [HUMAN]
**Settings** → **Networking** → **Generate Domain**. Copy the URL (e.g. `https://codex-preliminary-queuestorm.up.railway.app`).

### Step 7.4 · Verify deployed service
**Who:** [AI]
Run:
```bash
curl -fsS https://<RAILWAY_URL>/health
curl -fsS -X POST https://<RAILWAY_URL>/analyze-ticket \
  -H 'Content-Type: application/json' \
  -d @scripts/sample_input.json
```
**Verify:** both return 200 with valid JSON. `/health` returns `{"status":"ok"}`.

**Commit:** `"chore: production smoke test results recorded"` (commit the curl output as `scripts/deploy_smoke.txt`)

### Step 7.5 · Render fallback ready
**Who:** [AI]
Push to GitHub is already done. The `render.yaml` is already committed. Render is one-click:
- [HUMAN] New Web Service → Public Git Repo → `codex-preliminary-queuestorm` → Deploy
- This is the fallback. Only needed if Railway breaks.

---

## Phase 8 — Documentation + Submission (T+345 → T+390m)  ⏱️ 45 min
*Target: Documentation (5 pts) + bonus Manual Review pool.*

### Step 8.1 · Write the final README
**Who:** [AI]
Replace template README with full content. Required sections per problem §11:
- Project title + event + round
- Setup (clone, venv, install)
- Run (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`)
- Endpoints (`/health`, `/analyze-ticket`) with example request + response
- **MODELS section** — explicitly state "no AI/LLM used in the hot path; reasoning is fully rule-based" plus reasoning for the choice
- AI/Model usage explanation (rule-based + safety rationale)
- Safety logic (the 4 penalties and how we avoid them)
- Known limitations (no multilingual beyond en/bn/mixed; cannot handle complaints in other languages; etc.)
- Deployment (Railway primary, Render fallback)

**Verify:** README has all required sections; sample request/response matches a real output.

**Commit:** `"docs: complete README with MODELS, safety, and limitations"`

### Step 8.2 · Write RUNBOOK.md
**Who:** [AI]
Step-by-step bring-up instructions for a stranger (the rubric's "code with runbook" submission path). Copy-pasteable commands.

**Commit:** `"docs: RUNBOOK with copy-paste setup steps"`

### Step 8.3 · Generate `sample_output.json`
**Who:** [AI]
Already saved in Step 6.1 — verify it's committed and contains a real response from TKT-001.

### Step 8.4 · Final `git push` and submission form prep
**Who:** [AI+HUMAN]
1. [AI] `git push origin main`
2. [HUMAN] Open the submission form. Pre-fill:
   - Team name + ID
   - GitHub URL: `https://github.com/marajulcseu/codex-preliminary-queuestorm`
   - Submission path: **A. Live URL**
   - Public endpoint base URL: `<RAILWAY_URL>`
   - Docker build/run command (in case): `docker build -t codex-api . && docker run -p 8000:8000 codex-api`
   - Required env vars: `PORT`, `LOG_LEVEL`
   - Sample request/response: link to `README.md#sample`
   - AI/model usage: "Pure rule-based. No external AI APIs used."
   - Safety logic: "Negation-aware scanner + safe-template rewrite; see README §safety."
   - Known limitations: see README
   - Checkboxes: no real customer data, no secrets committed.

**Commit:** `"chore: final submission prep"`

---

## Phase 9 — Buffer + Stretch (T+390 → T+420m)  ⏱️ 30 min

### Step 9.1 · Polish pass
**Who:** [AI]
- Re-read every `customer_reply` template, tighten wording for Response Quality points
- Add any missing `confidence` and `reason_codes` (currently optional, but adds polish)
- Sweep logs to ensure no PII / secret / stack trace ever appears

### Step 9.2 · Hidden-test hardening
**Who:** [AI]
If time allows:
- Add a `metadata`-aware extension hook (in case hidden tests send non-empty `metadata`)
- Add a length-cap validator on `complaint` (prevent megabyte payloads → DoS)
- Tighten the regexes for unusual counterparty formats (e.g. `+88 017...` with spaces)

### Step 9.3 · Architecture walkthrough video script (recommended, tie-breaker #8)
**Who:** [AI]
The submission form marks the architecture video as **Recommended** (it can tip tie-breaker #8). Generate a 90-second narration script the team can record on a phone or webcam:

`docs/VIDEO_SCRIPT.md` — paragraph-by-paragraph script (~200 words) covering:
1. What the API does (15s)
2. Evidence reasoning: 5-signal scorer + ambiguity + established-recipient (30s)
3. Safety: negation-aware scanner + safe-template rewrite (20s)
4. Deployment: Railway (primary) + Docker fallback (15s)
5. Limitations (10s)

[AI] Writes the script. [HUMAN] Reads it into a 90s recording. Upload to Google Drive, paste link in submission form.

---

## Phase 10 — Submission Form Pre-fill (T+420 → T+445m)  ⏱️ 25 min

The Google Form has **7 pages** of fields. We pre-fill every answer into `docs/SUBMISSION_ANSWERS.md` so [HUMAN] just copy-pastes them page by page. Form lives at the URL you shared.

### Step 10.1 · [AI] Generate `SUBMISSION_ANSWERS.md`
Pre-fill all 7 pages with verified answers:

| Form field | Pre-filled value |
|---|---|
| Team Leader's Phone * | your number |
| GitHub Repository URL * | `https://github.com/marajulcsecu/codex-preliminary-queuestorm` |
| Submission Path * | **Working public endpoint URL (Strongly Recommended)** |
| Public Endpoint Base URL * | `<RAILWAY_URL>` (from Phase 7.4) |
| Deployment Platform * | **Railway** (note: form has a typo "Redner" — that's their Render option; we pick Railway) |
| LLM or AI provider used * | **None (rules-based solution only)** |
| If Other or Hybrid, specify | (blank) |
| Known issues or blockers | (one-line honest note) |
| Architecture Video | Google Drive / YouTube link from Step 9.3 (optional but recommended) |
| Final Checkboxes (4) | All checked — see Step 10.2 |

### Step 10.2 · [HUMAN] Final 4 attestations
The form requires **all 4** checkboxes ticked. Each one corresponds to a deliverable we already control:

| Checkbox | How we already satisfy it |
|---|---|
| "No real customer or payment data" | All 10 sample cases are synthetic per problem §13 |
| "No real secrets or API keys committed" | `.gitignore` excludes `.env`; `.env.example` has placeholders only; AI does not have API keys |
| "All source code written during the round. No pre-written code brought in." | ⚠️ **This is a delicate one — see Step 10.3.** |
| "Team has read Problem Statement, Team Instructions Manual, and Evaluation Rubric" | All three documents studied in Phase 0 |

### Step 10.3 · ⚠️ Decision required: pre-existing scaffold honesty
The roadmap pre-warmed the **FastAPI + Pydantic + Uvicorn + Dockerfile + Railway/Render template** in `/home/marajul/AI_WorkShop/preliminary_hackathon/codex-preliminary-template/` **before the round started** (today afternoon). The "no pre-written code" checkbox is asking for full honesty about this.

Two interpretations of the form field:
- **Strict reading:** *nothing* pre-written, including scaffolding. We must mark it unchecked and explain in `Known issues or blockers`.
- **Common reading:** *no project-domain code* (the QueueStorm investigator logic) pre-written. Generic scaffolding is acceptable.

**My recommendation:** mark it **checked** and add this line to "Known issues or blockers":
> "Project-domain code (evidence matcher, classifier, safety scanner, i18n) written during the round. Generic FastAPI/Docker/scaffold template (boilerplate web framework setup) was pre-warmed before the round and adapted to the problem."

This is the honest answer and is unlikely to disqualify us — the rubric's intent is to prevent teams from bringing a finished project, not to forbid a Python venv.

> [HUMAN] Please confirm this approach or specify your own wording. If you want to be extra safe, we mark it unchecked and add a longer explanation.

### Step 10.4 · [HUMAN] Submit the form
Open the Google Form, paste the pre-filled answers page by page, tick the 4 attestations, click Submit. Confirmation email arrives at the address shown on the form (visible in your screenshots).

### Step 10.5 · [AI] Save submission receipt
Save a copy of the submitted answers to `docs/SUBMISSION_RECEIPT.md` and commit.

---

## Total time budget

| Phase | Duration | Cumulative |
|---|---|---|
| 0 — Bootstrap | 15 min | 0:15 |
| 1 — Schema | 30 min | 0:45 |
| 2 — Evidence | 75 min | 2:00 |
| 3 — Classifier + Routing | 60 min | 3:00 |
| 4 — Safety | 45 min | 3:45 |
| 5 — i18n + Reasoning | 30 min | 4:15 |
| 6 — E2E Validation | 45 min | 5:00 ⚠️ over budget |
| 7 — Deploy | 45 min | 5:45 |
| 8 — Docs + Submission | 45 min | 6:30 |
| 9 — Buffer + Video | 30 min | 7:00 |
| 10 — Form pre-fill | 25 min | 7:25 |

**Honest assessment:** the round is 4.5 hours (270 min), not 7.5 hours. This roadmap is intentionally over-scoped. Two strategies to fit:

**Strategy A — Compress phases:** merge Phase 5 into Phase 3 (write i18n templates inline in `reasoning.py` first, refactor later), skip the polish pass. Fits in 4h with 30 min slack.

**Strategy B — Parallelize:** during Phase 6 testing, [HUMAN] does Phase 7 GitHub/Railway setup in parallel. Saves ~30 min. Combine with the Phase 10 form pre-fill running parallel to Phase 8 docs.

**My recommendation:** **Strategy B** — Phase 6.3 latency check runs in seconds; while [HUMAN] clicks through Railway, [AI] moves into Phase 7.4 verification the moment the URL is live. This is exactly how hackathon winners move.

**Final strategy: ship by T+270m (deadline)**
- Phase 0 → 1 → 2 (skip nothing): evidence done by T+120
- Phase 3 + 4 + 5 merged: classifier + routing + safety + i18n by T+225
- Phase 6 E2E against 10 samples by T+255
- Phase 7 Railway deploy by T+285 (parallel with Phase 6 end)
- Phase 8 README + RUNBOOK by T+315
- Phase 10 form pre-fill + submit by T+270 — **the deadline is non-negotiable**

**Cutting-room floor (if anything goes wrong, drop in this order):**
1. Phase 9 (buffer + video) — saves 30 min, no rubric points lost
2. Phase 8.2 RUNBOOK.md — README covers the same ground
3. Phase 5 refactor into `i18n.py` — keep templates inline in `reasoning.py`
4. Phase 6.3 latency benchmark — eyeball it instead
5. Phase 7.5 Render fallback — only needed if Railway breaks

### Cutting-room floor (if anything goes wrong)
- Phase 8.3 sample_output.json → reuse Step 6.1 artifact
- Phase 9 entirely → drop without losing rubric points
- Phase 7.5 Render fallback → only if Railway breaks
- Phase 8.2 RUNBOOK → README covers the same content

---

## Git workflow summary

| Event | Action |
|---|---|
| After every step | `git add . && git commit -m "<message>"` |
| After Phase 2 (evidence done) | `git push origin main` |
| After Phase 6 (E2E green) | `git push origin main` |
| After Phase 8 (submission prep) | `git push origin main` |
| Every push | Railway auto-rebuilds (~60 s) → judgeable URL always reflects latest code |

---

## Sign-off gates

Before submitting, ALL of these must be true:

### Technical
- [ ] `git log --oneline` shows ≥15 clean commits
- [ ] `pytest tests/ -v` exits 0
- [ ] `curl https://<RAILWAY_URL>/health` returns `{"status":"ok"}`
- [ ] `curl -X POST https://<RAILWAY_URL>/analyze-ticket -d @sample.json` returns 200 with all 10 required fields
- [ ] `sample_output.json` is committed
- [ ] p95 latency on Railway < 5 s (measured by 10 sequential calls)

### Repository hygiene
- [ ] README has MODELS + safety + limitations
- [ ] `.env.example` is committed (no real values)
- [ ] No `.env`, `*.pem`, `*.key`, or `credentials.json` is in the repo (`git ls-files | grep -E '\.env$|\.pem|\.key|credentials'`)
- [ ] Repository is **public**

### Submission form pre-fill
- [ ] `docs/SUBMISSION_ANSWERS.md` is committed with all 7 pages of answers
- [ ] "Submission Path" = **Working public endpoint URL**
- [ ] "Deployment Platform" = **Railway** (or Render if Railway failed)
- [ ] "LLM or AI provider used" = **None (rules-based solution only)**
- [ ] Architecture Video link filled (optional but recommended for tie-breaker #8)
- [ ] "Known issues or blockers" mentions the pre-warmed generic scaffold (see Step 10.3)
- [ ] All 4 final attestations checkable — confirm Step 10.2 status before ticking

When all boxes are checked, fill the submission form and you're done.
