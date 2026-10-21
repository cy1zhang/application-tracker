from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.repositories.dynamodb import field_times_for
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage

StoredOutcomeName = Literal["applied", "ignored", "review_required", "retryable"]
_TERMINAL = {"applied", "ignored", "review_required"}


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
        guard_run_id: str | None = None,
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
        self._guard_generation = guard_generation
        self._guard_run_id = guard_run_id
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

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
            return self._save_outcome(
                owner_id, message, "retryable", "stale_application", decision
            )
        proposed = {"status": decision.status.value}
        for field in ("season", "year"):
            value = getattr(decision, field)
            if value is None:
                continue
            value = value.value if isinstance(value, Enum) else value
            if application[field] not in {None, value}:
                return self._save_outcome(
                    owner_id, message, "review_required", "cycle_conflict", decision
                )
            proposed[field] = value
        changes = {
            field: {"before": application[field], "after": value}
            for field, value in proposed.items()
            if application[field] != value
        }
        if not changes:
            return self._save_outcome(
                owner_id, message, "ignored", "no_change", decision
            )
        field_times = field_times_for(application)
        if any(
            datetime.fromisoformat(field_times[field]) >= message.received_at
            for field in changes
            if field in field_times
        ):
            return self._save_outcome(
                owner_id, message, "review_required", "older_evidence", decision
            )

        event = self._outcome_item(owner_id, message, "applied", decision.reason, decision)
        event.update(
            {
                "application_id": decision.application_id,
                "owner_application_id": f"{owner_id}#{decision.application_id}",
                "occurred_at_event_id": (
                    f"{message.received_at.isoformat()}#{event['event_id']}"
                ),
                "event_type": "updated",
                "origin": "rules",
                "occurred_at": message.received_at.isoformat(),
                "changes": changes,
            }
        )
        updated = {
            **application,
            **proposed,
            "updated_at": max(
                datetime.fromisoformat(application["updated_at"]), message.received_at
            ).isoformat(),
            "version": application_version + 1,
            "field_updated_at": {
                **field_times,
                **{field: message.received_at.isoformat() for field in changes},
            },
        }
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._applications_table,
                            "Item": self._encode(updated),
                            "ConditionExpression": (
                                "attribute_exists(application_id) AND "
                                "attribute_not_exists(deleted_at) AND "
                                "version = :version"
                            ),
                            "ExpressionAttributeValues": {
                                ":version": self._serializer.serialize(application_version)
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._events_table,
                            "Item": self._encode(event),
                            "ConditionExpression": (
                                "attribute_not_exists(event_id) OR outcome = :retryable"
                            ),
                            "ExpressionAttributeValues": {
                                ":retryable": self._serializer.serialize("retryable")
                            },
                        }
                    },
                ] + self._guard(owner_id)
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            if not self._guard_valid(owner_id):
                raise StaleSyncRun from None
            prior = self._get_outcome(owner_id, message.message_id)
            if prior is not None and prior.outcome in _TERMINAL:
                return prior
            latest = self._get_application(owner_id, decision.application_id)
            if latest is None or "deleted_at" in latest:
                return self._save_outcome(
                    owner_id, message, "review_required", "missing_application", decision
                )
            if latest["version"] != application_version:
                return self._save_outcome(
                    owner_id, message, "retryable", "stale_application", decision
                )
            raise
        return StoredOutcome(
            message_id=message.message_id,
            outcome="applied",
            reason=decision.reason,
            application_id=decision.application_id,
        )

    def record_unparseable(
        self, *, owner_id: str, message_id: str,
        thread_id: str | None = None, received_at: datetime | None = None,
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
        return self._put_outcome(item)

    def _put_outcome(self, item: dict[str, Any]) -> StoredOutcome:
        put = {
            "TableName": self._events_table,
            "Item": self._encode(item),
            "ConditionExpression": (
                "attribute_not_exists(event_id) OR outcome = :retryable"
            ),
            "ExpressionAttributeValues": {
                ":retryable": self._serializer.serialize("retryable")
            },
        }
        try:
            if self._users_table is None:
                self._client.put_item(**put)
            else:
                self._client.transact_write_items(
                    TransactItems=[{"Put": put}, *self._guard(item["owner_id"])]
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
            if prior is None:
                raise
            return prior
        return self._as_outcome(item)

    def _guard(self, owner_id: str) -> list[dict]:
        if self._users_table is None:
            return []
        return [{
            "ConditionCheck": {
                "TableName": self._users_table,
                "Key": self._encode({"owner_id": owner_id}),
                "ConditionExpression": (
                    "generation = :generation AND active_run_id = :run "
                    "AND attribute_exists(encrypted_token)"
                ),
                "ExpressionAttributeValues": {
                    ":generation": self._serializer.serialize(self._guard_generation),
                    ":run": self._serializer.serialize(self._guard_run_id),
                },
            }
        }]

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
                item["subject"] = message.subject
            if message.sender:
                item["sender"] = message.sender
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
        return item

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
