"""
Pytest coverage for the case-type classifier + department router.

These tests exercise ``app.classify.classify`` and ``app.routing.department``
against the 10 worked sample cases and a focused set of unit tests
for the rule predicates. The goal is to lock in the
``(case_type, severity, department, human_review_required)`` decision
matrix so regressions surface immediately.

Run with:

    pytest tests/test_classifier.py -v

The module has no FastAPI dependency and no I/O, so no live server
is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.classifier import (
    Classification,
    agent_cash_in_signals,
    classify,
    duplicate_payment,
    failed_payment,
    is_contested_refund,
    merchant_settlement_signals,
    payment_failed_signals,
    pending_cash_in,
    pending_settlement,
    phishing_signals,
    refund_signals,
    wrong_transfer_signals,
)
from app.evidence import extract_signals, match, pick_relevant
from app.models import AnalyzeRequest
from app.routing import DEPARTMENT_TABLE, department


# ---------------------------------------------------------------------------
# Test fixture: load the 10 worked sample cases once per session
# ---------------------------------------------------------------------------

_SAMPLE_PATH = Path(__file__).resolve().parents[2] / (
    "Question Provided/SUST_Preli_Sample_Cases.json"
)


def _load_sample_cases() -> List[Dict[str, Any]]:
    with _SAMPLE_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data["cases"]


SAMPLE_CASES = _load_sample_cases()


# ---------------------------------------------------------------------------
# Per-sample-case (case_type, severity, department, human_review) assertions
# ---------------------------------------------------------------------------


def _classify_case(case: Dict[str, Any]):
    """Helper: run evidence + classify + route on a sample case."""
    req = AnalyzeRequest(**case["input"])
    ev = match(req)
    cls = classify(req, ev)
    dept = department(cls.case_type, cls.severity, req.user_type)
    return cls, dept, ev


@pytest.mark.parametrize(
    "case",
    SAMPLE_CASES,
    ids=[c["id"] for c in SAMPLE_CASES],
)
def test_case_type_matches_sample_case(case: Dict[str, Any]) -> None:
    cls, _dept, _ev = _classify_case(case)
    exp = case["expected_output"]
    assert cls.case_type == exp["case_type"], (
        f"{case['id']}: expected case_type={exp['case_type']!r}, "
        f"got {cls.case_type!r}"
    )


@pytest.mark.parametrize(
    "case",
    SAMPLE_CASES,
    ids=[c["id"] for c in SAMPLE_CASES],
)
def test_severity_matches_sample_case(case: Dict[str, Any]) -> None:
    cls, _dept, _ev = _classify_case(case)
    exp = case["expected_output"]
    assert cls.severity == exp["severity"], (
        f"{case['id']}: expected severity={exp['severity']!r}, "
        f"got {cls.severity!r}"
    )


@pytest.mark.parametrize(
    "case",
    SAMPLE_CASES,
    ids=[c["id"] for c in SAMPLE_CASES],
)
def test_department_matches_sample_case(case: Dict[str, Any]) -> None:
    cls, dept, _ev = _classify_case(case)
    exp = case["expected_output"]
    assert dept == exp["department"], (
        f"{case['id']}: expected department={exp['department']!r}, "
        f"got {dept!r}"
    )


@pytest.mark.parametrize(
    "case",
    SAMPLE_CASES,
    ids=[c["id"] for c in SAMPLE_CASES],
)
def test_human_review_flag_matches_sample_case(case: Dict[str, Any]) -> None:
    cls, _dept, _ev = _classify_case(case)
    exp = case["expected_output"]
    assert cls.human_review_required == exp["human_review_required"], (
        f"{case['id']}: expected human_review_required="
        f"{exp['human_review_required']!r}, got {cls.human_review_required!r}"
    )


# ---------------------------------------------------------------------------
# Unit tests — phishing predicate
# ---------------------------------------------------------------------------


def test_phishing_detects_otp_request() -> None:
    assert phishing_signals("Someone asked for my OTP to unblock my account") is True


def test_phishing_detects_pin_threat() -> None:
    assert phishing_signals("They called and said my account will be blocked if I share PIN") is True


def test_phishing_detects_bangla() -> None:
    assert phishing_signals("আমার ওটিপি দিয়ে যাচাই করতে বলেছে, অ্যাকাউন্ট বন্ধ হয়ে যাবে") is True


def test_phishing_input_always_indicates_social_engineering() -> None:
    """A customer complaint containing 'asked for my PIN' is a phishing
    report (SAMPLE-05). The defensive phrasing in the company's reply
    ('do not share your PIN') is handled by the safety layer, not the
    classifier — see ARCHITECTURE.md §Safety scanner.
    """
    # Customer complaint about someone asking for their PIN → phishing.
    assert phishing_signals(
        "Someone called me and asked for my OTP to unblock my account"
    ) is True
    # The pre-scan + post-scan safety layer handles the inverse case
    # (the company's own defensive warning text).


def test_phishing_requires_combination() -> None:
    """A bare 'pin' mention with no request/threat context is not phishing."""
    assert phishing_signals("My PIN was changed yesterday, how do I reset?") is False


# ---------------------------------------------------------------------------
# Unit tests — duplicate payment
# ---------------------------------------------------------------------------


def test_duplicate_payment_picks_later_tx() -> None:
    """Two identical payments 12 seconds apart → returns the LATER one."""
    from app.models import TransactionHistoryEntry

    history = [
        TransactionHistoryEntry(
            transaction_id="TX-A",
            timestamp="2026-04-14T08:15:30Z",
            type="payment",
            amount=850.0,
            counterparty="BILLER-DESCO",
            status="completed",
        ),
        TransactionHistoryEntry(
            transaction_id="TX-B",
            timestamp="2026-04-14T08:15:42Z",
            type="payment",
            amount=850.0,
            counterparty="BILLER-DESCO",
            status="completed",
        ),
    ]
    assert duplicate_payment(history) == "TX-B"


def test_duplicate_payment_rejects_different_counterparty() -> None:
    from app.models import TransactionHistoryEntry

    history = [
        TransactionHistoryEntry(
            transaction_id="TX-A",
            timestamp="2026-04-14T08:15:30Z",
            type="payment",
            amount=850.0,
            counterparty="BILLER-A",
            status="completed",
        ),
        TransactionHistoryEntry(
            transaction_id="TX-B",
            timestamp="2026-04-14T08:15:42Z",
            type="payment",
            amount=850.0,
            counterparty="BILLER-B",
            status="completed",
        ),
    ]
    assert duplicate_payment(history) is None


def test_duplicate_payment_rejects_too_far_apart() -> None:
    from app.models import TransactionHistoryEntry

    history = [
        TransactionHistoryEntry(
            transaction_id="TX-A",
            timestamp="2026-04-14T08:15:30Z",
            type="payment",
            amount=850.0,
            counterparty="BILLER-DESCO",
            status="completed",
        ),
        TransactionHistoryEntry(
            transaction_id="TX-B",
            timestamp="2026-04-14T08:20:00Z",  # 4 min 30 s later
            type="payment",
            amount=850.0,
            counterparty="BILLER-DESCO",
            status="completed",
        ),
    ]
    assert duplicate_payment(history) is None


def test_duplicate_payment_handles_empty_history() -> None:
    assert duplicate_payment([]) is None
    assert duplicate_payment(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit tests — agent cash-in
# ---------------------------------------------------------------------------


def test_agent_cash_in_detects_bangla() -> None:
    assert agent_cash_in_signals("আমার ব্যালেন্সে টাকা আসেনি") is True


def test_agent_cash_in_detects_english() -> None:
    assert agent_cash_in_signals("I did cash in this morning but the balance is not reflected") is True


def test_agent_cash_in_ignores_other_complaints() -> None:
    assert agent_cash_in_signals("I want a refund please") is False


def test_pending_cash_in_finds_status_pending() -> None:
    from app.models import TransactionHistoryEntry

    history = [
        TransactionHistoryEntry(
            transaction_id="TX-A",
            timestamp="2026-04-14T09:30:00Z",
            type="cash_in",
            amount=2000.0,
            counterparty="AGENT-318",
            status="pending",
        )
    ]
    assert pending_cash_in(history) is True


# ---------------------------------------------------------------------------
# Unit tests — merchant settlement
# ---------------------------------------------------------------------------


def test_merchant_settlement_signal_detects_phrase() -> None:
    assert merchant_settlement_signals("yesterday's sales of 15000 have not been settled") is True


def test_pending_settlement_finds_pending() -> None:
    from app.models import TransactionHistoryEntry

    history = [
        TransactionHistoryEntry(
            transaction_id="TX-A",
            timestamp="2026-04-13T18:00:00Z",
            type="settlement",
            amount=15000.0,
            counterparty="MERCHANT-SELF",
            status="pending",
        )
    ]
    assert pending_settlement(history) is True


# ---------------------------------------------------------------------------
# Unit tests — payment failed
# ---------------------------------------------------------------------------


def test_payment_failed_signal_detects_keyword() -> None:
    assert payment_failed_signals("the app showed failed but my balance was deducted") is True


def test_payment_failed_signal_detects_bangla() -> None:
    assert payment_failed_signals("পেমেন্ট ব্যর্থ হয়েছে কিন্তু টাকা কেটে নিয়েছে") is True


def test_failed_payment_finds_status_failed() -> None:
    from app.models import TransactionHistoryEntry

    history = [
        TransactionHistoryEntry(
            transaction_id="TX-A",
            timestamp="2026-04-14T16:00:00Z",
            type="payment",
            amount=1200.0,
            counterparty="MERCHANT-X",
            status="failed",
        )
    ]
    assert failed_payment(history) is True


# ---------------------------------------------------------------------------
# Unit tests — wrong transfer
# ---------------------------------------------------------------------------


def test_wrong_transfer_detects_explicit() -> None:
    assert wrong_transfer_signals("I sent 5000 taka to a wrong number") is True


def test_wrong_transfer_detects_mistake() -> None:
    assert wrong_transfer_signals("I sent 2000 to the wrong person by mistake") is True


def test_wrong_transfer_detects_not_received() -> None:
    """SAMPLE-08: 'I sent 1000 to my brother yesterday but he says
    he didn't get it' — no 'wrong' word, but the transfer-not-received
    pattern still indicates a wrong_transfer case.
    """
    assert (
        wrong_transfer_signals(
            "I sent 1000 to my brother yesterday but he says he didn't get it. Please check."
        )
        is True
    )


def test_wrong_transfer_ignores_bare_wrong() -> None:
    """SAMPLE-06: 'Something is wrong with my money. Please check.' —
    the bare word 'wrong' with no transfer context is NOT a
    wrong-transfer claim.
    """
    assert wrong_transfer_signals("Something is wrong with my money. Please check.") is False


# ---------------------------------------------------------------------------
# Unit tests — refund
# ---------------------------------------------------------------------------


def test_refund_signal_detects_keyword() -> None:
    assert refund_signals("Please refund my 500 taka") is True


def test_refund_signal_detects_bangla() -> None:
    assert refund_signals("টাকা ফেরত দিন") is True


def test_contested_refund_detects_non_receipt() -> None:
    assert is_contested_refund("I never received the product, I want my money back") is True


# ---------------------------------------------------------------------------
# Unit tests — top-level classifier
# ---------------------------------------------------------------------------


def test_classify_phishing_case() -> None:
    """A phishing complaint should yield critical + fraud_risk + review."""
    from app.models import AnalyzeRequest
    from app.evidence import match

    req = AnalyzeRequest(
        ticket_id="T-5",
        complaint="Someone called asking for my OTP and said my account will be blocked",
        user_type="customer",
        transaction_history=[],
    )
    ev = match(req)
    cls = classify(req, ev)
    assert cls.case_type == "phishing_or_social_engineering"
    assert cls.severity == "critical"
    assert cls.human_review_required is True
    assert cls.rule_id == "phishing_rule"


def test_classify_returns_classification_dataclass() -> None:
    from app.models import AnalyzeRequest
    from app.evidence import match

    req = AnalyzeRequest(
        ticket_id="T-1",
        complaint="I sent 1000 to a wrong number",
        user_type="customer",
        transaction_history=[],
    )
    ev = match(req)
    cls = classify(req, ev)
    assert isinstance(cls, Classification)
    assert cls.case_type in {
        "wrong_transfer",
        "payment_failed",
        "refund_request",
        "duplicate_payment",
        "merchant_settlement_delay",
        "agent_cash_in_issue",
        "phishing_or_social_engineering",
        "other",
    }


# ---------------------------------------------------------------------------
# Unit tests — department router
# ---------------------------------------------------------------------------


def test_department_table_has_eight_entries() -> None:
    assert len(DEPARTMENT_TABLE) == 8


def test_department_table_keys_are_case_types() -> None:
    expected = {
        "wrong_transfer",
        "payment_failed",
        "refund_request",
        "duplicate_payment",
        "merchant_settlement_delay",
        "agent_cash_in_issue",
        "phishing_or_social_engineering",
        "other",
    }
    assert set(DEPARTMENT_TABLE.keys()) == expected


def test_department_phishing_routes_to_fraud_risk() -> None:
    assert department("phishing_or_social_engineering", "critical") == "fraud_risk"


def test_department_wrong_transfer_routes_to_dispute() -> None:
    assert department("wrong_transfer", "high") == "dispute_resolution"
    assert department("wrong_transfer", "medium") == "dispute_resolution"


def test_department_unknown_case_type_falls_back_to_support() -> None:
    assert department("nonexistent_type", "low") == "customer_support"


def test_department_refund_customer_low_routes_to_support() -> None:
    assert department("refund_request", "low", "customer") == "customer_support"


def test_department_refund_contested_routes_to_dispute() -> None:
    assert department("refund_request", "medium") == "dispute_resolution"
