# Architecture Walkthrough Video — 90-Second Script

> **Phase:** 9.3 · Recommended for tie-breaker #8 (Architecture video)
> **Max length:** 90 seconds
> **Delivery:** upload to Google Drive / YouTube (unlisted) / OneDrive / Dropbox, paste the shareable link in the submission form

---

## Recording tips

- Use a phone camera or webcam — quality doesn't matter; clarity does
- Read at a calm pace (~150 words per minute → 200 words ≈ 80 seconds)
- Show `docs/ARCHITECTURE.md` open in a browser, scroll through as you speak
- Total script below is **200 words**, designed to land at ~85 seconds with pauses

---

## Script (~200 words, 85 seconds)

**[0:00–0:15 — What we built]**

> Hi, we're Team Codex from SUST. We built QueueStorm Investigator — a backend API for a digital-finance support team. It receives one customer complaint plus a short transaction history, and returns a structured JSON response that classifies the case, picks the right transaction, and drafts a safe reply. The service exposes two endpoints: GET /health and POST /analyze-ticket.

**[0:15–0:45 — Evidence reasoning]**

> Our evidence reasoning uses a five-signal scorer. We extract the complaint's amount, type, time phrase, counterparty, and expected status, then score every transaction in history. The highest-scoring transaction wins. When two transactions tie, we return null and ask for clarification instead of guessing — that's how we handle ambiguous cases. When a customer claims a wrong transfer but they've sent money to the same recipient three times before, we flag the evidence as inconsistent and route to human review.

**[0:45–1:05 — Safety]**

> Safety is a hard requirement. We use a two-layer guard. The pre-scan flags prompt-injection attempts. The post-scan uses a negation-aware scanner: it allows defensive warnings like "do not share your PIN" but blocks genuine requests like "share your PIN". Forbidden phrases — refund promises, suspicious third-party channels — are rewritten to a safe template. We verified against all ten organizer-provided cases and fourteen attack inputs.

**[1:05–1:20 — Deployment]**

> Deployment is Railway for the public endpoint, with a Dockerfile ready as fallback. Stateless service, no database, sub-second p95. Schema is enforced with Pydantic v2, all enums pinned by literal type.

**[1:20–1:30 — Closing]**

> Pure rule-based, fully reproducible, no external API dependencies. Thank you.

---

## Optional on-screen actions while recording

While the voice-over plays, you can show:

1. **`README.md`** scrolling — shows tech stack + sample request/response
2. **`docs/ARCHITECTURE.md`** scrolling — shows the layered pipeline
3. **A live `curl` to your Railway URL** — proves the service is up
4. **`app/safety.py`** highlighted — shows the negation-aware scanner
5. **`app/reasoning.py`** highlighted — shows the orchestrator
6. **`pytest tests/ -v`** running — shows green tests

Total recording time: ~85 seconds. Upload, paste link into submission form (Section 4 of `SUBMISSION_ANSWERS.md`).

---

## Honesty disclaimer

If you record before Phase 8 (final README) is done, the on-screen scroll may show a template README — that's fine. Judges see the **final** repo anyway; the video is a tie-breaker, not the primary deliverable.