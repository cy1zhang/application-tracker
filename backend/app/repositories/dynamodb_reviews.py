import json
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import datetime
from typing import Any
from urllib.parse import quote

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.applications import EventType
from app.domain.reviews import (
    ReviewItem,
    ReviewPage,
    ReviewResolution,
    ReviewResolutionResult,
)
from app.repositories.dynamodb import APPLICATION_FIELDS, _stored_value, field_times_for


class ReviewNotFound(Exception):
    pass


class ReviewAlreadyResolved(Exception):
    pass


class ReviewVersionConflict(Exception):
    pass


class InvalidReviewCursor(Exception):
    pass


class InvalidReviewResolution(Exception):
    pass


class DynamoReviewRepository:
    def __init__(self, *, client, applications_table: str, events_table: str) -> None:
        self._client = client
        self._applications_table = applications_table
        self._events_table = events_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def list_pending(self, *, owner_id: str, limit: int, cursor: str | None) -> ReviewPage:
        query: dict[str, Any] = {
            "TableName": self._events_table,
            "KeyConditionExpression": "owner_id = :owner AND begins_with(event_id, :prefix)",
            "FilterExpression": "outcome = :review AND attribute_not_exists(resolution)",
            "ExpressionAttributeValues": {
                ":owner": self._serializer.serialize(owner_id),
                ":prefix": self._serializer.serialize("gmail#"),
                ":review": self._serializer.serialize("review_required"),
            },
            "Limit": limit,
            "ConsistentRead": True,
        }
        if cursor is not None:
            query["ExclusiveStartKey"] = self._encode(
                {"owner_id": owner_id, "event_id": _decode_cursor(cursor)}
            )
        response = self._client.query(**query)
        items = [self._as_review(self._decode(item)) for item in response.get("Items", [])]
        last_key = response.get("LastEvaluatedKey")
        next_cursor = (
            _encode_cursor(self._decode(last_key)["event_id"])
            if last_key is not None
            else None
        )
        return ReviewPage(items=items, next_cursor=next_cursor)

    def resolve(
        self,
        *,
        owner_id: str,
        message_id: str,
        resolution: ReviewResolution,
        occurred_at: datetime,
        new_application_id: str | None = None,
    ) -> ReviewResolutionResult:
        review = self._get_review(owner_id, message_id)
        if review is None or review.get("outcome") != "review_required":
            raise ReviewNotFound
        if "resolution" in review:
            raise ReviewAlreadyResolved

        if resolution.action == "reject":
            resolved = {
                **review,
                "resolution": "reject",
                "resolved_at": occurred_at.isoformat(),
            }
            self._transact(owner_id, message_id, resolved, [])
            return ReviewResolutionResult(message_id=message_id, action="reject")

        if resolution.action == "create":
            if new_application_id is None or resolution.application is None:
                raise InvalidReviewResolution
            values = resolution.application.model_dump(mode="json")
            application = {
                "owner_id": owner_id,
                "application_id": new_application_id,
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
            changes = {
                field: {"before": None, "after": values[field]}
                for field in APPLICATION_FIELDS
            }
            resolved = self._linked_review(
                review, owner_id, new_application_id, "create", EventType.CREATED,
                changes, occurred_at,
            )
            self._transact(
                owner_id,
                message_id,
                resolved,
                [{"Put": {
                    "TableName": self._applications_table,
                    "Item": self._encode(application),
                    "ConditionExpression": "attribute_not_exists(application_id)",
                }}],
            )
            return ReviewResolutionResult(
                message_id=message_id, action="create", application_id=new_application_id
            )

        application_id = (
            review.get("application_id")
            if resolution.action == "accept"
            else resolution.application_id
        )
        if not application_id:
            raise InvalidReviewResolution
        application = self._get_application(owner_id, application_id)
        if application is None or "deleted_at" in application:
            raise ReviewNotFound
        expected_version = (
            resolution.patch.expected_version
            if resolution.action == "edit" and resolution.patch is not None
            else resolution.expected_version
        )
        if expected_version is None:
            raise InvalidReviewResolution
        if application["version"] != expected_version:
            raise ReviewVersionConflict

        if resolution.action == "accept":
            updates = {
                field: review[f"proposed_{field}"]
                for field in ("status", "season", "year")
                if f"proposed_{field}" in review
            }
            if not updates:
                raise InvalidReviewResolution
        elif resolution.action == "edit":
            if resolution.patch is None:
                raise InvalidReviewResolution
            updates = {
                field: _stored_value(value)
                for field, value in resolution.patch.updates().items()
            }
        else:
            updates = {}
        changes = {
            field: {"before": application[field], "after": value}
            for field, value in updates.items()
            if application[field] != value
        }
        resolved = self._linked_review(
            review,
            owner_id,
            application_id,
            resolution.action,
            EventType.UPDATED if changes else EventType.ATTACHED,
            changes,
            occurred_at,
        )
        if changes:
            updated = {
                **application,
                **updates,
                "updated_at": occurred_at.isoformat(),
                "version": expected_version + 1,
                "field_updated_at": {
                    **field_times_for(application),
                    **{field: occurred_at.isoformat() for field in changes},
                },
                "field_corrections": {
                    **application.get("field_corrections", {}),
                    **{field: occurred_at.isoformat() for field in changes},
                },
            }
            application_operation = {"Put": {
                "TableName": self._applications_table,
                "Item": self._encode(updated),
                "ConditionExpression": (
                    "attribute_exists(application_id) AND "
                    "attribute_not_exists(deleted_at) AND version = :version"
                ),
                "ExpressionAttributeValues": {
                    ":version": self._serializer.serialize(expected_version)
                },
            }}
        else:
            application_operation = {"ConditionCheck": {
                "TableName": self._applications_table,
                "Key": self._encode({"owner_id": owner_id, "application_id": application_id}),
                "ConditionExpression": (
                    "attribute_exists(application_id) AND "
                    "attribute_not_exists(deleted_at) AND version = :version"
                ),
                "ExpressionAttributeValues": {
                    ":version": self._serializer.serialize(expected_version)
                },
            }}
        self._transact(owner_id, message_id, resolved, [application_operation])
        return ReviewResolutionResult(
            message_id=message_id,
            action=resolution.action,
            application_id=application_id,
        )

    def _transact(
        self,
        owner_id: str,
        message_id: str,
        review: dict[str, Any],
        operations: list[dict[str, Any]],
    ) -> None:
        try:
            self._client.transact_write_items(TransactItems=[
                *operations,
                {"Put": {
                    "TableName": self._events_table,
                    "Item": self._encode(review),
                    "ConditionExpression": (
                        "attribute_exists(event_id) AND "
                        "attribute_not_exists(resolution) AND outcome = :review"
                    ),
                    "ExpressionAttributeValues": {
                        ":review": self._serializer.serialize("review_required")
                    },
                }},
            ])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            latest_review = self._get_review(owner_id, message_id)
            if latest_review is None:
                raise ReviewNotFound from error
            if "resolution" in latest_review:
                raise ReviewAlreadyResolved from error
            raise ReviewVersionConflict from error

    def _get_review(self, owner_id: str, message_id: str) -> dict[str, Any] | None:
        response = self._client.get_item(
            TableName=self._events_table,
            Key=self._encode({"owner_id": owner_id, "event_id": f"gmail#{message_id}"}),
            ConsistentRead=True,
        )
        return self._decode(response["Item"]) if "Item" in response else None

    def _get_application(self, owner_id: str, application_id: str) -> dict[str, Any] | None:
        response = self._client.get_item(
            TableName=self._applications_table,
            Key=self._encode({"owner_id": owner_id, "application_id": application_id}),
            ConsistentRead=True,
        )
        return self._decode(response["Item"]) if "Item" in response else None

    @staticmethod
    def _linked_review(
        review: dict[str, Any],
        owner_id: str,
        application_id: str,
        action: str,
        event_type: EventType,
        changes: dict[str, Any],
        occurred_at: datetime,
    ) -> dict[str, Any]:
        return {
            **review,
            "application_id": application_id,
            "owner_application_id": f"{owner_id}#{application_id}",
            "occurred_at_event_id": f"{occurred_at.isoformat()}#{review['event_id']}",
            "event_type": event_type.value,
            "origin": "user",
            "occurred_at": occurred_at.isoformat(),
            "changes": changes,
            "resolution": action,
            "resolved_at": occurred_at.isoformat(),
        }

    @staticmethod
    def _as_review(item: dict[str, Any]) -> ReviewItem:
        thread_id = item.get("thread_id")
        return ReviewItem(
            message_id=item["source_message_id"],
            reason=item["reason"],
            evidence=item.get("evidence", ""),
            received_at=item.get("received_at"),
            thread_id=thread_id,
            source_url=(
                f"https://mail.google.com/mail/u/0/#all/{quote(thread_id, safe='')}"
                if thread_id else None
            ),
            application_id=item.get("application_id"),
            proposed_status=item.get("proposed_status"),
            proposed_season=item.get("proposed_season"),
            proposed_year=item.get("proposed_year"),
        )

    def _encode(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _decode(self, item: dict[str, Any]) -> dict[str, Any]:
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}


def _encode_cursor(event_id: str) -> str:
    return urlsafe_b64encode(json.dumps({"event_id": event_id}).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> str:
    try:
        data = b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        value = json.loads(data)
    except (Base64Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InvalidReviewCursor from error
    if (
        not isinstance(value, dict)
        or set(value) != {"event_id"}
        or not isinstance(value["event_id"], str)
        or not value["event_id"].startswith("gmail#")
    ):
        raise InvalidReviewCursor
    return value["event_id"]
