import re
from dataclasses import replace
from datetime import UTC, datetime

from app.observability import category_of, get_logger, log_event
from app.repositories.dynamodb_auto_apply import AutoApplyMode
from app.repositories.dynamodb_email import DynamoEmailRepository, StoredOutcome
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.ai_reconcile import ESCALATION_REASONS
from app.services.email_filters import filter_reason, filter_unparseable_headers
from app.services.email_rules import ApplicationCandidate, classify_message
from app.services.gmail_message import MessageParseError, parse_gmail_message

logger = get_logger(__name__)


def extract_headers(gmail_message) -> tuple[str | None, str | None]:
    subject = sender = None
    payload = gmail_message.get("payload") if isinstance(gmail_message, dict) else None
    headers = payload.get("headers") if isinstance(payload, dict) else None
    for header in headers if isinstance(headers, list) else []:
        if not isinstance(header, dict):
            continue
        name, value = header.get("name"), header.get("value")
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if name.lower() == "subject" and subject is None:
            subject = value
        elif name.lower() == "from" and sender is None:
            sender = value
    return subject, sender


def process_message(
    owner_id: str,
    gmail_message: dict,
    applications: list[ApplicationCandidate],
    repository: DynamoEmailRepository,
    escalator=None,
    mode: AutoApplyMode = "off",
) -> StoredOutcome:
    try:
        message = parse_gmail_message(gmail_message)
    except MessageParseError:
        message_id = gmail_message.get("id") if isinstance(gmail_message, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise
        thread_id = gmail_message.get("threadId")
        if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", thread_id):
            thread_id = None
        received_at = None
        try:
            received_ms = int(gmail_message["internalDate"])
            if received_ms >= 0:
                received_at = datetime.fromtimestamp(received_ms / 1000, UTC)
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
        subject, sender = extract_headers(gmail_message)
        return repository.record_unparseable(
            owner_id=owner_id, message_id=message_id,
            thread_id=thread_id, received_at=received_at,
            subject=subject, sender=sender,
            filtered=mode != "off" and filter_unparseable_headers(subject, sender),
        )

    if mode != "off":
        reason = filter_reason(message)
        if reason is not None:
            return repository.record_ignored(owner_id, message, f"filtered:{reason}")
    decision = classify_message(message, applications)
    if mode != "off" and decision.outcome != "applied":
        # The local AI decides everything the rules don't; the hosted AI is not used.
        return repository.record_pending_ai(owner_id, message)
    if (
        escalator is not None
        and decision.outcome == "review_required"
        and decision.reason in ESCALATION_REASONS
    ):
        # Replays must never spend budget, so check for a recorded outcome first.
        prior = repository.get_terminal_outcome(owner_id, message.message_id)
        if prior is not None:
            return prior
        try:
            decision = escalator.escalate(message, decision, applications)
        except StaleSyncRun:
            raise
        except Exception as error:  # noqa: BLE001 - AI must never fail the sync
            log_event(
                logger, "warning", "ai_escalation_failed", category=category_of(error)
            )
            decision = replace(decision, ai_note="ai_unavailable")
    version = next(
        (
            candidate.application.version
            for candidate in applications
            if candidate.application.application_id == decision.application_id
        ),
        None,
    )
    return repository.record(
        owner_id=owner_id,
        message=message,
        decision=decision,
        application_version=version,
    )
