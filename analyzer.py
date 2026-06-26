"""Evidence-first complaint investigation pipeline for QueueStorm.

The repository is intentionally flat, so this file implements the services
from SYSTEM_ARCHITECTURE.md as small in-file stages:
normalizer, language detector, extractor, matcher, evidence verifier,
classifier, router, severity/confidence engines, reply generator, and safety.
The rule engine is the source of truth. Gemini may polish free-text fields
only after these deterministic decisions are complete.
"""

from __future__ import annotations

import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal


EvidenceVerdict = Literal["consistent", "inconsistent", "insufficient_data"]
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
Severity = Literal["low", "medium", "high", "critical"]
Department = Literal[
    "customer_support",
    "dispute_resolution",
    "payments_ops",
    "merchant_operations",
    "agent_operations",
    "fraud_risk",
]

CASE_DEPARTMENT: dict[str, Department] = {
    "wrong_transfer": "dispute_resolution",
    "payment_failed": "payments_ops",
    "duplicate_payment": "payments_ops",
    "merchant_settlement_delay": "merchant_operations",
    "agent_cash_in_issue": "agent_operations",
    "phishing_or_social_engineering": "fraud_risk",
    "refund_request": "customer_support",
    "other": "customer_support",
}

BN_DIGITS = str.maketrans("\u09e6\u09e7\u09e8\u09e9\u09ea\u09eb\u09ec\u09ed\u09ee\u09ef", "0123456789")
AMOUNT_RE = re.compile(
    r"(?:bdt|tk|taka|\u09f3|\u099f\u09be\u0995\u09be)?\s*"
    r"([0-9\u09e6-\u09ef][0-9,\u09e6-\u09ef]*(?:\.[0-9\u09e6-\u09ef]+)?)",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?:\+?88)?01[3-9]\d{8}")
TXN_ID_RE = re.compile(r"\b(?:txn|tx|transaction)[-\s_]*[a-z0-9-]+\b", re.IGNORECASE)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)

PROMPT_INJECTION_PATTERNS = [
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "developer message",
    "act as",
    "jailbreak",
    "return only",
    "override",
]

KEYWORDS: dict[str, list[str]] = {
    "phishing": [
        "otp", "pin", "password", "cvv", "one time password", "asked for my otp",
        "asked for otp", "share otp", "share my otp", "share pin", "fake call",
        "scam", "phishing", "social engineering", "verify your account",
        "account will be blocked", "someone called", "call from bkash",
        "\u0993\u099f\u09bf\u09aa\u09bf", "\u09aa\u09bf\u09a8",
        "\u09aa\u09be\u09b8\u0993\u09af\u09bc\u09be\u09b0\u09cd\u09a1",
        "\u09ab\u09bf\u09b6\u09bf\u0982", "\u09b8\u09cd\u0995\u09cd\u09af\u09be\u09ae",
    ],
    "wrong_transfer": [
        "wrong number", "wrong person", "wrong recipient", "wrong account",
        "sent to wrong", "send to wrong", "by mistake", "mistakenly",
        "typed it wrong", "vul number", "bhul number", "bhul kore",
        "\u09ad\u09c1\u09b2 \u09a8\u09ae\u09cd\u09ac\u09b0",
        "\u09ad\u09c1\u09b2 \u09a8\u09be\u09ae\u09cd\u09ac\u09be\u09b0",
        "\u09ad\u09c1\u09b2 \u0995\u09b0\u09c7",
    ],
    "not_received": [
        "didn't get", "did not get", "hasn't received", "has not received",
        "not received", "didn't receive", "recipient didn't", "brother",
        "sister", "friend", "claims he didn't", "says he didn't get",
    ],
    "payment_failed": [
        "failed", "payment failed", "transaction failed", "recharge failed",
        "didn't go through", "did not go through", "balance deducted",
        "money deducted", "amount deducted", "deducted",
        "\u09ac\u09cd\u09af\u09b0\u09cd\u09a5",
        "\u09ab\u09c7\u0987\u09b2", "\u0995\u09c7\u099f\u09c7",
    ],
    "refund_request": [
        "refund", "reverse it", "reversal", "give me back", "return my money",
        "money back", "changed my mind", "don't want it", "do not want it",
        "\u09b0\u09bf\u09ab\u09be\u09a8\u09cd\u09a1",
        "\u09ab\u09c7\u09b0\u09a4",
    ],
    "duplicate_payment": [
        "deducted twice", "charged twice", "paid twice", "duplicate",
        "two times", "double deducted", "twice from my", "deducted two times",
        "\u09a6\u09c1\u0987\u09ac\u09be\u09b0", "\u09a1\u09c1\u09aa\u09cd\u09b2\u09bf\u0995\u09c7\u099f",
    ],
    "merchant_settlement_delay": [
        "settlement", "settle", "not settled", "merchant settlement",
        "sales not", "yesterday's sales", "merchant account", "batch",
        "\u09b8\u09c7\u099f\u09c7\u09b2\u09ae\u09c7\u09a8\u09cd\u099f",
        "\u09ae\u09be\u09b0\u09cd\u099a\u09c7\u09a8\u09cd\u099f",
    ],
    "agent_cash_in_issue": [
        "cash in", "cash-in", "agent", "deposit", "deposited", "agent says",
        "agent said", "balance not reflected", "not credited", "didn't reflect",
        "\u0995\u09cd\u09af\u09be\u09b6 \u0987\u09a8",
        "\u098f\u099c\u09c7\u09a8\u09cd\u099f", "\u099c\u09ae\u09be",
        "\u0986\u09b8\u09c7\u09a8\u09bf",
    ],
}

