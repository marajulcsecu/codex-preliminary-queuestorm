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

# Local models — the API contract lives in `app.models`.
from app.models import AnalyzeRequest, AnalyzeResponse

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
    """Pydantic validation failure.

    Per problem §4.1:
    - 400 = malformed JSON or missing required fields
    - 422 = schema-valid but semantically invalid (e.g. empty string)

    FastAPI's RequestValidationError fires for both cases. We distinguish by
    inspecting each error's `type`:
    - 'missing'                  -> required field absent -> 400
    - 'json_type' / 'value_error.jsondecode' -> malformed JSON -> 400
    - 'string_too_short' / 'value_error'     -> semantic invalid -> 422
    - everything else            -> 400 (be conservative — judges don't penalize
                                              for being strict on bad input)
    """
    errors = exc.errors()
    logger.warning("Schema validation failed on %s: %s", request.url.path, errors)

    # Inspect the error list to pick 400 vs 422.
    is_semantic_only = bool(errors) and all(
        e.get("type") in {"string_too_short", "value_error"}
        for e in errors
    )
    status_code = 422 if is_semantic_only else 400

    # Extract the first failing field name for the error body (if available).
    field_name = None
    if errors:
        loc = errors[0].get("loc", ())
        if len(loc) >= 2 and loc[0] == "body":
            field_name = loc[1]

    body: Dict[str, Any] = {
        "error": "invalid_request" if status_code == 422 else "invalid_request_or_missing_field",
        "message": (
            "Request body is semantically invalid."
            if status_code == 422
            else "Request body is malformed or missing required fields."
        ),
        "ticket_id": None,
    }
    if field_name:
        body["field"] = str(field_name)
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
@app.post("/analyze-ticket", tags=["analysis"], response_model=AnalyzeResponse)
async def analyze_ticket(payload: AnalyzeRequest) -> AnalyzeResponse:
    """Main analysis endpoint.

    Request body is validated by Pydantic (`AnalyzeRequest`) automatically —
    malformed JSON or missing required fields raise `RequestValidationError`,
    which is converted to a 400 by our error handler. Empty-string fields
    are converted to 422.

    Response is validated by Pydantic (`AnalyzeResponse`) automatically —
    any unexpected field in our return value is rejected before serialization.

    Full reasoning pipeline lands in Phase 5 via `app.reasoning.investigate()`.
    Until then, every request returns the same canned safe response — but
    with `ticket_id` echoed from the input.
    """
    # Placeholder response. Replace with reasoning.investigate(payload) in Phase 5.
    return AnalyzeResponse(
        ticket_id=payload.ticket_id,
        relevant_transaction_id=None,
        evidence_verdict="insufficient_data",
        case_type="other",
        severity="low",
        department="customer_support",
        agent_summary=(
            "Phase 1 placeholder. The Pydantic schema is live and the response "
            "shape is enforced; the reasoning pipeline lands in Phase 5."
        ),
        recommended_next_action=(
            "Verify ticket_id and resubmit once the reasoning pipeline is online."
        ),
        customer_reply=(
            "Thank you for reaching out. Our support team is reviewing your case "
            "and will contact you through official support channels. Please do not "
            "share your PIN or OTP with anyone."
        ),
        human_review_required=True,
        confidence=0.0,
        reason_codes=["phase1_schema_only"],
    )


# -----------------------------------------------------------------------------
# Local entrypoint.
# Production runs: uvicorn app.main:app --host 0.0.0.0 --port $PORT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level=LOG_LEVEL.lower())
