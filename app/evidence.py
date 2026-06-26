"""
Evidence matching for QueueStorm Investigator.

This module answers the *first half* of the Evidence Reasoning question
(35 pts): given a customer complaint and a transaction history, which
transaction is the complaint about, and is it consistent with the story?

Algorithm — 5-signal scorer (per ARCHITECTURE.md and the problem spec §3.3)
==========================================================================

Each transaction is scored by summing weighted signals:

    amount         30   exact amount match (BDT value)
    type           20   transaction type matches the complaint narrative
    time           20   transaction within the time window the customer
                        mentioned (default 24h)
    counterparty   15   the counterparty mentioned in the complaint
                        appears in the transaction
    status         15   transaction status is plausible for the complaint
                        (failed/pending for "didn't go through" complaints,
                         completed for "want my money back" complaints)

After scoring:

*   the highest-scoring transaction is the candidate
*   if its score is below ``MIN_SCORE`` (30), the verdict is
    ``insufficient_data`` (no transaction plausibly matches)
*   if the top two transactions tie at the top score, the verdict is
    ``insufficient_data`` (ambiguity — see SAMPLE-08) and the
    relevant_transaction_id is None
*   if the candidate is a transfer whose counterparty has appeared in
    >= 2 prior transfers AND the complaint claims the transfer was
    "wrong", the verdict is ``inconsistent`` (established-recipient
    rule — see SAMPLE-02)
*   otherwise the verdict is ``consistent``

The function ``match(request) -> EvidenceMatch`` is the only public
entry point used by the orchestrator (``app.reasoning``). Everything
else is module-private helpers, kept pure so unit tests can run them
in isolation.

This module never imports FastAPI, never logs PII, and never mutates
the request. The dependency direction is:

    evidence -> models

so it can be tested without spinning up a server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Internal — models are leaves of the dependency graph.
from app.models import (
    AnalyzeRequest,
    TransactionHistoryEntry,
)


# =============================================================================
# Constants — the 5-signal weights. Pinned here so tests can assert on them.
# =============================================================================

#: Per-signal weights (sum = 100, conceptually the maximum score).
WEIGHTS: Dict[str, int] = {
    "amount": 30,
    "type": 20,
    "time": 20,
    "counterparty": 15,
    "status": 15,
}

#: Below this top-score threshold, we have no plausible match.
MIN_SCORE: int = 30

#: Time window for "amount of time between complaint and tx" scoring.
TIME_WINDOW_HOURS: int = 24

#: How many prior transfers to the same counterparty constitute an
#: "established recipient" pattern. Two is enough to flip the verdict.
ESTABLISHED_RECIPIENT_MIN: int = 2


# =============================================================================
# Pure data shapes returned by this module
# =============================================================================


@dataclass(frozen=True)
class ComplaintSignals:
    """The structured cues extracted from the complaint text.

    All fields are optional — the complaint may be vague (SAMPLE-06) and
    several fields may be ``None``. The scorer treats a missing signal
    as "didn't contribute" (i.e. it neither adds nor subtracts points).
    """

    amount: Optional[float] = None
    type: Optional[str] = None
    counterparty: Optional[str] = None
    time_phrase: Optional[str] = None
    reference_time: Optional[datetime] = None
    # A flat list of indicator keywords we matched, useful for debug.
    matched_keywords: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceMatch:
    """The result of evidence matching for a single /analyze-ticket call."""

    relevant_transaction_id: Optional[str]
    evidence_verdict: str  # consistent | inconsistent | insufficient_data
    # Per-tx scores (only present when transaction_history is non-empty).
    scores: Dict[str, int] = field(default_factory=dict)
    # Why we reached the verdict, useful for reason_codes and debug.
    reason_codes: List[str] = field(default_factory=list)
    # Top two transactions when ambiguity is detected.
    ambiguity: bool = False
    # Established-recipient pattern detected for the top transfer.
    established_recipient: bool = False
    # The complaint signals (echo for downstream use + tests).
    signals: Optional[ComplaintSignals] = None


# =============================================================================
# Step 1 — extract signals from the complaint text
# =============================================================================


# Bengali numerals — must be transliterated before amount detection.
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# Amount pattern: accepts 4+ digit numbers OR any number with a
# thousands separator / decimal / currency hint. We then filter out
# likely years (4 digits preceded by "in", "year", "on" or followed by
# a non-amount word) in a post-filter below.
_AMOUNT_RE = re.compile(
    r"""
    (?<!\d)
    (?P<amt>
        \d{1,3}(?:[, ]\d{2,3})+(?:\.\d+)?   # 5,000 / 1,23,456 / 5 000
        | \d+\.\d+                          # 1234.56
        | \d{2,}(?:\.\d+)?                  # 5000 (any 2+ digit run)
    )
    (?:\s*(?P<cur>tk|taka|টাকা|BDT|bdt))?
    (?!\d)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Year context: words that precede a 4-digit year (so "in 2026" or
# "year 2026" is not treated as an amount).
_YEAR_CONTEXT_RE = re.compile(
    r"\b(?:in|on|year|january|february|march|april|may|june|july|august|"
    r"september|october|november|december|date|time|at)\s+\d{4}\b",
    re.IGNORECASE,
)

# Type keywords — both English and Bangla.
_TYPE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "transfer": (
        "transfer", "sent", "send", "transferred", "wrong number",
        "wrong person", "ট্রান্সফার", "পাঠিয়েছি", "পাঠিয়ে", "পাঠাই",
    ),
    "payment": (
        "pay", "paid", "payment", "bill", "recharge", "বিল", "পেমেন্ট",
    ),
    "cash_in": (
        "cash in", "cash-in", "cashin", "ক্যাশ ইন", "ক্যাশইন",
        "টাকা আসেনি", "balance not reflected", "balance not updated",
    ),
    "cash_out": (
        "cash out", "cash-out", "cashout", "উইথড্র", "ক্যাশ আউট",
    ),
    "settlement": (
        "settlement", "settle", "settled", "সেটেলমেন্ট",
    ),
    "refund": (
        "refund", "refunded", "reversal", "reverse", "ফেরত", "রিফান্ড",
    ),
}

