import json
from datetime import UTC, datetime
from uuid import uuid4

from app.observability import get_logger, log_event
from app.repositories.dynamodb_email import InvalidCreateDecision
from app.services.auto_apply import AutoDecision, MessageRef, decide
from app.services.sync_candidates import load_candidates

logger = get_logger(__name__)


def apply_plan_page(
    owner_id: str, *, emails, applications, settings, publisher, queue_url: str,
    page_size: int = 25, clock=lambda: datetime.now(UTC),
) -> bool:
    """Execute the oldest saved plans; True when finished or switched off."""
    planned = emails.list_planned(owner_id)
    if not planned:
        return True
    candidates = load_candidates(owner_id, applications)
    for item in planned[:page_size]:
        if settings.get_mode(owner_id) != "on":
            return True
        ref = MessageRef(item.message_id, item.thread_id, item.received_at)
        decision = decide(item.extraction, message_ref=ref, candidates=candidates)
        version = next(
            (
                candidate.application.version for candidate in candidates
                if candidate.application.application_id == decision.application_id
            ),
            None,
        )
        try:
            stored = emails.execute(
                owner_id, message=ref, subject=item.subject, sender=item.sender,
                decision=decision, extraction=item.extraction,
                application_version=version,
                new_application_id=uuid4().hex if decision.action == "create" else None,
                occurred_at=clock(),
            )
        except InvalidCreateDecision:
            log_event(logger, "warning", "plan_apply_invalid_create")
            # Record it for review so the same plan is never picked up again.
            emails.execute(
                owner_id, message=ref, subject=item.subject, sender=item.sender,
                decision=AutoDecision("needs_look", "invalid_create", None, {}),
                extraction=item.extraction, application_version=None,
                new_application_id=None, occurred_at=clock(),
            )
            continue
        if stored.outcome == "applied" and decision.action in {"create", "update"}:
            candidates = load_candidates(owner_id, applications)
    if len(planned) <= page_size:
        return True
    publisher.send_message(
        QueueUrl=queue_url,
        MessageBody=json.dumps({"mode": "apply_plan", "owner_id": owner_id}),
    )
    return False
