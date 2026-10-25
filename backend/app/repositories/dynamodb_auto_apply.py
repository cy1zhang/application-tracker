from typing import Literal, get_args

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.account import AccountDeleting

AutoApplyMode = Literal["off", "plan", "on"]


class DynamoAutoApplySettings:
    def __init__(self, *, client, users_table: str) -> None:
        self._client = client
        self._users_table = users_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def get_mode(self, owner_id: str) -> AutoApplyMode:
        item = self._get_item(owner_id)
        mode = item.get("auto_apply_mode")
        return mode if mode in get_args(AutoApplyMode) else "off"

    def set_mode(self, owner_id: str, mode: AutoApplyMode) -> None:
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression="SET auto_apply_mode = :mode",
                ConditionExpression="attribute_not_exists(deletion_state)",
                ExpressionAttributeValues=self._encode({":mode": mode}),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise AccountDeleting from None
            raise

    def set_mode_if(
        self, owner_id: str, mode: AutoApplyMode, *, expected: AutoApplyMode
    ) -> bool:
        """Set the mode only while it is still `expected`; False when it moved."""
        try:
            self._client.update_item(
                TableName=self._users_table,
                Key=self._encode({"owner_id": owner_id}),
                UpdateExpression="SET auto_apply_mode = :mode",
                ConditionExpression=(
                    "auto_apply_mode = :expected "
                    "AND attribute_not_exists(deletion_state)"
                ),
                ExpressionAttributeValues=self._encode(
                    {":mode": mode, ":expected": expected}
                ),
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def _get_item(self, owner_id: str) -> dict:
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        return {
            k: self._deserializer.deserialize(v)
            for k, v in response.get("Item", {}).items()
        }

    def _encode(self, item: dict) -> dict:
        return {key: self._serializer.serialize(value) for key, value in item.items()}
