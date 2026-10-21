import json
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import boto3

from app.domain.ai import AiProvider
from app.observability import category_of, get_logger, log_event
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository, StaleSyncRun
from app.services.ai_escalation import AiEscalator
from app.services.ai_providers import create_provider
from app.services.credential_crypto import CredentialCipher
from app.services.gmail_fetch import GmailFetchClient
from app.services.google_oauth import GoogleOAuthClient, OAuthReconnectRequired
from app.services.sync_worker import process_sync_page

logger = get_logger(__name__)


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
        "ai_settings": DynamoAiSettingsRepository(
            client=client, users_table=os.environ["USERS_TABLE"]
        ),
    }


def _escalator_factory(dependencies, owner_id, generation, run_id):
    settings = dependencies.get("ai_settings")
    if settings is None:
        return None
    models = {
        AiProvider.OPENAI: os.environ.get("AI_MODEL_OPENAI", ""),
        AiProvider.ANTHROPIC: os.environ.get("AI_MODEL_ANTHROPIC", ""),
        AiProvider.GEMINI: os.environ.get("AI_MODEL_GEMINI", ""),
    }
    return lambda: AiEscalator(
        owner_id=owner_id, generation=generation, run_id=run_id,
        settings=settings, cipher=dependencies["cipher"],
        provider_factory=create_provider, models=models,
        clock=lambda: datetime.now(UTC),
    )


def handler(event, context):
    dependencies = _dependencies()
    failures = []
    for record in event["Records"]:
        lease_id = uuid4().hex
        now = datetime.now(UTC)
        owner_id = run_id = generation = None
        started = time.monotonic()
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
            run = process_sync_page(
                owner_id=owner_id, run_id=run_id, generation=generation,
                lease_id=lease_id, now=now, emails=emails,
                queue_url=os.environ["SYNC_QUEUE_URL"],
                escalator_factory=_escalator_factory(
                    dependencies, owner_id, generation, run_id
                ),
                **{
                    key: value for key, value in dependencies.items()
                    if key not in {"client", "ai_settings"}
                },
            )
            if run is not None:
                log_event(
                    logger, "info", "sync_page_completed", run_id=run_id,
                    mode=run.mode, processed=run.processed_count,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
        except StaleSyncRun:
            # A disconnect, completed run, or superseded lease made this delivery obsolete.
            continue
        except OAuthReconnectRequired:
            try:
                dependencies["syncs"].fail(
                    owner_id, run_id, generation, lease_id, "reconnect_required", now
                )
                log_event(
                    logger, "warning", "sync_run_failed", run_id=run_id,
                    category="reconnect_required",
                )
            except StaleSyncRun:
                pass
        except Exception as error:  # noqa: BLE001 - every worker failure must signal SQS to retry
            attempt = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
            log_event(
                logger, "warning", "sync_worker_failure",
                category=category_of(error), attempt=attempt,
            )
            if (
                attempt >= 5
                and owner_id is not None and run_id is not None and generation is not None
            ):
                try:
                    dependencies["syncs"].fail(
                        owner_id, run_id, generation, lease_id, "partial", now
                    )
                    log_event(
                        logger, "warning", "sync_run_failed", run_id=run_id,
                        category="partial",
                    )
                except StaleSyncRun:
                    pass
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
