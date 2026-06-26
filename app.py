"""QueueStorm Investigator — FastAPI service.

Exposes:
  GET  /health            -> {"status": "ok"}
  POST /analyze-ticket    -> structured analysis per the problem statement

The service is fully rule-based; no external API calls are made.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import analyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("queuestorm")

app = FastAPI(title="QueueStorm Investigator", version="1.0.0")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TransactionEntry(BaseModel):
    transaction_id: str | None = None
    timestamp: str | None = None
    type: str | None = None
    amount: float | None = None
    counterparty: str | None = None
    status: str | None = None


class AnalyzeRequest(BaseModel):
    ticket_id: str = Field(..., min_length=1)
    complaint: str = Field(..., min_length=1)
    language: str | None = None
    channel: str | None = None
    user_type: str | None = None
    campaign_context: str | None = None
    transaction_history: list[TransactionEntry] | None = None
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze-ticket")
async def analyze_ticket(req: Request) -> JSONResponse:
    # Parse JSON body explicitly so we can return clean 400s
    try:
        raw = await req.json()
    except Exception as e:
        log.warning("invalid json: %s", e)
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_json", "message": "Request body is not valid JSON."},
        )
    if not isinstance(raw, dict):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_body", "message": "Request body must be a JSON object."},
        )

    # Required-field check (fail early with 400 rather than 422 surprises)
    missing = [k for k in ("ticket_id", "complaint") if not raw.get(k)]
    if missing:
        return JSONResponse(
            status_code=400,
            content={"error": "missing_required_fields", "fields": missing,
                     "message": f"Missing required field(s): {', '.join(missing)}."},
        )

    # Validate via Pydantic for shape / types
    try:
        parsed = AnalyzeRequest(**raw)
    except RequestValidationError as e:
        return JSONResponse(
            status_code=400,
            content={"error": "schema_validation_failed", "details": e.errors(),
                     "message": "Request does not match the expected schema."},
        )
    except Exception as e:
        log.warning("pydantic parse failed: %s", e)
        return JSONResponse(
            status_code=400,
            content={"error": "schema_validation_failed", "message": str(e)},
        )

    try:
        payload = parsed.model_dump()
        result = analyzer.analyze(payload)
    except ValueError as ve:
        return JSONResponse(
            status_code=422,
            content={"error": "semantic_validation_failed", "message": str(ve)},
        )
    except Exception as e:
        log.exception("analyze failed")
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "message": "Unable to analyze ticket."},
        )

    return JSONResponse(status_code=200, content=result)


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "Unexpected server error."},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host=host, port=port, log_level="info")