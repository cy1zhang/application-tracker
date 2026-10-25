from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.ai import ApplicationKind
from app.domain.applications import ApplicationStatus, RoleType, Season
from app.repositories.dynamodb import APPLICATION_FIELDS, field_times_for
from app.repositories.dynamodb_reviews import (
    GmailConnectionChanged,
    InvalidReviewCursor,
    _decode_cursor,
    _encode_cursor,
)
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.ai_extraction import ValidatedExtraction
from app.services.auto_apply import AutoDecision, MessageRef
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage
from app.services.plan_report import PlannedItem

StoredOutcomeName = Literal[
    "applied", "ignored", "review_required", "retryable", "pending_ai"
]
_TERMINAL = {"applied", "ignored", "review_required"}
_MAX_HEADER = 300
_RECORD_CONDITION = "attribute_not_exists(event_id) OR outcome = :retryable"
# execute() may also replace an item that holds a planned, unexecuted, unresolved,
# not-undone automatic plan.
_EXECUTE_CONDITION = (
    "attribute_not_exists(event_id) OR outcome IN (:pending_ai, :retryable) OR "
    "(attribute_exists(ai_plan) AND attribute_not_exists(ai_plan.executed_at) "
    "AND attribute_not_exists(resolution) AND attribute_not_exists(undone_at))"
)
# reconsider may overwrite an ignored plan that nobody resolved or undid.
_RECONSIDER_CONDITION = (
    "outcome = :ignored AND attribute_exists(ai_plan) "
    "AND attribute_not_exists(resolution) AND attribute_not_exists(undone_at)"
)
_PROPOSAL_FIELDS = (
    "company", "role", "employment_type", "season", "year", "status",
    "requisition_id",
)


class InvalidMessageCursor(Exception):
    pass


class InvalidCreateDecision(ValueError):
    """A create decision that breaks the create invariants."""


class PlanConflict(Exception):
    """The message is missing, not queued, or already planned."""


class AlreadyUndone(Exception):
    """The message item already carries undone_at, or is missing."""


class NotReconsiderable(Exception):
    """The item is not an ignored, unresolved, not-undone message."""


def is_ai_change(item: dict[str, Any]) -> bool:
    """True for what the owner may review: AI writes and AI/filter ignores."""
    outcome, reason = item.get("outcome"), item.get("reason", "")
    if outcome == "applied":
        return item.get("origin") == "ai"
    return outcome == "ignored" and (
        reason.startswith(("filtered:", "not_application:"))
        or reason == "no_status_no_match"
    )


def is_reconsiderable(item: dict[str, Any]) -> bool:
    return (
        item.get("outcome") == "ignored" and is_ai_change(item)
        and "resolution" not in item and "undone_at" not in item
    )


def is_queued(item: dict[str, Any]) -> bool:
    """True when the local AI worker still owes this item a plan."""
    return "ai_plan" not in item and (
        item.get("outcome") == "pending_ai" or item.get("ai_backfill") is True
    )


def extraction_from_plan(plan: dict[str, Any]) -> ValidatedExtraction:
    """Rebuild the validated extraction stored in an item's ai_plan map."""
    def enum(kind, key):
        return kind(plan[key]) if plan.get(key) is not None else None

    return ValidatedExtraction(
        company=plan.get("company"),
        role=plan.get("role"),
        employment_type=enum(RoleType, "employment_type"),
        season=enum(Season, "season"),
        year=int(plan["year"]) if plan.get("year") is not None else None,
        status=enum(ApplicationStatus, "status"),
        requisition_id=plan.get("requisition_id"),
        evidence=dict(plan.get("evidence", {})),
        status_in_subject_only=bool(plan.get("status_in_subject_only", False)),
        kind=enum(ApplicationKind, "kind"),
    )


class _AiSettingsStale(Exception):
    """The AI settings changed after the provider call; discard the AI result."""


@dataclass(frozen=True)
class StoredOutcome:
    message_id: str
    outcome: StoredOutcomeName
    reason: str
    application_id: str | None


