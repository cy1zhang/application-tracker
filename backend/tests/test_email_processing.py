from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
from moto import mock_aws

from app.domain.applications import ApplicationCreate
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.email_processing import process_message
from app.services.email_rules import ApplicationCandidate

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _gmail(message_id: str, text: str) -> dict:
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Application update"},
                {"name": "From", "value": "Recruiting <jobs@acme.test>"},
            ],
            "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        },
    }


def test_processing_is_owner_scoped_idempotent_and_reviewable() -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_tables(client, applications_table="Applications", events_table="Events")
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        application = applications.create(
            owner_id="user-a",
            payload=ApplicationCreate(
                company="Acme", role="Software Engineer Intern", status="applied"
            ),
            application_id="app-1",
            event_id="create-1",
            occurred_at=NOW - timedelta(days=1),
        )
        candidates = [
            ApplicationCandidate(application=application, thread_ids=frozenset({"thread-1"}))
        ]
        clear = _gmail(
            "msg-1", "Acme invites you to interview for Software Engineer Intern."
        )
        first = process_message("user-a", clear, candidates, emails)
        repeated = process_message("user-a", clear, candidates, emails)
        ambiguous = process_message(
            "user-a", _gmail("msg-2", "Acme invites you to interview."), candidates, emails
        )
        foreign = process_message("user-b", clear, candidates, emails)
        malformed = process_message(
            "user-a", {**_gmail("bad-1", "text"), "payload": {}}, candidates, emails
        )

        assert first.outcome == "applied"
        assert repeated == first
        assert ambiguous.outcome == "review_required"
        assert foreign.outcome == "review_required"
        assert malformed.outcome == "review_required"
        assert applications.get(owner_id="user-a", application_id="app-1").version == 2
        assert len(applications.list_events(owner_id="user-a", application_id="app-1")) == 2
