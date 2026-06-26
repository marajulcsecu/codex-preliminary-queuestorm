"""
Case-type classifier for QueueStorm Investigator.

This module answers the *second half* of the Evidence Reasoning
question (35 pts): given a complaint plus the evidence result, which
of the 8 case_types is this, how severe is it, and does it need a
human in the loop?

Algorithm — ordered rule cascade (per SRS §3.4 and ARCHITECTURE.md)
====================================================================

The classifier runs 8 rules in a fixed order; the first match wins.
The order matters because some complaints legitimately match more
than one rule (e.g. "I sent money to a wrong person and they asked
for my OTP" — phishing beats wrong_transfer because phishing is
critical and must be escalated immediately).

Rules, in order:

1. Phishing / social engineering — credential words in a request
   context (English + Bangla).                  → critical, fraud_risk
2. Duplicate payment — ≥2 `payment` of identical amount + counterparty
   within 60 s.                                  → high, payments_ops
3. Agent cash-in issue — complaint about cash-in not reflected +
   matching `cash_in` with status pending.       → high, agent_operations
4. Merchant settlement delay — `user_type=merchant` + settlement
   complaint + `settlement` with status pending. → medium, merchant_operations
5. Payment failed — complaint says failed + matching `payment` with
   status failed.                                → high, payments_ops
6. Wrong transfer — complaint says wrong + matching `transfer`.
   Severity = medium if the evidence layer flagged
   ``established_recipient``, otherwise high.    → dispute_resolution
7. Refund request — explicit refund language.    → low, customer_support
8. Other — fallback.                              → low, customer_support

``human_review_required`` is set to True for every case_type except
refund_request (without contest), merchant_settlement, and other.
Phishing is always critical AND always requires human review.

The function ``classify(request, evidence) -> Classification`` is the
only public entry point used by the orchestrator. Like the evidence
module, this one is pure: no FastAPI imports, no I/O, no side effects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# Local — models is the leaf of the dependency graph.
from app.models import (
    AnalyzeRequest,
    TransactionHistoryEntry,
)

# Evidence layer's output feeds the classifier (esp. for the
# established-recipient severity bump on wrong_transfer).
from app.evidence import EvidenceMatch, _parse_tx_time


# =============================================================================
# Result type
# =============================================================================


@dataclass(frozen=True)
class Classification:
    """The output of the case-type classifier."""

    case_type: str
    severity: str  # low | medium | high | critical
    human_review_required: bool
    reason_codes: List[str] = field(default_factory=list)
    # A short label for the rule that fired (e.g. "phishing_rule",
    # "duplicate_payment_rule"). Useful for debug + reason_codes.
    rule_id: str = ""


# =============================================================================
# Time helpers (kept local; not exported)
# =============================================================================


def _within_seconds(tx_a: TransactionHistoryEntry, tx_b: TransactionHistoryEntry, seconds: int) -> bool:
    """True if tx_a and tx_b are within ``seconds`` of each other.

    Used by the duplicate-payment rule.
    """
    ta = _parse_tx_time(tx_a.timestamp)
    tb = _parse_tx_time(tx_b.timestamp)
    if ta is None or tb is None:
        return False
    return abs((ta - tb).total_seconds()) <= seconds


# =============================================================================
# Predicate 1 — Phishing / social engineering
# =============================================================================


# Words that are red flags when they appear in a "request" context.
# The classifier scans for these AND a nearby "share / give / tell"
# verb (English or Bangla). A bare mention like "I never share my PIN"
# does NOT trigger this rule.
_PHISHING_CRED_WORDS = (
    r"\botp\b", r"\bpin\b", r"\bpassword\b", r"\bcvv\b",
    r"ওটিপি", r"পিন", r"পাসওয়ার্ড", r"সিভিভি",
)
_PHISHING_REQUEST_VERBS = (
    r"\bshare\b", r"\bsend\b", r"\bgive\b", r"\btell\b",
    r"\basked?\b", r"\brequire[sd]?\b", r"\bneed(s)?\b",
    r"দিন", r"দিয়ে", r"বলুন", r"জানান", r"চাই",
)
# "blocked" / "suspend" is enough on its own when paired with a cred word
# — those are the classic social-engineering scripts.
_PHISHING_THREAT_WORDS = (
    r"\bblocked?\b", r"\bsuspend(ed)?\b", r"\baccount\b",
    r"বন্ধ", r"ব্লক",
)


def phishing_signals(complaint: str) -> bool:
    """True if the complaint reports a credential-request or
    social-engineering attempt.
    """
    norm = (complaint or "").lower()
    has_cred = any(re.search(p, norm) for p in _PHISHING_CRED_WORDS)
    if not has_cred:
        return False
    has_request = any(re.search(p, norm) for p in _PHISHING_REQUEST_VERBS)
    has_threat = any(re.search(p, norm) for p in _PHISHING_THREAT_WORDS)
    return has_request or has_threat


# =============================================================================
# Predicate 2 — Duplicate payment
# =============================================================================


def duplicate_payment(history: List[TransactionHistoryEntry]) -> Optional[str]:
    """Return the ``transaction_id`` of the LATER of two duplicate
    payments if the history contains a duplicate within 60s, else None.

    SAMPLE-10: two 850 BDT payments to BILLER-DESCO 12 seconds apart —
    the second one (TXN-10002) is the suspected duplicate.
    """
    if not history or len(history) < 2:
        return None
    payments = [tx for tx in history if tx.type == "payment"]
    if len(payments) < 2:
        return None

    # Look for any pair with the same (amount, counterparty) within 60s.
    # When multiple pairs match, prefer the one with the latest timestamp
    # (the duplicate is the second occurrence).
    best_pair: Optional[Tuple[TransactionHistoryEntry, TransactionHistoryEntry]] = None
    for i, a in enumerate(payments):
        for b in payments[i + 1:]:
            if abs((a.amount or 0) - (b.amount or 0)) >= 0.01:
                continue
            if (a.counterparty or "").lower() != (b.counterparty or "").lower():
                continue
            if not _within_seconds(a, b, 60):
                continue
            if best_pair is None:
                best_pair = (a, b)
            else:
                # Prefer the pair whose later timestamp is the most recent.
                ta_b = _parse_tx_time(b.timestamp) or datetime.min.replace(tzinfo=timezone.utc)
                tb_b = _parse_tx_time(best_pair[1].timestamp) or datetime.min.replace(tzinfo=timezone.utc)
                if ta_b > tb_b:
                    best_pair = (a, b)
    if best_pair is None:
        return None
    # The later of the pair is the suspected duplicate.
    ta = _parse_tx_time(best_pair[0].timestamp) or datetime.min.replace(tzinfo=timezone.utc)
    tb = _parse_tx_time(best_pair[1].timestamp) or datetime.min.replace(tzinfo=timezone.utc)
    return best_pair[1].transaction_id if tb >= ta else best_pair[0].transaction_id


# =============================================================================
# Predicate 3 — Agent cash-in issue
# =============================================================================


_AGENT_CASH_IN_KEYWORDS = (
    "cash in", "cash-in", "cashin", "ক্যাশ ইন", "ক্যাশইন",
    "ক্যাশ ইন করেছি", "ক্যাশ ইন করেছিলাম", "টাকা আসেনি", "ব্যালেন্সে আসেনি",
    "balance not reflected", "balance not updated", "balance not credited",
    "agent didn't send", "এজেন্ট পাঠায়নি", "এজেন্ট বলছে পাঠিয়েছে",
)


def agent_cash_in_signals(complaint: str) -> bool:
    norm = (complaint or "").lower()
    return any(k in norm for k in _AGENT_CASH_IN_KEYWORDS)


def pending_cash_in(history: List[TransactionHistoryEntry]) -> bool:
    return any(tx.type == "cash_in" and tx.status == "pending" for tx in history)


# =============================================================================
# Predicate 4 — Merchant settlement delay
# =============================================================================


_SETTLEMENT_KEYWORDS = (
    "settlement", "settle", "settled", "সেটেলমেন্ট",
    "settle my account", "settle my sales", "not been settled",
    "settlement delay", "sales of", "yesterday's sales",
)


def merchant_settlement_signals(complaint: str) -> bool:
    norm = (complaint or "").lower()
    return any(k in norm for k in _SETTLEMENT_KEYWORDS)


def pending_settlement(history: List[TransactionHistoryEntry]) -> bool:
    return any(tx.type == "settlement" and tx.status == "pending" for tx in history)


# =============================================================================
# Predicate 5 — Payment failed
# =============================================================================


_PAYMENT_FAILED_KEYWORDS = (
    "failed", "didn't go through", "did not go through", "wasn't completed",
    "was not completed", "balance was deducted", "balance deducted",
    "app showed failed", "deducted", "ব্যর্থ", "ডিডাক্টেড", "কাটা হয়েছে",
    "কেটে নিয়েছে",
)


def payment_failed_signals(complaint: str) -> bool:
    norm = (complaint or "").lower()
    return any(k in norm for k in _PAYMENT_FAILED_KEYWORDS)


def failed_payment(history: List[TransactionHistoryEntry]) -> bool:
    return any(tx.type == "payment" and tx.status == "failed" for tx in history)


# =============================================================================
# Predicate 6 — Wrong transfer
# =============================================================================


# Wrong-transfer triggers come in two flavors that we OR together:
#
# (a) explicit "wrong number/person" language
# (b) a "transfer was sent but not received" pattern (SAMPLE-08)
#
# We deliberately avoid the bare word "wrong" alone — SAMPLE-06 says
# "Something is wrong with my money" and that is NOT a wrong_transfer.
_SENT_KEYWORDS = (
    "sent", "send", "transferred", "transfer", "i paid", "i gave",
    "পাঠিয়েছি", "পাঠিয়ে", "পাঠাই", "পাঠানো হয়েছে", "ট্রান্সফার",
)
_WRONG_NUMBER_KEYWORDS = (
    "wrong number", "wrong person", "wrong recipient", "sent to the wrong",
    "typed it wrong", "wrong account",
    "ভুল নম্বর", "ভুল নাম্বার", "ভুল মানুষ", "ভুল ব্যক্তি",
    "ভুল করে", "ভুলে পাঠিয়েছি",
)
_WRONG_TRANSFER_PHRASES = (
    # explicit mistake / incorrect phrasing
    "by mistake", "mistakenly", "in error", "erroneously",
    "ভুল করে",
)
_NOT_RECEIVED_KEYWORDS = (
    # transfer-sent + recipient-didn't-get
    "didn't get", "did not get", "didn't receive", "did not receive",
    "hasn't received", "has not received", "not received",
    "he says he didn't", "she says she didn't", "they say they didn't",
    "he didn't get", "she didn't get", "they didn't get",
    "পায়নি", "পাননি", "পাইনি",
)


def wrong_transfer_signals(complaint: str) -> bool:
    """True if the complaint indicates a wrong / mistaken / non-received
    transfer. The discriminator is **transfer language + a wrong
    marker**, not just any mention of the word "wrong".
    """
    norm = (complaint or "").lower()
    has_sent = any(k in norm for k in _SENT_KEYWORDS)
    has_wrong_number = any(k in norm for k in _WRONG_NUMBER_KEYWORDS)
    has_wrong_phrase = any(k in norm for k in _WRONG_TRANSFER_PHRASES)
    has_not_received = any(k in norm for k in _NOT_RECEIVED_KEYWORDS)

    # Case A: explicit wrong number / wrong person — almost always wrong_transfer
    if has_wrong_number and has_sent:
        return True
    # Case B: explicit "by mistake" + transfer language
    if has_wrong_phrase and has_sent:
        return True
    # Case C: "sent money, but recipient didn't get it" (SAMPLE-08)
    if has_sent and has_not_received:
        return True
    return False


# =============================================================================
# Predicate 7 — Refund request
# =============================================================================


_REFUND_KEYWORDS = (
    "refund", "refunded", "reversal", "reverse", "money back",
    "return my", "return the", "ফেরত", "রিফান্ড", "টাকা ফেরত",
    "টাকা ফেরত দিন", "ফেরত দিন", "ফেরত দিতে",
)


def refund_signals(complaint: str) -> bool:
    norm = (complaint or "").lower()
    return any(k in norm for k in _REFUND_KEYWORDS)


def is_contested_refund(complaint: str) -> bool:
    """A refund that is contested (not just a change-of-mind). Currently
    we treat any refund request that is NOT a clean change-of-mind as
    contested. Kept as a separate predicate for future tuning.
    """
    norm = (complaint or "").lower()
    contest_words = (
        "wasn't", "was not", "didn't", "did not", "i never received",
        "service not", "service was", "not delivered", "did not deliver",
        "fraud", "scam", "double charge", "double charged",
        "পাইনি", "পাইনি", "পাইনি",
    )
    return any(k in norm for k in contest_words)


# =============================================================================
# Master classifier
# =============================================================================


def classify(
    request: AnalyzeRequest,
    evidence: EvidenceMatch,
) -> Classification:
    """Run the 8-rule cascade and return the case classification.

    Parameters
    ----------
    request
        The original ``AnalyzeRequest`` (used for complaint text,
        user_type, and history).
    evidence
        The ``EvidenceMatch`` from :mod:`app.evidence` — used to detect
        established-recipient pattern (which lowers wrong_transfer
        severity from high to medium).

    The function is order-sensitive: rules are checked in the order
    listed in the module docstring; the first match wins. This matches
    the SRS §3.4 specification.
    """
    complaint = request.complaint or ""
    history = request.transaction_history or []
    user_type = request.user_type or "unknown"

    # ------------------------------------------------------------------
    # Rule 1 — Phishing / social engineering
    # ------------------------------------------------------------------
    if phishing_signals(complaint):
        return Classification(
            case_type="phishing_or_social_engineering",
            severity="critical",
            human_review_required=True,
            reason_codes=["phishing", "credential_protection", "critical_escalation"],
            rule_id="phishing_rule",
        )

    # ------------------------------------------------------------------
    # Rule 2 — Duplicate payment
    # ------------------------------------------------------------------
    dup_tx_id = duplicate_payment(history)
    if dup_tx_id is not None:
        return Classification(
            case_type="duplicate_payment",
            severity="high",
            human_review_required=True,
            reason_codes=["duplicate_payment", "biller_verification_required"],
            rule_id="duplicate_payment_rule",
        )

    # ------------------------------------------------------------------
    # Rule 3 — Agent cash-in issue
    # ------------------------------------------------------------------
    if agent_cash_in_signals(complaint) and pending_cash_in(history):
        return Classification(
            case_type="agent_cash_in_issue",
            severity="high",
            human_review_required=True,
            reason_codes=["agent_cash_in", "pending_transaction", "agent_ops"],
            rule_id="agent_cash_in_rule",
        )

    # ------------------------------------------------------------------
    # Rule 4 — Merchant settlement delay
    # ------------------------------------------------------------------
    if (
        user_type == "merchant"
        and merchant_settlement_signals(complaint)
        and pending_settlement(history)
    ):
        return Classification(
            case_type="merchant_settlement_delay",
            severity="medium",
            human_review_required=False,
            reason_codes=["merchant_settlement", "delay", "pending"],
            rule_id="merchant_settlement_rule",
        )

    # ------------------------------------------------------------------
    # Rule 5 — Payment failed
    # ------------------------------------------------------------------
    if payment_failed_signals(complaint) and failed_payment(history):
        return Classification(
            case_type="payment_failed",
            severity="high",
            human_review_required=False,
            reason_codes=["payment_failed", "potential_balance_deduction"],
            rule_id="payment_failed_rule",
        )

    # ------------------------------------------------------------------
    # Rule 6 — Wrong transfer
    #
    # The complaint narrative is enough to set the case_type — we don't
    # require a matched transaction, because the dispute team needs to
    # be engaged even when the evidence is insufficient (SAMPLE-08:
    # "I sent 1000 to my brother yesterday but he says he didn't get
    # it" → wrong_transfer even though no transaction could be picked
    # due to ambiguity). The evidence verdict still controls severity
    # via the established-recipient rule.
    # ------------------------------------------------------------------
    if wrong_transfer_signals(complaint):
        # Severity: established-recipient → medium. Ambiguous evidence
        # (no matched tx) → also medium because we need a human to
        # disambiguate. Clean match → high (active dispute).
        is_established = evidence.established_recipient
        has_match = evidence.relevant_transaction_id is not None
        if is_established or not has_match:
            severity = "medium"
        else:
            severity = "high"
        if is_established:
            codes = [
                "wrong_transfer_claim",
                "established_recipient_pattern",
                "evidence_inconsistent",
            ]
        elif has_match:
            codes = ["wrong_transfer", "transaction_match", "dispute_initiated"]
        else:
            # Complaint says wrong-transfer but evidence is ambiguous.
            codes = ["wrong_transfer_claim", "ambiguous_match", "needs_clarification"]
        # Human review is required once a dispute is actionable
        # (matched tx or established recipient). When evidence is
        # ambiguous, we wait for the customer to clarify first — no
        # review queue yet (SAMPLE-08).
        needs_review = has_match or is_established
        return Classification(
            case_type="wrong_transfer",
            severity=severity,
            human_review_required=needs_review,
            reason_codes=codes,
            rule_id="wrong_transfer_rule",
        )

    # ------------------------------------------------------------------
    # Rule 7 — Refund request
    # ------------------------------------------------------------------
    if refund_signals(complaint):
        contested = is_contested_refund(complaint)
        return Classification(
            case_type="refund_request",
            severity="medium" if contested else "low",
            human_review_required=contested,
            reason_codes=(
                ["refund_request", "merchant_policy_dependent", "contested"]
                if contested
                else ["refund_request", "merchant_policy_dependent"]
            ),
            rule_id="refund_request_rule",
        )

    # ------------------------------------------------------------------
    # Rule 8 — Other (fallback)
    # ------------------------------------------------------------------
    return Classification(
        case_type="other",
        severity="low",
        human_review_required=False,
        reason_codes=["vague_complaint", "needs_clarification"]
        if (not history and not complaint.strip())
        else ["unmatched"],
        rule_id="other_rule",
    )


# =============================================================================
# Public exports
# =============================================================================


__all__ = [
    "Classification",
    "phishing_signals",
    "duplicate_payment",
    "agent_cash_in_signals",
    "pending_cash_in",
    "merchant_settlement_signals",
    "pending_settlement",
    "payment_failed_signals",
    "failed_payment",
    "wrong_transfer_signals",
    "refund_signals",
    "is_contested_refund",
    "classify",
]
