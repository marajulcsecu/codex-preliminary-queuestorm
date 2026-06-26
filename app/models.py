"""
Pydantic v2 schemas for QueueStorm Investigator.

These models are the **single source of truth** for the API contract. Every
endpoint that accepts or returns data uses these types — FastAPI handles
serialization, deserialization, validation, and OpenAPI schema generation.

Design rules (verified against the Preliminary Problem Statement §§5–7 and
verified in tests/test_*.py):

1. **Every enum value is a `Literal[...]` with exact spec spelling.**
   The judge is automated; a wrong enum string (different case, plural form,
   alternate spelling) fails schema validation and scores zero on the
   "API Contract & Schema" category (15 pts). Do not relax this.

2. **Required fields have no default.** Optional fields have explicit defaults
   (`None`, empty list, etc.) so the request schema matches the problem spec
   exactly.

3. **Response model uses `extra="forbid"`.** This rejects any unexpected field
   in the response body, catching schema drift at runtime.

4. **No business logic here.** Models are pure data shapes. Reasoning lives in
   `app/reasoning.py`; classification lives in `app/classifier.py`; etc.

5. **Field descriptions double as OpenAPI doc.** Judges and humans reading
   /docs see them.

Authoritative sources for the field set and enum values:
- Problem statement §5 (request schema)
- Problem statement §6 (response schema)
- Problem statement §7 (enums and taxonomy)
- Sample cases `SUST_Preli_Sample_Cases.json` (10 worked examples)
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# =============================================================================
# Enums (Litera­l types) — exact spelling per problem spec §7 and §5.2
#
# Adding a new value to any enum is a contract change. Don't do it without
# updating the SRS / Architecture docs and re-running tests.
# =============================================================================

# Problem §5.1 — request field: language
Language = Literal["en", "bn", "mixed"]

# Problem §5.1 — request field: channel
Channel = Literal[
    "in_app_chat",
    "call_center",
    "email",
    "merchant_portal",
    "field_agent",
]

# Problem §5.1 — request field: user_type
UserType = Literal["customer", "merchant", "agent", "unknown"]

# Problem §5.2 — transaction_history entry: type
TransactionType = Literal[
    "transfer",
    "payment",
    "cash_in",
    "cash_out",
    "settlement",
    "refund",
]

# Problem §5.2 — transaction_history entry: status
TransactionStatus = Literal["completed", "failed", "pending", "reversed"]

# Problem §6.1 — response field: evidence_verdict
EvidenceVerdict = Literal["consistent", "inconsistent", "insufficient_data"]

# Problem §7.1 — response field: case_type (8 values, exact spelling)
CaseType = Literal[
    "wrong_transfer",
    "payment_failed",
    "refund_request",
    "duplicate_payment",
    "merchant_settlement_delay",
    "agent_cash_in_issue",
    "phishing_or_social_engineering",
    "other",
]

# Problem §6.1 — response field: severity (4 values)
Severity = Literal["low", "medium", "high", "critical"]

# Problem §7.2 — response field: department (6 values)
Department = Literal[
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "merchant_operations",
    "agent_operations",
    "fraud_risk",
]


# =============================================================================
# Request models
# =============================================================================


class TransactionHistoryEntry(BaseModel):
    """One entry in the customer's recent transaction history.

    Per problem §5.2. `timestamp` is an ISO 8601 string — we keep it as `str`
    rather than `datetime` to avoid timezone normalization surprises and to
    keep the schema explicit (the harness can send any ISO 8601 format).
    """

    transaction_id: str = Field(..., description="Unique transaction identifier.")
    timestamp: str = Field(..., description="ISO 8601 timestamp of the transaction.")
    type: TransactionType = Field(..., description="Type of transaction.")
    amount: float = Field(..., ge=0, description="Amount in BDT (>=0).")
    counterparty: str = Field(
        ..., description="Recipient phone number, merchant ID, or agent ID."
    )
    status: TransactionStatus = Field(..., description="Transaction status.")


class AnalyzeRequest(BaseModel):
    """Request body for POST /analyze-ticket.

    Per problem §5.1.
    - `ticket_id` and `complaint` are required (validated by `Field(...)`).
    - All other fields are optional; if absent we treat them as not provided.

    We use `extra="ignore"` (the Pydantic default for request models) so the
    harness can send extra metadata fields without breaking us.
    """

    ticket_id: str = Field(..., min_length=1, description="Unique ticket identifier. Echoed in response.")
    complaint: str = Field(
        ...,
        min_length=1,
        description="Customer complaint text in English, Bangla, or mixed Banglish.",
    )
    language: Optional[Language] = Field(
        default=None,
        description="One of 'en', 'bn', 'mixed'. If absent, we detect from the complaint text.",
    )
    channel: Optional[Channel] = Field(
        default=None,
        description="One of 'in_app_chat', 'call_center', 'email', 'merchant_portal', 'field_agent'.",
    )
    user_type: Optional[UserType] = Field(
        default=None,
        description="One of 'customer', 'merchant', 'agent', 'unknown'.",
    )
    campaign_context: Optional[str] = Field(
        default=None,
        description="Campaign identifier provided by the harness (free text).",
    )
    transaction_history: Optional[List[TransactionHistoryEntry]] = Field(
        default=None,
        description="List of recent transactions (typically 2–5 entries). May be empty or absent for safety-only cases.",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Optional simulated context provided by the harness.",
    )

    # Reject empty-string ticket_id explicitly (already enforced by min_length=1,
    # but Pydantic's error message for missing min_length is more helpful than
    # the generic "string_type" error).
    @field_validator("ticket_id", "complaint")
    @classmethod
    def _no_blank_strings(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must be a non-empty, non-whitespace string")
        return v


# =============================================================================
# Response models
# =============================================================================


class AnalyzeResponse(BaseModel):
    """Response body for POST /analyze-ticket.

    Per problem §6.1.
    - Every field except `confidence` and `reason_codes` is required.
    - `extra="forbid"` means if reasoning produces an unexpected field,
      Pydantic rejects it — defense-in-depth against schema drift.

    The model is constructed by `app.reasoning.investigate()` and passed
    directly to FastAPI, which serializes it to JSON.
    """

    # Strict: no unexpected fields. Catches bugs in the reasoning pipeline.
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(..., description="Echoes the request's ticket_id.")
    relevant_transaction_id: Optional[str] = Field(
        ...,
        description="The transaction_id from history that the complaint refers to, or null if none matches.",
    )
    evidence_verdict: EvidenceVerdict = Field(
        ...,
        description="One of: consistent, inconsistent, insufficient_data.",
    )
    case_type: CaseType = Field(..., description="From the taxonomy in problem §7.1.")
    severity: Severity = Field(..., description="One of: low, medium, high, critical.")
    department: Department = Field(..., description="From the routing table in problem §7.2.")
    agent_summary: str = Field(
        ..., min_length=1, description="Concise agent-ready summary (1–2 sentences)."
    )
    recommended_next_action: str = Field(
        ..., min_length=1, description="Suggested operational next step for the support agent."
    )
    customer_reply: str = Field(
        ..., min_length=1, description="Safe official reply that respects safety rules in problem §8."
    )
    human_review_required: bool = Field(
        ...,
        description="True for disputes, suspicious cases, high-value cases, or ambiguous evidence.",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Float between 0 and 1 (optional).",
    )
    reason_codes: Optional[List[str]] = Field(
        default=None,
        description="Short reason labels supporting the decision (optional).",
    )


# =============================================================================
# Convenience: a single import surface
# =============================================================================

__all__ = [
    # Enums
    "Language",
    "Channel",
    "UserType",
    "TransactionType",
    "TransactionStatus",
    "EvidenceVerdict",
    "CaseType",
    "Severity",
    "Department",
    # Request models
    "TransactionHistoryEntry",
    "AnalyzeRequest",
    # Response model
    "AnalyzeResponse",
]
