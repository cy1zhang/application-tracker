from datetime import datetime

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.ai import (
    AI_DAILY_LIMIT,
    AiProvider,
    AiSettingsView,
    StoredAiSettings,
)
from app.domain.gmail import EncryptedCredential


class AiKeyRequired(Exception):
    pass


class DynamoAiSettingsRepository:
    def __init__(self, *, client, users_table: str) -> None:
        self._client = client
        self._users_table = users_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def get(self, owner_id: str) -> StoredAiSettings | None:
        item = self._get_item(owner_id)
        if not item or "ai_encrypted_key" not in item:
            return None
        return StoredAiSettings(
            provider=AiProvider(item["ai_provider"]),
            encrypted_key=item["ai_encrypted_key"],
            key_hint=item["ai_key_hint"],
            enabled=bool(item.get("ai_enabled", False)),
            key_status=item.get("ai_key_status", "ok"),
            version=int(item.get("ai_settings_version", 0)),
            consent_at=(
                datetime.fromisoformat(item["ai_consent_at"])
                if item.get("ai_consent_at") else None
            ),
        )

    def save(
        self,
        owner_id: str,
        provider: AiProvider,
        encrypted_key: EncryptedCredential | None,
        hint: str | None,
        enabled: bool,
        now: datetime,
    ) -> None:
        values = {
            ":provider": provider.value,
            ":enabled": enabled,
            ":one": 1,
        }
        if encrypted_key is not None:
            update = (
                "SET ai_provider = :provider, ai_encrypted_key = :key, "
                "ai_key_hint = :hint, ai_enabled = :enabled, ai_key_status = :ok, "
                "ai_consent_at = :consent_at ADD ai_settings_version :one"
            )
            condition = None
            values |= {
                ":key": encrypted_key,
                ":hint": hint,
                ":ok": "ok",
                ":consent_at": now.isoformat(),
            }
        else:
            update = "SET ai_enabled = :enabled ADD ai_settings_version :one"
            condition = "ai_provider = :provider AND attribute_exists(ai_encrypted_key)"
        request = {
            "TableName": self._users_table,
            "Key": self._encode({"owner_id": owner_id}),
            "UpdateExpression": update,
            "ExpressionAttributeValues": self._encode(values),
        }
        if condition:
            request["ConditionExpression"] = condition
        try:
            self._client.update_item(**request)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise AiKeyRequired from None
            raise

    def delete(self, owner_id: str) -> None:
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression=(
                    "REMOVE ai_provider, ai_encrypted_key, ai_key_hint, ai_enabled, "
                    "ai_key_status, ai_consent_at ADD ai_settings_version :one"
                ),
                ConditionExpression="attribute_exists(ai_settings_version)",
                ExpressionAttributeValues={":one": self._serializer.serialize(1)},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def public_view(self, owner_id: str, today: str) -> AiSettingsView:
        item = self._get_item(owner_id) or {}
        configured = "ai_encrypted_key" in item
        same_day = item.get("ai_usage_day") == today
        return AiSettingsView(
            provider=AiProvider(item["ai_provider"]) if configured else None,
            key_hint=item.get("ai_key_hint") if configured else None,
            enabled=bool(item.get("ai_enabled", False)) if configured else False,
            key_status=item.get("ai_key_status", "ok") if configured else None,
            requests_today=int(item.get("ai_requests_today", 0)) if same_day else 0,
            daily_limit=AI_DAILY_LIMIT,
            usage_day=today,
        )

    def _get_item(self, owner_id: str) -> dict | None:
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        if "Item" not in response:
            return None
        return {k: self._deserializer.deserialize(v) for k, v in response["Item"].items()}

    def _encode(self, item: dict) -> dict:
        return {key: self._serializer.serialize(value) for key, value in item.items()}
