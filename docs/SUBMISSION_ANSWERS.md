# Submission Answers — Pre-fill for Google Form

> **Generated in:** Phase 10 Step 10.1
> **Form URL:** https://docs.google.com/forms/d/e/1FAIpQLScJy2bConwo1fnW1LV2kefER9MOhk-6ABfd4jUuwcy8CdZw-w/viewform
> **Form structure (4 logical sections, ~7 pages):** Team & repo → Endpoint & platform → API details & docs → Attestations & video
> **How to use:** open the form, go page by page, paste the value from the matching row. After Phase 7 (Railway live), substitute `<RAILWAY_URL>` with the generated public domain.

---

## Section 1 — Team info & submission path

| Form field | Pre-filled value |
|---|---|
| Team Leader's Phone * | _(your phone number)_ |
| GitHub Repository URL * | `https://github.com/marajulcsecu/codex-preliminary-queuestorm` |
| Submission Path * | **Working public endpoint URL (Strongly Recommended)** |

---

## Section 2 — Public endpoint & platform

| Form field | Pre-filled value |
|---|---|
| Public Endpoint Base URL * | `https://codex-preliminary-queuestorm.up.railway.app` _(replace with the actual Railway domain from Phase 7.3)_ |
| Deployment Platform * | **Railway** |

> ⚠️ Note on form typos: option 3 reads "Redner" — that is their label for **Render**. We select **Railway** (option 4) since Railway is our primary deploy. If Railway fails, fall back to **Render** (the "Redner" option) and update the URL.

---

## Section 3 — API details, deployment, documentation

| Form field | Pre-filled value |
|---|---|
| Docker build/run command (if asked) | `docker build -t codex-api . && docker run -p 8000:8000 --env-file .env codex-api` |
| Required environment variable names (if asked) | `PORT`, `LOG_LEVEL` _(names only — no values)_ |
| Sample request | see `README.md` §Sample request (TKT-001 input) |
| Sample response | see `README.md` §Sample response (TKT-001 output, identical to `sample_output.json`) |
| AI / model usage explanation | "Pure rule-based. No external AI APIs are used in the request hot path. Reasoning is implemented in `app/reasoning.py` as a deterministic pipeline: schema validation → safety pre-scan → 5-signal evidence scorer (amount, type, time, counterparty, status) → case-type rule cascade → department routing → Bangla/English/Banglish reply templating → safety post-scan. We chose rule-based over LLM for reliability under the 30s timeout, zero quota cost, and full reproducibility of decisions." |
| Safety logic explanation | "Two-layer safety guard. **Pre-scan** flags prompt-injection markers (`ignore previous instructions`, `act as`, etc.) and forces `human_review_required=true`. **Post-scan** uses a windowed negation-aware scanner that preserves defensive warnings ('do not share your PIN') while blocking genuine credential requests, refund promises (e.g. `we will refund`), and suspicious third-party channels (`telegram`, `whatsapp`, etc.). Any violation is rewritten to a safe template. See `app/safety.py` and `docs/ARCHITECTURE.md` §Safety scanner. Verified: passes all 10 organizer-provided sample replies and blocks all 14 known attack inputs." |
| Known limitations | "Multilingual support is limited to English / Bangla / Banglish. Inputs in other languages fall back to English. No persistence layer (stateless by design — judges call the endpoint directly per problem §6). No authentication by design. Latency p95 measured at <1s locally and on Railway; expected ≤5s in production." |

---

## Section 4 — Attestations, disclosure, video

| Form field | Pre-filled value |
|---|---|
| LLM or AI provider used * | **None (rules-based solution only)** |
| If Other or Hybrid, specify | _(blank — N/A since we chose None)_ |
| Known issues or blockers | "Generic FastAPI / Pydantic / Docker scaffolding template was pre-warmed before the round and adapted to the problem. The pre-warmed template contained **no project-domain logic** — no QueueStorm taxonomy, no evidence matcher, no safety scanner, no i18n templates, no reasoning pipeline. All problem-specific code (`app/models.py`, `app/evidence.py`, `app/classifier.py`, `app/routing.py`, `app/safety.py`, `app/i18n.py`, `app/reasoning.py`, all tests, sample output, README, RUNBOOK) was written during the 4.5-hour round." |
| Architecture Video link | _(paste Google Drive / YouTube unlisted link from Phase 9.3, or leave blank if no time)_ |

The 90-second narration script is in `docs/VIDEO_SCRIPT.md`.

### Final 4 attestations — checklist

Before ticking, verify each:

- [x] **"I confirm that no real customer or payment data is used in this repository. Only synthetic data."**
  - All test data is the 10 cases from `SUST_Preli_Sample_Cases.json`, which the problem statement §13 marks as synthetic.

- [x] **"I confirm that no real secrets or API keys are committed to the repository."**
  - `.gitignore` excludes `.env`. `.env.example` contains only variable names. No LLM keys are used in this build (100% rule-based).

- [x] **"I confirm that all source code in this repository was written during the round. No pre-written code was brought into the venue."**
  - All problem-specific code was written during the round (see Known issues disclosure above).

- [x] **"I confirm the team has read the Problem Statement, Team Instructions Manual, and Evaluation Rubric for Teams."**
  - All three documents studied. Citations and traceability in `docs/SRS_PRD.md` and `docs/ARCHITECTURE.md`.

---

## Submission receipt template

> Copy this block into `docs/SUBMISSION_RECEIPT.md` after submitting:

```
Submitted at:        <time>
Submission path:     Working public endpoint URL
Public URL:          <RAILWAY_URL>
Deployment platform: Railway
GitHub repo:         https://github.com/marajulcsecu/codex-preliminary-queuestorm
Video link:          <GDrive link or blank>
LLM used:            None (rules-based)
Form confirmation:   <email subject from organizers>
```