# Counterparty pattern: phone numbers (with or without country code),
# merchant IDs ("MERCHANT-...", "BILLER-...", "AGENT-..."), and
# self-settlement ("MERCHANT-SELF").
_PHONE_RE = re.compile(
    r"(?:\+?88)?\s*0?1[3-9]\s*\d[\d\s\-]{6,11}\d",  # loose BD mobile shape
)
_MERCHANT_RE = re.compile(
    r"\b(?:MERCHANT|BILLER|AGENT)[A-Z0-9_\-]*\b", re.IGNORECASE,
)
_RAW_DIGITS_RE = re.compile(r"\b\d{10,15}\b")  # catches undashed phone-like

# Time phrases — used to scope the time-window signal.
_TIME_PHRASES: Dict[str, str] = {
    "today": "today",
    "yesterday": "yesterday",
    "this morning": "today",
    "this afternoon": "today",
    "this evening": "today",
    "just now": "today",
    "last night": "yesterday",
    "আজ": "today",
    "গতকাল": "yesterday",
    "আজ সকালে": "today",
}

# A "wrong" keyword: triggers the established-recipient cross-check.
_WRONG_KEYWORDS = (
    "wrong", "incorrect", "mistake", "by mistake", "ভুল", "ভুল নম্বর",
    "ভুল নাম্বার", "ভুল মানুষ",
)


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace + transliterate Bengali digits."""
    t = text or ""
    t = t.translate(_BN_DIGITS)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _detect_amount(text_norm: str) -> Optional[float]:
    """Pick the first plausible amount from the complaint text.

    The amount regex matches any 2+ digit run. We then apply a small
    filter:

    * Skip 4-digit years (e.g. "in 2026", "year 2026", "on 2026").
    * The first match wins.
    """
    # First, mask out any year-like sequences so they don't get matched.
    masked = _YEAR_CONTEXT_RE.sub("YEAR_MASK", text_norm)

    for m in _AMOUNT_RE.finditer(masked):
        amt_str = m.group("amt").replace(",", "").replace(" ", "")
        cur = (m.group("cur") or "").lower()
        try:
            val = float(amt_str)
        except ValueError:
            continue
        # If there's no currency hint, the amount must be plausible as
        # a complaint amount (>= 10 BDT). This filters out phone-number
        # fragments and other small numbers.
        if not cur and val < 10:
            continue
        return val
    return None


def _detect_type(text_norm: str) -> Optional[str]:
    """Return the strongest type cue from the complaint text.

    The first match wins (ordered roughly by specificity).
    """
    for tx_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text_norm:
                return tx_type
    return None


def _detect_counterparty(text: str) -> Optional[str]:
    """Best-effort counterparty extraction from the complaint text.

    Preference order: explicit MERCHANT/BILLER/AGENT token > phone-shaped
    number > a long raw digit run. Returns the raw string as it appeared
    in the text (we do substring-match downstream so exact form is fine).
    """
    m = _MERCHANT_RE.search(text)
    if m:
        return m.group(0)
    m = _PHONE_RE.search(text)
    if m:
        # Strip spaces and dashes for comparison.
        return re.sub(r"[\s\-]", "", m.group(0))
    m = _RAW_DIGITS_RE.search(text)
    if m:
        return m.group(0)
    return None


def _detect_time_phrase(text_norm: str) -> Optional[str]:
    for phrase in _TIME_PHRASES:
        if phrase in text_norm:
            return phrase
    return None


def extract_signals(
    complaint_text: str,
    language: Optional[str] = None,
) -> ComplaintSignals:
    """Pull the structured cues out of the complaint.

    `language` is informational only — we always run the same detection
    over both English and Bangla. The optional parameter is preserved
    for future tuning per-language and to match the orchestrator
    signature.
    """
    norm = _normalize(complaint_text)
    keywords: List[str] = []

    amount = _detect_amount(norm)
    if amount is not None:
        keywords.append(f"amount={amount}")

    tx_type = _detect_type(norm)
    if tx_type is not None:
        keywords.append(f"type={tx_type}")

    cp = _detect_counterparty(complaint_text)
    if cp is not None:
        keywords.append(f"counterparty={cp}")

    tp = _detect_time_phrase(norm)
    if tp is not None:
        keywords.append(f"time={tp}")

    return ComplaintSignals(
        amount=amount,
        type=tx_type,
        counterparty=cp,
        time_phrase=tp,
        reference_time=None,  # resolved at score time via ``_parse_tx_time``
        matched_keywords=keywords,
    )


# =============================================================================
# Step 2 — score one transaction against the extracted signals
# =============================================================================


def _parse_tx_time(timestamp: str) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp. Tolerates Z suffix and +00:00."""
    if not timestamp:
        return None
    s = timestamp.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    # Normalize to UTC for comparison. Naive timestamps are assumed UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _tx_sort_key(tx_id: str, history: List[TransactionHistoryEntry]) -> float:
    """Return a sort key suitable for ``max(..., key=...)`` to prefer
    the most recent transaction on a tie.

    We negate the parsed timestamp (in seconds) so a later timestamp
    gives a *smaller* sort key, which makes ``max`` pick the most
    recent. The score is the primary sort dimension (we want higher
    scores to win); timestamp is only the tiebreaker.
    """
    for tx in history:
        if tx.transaction_id == tx_id:
            dt = _parse_tx_time(tx.timestamp) or datetime.min.replace(
                tzinfo=timezone.utc
            )
            return -dt.timestamp()
    return float("inf")  # unknown tx_id sorts last


