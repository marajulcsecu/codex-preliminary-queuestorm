"""
Multilingual reply templating for QueueStorm Investigator.

The service must produce the three customer-facing strings —
``agent_summary``, ``recommended_next_action``, ``customer_reply`` —
in the same language as the complaint, and every ``customer_reply``
must end with a defensive credential warning ("Please do not share
your PIN or OTP with anyone.") so the post-generation safety scanner
can never rewrite it for missing the warning.

Per SRS §3.6:

* ``language`` is taken from the input if present; otherwise we detect
  from Unicode ranges (Bengali block U+0980–U+09FF → ``bn``; Latin →
  ``en``; both → ``mixed``).
* Customer reply is rendered in the same language.
* Bangla template fragments are prescribed by the spec.
* Banglish is treated as ``mixed`` and replied in Banglish-friendly
  English (short, simple sentences — most Bangladeshis read English
  even when they type Banglish).

Every customer_reply MUST end with the credential warning:

* en:  "...Please do not share your PIN or OTP with anyone."
* bn:  "...অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"

Every customer_reply that mentions money MUST use the safe phrase
"any eligible amount will be returned through official channels"
instead of "we will refund" — the post-scan would rewrite a refund
promise anyway, but using safe language up front is better.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.models import AnalyzeRequest, Language


# =============================================================================
# Defensive credential-warning phrases (required at end of every reply)
# =============================================================================


WARNING_EN = "Please do not share your PIN or OTP with anyone."
WARNING_BN = "অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"


# =============================================================================
# Language detection
# =============================================================================


def detect_language(text: str, declared: Optional[str] = None) -> Language:
    """Detect the dominant script of ``text``.

    Per SRS §3.6: Bengali block (U+0980–U+09FF) → ``bn``; Latin → ``en``;
    both present → ``mixed``.

    The ``declared`` argument is honored if it is one of the three
    enum values — the harness can pass a hint in the request.
    """
    if declared in {"en", "bn", "mixed"}:
        return declared  # type: ignore[return-value]
    if not text:
        return "en"
    bangla = sum(1 for ch in text if "\u0980" <= ch <= "\u09FF")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if bangla > 0 and latin > 0:
        return "mixed"
    if bangla > 0:
        return "bn"
    return "en"


# =============================================================================
# Per-case-type reply templates (en / bn)
# =============================================================================


# Wrong transfer — clean match (English)
_WT_EN_CLEAN = (
    "We have noted your concern about transaction {tx_id}. "
    "Our dispute team will review the case and contact you through "
    "official support channels."
)
# Wrong transfer — established recipient
_WT_EN_ESTABLISHED = (
    "We have received your request regarding transaction {tx_id}. "
    "Our dispute team will review the case carefully and contact you "
    "through official support channels."
)
# Wrong transfer — ambiguous evidence
_WT_EN_AMBIGUOUS = (
    "Thank you for reaching out. We see multiple transactions of that "
    "amount on the date in question. Could you share more details so we "
    "can identify the right transaction?"
)

_WT_BN_CLEAN = (
    "আপনার লেনদেন {tx_id} এর বিষয়ে আমরা অবগত হয়েছি। "
    "আমাদের বিরোধ নিষ্পত্তি দল এটি যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে।"
)

# Payment failed
_PF_EN = (
    "We have noted that transaction {tx_id} may have caused an unexpected "
    "balance deduction. Our payments team will review the case and any "
    "eligible amount will be returned through official channels."
)
_PF_BN = (
    "{tx_id} লেনদেনের ক্ষেত্রে আপনার ব্যালেন্স থেকে ভুলবশত টাকা কেটে নেওয়া "
    "হতে পারে। আমাদের পেমেন্টস টিম বিষয়টি যাচাই করবে এবং অফিসিয়াল চ্যানেলে "
    "আপনাকে জানাবে।"
)

# Refund request (change-of-mind)
_RR_EN = (
    "Thank you for reaching out. Refunds for completed merchant payments "
    "depend on the merchant's own policy. We recommend contacting the "
    "merchant directly. If you need help reaching them, please reply and "
    "we will guide you."
)
_RR_BN = (
    "আপনার বার্তার জন্য ধন্যবাদ। সম্পন্ন মার্চেন্ট পেমেন্টের রিফান্ড মার্চেন্টের "
    "নিজস্ব নীতির উপর নির্ভর করে। আমরা সরাসরি মার্চেন্টের সাথে যোগাযোগ করার পরামর্শ দিচ্ছি।"
)

# Duplicate payment
_DP_EN = (
    "We have noted the possible duplicate payment for transaction {tx_id}. "
    "Our payments team will verify with the biller and any eligible amount "
    "will be returned through official channels."
)
_DP_BN = (
    "{tx_id} লেনদেনে সম্ভাব্য ডুপ্লিকেট পেমেন্ট আমরা লক্ষ্য করেছি। "
    "আমাদের পেমেন্টস টিম বিলারের সাথে যাচাই করবে এবং অফিসিয়াল চ্যানেলে "
    "আপনাকে জানাবে।"
)

# Merchant settlement delay
_MS_EN = (
    "We have noted your concern about settlement {tx_id}. Our merchant "
    "operations team will check the batch status and update you on the "
    "expected settlement time through official channels."
)
_MS_BN = (
    "{tx_id} সেটেলমেন্ট সংক্রান্ত আপনার উদ্বেগ আমরা লক্ষ্য করেছি। "
    "আমাদের মার্চেন্ট অপারেশন্স টিম ব্যাচের অবস্থা যাচাই করে অফিসিয়াল "
    "চ্যানেলে আপনাকে জানাবে।"
)

# Agent cash-in issue
_AC_EN = (
    "We have noted your concern about transaction {tx_id}. Our agent "
    "operations team will verify this quickly and contact you through "
    "official channels."
)
_AC_BN = (
    "আপনার লেনদেন {tx_id} এর বিষয়ে আমরা অবগত হয়েছি। আমাদের এজেন্ট "
    "অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে।"
)

# Phishing — the response reinforces the credential safety rule.
_PHISHING_EN = (
    "Thank you for reaching out before sharing any information. "
    "We never ask for your PIN, OTP, or password under any circumstances. "
    "Please do not share these with anyone, even if they claim to be "
    "from us. Our fraud team has been notified of this incident."
)
_PHISHING_BN = (
    "যেকোনো তথ্য শেয়ার করার আগে আমাদের সাথে যোগাযোগ করার জন্য আপনাকে "
    "ধন্যবাদ। আমরা কখনোই আপনার পিন, ওটিপি বা পাসওয়ার্ড চাই না। "
    "অনুগ্রহ করে এগুলো কারো সাথে শেয়ার করবেন না, এমনকি নিজেকে আমাদের "
    "প্রতিনিধি বললেও। আমাদের ফ্রড টিম এই ঘটনা সম্পর্কে অবহিত হয়েছে।"
)

# Other / insufficient data
_OTHER_EN = (
    "Thank you for reaching out. To help you faster, please share the "
    "transaction ID, the amount involved, and a short description of "
    "what went wrong."
)
_OTHER_BN = (
    "আপনার বার্তার জন্য ধন্যবাদ। দ্রুত সাহায্য করার জন্য, অনুগ্রহ করে "
    "লেনদেনের আইডি, পরিমাণ এবং কী ভুল হয়েছে তার সংক্ষিপ্ত বিবরণ জানান।"
)


# =============================================================================
# Templates keyed by (case_type, language)
# =============================================================================


# Each entry is (customer_reply_template, agent_summary_template, next_action_template).
# The customer_reply template should NOT include the trailing credential warning;
# we always append it. The summary and next-action templates include all info.

_EN_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "wrong_transfer": (
        _WT_EN_CLEAN + " " + WARNING_EN,
        "Customer reports an issue with transaction {tx_id} that may have been sent to the wrong recipient. The case has been flagged for the dispute team.",
        "Verify {tx_id} with the customer and initiate the wrong-transfer dispute workflow per policy.",
    ),
    "payment_failed": (
        _PF_EN + " " + WARNING_EN,
        "Customer attempted a payment via {tx_id} which failed, but reports the balance was deducted. Requires payments operations investigation.",
        "Investigate {tx_id} ledger status. If balance was deducted on a failed payment, initiate the automatic reversal flow within standard SLA.",
    ),
    "refund_request": (
        _RR_EN + " " + WARNING_EN,
        "Customer requests a refund for transaction {tx_id}. Refund eligibility depends on the merchant's own policy.",
        "Inform the customer that refund eligibility depends on the merchant's own policy. Provide guidance on contacting the merchant directly.",
    ),
    "duplicate_payment": (
        _DP_EN + " " + WARNING_EN,
        "Customer reports a possible duplicate payment involving {tx_id}. Two identical payments were completed close together; the suspected duplicate is the second one.",
        "Verify the duplicate with payments_ops. If the biller confirms only one payment was received, initiate reversal of {tx_id}.",
    ),
    "merchant_settlement_delay": (
        _MS_EN + " " + WARNING_EN,
        "Merchant reports that settlement {tx_id} is delayed beyond the standard window. Settlement status is pending.",
        "Route to merchant_operations to verify settlement batch status. If the batch is delayed, communicate a revised ETA to the merchant.",
    ),
    "agent_cash_in_issue": (
        _AC_EN + " " + WARNING_EN,
        "Customer reports a cash-in via {tx_id} not reflected in the balance. Transaction status is pending.",
        "Investigate {tx_id} pending status with agent operations. Confirm settlement state and resolve within the standard cash-in SLA.",
    ),
    "phishing_or_social_engineering": (
        _PHISHING_EN,
        "Customer reports an unsolicited contact requesting credentials. No credentials have been shared. Likely social-engineering attempt.",
        "Escalate to fraud_risk team immediately. Confirm to customer that the company never asks for OTP. Log the reported number for fraud pattern analysis.",
    ),
    "other": (
        _OTHER_EN + " " + WARNING_EN,
        "Customer reported a concern but provided insufficient detail to identify a specific transaction or case type. Needs clarification.",
        "Reply to customer asking for specific details: which transaction, what amount, what went wrong, and approximate time.",
    ),
}


_BN_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "wrong_transfer": (
        _WT_BN_CLEAN + " " + WARNING_BN,
        "গ্রাহক লেনদেন {tx_id} নিয়ে অভিযোগ করেছেন যা ভুল প্রাপকের কাছে পাঠানো হতে পারে। বিরোধ নিষ্পত্তি দল এটি পর্যালোচনা করবে।",
        "{tx_id} গ্রাহকের সাথে যাচাই করুন এবং ভুল-ট্রান্সফার বিরোধ নিষ্পত্তি কর্মপ্রবাহ শুরু করুন।",
    ),
    "payment_failed": (
        _PF_BN + " " + WARNING_BN,
        "গ্রাহক {tx_id} পেমেন্ট করার চেষ্টা করেছিলেন যা ব্যর্থ হয়েছে, কিন্তু ব্যালেন্স কেটে নেওয়া হয়েছে বলে জানিয়েছেন। পেমেন্টস অপারেশন্স তদন্ত প্রয়োজন।",
        "{tx_id} লেজারের অবস্থা তদন্ত করুন। ব্যর্থ পেমেন্টে ব্যালেন্স কাটা হলে স্বয়ংক্রিয় রিভার্সাল প্রবাহ শুরু করুন।",
    ),
    "refund_request": (
        _RR_BN + " " + WARNING_BN,
        "গ্রাহক {tx_id} লেনদেনের জন্য রিফান্ডের অনুরোধ করেছেন। রিফান্ড যোগ্যতা মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে।",
        "গ্রাহককে জানান যে রিফান্ড যোগ্যতা মার্চেন্টের নিজস্ব নীতির উপর নির্ভর করে। মার্চেন্টের সাথে সরাসরি যোগাযোগের নির্দেশনা দিন।",
    ),
    "duplicate_payment": (
        _DP_BN + " " + WARNING_BN,
        "গ্রাহক {tx_id} সম্পর্কিত সম্ভাব্য ডুপ্লিকেট পেমেন্টের রিপোর্ট করেছেন।",
        "পেমেন্টস অপারেশন্স দলের সাথে ডুপ্লিকেট যাচাই করুন। বিলার শুধুমাত্র একটি পেমেন্ট পেয়ে থাকলে {tx_id} এর রিভার্সাল শুরু করুন।",
    ),
    "merchant_settlement_delay": (
        _MS_BN + " " + WARNING_BN,
        "মার্চেন্ট রিপোর্ট করেছেন যে {tx_id} সেটেলমেন্ট স্ট্যান্ডার্ড সময়সীমার বাইরে বিলম্বিত হয়েছে।",
        "সেটেলমেন্ট ব্যাচের অবস্থা যাচাই করতে merchant_operations এ রাউট করুন।",
    ),
    "agent_cash_in_issue": (
        _AC_BN + " " + WARNING_BN,
        "গ্রাহক রিপোর্ট করেছেন যে {tx_id} ক্যাশ-ইন ব্যালেন্সে প্রতিফলিত হয়নি। লেনদেনের অবস্থা পেন্ডিং।",
        "এজেন্ট অপারেশন্সের সাথে {tx_id} পেন্ডিং অবস্থা তদন্ত করুন। স্ট্যান্ডার্ড ক্যাশ-ইন SLA এর মধ্যে সমাধান করুন।",
    ),
    "phishing_or_social_engineering": (
        _PHISHING_BN,
        "গ্রাহক অযাচিত যোগাযোগের রিপোর্ট করেছেন যা শংসাপত্র চেয়েছে। কোনও শংসাপত্র শেয়ার করা হয়নি। সম্ভাব্য সোশ্যাল ইঞ্জিনিয়ারিং প্রচেষ্টা।",
        "অবিলম্বে fraud_risk টিমে এসকেলেট করুন। গ্রাহককে নিশ্চিত করুন যে কোম্পানি কখনো ওটিপি চায় না। রিপোর্ট করা নম্বরটি ফ্রড প্যাটার্ন বিশ্লেষণে লগ করুন।",
    ),
    "other": (
        _OTHER_BN + " " + WARNING_BN,
        "গ্রাহক একটি উদ্বেগ জানিয়েছেন কিন্তু নির্দিষ্ট লেনদেন বা কেস টাইপ চিহ্নিত করার জন্য অপর্যাপ্ত বিবরণ দিয়েছেন।",
        "গ্রাহককে নির্দিষ্ট বিবরণ জানতে বলুন: কোন লেনদেন, কত পরিমাণ, কী ভুল হয়েছে, এবং আনুমানিক সময়।",
    ),
}


# Mixed / Banglish — fall back to simple English. Per the SRS spec.
def _mixed_template(case_type: str) -> tuple[str, str, str]:
    """Banglish is replied in plain English (most readers understand)."""
    return _EN_TEMPLATES[case_type]


# =============================================================================
# Public entry point
# =============================================================================


@dataclass(frozen=True)
class Reply:
    """The three customer-facing strings."""

    agent_summary: str
    recommended_next_action: str
    customer_reply: str


def draft_reply(
    case_type: str,
    language: Language,
    ticket_id: str,
    tx_id: Optional[str] = None,
    severity: str = "low",
    reason_codes: Optional[List[str]] = None,
) -> Reply:
    """Pick the right template and return the three strings.

    Parameters
    ----------
    case_type
        One of the 8 case_type enum values.
    language
        One of "en", "bn", "mixed".
    ticket_id
        The customer's ticket ID (echoed in agent_summary for traceability).
    tx_id
        The relevant transaction ID, or None for ambiguous / other cases.
        Templates use ``{tx_id}`` placeholders.
    severity
        Currently informational; reserved for future tone adjustments.
    reason_codes
        Optional list of reason codes from the classifier (for traceability).

    Returns
    -------
    Reply
        Three strings, all non-empty.
    """
    tx_placeholder = tx_id or "your recent transaction"

    # Pick the language bucket.
    if language == "bn":
        bucket = _BN_TEMPLATES
    elif language == "mixed":
        # Banglish-friendly English
        return _format(_EN_TEMPLATES[case_type], ticket_id, tx_placeholder)
    else:
        bucket = _EN_TEMPLATES

    template = bucket.get(case_type) or bucket["other"]
    return _format(template, ticket_id, tx_placeholder)


def _format(
    template: tuple[str, str, str],
    ticket_id: str,
    tx_placeholder: str,
) -> Reply:
    """Format the template with {tx_id} and {ticket_id} placeholders.

    Uses ``format_map`` with a default dict so an unknown placeholder
    becomes the literal text rather than raising ``KeyError``. This is
    defense-in-depth: if a template has a typo, the customer still gets
    a response, just with the placeholder word visible.
    """
    fmt_map = {"tx_id": tx_placeholder, "ticket_id": ticket_id}
    reply_t, summary_t, next_t = template
    reply_text = reply_t.format_map(_SafeDict(fmt_map))
    summary_text = summary_t.format_map(_SafeDict(fmt_map))
    next_text = next_t.format_map(_SafeDict(fmt_map))
    return Reply(
        agent_summary=summary_text,
        recommended_next_action=next_text,
        customer_reply=reply_text,
    )


class _SafeDict(dict):
    """A dict that returns the literal key for missing entries."""

    def __missing__(self, key: str) -> str:  # type: ignore[override]
        return "{" + key + "}"


def reply_for_request(
    request: AnalyzeRequest,
    case_type: str,
    tx_id: Optional[str] = None,
) -> Reply:
    """Convenience: detect language from the request, then draft."""
    lang = detect_language(request.complaint or "", request.language)
    return draft_reply(
        case_type=case_type,
        language=lang,
        ticket_id=request.ticket_id,
        tx_id=tx_id,
    )


__all__ = [
    "WARNING_EN",
    "WARNING_BN",
    "Reply",
    "detect_language",
    "draft_reply",
    "reply_for_request",
]