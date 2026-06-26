"""
Reasoning orchestrator for QueueStorm Investigator.

The single public function ``investigate(request) -> AnalyzeResponse``
wires the entire analysis pipeline together. It is the only function
the HTTP layer (:mod:`app.main`) calls.

Pipeline (per ARCHITECTURE.md)
==============================

    1. pre_scan_complaint(request.complaint)        → injection_flags
    2. evidence.match(request)                      → EvidenceMatch
    3. classifier.classify(request, evidence)       → Classification
    4. routing.department(case_type, severity, ...) → Department
    5. i18n.reply_for_request(request, case_type,   → Reply
                              tx_id)
    6. safety.post_scan_pair(reply, action)         → (verdict, verdict)
       (rewrite to SAFE_REPLY_TEMPLATE on any violation)
    7. merge flags into the response
    8. return AnalyzeResponse (Pydantic auto-validates)

The function is total: it never raises on a well-formed request.
Any internal exception is caught and turned into a controlled
fallback response (the error handler in ``app.main`` is the
outermost safety net; this is the inner one).
"""

from __future__ import annotations

import logging
from typing import List, Optional

# Internal — the leaf of the dependency graph.
from app.models import AnalyzeRequest, AnalyzeResponse

# Reasoning modules.
from app.evidence import match as evidence_match
from app.classifier import classify as run_classifier
from app.routing import department as pick_department
from app.i18n import reply_for_request, detect_language
from app.safety import pre_scan_complaint, post_scan_pair


logger = logging.getLogger(__name__)


# =============================================================================
# Public entry point
# =============================================================================


def investigate(request: AnalyzeRequest) -> AnalyzeResponse:
    """Run the full pipeline and return a schema-validated response.

    This function is the ONLY function the HTTP layer needs to call.
    Every other module is wired through here.
    """
    try:
        return _investigate(request)
    except Exception:  # pragma: no cover — last-resort safety net
        logger.exception("Unhandled error in investigate()")
        return _fallback_response(request)


def _investigate(request: AnalyzeRequest) -> AnalyzeResponse:
    """The actual pipeline. Wrapped in :func:`investigate` for safety."""
    # ------------------------------------------------------------------
    # Step 1 — Pre-scan: detect prompt-injection markers in the complaint.
    # ------------------------------------------------------------------
    injection_flags = pre_scan_complaint(request.complaint or "")

    # ------------------------------------------------------------------
    # Step 2 — Evidence matching.
    # ------------------------------------------------------------------
    evidence = evidence_match(request)

    # ------------------------------------------------------------------
    # Step 3 — Case-type classification (rule cascade).
    # ------------------------------------------------------------------
    classification = run_classifier(request, evidence)

    # ------------------------------------------------------------------
    # Step 4 — Department routing.
    # ------------------------------------------------------------------
    dept = pick_department(
        classification.case_type,
        classification.severity,
        request.user_type,
    )

    # ------------------------------------------------------------------
    # Step 5 — i18n: draft the three customer-facing strings.
    # ------------------------------------------------------------------
    reply = reply_for_request(
        request,
        classification.case_type,
        tx_id=evidence.relevant_transaction_id,
    )

    # ------------------------------------------------------------------
    # Step 6 — Post-generation safety scan.
    # ------------------------------------------------------------------
    reply_v, action_v = post_scan_pair(
        reply.customer_reply, reply.recommended_next_action
    )
    final_customer_reply = reply_v.output
    final_next_action = action_v.output

    # ------------------------------------------------------------------
    # Step 7 — Merge flags: human_review_required if any injection was
    # detected, or if any safety post-scan rule fired.
    # ------------------------------------------------------------------
    human_review = classification.human_review_required
    if injection_flags:
        human_review = True
    if not reply_v.safe or not action_v.safe:
        # A safety rewrite happened; the human must see the conversation
        # log to understand what was changed.
        human_review = True

    # ------------------------------------------------------------------
    # Step 7b — Aggregate reason_codes for traceability.
    # ------------------------------------------------------------------
    reason_codes: List[str] = list(classification.reason_codes)
    if injection_flags:
        reason_codes.append("injection_marker")
    if not reply_v.safe:
        reason_codes.extend(f"reply_safety:{f}" for f in reply_v.flags)
    if not action_v.safe:
        reason_codes.extend(f"action_safety:{f}" for f in action_v.flags)

    # ------------------------------------------------------------------
    # Step 7c — Confidence heuristic.
    # ------------------------------------------------------------------
    confidence = _compute_confidence(evidence, classification, reply_v, action_v)

    # ------------------------------------------------------------------
    # Step 8 — Build the AnalyzeResponse. Pydantic validates it.
    # ------------------------------------------------------------------
    return AnalyzeResponse(
        ticket_id=request.ticket_id,
        relevant_transaction_id=evidence.relevant_transaction_id,
        evidence_verdict=evidence.evidence_verdict,
        case_type=classification.case_type,
        severity=classification.severity,
        department=dept,
        agent_summary=reply.agent_summary,
        recommended_next_action=final_next_action,
        customer_reply=final_customer_reply,
        human_review_required=human_review,
        confidence=confidence,
        reason_codes=reason_codes or None,
    )