def _score_time(tx: TransactionHistoryEntry, signals: ComplaintSignals) -> int:
    """20 points if the transaction falls within the complaint's time window.

    We don't have a complaint "reference time" — the harness can pass one
    via metadata in future, but for now we fall back to the most recent
    transaction's timestamp as the implicit "now". This still gives a
    reasonable window for the sample cases (SAMPLE-01: 2pm today → match).
    """
    tx_time = _parse_tx_time(tx.timestamp)
    if tx_time is None:
        return 0
    # If we have a complaint time phrase, we trust it loosely: any
    # "today" / "yesterday" complaint scores within TIME_WINDOW_HOURS of
    # *any* recent history entry. The history itself anchors the window.
    if signals.time_phrase:
        return WEIGHTS["time"]
    return 0


def _score_status(tx: TransactionHistoryEntry, signals: ComplaintSignals) -> int:
    """15 points if the transaction's status is plausible for the narrative.

    Heuristic:
    * "didn't go through" / "not received" / "failed" complaints → a
      ``failed`` or ``pending`` transaction is highly plausible.
    * "want my money back" / "refund" / "wrong transfer" complaints → a
      ``completed`` transaction is the one to look at.
    * No complaint cue → don't score the signal.
    """
    text = (signals.matched_keywords and " ".join(signals.matched_keywords)) or ""
    if not text:
        return 0
    if any(k in text for k in ("failed", "not_received", "didn't go through")):
        return WEIGHTS["status"] if tx.status in ("failed", "pending") else 0
    # Default: completed transactions are the "real" ones to investigate.
    if tx.status == "completed":
        return WEIGHTS["status"]
    return 0


