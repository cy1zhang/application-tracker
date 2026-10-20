from dataclasses import dataclass
from datetime import datetime, timedelta

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.sync import SyncRun


class SyncUnavailable(Exception):
    pass


class StaleSyncRun(Exception):
    pass


@dataclass(frozen=True)
class SyncLease:
    run: SyncRun
    page_token: str | None


class DynamoSyncRepository:
    def __init__(self, *, client, users_table: str, sync_runs_table: str) -> None:
        self._client = client
        self._users_table = users_table
        self._sync_runs_table = sync_runs_table
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def start(self, owner_id: str, run_id: str, now: datetime) -> SyncRun:
        user = self._get_user(owner_id)
        if not user or not user.get("email") or not user.get("encrypted_token"):
            raise SyncUnavailable("Gmail is not connected")
        active_id = user.get("active_run_id")
        if active_id:
            active = self._get_run(owner_id, active_id)
            if active is not None:
                return active
            raise SyncUnavailable("Active sync run is unavailable")

        mode = "incremental" if user.get("history_id") else "import"
        anchor = datetime.fromisoformat(user["connected_at"]) if user.get("connected_at") else now
        run = SyncRun(
            owner_id=owner_id,
            run_id=run_id,
            generation=int(user["generation"]),
            mode=mode,
            status="pending",
            started_at=now,
            import_after=anchor - timedelta(days=90) if mode == "import" else None,
        )
        item = {
            "owner_id": owner_id,
            "run_id": run_id,
            "generation": run.generation,
            "mode": run.mode,
            "status": run.status,
            "started_at": now.isoformat(),
            "processed_count": 0,
        }
        if run.import_after is not None:
            item["import_after"] = run.import_after.isoformat()
        try:
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._users_table,
                            "Key": self._encode({"owner_id": owner_id}),
                            "UpdateExpression": "SET active_run_id = :run, latest_run_id = :run",
                            "ConditionExpression": (
                                "generation = :generation AND attribute_exists(email) "
                                "AND attribute_exists(encrypted_token) "
                                "AND attribute_not_exists(active_run_id)"
                            ),
                            "ExpressionAttributeValues": {
                                ":run": self._serializer.serialize(run_id),
                                ":generation": self._serializer.serialize(run.generation),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._sync_runs_table,
                            "Item": self._encode(item),
                            "ConditionExpression": (
                                "attribute_not_exists(owner_id) AND attribute_not_exists(run_id)"
                            ),
                        }
                    },
                ]
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            active_id = (self._get_user(owner_id) or {}).get("active_run_id")
            if active_id:
                active = self._get_run(owner_id, active_id)
                if active is not None:
                    return active
            raise SyncUnavailable("Gmail connection changed") from None
        return run

    def get_latest(self, owner_id: str) -> SyncRun | None:
        latest_id = (self._get_user(owner_id) or {}).get("latest_run_id")
        return self._get_run(owner_id, latest_id) if latest_id else None

    def claim(
        self, owner_id: str, run_id: str, generation: int,
        lease_id: str, now: datetime,
    ) -> SyncLease | None:
        try:
            self._client.transact_write_items(TransactItems=[
                {
                    "Update": {
                        "TableName": self._sync_runs_table,
                        "Key": self._encode({"owner_id": owner_id, "run_id": run_id}),
                        "UpdateExpression": (
                            "SET #state = :running, lease_id = :lease, "
                            "lease_expires_at = :expiry"
                        ),
                        "ConditionExpression": (
                            "#state = :pending OR "
                            "(#state = :running AND lease_expires_at < :now)"
                        ),
                        "ExpressionAttributeNames": {"#state": "status"},
                        "ExpressionAttributeValues": {
                            ":running": self._serializer.serialize("running"),
                            ":pending": self._serializer.serialize("pending"),
                            ":lease": self._serializer.serialize(lease_id),
                            ":expiry": self._serializer.serialize(
                                (now + timedelta(minutes=2)).isoformat()
                            ),
                            ":now": self._serializer.serialize(now.isoformat()),
                        },
                    }
                },
                self._run_guard(owner_id, run_id, generation),
            ])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            if not self._valid_run(owner_id, run_id, generation):
                raise StaleSyncRun from None
            return None
        item = self._get_run_item(owner_id, run_id)
        return SyncLease(run=self._as_run(item), page_token=item.get("page_token"))

    def checkpoint(
        self, owner_id: str, run_id: str, generation: int, lease_id: str,
        next_page_token: str, processed_count: int, now: datetime,
    ) -> SyncRun:
        try:
            self._client.transact_write_items(TransactItems=[
                {
                    "Update": {
                        "TableName": self._sync_runs_table,
                        "Key": self._encode({"owner_id": owner_id, "run_id": run_id}),
                        "UpdateExpression": (
                            "SET #state = :pending, page_token = :cursor, "
                            "processed_count = processed_count + :count "
                            "REMOVE lease_id, lease_expires_at"
                        ),
                        "ConditionExpression": "#state = :running AND lease_id = :lease",
                        "ExpressionAttributeNames": {"#state": "status"},
                        "ExpressionAttributeValues": {
                            ":pending": self._serializer.serialize("pending"),
                            ":running": self._serializer.serialize("running"),
                            ":cursor": self._serializer.serialize(next_page_token),
                            ":count": self._serializer.serialize(processed_count),
                            ":lease": self._serializer.serialize(lease_id),
                        },
                    }
                },
                self._run_guard(owner_id, run_id, generation),
            ])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            raise StaleSyncRun from None
        return self._get_run(owner_id, run_id)

    def complete(
        self, owner_id: str, run_id: str, generation: int, lease_id: str,
        processed_count: int, now: datetime,
    ) -> SyncRun:
        try:
            self._client.transact_write_items(TransactItems=[
                {
                    "Update": {
                        "TableName": self._sync_runs_table,
                        "Key": self._encode({"owner_id": owner_id, "run_id": run_id}),
                        "UpdateExpression": (
                            "SET #state = :completed, completed_at = :now, "
                            "processed_count = processed_count + :count "
                            "REMOVE lease_id, lease_expires_at, page_token"
                        ),
                        "ConditionExpression": "#state = :running AND lease_id = :lease",
                        "ExpressionAttributeNames": {"#state": "status"},
                        "ExpressionAttributeValues": {
                            ":completed": self._serializer.serialize("completed"),
                            ":running": self._serializer.serialize("running"),
                            ":now": self._serializer.serialize(now.isoformat()),
                            ":count": self._serializer.serialize(processed_count),
                            ":lease": self._serializer.serialize(lease_id),
                        },
                    }
                },
                {
                    "Update": {
                        "TableName": self._users_table,
                        "Key": self._encode({"owner_id": owner_id}),
                        "UpdateExpression": (
                            "SET last_success_at = :now REMOVE active_run_id"
                        ),
                        "ConditionExpression": (
                            "generation = :generation AND active_run_id = :run "
                            "AND attribute_exists(encrypted_token)"
                        ),
                        "ExpressionAttributeValues": {
                            ":now": self._serializer.serialize(now.isoformat()),
                            ":generation": self._serializer.serialize(generation),
                            ":run": self._serializer.serialize(run_id),
                        },
                    }
                },
            ])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            raise StaleSyncRun from None
        return self._get_run(owner_id, run_id)

    def _run_guard(self, owner_id: str, run_id: str, generation: int) -> dict:
        return {
            "ConditionCheck": {
                "TableName": self._users_table,
                "Key": self._encode({"owner_id": owner_id}),
                "ConditionExpression": (
                    "generation = :generation AND active_run_id = :run "
                    "AND attribute_exists(encrypted_token)"
                ),
                "ExpressionAttributeValues": {
                    ":generation": self._serializer.serialize(generation),
                    ":run": self._serializer.serialize(run_id),
                },
            }
        }

    def _valid_run(self, owner_id: str, run_id: str, generation: int) -> bool:
        user = self._get_user(owner_id) or {}
        return (
            user.get("generation") == generation
            and user.get("active_run_id") == run_id
            and "encrypted_token" in user
        )

    def _get_user(self, owner_id: str) -> dict | None:
        response = self._client.get_item(
            TableName=self._users_table,
            Key=self._encode({"owner_id": owner_id}),
            ConsistentRead=True,
        )
        return self._decode(response["Item"]) if "Item" in response else None

    def _get_run(self, owner_id: str, run_id: str) -> SyncRun | None:
        item = self._get_run_item(owner_id, run_id)
        return self._as_run(item) if item else None

    def _get_run_item(self, owner_id: str, run_id: str) -> dict | None:
        response = self._client.get_item(
            TableName=self._sync_runs_table,
            Key=self._encode({"owner_id": owner_id, "run_id": run_id}),
            ConsistentRead=True,
        )
        if "Item" not in response:
            return None
        return self._decode(response["Item"])

    @staticmethod
    def _as_run(item: dict) -> SyncRun:
        return SyncRun(
            owner_id=item["owner_id"],
            run_id=item["run_id"],
            generation=int(item["generation"]),
            mode=item["mode"],
            status=item["status"],
            started_at=datetime.fromisoformat(item["started_at"]),
            import_after=(
                datetime.fromisoformat(item["import_after"])
                if "import_after" in item else None
            ),
            processed_count=int(item.get("processed_count", 0)),
        )

    def _encode(self, item: dict) -> dict:
        return {key: self._serializer.serialize(value) for key, value in item.items()}

    def _decode(self, item: dict) -> dict:
        return {key: self._deserializer.deserialize(value) for key, value in item.items()}
