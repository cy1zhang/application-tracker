from dataclasses import replace

from app.domain.applications import ApplicationStatus
from app.services.ai_extraction import ValidatedExtraction
from app.services.email_rules import (
    ApplicationCandidate,
    RuleDecision,
    linked_matches,
    mentions_whole_word,
    would_regress,
)
from app.services.gmail_message import ParsedGmailMessage

ESCALATION_REASONS = frozenset(
    {
        "ambiguous_application",
        "unknown_stage",
        "subject_only_event",
        "nonaffirmative_event",
        "conflicting_roles",
    }
)
AUTO_APPLY_REASONS = frozenset({"ambiguous_application", "unknown_stage"})

_EVIDENCE_FIELDS = (
    "status",
    "company",
    "role",
    "season",
    "year",
    "requisition_id",
    "employment_type",
)
_MAX_EVIDENCE = 500


def reconcile_extraction(
    rule_decision: RuleDecision,
    extraction: ValidatedExtraction,
    message: ParsedGmailMessage,
    applications: list[ApplicationCandidate],
) -> RuleDecision:
    season = extraction.season or rule_decision.season
    year = extraction.year or rule_decision.year
    requisition_id = extraction.requisition_id or rule_decision.requisition_id
    status = _status(rule_decision, extraction)

    matched = _match(message, applications, extraction, requisition_id, season, year)
    evidence = _evidence(extraction, rule_decision)
    review = RuleDecision(
        outcome="review_required",
        reason="ai_proposal",
        evidence=evidence,
        origin="ai",
        rule_reason=rule_decision.reason,
        ai_proposal=extraction,
    )
    if matched is None:
        return review

    review = replace(
        review,
        application_id=matched.application.application_id,
        status=status,
        season=season,
        year=year,
        requisition_id=requisition_id,
    )
    application = matched.application
    if (
        status is None
        or rule_decision.reason not in AUTO_APPLY_REASONS
        or _status_conflict(rule_decision, extraction)
        or extraction.status_in_subject_only
        or would_regress(application.status, status)
        or (season is not None and application.season not in {None, season})
        or (year is not None and application.year not in {None, year})
        or (
            application.status == status
            and (season is None or application.season == season)
            and (year is None or application.year == year)
        )
    ):
        return review
    return replace(review, outcome="applied", reason="ai_extraction")


def _status(
    rule_decision: RuleDecision, extraction: ValidatedExtraction
) -> ApplicationStatus | None:
    return extraction.status or rule_decision.status


def _status_conflict(
    rule_decision: RuleDecision, extraction: ValidatedExtraction
) -> bool:
    return (
        extraction.status is not None
        and rule_decision.status is not None
        and extraction.status != rule_decision.status
    )


def _match(message, applications, extraction, requisition_id, season, year):
    requisitions = [requisition_id] if requisition_id else []
    linked = linked_matches(message, applications, requisitions, season, year)
    if not linked:
        # Cycle-conflicting links are kept so the guard can record the match.
        linked = linked_matches(message, applications, requisitions, None, None)
    if linked:
        if len(linked) != 1 or not _agrees(linked[0], extraction):
            return None
        return linked[0]
    if not extraction.company or not extraction.role:
        return None
    company, role = _fold(extraction.company), _fold(extraction.role)
    named = [
        item
        for item in applications
        if item.application.company
        and item.application.role
        and _fold(item.application.company) == company
        and _fold(item.application.role) == role
        and (season is None or item.application.season in {None, season})
        and (year is None or item.application.year in {None, year})
    ]
    if len(named) != 1:
        return None
    return named[0] if _name_anchored(message, applications, named[0]) else None


def _agrees(item, extraction) -> bool:
    """A linked application must not contradict the identity the email names."""
    application = item.application
    for extracted, actual in (
        (extraction.company, application.company),
        (extraction.role, application.role),
    ):
        if extracted and _fold(extracted) != _fold(actual or ""):
            return False
    return True


def _name_anchored(message, applications, matched) -> bool:
    """Identity must come from the headers and be the only owner company named."""
    headers = f"{message.subject}\n{message.sender}"
    if not mentions_whole_word(headers, matched.application.company):
        return False
    sent = f"{headers}\n{message.text}"
    mentioned = {
        _fold(item.application.company)
        for item in applications
        if item.application.company and mentions_whole_word(sent, item.application.company)
    }
    return len(mentioned) == 1


def _fold(value: str) -> str:
    return " ".join(value.split()).casefold()


def _evidence(extraction: ValidatedExtraction, rule_decision: RuleDecision) -> str:
    quotes = [
        extraction.evidence[field]
        for field in _EVIDENCE_FIELDS
        if field in extraction.evidence
    ]
    return " | ".join(quotes or [rule_decision.evidence])[:_MAX_EVIDENCE]