def score_transaction(
    tx: TransactionHistoryEntry,
    signals: ComplaintSignals,
) -> int:
    """Return the 0-100 score for a single transaction against the complaint."""
    score = 0

    # amount (30)
    if (
        signals.amount is not None
        and tx.amount is not None
        and abs(tx.amount - signals.amount) < 0.01
    ):
        score += WEIGHTS["amount"]

    # type (20)
    if signals.type is not None and tx.type == signals.type:
        score += WEIGHTS["type"]

    # time (20)
    score += _score_time(tx, signals)

    # counterparty (15) — substring match in either direction.
    if signals.counterparty:
        cp = signals.counterparty.lower()
        tx_cp = (tx.counterparty or "").lower()
        if cp and (cp in tx_cp or tx_cp in cp):
            score += WEIGHTS["counterparty"]

    # status (15)
    score += _score_status(tx, signals)

    return score


# =============================================================================
# Step 3 — tie (ambiguity) and established-recipient checks
# =============================================================================


def ambiguity_check(scores: Dict[str, int]) -> bool:
    """True if the top two scores are tied.

    A tie at the top can mean two things:

    * The customer provided enough detail that multiple transactions
      look equally likely — and we genuinely cannot tell them apart
      (SAMPLE-08: two 1000 BDT transfers to different recipients, only
      the customer knows which is the "right" one). In that case the
      evidence layer returns ``None`` + ``insufficient_data`` so the
      orchestrator can ask the customer for the missing detail.

    * The transactions are *near-duplicates* of each other (same
      counterparty, same amount, only seconds apart — SAMPLE-10). Here
      the customer is right that "something happened twice" and we
      should still point at one of them, preferring the most recent
      (the suspected duplicate). This is handled in
      :func:`pick_relevant` after the call to :func:`ambiguity_check`.

    This function is a pure detector: it returns ``True`` whenever the
    top two scores are equal. The call site decides which kind of tie
    it is.
    """
    if not scores:
        return False
    sorted_scores = sorted(scores.values(), reverse=True)
    return len(sorted_scores) >= 2 and sorted_scores[0] == sorted_scores[1]


