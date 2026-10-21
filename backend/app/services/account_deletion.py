import time
from datetime import UTC, datetime
from enum import StrEnum

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.domain.account import AccountState, DeletionPhase
from app.observability import category_of, get_logger, log_event
from app.services.google_oauth import revoke_refresh_token

logger = get_logger(__name__)

BATCH_LIMIT = 25
MAX_PASSES = 3
MAX_RETRIES = 6
BACKOFF_SECONDS = 0.05
_ORDER = list(DeletionPhase)


class DeletionResult(StrEnum):
    DONE = "done"
    CONTINUE = "continue"


class StaleLease(Exception):
    """The lease or phase changed under this run; another holder owns the work."""


class PhaseFailure(Exception):
    """A phase failed. Carries only the phase and exception class name."""

    def __init__(self, phase: str, category: str) -> None:
        super().__init__(f"{phase}:{category}")
        self.phase = phase
        self.category = category


class _OutOfTime(Exception):
    pass


class AccountDeleter:
    def __init__(
        self, *, client, accounts, cipher, cognito, user_pool_id: str,
        users_table: str, applications_table: str, events_table: str,
        sync_runs_table: str, oauth_states_table: str,
        revoke=revoke_refresh_token, page_size: int = 100,
        sleep=time.sleep, monotonic=time.monotonic,
    ) -> None:
        self._client = client
        self._accounts = accounts
        self._cipher = cipher
        self._cognito = cognito
        self._pool = user_pool_id
        self._users = users_table
        self._partitions = (
            (applications_table, "application_id"),
            (events_table, "event_id"),
            (sync_runs_table, "run_id"),
        )
        self._oauth_states = oauth_states_table
        self._revoke = revoke
        self._page_size = page_size
        self._sleep = sleep
        self._monotonic = monotonic
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()

    def run(self, owner_id: str, lease_id: str, deadline: float) -> DeletionResult:
        phase = None
        try:
            while True:
                status = self._accounts.get_status(owner_id)
                if status.state == AccountState.DELETED:
                    return DeletionResult.DONE
                if status.state != AccountState.DELETING or status.phase is None:
                    raise StaleLease
                phase = status.phase
                try:
                    finished = self._run_phase(owner_id, phase, lease_id, deadline)
                except _OutOfTime:
                    return DeletionResult.CONTINUE
                if not finished:
                    return DeletionResult.CONTINUE
                index = _ORDER.index(DeletionPhase(phase))
                if index + 1 < len(_ORDER):
                    if not self._accounts.advance_phase(
                        owner_id, lease_id, phase, _ORDER[index + 1].value
                    ):
                        raise StaleLease
                elif not self._accounts.finalize(owner_id, lease_id, self._now()):
                    raise StaleLease
        except StaleLease:
            raise
        except Exception as error:  # noqa: BLE001 - reduced to phase and class name
            raise PhaseFailure(phase or "unknown", category_of(error)) from None

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _run_phase(self, owner_id, phase, lease_id, deadline) -> bool:
        if phase == DeletionPhase.REVOKE_GMAIL:
            self._revoke_gmail(owner_id)
        elif phase == DeletionPhase.DELETE_PARTITIONS:
            return self._delete_partitions(owner_id, deadline)
        elif phase == DeletionPhase.WIPE_PROFILE:
            self._wipe_profile(owner_id, lease_id)
        elif phase == DeletionPhase.DELETE_LOGIN:
            self._delete_login(owner_id)
        return True

    # -- phases -----------------------------------------------------------------

    def _revoke_gmail(self, owner_id: str) -> None:
        item = self._get_user(owner_id)
        encrypted = item.get("encrypted_token")
        if not encrypted:
            return
        try:
            token = self._cipher.decrypt_refresh_token(owner_id, encrypted)
            self._revoke(token)
        except Exception as error:  # noqa: BLE001 - revoke is best effort
            log_event(
                logger, "warning", "deletion_revoke_failed",
                phase="revoke_gmail", category=category_of(error),
            )

    def _delete_partitions(self, owner_id: str, deadline: float) -> bool:
        for _ in range(MAX_PASSES):
            for table, sort_key in self._partitions:
                self._sweep_partition(owner_id, table, sort_key, deadline)
            self._sweep_oauth_states(owner_id, deadline)
            if self._partitions_empty(owner_id):
                return True
        return False

    def _sweep_partition(self, owner_id, table, sort_key, deadline) -> None:
        request = {
            "TableName": table,
            "KeyConditionExpression": "owner_id = :o",
            "ExpressionAttributeValues": {":o": {"S": owner_id}},
            "ProjectionExpression": f"owner_id, {sort_key}",
            "ConsistentRead": True,
            "Limit": self._page_size,
        }
        while True:
            page = self._client.query(**request)
            self._delete_keys(table, page.get("Items", []), deadline)
            if "LastEvaluatedKey" not in page:
                return
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def _sweep_oauth_states(self, owner_id, deadline) -> None:
        request = {
            "TableName": self._oauth_states,
            "FilterExpression": "owner_id = :o",
            "ExpressionAttributeValues": {":o": {"S": owner_id}},
            "ProjectionExpression": "state_hash",
            "Limit": self._page_size,
        }
        while True:
            page = self._client.scan(**request)
            self._delete_keys(self._oauth_states, page.get("Items", []), deadline)
            if "LastEvaluatedKey" not in page:
                return
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def _delete_keys(self, table, keys, deadline) -> None:
        for start in range(0, len(keys), BATCH_LIMIT):
            pending = {table: [
                {"DeleteRequest": {"Key": key}}
                for key in keys[start:start + BATCH_LIMIT]
            ]}
            for attempt in range(MAX_RETRIES + 1):
                response = self._client.batch_write_item(RequestItems=pending)
                pending = response.get("UnprocessedItems") or {}
                if not pending:
                    break
                if attempt == MAX_RETRIES:
                    raise RuntimeError("unprocessed items remain")
                self._sleep(BACKOFF_SECONDS * 2**attempt)
            # Always finish a batch before yielding so every run makes progress.
            if self._monotonic() >= deadline:
                raise _OutOfTime

    def _partitions_empty(self, owner_id: str) -> bool:
        for table, _ in self._partitions:
            page = self._client.query(
                TableName=table, KeyConditionExpression="owner_id = :o",
                ExpressionAttributeValues={":o": {"S": owner_id}},
                Limit=1, ConsistentRead=True,
            )
            if page.get("Items"):
                return False
        request = {
            "TableName": self._oauth_states,
            "FilterExpression": "owner_id = :o",
            "ExpressionAttributeValues": {":o": {"S": owner_id}},
            "ProjectionExpression": "state_hash",
            "Limit": self._page_size,
        }
        while True:
            page = self._client.scan(**request)
            if page.get("Items"):
                return False
            if "LastEvaluatedKey" not in page:
                return True
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def _wipe_profile(self, owner_id: str, lease_id: str) -> None:
        item = self._get_user(owner_id)
        keep = {
            "owner_id": owner_id,
            "deletion_state": AccountState.DELETING.value,
            "deletion_phase": DeletionPhase.WIPE_PROFILE.value,
            "deletion_lease_id": lease_id,
        }
        for field in (
            "deletion_requested_at", "deletion_lease_expires_at", "cognito_username",
        ):
            if item.get(field) is not None:
                keep[field] = item[field]
        try:
            self._client.put_item(
                TableName=self._users,
                Item={k: self._serializer.serialize(v) for k, v in keep.items()},
                ConditionExpression=(
                    "deletion_state = :deleting AND deletion_lease_id = :lease "
                    "AND deletion_phase = :phase"
                ),
                ExpressionAttributeValues={
                    ":deleting": {"S": AccountState.DELETING.value},
                    ":lease": {"S": lease_id},
                    ":phase": {"S": DeletionPhase.WIPE_PROFILE.value},
                },
            )
        except ClientError as error:
            if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise StaleLease from None
            raise

    def _delete_login(self, owner_id: str) -> None:
        username = self._get_user(owner_id).get("cognito_username")
        if not username:
            return
        try:
            self._cognito.admin_delete_user(UserPoolId=self._pool, Username=username)
        except ClientError as error:
            if error.response["Error"]["Code"] != "UserNotFoundException":
                raise

    def _get_user(self, owner_id: str) -> dict:
        response = self._client.get_item(
            TableName=self._users, Key={"owner_id": {"S": owner_id}}, ConsistentRead=True,
        )
        return {
            key: self._deserializer.deserialize(value)
            for key, value in response.get("Item", {}).items()
        }
