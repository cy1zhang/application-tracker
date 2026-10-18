import re
from dataclasses import dataclass
from typing import Literal

from app.domain.applications import Application, ApplicationStatus, Season
from app.services.gmail_message import ParsedGmailMessage

Outcome = Literal["applied", "ignored", "review_required"]

_STAGES = {
    ApplicationStatus.APPLIED: (
        r"\bthank you for applying\b",
        (
            r"\b(?:we (?:have )?received your application|your application "
            r"(?:has been )?received)\b"
        ),
    ),
    ApplicationStatus.OA_RECEIVED: (
        (
            r"\b(?:invite|invites|invited) you to complete an? "
            r"(?:online )?assessment\b"
        ),
    ),
    ApplicationStatus.INTERVIEWING: (
        r"\b(?:invite|invites|invited) you to interview\b",
        r"\bschedule an? interview\b",
        r"\binterview (?:is )?scheduled\b",
    ),
    ApplicationStatus.OFFER: (
        r"\bpleased to offer you\b",
        r"\boffer you the\b",
    ),
    ApplicationStatus.REJECTED: (
        r"\bdecided not to move forward with your application\b",
        r"\bwill not be moving forward with your application\b",
    ),
}
_MARKETING = re.compile(
    r"\b(?:newsletter|job alert|recommended jobs|career opportunities|new openings)\b",
    re.IGNORECASE,
)
_HYPOTHETICAL = re.compile(
    r"\bif\s+(?:we|you|your)\b|\b(?:may|might|could)\s+"
    r"(?:invite|receive|be offered)\b",
    re.IGNORECASE,
)
_CYCLE = re.compile(r"\b(summer|fall|spring|winter)\s+(20\d{2})\b", re.IGNORECASE)
_REQUISITION = re.compile(
    r"\b(?:req(?:uisition)?\s*(?:id|#)?|job\s*id)\s*[:#-]?\s*"
    r"([a-z0-9][a-z0-9-]{2,})\b",
    re.IGNORECASE,
)
_ROLE = (
    re.compile(
        r"\bfor\s+(?P<role>[a-z0-9 /+&-]{3,80}?)"
        r"(?=\s+(?:at|with|in\s+(?:summer|fall|spring|winter)\b)|[.,;!?]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bthe\s+(?P<role>[a-z0-9 /+&-]{3,80}?)\s+(?:position|role)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ApplicationCandidate:
    application: Application
    thread_ids: frozenset[str] = frozenset()
    requisition_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RuleDecision:
    outcome: Outcome
    reason: str
    application_id: str | None = None
    status: ApplicationStatus | None = None
    season: Season | None = None
    year: int | None = None
    requisition_id: str | None = None
    evidence: str = ""


def classify_message(
    message: ParsedGmailMessage, applications: list[ApplicationCandidate]
) -> RuleDecision:
    content = f"{message.subject}\n{message.text}"
    if (
        _MARKETING.search(message.subject)
        or re.match(r"\s*apply now\b", message.text, re.IGNORECASE)
    ):
        return RuleDecision(outcome="ignored", reason="non_application_message")

    raw_matches = _stage_matches(message.text)
    if not raw_matches:
        raw_matches = _stage_matches(message.subject)
    stage_content = message.text if _stage_matches(message.text) else message.subject
    found = [
        (status, match.group(0))
        for status, match in raw_matches
        if not _is_hypothetical(stage_content, match.start())
    ]
    if raw_matches and not found:
        return RuleDecision(outcome="ignored", reason="hypothetical_event")
    cycle_text = message.text if _CYCLE.search(message.text) else message.subject
    cycles = {
        (Season(season.lower()), int(year))
        for season, year in _CYCLE.findall(cycle_text)
    }
    requisitions = list(dict.fromkeys(_REQUISITION.findall(content)))
    if len(found) > 1 and len({status for status, _ in found}) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_stages")
    if len(cycles) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_cycles")
    if len({value.casefold() for value in requisitions}) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_requisitions")

    status = found[0][0] if found else None
    evidence = found[0][1] if found else ""
    season, year = next(iter(cycles)) if cycles else (None, None)
    candidate = _match_application(message, applications, content, season, year)

    if status is None:
        names_mentioned = any(
            item.application.company
            and item.application.role
            and _mentions(content, item.application.company)
            and _mentions(content, item.application.role)
            for item in applications
        )
        if candidate is not None or names_mentioned or re.search(
            r"\bfor .{3,100} at .{2,80}\b", content
        ):
            return RuleDecision(
                outcome="review_required", reason="unknown_stage", season=season, year=year
            )
        return RuleDecision(outcome="ignored", reason="no_event")
    if candidate is None:
        return RuleDecision(
            outcome="review_required",
            reason="ambiguous_application",
            status=status,
            season=season,
            year=year,
            requisition_id=requisitions[0] if requisitions else None,
            evidence=evidence,
        )

    application = candidate.application
    common = {
        "application_id": application.application_id,
        "status": status,
        "season": season,
        "year": year,
        "requisition_id": requisitions[0] if requisitions else None,
        "evidence": evidence,
    }
    if message.received_at < application.updated_at:
        return RuleDecision(outcome="review_required", reason="older_evidence", **common)
    if _would_regress(application.status, status):
        return RuleDecision(outcome="review_required", reason="status_regression", **common)
    if season is not None and application.season not in {None, season}:
        return RuleDecision(outcome="review_required", reason="cycle_conflict", **common)
    if year is not None and application.year not in {None, year}:
        return RuleDecision(outcome="review_required", reason="cycle_conflict", **common)
    if (
        application.status == status
        and (season is None or application.season == season)
        and (year is None or application.year == year)
    ):
        return RuleDecision(outcome="ignored", reason="no_change", **common)
    return RuleDecision(outcome="applied", reason="explicit_event", **common)


def _match_application(
    message: ParsedGmailMessage,
    applications: list[ApplicationCandidate],
    content: str,
    season: Season | None,
    year: int | None,
) -> ApplicationCandidate | None:
    requisitions = {value.casefold() for value in _REQUISITION.findall(content)}
    roles = _explicit_roles(message.text)
    if not roles:
        roles = _explicit_roles(message.subject)
    by_thread = [item for item in applications if message.thread_id in item.thread_ids]
    by_req = [
        item
        for item in applications
        if requisitions & {value.casefold() for value in item.requisition_ids}
    ]
    if by_thread and by_req:
        matches = [item for item in by_thread if item in by_req]
    elif by_thread or by_req:
        matches = by_thread or by_req
    else:
        matches = [
            item
            for item in applications
            if item.application.company
            and item.application.role
            and _mentions(content, item.application.company)
            and item.application.role.casefold() in roles
        ]
    matches = [
        item
        for item in matches
        if (season is None or item.application.season in {None, season})
        and (year is None or item.application.year in {None, year})
        and (not roles or (item.application.role or "").casefold() in roles)
    ]
    mentioned_in_body = [
        item
        for item in applications
        if item.application.company
        and item.application.role
        and _mentions(message.text, item.application.company)
        and item.application.role.casefold() in _explicit_roles(message.text)
    ]
    if mentioned_in_body:
        matches = [item for item in matches if item in mentioned_in_body]
    return matches[0] if len(matches) == 1 else None


def _mentions(content: str, value: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(value)}(?!\w)", content, re.IGNORECASE) is not None


def _explicit_roles(content: str) -> set[str]:
    return {
        " ".join(match.group("role").casefold().removeprefix("applying for ").split())
        for pattern in _ROLE
        for match in pattern.finditer(content)
    }


def _stage_matches(content: str) -> list[tuple[ApplicationStatus, re.Match[str]]]:
    return [
        (status, match)
        for status, patterns in _STAGES.items()
        for pattern in patterns
        for match in re.finditer(pattern, content, re.IGNORECASE)
    ]


def _is_hypothetical(content: str, position: int) -> bool:
    clause_start = max(content.rfind(char, 0, position) for char in ".!?\n") + 1
    return _HYPOTHETICAL.search(content[clause_start:position]) is not None


def _would_regress(
    current: ApplicationStatus | None, proposed: ApplicationStatus
) -> bool:
    if current is None or current == proposed:
        return False
    if current in {ApplicationStatus.OFFER, ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN}:
        return proposed != ApplicationStatus.REJECTED
    if proposed == ApplicationStatus.APPLIED:
        return True
    return current == ApplicationStatus.INTERVIEWING and proposed == ApplicationStatus.OA_RECEIVED
