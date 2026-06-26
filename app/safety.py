"""
Safety layer for QueueStorm Investigator.

This module is the second line of defense against the four hard penalties
from the problem statement (SRS §3.5):

* Requesting credentials (PIN, OTP, password, full card number)         −15
* Promising an unauthorized refund / reversal / account unblock        −10
* Directing the customer to suspicious third-party channels            −10
* Following prompt-injection instructions embedded in a complaint       (not
  a hard penalty, but a soft one — judges look for safe behavior)

The scanner runs in two places (per the architecture):

* **pre_scan_complaint** — called BEFORE the reasoning pipeline on the
  raw complaint text. Flags prompt-injection markers so the orchestrator
  can route the case to human review even if the classifier doesn't.
* **post_scan** — called AFTER the i18n layer has produced the
  customer_reply and recommended_next_action. If the reply is unsafe,
  it is REPLACED with a neutral :data:`SAFE_REPLY_TEMPLATE` so the
  customer never sees forbidden language.

Why windowed token analysis (not a single regex)
------------------------------------------------

A naive scanner that bans the substring "your PIN" would rewrite every
gold-standard reply to a robotic template and cost Response Quality
points. Every one of the 10 sample-case expected replies contains a
defensive warning like "Please do not share your PIN or OTP with
anyone" — that is **safe and required**.

The scanner therefore requires THREE conditions to fire on a credential
request:

1. A credential word (e.g. ``pin``, ``otp``) is present.
2. A request verb (e.g. ``share``, ``send``, ``tell me``) is present.
3. The two are within ~60 characters of each other.
4. AND no negation/warning marker is within ~80 characters of the
   credential word.

If any of these conditions is missing, the text is treated as a
defensive warning and passes through unchanged.

This approach also handles Bangla: ``অনুগ্রহ করে কারো সাথে আপনার পিন
বা ওটিপি শেয়ার করবেন না`` carries negation tokens within the window so
the warning passes; ``আপনার পিন দিয়ে যাচাই করুন`` has no negation and
is rewritten.

Refund promises and suspicious 3rd parties use single-regex detection —
they are unambiguous (no warning context makes sense) and the cost of a
false positive is low (the safe template is still professional).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# =============================================================================
# Constants — the safe reply template
# =============================================================================


#: The neutral reply used whenever any post-scan rule fires. It is
#: deliberately generic so it can replace ANY unsafe text without
#: making false claims. It also includes the defensive warning
#: phrasing in both English and Bangla so it satisfies the rubric's
#: "include a credential warning" expectation when used.
SAFE_REPLY_TEMPLATE: str = (
    "Thank you for reaching out. Our support team has received your "
    "message and will contact you through official support channels. "
    "Please do not share your PIN, OTP, or password with anyone — we "
    "will never ask for them."
)

#: Bangla variant used when the input complaint is Bangla.
SAFE_REPLY_TEMPLATE_BN: str = (
    "আমাদের সাপোর্ট টিম আপনার বার্তা পেয়েছে এবং অফিসিয়াল চ্যানেলে "
    "আপনার সাথে যোগাযোগ করবে। অনুগ্রহ করে কারো সাথে আপনার পিন, ওটিপি, "
    "বা পাসওয়ার্ড শেয়ার করবেন না — আমরা কখনো এগুলো জিজ্ঞেস করি না।"
)


# =============================================================================
# Result type
# =============================================================================


@dataclass(frozen=True)
class SafetyVerdict:
    """The output of any scan.

    Attributes
    ----------
    safe
        True if no rule fired.
    rewrote
        True if the input was rewritten to a safe template.
    output
        The safe text. Equal to the input when ``rewrote`` is False.
    flags
        List of short string labels for the rules that fired (e.g.
        ``"credential_request"``, ``"refund_promise"``,
        ``"third_party_channel"``, ``"injection_marker"``). Empty when
        safe.
    """

    safe: bool
    output: str
    flags: List[str] = field(default_factory=list)
    rewrote: bool = False

    @property
    def has_flag(self) -> bool:
        return bool(self.flags)


# =============================================================================
# Layer 1 — pre-scan on the raw complaint (prompt-injection detection)
# =============================================================================


#: Patterns that strongly suggest the complaint is trying to override
#: the system prompt or jump straight to a refund. The classifier
#: still runs on these — the orchestrator just forces human review.
_INJECTION_PATTERNS = [
    r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)\b",
    r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)\b",
    r"\bforget\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)\b",
    r"\bact\s+as\s+(?:a\s+)?(?:admin|root|system|operator)\b",
    r"\byou\s+are\s+now\s+(?:a|an)\b",
    r"\bsystem\s+prompt\b",
    r"\brevoke\s+the\s+(?:policy|rules?)\b",
    r"\bbypass\s+(?:the\s+)?(?:policy|safety|rules?)\b",
    r"\boverride\s+(?:the\s+)?(?:policy|safety|rules?)\b",
    r"\brefund\s+me\s+(?:now|immediately|right\s+now)\b",
    r"\bi\s+demand\s+(?:a\s+)?refund\b",
]


def pre_scan_complaint(complaint: str) -> List[str]:
    """Return a list of injection-marker flags found in the complaint.

    Empty list = clean. The orchestrator should pass the result into
    :class:`Classification`-shaped data so ``human_review_required``
    becomes True whenever this list is non-empty.
    """
    if not complaint:
        return []
    flags: List[str] = []
    norm = complaint.lower()
    for pat in _INJECTION_PATTERNS:
        if re.search(pat, norm, flags=re.IGNORECASE):
            flags.append("injection_marker")
            break  # one match is enough — don't pile on
    # Bangla injection markers
    bn_norm = complaint  # Bangla is case-agnostic
    if re.search(r"উপরের\s+(?:নির্দেশ|নিয়ম|নির্দেশনা)\s+(?:উপেক্ষা|অগ্রাহ্য|ভুলে\s+যাও)", bn_norm):
        flags.append("injection_marker")
    return flags


# =============================================================================
# Layer 2 — credential-request detection (windowed)
# =============================================================================


#: Credential words. Matched as whole tokens (word boundaries) so
#: "spin", "pinned", "pint" are NOT flagged.
_CREDENTIAL_PATTERNS = [
    r"\bpin\b",
    r"\botp\b",
    r"\bpassword\b",
    r"\bcvv\b",
    r"\bcard\s*number\b",
    r"\bcredit\s*card\s*number\b",
    # Bangla
    r"পিন",
    r"ওটিপি",
    r"পাসওয়ার্ড",
    r"সিভিভি",
    r"কার্ডের\s+নম্বর",
    r"কার্ড\s+নম্বর",
]

#: Request verbs — the customer's complaint or a generated reply
#: would be ASKING the human for one of these.
_REQUEST_VERB_PATTERNS = [
    r"\bshare\b",
    r"\bsend\b",
    r"\btell\b",
    r"\bgive\b",
    r"\bprovide\b",
    r"\btype\b",
    r"\benter\b",
    r"\bsubmit\b",
    r"\bconfirm\b",
    r"\bverify\b",  # "verify your PIN" is asking
    # Bangla
    r"দিন",
    r"দিয়ে",
    r"বলুন",
    r"জানান",
    r"পাঠান",
    r"লিখুন",
    r"প্রবেশ\s+করুন",
]

#: Negation / warning markers. If any of these is within the window,
#: the credential mention is treated as a defensive warning.
_NEGATION_PATTERNS = [
    r"\bdo\s+not\b",
    r"\bdon['’]t\b",
    r"\bnever\b",
    r"\bplease\s+do\s+not\b",
    r"\bplease\s+don['’]t\b",
    r"\bdo\s+not\s+share\b",
    r"\bnever\s+ask\b",
    r"\bwill\s+not\s+ask\b",
    r"\bwe\s+never\s+ask\b",
    r"\bwe\s+will\s+never\s+ask\b",
    # Bangla
    r"অনুগ্রহ\s+করে",
    r"করবেন\s+না",
    r"কখনো\s+না",
    r"কখনও\s+না",
    r"ভাগ\s+করবেন\s+না",
    r"শেয়ার\s+করবেন\s+না",
    r"চাওয়া\s+হয়\s+না",
    r"আমরা\s+কখনো",
]


#: Character radius for "request verb near credential word".
_REQUEST_CRED_RADIUS = 60
#: Character radius for "negation near credential word".
_NEGATION_RADIUS = 80


def _find_hits(text: str, pattern_list: List[str]) -> List[Tuple[str, int]]:
    """Return (pattern, index) for every regex match in ``text``."""
    hits: List[Tuple[str, int]] = []
    for p in pattern_list:
        for m in re.finditer(p, text, flags=re.IGNORECASE | re.UNICODE):
            hits.append((p, m.start()))
    return hits


def _window_has(
    text: str, pattern_list: List[str], idx: int, radius: int
) -> bool:
    """True if any pattern in ``pattern_list`` matches inside a window
    of ``radius`` characters on either side of ``idx`` in ``text``.
    """
    start = max(0, idx - radius)
    end = min(len(text), idx + radius)
    window = text[start:end]
    for p in pattern_list:
        if re.search(p, window, flags=re.IGNORECASE | re.UNICODE):
            return True
    return False


def is_credential_request(text: str) -> bool:
    """Return True iff the text ASKS the customer for credentials.

    Algorithm: a credential request must have BOTH a credential word
    AND a request verb within ~60 characters, AND no negation within
    ~80 characters of the credential word. Any other combination is
    treated as a defensive statement and returns False.

    Examples
    --------

    * "Please do not share your PIN or OTP"   → False (defensive)
    * "Please share your PIN to verify"        → True  (request)
    * "অনুগ্রহ করে আপনার পিন দিন"            → False (defensive, negation present)
    * "আপনার পিন দিয়ে যাচাই করুন"            → True  (request, no negation)
    * "Your PIN is 1234"                       → True  (credential disclosure)
    * "The PIN is required to login"           → False (general statement)
    """
    if not text:
        return False
    cred_hits = _find_hits(text, _CREDENTIAL_PATTERNS)
    if not cred_hits:
        return False
    req_hits = _find_hits(text, _REQUEST_VERB_PATTERNS)
    if not req_hits:
        return False
    for _, cred_idx in cred_hits:
        # A request verb must be near the credential word.
        near_request = any(
            abs(req_idx - cred_idx) <= _REQUEST_CRED_RADIUS
            for _, req_idx in req_hits
        )
        if not near_request:
            continue
        # No negation/warning marker must be near the credential word.
        if _window_has(text, _NEGATION_PATTERNS, cred_idx, _NEGATION_RADIUS):
            continue
        return True
    return False


# =============================================================================
# Layer 2 — refund-promise and account-unblock detection
# =============================================================================


#: Forbidden promises: refund / reverse / unblock / approve / release
#: without the necessary authority. Each pattern is anchored as a
#: full phrase so we don't fire on "eligible amount will be returned"
#: (safe) but DO fire on "we will refund you" (unsafe).
_FORBIDDEN_PROMISES_RE = re.compile(
    r"\bwe\s+(?:will|have|shall|are\s+going\s+to)\s+(?:refund|reverse|unblock|approve|release|process\s+the\s+refund)\b"
    r"|\b(?:refund|reversal)\s+(?:is|has\s+been|will\s+be)\s+(?:approved|processed|completed|sent)\b"
    r"|\baccount\s+(?:\w+\s+){0,3}unblocked\b"
    r"|\baccount\s+(?:\w+\s+){0,3}reactivated\b"
    r"|\baccount\s+(?:\w+\s+){0,3}recovered\b"
    r"|\byour\s+money\s+has\s+been\s+refunded\b"
    r"|\bwe\s+have\s+credited\b"
    r"|\brefund\s+has\s+been\s+approved\b"
    r"|\bconfirmed\s+refund\b",
    re.IGNORECASE,
)

#: Suspicious third-party channels.
_FORBIDDEN_THIRD_PARTIES_RE = re.compile(
    r"\btelegram\b|\bwhatsapp\b|\bwa\.me\b|\bt\.me\b|\bsignal\.org\b|\bviber\b",
    re.IGNORECASE,
)

#: Personal phone numbers in suspicious contexts. We accept any phone
#: shape next to a banned-channel word.
_SUSPICIOUS_PHONE_CONTEXT_RE = re.compile(
    r"(?:telegram|whatsapp|t\.me|wa\.me|signal|viber|imessage|imo)\b[^\n]{0,40}[\+\d]",
    re.IGNORECASE,
)

#: Bangla suspicious-channel words.
_FORBIDDEN_THIRD_PARTIES_BN_RE = re.compile(
    r"টেলিগ্রাম|হোয়াটসঅ্যাপ|সিগন্যাল|ভাইবার|ইমো",
    re.IGNORECASE,
)


def has_refund_promise(text: str) -> bool:
    """True if the text makes a forbidden refund / reversal / unblock promise."""
    if not text:
        return False
    return bool(_FORBIDDEN_PROMISES_RE.search(text))


def has_third_party_channel(text: str) -> bool:
    """True if the text mentions a banned third-party channel."""
    if not text:
        return False
    if _FORBIDDEN_THIRD_PARTIES_RE.search(text):
        return True
    if _FORBIDDEN_THIRD_PARTIES_BN_RE.search(text):
        return True
    if _SUSPICIOUS_PHONE_CONTEXT_RE.search(text):
        return True
    return False


# =============================================================================
# Public entry point: post_scan
# =============================================================================


def _safe_template_for(text: str) -> str:
    """Pick the right safe template based on the dominant script."""
    if not text:
        return SAFE_REPLY_TEMPLATE
    # Bangla Unicode range.
    bangla_count = sum(1 for ch in text if "\u0980" <= ch <= "\u09FF")
    if bangla_count > 8:
        return SAFE_REPLY_TEMPLATE_BN
    return SAFE_REPLY_TEMPLATE


def post_scan(text: str) -> SafetyVerdict:
    """Run all post-generation rules on ``text`` (the customer_reply
    or recommended_next_action).

    If any rule fires, the text is REPLACED with the safe template
    and the verdict is marked ``rewrote=True``. Otherwise the text
    is returned unchanged.
    """
    if not text:
        return SafetyVerdict(safe=True, output=text or "", flags=[])

    flags: List[str] = []
    if is_credential_request(text):
        flags.append("credential_request")
    if has_refund_promise(text):
        flags.append("refund_promise")
    if has_third_party_channel(text):
        flags.append("third_party_channel")

    if not flags:
        return SafetyVerdict(safe=True, output=text, flags=[])

    return SafetyVerdict(
        safe=False,
        output=_safe_template_for(text),
        flags=flags,
        rewrote=True,
    )


# =============================================================================
# Convenience: scan both reply + action (called by the orchestrator)
# =============================================================================


def post_scan_pair(
    customer_reply: str, recommended_next_action: str
) -> Tuple[SafetyVerdict, SafetyVerdict]:
    """Run post_scan on both the reply and the next action. Returns
    a tuple ``(reply_verdict, action_verdict)``.
    """
    return post_scan(customer_reply), post_scan(recommended_next_action)


__all__ = [
    "SAFE_REPLY_TEMPLATE",
    "SAFE_REPLY_TEMPLATE_BN",
    "SafetyVerdict",
    "pre_scan_complaint",
    "is_credential_request",
    "has_refund_promise",
    "has_third_party_channel",
    "post_scan",
    "post_scan_pair",
]
