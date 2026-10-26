from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.ai import ApplicationKind
from app.domain.applications import ApplicationStatus
from app.services.ai_extraction import ValidatedExtraction
from app.services.application_matching import (
    Ambiguous,
    Matched,
    NoMatch,
    match_application,
    normalize_company,
    normalize_role,
)
from app.services.email_rules import ApplicationCandidate, would_regress

Action = Literal["create", "update", "ignore", "needs_look"]


@dataclass(frozen=True)
class MessageRef:
    message_id: str
    thread_id: str | None
    received_at: datetime


@dataclass(frozen=True)
class AutoDecision:
    action: Action
    reason: str
    application_id: str | None
    fields: dict[str, object]
    requisition_id: str | None = None


def _supported_fields(extraction: ValidatedExtraction) -> dict[str, object]:
    values = {
        "company": extraction.company,
        "role": extraction.role,
        "role_type": extraction.employment_type,
        "season": extraction.season,
        "year": extraction.year,
        "status": extraction.status,
    }
    return {name: value for name, value in values.items() if value is not None}


def _cycle_conflicts(
    candidate: ApplicationCandidate, extraction: ValidatedExtraction
) -> bool:
    row = candidate.application
    return any(
        have is not None and want is not None and have != want
        for have, want in (
            (row.season, extraction.season),
            (row.year, extraction.year),
        )
    )


def _new_requisition_for_same_role(
    extraction: ValidatedExtraction,
    message_ref: MessageRef,
    candidates: list[ApplicationCandidate],
) -> bool:
    if (
        extraction.status != ApplicationStatus.APPLIED
        or not extraction.company or not extraction.role or not extraction.requisition_id
    ):
        return False
    company = normalize_company(extraction.company)
    role = normalize_role(extraction.role)
    if not company or not role:
        return False
    requisition = extraction.requisition_id.strip().casefold()
    same_role = [
        candidate for candidate in candidates
        if candidate.application.company and candidate.application.role
        and normalize_company(candidate.application.company) == company
        and normalize_role(candidate.application.role) == role
    ]
    if not same_role or any(not candidate.requisition_ids for candidate in same_role):
        return False
    same_ids = {candidate.application.application_id for candidate in same_role}
    if message_ref.thread_id and any(
        message_ref.thread_id in candidate.thread_ids
        and candidate.application.application_id not in same_ids
        for candidate in candidates
    ):
        return False
    return not any(
        requisition in {value.strip().casefold() for value in candidate.requisition_ids}
        for candidate in candidates
    )


def decide(
    extraction: ValidatedExtraction,
    *,
    message_ref: MessageRef,
    candidates: list[ApplicationCandidate],
) -> AutoDecision:
    if extraction.kind != ApplicationKind.APPLICATION_UPDATE:
        kind = extraction.kind.value if extraction.kind else "unknown"
        return AutoDecision("ignore", f"not_application:{kind}", None, {})
    if extraction.status_in_subject_only:
        return AutoDecision("needs_look", "status_in_subject_only", None, {})

    match = match_application(
        candidates,
        thread_id=message_ref.thread_id,
        requisition_id=extraction.requisition_id,
        company=extraction.company,
        role=extraction.role,
        season=extraction.season,
        year=extraction.year,
        role_type=extraction.employment_type,
    )
    proposed = _supported_fields(extraction)
    if isinstance(match, Ambiguous):
        if extraction.status is None:
            return AutoDecision("ignore", "ambiguous_no_status", None, {})
        if _new_requisition_for_same_role(extraction, message_ref, candidates):
            return AutoDecision(
                "create", "ai_create", None, proposed, extraction.requisition_id
            )
        return AutoDecision("needs_look", "ambiguous_match", None, {})

    if isinstance(match, Matched) and _cycle_conflicts(
        match.candidate, extraction
    ):
        match = NoMatch()
    if isinstance(match, Matched):
        current = match.candidate.application
        target = current.application_id
        status = extraction.status
        # would_regress(None, ...) is False, so a null-status row may take APPLIED.
        if (
            status is not None
            and current.status is not None
            and would_regress(current.status, status)
        ):
            if (
                current.status == ApplicationStatus.REJECTED
                and status == ApplicationStatus.APPLIED
                and match.candidate.last_status_event_at is not None
            ):
                known_requisitions = {
                    value.strip().casefold()
                    for value in match.candidate.requisition_ids
                }
                requisition = (extraction.requisition_id or "").strip().casefold()
                distinct_requisition = bool(
                    requisition and known_requisitions
                    and requisition not in known_requisitions
                )
                if (
                    message_ref.received_at <= match.candidate.last_status_event_at
                    and not distinct_requisition
                ):
                    return AutoDecision(
                        "ignore", "stale_status_regression", target, {}
                    )
                if (
                    message_ref.received_at > match.candidate.last_status_event_at
                    and distinct_requisition
                    and _new_requisition_for_same_role(
                        extraction, message_ref, candidates
                    )
                ):
                    return AutoDecision(
                        "create", "ai_create", None, proposed,
                        extraction.requisition_id,
                    )
            return AutoDecision("needs_look", "status_regression", target, {})
        changes = {
            name: value
            for name, value in proposed.items()
            if getattr(current, name) != value
            and not (
                name in {"season", "year", "company", "role"}
                and getattr(current, name) is not None
            )
        }
        if not changes:
            return AutoDecision("ignore", "no_change", target, {})
        return AutoDecision(
            "update", "ai_update", target, changes, extraction.requisition_id
        )

    if extraction.company and extraction.status is not None:
        return AutoDecision(
            "create", "ai_create", None, proposed, extraction.requisition_id
        )
    return AutoDecision("ignore", "no_status_no_match", None, {})

