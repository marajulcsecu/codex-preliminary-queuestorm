"""
Department routing for QueueStorm Investigator.

Static lookup table from SRS §3.4 / problem §7.2. Each case_type maps
to exactly one department. Severity is also considered: high/critical
disputes escalate (the function returns the same department either way,
but downstream code may use the severity for response tone).

This module is intentionally tiny. The work lives in
:mod:`app.classifier`; this file is just a pure function that takes
``(case_type, severity, user_type)`` and returns the department enum
string. Pure functions are easy to test in isolation.
"""

from __future__ import annotations

from typing import Optional

from app.models import Department, UserType


# =============================================================================
# The case_type → department table (per SRS §3.4 + problem §7.2)
# =============================================================================

#: Static mapping from the 8 case_types to their default departments.
#: ``None`` means: fall back to severity-based escalation.
DEPARTMENT_TABLE: dict[str, str] = {
    "wrong_transfer": "dispute_resolution",
    "payment_failed": "payments_ops",
    "refund_request": "customer_support",
    "duplicate_payment": "payments_ops",
    "merchant_settlement_delay": "merchant_operations",
    "agent_cash_in_issue": "agent_operations",
    "phishing_or_social_engineering": "fraud_risk",
    "other": "customer_support",
}


def department(
    case_type: str,
    severity: str = "low",
    user_type: Optional[str] = None,
) -> Department:
    """Return the department enum value for a given (case_type, severity).

    Parameters
    ----------
    case_type
        One of the 8 enum values from ``CaseType``.
    severity
        One of the 4 enum values from ``Severity``. Used for escalation
        heuristics (currently: contested refunds route to dispute_resolution
        instead of customer_support).
    user_type
        One of the 4 enum values from ``UserType``. Used to refine the
        default for refund_request (merchants → merchant_operations,
        agents → agent_operations, customers → customer_support).

    Returns
    -------
    Department
        The string enum value, suitable for inclusion in
        :class:`AnalyzeResponse`.
    """
    # Refund-request refinement: route by user_type when severity is low
    # (a clean change-of-mind refund from a customer goes to
    # customer_support, not dispute_resolution). For medium severity
    # (contested refund) we always route to dispute_resolution.
    if case_type == "refund_request":
        if severity == "medium":
            return "dispute_resolution"
        if user_type == "merchant":
            return "merchant_operations"
        if user_type == "agent":
            return "agent_operations"
        return "customer_support"

    # Default: look up the static table. If the case_type is unknown
    # (shouldn't happen — the classifier only emits enum values), fall
    # back to customer_support for safety.
    return DEPARTMENT_TABLE.get(case_type, "customer_support")  # type: ignore[return-value]


__all__ = ["DEPARTMENT_TABLE", "department"]
