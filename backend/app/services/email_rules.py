import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.domain.applications import Application, ApplicationStatus, Season
from app.services.gmail_message import ParsedGmailMessage

if TYPE_CHECKING:
    from app.services.ai_extraction import ValidatedExtraction

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
_UNCERTAIN = re.compile(
    r"\b(?:if|may|might|could|would|unable|cannot|can't|won't|"
    r"haven't|hasn't|pending)\b|\b(?:plan|expect|hope|intend)\s+to\b|"
    r"\b(?:have|has|had)\s+not\s+(?:yet\s+)?decided\b",
    re.IGNORECASE,
)
HYPOTHETICAL = _HYPOTHETICAL
UNCERTAIN = _UNCERTAIN
_FUTURE_OR_NEGATED = re.compile(r"\b(?:will|not)\b", re.IGNORECASE)
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
    re.compile(
        r"\b(?:your|the)\s+(?P<role>[a-z0-9 /+&-]{3,80}?)\s+"
        r"interview\s+(?:is\s+)?scheduled\b",
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
    origin: Literal["rules", "ai"] = "rules"
    rule_reason: str | None = None
    ai_proposal: "ValidatedExtraction | None" = None


def classify_message(
    message: ParsedGmailMessage, applications: list[ApplicationCandidate]
) -> RuleDecision:
    content = f"{message.subject}\n{message.text}"
    if message.is_forwarded:
        return RuleDecision(outcome="review_required", reason="forwarded_message")
    if (
        _MARKETING.search(message.subject)
        or re.match(r"\s*apply now\b", message.text, re.IGNORECASE)
    ):
        return RuleDecision(outcome="ignored", reason="non_application_message")

    raw_matches = _stage_matches(message.text)
    if not raw_matches and _stage_matches(message.subject):
        return RuleDecision(outcome="review_required", reason="subject_only_event")
    stage_content = message.text
    found = [
        (status, match)
        for status, match in raw_matches
        if not _is_hypothetical(stage_content, match.start())
    ]
    if raw_matches and not found:
        return RuleDecision(outcome="ignored", reason="hypothetical_event")
    roles = _explicit_roles(message.text)
    requisitions = list(dict.fromkeys(_REQUISITION.findall(content)))
    if not found and len(roles) > 1:
        linked = any(
            message.thread_id in item.thread_ids
            or bool({value.casefold() for value in requisitions} & {
                value.casefold() for value in item.requisition_ids
            })
            or (
                item.application.company
                and item.application.role
                and _mentions(content, item.application.company)
                and _mentions(content, item.application.role)
            )
            for item in applications
        )
        return RuleDecision(
            outcome="review_required" if linked else "ignored",
            reason="unknown_stage" if linked else "no_event",
        )
    if len(roles) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_roles")
    cycles = {
        (Season(season.lower()), int(year))
        for season, year in _CYCLE.findall(content)
    }
    if len(found) > 1 and len({status for status, _ in found}) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_stages")
    if len(cycles) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_cycles")
    if len({value.casefold() for value in requisitions}) > 1:
        return RuleDecision(outcome="review_required", reason="conflicting_requisitions")

    status = found[0][0] if found else None
    evidence = found[0][1].group(0) if found else ""
    event_sentence = _sentence_at(stage_content, found[0][1].start()) if found else ""
    if status is not None and (
        _UNCERTAIN.search(event_sentence)
        or (status != ApplicationStatus.REJECTED and _FUTURE_OR_NEGATED.search(event_sentence))
    ):
        return RuleDecision(
            outcome="review_required", reason="nonaffirmative_event", status=status, evidence=evidence
        )
    event_roles = _explicit_roles(event_sentence)
    event_requisitions = (
        _direct_requisitions(event_sentence, next(iter(event_roles)))
        if len(event_roles) == 1
        else []
    )
    season, year = (
        _cycle_for_role(event_sentence, next(iter(event_roles)))
        if len(event_roles) == 1
        else (None, None)
    )
    candidate = _match_application(
        message, applications, event_requisitions, season, year
    )

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
            requisition_id=event_requisitions[0] if event_requisitions else None,
            evidence=evidence,
        )
    if (
        event_roles != {(candidate.application.role or "").casefold()}
        or not candidate.application.company
        or not _company_matches_event(
            event_sentence, candidate.application.company, candidate.application.role or ""
        )
    ):
        return RuleDecision(
            outcome="review_required",
            reason="ambiguous_event_identity",
            status=status,
            evidence=evidence,
        )

    application = candidate.application
    common = {
        "application_id": application.application_id,
        "status": status,
        "season": season,
        "year": year,
        "requisition_id": event_requisitions[0] if event_requisitions else None,
        "evidence": evidence,
    }
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
    event_requisitions: list[str],
    season: Season | None,
    year: int | None,
) -> ApplicationCandidate | None:
    matches = _linked_matches(message, applications, event_requisitions, season, year)
    return matches[0] if len(matches) == 1 else None


def linked_matches(
    message: ParsedGmailMessage,
    applications: list[ApplicationCandidate],
    event_requisitions: list[str],
    season: Season | None,
    year: int | None,
) -> list[ApplicationCandidate]:
    return _linked_matches(message, applications, event_requisitions, season, year)


def _linked_matches(
    message: ParsedGmailMessage,
    applications: list[ApplicationCandidate],
    event_requisitions: list[str],
    season: Season | None,
    year: int | None,
) -> list[ApplicationCandidate]:
    requisitions = {value.casefold() for value in event_requisitions}
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
    else:
        matches = by_thread or by_req
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
    return matches


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


def _sentence_at(content: str, position: int) -> str:
    start = max(content.rfind(char, 0, position) for char in ".!?;\n") + 1
    ends = [content.find(char, position) for char in ".!?;\n"]
    end = min((value for value in ends if value >= 0), default=len(content))
    return content[start:end]


def _cycle_for_role(content: str, role: str) -> tuple[Season | None, int | None]:
    match = re.search(
        rf"\bfor\s+{re.escape(role)}\s+in\s+"
        rf"{_CYCLE.pattern}",
        content,
        re.IGNORECASE,
    )
    return (Season(match.group(1).lower()), int(match.group(2))) if match else (None, None)


def _direct_requisitions(content: str, role: str) -> list[str]:
    match = re.search(
        rf"\bfor\s+{re.escape(role)}\s*,?\s*{_REQUISITION.pattern}\s*$",
        content,
        re.IGNORECASE,
    )
    return [match.group(1)] if match else []


def _company_matches_event(content: str, company: str, role: str) -> bool:
    role_at_company = re.search(
        rf"\bfor\s+{re.escape(role)}\s+at\s+([^,;.!?]+)", content, re.IGNORECASE
    )
    if role_at_company:
        return role_at_company.group(1).strip().casefold() == company.casefold()
    return re.match(
        rf"\s*{re.escape(company)}\s+"
        r"(?:invites?|invited|is pleased|has decided|has received)\b",
        content,
        re.IGNORECASE,
    ) is not None


def would_regress(
    current: ApplicationStatus | None, proposed: ApplicationStatus
) -> bool:
    return _would_regress(current, proposed)


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
