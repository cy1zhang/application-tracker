import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from app.domain.applications import Application, ApplicationPage
from app.repositories.applications import InvalidCursor


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

    def _serialize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _deserialize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: self._deserializer.deserialize(value) for key, value in item.items()
        }


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
