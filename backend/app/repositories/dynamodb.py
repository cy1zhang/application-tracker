import builtins
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import datetime
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from app.domain.applications import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationPage,
    EventOrigin,
    EventType,
)
from app.repositories.applications import InvalidCursor

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
        decoded = self._deserialize_item(item)
        if "deleted_at" in decoded:
            return None
        return self._application_from_item(decoded)

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
