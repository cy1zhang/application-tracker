"""Shared synthetic helpers for the AI escalation and pipeline tests."""

from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3

from app.domain.ai import AiProvider
from app.domain.applications import ApplicationCreate
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.ai_providers import ProviderError
from app.services.email_rules import ApplicationCandidate
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
TOKEN = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}
MODELS = {AiProvider.OPENAI: "synthetic-model"}
SYNTHETIC_KEY = "synthetic-key-0123456789"

AMBIGUOUS_TEXT = "We invite you to interview for Software Engineer Intern at Acme."
UNKNOWN_STAGE_TEXT = (
    "Acme next steps for Software Engineer Intern. "
    "Please complete the coding challenge today."
)
CLEAR_TEXT = "Globex invites you to interview for Data Intern."


def gmail_message(message_id: str, text: str, *, thread: str | None = None,
                  subject: str = "Application update") -> dict:
    return {
        "id": message_id,
        "threadId": thread or f"thread-{message_id}",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "Recruiting <jobs@acme.test>"},
            ],
            "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        },
    }


def parsed(message_id: str, text: str, *, subject: str = "Application update"):
    return ParsedGmailMessage(
        message_id=message_id, thread_id=f"thread-{message_id}", received_at=NOW,
        subject=subject, sender="Recruiting <jobs@acme.test>", text=text,
    )


class FakeCipher:
    """Returns a per-owner key so isolation is observable; counts decrypts."""

    def __init__(self):
        self.calls = []

    def decrypt(self, owner_id, credential_type, encrypted):
        self.calls.append((owner_id, credential_type))
        assert credential_type == "ai_api_key"
        return f"{SYNTHETIC_KEY}-{owner_id}"

    def decrypt_refresh_token(self, owner_id, encrypted):
        return "synthetic-refresh"


class StubProvider:
    """Scripted provider: each response is a dict to return or a ProviderError."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def extract(self, api_key, model, prompt_input):
        self.calls.append((api_key, model, prompt_input))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


def error(category: str) -> ProviderError:
    return ProviderError(category)


def extraction(**overrides) -> dict:
    """A full raw extraction object (every key present)."""
    evidence_keys = (
        "company", "role", "employment_type", "season", "year", "status",
        "requisition_id",
    )
    raw = {key: None for key in evidence_keys}
    raw["kind"] = "application_update"
    raw["evidence"] = {key: None for key in evidence_keys}
    evidence = overrides.pop("evidence", {})
    raw.update(overrides)
    raw["evidence"].update(evidence)
    return raw


def interview_extraction() -> dict:
    return extraction(
        company="Acme", role="Software Engineer Intern", status="interviewing",
        evidence={
            "company": "Acme", "role": "Software Engineer Intern",
            "status": "invite you to interview",
        },
    )


def oa_extraction() -> dict:
    return extraction(
        company="Acme", role="Software Engineer Intern", status="oa_received",
        evidence={
            "company": "Acme", "role": "Software Engineer Intern",
            "status": "complete the coding challenge",
        },
    )


class Env:
    def __init__(self):
        self.client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            self.client, applications_table="Applications", events_table="Events",
            users_table="Users", sync_runs_table="SyncRuns",
        )
        self.ai = DynamoAiSettingsRepository(client=self.client, users_table="Users")
        self.gmail = DynamoGmailRepository(
            client=self.client, users_table="Users", oauth_states_table="OAuthStates"
        )
        self.syncs = DynamoSyncRepository(
            client=self.client, users_table="Users", sync_runs_table="SyncRuns"
        )
        self.applications = DynamoApplicationRepository(
            client=self.client, applications_table="Applications",
            events_table="Events",
        )
        self.runs = {}

    def connect(self, owner="owner-a", *, ai=True, enabled=True):
        if ai:
            self.ai.save(owner, AiProvider.OPENAI, TOKEN, "1234", enabled, NOW)
        self.gmail.save_connection(owner, 0, f"{owner}@example.test", TOKEN, NOW)
        run = self.syncs.start(owner, f"run-{owner}", NOW)
        assert self.syncs.claim(owner, run.run_id, run.generation, f"lease-{owner}", NOW)
        self.runs[owner] = run
        return run

    def emails(self, owner="owner-a") -> DynamoEmailRepository:
        run = self.runs[owner]
        return DynamoEmailRepository(
            client=self.client, applications_table="Applications",
            events_table="Events", users_table="Users",
            guard_generation=run.generation, guard_run_id=run.run_id,
        )

    def app(self, owner="owner-a", application_id="app-1", *, company="Acme",
            role="Software Engineer Intern", **extra):
        return self.applications.create(
            owner_id=owner,
            payload=ApplicationCreate(
                company=company, role=role, status="applied", **extra
            ),
            application_id=application_id, event_id=f"create-{owner}-{application_id}",
            occurred_at=NOW - timedelta(days=5),
        )

    def candidates(self, owner="owner-a", threads=None):
        threads = threads or {}
        out = []
        for application in self.applications.list(
            owner_id=owner, limit=100, cursor=None
        ).items:
            out.append(ApplicationCandidate(
                application=application,
                thread_ids=frozenset(threads.get(application.application_id, ())),
            ))
        return out

    def event(self, message_id, owner="owner-a"):
        item = self.client.get_item(
            TableName="Events", ConsistentRead=True,
            Key={"owner_id": {"S": owner}, "event_id": {"S": f"gmail#{message_id}"}},
        ).get("Item")
        if item is None:
            return None
        from boto3.dynamodb.types import TypeDeserializer

        deserializer = TypeDeserializer()
        return {k: deserializer.deserialize(v) for k, v in item.items()}

    def user(self, owner="owner-a"):
        from boto3.dynamodb.types import TypeDeserializer

        deserializer = TypeDeserializer()
        item = self.client.get_item(
            TableName="Users", Key={"owner_id": {"S": owner}}, ConsistentRead=True
        )["Item"]
        return {k: deserializer.deserialize(v) for k, v in item.items()}
