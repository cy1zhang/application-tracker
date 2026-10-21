import json
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from uuid import uuid4

import boto3

from app.observability import category_of, get_logger, log_event
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.services.account_deletion import (
    AccountDeleter,
    DeletionResult,
    PhaseFailure,
    StaleLease,
)
from app.services.credential_crypto import CredentialCipher

logger = get_logger(__name__)

DEFAULT_BUDGET_SECONDS = 240
DEADLINE_BUFFER_SECONDS = 60


def _dependencies():
    client = boto3.client("dynamodb")
    users_table = os.environ["USERS_TABLE"]
    accounts = DynamoAccountRepository(client=client, users_table=users_table)
    return {
        "accounts": accounts,
        "deleter": AccountDeleter(
            client=client, accounts=accounts,
            cipher=CredentialCipher(boto3.client("kms"), os.environ["GMAIL_KMS_KEY_ARN"]),
            cognito=boto3.client("cognito-idp"),
            user_pool_id=os.environ["USER_POOL_ID"], users_table=users_table,
            applications_table=os.environ["APPLICATIONS_TABLE"],
            events_table=os.environ["EVENTS_TABLE"],
            sync_runs_table=os.environ["SYNC_RUNS_TABLE"],
            oauth_states_table=os.environ["OAUTH_STATES_TABLE"],
        ),
        "publisher": boto3.client("sqs"),
    }


def _owner_of(record) -> str | None:
    try:
        job = json.loads(record["body"])
        owner_id = job["owner_id"]
        if job["kind"] == "delete_account" and isinstance(owner_id, str) and owner_id:
            return owner_id
    except (ValueError, KeyError, TypeError):
        pass
    return None


def handler(event, context):
    dependencies = _dependencies()
    accounts = dependencies["accounts"]
    failures = []
    for record in event["Records"]:
        owner_id = _owner_of(record)
        if owner_id is None:
            continue  # malformed or foreign message: acknowledge
        lease_id = uuid4().hex
        started = time.monotonic()
        if accounts.claim_deletion(owner_id, lease_id, datetime.now(UTC)) is None:
            continue  # finished, not deleting, or another holder is live
        remaining = (
            context.get_remaining_time_in_millis() / 1000
            if context is not None else DEFAULT_BUDGET_SECONDS + DEADLINE_BUFFER_SECONDS
        )
        deadline = started + max(0.0, remaining - DEADLINE_BUFFER_SECONDS)
        phase = None
        try:
            result = dependencies["deleter"].run(owner_id, lease_id, deadline)
            if result == DeletionResult.CONTINUE:
                accounts.release_deletion(owner_id, lease_id)
                dependencies["publisher"].send_message(
                    QueueUrl=os.environ["DELETION_QUEUE_URL"],
                    MessageBody=json.dumps({"kind": "delete_account", "owner_id": owner_id}),
                )
                log_event(logger, "info", "deletion_continued")
            else:
                log_event(
                    logger, "info", "deletion_completed",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
        except StaleLease:
            continue
        except Exception as error:  # noqa: BLE001 - every failure must signal SQS to retry
            if isinstance(error, PhaseFailure):
                phase, category = error.phase, error.category
            else:
                category = category_of(error)
            log_event(
                logger, "warning", "deletion_job_failed", phase=phase, category=category,
            )
            with suppress(Exception):  # the lease simply expires on its own
                accounts.release_deletion(owner_id, lease_id)
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
