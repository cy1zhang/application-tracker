import builtins
import json
import re
from base64 import b64decode, urlsafe_b64encode
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
    MergeRequest,
)
from app.repositories.applications import (
    ApplicationNotFound,
    InvalidCursor,
    InvalidMerge,
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
            "field_updated_at": {
                field: occurred_at.isoformat()
                for field in APPLICATION_FIELDS
                if values[field] is not None
            },
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
            "ConsistentRead": True,
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
        item = self._resolve_item(owner_id=owner_id, application_id=application_id)
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
        retained = self._resolve_item(owner_id=owner_id, application_id=application_id)
        if retained is None:
            return []
        items = []
        for source_id in [retained["application_id"], *retained.get("merged_from", [])]:
            query: dict[str, Any] = {
                "TableName": self._events_table,
                "IndexName": "application-events-index",
                "KeyConditionExpression": "owner_application_id = :owner_application_id",
                "ExpressionAttributeValues": {
                    ":owner_application_id": self._serializer.serialize(
                        f"{owner_id}#{source_id}"
                    )
                },
                "ScanIndexForward": True,
            }
            while True:
                response = self._client.query(**query)
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if last_key is None:
                    break
                query["ExclusiveStartKey"] = last_key

        events = [
            ApplicationEvent.model_validate(self._deserialize_item(item))
            for item in items
        ]
        return sorted(events, key=lambda event: (event.occurred_at, event.event_id))

    def update(
        self,
        *,
        owner_id: str,
        application_id: str,
        patch: ApplicationPatch,
        event_id: str,
        occurred_at: datetime,
        extra_items: builtins.list[dict[str, Any]] | None = None,
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
            "field_updated_at": {
                **field_times_for(current),
                **{field: occurred_at.isoformat() for field in changes},
            },
            "field_corrections": {
                **current.get("field_corrections", {}),
                **{field: occurred_at.isoformat() for field in changes},
            },
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
            extra_items=extra_items,
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
        extra_items: builtins.list[dict[str, Any]] | None = None,
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
            extra_items=extra_items,
        )

    def merge(
        self,
        *,
        owner_id: str,
        keep_id: str,
        payload: MergeRequest,
        event_id: str,
        occurred_at: datetime,
    ) -> Application:
        duplicate_id = payload.duplicate_id
        if keep_id == duplicate_id:
            raise InvalidMerge
        keep = self._get_active_item(owner_id=owner_id, application_id=keep_id)
        duplicate = self._get_active_item(owner_id=owner_id, application_id=duplicate_id)
        if (
            keep["version"] != payload.expected_keep_version
            or duplicate["version"] != payload.expected_duplicate_version
        ):
            raise VersionConflict

        conflicts = {
            field
            for field in APPLICATION_FIELDS
            if keep[field] is not None
            and duplicate[field] is not None
            and keep[field] != duplicate[field]
        }
        if set(payload.resolutions) != conflicts:
            raise InvalidMerge
        values = {
            field: (
                duplicate[field]
                if payload.resolutions.get(field) == "duplicate" or keep[field] is None
                else keep[field]
            )
            for field in APPLICATION_FIELDS
        }
        changes = {
            field: {"before": keep[field], "after": value}
            for field, value in values.items()
            if keep[field] != value
        }
        decided_fields = changes.keys() | conflicts
        merged_times = _latest_times(field_times_for(keep), field_times_for(duplicate))
        merged_corrections = _latest_times(
            keep.get("field_corrections", {}), duplicate.get("field_corrections", {})
        )
        retained = {
            **keep,
            **values,
            "version": payload.expected_keep_version + 1,
            "updated_at": occurred_at.isoformat(),
            "merged_from": list(dict.fromkeys([
                *keep.get("merged_from", []), duplicate_id, *duplicate.get("merged_from", [])
            ])),
            "field_updated_at": {
                **merged_times,
                **{field: occurred_at.isoformat() for field in decided_fields},
            },
            "field_corrections": {
                **merged_corrections,
                **{field: occurred_at.isoformat() for field in decided_fields},
            },
        }
        tombstone = {
            **duplicate,
            "version": payload.expected_duplicate_version + 1,
            "deleted_at": occurred_at.isoformat(),
            "merged_into": keep_id,
        }
        event = self._mutation_event_item(
            owner_id=owner_id,
            application_id=keep_id,
            event_id=event_id,
            event_type=EventType.MERGED,
            occurred_at=occurred_at,
            changes=changes,
        )
        event["merged_application_id"] = duplicate_id
        event["merge_resolutions"] = payload.resolutions

        try:
            self._client.transact_write_items(TransactItems=[
                self._merge_put(keep_id, owner_id, payload.expected_keep_version, retained),
                self._merge_put(duplicate_id, owner_id, payload.expected_duplicate_version, tombstone),
                {"Put": {
                    "TableName": self._events_table,
                    "Item": self._serialize_item(event),
                    "ConditionExpression": "attribute_not_exists(event_id)",
                }},
            ])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            latest_keep = self._get_stored_item(owner_id=owner_id, application_id=keep_id)
            latest_duplicate = self._get_stored_item(owner_id=owner_id, application_id=duplicate_id)
            if (
                latest_keep is None or "deleted_at" in latest_keep
                or latest_duplicate is None or "deleted_at" in latest_duplicate
            ):
                raise ApplicationNotFound from error
            if (
                latest_keep["version"] != payload.expected_keep_version
                or latest_duplicate["version"] != payload.expected_duplicate_version
            ):
                raise VersionConflict from error
            raise
        return self._application_from_item(retained)

    def _merge_put(
        self, application_id: str, owner_id: str, version: int, item: dict[str, Any]
    ) -> dict[str, Any]:
        return {"Put": {
            "TableName": self._applications_table,
            "Item": self._serialize_item(item),
            "ConditionExpression": (
                "attribute_exists(application_id) AND "
                "attribute_not_exists(deleted_at) AND version = :version"
            ),
            "ExpressionAttributeValues": {":version": self._serializer.serialize(version)},
        }}

    def _resolve_item(self, *, owner_id: str, application_id: str) -> dict[str, Any] | None:
        visited: set[str] = set()
        while application_id not in visited:
            visited.add(application_id)
            item = self._get_stored_item(owner_id=owner_id, application_id=application_id)
            if item is None or "merged_into" not in item:
                return item
            application_id = item["merged_into"]
        return None

    def _write_mutation(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_version: int,
        application_item: dict[str, Any],
        event_item: dict[str, Any],
        extra_items: builtins.list[dict[str, Any]] | None = None,
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
                    *(extra_items or []),
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
    if re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None:
        raise InvalidCursor

    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(decoded)
    except (Base64Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
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


def field_times_for(item: dict[str, Any]) -> dict[str, str]:
    if "field_updated_at" in item:
        return dict(item["field_updated_at"])
    return {
        field: item["updated_at"]
        for field in APPLICATION_FIELDS
        if item.get(field) is not None
    }


def _latest_times(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    merged = dict(left)
    for field, timestamp in right.items():
        if field not in merged or datetime.fromisoformat(timestamp) > datetime.fromisoformat(merged[field]):
            merged[field] = timestamp
    return merged