FORBIDDEN_REPLY_PHRASES = [
    "share your pin", "share your otp", "share your password", "send your otp",
    "send your pin", "give me your pin", "give me your otp", "tell me your pin",
    "tell me your otp", "we will refund", "we'll refund", "we have refunded",
    "we have reversed", "we will reverse", "we'll reverse",
    "your account will be unblocked", "call this number", "contact this number",
    "western union", "wire transfer",
]


@dataclass
class ExtractedInfo:
    language: str
    normalized_complaint: str
    amounts: list[float] = field(default_factory=list)
    phones: set[str] = field(default_factory=set)
    transaction_ids: set[str] = field(default_factory=set)
    times: list[str] = field(default_factory=list)
    flags: dict[str, bool] = field(default_factory=dict)
    prompt_injection_detected: bool = False


@dataclass
class MatchResult:
    transaction_id: str | None
    transaction: dict[str, Any] | None
    verdict: EvidenceVerdict
    candidates: list[dict[str, Any]]
    reasons: list[str] = field(default_factory=list)


def _normalize_digits(text: str) -> str:
    return text.translate(BN_DIGITS)


def _has_bangla(text: str) -> bool:
    return any("\u0980" <= ch <= "\u09ff" for ch in text)


def normalize_complaint(complaint: str) -> str:
    text = _normalize_digits(complaint or "").lower()
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("-", " ")
    for bad, good in {
        "vul": "bhul",
        "tk.": "tk",
        " taka ": " taka ",
        "bkash": "bkash",
    }.items():
        text = text.replace(bad, good)
    keep = set("+@#_-") | set(string.ascii_lowercase) | set(string.digits) | {" ", "'", "."}
    text = "".join(ch if (ch in keep or "\u0980" <= ch <= "\u09ff") else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def detect_language(complaint: str, provided: str | None = None) -> str:
    if provided in {"en", "bn", "mixed"}:
        return provided
    has_bn = _has_bangla(complaint)
    has_ascii = any("a" <= ch.lower() <= "z" for ch in complaint)
    if has_bn and has_ascii:
        return "mixed"
    return "bn" if has_bn else "en"


def _keyword(text: str, key: str) -> bool:
    return any(term in text for term in KEYWORDS[key])


def extract_information(complaint: str, provided_language: str | None = None) -> ExtractedInfo:
    normalized = normalize_complaint(complaint)
    amounts: list[float] = []
    for match in AMOUNT_RE.finditer(normalized):
        try:
            amount = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if amount >= 1 and amount not in amounts:
            amounts.append(amount)

    transaction_ids = {m.group(0).upper().replace(" ", "-").replace("_", "-") for m in TXN_ID_RE.finditer(normalized)}
    times = []
    for hour, minute, meridiem in TIME_RE.findall(normalized):
        if meridiem:
            times.append(f"{hour}:{minute or '00'}{meridiem.lower()}")

    flags = {name: _keyword(normalized, name) for name in KEYWORDS}
    prompt_injection = any(pattern in normalized for pattern in PROMPT_INJECTION_PATTERNS)
    return ExtractedInfo(
        language=detect_language(complaint, provided_language),
        normalized_complaint=normalized,
        amounts=amounts,
        phones=set(PHONE_RE.findall(normalized)),
        transaction_ids=transaction_ids,
        times=times,
        flags=flags,
        prompt_injection_detected=prompt_injection,
    )


def _amount(txn: dict[str, Any]) -> float:
    try:
        return float(txn.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _tx_type(txn: dict[str, Any]) -> str:
    return str(txn.get("type") or "").lower()


def _tx_status(txn: dict[str, Any]) -> str:
    return str(txn.get("status") or "").lower()


def _score_transaction(txn: dict[str, Any], info: ExtractedInfo, case_hint: str) -> float:
    score = 0.0
    txn_id = str(txn.get("transaction_id") or "").upper()
    if txn_id and txn_id in info.transaction_ids:
        score += 8

    amount = _amount(txn)
    if info.amounts:
        if any(abs(amount - mentioned) < 0.01 for mentioned in info.amounts):
            score += 5
        elif any(mentioned and abs(amount - mentioned) / max(amount, mentioned) <= 0.10 for mentioned in info.amounts):
            score += 2

    counterparty = _normalize_digits(str(txn.get("counterparty") or ""))
    if any(phone in counterparty for phone in info.phones):
        score += 4

    tx_type = _tx_type(txn)
    type_weights = {
        "wrong_transfer": {"transfer": 2},
        "payment_failed": {"payment": 2, "refund": 1},
        "refund_request": {"payment": 2, "refund": 1},
        "duplicate_payment": {"payment": 2},
        "merchant_settlement_delay": {"settlement": 3},
        "agent_cash_in_issue": {"cash_in": 3},
    }
    score += type_weights.get(case_hint, {}).get(tx_type, 0)

    status = _tx_status(txn)
    if case_hint == "payment_failed" and status == "failed":
        score += 3
    if case_hint in {"merchant_settlement_delay", "agent_cash_in_issue"} and status == "pending":
        score += 2
    if status == "reversed" and case_hint in {"payment_failed", "refund_request"}:
        score += 1

    return score


def classify_case(info: ExtractedInfo, user_type: str = "customer") -> CaseType:
    flags = info.flags
    if flags["phishing"]:
        return "phishing_or_social_engineering"
    if flags["duplicate_payment"]:
        return "duplicate_payment"
    if flags["merchant_settlement_delay"] or user_type == "merchant":
        return "merchant_settlement_delay"
    if flags["agent_cash_in_issue"]:
        return "agent_cash_in_issue"
    if flags["payment_failed"]:
        return "payment_failed"
    if flags["wrong_transfer"] or flags["not_received"]:
        return "wrong_transfer"
    if flags["refund_request"]:
        return "refund_request"
    return "other"


def _duplicate_match(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    groups: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
    for txn in history:
        if _tx_type(txn) == "payment" and _tx_status(txn) in {"completed", "pending"}:
            groups[(str(txn.get("counterparty") or ""), _amount(txn))].append(txn)
    for items in groups.values():
        if len(items) >= 2:
            return sorted(items, key=lambda t: str(t.get("timestamp") or ""))[-1]
    return None


def match_transactions(history: list[dict[str, Any]], info: ExtractedInfo, case_type: CaseType) -> MatchResult:
    if case_type == "phishing_or_social_engineering":
        return MatchResult(None, None, "insufficient_data", [], ["credential_report"])
    if not history:
        return MatchResult(None, None, "insufficient_data", [], ["no_transaction_history"])

    if case_type == "duplicate_payment":
        duplicate = _duplicate_match(history)
        if duplicate:
            return MatchResult(
                str(duplicate.get("transaction_id") or "") or None,
                duplicate,
                "consistent",
                [duplicate],
                ["duplicate_pattern"],
            )

    scored = [(txn, _score_transaction(txn, info, case_type)) for txn in history]
    scored.sort(key=lambda item: item[1], reverse=True)
    best, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0.0

    if best_score <= 0:
        return MatchResult(None, None, "insufficient_data", [], ["no_matching_signal"])

    if info.amounts:
        exact_amount = _amount(best)
        exact_matches = [
            txn for txn in history
            if abs(_amount(txn) - exact_amount) < 0.01
            and any(abs(_amount(txn) - mentioned) < 0.01 for mentioned in info.amounts)
            and _tx_status(txn) == "completed"
        ]
        if len(exact_matches) >= 2 and not info.phones and not info.transaction_ids:
            return MatchResult(None, None, "insufficient_data", exact_matches, ["ambiguous_amount_matches"])

    if best_score - second_score < 0.5 and second_score > 0:
        return MatchResult(None, None, "insufficient_data", [x[0] for x in scored if x[1] > 0], ["ambiguous_match"])

    verdict: EvidenceVerdict = "consistent"
    reasons = ["transaction_match"]

    if case_type == "wrong_transfer" and best:
        counterparty = str(best.get("counterparty") or "")
        prior_same = [
            txn for txn in history
            if txn is not best
            and _tx_type(txn) == "transfer"
            and str(txn.get("counterparty") or "") == counterparty
        ]
        if len(prior_same) >= 2:
            verdict = "inconsistent"
            reasons = ["established_recipient_pattern"]

    return MatchResult(str(best.get("transaction_id") or "") or None, best, verdict, [best], reasons)


def route_department(case_type: CaseType) -> Department:
    return CASE_DEPARTMENT[case_type]


def assess_severity(case_type: CaseType, match: MatchResult, info: ExtractedInfo) -> Severity:
    amount = _amount(match.transaction or {})
    if case_type == "phishing_or_social_engineering":
        return "critical"
    if case_type == "wrong_transfer":
        return "high" if match.verdict == "consistent" and amount >= 3000 else "medium"
    if case_type == "payment_failed":
        return "high" if amount >= 1000 or match.verdict == "consistent" else "medium"
    if case_type == "duplicate_payment":
        return "high"
    if case_type == "agent_cash_in_issue":
        return "high" if _tx_status(match.transaction or {}) == "pending" else "medium"
    if case_type == "merchant_settlement_delay":
        return "medium" if amount < 50000 else "high"
    if case_type == "refund_request":
        return "medium" if amount >= 10000 else "low"
    return "low"


def needs_human_review(case_type: CaseType, severity: Severity, match: MatchResult) -> bool:
    if case_type == "wrong_transfer" and match.verdict == "insufficient_data":
        return False
    if case_type in {"phishing_or_social_engineering", "wrong_transfer", "agent_cash_in_issue", "duplicate_payment"}:
        return True
    if match.verdict in {"inconsistent", "insufficient_data"} and case_type != "other":
        return case_type in {"refund_request", "payment_failed"}
    return severity in {"high", "critical"} and case_type not in {"payment_failed", "merchant_settlement_delay"}


def calculate_confidence(case_type: CaseType, match: MatchResult, info: ExtractedInfo) -> float:
    if case_type == "phishing_or_social_engineering":
        return 0.95
    if case_type == "other":
        return 0.60
    if match.verdict == "consistent" and match.transaction:
        base = 0.86
        if info.amounts:
            base += 0.03
        if info.phones or info.transaction_ids:
            base += 0.03
        return min(base, 0.94)
    if match.verdict == "inconsistent":
        return 0.75
    if match.reasons and "ambiguous" in " ".join(match.reasons):
        return 0.65
    return 0.55


def _format_amount(txn: dict[str, Any] | None) -> str:
    amount = _amount(txn or {})
    return str(int(amount)) if amount.is_integer() else f"{amount:.2f}"


def build_agent_summary(case_type: CaseType, match: MatchResult, info: ExtractedInfo) -> str:
    txn = match.transaction
    if case_type == "phishing_or_social_engineering":
        return "Customer reports a possible social engineering attempt involving OTP, PIN, password, or account blocking language."
    if not txn:
        if match.candidates:
            return "Customer complaint has multiple plausible transaction matches, so the exact transaction cannot be determined from the provided evidence."
        return "Customer complaint lacks enough transaction, amount, or counterparty detail to identify a relevant transaction."

    tid = str(txn.get("transaction_id") or "")
    cp = str(txn.get("counterparty") or "")
    amount = _format_amount(txn)
    status = _tx_status(txn)
    tx_type = _tx_type(txn)

    if case_type == "wrong_transfer":
        if match.verdict == "inconsistent":
            return f"Customer claims {tid} ({amount} BDT to {cp}) was a wrong transfer, but prior transfers to the same recipient suggest an established recipient pattern."
        return f"Customer reports a {amount} BDT transfer via {tid} to {cp} that may involve the wrong recipient or non-receipt."
    if case_type == "payment_failed":
        return f"Customer reports {amount} BDT payment {tid} failed or caused an unexpected deduction. Transaction status is {status}."
    if case_type == "duplicate_payment":
        return f"Customer reports duplicate payment. Matching history shows repeated {amount} BDT payment activity to {cp}; suspected duplicate is {tid}."
    if case_type == "merchant_settlement_delay":
        return f"Merchant reports delayed settlement {tid} for {amount} BDT. Transaction status is {status}."
    if case_type == "agent_cash_in_issue":
        return f"Customer reports {amount} BDT cash-in via {cp} ({tid}) not reflected in balance. Transaction status is {status}."
    if case_type == "refund_request":
        return f"Customer requests refund review for {amount} BDT {tx_type} transaction {tid}. Eligibility depends on policy and evidence."
    return f"Customer complaint references {tx_type} transaction {tid} for {amount} BDT."


def build_next_action(case_type: CaseType, match: MatchResult) -> str:
    tid = match.transaction_id or "the transaction"
    if case_type == "phishing_or_social_engineering":
        return "Escalate to fraud_risk, record the reported contact details if available, and remind the customer that official support never asks for credentials."
    if case_type == "wrong_transfer":
        if match.verdict == "insufficient_data":
            return "Ask for the recipient number or transaction ID before opening a dispute."
        if match.verdict == "inconsistent":
            return f"Flag {tid} for human review and verify whether the established recipient pattern contradicts the wrong-transfer claim."
        return f"Verify {tid} details and initiate the wrong-transfer dispute workflow per policy."
    if case_type == "payment_failed":
        return f"Investigate {tid} ledger status and trigger the standard reversal flow only if eligibility is confirmed."
    if case_type == "duplicate_payment":
        return f"Verify suspected duplicate {tid} with payments_ops and biller records before any reversal action."
    if case_type == "merchant_settlement_delay":
        return f"Route {tid} to merchant_operations to verify batch status and communicate the expected update through official channels."
    if case_type == "agent_cash_in_issue":
        return f"Investigate {tid} with agent_operations and confirm settlement state under the cash-in SLA."
    if case_type == "refund_request":
        return f"Review {tid} against merchant or refund policy and guide the customer through official support channels."
    return "Ask the customer for transaction ID, amount, approximate time, and a short description of what went wrong."


SAFE_REPLY_EN: dict[str, str] = {
    "phishing_or_social_engineering": "Thank you for reporting this. We never ask for your PIN, OTP, password, or card details. Keep them private and our fraud risk team will review the incident through official channels.",
    "wrong_transfer": "We have noted your concern about transaction {txn}. Our dispute team will review the evidence and contact you through official support channels. Please keep your PIN and OTP private.",
    "wrong_transfer_ambiguous": "Thank you for reaching out. We need the recipient number or transaction ID to identify the correct transfer. Please keep your PIN and OTP private.",
    "payment_failed": "We have noted the issue with transaction {txn}. Our payments team will review the ledger, and any eligible amount will be handled through official channels. Please keep your PIN and OTP private.",
    "duplicate_payment": "We have noted the possible duplicate payment for transaction {txn}. Our payments team will verify the records, and any eligible amount will be handled through official channels. Please keep your PIN and OTP private.",
    "merchant_settlement_delay": "We have noted your settlement concern for {txn}. Our merchant operations team will check the batch status and update you through official channels.",
    "agent_cash_in_issue": "We have noted your cash-in concern for transaction {txn}. Our agent operations team will verify the status and update you through official channels. Please keep your PIN and OTP private.",
    "refund_request": "Thank you for reaching out. Refund eligibility depends on the applicable merchant or service policy. We will guide you through official support channels. Please keep your PIN and OTP private.",
    "other": "Thank you for reaching out. Please share the transaction ID, amount, approximate time, and what went wrong so we can check accurately. Please keep your PIN and OTP private.",
}

SAFE_REPLY_BN: dict[str, str] = {
    "phishing_or_social_engineering": "\u09a7\u09a8\u09cd\u09af\u09ac\u09be\u09a6\u0964 \u0986\u09ae\u09b0\u09be \u0995\u0996\u09a8\u0993 \u0986\u09aa\u09a8\u09be\u09b0 \u09aa\u09bf\u09a8, \u0993\u099f\u09bf\u09aa\u09bf \u09ac\u09be \u09aa\u09be\u09b8\u0993\u09af\u09bc\u09be\u09b0\u09cd\u09a1 \u099a\u09be\u0987 \u09a8\u09be\u0964 \u098f\u0997\u09c1\u09b2\u09cb \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8; \u0986\u09ae\u09be\u09a6\u09c7\u09b0 \u09ab\u09cd\u09b0\u09a1 \u09b0\u09bf\u09b8\u09cd\u0995 \u099f\u09bf\u09ae \u09ac\u09bf\u09b7\u09af\u09bc\u099f\u09bf \u09aa\u09b0\u09cd\u09af\u09be\u09b2\u09cb\u099a\u09a8\u09be \u0995\u09b0\u09ac\u09c7\u0964",
    "wrong_transfer": "\u0986\u09aa\u09a8\u09be\u09b0 \u09b2\u09c7\u09a8\u09a6\u09c7\u09a8 {txn} \u09a8\u09bf\u09af\u09bc\u09c7 \u0985\u09ad\u09bf\u09af\u09cb\u0997\u099f\u09bf \u0986\u09ae\u09b0\u09be \u09a8\u09cb\u099f \u0995\u09b0\u09c7\u099b\u09bf\u0964 \u09a1\u09bf\u09b8\u09aa\u09bf\u0989\u099f \u099f\u09bf\u09ae \u09aa\u09cd\u09b0\u09ae\u09be\u09a3 \u09af\u09be\u099a\u09be\u0987 \u0995\u09b0\u09c7 \u0985\u09ab\u09bf\u09b8\u09bf\u09af\u09bc\u09be\u09b2 \u099a\u09cd\u09af\u09be\u09a8\u09c7\u09b2\u09c7 \u0986\u09aa\u09a1\u09c7\u099f \u09a6\u09c7\u09ac\u09c7\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
    "wrong_transfer_ambiguous": "\u09a7\u09a8\u09cd\u09af\u09ac\u09be\u09a6\u0964 \u09b8\u09a0\u09bf\u0995 \u099f\u09cd\u09b0\u09be\u09a8\u09cd\u09b8\u09ab\u09be\u09b0 \u099a\u09bf\u09b9\u09cd\u09a8\u09bf\u09a4 \u0995\u09b0\u09a4\u09c7 \u09aa\u09cd\u09b0\u09be\u09aa\u0995\u09c7\u09b0 \u09a8\u09ae\u09cd\u09ac\u09b0 \u09ac\u09be \u09b2\u09c7\u09a8\u09a6\u09c7\u09a8 \u0986\u0987\u09a1\u09bf \u09aa\u09cd\u09b0\u09af\u09bc\u09cb\u099c\u09a8\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
    "payment_failed": "\u09b2\u09c7\u09a8\u09a6\u09c7\u09a8 {txn} \u09a8\u09bf\u09af\u09bc\u09c7 \u0986\u09aa\u09a8\u09be\u09b0 \u09b8\u09ae\u09b8\u09cd\u09af\u09be\u099f\u09bf \u0986\u09ae\u09b0\u09be \u09a8\u09cb\u099f \u0995\u09b0\u09c7\u099b\u09bf\u0964 \u09aa\u09c7\u09ae\u09c7\u09a8\u09cd\u099f\u09b8 \u099f\u09bf\u09ae \u09b2\u09c7\u099c\u09be\u09b0 \u09af\u09be\u099a\u09be\u0987 \u0995\u09b0\u09ac\u09c7; \u09af\u09cb\u0997\u09cd\u09af \u0985\u09b0\u09cd\u09a5 \u0985\u09ab\u09bf\u09b8\u09bf\u09af\u09bc\u09be\u09b2 \u099a\u09cd\u09af\u09be\u09a8\u09c7\u09b2\u09c7 \u09b9\u09cd\u09af\u09be\u09a8\u09cd\u09a1\u09b2 \u0995\u09b0\u09be \u09b9\u09ac\u09c7\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
    "duplicate_payment": "\u09b2\u09c7\u09a8\u09a6\u09c7\u09a8 {txn} \u09a8\u09bf\u09af\u09bc\u09c7 \u09b8\u09ae\u09cd\u09ad\u09be\u09ac\u09cd\u09af \u09a1\u09c1\u09aa\u09cd\u09b2\u09bf\u0995\u09c7\u099f \u09aa\u09c7\u09ae\u09c7\u09a8\u09cd\u099f \u0986\u09ae\u09b0\u09be \u09a8\u09cb\u099f \u0995\u09b0\u09c7\u099b\u09bf\u0964 \u09aa\u09c7\u09ae\u09c7\u09a8\u09cd\u099f\u09b8 \u099f\u09bf\u09ae \u09b0\u09c7\u0995\u09b0\u09cd\u09a1 \u09af\u09be\u099a\u09be\u0987 \u0995\u09b0\u09ac\u09c7\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
    "merchant_settlement_delay": "\u09b8\u09c7\u099f\u09c7\u09b2\u09ae\u09c7\u09a8\u09cd\u099f {txn} \u09a8\u09bf\u09af\u09bc\u09c7 \u0986\u09aa\u09a8\u09be\u09b0 \u0989\u09a6\u09cd\u09ac\u09c7\u0997 \u0986\u09ae\u09b0\u09be \u09a8\u09cb\u099f \u0995\u09b0\u09c7\u099b\u09bf\u0964 \u09ae\u09be\u09b0\u09cd\u099a\u09c7\u09a8\u09cd\u099f \u0985\u09aa\u09be\u09b0\u09c7\u09b6\u09a8\u09b8 \u099f\u09bf\u09ae \u09ac\u09cd\u09af\u09be\u099a \u09b8\u09cd\u099f\u09cd\u09af\u09be\u099f\u09be\u09b8 \u09af\u09be\u099a\u09be\u0987 \u0995\u09b0\u09c7 \u0985\u09ab\u09bf\u09b8\u09bf\u09af\u09bc\u09be\u09b2 \u099a\u09cd\u09af\u09be\u09a8\u09c7\u09b2\u09c7 \u0986\u09aa\u09a1\u09c7\u099f \u09a6\u09c7\u09ac\u09c7\u0964",
    "agent_cash_in_issue": "\u09b2\u09c7\u09a8\u09a6\u09c7\u09a8 {txn} \u09a8\u09bf\u09af\u09bc\u09c7 \u0986\u09aa\u09a8\u09be\u09b0 \u0995\u09cd\u09af\u09be\u09b6 \u0987\u09a8 \u09b8\u09ae\u09b8\u09cd\u09af\u09be\u099f\u09bf \u0986\u09ae\u09b0\u09be \u09a8\u09cb\u099f \u0995\u09b0\u09c7\u099b\u09bf\u0964 \u098f\u099c\u09c7\u09a8\u09cd\u099f \u0985\u09aa\u09be\u09b0\u09c7\u09b6\u09a8\u09b8 \u099f\u09bf\u09ae \u09b8\u09cd\u099f\u09cd\u09af\u09be\u099f\u09be\u09b8 \u09af\u09be\u099a\u09be\u0987 \u0995\u09b0\u09c7 \u0985\u09ab\u09bf\u09b8\u09bf\u09af\u09bc\u09be\u09b2 \u099a\u09cd\u09af\u09be\u09a8\u09c7\u09b2\u09c7 \u0986\u09aa\u09a1\u09c7\u099f \u09a6\u09c7\u09ac\u09c7\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
    "refund_request": "\u09a7\u09a8\u09cd\u09af\u09ac\u09be\u09a6\u0964 \u09b0\u09bf\u09ab\u09be\u09a8\u09cd\u09a1 \u09af\u09cb\u0997\u09cd\u09af\u09a4\u09be \u09b8\u0982\u09b6\u09cd\u09b2\u09bf\u09b7\u09cd\u099f \u09ae\u09be\u09b0\u09cd\u099a\u09c7\u09a8\u09cd\u099f \u09ac\u09be \u09b8\u09be\u09b0\u09cd\u09ad\u09bf\u09b8 \u09a8\u09c0\u09a4\u09bf\u09b0 \u0989\u09aa\u09b0 \u09a8\u09bf\u09b0\u09cd\u09ad\u09b0 \u0995\u09b0\u09c7\u0964 \u0986\u09ae\u09b0\u09be \u0985\u09ab\u09bf\u09b8\u09bf\u09af\u09bc\u09be\u09b2 \u099a\u09cd\u09af\u09be\u09a8\u09c7\u09b2\u09c7 \u09a8\u09bf\u09b0\u09cd\u09a6\u09c7\u09b6\u09a8\u09be \u09a6\u09c7\u09ac\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
    "other": "\u09a7\u09a8\u09cd\u09af\u09ac\u09be\u09a6\u0964 \u09b8\u09a0\u09bf\u0995\u09ad\u09be\u09ac\u09c7 \u09af\u09be\u099a\u09be\u0987 \u0995\u09b0\u09a4\u09c7 \u09b2\u09c7\u09a8\u09a6\u09c7\u09a8 \u0986\u0987\u09a1\u09bf, \u09aa\u09b0\u09bf\u09ae\u09be\u09a3, \u0986\u09a8\u09c1\u09ae\u09be\u09a8\u09bf\u0995 \u09b8\u09ae\u09af\u09bc \u098f\u09ac\u0982 \u09b8\u09ae\u09b8\u09cd\u09af\u09be\u099f\u09bf \u099c\u09be\u09a8\u09be\u09a8\u0964 \u09aa\u09bf\u09a8 \u0993 \u0993\u099f\u09bf\u09aa\u09bf \u0997\u09cb\u09aa\u09a8 \u09b0\u09be\u0996\u09c1\u09a8\u0964",
}


def _is_safe(text: str) -> bool:
    lower = text.lower()
    return bool(text.strip()) and not any(phrase in lower for phrase in FORBIDDEN_REPLY_PHRASES)


def safety_validate(text: str, language: str) -> str:
    if _is_safe(text):
        return text
    return SAFE_REPLY_BN["other"] if language == "bn" else SAFE_REPLY_EN["other"]


def build_customer_reply(case_type: CaseType, match: MatchResult, language: str) -> str:
    key = case_type
    if case_type == "wrong_transfer" and match.verdict == "insufficient_data":
        key = "wrong_transfer_ambiguous"
    templates = SAFE_REPLY_BN if language == "bn" else SAFE_REPLY_EN
    template = templates.get(key, templates["other"])
    txn_id = match.transaction_id
    if "{txn}" in template:
        text = template.replace("{txn}", txn_id or "the relevant transaction")
    else:
        text = template
    return safety_validate(text, language)


def build_reason_codes(case_type: CaseType, match: MatchResult, info: ExtractedInfo) -> list[str]:
    codes = [case_type]
    codes.extend(match.reasons)
    if match.verdict == "consistent":
        codes.append("transaction_match")
    elif match.verdict == "inconsistent":
        codes.append("evidence_inconsistent")
    else:
        codes.append("insufficient_data")
    if info.prompt_injection_detected:
        codes.append("prompt_injection_ignored")
    if case_type == "phishing_or_social_engineering":
        codes.extend(["credential_protection", "critical_escalation"])
    return list(dict.fromkeys(codes))


def _validate_history(history: Any) -> list[dict[str, Any]]:
    if history is None:
        return []
    if not isinstance(history, list):
        raise ValueError("transaction_history must be a list")
    cleaned: list[dict[str, Any]] = []
    for index, txn in enumerate(history):
        if not isinstance(txn, dict):
            raise ValueError(f"transaction_history[{index}] must be an object")
        cleaned.append(txn)
    return cleaned


def _polish_with_gemini(
    customer_reply: str,
    agent_summary: str,
    case_type: CaseType,
    severity: Severity,
    department: Department,
    match: MatchResult,
    language: str,
) -> tuple[str, str]:
    try:
        import gemini_fallback

        if not gemini_fallback.is_available():
            return customer_reply, agent_summary
        polished = gemini_fallback.polish(
            base_reply=customer_reply,
            base_summary=agent_summary,
            case_type=case_type,
            severity=severity,
            department=department,
            evidence_verdict=match.verdict,
            txn_id=match.transaction_id,
            language=language,
        )
        if not polished:
            return customer_reply, agent_summary
        new_reply, new_summary = polished
        if _is_safe(new_reply) and _is_safe(new_summary):
            return new_reply, new_summary
    except Exception:
        pass
    return customer_reply, agent_summary


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the complete architecture-defined investigation pipeline."""
    ticket_id = str(payload.get("ticket_id") or "").strip()
    complaint = str(payload.get("complaint") or "").strip()
    user_type = str(payload.get("user_type") or "customer").strip() or "customer"
    history = _validate_history(payload.get("transaction_history") or [])

    if not ticket_id:
        raise ValueError("ticket_id is required")
    if not complaint:
        raise ValueError("complaint is required")

    info = extract_information(complaint, payload.get("language"))
    case_type = classify_case(info, user_type)
    match = match_transactions(history, info, case_type)

    if case_type == "other" and not history:
        match = MatchResult(None, None, "insufficient_data", [], ["no_transaction_history"])

    department = route_department(case_type)
    severity = assess_severity(case_type, match, info)
    human_review = needs_human_review(case_type, severity, match)
    confidence = calculate_confidence(case_type, match, info)
    agent_summary = build_agent_summary(case_type, match, info)
    next_action = build_next_action(case_type, match)
    customer_reply = build_customer_reply(case_type, match, info.language)
    reason_codes = build_reason_codes(case_type, match, info)

    customer_reply, agent_summary = _polish_with_gemini(
        customer_reply,
        agent_summary,
        case_type,
        severity,
        department,
        match,
        info.language,
    )
    customer_reply = safety_validate(customer_reply, info.language)
    agent_summary = safety_validate(agent_summary, "en") if not _is_safe(agent_summary) else agent_summary

    return {
        "ticket_id": ticket_id,
        "relevant_transaction_id": match.transaction_id,
        "evidence_verdict": match.verdict,
        "case_type": case_type,
        "severity": severity,
        "department": department,
        "agent_summary": agent_summary,
        "recommended_next_action": next_action,
        "customer_reply": customer_reply,
        "human_review_required": human_review,
        "confidence": round(confidence, 2),
        "reason_codes": reason_codes,
    }
