from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from app.domain.ai import ApplicationKind
from app.domain.applications import ApplicationPatch
from app.repositories.applications import ApplicationRepository, VersionConflict
from app.repositories.dynamodb_email import (
    AlreadyUndone,
    DynamoEmailRepository,
    NotReconsiderable,
    extraction_from_plan,
    is_reconsiderable,
)
from app.services.auto_apply import MessageRef, decide
from app.services.sync_candidates import load_candidates


class AiChangeNotFound(Exception):
    pass


class NotUndoable(Exception):
    pass


class ChangedSince(Exception):
    pass


@dataclass(frozen=True)
class ReconsiderResult:
    queued: bool
    action: str | None = None


def plain(value: Any) -> Any:
    """Normalize stored/current values so they compare and serialize alike."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


class AiUndoService:
    def __init__(
        self, *, emails: DynamoEmailRepository, applications: ApplicationRepository,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._emails = emails
        self._applications = applications
        self._clock = clock

    def _item(self, owner_id: str, message_id: str) -> dict[str, Any]:
        item = self._emails.get_event_item(owner_id, message_id)
        if item is None:
            raise AiChangeNotFound
        return item

    def undo(self, owner_id: str, message_id: str) -> None:
        item = self._item(owner_id, message_id)
        if item.get("outcome") != "applied" or item.get("origin") != "ai":
            raise NotUndoable
        if "undone_at" in item:
            raise AlreadyUndone
        application = self._applications.get(
            owner_id=owner_id, application_id=item.get("application_id", "")
        )
        if application is None:
            raise ChangedSince
        created = item.get("reason") == "ai_create"
        changes = item.get("changes", {})
        if created:
            if application.version != 1:
                raise ChangedSince
        else:
            current = application.model_dump()
            if any(
                plain(current[field]) != plain(change["after"])
                for field, change in changes.items()
            ):
                raise ChangedSince

        # Claim first so a double click cannot patch twice; release if the write fails.
        now = self._clock()
        self._emails.mark_undone(owner_id, message_id, now)
        try:
            if created:
                self._applications.delete(
                    owner_id=owner_id, application_id=application.application_id,
                    expected_version=application.version, event_id=uuid4().hex,
                    occurred_at=now,
                )
            else:
                patch = ApplicationPatch(
                    expected_version=application.version,
                    **{
                        field: plain(change["before"])
                        for field, change in changes.items()
                    },
                )
                self._applications.update(
                    owner_id=owner_id, application_id=application.application_id,
                    patch=patch, event_id=uuid4().hex, occurred_at=now,
                )
        except Exception as error:
            self._emails.clear_undone(owner_id, message_id, now)
            if isinstance(error, VersionConflict):
                raise ChangedSince from None
            raise

    def reconsider(self, owner_id: str, message_id: str) -> ReconsiderResult:
        item = self._item(owner_id, message_id)
        if not is_reconsiderable(item):
            raise NotReconsiderable
        plan = item.get("ai_plan")
        if plan is None:
            self._emails.queue_reconsider(owner_id, message_id)
            return ReconsiderResult(queued=True)
        extraction = replace(
            extraction_from_plan(plan), kind=ApplicationKind.APPLICATION_UPDATE
        )
        ref = MessageRef(
            message_id, item.get("thread_id"), datetime.fromisoformat(item["received_at"])
        )
        candidates = load_candidates(owner_id, self._applications)
        decision = decide(extraction, message_ref=ref, candidates=candidates)
        version = next(
            (
                c.application.version for c in candidates
                if c.application.application_id == decision.application_id
            ),
            None,
        )
        try:
            stored = self._emails.execute(
                owner_id, message=ref, subject=item.get("subject", ""),
                sender=item.get("sender", ""), decision=decision, extraction=extraction,
                application_version=version,
                new_application_id=uuid4().hex if decision.action == "create" else None,
                occurred_at=self._clock(), reconsider=True,
            )
        except ValueError:
            raise NotReconsiderable from None
        return ReconsiderResult(queued=False, action=_action(stored.outcome, stored.reason))


def _action(outcome: str, reason: str) -> str:
    if outcome == "applied":
        return "created" if reason == "ai_create" else "updated"
    return "ignored" if outcome == "ignored" else "review"
