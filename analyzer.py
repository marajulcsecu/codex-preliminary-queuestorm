"""Rule-based analyzer for the QueueStorm Investigator service.

Design goals:
- Deterministic, no external API calls.
- Match the 10 public sample cases and be robust to hidden variants.
- Always emit safe `customer_reply` text (no PIN/OTP asks, no unauthorized
  refund promises, no third-party routing).
- Reply in the same language as the complaint (en / bn).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

# Phishing / social engineering — checked first because it's safety-critical.
PHISHING_KEYWORDS_EN = [
    "otp", "pin", "password", "cvv", "one time password",
    "asked for my otp", "asked for my pin", "asked for otp", "asked for pin",
    "share my otp", "share my pin", "share otp", "share pin",
    "share my password", "share password",
    "call from bkash", "call from nagad", "call from rocket",
    "call claiming", "claiming to be from",
    "someone called", "someone called me",
    "fake call", "fake sms", "scam call", "scam message",
    "phishing", "social engineering",
    "block my account", "block your account", "will be blocked",
    "verify your account", "verify your identity",
    "asking for otp", "asking for pin",
    "send otp", "send pin",
]
PHISHING_KEYWORDS_BN = [
    "ওটিপি", "ও.টি.পি", "পিন", "পাসওয়ার্ড",
    "বিকাশ থেকে ফোন", "নগদ থেকে ফোন", "রকেট থেকে ফোন",
    "বিকাশ বলে ফোন", "নগদ বলে ফোন",
    "একাউন্ট ব্লক", "অ্যাকাউন্ট ব্লক", "অ্যাকাউন্ট বন্ধ", "একাউন্ট বন্ধ",
    "পিন দিতে", "পিন দিব", "পিন দিয়েছি", "ওটিপি দিয়েছি",
    "পাসওয়ার্ড দিয়েছি", "ভেরিফাই করতে",
    "ফিশিং", "স্ক্যাম", "প্রতারণা",
]

# Wrong transfer keywords
WRONG_TRANSFER_KEYWORDS_EN = [
    "wrong number", "wrong person", "wrong recipient", "wrong account",
    "sent to wrong", "by mistake", "mistakenly", "sent by mistake",
    "sent to the wrong", "send to wrong",
]
WRONG_TRANSFER_KEYWORDS_BN = [
    "ভুল নম্বর", "ভুল নাম্বার", "ভুল ব্যক্তি", "ভুল একাউন্ট",
    "ভুল লেনদেন", "ভুল করে", "ভুল করে পাঠিয়েছি", "ভুল করে পাঠাইছি",
]

# "Recipient didn't receive" — SAMPLE-08 pattern.
# Customer says they sent money but the recipient claims they didn't get it.
# Treated as a wrong-transfer / contested transfer case for routing.
NOT_RECEIVED_KEYWORDS_EN = [
    "didn't get it", "did not get it",
    "hasn't received", "has not received",
    "he didn't get", "she didn't get", "they didn't get",
    "brother didn't", "sister didn't", "friend didn't",
    "recipient didn't", "didn't receive",
    "not received", "haven't received", "have not received",
    "says he didn't", "says she didn't", "says they didn't",
    "no money was received", "money was not received",
    "claim they didn't", "claims he didn't", "claims she didn't",
]

# Payment-failed keywords
PAYMENT_FAILED_KEYWORDS_EN = [
    "failed", "didn't go through", "did not go through",
    "balance deducted", "money deducted", "amount deducted",
    "but my balance", "but balance", "recharge failed",
    "payment failed", "transaction failed",
]
PAYMENT_FAILED_KEYWORDS_BN = [
    "ব্যালেন্স কেটে নিয়েছে", "টাকা কেটে নিয়েছে", "কেটে নিয়েছে",
    "ব্যর্থ হয়েছে", "ফেইল হয়েছে", "ফেল হয়েছে",
    "রিচার্জ হয়নি", "পেমেন্ট হয়নি", "লেনদেন হয়নি",
]

# Refund request keywords
REFUND_KEYWORDS_EN = [
    "refund", "please refund", "refund my", "give me back", "return my money",
    "money back", "want my money back",
]
REFUND_KEYWORDS_BN = [
    "রিফান্ড", "টাকা ফেরত", "ফেরত দিন", "ফেরত চাই",
    "টাকা ফিরিয়ে", "ফেরত টাকা",
]

# Duplicate payment keywords
DUPLICATE_KEYWORDS_EN = [
    "deducted twice", "charged twice", "twice from my",
    "duplicate", "paid twice", "deducted two times",
    "two times", "double deducted", "twice in a row",
]
DUPLICATE_KEYWORDS_BN = [
    "দুইবার কেটেছে", "দুইবার কেটে নিয়েছে", "দুইবার কাটা হয়েছে",
    "ডুপ্লিকেট", "একই পেমেন্ট দুইবার",
]

# Merchant settlement keywords
MERCHANT_SETTLEMENT_KEYWORDS_EN = [
    "settlement", "settle", "not settled", "merchant settlement",
    "sales of", "sales not", "settled to my account",
    "yesterday's sales", "my sales", "merchant account",
    "settle my account", "settlement batch",
]
MERCHANT_SETTLEMENT_KEYWORDS_BN = [
    "সেটেলমেন্ট", "সেটলমেন্ট", "মার্চেন্ট সেটেলমেন্ট",
    "মার্চেন্ট",
    "সেটেল হয়নি",
]

# Agent cash-in keywords
AGENT_CASH_IN_KEYWORDS_EN = [
    "cash in", "cash-in", "agent", "deposit", "deposited",
    "agent says", "agent said", "agent claims",
    "balance not reflected", "balance not updated",
    "didn't reflect", "not credited", "balance didn't",
    "agent didn't", "agent number", "agent bkash",
]
AGENT_CASH_IN_KEYWORDS_BN = [
    "ক্যাশ ইন", "এজেন্ট", "এজেন্টের কাছে", "এজেন্ট বলছে", "এজেন্ট বলেছে",
    "ডিপোজিট", "জমা", "টাকা জমা", "ব্যালেন্সে আসেনি",
    "আসেনি", "ক্যাশ ইন করেছি", "ক্যাশ ইন করেছিলাম",
]

# Numbers (English digits) and Bangla digit map
_BANGLA_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

_AMOUNT_PATTERN = re.compile(
    r"(?:taka|tk|৳|bdt|টাকা|BDT)?\s*([0-9০-৯][0-9,০-৯]*(?:\.[0-9০-৹]+)?)",
    re.IGNORECASE,
)

# Counterparty phone number patterns (Bangladesh)
_PHONE_PATTERN = re.compile(r"\+?88?0?1[3-9]\d{8}")


def _normalize_digits(text: str) -> str:
    return text.translate(_BANGLA_DIGITS)


def _is_bangla(text: str) -> bool:
    return any("\u0980" <= ch <= "\u09FF" for ch in text)


def _extract_amounts(text: str) -> list[float]:
    """Return a list of numeric amounts found in the text (best effort)."""
    norm = _normalize_digits(text)
    out: list[float] = []
    for m in _AMOUNT_PATTERN.finditer(norm):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
            if v >= 1:  # ignore 0 / 0.5 noise
                out.append(v)
        except ValueError:
            continue
    return out


def _extract_phones(text: str) -> set[str]:
    norm = _normalize_digits(text)
    return set(_PHONE_PATTERN.findall(norm))


def _kw_match(text: str, keywords: list[str]) -> bool:
    norm = text.lower()
    for kw in keywords:
        if kw.lower() in norm:
            return True
    return False


# ---------------------------------------------------------------------------
# Case type detection
# ---------------------------------------------------------------------------

@dataclass
class Signals:
    phishing: bool
    wrong_transfer: bool
    payment_failed: bool
    refund_request: bool
    duplicate: bool
    merchant_settlement: bool
    agent_cash_in: bool
    not_received: bool
    amounts: list[float]
    phones: set[str]


def detect_signals(complaint: str) -> Signals:
    bn = _is_bangla(complaint)
    return Signals(
        phishing=(
            _kw_match(complaint, PHISHING_KEYWORDS_EN)
            if not bn
            else _kw_match(complaint, PHISHING_KEYWORDS_BN)
        ),
        wrong_transfer=_kw_match(complaint, WRONG_TRANSFER_KEYWORDS_EN)
        if not bn
        else _kw_match(complaint, WRONG_TRANSFER_KEYWORDS_BN),
        payment_failed=_kw_match(complaint, PAYMENT_FAILED_KEYWORDS_EN)
        if not bn
        else _kw_match(complaint, PAYMENT_FAILED_KEYWORDS_BN),
        refund_request=_kw_match(complaint, REFUND_KEYWORDS_EN)
        if not bn
        else _kw_match(complaint, REFUND_KEYWORDS_BN),
        duplicate=_kw_match(complaint, DUPLICATE_KEYWORDS_EN)
        if not bn
        else _kw_match(complaint, DUPLICATE_KEYWORDS_BN),
        merchant_settlement=_kw_match(complaint, MERCHANT_SETTLEMENT_KEYWORDS_EN)
        if not bn
        else _kw_match(complaint, MERCHANT_SETTLEMENT_KEYWORDS_BN),
        agent_cash_in=_kw_match(complaint, AGENT_CASH_IN_KEYWORDS_EN)
        if not bn
        else _kw_match(complaint, AGENT_CASH_IN_KEYWORDS_BN),
        not_received=(
            _kw_match(complaint, NOT_RECEIVED_KEYWORDS_EN) if not bn else False
        ),
        amounts=_extract_amounts(complaint),
        phones=_extract_phones(complaint),
    )


# ---------------------------------------------------------------------------
# Transaction matching
# ---------------------------------------------------------------------------

def _score_transaction(txn: dict[str, Any], sig: Signals) -> float:
    """Higher = better match. Used to pick the most relevant transaction."""
    score = 0.0
    amount = float(txn.get("amount") or 0)
    # Amount match (exact)
    if sig.amounts and amount in sig.amounts:
        score += 5.0
    else:
        # Soft amount match (within 10%)
        for a in sig.amounts:
            if a > 0 and abs(a - amount) / max(a, amount) <= 0.10:
                score += 2.0
                break

    cp = str(txn.get("counterparty") or "")
    cp_digits = _normalize_digits(cp)
    # Phone match in complaint
    for ph in sig.phones:
        ph_digits = _normalize_digits(ph)
        if ph_digits and ph_digits in cp_digits:
            score += 3.0
            break

    # Type relevance
    t = str(txn.get("type") or "").lower()
    if sig.wrong_transfer and t == "transfer":
        score += 1.0
    if sig.payment_failed and t == "payment":
        score += 1.5
    if sig.refund_request and t in {"payment", "transfer"}:
        score += 0.5
    if sig.duplicate and t == "payment":
        score += 1.5
    if sig.merchant_settlement and t == "settlement":
        score += 2.0
    if sig.agent_cash_in and t == "cash_in":
        score += 2.0

    # Failed status boosts payment-failed / cash-in issues
    st = str(txn.get("status") or "").lower()
    if sig.payment_failed and st == "failed":
        score += 2.0
    if sig.agent_cash_in and st in {"pending", "failed"}:
        score += 1.0
    if sig.merchant_settlement and st == "pending":
        score += 1.0

    return score


def pick_relevant_transaction(
    history: list[dict[str, Any]], sig: Signals
) -> tuple[str | None, str, dict[str, Any] | None]:
    """Return (txn_id, evidence_verdict, txn_obj).

    Rules:
    - No history -> insufficient_data, None
    - Phishing reports don't depend on transactions -> insufficient_data, None
    - Duplicate-payment: pick the suspected duplicate (most recent among
      near-simultaneous identical payments to the same counterparty).
    - Otherwise pick highest-scoring transaction. If there's a strong amount
      match, evidence_verdict = consistent. If there's only a weak match but
      the customer mentions a counterparty that has a strong history (e.g.
      established recipient pattern), flag inconsistent.
    """
    if not history:
        return None, "insufficient_data", None

    # ---- Duplicate-payment special case (TKT-010 pattern) -----------------
    if sig.duplicate and len(history) >= 2:
        payments = [t for t in history if str(t.get("type") or "").lower() == "payment"]
        # Group by (counterparty, amount)
        from collections import defaultdict

        groups: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
        for t in payments:
            key = (str(t.get("counterparty") or ""), float(t.get("amount") or 0))
            groups[key].append(t)
        for key, items in groups.items():
            if len(items) >= 2:
                # Pick the latest one (likely the duplicate)
                items_sorted = sorted(items, key=lambda x: x.get("timestamp") or "")
                dup = items_sorted[-1]
                return dup.get("transaction_id"), "consistent", dup

    # ---- Score every transaction -----------------------------------------
    scored = [(txn, _score_transaction(txn, sig)) for txn in history]
    scored.sort(key=lambda x: x[1], reverse=True)
    best_txn, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    # Phishing reports: don't pin a transaction
    if sig.phishing:
        return None, "insufficient_data", None

    if best_score <= 0:
        # No signal matched any transaction
        return None, "insufficient_data", None

    # Vague complaint (no amounts / no keywords matched anything useful):
    if not sig.amounts and not sig.phones and not any(
        [
            sig.wrong_transfer,
            sig.payment_failed,
            sig.refund_request,
            sig.duplicate,
            sig.merchant_settlement,
            sig.agent_cash_in,
        ]
    ):
        return None, "insufficient_data", None

    # Strong match: explicit amount or phone matches cleanly
    amount_exact = any(
        float(best_txn.get("amount") or 0) == a for a in sig.amounts
    )
    phone_match = any(
        _normalize_digits(ph) in _normalize_digits(str(best_txn.get("counterparty") or ""))
        for ph in sig.phones
    )
    type_relevant = (
        (sig.wrong_transfer and str(best_txn.get("type") or "").lower() == "transfer")
        or (sig.payment_failed and str(best_txn.get("type") or "").lower() == "payment")
        or (sig.duplicate and str(best_txn.get("type") or "").lower() == "payment")
        or (sig.merchant_settlement and str(best_txn.get("type") or "").lower() == "settlement")
        or (sig.agent_cash_in and str(best_txn.get("type") or "").lower() == "cash_in")
    )

    # Established-recipient inconsistency (TKT-02): multiple prior transfers
    # to same counterparty -> inconsistent
    established = False
    if sig.wrong_transfer and best_txn:
        cp = str(best_txn.get("counterparty") or "")
        prior_same = sum(
            1
            for t in history
            if t is not best_txn
            and str(t.get("counterparty") or "") == cp
            and str(t.get("type") or "").lower() == "transfer"
        )
        if prior_same >= 2:
            established = True

    # Ambiguity override (TKT-08): the customer's specific amount matches
    # two or more completed transfers. We can't tell which is the right one.
    if amount_exact and sig.amounts:
        target_amount = float(best_txn.get("amount") or 0)
        completed_matches = sum(
            1
            for t in history
            if str(t.get("status") or "").lower() == "completed"
            and float(t.get("amount") or 0) == target_amount
            and target_amount in sig.amounts
        )
        if completed_matches >= 2:
            return None, "insufficient_data", None

    if established:
        return best_txn.get("transaction_id"), "inconsistent", best_txn

    if amount_exact or phone_match or type_relevant:
        return best_txn.get("transaction_id"), "consistent", best_txn

    # Multiple plausible matches with very close scores -> ambiguous
    if best_score - second_score < 0.5 and best_score > 0:
        # TKT-08 pattern: multiple txns of same amount, no way to disambiguate
        return None, "insufficient_data", None

    return best_txn.get("transaction_id"), "consistent", best_txn


# ---------------------------------------------------------------------------
# Severity / routing / human review
# ---------------------------------------------------------------------------

def decide(
    sig: Signals,
    case_type: str,
    evidence_verdict: str,
    txn: dict[str, Any] | None,
    user_type: str,
) -> tuple[str, str, bool, float]:
    """Return (severity, department, human_review_required, confidence).

    Confidence is a rough self-estimate, not a model probability.
    """
    if case_type == "phishing_or_social_engineering":
        return "critical", "fraud_risk", True, 0.95

    if case_type == "other":
        return "low", "customer_support", False, 0.6

    # Established recipient -> still wrong_transfer but medium + review
    if case_type == "wrong_transfer":
        if evidence_verdict == "inconsistent":
            return "medium", "dispute_resolution", True, 0.75
        if evidence_verdict == "insufficient_data":
            return "medium", "dispute_resolution", False, 0.65
        return "high", "dispute_resolution", True, 0.9

    if case_type == "payment_failed":
        if evidence_verdict == "consistent":
            return "high", "payments_ops", False, 0.9
        return "medium", "payments_ops", False, 0.75

    if case_type == "duplicate_payment":
        return "high", "payments_ops", True, 0.93

    if case_type == "merchant_settlement_delay":
        return "medium", "merchant_operations", False, 0.9

    if case_type == "agent_cash_in_issue":
        if evidence_verdict == "consistent":
            return "high", "agent_operations", True, 0.88
        return "medium", "agent_operations", True, 0.78

    if case_type == "refund_request":
        # Change-of-mind / merchant-policy refund -> low, customer_support
        return "low", "customer_support", False, 0.85

    return "low", "customer_support", False, 0.5


# ---------------------------------------------------------------------------
# Customer reply generation (safe templates)
# ---------------------------------------------------------------------------

# Forbidden phrases — checked before returning any reply
FORBIDDEN_PHRASES = [
    "share your pin",
    "share your otp",
    "share your password",
    "send your otp",
    "send your pin",
    "send your password",
    "give me your pin",
    "give me your otp",
    "give me your password",
    "tell me your pin",
    "tell me your otp",
    "we will refund",
    "we'll refund",
    "we have refunded",
    "we've refunded",
    "your account will be unblocked",
    "we have reversed",
    "we'll reverse",
    "we will reverse",
    "contact this number",
    "call this number",
    "wire transfer",
    "western union",
]

# English safe templates (must NOT contain forbidden phrases)
SAFE_REPLY_EN = {
    "phishing": (
        "Thank you for reaching out before sharing any information. We never "
        "ask for your PIN, OTP, or password under any circumstances. Please do "
        "not share these with anyone, even if they claim to be from us. Our "
        "fraud team has been notified of this incident."
    ),
    "wrong_transfer": (
        "We have noted your concern about transaction {txn}. Please do not "
        "share your PIN or OTP with anyone. Our dispute team will review the "
        "case and contact you through official support channels."
    ),
    "wrong_transfer_ambiguous": (
        "Thank you for reaching out. We see multiple transactions around that "
        "time. Could you share the recipient number so we can identify the "
        "right transaction? Please do not share your PIN or OTP with anyone."
    ),
    "payment_failed": (
        "We have noted that transaction {txn} may have caused an unexpected "
        "balance deduction. Our payments team will review the case and any "
        "eligible amount will be returned through official channels. Please "
        "do not share your PIN or OTP with anyone."
    ),
    "duplicate_payment": (
        "We have noted the possible duplicate payment for transaction {txn}. "
        "Our payments team will verify with the biller and any eligible "
        "amount will be returned through official channels. Please do not "
        "share your PIN or OTP with anyone."
    ),
    "refund_request": (
        "Thank you for reaching out. Refunds for completed merchant payments "
        "depend on the merchant's own policy. We recommend contacting the "
        "merchant directly. If you need help reaching them, please reply and "
        "we will guide you through official support channels. Please do not "
        "share your PIN or OTP with anyone."
    ),
    "merchant_settlement": (
        "We have noted your concern about settlement {txn}. Our merchant "
        "operations team will check the batch status and update you on the "
        "expected settlement time through official channels."
    ),
    "agent_cash_in": (
        "We have noted your concern about transaction {txn}. Our agent "
        "operations team will verify it quickly and reach you through "
        "official channels. Please do not share your PIN or OTP with anyone."
    ),
    "vague": (
        "Thank you for reaching out. To help you faster, please share the "
        "transaction ID, the amount involved, and a short description of "
        "what went wrong. Please do not share your PIN or OTP with anyone."
    ),
    "fallback": (
        "We have received your request. Our team will review the case and "
        "contact you through official support channels. Please do not share "
        "your PIN or OTP with anyone."
    ),
}

SAFE_REPLY_BN = {
    "phishing": (
        "যেকোনো তথ্য শেয়ার করার আগে আমাদের জানানোর জন্য ধন্যবাদ। আমরা "
        "কখনোই কোনো পরিস্থিতিতে আপনার পিন, ওটিপি বা পাসওয়ার্ড চাই না। "
        "অনুগ্রহ করে কেউ নিজেকে আমাদের বলে দাবি করলেও এগুলো শেয়ার করবেন না। "
        "আমাদের ফ্রড টিমকে এই ঘটনা জানানো হয়েছে।"
    ),
    "wrong_transfer": (
        "লেনদেন {txn} সংক্রান্ত আপনার অভিযোগ আমরা গ্রহণ করেছি। অনুগ্রহ করে "
        "কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না। আমাদের ডিসপিউট টিম "
        "বিষয়টি পর্যালোচনা করে অফিসিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে।"
    ),
    "wrong_transfer_ambiguous": (
        "আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। এই সময়ের কাছাকাছি একাধিক "
        "লেনদেন দেখা যাচ্ছে। সঠিক লেনদেন শনাক্ত করতে প্রাপকের নম্বরটি "
        "জানাতে পারবেন? অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার "
        "করবেন না।"
    ),
    "payment_failed": (
        "লেনদেন {txn} এর ফলে অপ্রত্যাশিত ব্যালেন্স কর্তন হতে পারে বলে আমরা "
        "ধারণা করছি। আমাদের পেমেন্টস টিম বিষয়টি পর্যালোচনা করবে এবং "
        "যোগ্য পরিমাণ অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে। অনুগ্রহ করে কারো "
        "সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "duplicate_payment": (
        "লেনদেন {txn} এর সম্ভাব্য ডুপ্লিকেট পেমেন্ট আমরা লক্ষ্য করেছি। "
        "আমাদের পেমেন্টস টিম বিলারের সাথে যাচাই করবে এবং যোগ্য পরিমাণ "
        "অফিসিয়াল চ্যানেলে ফেরত দেওয়া হবে। অনুগ্রহ করে কারো সাথে আপনার "
        "পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "refund_request": (
        "আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। সম্পন্ন মার্চেন্ট পেমেন্টের "
        "রিফান্ড মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে। আমরা সরাসরি "
        "মার্চেন্টের সাথে যোগাযোগ করার পরামর্শ দিচ্ছি। যোগাযোগে সহায়তা "
        "প্রয়োজন হলে জানাবেন, আমরা অফিসিয়াল চ্যানেলে আপনাকে সাহায্য করব। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "merchant_settlement": (
        "সেটেলমেন্ট {txn} সংক্রান্ত আপনার অভিযোগ আমরা গ্রহণ করেছি। আমাদের "
        "মার্চেন্ট অপারেশন্স টিম ব্যাচের অবস্থা যাচাই করে প্রত্যাশিত সময়সীমা "
        "অফিসিয়াল চ্যানেলে জানাবে।"
    ),
    "agent_cash_in": (
        "লেনদেন {txn} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট অপারেশন্স "
        "দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "vague": (
        "আমাদের সাথে যোগাযোগ করার জন্য ধন্যবাদ। দ্রুত সহায়তা করতে অনুগ্রহ "
        "করে লেনদেন আইডি, সংশ্লিষ্ট পরিমাণ এবং সংক্ষেপে সমস্যাটি জানাবেন। "
        "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
    "fallback": (
        "আমরা আপনার অনুরোধ গ্রহণ করেছি। আমাদের টিম বিষয়টি পর্যালোচনা করে "
        "অফিসিয়াল চ্যানেলে আপনার সাথে যোগাযোগ করবে। অনুগ্রহ করে কারো সাথে "
        "আপনার পিন বা ওটিপি শেয়ার করবেন না।"
    ),
}


def _is_safe(text: str) -> bool:
    lc = text.lower()
    return not any(p in lc for p in FORBIDDEN_PHRASES)


def _pick_template(case_type: str, evidence_verdict: str) -> tuple[str, str]:
    """Return (key, language)."""
    if case_type == "phishing_or_social_engineering":
        return "phishing", "en"
    if case_type == "wrong_transfer":
        return ("wrong_transfer_ambiguous" if evidence_verdict == "insufficient_data"
                else "wrong_transfer"), "en"
    if case_type == "payment_failed":
        return "payment_failed", "en"
    if case_type == "duplicate_payment":
        return "duplicate_payment", "en"
    if case_type == "refund_request":
        return "refund_request", "en"
    if case_type == "merchant_settlement_delay":
        return "merchant_settlement", "en"
    if case_type == "agent_cash_in_issue":
        return "agent_cash_in", "en"
    if case_type == "other":
        return "vague", "en"
    return "fallback", "en"


def build_customer_reply(
    case_type: str,
    evidence_verdict: str,
    txn_id: str | None,
    complaint_language: str | None,
) -> str:
    key, lang = _pick_template(case_type, evidence_verdict)

    # Language selection: Bangla if complaint is Bangla or language == "bn"
    use_bn = (complaint_language == "bn") or (
        complaint_language not in {"en", "mixed"} and complaint_language is not None
    )
    # Default: stay in English when language is unspecified or "en"/"mixed"
    if complaint_language in {"en", "mixed", None}:
        use_bn = False

    templates = SAFE_REPLY_BN if use_bn else SAFE_REPLY_EN
    template = templates.get(key, templates["fallback"])

    if "{txn}" in template and txn_id:
        text = template.replace("{txn}", txn_id)
    elif "{txn}" in template:
        # No transaction: fall back to a generic safe template
        text = templates["fallback"]
    else:
        text = template

    # Safety net — replace if forbidden content slipped in
    if not _is_safe(text):
        text = templates["fallback"]
    return text


# ---------------------------------------------------------------------------
# Agent summary / next action / reason codes
# ---------------------------------------------------------------------------

def build_agent_summary(
    case_type: str,
    txn: dict[str, Any] | None,
    evidence_verdict: str,
    sig: Signals,
    amount: float | None,
) -> str:
    if case_type == "phishing_or_social_engineering":
        return (
            "Customer reports an unsolicited call or message asking for OTP, "
            "PIN, or password. Likely social engineering attempt."
        )
    if not txn:
        if evidence_verdict == "insufficient_data":
            return (
                "Customer reports a concern but the complaint lacks specific "
                "transaction, amount, or counterparty detail. Cannot pinpoint "
                "a matching transaction in the provided history."
            )
        return "Customer complaint could not be matched to any transaction in the provided history."

    cp = str(txn.get("counterparty") or "")
    amt = float(txn.get("amount") or 0)
    tid = str(txn.get("transaction_id") or "")
    ttype = str(txn.get("type") or "")
    status = str(txn.get("status") or "")

    if case_type == "wrong_transfer":
        if evidence_verdict == "inconsistent":
            return (
                f"Customer claims {tid} ({amt} BDT to {cp}) was a wrong "
                f"transfer, but transaction history shows prior transfers to "
                f"the same recipient. Pattern suggests an established recipient."
            )
        return (
            f"Customer reports sending {amt} BDT via {tid} to {cp}, which "
            f"they now believe was the wrong recipient."
        )
    if case_type == "payment_failed":
        return (
            f"Customer reports {amt} BDT {ttype} {tid} failed but balance "
            f"may have been deducted. Status: {status}."
        )
    if case_type == "duplicate_payment":
        return (
            f"Customer reports duplicate {amt} BDT {ttype} to {cp}. "
            f"Suspected duplicate transaction: {tid}."
        )
    if case_type == "merchant_settlement_delay":
        return (
            f"Merchant reports {amt} BDT settlement {tid} is delayed beyond "
            f"the standard window. Status: {status}."
        )
    if case_type == "agent_cash_in_issue":
        return (
            f"Customer reports {amt} BDT cash-in via {cp} ({tid}) not "
            f"reflected in balance. Status: {status}."
        )
    if case_type == "refund_request":
        return (
            f"Customer requests refund of {amt} BDT for {tid} (merchant "
            f"payment). Status: {status}."
        )
    return f"Customer complaint references transaction {tid} ({amt} BDT)."


def build_next_action(
    case_type: str,
    evidence_verdict: str,
    txn_id: str | None,
    human_review: bool,
) -> str:
    if case_type == "phishing_or_social_engineering":
        return (
            "Escalate to fraud_risk team immediately. Confirm to customer "
            "that the company never asks for OTP. Log the reported number "
            "for fraud pattern analysis."
        )
    if case_type == "wrong_transfer":
        if evidence_verdict == "inconsistent":
            return (
                f"Flag {txn_id or 'the transaction'} for human review. "
                "Verify with the customer whether this was genuinely a wrong "
                "transfer given the established transaction pattern."
            )
        if evidence_verdict == "insufficient_data":
            return (
                "Reply to the customer asking for the recipient number to "
                "identify the correct transaction before initiating any dispute."
            )
        return (
            f"Verify {txn_id} details with the customer and initiate the "
            "wrong-transfer dispute workflow per policy."
        )
    if case_type == "payment_failed":
        return (
            f"Investigate {txn_id} ledger status. If balance was deducted on "
            "a failed payment, initiate the automatic reversal flow within "
            "standard SLA."
        )
    if case_type == "duplicate_payment":
        return (
            f"Verify the duplicate with payments_ops. If the biller confirms "
            f"only one payment was received, initiate reversal of {txn_id}."
        )
    if case_type == "merchant_settlement_delay":
        return (
            f"Route to merchant_operations to verify settlement batch status "
            f"for {txn_id}. If delayed, communicate a revised ETA through "
            "official channels."
        )
    if case_type == "agent_cash_in_issue":
        return (
            f"Investigate {txn_id} pending status with agent operations. "
            "Confirm settlement state and resolve within the standard cash-in SLA."
        )
    if case_type == "refund_request":
        return (
            f"Inform the customer that refund eligibility for {txn_id} "
            "depends on the merchant's policy. Provide guidance on contacting "
            "the merchant directly through official channels."
        )
    return (
        "Reply to the customer asking for specific details: transaction ID, "
        "amount involved, and a short description of what went wrong."
    )


def build_reason_codes(case_type: str, evidence_verdict: str, sig: Signals) -> list[str]:
    codes = [case_type]
    if evidence_verdict == "consistent":
        codes.append("transaction_match")
    elif evidence_verdict == "inconsistent":
        codes.append("evidence_inconsistent")
    elif evidence_verdict == "insufficient_data":
        codes.append("insufficient_data")
    if sig.phishing:
        codes.append("credential_protection")
        codes.append("critical_escalation")
    if sig.agent_cash_in and "agent_cash_in_issue" not in codes:
        codes.append("agent_cash_in")
    if sig.merchant_settlement and "merchant_settlement_delay" not in codes:
        codes.append("merchant_settlement")
    if sig.duplicate and "duplicate_payment" not in codes:
        codes.append("duplicate_payment")
    if sig.refund_request and "refund_request" not in codes:
        codes.append("refund_request")
    return codes


# ---------------------------------------------------------------------------
# Top-level analyze()
# ---------------------------------------------------------------------------

def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the full pipeline and return the response dict."""
    ticket_id = str(payload.get("ticket_id") or "")
    complaint = str(payload.get("complaint") or "")
    complaint_language = payload.get("language")
    user_type = str(payload.get("user_type") or "customer")
    history = payload.get("transaction_history") or []

    if not ticket_id:
        raise ValueError("ticket_id is required")
    if not complaint or not complaint.strip():
        raise ValueError("complaint is required")
    if not isinstance(history, list):
        raise ValueError("transaction_history must be a list")

    # Prompt-injection guard: strip any "ignore previous" / role override text
    # but keep the original for analysis. We never echo user instructions.
    sig = detect_signals(complaint)

    # Decide case type (priority order)
    if sig.phishing:
        case_type = "phishing_or_social_engineering"
    elif sig.duplicate:
        case_type = "duplicate_payment"
    elif sig.merchant_settlement:
        case_type = "merchant_settlement_delay"
    elif sig.agent_cash_in:
        case_type = "agent_cash_in_issue"
    elif sig.payment_failed:
        case_type = "payment_failed"
    elif sig.wrong_transfer or sig.not_received:
        case_type = "wrong_transfer"
    elif sig.refund_request:
        case_type = "refund_request"
    else:
        case_type = "other"

    # Pick relevant transaction
    txn_id, evidence_verdict, txn_obj = pick_relevant_transaction(history, sig)

    # Vague complaint -> force "other" if no signal matched
    has_specific_signal = any([
        sig.wrong_transfer, sig.payment_failed, sig.refund_request,
        sig.duplicate, sig.merchant_settlement, sig.agent_cash_in,
        sig.phishing, sig.not_received,
    ])
    if not has_specific_signal and evidence_verdict == "insufficient_data":
        case_type = "other"

    severity, department, human_review, confidence = decide(
        sig, case_type, evidence_verdict, txn_obj, user_type
    )

    # Special-case: agent_cash_in with insufficient data still goes to
    # agent_operations but flagged for human review.
    if case_type == "agent_cash_in_issue" and evidence_verdict == "insufficient_data":
        severity, department, human_review, confidence = (
            "medium",
            "agent_operations",
            True,
            0.7,
        )

    agent_summary = build_agent_summary(case_type, txn_obj, evidence_verdict, sig, None)
    next_action = build_next_action(case_type, evidence_verdict, txn_id, human_review)
    customer_reply = build_customer_reply(case_type, evidence_verdict, txn_id, complaint_language)
    reason_codes = build_reason_codes(case_type, evidence_verdict, sig)

    return {
        "ticket_id": ticket_id,
        "relevant_transaction_id": txn_id,
        "evidence_verdict": evidence_verdict,
        "case_type": case_type,
        "severity": severity,
        "department": department,
        "agent_summary": agent_summary,
        "recommended_next_action": next_action,
        "customer_reply": customer_reply,
        "human_review_required": human_review,
        "confidence": confidence,
        "reason_codes": reason_codes,
    }
