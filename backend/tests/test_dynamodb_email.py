from datetime import UTC, datetime, timedelta

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer
from moto import mock_aws

from app.domain.applications import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationStatus,
)
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository, StaleSyncRun
from app.repositories.dynamodb_tables import create_tables
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.fixture
def storage():
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
        yield client, applications, emails


def _message(message_id="msg-1", text="SECRET BODY", received_at=NOW):
    return ParsedGmailMessage(
        message_id=message_id,
        thread_id="thread-1",
        received_at=received_at,
        subject="Update",
        sender="jobs@example.test",
        text=text,
    )


def _create(applications, owner_id="user-a"):
    return applications.create(
        owner_id=owner_id,
        payload=ApplicationCreate(
            company="Acme", role="Software Engineer Intern", status="applied"
        ),
        application_id="app-1",
        event_id="create-1",
        occurred_at=NOW - timedelta(days=1),
    )


def _decision(outcome="applied", application_id="app-1", status=None):
    return RuleDecision(
        outcome=outcome,
        reason="explicit_event" if outcome == "applied" else "ambiguous_application",
        application_id=application_id,
        status=status or ApplicationStatus.INTERVIEWING,
        evidence="invite you to interview",
    )


def _stored_event(client, owner_id, message_id):
    response = client.get_item(
        TableName="Events",
        Key={"owner_id": {"S": owner_id}, "event_id": {"S": f"gmail#{message_id}"}},
        ConsistentRead=True,
    )
    deserializer = TypeDeserializer()
    return {
        key: deserializer.deserialize(value)
        for key, value in response["Item"].items()
    }


def test_applied_status_and_outcome_are_one_idempotent_history_event(storage) -> None:
    client, applications, emails = storage
    _create(applications)

    first = emails.record(
        owner_id="user-a",
        message=_message(),
        decision=_decision(),
        application_version=1,
    )
    second = emails.record(
        owner_id="user-a",
        message=_message(),
        decision=_decision(),
        application_version=1,
    )

    assert first == second
    assert first.outcome == "applied"
    application = applications.get(owner_id="user-a", application_id="app-1")
    assert application.version == 2
    assert application.status == ApplicationStatus.INTERVIEWING
    events = applications.list_events(owner_id="user-a", application_id="app-1")
    assert len(events) == 2
    assert events[-1].origin.value == "rules"
    item = _stored_event(client, "user-a", "msg-1")
    assert item["source_message_id"] == "msg-1"
    assert item["thread_id"] == "thread-1"
    assert "SECRET BODY" not in str(item)


def test_ignored_and_review_outcomes_are_persisted_without_mutation(storage) -> None:
    client, applications, emails = storage
    _create(applications)

    ignored = emails.record(
        owner_id="user-a",
        message=_message("ignored"),
        decision=_decision(outcome="ignored", application_id=None),
    )
    review = emails.record(
        owner_id="user-a",
        message=_message("review"),
        decision=_decision(outcome="review_required", application_id=None),
    )

    assert ignored.outcome == "ignored"
    assert review.outcome == "review_required"
    assert _stored_event(client, "user-a", "review")["outcome"] == "review_required"
    assert applications.get(owner_id="user-a", application_id="app-1").version == 1
    assert len(applications.list_events(owner_id="user-a", application_id="app-1")) == 1


def test_message_and_application_keys_are_owner_scoped(storage) -> None:
    _, applications, emails = storage
    _create(applications)
    foreign = emails.record(
        owner_id="user-b",
        message=_message(),
        decision=_decision(),
        application_version=1,
    )
    owner = emails.record(
        owner_id="user-a",
        message=_message(),
        decision=_decision(),
        application_version=1,
    )

    assert foreign.outcome == "review_required"
    assert owner.outcome == "applied"
    assert applications.get(owner_id="user-a", application_id="app-1").version == 2


def test_stale_version_is_retryable_and_can_be_reprocessed(storage) -> None:
    _, applications, emails = storage
    _create(applications)
    applications.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, role="Software Developer Intern"),
        event_id="manual-edit",
        occurred_at=NOW - timedelta(hours=1),
    )

    stale = emails.record(
        owner_id="user-a",
        message=_message(),
        decision=_decision(),
        application_version=1,
    )
    retried = emails.record(
        owner_id="user-a",
        message=_message(),
        decision=_decision(),
        application_version=2,
    )

    assert stale.outcome == "retryable"
    assert retried.outcome == "applied"
    assert applications.get(owner_id="user-a", application_id="app-1").version == 3


