"""QueueStorm Investigator — FastAPI service.

Exposes:
  GET  /health            -> {"status": "ok"}
  POST /analyze-ticket    -> structured analysis per the problem statement

The service is fully rule-based; no external API calls are made.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

import analyzer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("queuestorm")

app = FastAPI(title="QueueStorm Investigator", version="1.0.0")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TransactionEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    transaction_id: str | None = None
    timestamp: str | None = None
    type: Literal["transfer", "payment", "cash_in", "cash_out", "settlement", "refund"] | None = None
    amount: float | None = None
    counterparty: str | None = None
    status: Literal["completed", "failed", "pending", "reversed"] | None = None

    @field_validator("transaction_id", "timestamp", "type", "counterparty", "status", mode="before")
    @classmethod
    def _blank_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("amount")
    @classmethod
    def _amount_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("amount must be non-negative")
        return value


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    ticket_id: str = Field(..., min_length=1)
    complaint: str = Field(..., min_length=1)
    language: Literal["en", "bn", "mixed"] | None = None
    channel: Literal["in_app_chat", "call_center", "email", "merchant_portal", "field_agent"] | None = None
    user_type: Literal["customer", "merchant", "agent", "unknown"] | None = None
    campaign_context: str | None = None
    transaction_history: list[TransactionEntry] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("ticket_id", "complaint")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("field must be a non-empty string")
        return value.strip()


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
    except (RequestValidationError, ValidationError) as e:
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
