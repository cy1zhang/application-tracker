from dataclasses import dataclass
from datetime import datetime

from app.domain.applications import Application, ApplicationStatus, Season
from app.services.ai_extraction import ValidatedExtraction
from app.services.auto_apply import AutoDecision, MessageRef, decide
from app.services.email_rules import ApplicationCandidate

SAMPLE_LIMIT = 100
_ACTIONS = ("create", "update", "ignore", "needs_look")


@dataclass(frozen=True)
class PlannedItem:
    message_id: str
    received_at: datetime
    extraction: ValidatedExtraction
    subject: str
    sender: str
    thread_id: str | None = None


@dataclass(frozen=True)
class PlanLine:
    message_id: str
    action: str
    reason: str
    company: str | None
    role: str | None
    status: ApplicationStatus | None
    season: Season | None
    year: int | None
    target: str | None


@dataclass(frozen=True)
class PlanReport:
    counts: dict[str, int]
    reasons: dict[str, int]
    sample: list[PlanLine]


def _with_thread(
    candidate: ApplicationCandidate, thread_id: str | None, requisition_id: str | None
) -> ApplicationCandidate:
    return ApplicationCandidate(
        application=candidate.application,
        thread_ids=candidate.thread_ids | ({thread_id} if thread_id else set()),
        requisition_ids=candidate.requisition_ids
        | ({requisition_id} if requisition_id else set()),
    )


def _apply(
    candidates: list[ApplicationCandidate],
    item: PlannedItem,
    decision: AutoDecision,
) -> str | None:
    """Apply a decision in memory; return the target application id."""
    if decision.action == "create":
        application_id = f"planned:{item.message_id}"
        fields = decision.fields
        candidates.append(
            _with_thread(
                ApplicationCandidate(
                    application=Application(
                        application_id=application_id,
                        company=fields.get("company"),
                        role=fields.get("role"),
                        role_type=fields.get("role_type"),
                        season=fields.get("season"),
                        year=fields.get("year"),
                        status=fields.get("status"),
                        application_date=None,
                        created_at=item.received_at,
                        updated_at=item.received_at,
                        version=1,
                    )
                ),
                item.thread_id,
                decision.requisition_id,
            )
        )
        return application_id
    if decision.action == "update":
        for index, candidate in enumerate(candidates):
            if candidate.application.application_id == decision.application_id:
                merged = candidate.application.model_copy(
                    update={
                        **decision.fields,
                        "updated_at": item.received_at,
                        "version": candidate.application.version + 1,
                    }
                )
                candidates[index] = _with_thread(
                    ApplicationCandidate(
                        application=merged,
                        thread_ids=candidate.thread_ids,
                        requisition_ids=candidate.requisition_ids,
                    ),
                    item.thread_id,
                    decision.requisition_id,
                )
                break
    return decision.application_id


def simulate(
    items: list[PlannedItem], candidates: list[ApplicationCandidate]
) -> PlanReport:
    working = list(candidates)
    counts = {action: 0 for action in _ACTIONS}
    reasons: dict[str, int] = {}
    sample: list[PlanLine] = []
    for item in sorted(items, key=lambda i: i.received_at):
        ref = MessageRef(item.message_id, item.thread_id, item.received_at)
        decision = decide(item.extraction, message_ref=ref, candidates=working)
        target = _apply(working, item, decision)
        counts[decision.action] += 1
        reasons[decision.reason] = reasons.get(decision.reason, 0) + 1
        if decision.action in {"create", "update"} and len(sample) < SAMPLE_LIMIT:
            e = item.extraction
            sample.append(
                PlanLine(
                    message_id=item.message_id,
                    action=decision.action,
                    reason=decision.reason,
                    company=e.company,
                    role=e.role,
                    status=e.status,
                    season=e.season,
                    year=e.year,
                    target=target,
                )
            )
    return PlanReport(counts=counts, reasons=reasons, sample=sample)
