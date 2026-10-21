import os
from datetime import UTC, datetime

import boto3

from app.observability import get_logger, log_event
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.services.sync_dispatch import dispatch_daily, recover_unfinished

logger = get_logger(__name__)


def handler(event, context):
    client = boto3.client("dynamodb")
    publisher = boto3.client("sqs")
    queue_url = os.environ["SYNC_QUEUE_URL"]
    now = datetime.now(UTC)
    if event.get("mode") == "daily":
        count = dispatch_daily(
            client,
            DynamoSyncRepository(
                client=client, users_table=os.environ["USERS_TABLE"],
                sync_runs_table=os.environ["SYNC_RUNS_TABLE"],
            ),
            publisher, os.environ["USERS_TABLE"], queue_url, now,
        )
    elif event.get("mode") == "recover":
        count = recover_unfinished(
            client, publisher, os.environ["USERS_TABLE"],
            os.environ["SYNC_RUNS_TABLE"], queue_url, now
        )
    else:
        raise ValueError("Unknown sync dispatch mode")
    log_event(logger, "info", "dispatch_summary", mode=event["mode"], count=count)
