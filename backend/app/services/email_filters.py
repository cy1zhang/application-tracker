"""Hard filters: drop mail that is clearly not about the owner's applications.

Precision first: when in doubt, keep the email. Rules only apply to automated
or known-service senders, and any application wording vetoes a match.
"""

import re
from email.utils import parseaddr
from typing import Literal

from app.services.gmail_message import ParsedGmailMessage

FilterReason = Literal["job_alert", "digest", "newsletter", "marketing", "event"]

VETO_BODY_CHARS = 1000

# Application wording in the subject or the start of the body keeps the email.
VETO_PATTERNS = re.compile(
    r"your application|thank you for applying|interview|assessment|\boffers?\b"
    r"|unfortunately|regret to inform|not selected|not be moving forward"
    r"|moving forward with other|other candidates|next steps|application status"
    r"|coding challenge|hackerrank|codesignal",
    re.IGNORECASE,
)

# (local-part regex, domain suffix or None for any domain, reason): the sender
# alone identifies the mail. Only known job-board domains; generic senders
# need a subject template match as well.
SENDER_RULES: tuple[tuple[str, str | None, FilterReason], ...] = (
    (r"jobalerts?[-_.]?(noreply|no-reply)?", "linkedin.com", "job_alert"),
    (r"jobs-(listings|noreply)", "linkedin.com", "job_alert"),
    (r"alerts?", "indeed.com", "job_alert"),
    (r"(donotreply|noreply|no-reply)", "indeed.com", "job_alert"),
    (r"jobalerts?", "glassdoor.com", "job_alert"),
    (r"alerts?", "ziprecruiter.com", "job_alert"),
    (r"(digest|updates|recommendations)", "joinhandshake.com", "digest"),
)

# Local parts of automated mailboxes; subject rules only apply to these or to
# known service senders in SENDER_RULES, never to a person's address.
AUTOMATED_LOCAL_PART = re.compile(
    r"(no-?reply|do-?not-?reply|notifications?|updates|mailer|"
    r"(job)?alerts?|digest|newsletters?|marketing|promotions?|events?|webinars?)",
    re.IGNORECASE,
)

# (subject regex, reason), first match wins.
SUBJECT_RULES: tuple[tuple[str, FilterReason], ...] = (
    (
        (
            r"\b\d+ new jobs?\b|jobs? you may be interested in|jobs? picked for you"
            r"|recommended jobs|job recommendations"
        ),
        "digest",
    ),
    (r"\b(weekly|daily|monthly) (digest|roundup|summary)\b|\bdigest\b", "digest"),
    (
        (
            r"\bjob alerts?\b|\bnew jobs?\b.*\bfor you\b|\bjobs? near you\b"
            r"|\bnew [\w ]*jobs\b"
        ),
        "job_alert",
    ),
    (r"\bnewsletter\b", "newsletter"),
    (r"\bwebinar\b|you'?re invited|\bevent\b|\bcareer fair\b", "event"),
    (r"\bsale\b|\bsavings\b|\bdiscount\b|\bpromo(tion)?\b", "marketing"),
)

_SENDER_RULES = tuple(
    (re.compile(local, re.IGNORECASE), domain, r) for local, domain, r in SENDER_RULES
)
_SUBJECT_RULES = tuple((re.compile(p, re.IGNORECASE), r) for p, r in SUBJECT_RULES)


def _domain_matches(domain: str, suffix: str) -> bool:
    return domain == suffix or domain.endswith("." + suffix)


def _split_address(sender: str) -> tuple[str, str]:
    address = parseaddr(sender)[1].lower()
    local, _, domain = address.rpartition("@")
    return local, domain


def filter_reason(message: ParsedGmailMessage) -> FilterReason | None:
    if VETO_PATTERNS.search(message.subject) or VETO_PATTERNS.search(
        message.text[:VETO_BODY_CHARS]
    ):
        return None
    local, domain = _split_address(message.sender)
    if not local or not domain:
        return None

    automated = bool(AUTOMATED_LOCAL_PART.fullmatch(local))
    # Subject templates on a service sender take precedence over its default.
    if automated or _is_service_local(local, domain):
        for pattern, reason in _SUBJECT_RULES:
            if pattern.search(message.subject):
                return reason

    for pattern, suffix, reason in _SENDER_RULES:
        if pattern.fullmatch(local) and (suffix is None or _domain_matches(domain, suffix)):
            return reason
    return None


def _is_service_local(local: str, domain: str) -> bool:
    return any(pattern.fullmatch(local) and suffix and _domain_matches(domain, suffix)
               for pattern, suffix, _ in _SENDER_RULES)


# Parse failures have no trustworthy body. Use only narrow subject/sender pairs
# for obviously unrelated mail; unfamiliar messages stay visible for inspection.
_UNPARSEABLE_NOISE: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (domain, re.compile(subject, re.IGNORECASE))
    for domain, subject in (
        ("nytimes.com", r"^The Morning:"),
        ("nvidia.com", r"^(?:Your Personalized Developer News|Learn for Free|Six new AI courses)"),
        ("amazon.com", r"^(?:Delivered \d+ items?|Ordered \d+ items?|Shipped \d+ items?|Psst, your Prime Big Deal Days)"),
        ("business.amazon.com", r"^(?:Prime Big Deal Days|.{0,80}Amazon Business offers business-only pricing)"),
        ("news.paypal.com", r"^Shop sales"),
        ("e.zoom.us", r"^Enhance your experience with Zoom"),
        ("substack.com", r'^[“"]'),
        ("email.viz.com", r"\bBlu-ray\b"),
        ("searchdoc.live", r"\bYou Are Our Winner\b"),
        ("jobright.ai", r"^Welcome to Jobright!"),
        ("fidelity.com", r"^Upgrade your resume with these tips"),
    )
)
_UNPARSEABLE_SPAM = re.compile(
    r"^we(?: have|'ve) blocked your account!? .{0,80}photos and videos will be deleted",
    re.IGNORECASE,
)


def filter_unparseable_headers(subject: str | None, sender: str | None) -> bool:
    if not subject or not sender:
        return False
    domain = _split_address(sender)[1]
    if _domain_matches(domain, "business.amazon.com") and re.match(
        r"^.{0,80}Amazon Business offers business-only pricing", subject, re.IGNORECASE
    ):
        return True
    if VETO_PATTERNS.search(subject) or re.search(r"\bapplication\b", subject, re.IGNORECASE):
        return False
    if _UNPARSEABLE_SPAM.search(subject):
        return True
    return bool(domain) and any(
        _domain_matches(domain, suffix) and pattern.search(subject)
        for suffix, pattern in _UNPARSEABLE_NOISE
    )