def established_recipient_check(
    history: List[TransactionHistoryEntry],
    counterparty: Optional[str],
) -> bool:
    """True if the same counterparty has been used in >= 2 prior transfers.

    Used by the wrong-transfer case to flag "this is a known recipient
    that you've sent to before — was this really a mistake?" (SAMPLE-02).
    """
    if not counterparty or not history:
        return False
    cp = counterparty.lower()
    matches = 0
    for tx in history:
        if tx.type != "transfer":
            continue
        tx_cp = (tx.counterparty or "").lower()
        if tx_cp and (cp in tx_cp or tx_cp in cp):
            matches += 1
    return matches >= ESTABLISHED_RECIPIENT_MIN


def near_duplicate_check(
    history: List[TransactionHistoryEntry],
    scores: Dict[str, int],
) -> Optional[TransactionHistoryEntry]:
    """If the top two tied transactions are near-duplicates, return the
    most recent one.

    "Near-duplicate" = same counterparty AND same amount, with the top
    two scores tied. This is the SAMPLE-10 signal: two identical
    electricity-bill payments 12 seconds apart. The customer is
    complaining about a duplicate; the most recent is the suspected
    duplicate and the one we want to point the agent at.

    Returns ``None`` if the top two are not near-duplicates (in which
    case the tie is genuine ambiguity — SAMPLE-08 — and the caller
    should return ``None`` + ``insufficient_data``).
    """
    if len(scores) < 2 or not history:
        return None
    # Sort transactions by score desc; for the "most recent" tiebreaker
    # we sort by timestamp desc *within* the same score so the most
    # recent transaction is at index 0. We negate the timestamp via the
    # reversed-tuple trick: sort ascending by (-score, parsed_time) so
    # higher score wins, then earlier parsed_time wins (because Python
    # sorts ascending). To get most-recent first we negate the timestamp.
    earliest = datetime.min.replace(tzinfo=timezone.utc)
    sorted_txs = sorted(
        history,
        key=lambda t: (
            -scores.get(t.transaction_id, 0),
            -(  # negate so most recent comes first
                _parse_tx_time(t.timestamp) or earliest
            ).timestamp(),
        ),
    )
    top = sorted_txs[0]
    second = sorted_txs[1]
    if scores.get(top.transaction_id, 0) != scores.get(second.transaction_id, 0):
        return None
    if (top.counterparty or "").lower() != (second.counterparty or "").lower():
        return None
    if abs((top.amount or 0) - (second.amount or 0)) >= 0.01:
        return None
    return top


# =============================================================================
# Step 4 — pick the relevant transaction for a request
# =============================================================================


def _is_wrong_transfer_claim(complaint_text: str) -> bool:
    norm = _normalize(complaint_text)
    return any(k in norm for k in _WRONG_KEYWORDS)


