import builtins
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import date, datetime
from enum import Enum
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.applications import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationPage,
    ApplicationPatch,
    EventOrigin,
    EventType,
)
from app.repositories.applications import (
    ApplicationNotFound,
    InvalidCursor,
    VersionConflict,
)

APPLICATION_FIELDS = (
    "company",
    "role",
    "role_type",
    "season",
    "year",
    "status",
    "application_date",
)


class DynamoApplicationRepository:
    def __init__(
        self,
        *,
        client,
        applications_table: str,
        events_table: str,
    ) -> None:
        self._client = client
        self._applications_table = applications_table
        self._events_table = events_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def create(
        self,
        *,
        owner_id: str,
        payload: ApplicationCreate,
        application_id: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Application:
        values = payload.model_dump(mode="json")
        application_item = {
            "owner_id": owner_id,
            "application_id": application_id,
            **values,
            "created_at": occurred_at.isoformat(),
            "updated_at": occurred_at.isoformat(),
            "version": 1,
        }
        event_item = {
            "owner_id": owner_id,
            "event_id": event_id,
            "application_id": application_id,
            "owner_application_id": f"{owner_id}#{application_id}",
            "occurred_at_event_id": f"{occurred_at.isoformat()}#{event_id}",
            "event_type": EventType.CREATED.value,
            "origin": EventOrigin.USER.value,
            "occurred_at": occurred_at.isoformat(),
            "changes": {
                field: {"before": None, "after": values[field]}
                for field in APPLICATION_FIELDS
            },
        }
        self._client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": self._applications_table,
                        "Item": self._serialize_item(application_item),
                        "ConditionExpression": "attribute_not_exists(application_id)",
                    }
                },
                {
                    "Put": {
                        "TableName": self._events_table,
                        "Item": self._serialize_item(event_item),
                        "ConditionExpression": "attribute_not_exists(event_id)",
                    }
                },
            ]
        )
        return self._application_from_item(application_item)

    def list(
        self,
        *,
        owner_id: str,
        limit: int,
        cursor: str | None,
    ) -> ApplicationPage:
        query: dict[str, Any] = {
            "TableName": self._applications_table,
            "KeyConditionExpression": "owner_id = :owner_id",
            "ExpressionAttributeValues": {
                ":owner_id": self._serializer.serialize(owner_id),
            },
            "FilterExpression": "attribute_not_exists(deleted_at)",
            "Limit": limit,
        }
        if cursor is not None:
            application_id = _decode_cursor(cursor)
            query["ExclusiveStartKey"] = self._serialize_item(
                {"owner_id": owner_id, "application_id": application_id}
            )

        response = self._client.query(**query)
        items = [
            Application.model_validate(self._deserialize_item(item))
            for item in response.get("Items", [])
        ]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = None
        if last_key is not None:
            decoded_key = self._deserialize_item(last_key)
            next_cursor = _encode_cursor(decoded_key["application_id"])

        return ApplicationPage(items=items, next_cursor=next_cursor)

    def get(self, *, owner_id: str, application_id: str) -> Application | None:
        item = self._get_stored_item(
            owner_id=owner_id,
            application_id=application_id,
        )
        if item is None:
            return None
        if "deleted_at" in item:
            return None
        return self._application_from_item(item)

    def list_events(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> builtins.list[ApplicationEvent]:
        response = self._client.query(
            TableName=self._events_table,
            IndexName="application-events-index",
            KeyConditionExpression="owner_application_id = :owner_application_id",
            ExpressionAttributeValues={
                ":owner_application_id": self._serializer.serialize(
                    f"{owner_id}#{application_id}"
                )
            },
            ScanIndexForward=True,
        )
        return [
            ApplicationEvent.model_validate(self._deserialize_item(item))
            for item in response.get("Items", [])
        ]

    def update(
        self,
        *,
        owner_id: str,
        application_id: str,
        patch: ApplicationPatch,
        event_id: str,
        occurred_at: datetime,
    ) -> Application:
        current = self._get_active_item(
            owner_id=owner_id,
            application_id=application_id,
        )
        if current["version"] != patch.expected_version:
            raise VersionConflict

        updates = {
            field: _stored_value(value) for field, value in patch.updates().items()
        }
        changes = {
            field: {"before": current[field], "after": value}
            for field, value in updates.items()
            if current[field] != value
        }
        if not changes:
            return self._application_from_item(current)

        updated_item = {
            **current,
            **updates,
            "updated_at": occurred_at.isoformat(),
            "version": patch.expected_version + 1,
        }
        event_item = self._mutation_event_item(
            owner_id=owner_id,
            application_id=application_id,
            event_id=event_id,
            event_type=EventType.UPDATED,
            occurred_at=occurred_at,
            changes=changes,
        )
        self._write_mutation(
            owner_id=owner_id,
            application_id=application_id,
            expected_version=patch.expected_version,
            application_item=updated_item,
            event_item=event_item,
        )
        return self._application_from_item(updated_item)

    def delete(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_version: int,
        event_id: str,
        occurred_at: datetime,
    ) -> None:
        current = self._get_active_item(
            owner_id=owner_id,
            application_id=application_id,
        )
        if current["version"] != expected_version:
            raise VersionConflict

        tombstone = {
            "owner_id": owner_id,
            "application_id": application_id,
            "version": expected_version + 1,
            "deleted_at": occurred_at.isoformat(),
        }
        event_item = self._mutation_event_item(
            owner_id=owner_id,
            application_id=application_id,
            event_id=event_id,
            event_type=EventType.DELETED,
            occurred_at=occurred_at,
            changes={
                field: {"before": current[field], "after": None}
                for field in APPLICATION_FIELDS
            },
        )
        self._write_mutation(
            owner_id=owner_id,
            application_id=application_id,
            expected_version=expected_version,
            application_item=tombstone,
            event_item=event_item,
        )

    def _write_mutation(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_version: int,
        application_item: dict[str, Any],
        event_item: dict[str, Any],
    ) -> None:
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._applications_table,
                            "Item": self._serialize_item(application_item),
                            "ConditionExpression": (
                                "attribute_exists(application_id) AND "
                                "attribute_not_exists(deleted_at) AND "
                                "version = :expected_version"
                            ),
                            "ExpressionAttributeValues": {
                                ":expected_version": self._serializer.serialize(
                                    expected_version
                                )
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._events_table,
                            "Item": self._serialize_item(event_item),
                            "ConditionExpression": "attribute_not_exists(event_id)",
                        }
                    },
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            latest = self._get_stored_item(
                owner_id=owner_id,
                application_id=application_id,
            )
            if latest is None or "deleted_at" in latest:
                raise ApplicationNotFound from error
            if latest["version"] != expected_version:
                raise VersionConflict from error
            raise

    def _get_active_item(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> dict[str, Any]:
        item = self._get_stored_item(
            owner_id=owner_id,
            application_id=application_id,
        )
        if item is None or "deleted_at" in item:
            raise ApplicationNotFound
        return item

    def _get_stored_item(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> dict[str, Any] | None:
        response = self._client.get_item(
            TableName=self._applications_table,
            Key=self._serialize_item(
                {"owner_id": owner_id, "application_id": application_id}
            ),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        return self._deserialize_item(item)

    @staticmethod
    def _mutation_event_item(
        *,
        owner_id: str,
        application_id: str,
        event_id: str,
        event_type: EventType,
        occurred_at: datetime,
        changes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "owner_id": owner_id,
            "event_id": event_id,
            "application_id": application_id,
            "owner_application_id": f"{owner_id}#{application_id}",
            "occurred_at_event_id": f"{occurred_at.isoformat()}#{event_id}",
            "event_type": event_type.value,
            "origin": EventOrigin.USER.value,
            "occurred_at": occurred_at.isoformat(),
            "changes": changes,
        }

    def _serialize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _deserialize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: self._deserializer.deserialize(value) for key, value in item.items()
        }

    @staticmethod
    def _application_from_item(item: dict[str, Any]) -> Application:
        return Application.model_validate(item)


def _encode_cursor(application_id: str) -> str:
    payload = json.dumps(
        {"application_id": application_id},
        separators=(",", ":"),
    ).encode()
    return urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(urlsafe_b64decode(cursor + padding))
    except (Base64Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidCursor from error

    if (
        not isinstance(payload, dict)
        or set(payload) != {"application_id"}
        or not isinstance(payload["application_id"], str)
        or not payload["application_id"]
    ):
        raise InvalidCursor
    return payload["application_id"]


def _stored_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    return value
