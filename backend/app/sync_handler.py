import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import boto3

from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository, StaleSyncRun
from app.services.credential_crypto import CredentialCipher
from app.services.gmail_fetch import GmailFetchClient
from app.services.google_oauth import GoogleOAuthClient, OAuthReconnectRequired
from app.services.sync_worker import process_sync_page


def _dependencies():
    client = boto3.client("dynamodb")
    return {
        "client": client,
        "syncs": DynamoSyncRepository(
            client=client, users_table=os.environ["USERS_TABLE"],
            sync_runs_table=os.environ["SYNC_RUNS_TABLE"],
        ),
        "connections": DynamoGmailRepository(
            client=client, users_table=os.environ["USERS_TABLE"],
            oauth_states_table=os.environ["OAUTH_STATES_TABLE"],
        ),
        "cipher": CredentialCipher(
            boto3.client("kms"), os.environ["GMAIL_KMS_KEY_ARN"]
        ),
        "oauth": GoogleOAuthClient(
            boto3.client("secretsmanager"), os.environ["GMAIL_OAUTH_SECRET_NAME"], ""
        ),
        "fetch": GmailFetchClient(),
        "applications": DynamoApplicationRepository(
            client=client, applications_table=os.environ["APPLICATIONS_TABLE"],
            events_table=os.environ["EVENTS_TABLE"],
        ),
        "publisher": boto3.client("sqs"),
    }


def handler(event, context):
    dependencies = _dependencies()
    failures = []
    for record in event["Records"]:
        lease_id = uuid4().hex
        now = datetime.now(UTC)
        owner_id = run_id = generation = None
        try:
            job = json.loads(record["body"])
            owner_id = job["owner_id"]
            run_id = job["run_id"]
            generation = int(job["generation"])
            emails = DynamoEmailRepository(
                client=dependencies["client"],
                applications_table=os.environ["APPLICATIONS_TABLE"],
                events_table=os.environ["EVENTS_TABLE"],
                users_table=os.environ["USERS_TABLE"],
                guard_generation=generation, guard_run_id=run_id,
            )
            process_sync_page(
                owner_id=owner_id, run_id=run_id, generation=generation,
                lease_id=lease_id, now=now, emails=emails,
                queue_url=os.environ["SYNC_QUEUE_URL"],
                **{key: value for key, value in dependencies.items() if key != "client"},
            )
        except StaleSyncRun:
            # A disconnect, completed run, or superseded lease made this delivery obsolete.
            continue
        except OAuthReconnectRequired:
            try:
                dependencies["syncs"].fail(
                    owner_id, run_id, generation, lease_id, "reconnect_required", now
                )
            except StaleSyncRun:
                pass
        except Exception:  # noqa: BLE001 - every worker failure must signal SQS to retry
            if (
                int(record.get("attributes", {}).get("ApproximateReceiveCount", "1")) >= 5
                and owner_id is not None and run_id is not None and generation is not None
            ):
                try:
                    dependencies["syncs"].fail(
                        owner_id, run_id, generation, lease_id, "partial", now
                    )
                except StaleSyncRun:
                    pass
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
