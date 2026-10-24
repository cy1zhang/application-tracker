from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.domain.ai import ApplicationKind
from app.services.ai_extraction import ValidatedExtraction
from app.services.application_matching import (
    Ambiguous,
    Matched,
    NoMatch,
    match_application,
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
    )
    if isinstance(match, Ambiguous):
        return AutoDecision("needs_look", "ambiguous_match", None, {})

    proposed = _supported_fields(extraction)
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

