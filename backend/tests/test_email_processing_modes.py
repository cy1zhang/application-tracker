from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.domain.applications import ApplicationCreate
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.email_processing import process_message
from app.services.email_rules import ApplicationCandidate

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _gmail(message_id, text, *, sender="Recruiting <jobs@acme.test>", subject="Update"):
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        },
    }


class ExplodingEscalator:
    def escalate(self, *args, **kwargs):
        raise AssertionError("the hosted AI must not run when auto-apply is on")


@pytest.fixture
def setup():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
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
            application_id="app-1", event_id="create-1",
            occurred_at=NOW - timedelta(days=1),
        )
        candidates = [
            ApplicationCandidate(application=application, thread_ids=frozenset({"thread-1"}))
        ]
        yield client, emails, candidates


def _item(client, message_id, owner="user-a"):
    response = client.get_item(
        TableName="Events",
        Key={"owner_id": {"S": owner}, "event_id": {"S": f"gmail#{message_id}"}},
    )
    return response.get("Item")


CLEAR = "Acme invites you to interview for Software Engineer Intern."
VAGUE = "Acme has an update about your candidacy."
ALERT = {"sender": "Alerts <jobalerts-noreply@linkedin.com>", "subject": "Acme Corp - Remote and more"}


def test_mode_off_processing_unchanged(setup) -> None:
    client, emails, candidates = setup

    default = process_message("user-a", _gmail("m1", CLEAR), candidates, emails)
    explicit = process_message("user-a", _gmail("m1", CLEAR), candidates, emails, mode="off")
    vague = process_message("user-a", _gmail("m2", VAGUE), candidates, emails, mode="off")
    alert = process_message("user-a", _gmail("m3", VAGUE, **ALERT), candidates, emails, mode="off")

    assert default.outcome == "applied" and explicit == default
    assert vague.outcome in {"ignored", "review_required"}
    assert alert.reason != "filtered:job_alert"
    assert all(
        _item(client, name)["outcome"]["S"] != "pending_ai" for name in ("m1", "m2", "m3")
    )


def test_plan_mode_filters_then_queues(setup) -> None:
    client, emails, candidates = setup

    filtered = process_message(
        "user-a", _gmail("m1", VAGUE, **ALERT), candidates, emails, mode="plan"
    )
    queued = process_message("user-a", _gmail("m2", VAGUE), candidates, emails, mode="plan")

    assert (filtered.outcome, filtered.reason) == ("ignored", "filtered:job_alert")
    first = _item(client, "m1")
    assert first["subject"]["S"] == ALERT["subject"]
    assert "jobalerts-noreply@linkedin.com" in first["sender"]["S"]
    assert (queued.outcome, queued.reason) == ("pending_ai", "awaiting_local_ai")
    second = _item(client, "m2")
    assert second["subject"]["S"] == "Update"
    assert second["thread_id"]["S"] == "thread-1"
    assert second["received_at"]["S"] == NOW.isoformat()


def test_plan_mode_keeps_rules_applied(setup) -> None:
    client, emails, candidates = setup

    outcome = process_message("user-a", _gmail("m1", CLEAR), candidates, emails, mode="on")

    assert outcome.outcome == "applied"
    assert _item(client, "m1")["outcome"]["S"] == "applied"


def test_plan_mode_does_not_call_escalator(setup) -> None:
    _, emails, candidates = setup

    outcome = process_message(
        "user-a", _gmail("m1", "Acme invites you to interview."), candidates, emails,
        escalator=ExplodingEscalator(), mode="plan",
    )

    assert outcome.outcome == "pending_ai"


def test_pending_ai_replay_is_idempotent(setup) -> None:
    client, emails, candidates = setup
    first = process_message("user-a", _gmail("m1", VAGUE), candidates, emails, mode="plan")
    before = _item(client, "m1")

    again = process_message("user-a", _gmail("m1", VAGUE), candidates, emails, mode="plan")
    off_again = process_message("user-a", _gmail("m1", VAGUE), candidates, emails)

    assert first == again == off_again
    assert _item(client, "m1") == before
    assert client.query(
        TableName="Events",
        KeyConditionExpression="owner_id = :o AND begins_with(event_id, :p)",
        ExpressionAttributeValues={":o": {"S": "user-a"}, ":p": {"S": "gmail#"}},
    )["Count"] == 1
    assert emails.get_terminal_outcome("user-a", "m1") is None
