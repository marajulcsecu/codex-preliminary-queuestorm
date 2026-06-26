"""
QueueStorm Investigator — FastAPI entry point.

bKash presents SUST CSE Carnival 2026 — Codex Community Hackathon (Preliminary).
Round: AI/API Challenge · 4.5-hour online preliminary.

This module wires the HTTP layer only. All reasoning logic lives in
sub-modules (evidence, classifier, routing, safety, i18n, reasoning) so
each can be unit-tested in isolation. The dependency direction is:

    main -> reasoning -> {evidence, classifier, routing, safety, i18n} -> models

Endpoints
---------
GET  /health           -> liveness probe. Must return {"status":"ok"} within 60 s of service start.
POST /analyze-ticket   -> main analysis endpoint. Receives one customer complaint plus
                          a short transaction history, returns structured JSON.

Per the problem statement (Preliminary Problem Statement, Sections 4–6):
- 200 on successful analysis (response body conforms to schema).
- 400 on malformed JSON or missing required fields.
- 422 on schema-valid but semantically invalid input (e.g. empty complaint).
- 500 on internal error — but body must be a safe fallback, never a stack trace.

Design notes
------------
- No authentication by design — judges call the endpoint directly
  (rubric: "no login, dashboard access, manual approval, or private network access").
- Stateless: no database, no in-memory cache, no session.
- Bind to 0.0.0.0 on $PORT (defaults 8000) — required by Docker rules and
  needed by Railway/Render.
- Logging goes to stdout in a structured format; no PII or secrets in logs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
APP_NAME = "QueueStorm Investigator"
APP_VERSION = "0.1.0"
APP_DESCRIPTION = "AI/API SupportOps copilot for a digital-finance support team."

# PORT is injected by Railway/Render. Default to 8000 for local dev.
PORT = int(os.getenv("PORT", "8000"))
HOST = "0.0.0.0"

# LOG_LEVEL controls verbosity. Never log secrets or PII.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s :: %(message)s",
)
logger = logging.getLogger(APP_NAME)

# -----------------------------------------------------------------------------
# FastAPI application
# -----------------------------------------------------------------------------
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=APP_DESCRIPTION,
)


# -----------------------------------------------------------------------------
# Error handlers — must NEVER leak stack traces, tokens, or secrets.
# -----------------------------------------------------------------------------
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic validation failure (400 / 422). Logs details, returns safe body."""
    logger.warning("Schema validation failed on %s: %s", request.url.path, exc.errors())
    # 422 if all required fields are present but values invalid (e.g. empty complaint);
    # 400 if JSON itself is malformed or required fields are missing.
    status_code = 422
    body: Dict[str, Any] = {
        "error": "invalid_request",
        "message": "Request body does not match the expected schema. Please review required fields and types.",
        "ticket_id": None,
    }
    return JSONResponse(status_code=status_code, content=body)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unexpected errors. Returns a controlled 500 — never 5xx with traceback."""
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An internal issue occurred. Please retry or contact support through official channels.",
        },
    )


# -----------------------------------------------------------------------------
# GET /health — liveness probe.
# Rubric §4: must return {"status":"ok"} within 60 s of service start.
# -----------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
async def health() -> Dict[str, str]:
    """Liveness probe. No side effects, no I/O."""
    return {"status": "ok"}


# -----------------------------------------------------------------------------
# POST /analyze-ticket — main analysis endpoint.
#
# Phase 0 (this file): returns a safe, schema-valid placeholder so the endpoint
# is reachable end-to-end and the deploy story works. The full reasoning
# pipeline lands in Phase 5 via app.reasoning.investigate().
#
# Until then, every request returns the same canned response — but with the
# ticket_id echoed from the input, so the judge harness sees the contract work.
# -----------------------------------------------------------------------------
@app.post("/analyze-ticket", tags=["analysis"])
async def analyze_ticket(request: Request) -> JSONResponse:
    """Main analysis endpoint.

    Request body must conform to the schema documented in `docs/SRS_PRD.md` §3.1.
    Full implementation lands in Phase 5. Currently returns a safe placeholder.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_json",
                "message": "Request body is not valid JSON.",
            },
        )

    ticket_id = body.get("ticket_id") if isinstance(body, dict) else None
    if not ticket_id or not isinstance(ticket_id, str):
        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_field",
                "message": "Required field 'ticket_id' is missing or not a string.",
                "field": "ticket_id",
            },
        )

    complaint = body.get("complaint", "")
    if not isinstance(complaint, str) or not complaint.strip():
        return JSONResponse(
            status_code=422,
            content={
                "error": "invalid_field",
                "message": "Required field 'complaint' must be a non-empty string.",
                "field": "complaint",
            },
        )

    # Placeholder response. Will be replaced by app.reasoning.investigate() in Phase 5.
    return JSONResponse(
        status_code=200,
        content={
            "ticket_id": ticket_id,
            "relevant_transaction_id": None,
            "evidence_verdict": "insufficient_data",
            "case_type": "other",
            "severity": "low",
            "department": "customer_support",
            "agent_summary": "Phase 0 placeholder. Reasoning pipeline lands in Phase 5.",
            "recommended_next_action": "Verify ticket_id and resubmit once the reasoning pipeline is online.",
            "customer_reply": "Thank you for reaching out. Our support team is reviewing your case and will contact you through official support channels. Please do not share your PIN or OTP with anyone.",
            "human_review_required": True,
            "confidence": 0.0,
            "reason_codes": ["phase0_placeholder"],
        },
    )


# -----------------------------------------------------------------------------
# Local entrypoint.
# Production runs: uvicorn app.main:app --host 0.0.0.0 --port $PORT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level=LOG_LEVEL.lower())