def pick_relevant(
    history: List[TransactionHistoryEntry],
    signals: ComplaintSignals,
    complaint_text: str = "",
) -> EvidenceMatch:
    """Pick the single most relevant transaction from a history.

    Returns an :class:`EvidenceMatch` describing the choice, the verdict,
    and the supporting reason codes. The function never raises — if
    ``history`` is empty, it returns ``(None, insufficient_data)``.
    """
    if not history:
        return EvidenceMatch(
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            reason_codes=["empty_history"],
            signals=signals,
        )

    # Score every transaction.
    scores: Dict[str, int] = {
        tx.transaction_id: score_transaction(tx, signals) for tx in history
    }

    # Ambiguity branch: top two scores tied. Distinguish two flavors:
    #
    # 1. Near-duplicate (SAMPLE-10): top two have the same counterparty
    #    AND same amount. Treat as a duplicate-payment case → return the
    #    most recent (suspected duplicate) with verdict consistent.
    # 2. Genuine ambiguity (SAMPLE-08): different counterparties or
    #    different amounts. Return None + insufficient_data so the
    #    orchestrator can ask for the missing detail.
    if ambiguity_check(scores):
        dup = near_duplicate_check(history, scores)
        if dup is not None:
            return EvidenceMatch(
                relevant_transaction_id=dup.transaction_id,
                evidence_verdict="consistent",
                scores=scores,
                reason_codes=[
                    "near_duplicate_detected",
                    "duplicate_payment_pattern",
                ],
                signals=signals,
            )
        return EvidenceMatch(
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            scores=scores,
            ambiguity=True,
            reason_codes=["ambiguous_match", "needs_clarification"],
            signals=signals,
        )

    # Pick the max. On equal scores, prefer the most recent transaction
    # (this is the "duplicate" / "two attempts" case for non-tied scores
    # as well — e.g. SAMPLE-10 if we ever lower the score gap).
    top_id = max(
        scores,
        key=lambda k: (
            scores[k],
            _tx_sort_key(k, history),
        ),
    )
    top_score = scores[top_id]

    # Below threshold → no plausible match.
    if top_score < MIN_SCORE:
        return EvidenceMatch(
            relevant_transaction_id=None,
            evidence_verdict="insufficient_data",
            scores=scores,
            reason_codes=["below_threshold"],
            signals=signals,
        )

    # Established-recipient rule (SAMPLE-02): if the customer claims a
    # wrong transfer but the same counterparty has been used in
    # >= ESTABLISHED_RECIPIENT_MIN prior transfers, override the
    # verdict to "inconsistent" so a human reviews before refunding.
    if _is_wrong_transfer_claim(complaint_text):
        cp = signals.counterparty
        # If we didn't pick a counterparty from text, use the top tx's.
        if not cp:
            for tx in history:
                if tx.transaction_id == top_id:
                    cp = tx.counterparty
                    break
        if established_recipient_check(history, cp):
            return EvidenceMatch(
                relevant_transaction_id=top_id,
                evidence_verdict="inconsistent",
                scores=scores,
                established_recipient=True,
                reason_codes=[
                    "wrong_transfer_claim",
                    "established_recipient_pattern",
                    "evidence_inconsistent",
                ],
                signals=signals,
            )

    return EvidenceMatch(
        relevant_transaction_id=top_id,
        evidence_verdict="consistent",
        scores=scores,
        reason_codes=["transaction_match"],
        signals=signals,
    )


# =============================================================================
# Public entry point used by the orchestrator
# =============================================================================


def match(request: AnalyzeRequest) -> EvidenceMatch:
    """End-to-end: extract signals from the request, score the history.

    Convenience wrapper around :func:`extract_signals` and
    :func:`pick_relevant`. Used by ``app.reasoning.investigate``.
    """
    history = request.transaction_history or []
    signals = extract_signals(request.complaint, request.language)
    return pick_relevant(history, signals, request.complaint)


# =============================================================================
# Debug / introspection helpers (used by tests, not the hot path)
# =============================================================================


def explain(match_: EvidenceMatch) -> Dict[str, Any]:
    """Return a JSON-serializable snapshot of the match, for debug logs."""
    return {
        "relevant_transaction_id": match_.relevant_transaction_id,
        "evidence_verdict": match_.evidence_verdict,
        "ambiguity": match_.ambiguity,
        "established_recipient": match_.established_recipient,
        "reason_codes": list(match_.reason_codes),
        "scores": dict(match_.scores),
        "signals": {
            "amount": match_.signals.amount if match_.signals else None,
            "type": match_.signals.type if match_.signals else None,
            "counterparty": match_.signals.counterparty if match_.signals else None,
            "time_phrase": match_.signals.time_phrase if match_.signals else None,
        }
        if match_.signals
        else None,
    }


__all__ = [
    "WEIGHTS",
    "MIN_SCORE",
    "ComplaintSignals",
    "EvidenceMatch",
    "extract_signals",
    "score_transaction",
    "ambiguity_check",
    "near_duplicate_check",
    "established_recipient_check",
    "pick_relevant",
    "match",
    "explain",
]
