import re
from collections.abc import Mapping
from datetime import UTC, datetime

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.ai import (
    AI_DAILY_LIMIT,
    AiProvider,
    AiRuntime,
    AiSettingsView,
    StoredAiSettings,
)
from app.domain.gmail import EncryptedCredential
from app.repositories.dynamodb_sync import StaleSyncRun

DAILY_LIMIT = AI_DAILY_LIMIT


class AiKeyRequired(Exception):
    pass


class AiSettingsChanged(Exception):
    pass


class AiKeyInvalid(Exception):
    pass


class AiBudgetExhausted(Exception):
    pass


_GUARD = (
    "generation = :generation AND active_run_id = :run "
    "AND attribute_exists(encrypted_token)"
)
_AI_READY = (
    "ai_enabled = :true AND ai_key_status = :ok "
    "AND ai_settings_version = :version"
)


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

    def reserve_request(
        self,
        owner_id: str,
        *,
        generation: int,
        run_id: str,
        settings_version: int,
        now: datetime,
    ) -> None:
        today = now.astimezone(UTC).date().isoformat()
        values = self._encode({
            ":generation": generation,
            ":run": run_id,
            ":true": True,
            ":ok": "ok",
            ":version": settings_version,
            ":today": today,
            ":limit": DAILY_LIMIT,
            ":one": 1,
        })
        same_day = {
            "UpdateExpression": "ADD ai_requests_today :one",
            "ConditionExpression": (
                f"{_GUARD} AND {_AI_READY} "
                "AND ai_usage_day = :today AND ai_requests_today < :limit"
            ),
        }
        new_day = {
            "UpdateExpression": "SET ai_usage_day = :today, ai_requests_today = :one",
            "ConditionExpression": (
                f"{_GUARD} AND {_AI_READY} "
                "AND (attribute_not_exists(ai_usage_day) OR ai_usage_day <> :today)"
            ),
        }
        if self._try_update(owner_id, same_day, values):
            return
        if self._try_update(owner_id, new_day, values):
            return
        # Another worker may have started today between the two attempts.
        self._raise_for_failure(owner_id, generation, run_id, settings_version, today)
        if not self._try_update(owner_id, same_day, values):
            self._raise_for_failure(
                owner_id, generation, run_id, settings_version, today, final=True
            )

    def mark_key_invalid(self, owner_id: str, settings_version: int) -> None:
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression="SET ai_key_status = :invalid",
                ConditionExpression="ai_settings_version = :version",
                ExpressionAttributeValues=self._encode(
                    {":invalid": "invalid", ":version": settings_version}
                ),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise

    def load_runtime(
        self, owner_id: str, models: Mapping[AiProvider, str]
    ) -> AiRuntime | None:
        stored = self.get(owner_id)
        if stored is None or not stored.enabled or stored.key_status != "ok":
            return None
        model = (models.get(stored.provider) or "").strip()
        if not model:
            return None
        return AiRuntime(
            provider=stored.provider,
            model=model,
            settings_version=stored.version,
            encrypted_key=stored.encrypted_key,
        )

    def _try_update(self, owner_id: str, expressions: dict, values: dict) -> bool:
        used = set(re.findall(r":\w+", " ".join(expressions.values())))
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                ExpressionAttributeValues={k: v for k, v in values.items() if k in used},
                **expressions,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def _raise_for_failure(
        self, owner_id: str, generation: int, run_id: str, version: int,
        today: str, *, final: bool = False,
    ) -> None:
        """Raise exactly one typed error, in a fixed precedence order.

        Returns only when the item looks reservable (a lost first-of-day race).
        """
        item = self._get_item(owner_id) or {}
        if not (
            item.get("generation") == generation
            and item.get("active_run_id") == run_id
            and "encrypted_token" in item
        ):
            raise StaleSyncRun
        if (
            "ai_encrypted_key" not in item
            or not item.get("ai_enabled", False)
            or int(item.get("ai_settings_version", 0)) != version
        ):
            raise AiSettingsChanged
        if item.get("ai_key_status", "ok") != "ok":
            raise AiKeyInvalid
        if item.get("ai_usage_day") == today and int(
            item.get("ai_requests_today", 0)
        ) >= DAILY_LIMIT:
            raise AiBudgetExhausted
        if final:
            raise AiSettingsChanged

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