# =============================================================================
# Confidence heuristic
# =============================================================================


def _compute_confidence(
    evidence,
    classification,
    reply_verdict,
    action_verdict,
) -> float:
    """A simple, transparent confidence score in [0.0, 1.0].

    Starts at 0.5 and adjusts based on:
    * +0.2 if the evidence has a confident (consistent/inconsistent) verdict
    * -0.2 if the evidence is insufficient
    * -0.1 if we had to rewrite the reply (safety fallback)
    * +0.1 if the case_type is well-defined (not 'other')
    * -0.1 if human_review is forced (injection or safety rewrite)
    """
    score = 0.5
    if evidence.evidence_verdict in ("consistent", "inconsistent"):
        score += 0.2
    elif evidence.evidence_verdict == "insufficient_data":
        score -= 0.2
    if not reply_verdict.safe or not action_verdict.safe:
        score -= 0.1
    if classification.case_type != "other":
        score += 0.1
    # Clamp
    return max(0.0, min(1.0, round(score, 2)))


# =============================================================================
# Safe fallback
# =============================================================================


def _fallback_response(request: AnalyzeRequest) -> AnalyzeResponse:
    """A safe response used when the pipeline itself blows up.

    Never raises; never returns a value that fails Pydantic validation.
    """
    language = detect_language(request.complaint or "", request.language)
    if language == "bn":
        customer_reply = (
            "আমাদের সাপোর্ট টিম আপনার বার্তা পেয়েছে এবং অফিসিয়াল "
            "চ্যানেলে আপনার সাথে যোগাযোগ করবে। অনুগ্রহ করে কারো সাথে "
            "আপনার পিন, ওটিপি, বা পাসওয়ার্ড শেয়ার করবেন না — আমরা কখনো "
            "এগুলো জিজ্ঞেস করি না।"
        )
    else:
        customer_reply = (
            "Thank you for reaching out. Our support team has received your "
            "message and will contact you through official support channels. "
            "Please do not share your PIN, OTP, or password with anyone — we "
            "will never ask for them."
        )
    return AnalyzeResponse(
        ticket_id=request.ticket_id,
        relevant_transaction_id=None,
        evidence_verdict="insufficient_data",
        case_type="other",
        severity="low",
        department="customer_support",
        agent_summary=(
            "An internal issue occurred while analyzing the ticket. The case "
            "has been queued for human review."
        ),
        recommended_next_action=(
            "Route this ticket to a human agent for manual investigation."
        ),
        customer_reply=customer_reply,
        human_review_required=True,
        confidence=0.0,
        reason_codes=["pipeline_error"],
    )


__all__ = ["investigate"]
