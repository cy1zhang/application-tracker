from datetime import datetime, timedelta

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.account import (
    LEASE_MINUTES,
    TOMBSTONE_TTL_HOURS,
    AccountState,
    AccountStatus,
    DeletionPhase,
)


class DynamoAccountRepository:
    def __init__(self, *, client, users_table: str) -> None:
        self._client = client
        self._users_table = users_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def begin_deletion(
        self, owner_id: str, cognito_username: str, now: datetime
    ) -> AccountStatus:
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression=(
                    "SET deletion_state = :deleting, deletion_phase = :phase, "
                    "deletion_requested_at = :now, cognito_username = :username, "
                    "generation = if_not_exists(generation, :zero) + :one "
                    "REMOVE active_run_id"
                ),
                ConditionExpression="attribute_not_exists(deletion_state)",
                ExpressionAttributeValues=self._encode({
                    ":deleting": AccountState.DELETING.value,
                    ":phase": DeletionPhase.REVOKE_GMAIL.value,
                    ":now": now.isoformat(),
                    ":username": cognito_username,
                    ":zero": 0,
                    ":one": 1,
                }),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self.get_status(owner_id)
        return AccountStatus(AccountState.DELETING, DeletionPhase.REVOKE_GMAIL.value)

    def get_status(self, owner_id: str) -> AccountStatus:
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        item = self._decode(response.get("Item", {}))
        return AccountStatus(
            AccountState(item.get("deletion_state", AccountState.ACTIVE.value)),
            item.get("deletion_phase"),
        )

    def claim_deletion(self, owner_id: str, lease_id: str, now: datetime) -> str | None:
        try:
            response = self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression=(
                    "SET deletion_lease_id = :lease, deletion_lease_expires_at = :expiry"
                ),
                ConditionExpression=(
                    "deletion_state = :deleting AND "
                    "(attribute_not_exists(deletion_lease_expires_at) "
                    "OR deletion_lease_expires_at < :now)"
                ),
                ExpressionAttributeValues=self._encode({
                    ":deleting": AccountState.DELETING.value,
                    ":lease": lease_id,
                    ":expiry": int((now + timedelta(minutes=LEASE_MINUTES)).timestamp()),
                    ":now": int(now.timestamp()),
                }),
                ReturnValues="ALL_NEW",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return self._decode(response["Attributes"]).get("deletion_phase")

    def release_deletion(self, owner_id: str, lease_id: str) -> None:
        """Expire our own lease so a continuation or retry can claim it at once."""
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression="SET deletion_lease_expires_at = :zero",
                ConditionExpression=(
                    "deletion_state = :deleting AND deletion_lease_id = :lease"
                ),
                ExpressionAttributeValues=self._encode({
                    ":deleting": AccountState.DELETING.value,
                    ":lease": lease_id,
                    ":zero": 0,
                }),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def advance_phase(
        self, owner_id: str, lease_id: str, from_phase: str, to_phase: str
    ) -> bool:
        DeletionPhase(from_phase)
        DeletionPhase(to_phase)
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression="SET deletion_phase = :to",
                ConditionExpression=(
                    "deletion_state = :deleting AND deletion_lease_id = :lease "
                    "AND deletion_phase = :from"
                ),
                ExpressionAttributeValues=self._encode({
                    ":deleting": AccountState.DELETING.value,
                    ":lease": lease_id,
                    ":from": from_phase,
                    ":to": to_phase,
                }),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def finalize(self, owner_id: str, lease_id: str, now: datetime) -> bool:
        try:
            self._client.put_item(
                TableName=self._users_table,
                Item=self._encode({
                    "owner_id": owner_id,
                    "deletion_state": AccountState.DELETED.value,
                    "deleted_at": now.isoformat(),
                    "expires_at": int(
                        (now + timedelta(hours=TOMBSTONE_TTL_HOURS)).timestamp()
                    ),
                }),
                ConditionExpression=(
                    "deletion_state = :deleting AND deletion_lease_id = :lease"
                ),
                ExpressionAttributeValues=self._encode({
                    ":deleting": AccountState.DELETING.value,
                    ":lease": lease_id,
                }),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def _encode(self, item: dict) -> dict:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _decode(self, item: dict) -> dict:
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}