def test_explicit_cycle_fills_unknown_fields_without_guessing(storage) -> None:
    client, applications, emails = storage
    _create(applications)
    decision = RuleDecision(
        outcome="applied",
        reason="explicit_event",
        application_id="app-1",
        status=ApplicationStatus.INTERVIEWING,
        season="summer",
        year=2027,
        requisition_id="REQ-123",
        evidence="Summer 2027 interview",
    )

    emails.record(
        owner_id="user-a", message=_message(), decision=decision, application_version=1
    )

    application = applications.get(owner_id="user-a", application_id="app-1")
    assert application.season.value == "summer"
    assert application.year == 2027
    assert _stored_event(client, "user-a", "msg-1")["requisition_id"] == "REQ-123"


def test_old_email_cannot_undo_manual_status_correction(storage) -> None:
    _, applications, emails = storage
    _create(applications)
    applications.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, status=ApplicationStatus.OFFER),
        event_id="manual-status",
        occurred_at=NOW + timedelta(hours=1),
    )

    result = emails.record(
        owner_id="user-a",
        message=_message(received_at=NOW),
        decision=_decision(status=ApplicationStatus.INTERVIEWING),
        application_version=2,
    )

    assert result.outcome == "review_required"
    assert applications.get(owner_id="user-a", application_id="app-1").status == ApplicationStatus.OFFER


def test_later_clear_email_can_update_manually_corrected_status(storage) -> None:
    _, applications, emails = storage
    _create(applications)
    applications.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, status=ApplicationStatus.INTERVIEWING),
        event_id="manual-status",
        occurred_at=NOW,
    )

    result = emails.record(
        owner_id="user-a",
        message=_message(received_at=NOW + timedelta(hours=1)),
        decision=_decision(status=ApplicationStatus.REJECTED),
        application_version=2,
    )

    assert result.outcome == "applied"
    assert applications.get(owner_id="user-a", application_id="app-1").status == ApplicationStatus.REJECTED


def test_older_email_can_fill_unknown_cycle_after_unrelated_role_edit(storage) -> None:
    _, applications, emails = storage
    _create(applications)
    applications.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, role="Software Developer Intern"),
        event_id="manual-role",
        occurred_at=NOW + timedelta(hours=1),
    )

    result = emails.record(
        owner_id="user-a",
        message=_message(received_at=NOW),
        decision=RuleDecision(
            outcome="applied",
            reason="explicit_event",
            application_id="app-1",
            status=ApplicationStatus.INTERVIEWING,
            season="summer",
            year=2027,
        ),
        application_version=2,
    )

    assert result.outcome == "applied"
    application = applications.get(owner_id="user-a", application_id="app-1")
    assert application.role == "Software Developer Intern"
    assert application.season.value == "summer"
    assert application.year == 2027
    assert application.updated_at == NOW + timedelta(hours=1)


@pytest.mark.parametrize("outcome", ["applied", "ignored"])
def test_disconnected_run_cannot_commit_email_outcome(outcome) -> None:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", sync_runs_table="SyncRuns",
        )
        gmail = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        gmail.save_connection(
            "user-a", 0, "a@example.test",
            {"ciphertext": "opaque", "nonce": "opaque", "encrypted_data_key": "opaque"},
            NOW - timedelta(days=1),
        )
        sync = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        run = sync.start("user-a", "run-1", NOW)
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        if outcome == "applied":
            _create(applications)
        emails = DynamoEmailRepository(
            client=client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            guard_generation=run.generation,
            guard_run_id=run.run_id,
        )
        first = emails.record(
            owner_id="user-a",
            message=_message(message_id="before-disconnect"),
            decision=_decision(outcome=outcome),
            application_version=1 if outcome == "applied" else None,
        )
        assert first.outcome == outcome
        gmail.disconnect("user-a")

        with pytest.raises(StaleSyncRun):
            emails.record(
                owner_id="user-a",
                message=_message(received_at=NOW + timedelta(hours=1)),
                decision=_decision(
                    outcome=outcome,
                    status=ApplicationStatus.REJECTED if outcome == "applied" else None,
                ),
                application_version=2 if outcome == "applied" else None,
            )

        assert "Item" not in client.get_item(
            TableName="Events",
            Key={"owner_id": {"S": "user-a"}, "event_id": {"S": "gmail#msg-1"}},
        )
        if outcome == "applied":
            assert applications.get(owner_id="user-a", application_id="app-1").version == 2
