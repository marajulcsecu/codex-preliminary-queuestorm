"""
Pytest coverage for the evidence layer (Phase 2).

These tests exercise ``app.evidence.match`` against the 10 worked
sample cases in ``Question Provided/SUST_Preli_Sample_Cases.json`` and
a handful of focused unit tests for the internal helpers. The goal is
to lock in the (relevant_transaction_id, evidence_verdict) decision
matrix so regressions surface immediately.

Run with:

    pytest tests/test_evidence.py -v

The module has no FastAPI dependency and no I/O, so no live server is
required. The classifier + i18n layers (Phase 3+) will layer on top of
this; the cases here intentionally only assert on evidence-layer
fields.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from app.evidence import (
    ComplaintSignals,
    EvidenceMatch,
    WEIGHTS,
    MIN_SCORE,
    ambiguity_check,
    established_recipient_check,
    extract_signals,
    near_duplicate_check,
    pick_relevant,
    score_transaction,
)
from app.models import AnalyzeRequest, TransactionHistoryEntry


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
# Per-sample-case evidence assertions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    SAMPLE_CASES,
    ids=[c["id"] for c in SAMPLE_CASES],
)
def test_evidence_matches_sample_case(case: Dict[str, Any]) -> None:
    """The evidence layer must pick the expected tx + verdict for every
    worked sample case from the problem pack.
    """
    req = AnalyzeRequest(**case["input"])
    result = pick_relevant(
        req.transaction_history or [],
        extract_signals(req.complaint, req.language),
        req.complaint,
    )

    expected = case["expected_output"]
    assert result.relevant_transaction_id == expected["relevant_transaction_id"], (
        f"{case['id']}: expected relevant_transaction_id="
        f"{expected['relevant_transaction_id']!r}, got {result.relevant_transaction_id!r}"
    )
    assert result.evidence_verdict == expected["evidence_verdict"], (
        f"{case['id']}: expected evidence_verdict="
        f"{expected['evidence_verdict']!r}, got {result.evidence_verdict!r}"
    )


# ---------------------------------------------------------------------------
# Unit tests: signal extraction
# ---------------------------------------------------------------------------


def test_extract_signals_finds_amount_in_english() -> None:
    sig = extract_signals("I sent 5000 taka to the wrong person", None)
    assert sig.amount == 5000.0
    assert sig.type == "transfer"
    assert "amount=5000.0" in sig.matched_keywords


def test_extract_signals_finds_amount_with_comma_separator() -> None:
    sig = extract_signals("Please help me get back 5,000 taka", None)
    assert sig.amount == 5000.0


def test_extract_signals_finds_amount_in_bangla_digits() -> None:
    sig = extract_signals("আমি ২০০০ টাকা পাঠিয়েছি", None)
    assert sig.amount == 2000.0


def test_extract_signals_does_not_match_year_or_phone_as_amount() -> None:
    """A bare 4-digit year without any currency hint or amount-shaped
    phrasing should not be treated as a money amount. (The scorer is
    intentionally tolerant of standalone amounts >= 4 digits, but it
    prefers currency hints.)
    """
    sig = extract_signals("in the year 2026 something happened", None)
    # 2026 has no currency hint; the regex requires a "tk/taka/টাকা/BDT"
    # suffix or a thousands separator. So amount is None.
    assert sig.amount is None


def test_extract_signals_detects_payment_type() -> None:
    sig = extract_signals("I paid my bill but it deducted twice", None)
    assert sig.type == "payment"


def test_extract_signals_detects_cash_in_bangla() -> None:
    sig = extract_signals(
        "আমি ২০০০ টাকা ক্যাশ ইন করেছি কিন্তু ব্যালেন্সে আসেনি", None
    )
    assert sig.type == "cash_in"
    assert sig.amount == 2000.0


def test_extract_signals_detects_counterparty_merchant_id() -> None:
    sig = extract_signals("Payment to MERCHANT-DESCO failed", None)
    assert sig.counterparty is not None
    assert "MERCHANT" in sig.counterparty.upper()


def test_extract_signals_detects_counterparty_phone() -> None:
    sig = extract_signals("I sent 1000 to +8801712345678 by mistake", None)
    assert sig.counterparty is not None
    assert "1712345678" in sig.counterparty


def test_extract_signals_finds_today_phrase() -> None:
    sig = extract_signals("I sent money today and the receiver didn't get it", None)
    assert sig.time_phrase is not None
    assert "today" in sig.time_phrase


def test_extract_signals_finds_bangla_today_phrase() -> None:
    sig = extract_signals("আজ সকালে ২০০০ টাকা ক্যাশ ইন করেছি", None)
    assert sig.time_phrase is not None


# ---------------------------------------------------------------------------
# Unit tests: scoring
# ---------------------------------------------------------------------------


def _tx(**kw: Any) -> TransactionHistoryEntry:
    defaults: Dict[str, Any] = dict(
        transaction_id="TX-T",
        timestamp="2026-04-14T12:00:00Z",
        type="transfer",
        amount=1000.0,
        counterparty="+8801712345678",
        status="completed",
    )
    defaults.update(kw)
    return TransactionHistoryEntry(**defaults)


def test_score_full_match() -> None:
    """All 5 signals hit: amount + type + time + counterparty + status
    should sum to the maximum (100).

    The default status signal awards a completed transaction when any
    complaint cue is present, so all 5 signals fire:
    30 + 20 + 20 + 15 + 15 = 100.
    """
    tx = _tx()
    sig = ComplaintSignals(
        amount=1000.0,
        type="transfer",
        counterparty="+8801712345678",
        time_phrase="today",
        matched_keywords=["type=transfer"],
    )
    assert score_transaction(tx, sig) == 100


def test_score_amount_match_only() -> None:
    """Amount match with no other signals: 30 (amount) + 15 (default
    status for completed tx) = 45.
    """
    tx = _tx(type="payment", counterparty="OTHER")
    sig = ComplaintSignals(amount=1000.0, matched_keywords=["amount=1000.0"])
    assert score_transaction(tx, sig) == WEIGHTS["amount"] + WEIGHTS["status"]


def test_score_type_match_adds_weight() -> None:
    """Amount + type + status: 30 + 20 + 15 = 65."""
    tx = _tx(type="payment", counterparty="OTHER")
    sig = ComplaintSignals(
        amount=1000.0, type="payment", matched_keywords=["type=payment"]
    )
    assert score_transaction(tx, sig) == WEIGHTS["amount"] + WEIGHTS["type"] + WEIGHTS["status"]


def test_score_counterparty_substring_match() -> None:
    """Counterparty in complaint is a substring of the tx counterparty
    (or vice versa) — we still award the signal.
    """
    tx = _tx(counterparty="+8801712345678")
    sig = ComplaintSignals(counterparty="01712345678")
    assert score_transaction(tx, sig) >= WEIGHTS["counterparty"]


def test_score_status_failed_preferred_for_didnt_go_through() -> None:
    """When the complaint signals include a "didn't go through" hint,
    a failed/pending transaction is more plausible than a completed one.
    """
    sig = ComplaintSignals(matched_keywords=["type=payment", "didn't go through"])
    # Failed tx gets the status weight; completed tx does not.
    assert score_transaction(_tx(status="failed"), sig) >= WEIGHTS["status"]
    assert score_transaction(_tx(status="completed"), sig) == 0


# ---------------------------------------------------------------------------
# Unit tests: ambiguity + near-duplicate + established-recipient
# ---------------------------------------------------------------------------


def test_ambiguity_check_detects_top_two_tie() -> None:
    assert ambiguity_check({"a": 50, "b": 50, "c": 30}) is True


def test_ambiguity_check_passes_unique_top() -> None:
    assert ambiguity_check({"a": 60, "b": 50, "c": 30}) is False


def test_ambiguity_check_handles_empty() -> None:
    assert ambiguity_check({}) is False
    assert ambiguity_check({"a": 50}) is False


def test_near_duplicate_check_returns_most_recent() -> None:
    history = [
        _tx(transaction_id="TX-A", timestamp="2026-04-14T08:15:30Z"),
        _tx(transaction_id="TX-B", timestamp="2026-04-14T08:15:42Z"),
    ]
    scores = {"TX-A": 65, "TX-B": 65}
    pick = near_duplicate_check(history, scores)
    assert pick is not None
    assert pick.transaction_id == "TX-B"  # more recent


def test_near_duplicate_check_rejects_different_counterparty() -> None:
    history = [
        _tx(transaction_id="TX-A", counterparty="+8801711111111"),
        _tx(transaction_id="TX-B", counterparty="+8801722222222"),
    ]
    assert near_duplicate_check(history, {"TX-A": 65, "TX-B": 65}) is None


def test_near_duplicate_check_rejects_different_amount() -> None:
    history = [
        _tx(transaction_id="TX-A", amount=850.0),
        _tx(transaction_id="TX-B", amount=900.0),
    ]
    assert near_duplicate_check(history, {"TX-A": 65, "TX-B": 65}) is None


def test_established_recipient_check_requires_two_transfers() -> None:
    history = [
        _tx(transaction_id="TX-A"),
        _tx(transaction_id="TX-B"),
    ]
    assert established_recipient_check(history, "+8801712345678") is True


def test_established_recipient_check_ignores_non_transfer_type() -> None:
    history = [
        _tx(transaction_id="TX-A", type="payment"),
        _tx(transaction_id="TX-B", type="payment"),
    ]
    assert established_recipient_check(history, "+8801712345678") is False


def test_established_recipient_check_handles_no_history() -> None:
    assert established_recipient_check([], "+8801712345678") is False
    assert established_recipient_check(
        [_tx()], None
    ) is False


# ---------------------------------------------------------------------------
# Top-level orchestrator: match() returns a fully populated EvidenceMatch
# ---------------------------------------------------------------------------


def test_match_returns_evidence_match_dataclass() -> None:
    req = AnalyzeRequest(
        ticket_id="T-1",
        complaint="I sent 1000 taka to the wrong number",
        transaction_history=[
            _tx(transaction_id="TX-X", amount=1000.0, type="transfer"),
        ],
    )
    result = pick_relevant(
        req.transaction_history or [],
        extract_signals(req.complaint),
        req.complaint,
    )
    assert isinstance(result, EvidenceMatch)
    assert result.relevant_transaction_id == "TX-X"
    assert result.evidence_verdict in {"consistent", "inconsistent", "insufficient_data"}


def test_match_with_empty_history_returns_insufficient_data() -> None:
    req = AnalyzeRequest(
        ticket_id="T-2",
        complaint="Something is wrong with my account",
        transaction_history=[],
    )
    result = pick_relevant(
        [],
        extract_signals(req.complaint),
        req.complaint,
    )
    assert result.relevant_transaction_id is None
    assert result.evidence_verdict == "insufficient_data"


def test_match_with_vague_complaint_returns_insufficient_data() -> None:
    """A complaint with no amount, no time, and no counterparty
    (SAMPLE-06) should fall through to insufficient_data without
    picking a transaction.
    """
    req = AnalyzeRequest(
        ticket_id="T-3",
        complaint="Something is wrong with my money. Please check.",
        transaction_history=[
            _tx(transaction_id="TX-A", amount=3000, type="cash_in", counterparty="AGENT-220"),
            _tx(transaction_id="TX-B", amount=800, type="transfer", counterparty="+8801911223344"),
        ],
    )
    result = pick_relevant(
        req.transaction_history or [],
        extract_signals(req.complaint),
        req.complaint,
    )
    # Vague complaint: signals are weak; we expect either None or the
    # top transaction by status. Per SAMPLE-06 the expected is None.
    # Since status is awarded for "completed" transactions by default,
    # both txs tie at status=15. The top is the first one. But the
    # threshold MIN_SCORE (30) prevents either from being selected
    # because no amount/type match is found.
    assert result.evidence_verdict == "insufficient_data"


# ---------------------------------------------------------------------------
# Constants sanity check
# ---------------------------------------------------------------------------


def test_weights_sum_to_one_hundred() -> None:
    """The 5-signal weights should sum to 100 (conceptual maximum)."""
    assert sum(WEIGHTS.values()) == 100


def test_min_score_is_strictly_positive() -> None:
    assert MIN_SCORE > 0
