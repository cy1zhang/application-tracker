from datetime import UTC, datetime

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.gmail import EncryptedCredential
from app.repositories.gmail import GmailConnection, OAuthState, StaleGeneration


class DynamoGmailRepository:
    def __init__(self, *, client, users_table: str, oauth_states_table: str) -> None:
        self._client = client
        self._users_table = users_table
        self._oauth_states_table = oauth_states_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def create_state(
        self,
        state_hash: str,
        owner_id: str,
        expected_email: str,
        expected_generation: int,
        expires_at: datetime,
    ) -> None:
        self._client.put_item(
            TableName=self._oauth_states_table,
            Item=self._encode(
                {
                    "state_hash": state_hash,
                    "owner_id": owner_id,
                    "expected_email": expected_email,
                    "expected_generation": expected_generation,
                    "expires_at": int(expires_at.timestamp()),
                }
            ),
            ConditionExpression="attribute_not_exists(state_hash)",
        )

    def consume_state(self, state_hash: str, now: datetime) -> OAuthState | None:
        try:
            response = self._client.delete_item(
                TableName=self._oauth_states_table,
                Key=self._encode({"state_hash": state_hash}),
                ConditionExpression="attribute_exists(state_hash)",
                ReturnValues="ALL_OLD",
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        item = self._decode(response["Attributes"])
        expires_at = datetime.fromtimestamp(int(item["expires_at"]), UTC)
        if expires_at <= now:
            return None
        return OAuthState(
            owner_id=item["owner_id"],
            expected_email=item["expected_email"],
            expected_generation=int(item["expected_generation"]),
            expires_at=expires_at,
        )

    def get_connection(self, owner_id: str) -> GmailConnection:
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        if "Item" not in response:
            return GmailConnection(generation=0, email=None, encrypted_token=None)
        item = self._decode(response["Item"])
        return GmailConnection(
            generation=int(item.get("generation", 0)),
            email=item.get("email"),
            encrypted_token=item.get("encrypted_token"),
            reconnect_required=bool(item.get("reconnect_required", False)),
        )

    def save_connection(
        self,
        owner_id: str,
        expected_generation: int,
        email: str,
        encrypted_token: EncryptedCredential,
        connected_at: datetime,
    ) -> None:
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression=(
                    "SET generation = :new, email = :email, encrypted_token = :token, "
                    "connected_at = :connected_at "
                    "REMOVE history_id, last_success_at, active_run_id, "
                    "latest_run_id, reconnect_required"
                ),
                ConditionExpression=(
                    "(attribute_not_exists(generation) OR generation = :expected) "
                    "AND attribute_not_exists(deletion_state)"
                ),
                ExpressionAttributeValues={
                    ":expected": self._serializer.serialize(expected_generation),
                    ":new": self._serializer.serialize(expected_generation + 1),
                    ":email": self._serializer.serialize(email),
                    ":token": self._serializer.serialize(encrypted_token),
                    ":connected_at": self._serializer.serialize(connected_at.isoformat()),
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise StaleGeneration from None
            raise

    def disconnect(self, owner_id: str) -> None:
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression=(
                    "SET generation = if_not_exists(generation, :zero) + :one "
                    "REMOVE email, encrypted_token, connected_at, history_id, "
                    "last_success_at, active_run_id, latest_run_id, reconnect_required"
                ),
                ConditionExpression="attribute_not_exists(deletion_state)",
                ExpressionAttributeValues={
                    ":zero": self._serializer.serialize(0),
                    ":one": self._serializer.serialize(1),
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise StaleGeneration from None
            raise

    def get_active(
        self, owner_id: str, expected_generation: int
    ) -> EncryptedCredential | None:
        connection = self.get_connection(owner_id)
        if connection.generation != expected_generation or connection.email is None:
            return None
        return connection.encrypted_token

    def _encode(self, item: dict) -> dict:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _decode(self, item: dict) -> dict:
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}
