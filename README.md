# QueueStorm Investigator

> bKash presents SUST CSE Carnival 2026 — Codex Community Hackathon
> AI/API Challenge · 4.5-hour Online Preliminary

A backend API service that reads a customer support complaint together with
the customer's recent transaction history and returns a structured JSON
response classifying, routing, and explaining the case for the support team.

This repository is a **placeholder scaffold** created in Phase 0. The full
implementation — evidence matcher, case-type classifier, department router,
Bangla/English/Banglish reply templating, and safety guardrails — is being
built during the round and committed incrementally.

---

## Status (live updates)

- [x] Phase 0 — project bootstrap (`/health`, `/analyze-ticket` reachable)
- [ ] Phase 1 — Pydantic v2 schema layer
- [ ] Phase 2 — evidence matcher (5-signal scorer)
- [ ] Phase 3 — case-type classifier + department router
- [ ] Phase 4 — safety scanner (negation-aware)
- [ ] Phase 5 — i18n + reasoning orchestrator
- [ ] Phase 6 — end-to-end validation against 10 sample cases
- [ ] Phase 7 — Railway deploy
- [ ] Phase 8 — final documentation
- [ ] Phase 10 — submission form

The final README with setup, run, sample request/response, AI strategy,
safety logic, and limitations is written in Phase 8.

---

## Endpoints (placeholder)

| Method | Path             | Status                                     |
| ------ | ---------------- | ------------------------------------------ |
| GET    | `/health`        | `200 OK` → `{"status":"ok"}`               |
| POST   | `/analyze-ticket` | `200 OK` → schema-valid placeholder body  |

## Quick local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then `curl http://localhost:8000/health`.

---

## Design documents

The full design lives in `docs/`:

- [`docs/SRS_PRD.md`](docs/SRS_PRD.md) — software requirements / product spec
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module design + algorithms
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — step-by-step implementation plan
- [`docs/SUBMISSION_ANSWERS.md`](docs/SUBMISSION_ANSWERS.md) — pre-filled Google Form answers
- [`docs/VIDEO_SCRIPT.md`](docs/VIDEO_SCRIPT.md) — 90-second narration script

---

## License

Built for the SUST CSE Carnival 2026 preliminary round. Team-internal use only.