class DynamoEmailRepository:
    def __init__(
        self, *, client, applications_table: str, events_table: str,
        users_table: str | None = None, guard_generation: int | None = None,
        guard_run_id: str | None = None, plan_users_table: str | None = None,
    ) -> None:
        guard_parts = (users_table, guard_generation, guard_run_id)
        if any(part is not None for part in guard_parts) and not all(
            part is not None for part in guard_parts
        ):
            raise ValueError("Sync guard requires a users table, generation, and run ID")
        self._client = client
        self._applications_table = applications_table
        self._events_table = events_table
        self._users_table = users_table
        self._plan_users_table = plan_users_table
        self._guard_generation = guard_generation
        self._guard_run_id = guard_run_id
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def get_event_item(self, owner_id: str, message_id: str) -> dict[str, Any] | None:
        return self._get_event_item(owner_id, message_id)

    def record_pending_ai(
        self, owner_id: str, message: ParsedGmailMessage
    ) -> StoredOutcome:
        """Queue a message for the local AI; any other stored outcome is left alone."""
        prior = self._get_outcome(owner_id, message.message_id)
        if prior is not None and prior.outcome != "retryable":
            return prior
        return self._put_outcome(
            self._header_item(
                owner_id, message, "pending_ai", "awaiting_local_ai"
            )
        )

    def record_ignored(
        self, owner_id: str, message: ParsedGmailMessage, reason: str
    ) -> StoredOutcome:
        prior = self._get_outcome(owner_id, message.message_id)
        if prior is not None and prior.outcome != "retryable":
            return prior
        return self._put_outcome(
            self._header_item(owner_id, message, "ignored", reason)
        )

    @staticmethod
    def _header_item(
        owner_id: str, message: ParsedGmailMessage, outcome: str, reason: str
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "owner_id": owner_id,
            "event_id": f"gmail#{message.message_id}",
            "source_message_id": message.message_id,
            "thread_id": message.thread_id,
            "received_at": message.received_at.isoformat(),
            "outcome": outcome,
            "reason": reason,
        }
        if message.subject:
            item["subject"] = message.subject[:_MAX_HEADER]
        if message.sender:
            item["sender"] = message.sender[:_MAX_HEADER]
        return item

    def _owner_events(self, owner_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        query: dict[str, Any] = {
            "TableName": self._events_table,
            "KeyConditionExpression": "owner_id = :owner AND begins_with(event_id, :prefix)",
            "ExpressionAttributeValues": {
                ":owner": self._serializer.serialize(owner_id),
                ":prefix": self._serializer.serialize("gmail#"),
            },
            "ConsistentRead": True,
        }
        while True:
            response = self._client.query(**query)
            items.extend(self._decode(item) for item in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if last_key is None:
                return items
            query["ExclusiveStartKey"] = last_key

    def list_ai_queue(
        self, owner_id: str, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Queued items, oldest first; limit=None returns the whole queue."""
        queued = sorted(
            (item for item in self._owner_events(owner_id) if is_queued(item)),
            key=lambda item: (item.get("received_at", ""), item["event_id"]),
        )
        return queued if limit is None else queued[:limit]

    def list_planned(self, owner_id: str) -> list[PlannedItem]:
        """Saved, unexecuted, unresolved, not-undone plans, oldest first."""
        planned = [
            item for item in self._owner_events(owner_id)
            if "ai_plan" in item and "executed_at" not in item["ai_plan"]
            and "resolution" not in item and "undone_at" not in item
        ]
        planned.sort(key=lambda item: (item.get("received_at", ""), item["event_id"]))
        return [
            PlannedItem(
                message_id=item["source_message_id"],
                received_at=datetime.fromisoformat(item["received_at"]),
                extraction=extraction_from_plan(item["ai_plan"]),
                subject=item.get("subject", ""),
                sender=item.get("sender", ""),
                thread_id=item.get("thread_id"),
            )
            for item in planned
        ]

    def mark_backfill(self, owner_id: str) -> int:
        """Queue old no_event/unresolved-review items for the local AI; returns new marks."""
        marked = 0
        for item in self._owner_events(owner_id):
            if "ai_plan" in item or "ai_backfill" in item or "resolution" in item:
                continue
            outcome, reason = item.get("outcome"), item.get("reason")
            if not (
                (outcome == "ignored" and reason == "no_event")
                or (outcome == "review_required" and reason != "unparseable_message")
            ):
                continue
            try:
                self._client.update_item(
                    TableName=self._events_table,
                    Key=self._encode(
                        {"owner_id": owner_id, "event_id": item["event_id"]}
                    ),
                    UpdateExpression="SET ai_backfill = :true",
                    ConditionExpression=(
                        "outcome = :outcome AND reason = :reason "
                        "AND attribute_not_exists(ai_backfill) "
                        "AND attribute_not_exists(ai_plan) "
                        "AND attribute_not_exists(resolution)"
                    ),
                    ExpressionAttributeValues=self._encode({
                        ":true": True, ":outcome": outcome, ":reason": reason,
                    }),
                )
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                continue
            marked += 1
        return marked

    def _update_queued(
        self, owner_id: str, message_id: str, update: str, values: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update an item only while it is still queued; None when it is not."""
        try:
            response = self._client.update_item(
                TableName=self._events_table,
                Key=self._encode(
                    {"owner_id": owner_id, "event_id": f"gmail#{message_id}"}
                ),
                UpdateExpression=update,
                ConditionExpression=(
                    "attribute_exists(event_id) AND attribute_not_exists(ai_plan) "
                    "AND attribute_not_exists(resolution) "
                    "AND (#outcome = :pending OR ai_backfill = :true)"
                ),
                ExpressionAttributeNames={
                    name: name[1:] for name in ("#outcome", "#reason")
                    if name in update or name == "#outcome"
                },
                ExpressionAttributeValues=self._encode({
                    ":pending": "pending_ai", ":true": True, **values,
                }),
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return None
        return self._decode(response["Attributes"])

    def ignore_unavailable_queued(self, owner_id: str, message_id: str) -> None:
        """Take a queued item whose Gmail message is gone out of the queue."""
        self._update_queued(
            owner_id, message_id,
            "SET #outcome = :ignored, #reason = :reason REMOVE ai_backfill",
            {":ignored": "ignored", ":reason": "message_unavailable"},
        )

    def record_invalid_output(
        self, owner_id: str, message_id: str, *, max_attempts: int = 3
    ) -> None:
        """Count an invalid model output; the last allowed one goes to review."""
        item = self._update_queued(
            owner_id, message_id, "ADD ai_attempts :one", {":one": 1}
        )
        if item is None or int(item["ai_attempts"]) < max_attempts:
            return
        self._update_queued(
            owner_id, message_id,
            "SET #outcome = :review, #reason = :needs_look, "
            "rule_reason = :rule REMOVE ai_backfill",
            {
                ":review": "review_required", ":needs_look": "needs_look",
                ":rule": "ai_invalid_output",
            },
        )

    def save_plan(
        self, owner_id: str, message_id: str, *, extraction: ValidatedExtraction,
        model: str, generation: int, planned_at: datetime,
    ) -> None:
        """Store the local AI's extraction on a queued item, once."""
        users_table = self._users_table or self._plan_users_table
        plan = {
            **self._proposal(extraction),
            "model": model,
            "generation": generation,
            "planned_at": planned_at.isoformat(),
        }
        items: list[dict[str, Any]] = [{
            "Update": {
                "TableName": self._events_table,
                "Key": self._encode(
                    {"owner_id": owner_id, "event_id": f"gmail#{message_id}"}
                ),
                "UpdateExpression": "SET ai_plan = :plan REMOVE ai_backfill",
                "ConditionExpression": (
                    "attribute_exists(event_id) AND attribute_not_exists(ai_plan) "
                    "AND (outcome = :pending OR ai_backfill = :true)"
                ),
                "ExpressionAttributeValues": self._encode({
                    ":plan": plan, ":pending": "pending_ai", ":true": True,
                }),
            }
        }]
        if users_table is not None:
            items.append({
                "ConditionCheck": {
                    "TableName": users_table,
                    "Key": self._encode({"owner_id": owner_id}),
                    "ConditionExpression": (
                        "generation = :generation AND attribute_exists(encrypted_token) "
                        "AND attribute_not_exists(deletion_state)"
                    ),
                    "ExpressionAttributeValues": self._encode(
                        {":generation": generation}
                    ),
                }
            })
        try:
            self._client.transact_write_items(TransactItems=items)
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            if users_table is not None:
                response = self._client.get_item(
                    TableName=users_table,
                    Key=self._encode({"owner_id": owner_id}),
                    ConsistentRead=True,
                )
                user = self._decode(response["Item"]) if "Item" in response else {}
                if (
                    user.get("generation") != generation
                    or "encrypted_token" not in user
                    or "deletion_state" in user
                ):
                    raise GmailConnectionChanged from None
            raise PlanConflict from None

    def list_ai_changes(
        self, owner_id: str, *, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """AI-made changes and ignores, newest received_at first."""
        everything = self._owner_events(owner_id)
        changes = sorted(
            (item for item in everything if is_ai_change(item)),
            key=lambda item: (item.get("received_at", ""), item["event_id"]),
            reverse=True,
        )
        if cursor is not None:
            try:
                event_id = _decode_cursor(cursor)
            except InvalidReviewCursor as error:
                raise InvalidMessageCursor from error
            anchor = next(
                (item for item in everything if item["event_id"] == event_id), None
            )
            if anchor is None:
                raise InvalidMessageCursor
            key = (anchor.get("received_at", ""), event_id)
            changes = [
                item for item in changes
                if (item.get("received_at", ""), item["event_id"]) < key
            ]
        page = changes[:limit]
        next_cursor = (
            _encode_cursor(page[-1]["event_id"]) if len(changes) > limit else None
        )
        return page, next_cursor

    def undo_claim_item(self, owner_id: str, message_id: str, at: datetime) -> dict:
        """Transaction item that sets undone_at, only once, with the undo write."""
        return {
            "Update": {
                "TableName": self._events_table,
                "Key": self._encode(
                    {"owner_id": owner_id, "event_id": f"gmail#{message_id}"}
                ),
                "UpdateExpression": "SET undone_at = :at",
                "ConditionExpression": (
                    "attribute_exists(event_id) AND attribute_not_exists(undone_at)"
                ),
                "ExpressionAttributeValues": self._encode({":at": at.isoformat()}),
            }
        }

    def queue_reconsider(self, owner_id: str, message_id: str) -> None:
        """Send a plan-less ignored item back to the local AI queue."""
        try:
            self._client.update_item(
                TableName=self._events_table,
                Key=self._encode(
                    {"owner_id": owner_id, "event_id": f"gmail#{message_id}"}
                ),
                UpdateExpression="SET ai_backfill = :true",
                ConditionExpression=(
                    "outcome = :ignored AND attribute_not_exists(ai_plan) "
                    "AND attribute_not_exists(resolution) "
                    "AND attribute_not_exists(undone_at)"
                ),
                ExpressionAttributeValues=self._encode(
                    {":true": True, ":ignored": "ignored"}
                ),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise NotReconsiderable from None

    def get_terminal_outcome(
        self, owner_id: str, message_id: str
    ) -> StoredOutcome | None:
        prior = self._get_outcome(owner_id, message_id)
        return prior if prior is not None and prior.outcome in _TERMINAL else None

    def get_outcome(self, owner_id: str, message_id: str) -> StoredOutcome | None:
        return self._get_outcome(owner_id, message_id)

    def list_processed(
        self, owner_id: str, *, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {
            "TableName": self._events_table,
            "KeyConditionExpression": "owner_id = :owner AND begins_with(event_id, :prefix)",
            "ExpressionAttributeValues": {
                ":owner": self._serializer.serialize(owner_id),
                ":prefix": self._serializer.serialize("gmail#"),
            },
            "Limit": limit,
            "ConsistentRead": True,
        }
        if cursor is not None:
            try:
                event_id = _decode_cursor(cursor)
            except InvalidReviewCursor as error:
                raise InvalidMessageCursor from error
            query["ExclusiveStartKey"] = self._encode(
                {"owner_id": owner_id, "event_id": event_id}
            )
        response = self._client.query(**query)
        items = [self._decode(item) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = (
            _encode_cursor(self._decode(last_key)["event_id"])
            if last_key is not None
            else None
        )
        return items, next_cursor

    def record(
        self,
        *,
        owner_id: str,
        message: ParsedGmailMessage,
        decision: RuleDecision,
        application_version: int | None = None,
    ) -> StoredOutcome:
        prior = self._get_outcome(owner_id, message.message_id)
        if prior is not None and prior.outcome in _TERMINAL:
            return prior
        ai = decision.origin == "ai"
        if ai and self._users_table is None:
            raise ValueError("AI decisions require a users table")
        if decision.outcome != "applied":
            return self._save_outcome(
                owner_id, message, decision.outcome, decision.reason, decision
            )
        if decision.application_id is None or decision.status is None:
            raise ValueError("Applied decision requires application and status")
        if application_version is None:
            raise ValueError("Applied decision requires application version")

        application = self._get_application(owner_id, decision.application_id)
        if application is None or "deleted_at" in application:
            return self._save_outcome(
                owner_id, message, "review_required", "missing_application", decision
            )
        if application["version"] != application_version:
            return self._stale_application(owner_id, message, decision)
        proposed = {"status": decision.status.value}
        for field in ("season", "year"):
            value = getattr(decision, field)
            if value is not None:
                proposed[field] = value.value if isinstance(value, Enum) else value
        evaluated = self._evaluate_changes(application, proposed, message.received_at)
        if isinstance(evaluated[0], str):
            return self._save_outcome(owner_id, message, *evaluated, decision)
        changes, field_times = evaluated

        event = self._outcome_item(owner_id, message, "applied", decision.reason, decision)
        event.update(
            {
                "application_id": decision.application_id,
                "owner_application_id": f"{owner_id}#{decision.application_id}",
                "occurred_at_event_id": (
                    f"{message.received_at.isoformat()}#{event['event_id']}"
                ),
                "event_type": "updated",
                "origin": decision.origin,
                "occurred_at": message.received_at.isoformat(),
                "changes": changes,
            }
        )
        updated = self._updated_application(
            application, proposed, changes, field_times, message.received_at
        )
        try:
            self._client.transact_write_items(
                TransactItems=[
                    self._application_update_put(updated, application_version),
                    self._event_put(event),
                ] + self._guard(owner_id, decision)
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            if not self._guard_valid(owner_id):
                raise StaleSyncRun from None
            prior = self._get_outcome(owner_id, message.message_id)
            if prior is not None and prior.outcome in _TERMINAL:
                return prior
            if ai and not self._ai_valid(owner_id, decision.ai_call.settings_version):
                return self._discard_ai(owner_id, message, decision)
            latest = self._get_application(owner_id, decision.application_id)
            if latest is None or "deleted_at" in latest:
                return self._save_outcome(
                    owner_id, message, "review_required", "missing_application", decision
                )
            if latest["version"] != application_version:
                return self._stale_application(owner_id, message, decision)
            raise
        return StoredOutcome(
            message_id=message.message_id,
            outcome="applied",
            reason=decision.reason,
            application_id=decision.application_id,
        )

    def execute(
        self,
        owner_id: str,
        *,
        message: MessageRef,
        subject: str,
        sender: str,
        decision: AutoDecision,
        extraction: ValidatedExtraction,
        application_version: int | None,
        new_application_id: str | None,
        occurred_at: datetime,
        reconsider: bool = False,
    ) -> StoredOutcome:
        """Apply one automatic decision; the gmail# item is the audit event.

        reconsider=True also lets an executed, ignored, unresolved, not-undone plan
        be overwritten (still bound to the same planned_at).
        """
        if decision.action == "create" and (
            decision.fields.get("company") is None
            or decision.fields.get("status") is None
            or extraction.kind != ApplicationKind.APPLICATION_UPDATE
        ):
            raise InvalidCreateDecision("invalid create decision")
        existing = self._get_event_item(owner_id, message.message_id)
        if existing is not None and not self._replaceable(existing, reconsider):
            return self._as_outcome(existing)

        def review(outcome: StoredOutcomeName, reason: str, **extra: Any):
            item = self._execute_item(
                owner_id, message, subject, sender, outcome, reason, decision,
                extraction, existing, occurred_at,
            )
            item.update(extra)
            return self._put_outcome(
                item, execute=True, existing=existing, reconsider=reconsider
            )

        if decision.action == "ignore":
            return review("ignored", decision.reason)
        if decision.action == "needs_look":
            return review("review_required", "needs_look", rule_reason=decision.reason)
        if decision.action == "create":
            if new_application_id is None:
                raise ValueError("Create decision requires a new application ID")
            return self._execute_create(
                owner_id, message, subject, sender, decision, extraction,
                new_application_id, existing, occurred_at, reconsider,
            )
        if decision.application_id is None or application_version is None:
            raise ValueError("Update decision requires application and version")

        def demote(reason: str):
            return review("review_required", reason)

        application = self._get_application(owner_id, decision.application_id)
        if application is None or "deleted_at" in application:
            return demote("missing_application")
        if application["version"] != application_version:
            return demote("ai_proposal")
        proposed = {
            field: value.value if isinstance(value, Enum) else value
            for field, value in decision.fields.items()
        }
        evaluated = self._evaluate_changes(application, proposed, message.received_at)
        if isinstance(evaluated[0], str):
            outcome, reason = evaluated
            return review(outcome, reason)
        changes, field_times = evaluated
        event = self._execute_item(
            owner_id, message, subject, sender, "applied", decision.reason, decision,
            extraction, existing, occurred_at,
        )
        event.update(self._audit_fields(
            owner_id, decision.application_id, "updated", message, changes
        ))
        updated = self._updated_application(
            application, proposed, changes, field_times, message.received_at
        )
        try:
            self._client.transact_write_items(
                TransactItems=[
                    self._application_update_put(updated, application_version),
                    self._event_put(
                        event, execute=True, existing=existing, reconsider=reconsider
                    ),
                    *self._guard(owner_id),
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            replayed = self._after_cancel(
                owner_id, message.message_id, existing, reconsider
            )
            if replayed is not None:
                return replayed
            latest = self._get_application(owner_id, decision.application_id)
            if latest is None or "deleted_at" in latest:
                return demote("missing_application")
            if latest["version"] != application_version:
                return demote("ai_proposal")
            raise
        return self._as_outcome(event)

    def _execute_create(
        self, owner_id: str, message: MessageRef, subject: str, sender: str,
        decision: AutoDecision, extraction: ValidatedExtraction,
        application_id: str, existing: dict[str, Any] | None, occurred_at: datetime,
        reconsider: bool = False,
    ) -> StoredOutcome:
        values = {field: None for field in APPLICATION_FIELDS}
        values.update({
            field: value.value if isinstance(value, Enum) else value
            for field, value in decision.fields.items()
        })
        received = message.received_at.isoformat()
        application = {
            "owner_id": owner_id,
            "application_id": application_id,
            **values,
            "created_at": occurred_at.isoformat(),
            "updated_at": occurred_at.isoformat(),
            "version": 1,
            "field_updated_at": {
                field: received for field in decision.fields if field in values
            },
        }
        event = self._execute_item(
            owner_id, message, subject, sender, "applied", decision.reason, decision,
            extraction, existing, occurred_at,
        )
        event["application_id"] = application_id
        event.update(self._audit_fields(
            owner_id, application_id, "created", message,
            {
                field: {"before": None, "after": values[field]}
                for field in decision.fields if field in values
            },
        ))
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._applications_table,
                            "Item": self._encode(application),
                            "ConditionExpression": "attribute_not_exists(application_id)",
                        }
                    },
                    self._event_put(
                        event, execute=True, existing=existing, reconsider=reconsider
                    ),
                    *self._guard(owner_id),
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            replayed = self._after_cancel(
                owner_id, message.message_id, existing, reconsider
            )
            if replayed is not None:
                return replayed
            raise
        return self._as_outcome(event)

    def _after_cancel(
        self, owner_id: str, message_id: str, existing: dict[str, Any] | None,
        reconsider: bool = False,
    ) -> StoredOutcome | None:
        """Stale guard raises; a message that is already settled returns as stored."""
        if not self._guard_valid(owner_id):
            raise StaleSyncRun from None
        current = self._get_event_item(owner_id, message_id)
        if current is not None and (
            not self._replaceable(current, reconsider)
            or current.get("ai_plan") != (existing or {}).get("ai_plan")
        ):
            return self._as_outcome(current)
        return None

    @staticmethod
    def _replaceable(item: dict[str, Any], reconsider: bool = False) -> bool:
        """Python mirror of _EXECUTE_CONDITION for an existing event item."""
        plan = item.get("ai_plan")
        if reconsider:
            return plan is not None and is_reconsiderable(item)
        return item["outcome"] in {"pending_ai", "retryable"} or (
            plan is not None
            and "executed_at" not in plan
            and "resolution" not in item
            and "undone_at" not in item
        )

    @staticmethod
    def _audit_fields(
        owner_id: str, application_id: str, event_type: str,
        message: MessageRef, changes: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "owner_application_id": f"{owner_id}#{application_id}",
            "occurred_at_event_id": (
                f"{message.received_at.isoformat()}#gmail#{message.message_id}"
            ),
            "event_type": event_type,
            "occurred_at": message.received_at.isoformat(),
            "changes": changes,
        }

    @staticmethod
    def _execute_item(
        owner_id: str, message: MessageRef, subject: str, sender: str,
        outcome: StoredOutcomeName, reason: str, decision: AutoDecision,
        extraction: ValidatedExtraction, existing: dict[str, Any] | None,
        occurred_at: datetime,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "owner_id": owner_id,
            "event_id": f"gmail#{message.message_id}",
            "source_message_id": message.message_id,
            "thread_id": message.thread_id,
            "received_at": message.received_at.isoformat(),
            "outcome": outcome,
            "reason": reason,
            "origin": "ai",
            "ai_proposal": DynamoEmailRepository._proposal(extraction),
        }
        if subject:
            item["subject"] = subject[:_MAX_HEADER]
        if sender:
            item["sender"] = sender[:_MAX_HEADER]
        if decision.application_id is not None:
            item["application_id"] = decision.application_id
        if decision.requisition_id is not None:
            item["requisition_id"] = decision.requisition_id
        if outcome == "review_required":
            for key, value in (
                ("proposed_status", extraction.status),
                ("proposed_season", extraction.season),
                ("proposed_year", extraction.year),
            ):
                if value is not None:
                    item[key] = value.value if isinstance(value, Enum) else value
        item["ai_plan"] = {
            **((existing or {}).get("ai_plan") or {}),
            "executed_at": occurred_at.isoformat(),
        }
        return item

    def _get_event_item(self, owner_id: str, message_id: str) -> dict[str, Any] | None:
        response = self._client.get_item(
            TableName=self._events_table,
            Key=self._encode(
                {"owner_id": owner_id, "event_id": f"gmail#{message_id}"}
            ),
            ConsistentRead=True,
        )
        return self._decode(response["Item"]) if "Item" in response else None

    def record_unparseable(
        self, *, owner_id: str, message_id: str,
        thread_id: str | None = None, received_at: datetime | None = None,
        subject: str | None = None, sender: str | None = None,
    ) -> StoredOutcome:
        prior = self._get_outcome(owner_id, message_id)
        if prior is not None and prior.outcome in _TERMINAL:
            return prior
        item = {
            "owner_id": owner_id,
            "event_id": f"gmail#{message_id}",
            "source_message_id": message_id,
            "outcome": "review_required",
            "reason": "unparseable_message",
        }
        if thread_id is not None:
            item["thread_id"] = thread_id
        if received_at is not None:
            item["received_at"] = received_at.isoformat()
        if subject:
            item["subject"] = subject[:_MAX_HEADER]
        if sender:
            item["sender"] = sender[:_MAX_HEADER]
        return self._put_outcome(item)

    def record_unavailable(self, *, owner_id: str, message_id: str) -> StoredOutcome:
        prior = self._get_outcome(owner_id, message_id)
        if prior is not None and prior.outcome in _TERMINAL:
            return prior
        return self._put_outcome({
            "owner_id": owner_id,
            "event_id": f"gmail#{message_id}",
            "source_message_id": message_id,
            "outcome": "ignored",
            "reason": "message_unavailable",
        })

    def _save_outcome(
        self,
        owner_id: str,
        message: ParsedGmailMessage,
        outcome: StoredOutcomeName,
        reason: str,
        decision: RuleDecision,
    ) -> StoredOutcome:
        item = self._outcome_item(owner_id, message, outcome, reason, decision)
        try:
            return self._put_outcome(item, decision)
        except _AiSettingsStale:
            return self._discard_ai(owner_id, message, decision)

    def _stale_application(
        self, owner_id: str, message: ParsedGmailMessage, decision: RuleDecision
    ) -> StoredOutcome:
        if decision.origin == "ai":
            # Never retryable: a redelivery would re-run the provider and re-spend budget.
            return self._save_outcome(
                owner_id, message, "review_required", "ai_proposal", decision
            )
        return self._save_outcome(
            owner_id, message, "retryable", "stale_application", decision
        )

    def _discard_ai(
        self, owner_id: str, message: ParsedGmailMessage, decision: RuleDecision
    ) -> StoredOutcome:
        fallback = replace(decision.ai_call.rule_decision, ai_note="ai_unavailable")
        item = self._outcome_item(
            owner_id, message, fallback.outcome, fallback.reason, fallback
        )
        return self._put_outcome(item, fallback)

    def _put_outcome(
        self, item: dict[str, Any], decision: RuleDecision | None = None,
        *, execute: bool = False, existing: dict[str, Any] | None = None,
        reconsider: bool = False,
    ) -> StoredOutcome:
        put = self._event_put(
            item, execute=execute, existing=existing, reconsider=reconsider
        )["Put"]
        try:
            if self._users_table is None:
                self._client.put_item(**put)
            else:
                self._client.transact_write_items(
                    TransactItems=[
                        {"Put": put}, *self._guard(item["owner_id"], decision)
                    ]
                )
        except ClientError as error:
            code = error.response["Error"]["Code"]
            expected_code = (
                "ConditionalCheckFailedException"
                if self._users_table is None else "TransactionCanceledException"
            )
            if code != expected_code:
                raise
            if not self._guard_valid(item["owner_id"]):
                raise StaleSyncRun from None
            prior = self._get_outcome(item["owner_id"], item["source_message_id"])
            if (
                decision is not None and decision.origin == "ai"
                and (prior is None or prior.outcome not in _TERMINAL)
                and not self._ai_valid(
                    item["owner_id"], decision.ai_call.settings_version
                )
            ):
                raise _AiSettingsStale from None
            if prior is None:
                raise
            return prior
        return self._as_outcome(item)

    def _event_put(
        self, item: dict[str, Any], *, execute: bool = False,
        existing: dict[str, Any] | None = None, reconsider: bool = False,
    ) -> dict:
        values: dict[str, Any] = {":retryable": "retryable"}
        condition = _RECORD_CONDITION
        if execute:
            values[":pending_ai"] = "pending_ai"
            condition = _EXECUTE_CONDITION
            if reconsider:
                condition = _RECONSIDER_CONDITION
                del values[":retryable"], values[":pending_ai"]
                values[":ignored"] = "ignored"
            # Bind the write to the item (and plan) that was read, so a different
            # plan stored since then is never marked executed.
            if existing is None:
                condition = "attribute_not_exists(event_id)"
                del values[":retryable"], values[":pending_ai"]
            elif "ai_plan" not in existing:
                condition = f"({condition}) AND attribute_not_exists(ai_plan)"
            elif "planned_at" in existing["ai_plan"]:
                condition = f"({condition}) AND ai_plan.planned_at = :planned_at"
                values[":planned_at"] = existing["ai_plan"]["planned_at"]
            else:
                condition = (
                    f"({condition}) AND attribute_not_exists(ai_plan.planned_at)"
                )
        put: dict[str, Any] = {
            "TableName": self._events_table,
            "Item": self._encode(item),
            "ConditionExpression": condition,
        }
        if values:
            put["ExpressionAttributeValues"] = {
                key: self._serializer.serialize(value)
                for key, value in values.items()
            }
        return {"Put": put}

    def _application_update_put(self, updated: dict[str, Any], version: int) -> dict:
        return {
            "Put": {
                "TableName": self._applications_table,
                "Item": self._encode(updated),
                "ConditionExpression": (
                    "attribute_exists(application_id) AND "
                    "attribute_not_exists(deleted_at) AND "
                    "version = :version"
                ),
                "ExpressionAttributeValues": {
                    ":version": self._serializer.serialize(version)
                },
            }
        }

    @staticmethod
    def _evaluate_changes(
        application: dict[str, Any], proposed: dict[str, Any], received_at: datetime
    ) -> tuple[str, str] | tuple[dict[str, Any], dict[str, str]]:
        """Return a demotion (outcome, reason) or (changes, field_times)."""
        for field in ("season", "year"):
            if field in proposed and application[field] not in {None, proposed[field]}:
                return "review_required", "cycle_conflict"
        changes = {
            field: {"before": application[field], "after": value}
            for field, value in proposed.items()
            if application[field] != value
        }
        if not changes:
            return "ignored", "no_change"
        field_times = field_times_for(application)
        if any(
            datetime.fromisoformat(field_times[field]) >= received_at
            for field in changes
            if field in field_times
        ):
            return "review_required", "older_evidence"
        return changes, field_times

    @staticmethod
    def _updated_application(
        application: dict[str, Any], proposed: dict[str, Any],
        changes: dict[str, Any], field_times: dict[str, str], received_at: datetime,
    ) -> dict[str, Any]:
        return {
            **application,
            **proposed,
            "updated_at": max(
                datetime.fromisoformat(application["updated_at"]), received_at
            ).isoformat(),
            "version": application["version"] + 1,
            "field_updated_at": {
                **field_times,
                **{field: received_at.isoformat() for field in changes},
            },
        }

    def _guard(self, owner_id: str, decision: RuleDecision | None = None) -> list[dict]:
        if self._users_table is None:
            return []
        condition = (
            "generation = :generation AND active_run_id = :run "
            "AND attribute_exists(encrypted_token)"
        )
        values = {
            ":generation": self._serializer.serialize(self._guard_generation),
            ":run": self._serializer.serialize(self._guard_run_id),
        }
        if decision is not None and decision.origin == "ai":
            # One check per item: DynamoDB rejects two operations on the Users item.
            condition += (
                " AND ai_settings_version = :ai_version AND ai_enabled = :true "
                "AND ai_key_status = :ok"
            )
            values |= {
                ":ai_version": self._serializer.serialize(
                    decision.ai_call.settings_version
                ),
                ":true": self._serializer.serialize(True),
                ":ok": self._serializer.serialize("ok"),
            }
        return [{
            "ConditionCheck": {
                "TableName": self._users_table,
                "Key": self._encode({"owner_id": owner_id}),
                "ConditionExpression": condition,
                "ExpressionAttributeValues": values,
            }
        }]

    def _ai_valid(self, owner_id: str, settings_version: int) -> bool:
        """Re-read the Users item to tell an AI-settings failure from a guard failure."""
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        user = self._decode(response["Item"]) if "Item" in response else {}
        return (
            user.get("ai_settings_version") == settings_version
            and user.get("ai_enabled") is True
            and user.get("ai_key_status") == "ok"
        )

    def _guard_valid(self, owner_id: str) -> bool:
        if self._users_table is None:
            return True
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        user = self._decode(response["Item"]) if "Item" in response else {}
        return (
            user.get("generation") == self._guard_generation
            and user.get("active_run_id") == self._guard_run_id
            and "encrypted_token" in user
        )

    def _get_outcome(self, owner_id: str, message_id: str) -> StoredOutcome | None:
        response = self._client.get_item(
            TableName=self._events_table,
            Key=self._encode(
                {"owner_id": owner_id, "event_id": f"gmail#{message_id}"}
            ),
            ConsistentRead=True,
        )
        if "Item" not in response:
            return None
        return self._as_outcome(self._decode(response["Item"]))

    def _get_application(self, owner_id: str, application_id: str) -> dict | None:
        response = self._client.get_item(
            TableName=self._applications_table,
            Key=self._encode(
                {"owner_id": owner_id, "application_id": application_id}
            ),
            ConsistentRead=True,
        )
        return self._decode(response["Item"]) if "Item" in response else None

    @staticmethod
    def _outcome_item(
        owner_id: str,
        message: ParsedGmailMessage,
        outcome: StoredOutcomeName,
        reason: str,
        decision: RuleDecision,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "owner_id": owner_id,
            "event_id": f"gmail#{message.message_id}",
            "source_message_id": message.message_id,
            "thread_id": message.thread_id,
            "received_at": message.received_at.isoformat(),
            "outcome": outcome,
            "reason": reason,
            "evidence": decision.evidence[:500],
        }
        if outcome == "review_required":
            if message.subject:
                item["subject"] = message.subject[:_MAX_HEADER]
            if message.sender:
                item["sender"] = message.sender[:_MAX_HEADER]
        if decision.application_id is not None:
            item["application_id"] = decision.application_id
        if decision.status is not None:
            item["proposed_status"] = decision.status.value
        if decision.season is not None:
            item["proposed_season"] = (
                decision.season.value
                if isinstance(decision.season, Enum)
                else decision.season
            )
        if decision.year is not None:
            item["proposed_year"] = decision.year
        if decision.requisition_id is not None:
            item["requisition_id"] = decision.requisition_id
        if decision.origin == "ai":
            item["origin"] = "ai"
            item["rule_reason"] = decision.rule_reason
            item["ai_provider"] = decision.ai_call.provider
            item["ai_model"] = decision.ai_call.model
            item["ai_proposal"] = DynamoEmailRepository._proposal(decision.ai_proposal)
        if decision.ai_note is not None:
            item["ai_note"] = decision.ai_note
        return item

    @staticmethod
    def _proposal(proposal) -> dict[str, Any]:
        stored: dict[str, Any] = {}
        for field in _PROPOSAL_FIELDS:
            value = getattr(proposal, field)
            if value is not None:
                stored[field] = value.value if isinstance(value, Enum) else value
        if proposal.kind is not None:
            stored["kind"] = proposal.kind.value
        stored["evidence"] = dict(proposal.evidence)
        stored["status_in_subject_only"] = bool(proposal.status_in_subject_only)
        return stored

    @staticmethod
    def _as_outcome(item: dict[str, Any]) -> StoredOutcome:
        return StoredOutcome(
            message_id=item["source_message_id"],
            outcome=item["outcome"],
            reason=item["reason"],
            application_id=item.get("application_id"),
        )

    def _encode(self, item: dict[str, Any]) -> dict:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _decode(self, item: dict) -> dict[str, Any]:
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}
