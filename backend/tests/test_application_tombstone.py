"""Pins what survives an application delete (PRD criteria 2, 8, 10, 11, 13).

Application delete is soft: a minimal item stays, and every processed Gmail
message keeps a terminal Events row. These tests lock in that deleted
applications are never resurrected by replays, new mail, or review decisions,
and that account deletion removes every one of those residues.
"""

from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.domain.account import AccountState
from app.domain.applications import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationStatus,
)
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_reviews import (
    DynamoReviewRepository,
    ReviewNotFound,
    ReviewResolution,
)
from app.repositories.dynamodb_tables import create_tables
from app.services.account_deletion import AccountDeleter, DeletionResult
from app.services.email_processing import process_message
from app.services.email_rules import ApplicationCandidate, RuleDecision
from app.services.gmail_message import ParsedGmailMessage
from app.services.sync_candidates import load_candidates

NOW = datetime(2026, 10, 18, tzinfo=UTC)
CLEAR = "Acme invites you to interview for Software Engineer Intern."


class Env:
    pass


@pytest.fixture
def env():
    with mock_aws():
        e = Env()
        e.client = client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        names = {"applications_table": "Applications", "events_table": "Events"}
        e.applications = DynamoApplicationRepository(client=client, **names)
        e.emails = DynamoEmailRepository(client=client, **names)
        e.reviews = DynamoReviewRepository(client=client, **names)
        yield e


def _gmail(message_id: str, text: str = CLEAR, thread_id: str = "thread-1") -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
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


def _create(e, owner="user-a", app_id="app-1"):
    return e.applications.create(
        owner_id=owner,
        payload=ApplicationCreate(
            company="Acme", role="Software Engineer Intern", status="applied"
        ),
        application_id=app_id,
        event_id=f"create-{app_id}",
        occurred_at=NOW - timedelta(days=2),
    )


def _linked(application):
    return [
        ApplicationCandidate(application=application, thread_ids=frozenset({"thread-1"}))
    ]


def _delete(e, owner="user-a", app_id="app-1", version=1):
    e.applications.delete(
        owner_id=owner,
        application_id=app_id,
        expected_version=version,
        event_id=f"delete-{app_id}",
        occurred_at=NOW,
    )


def _dump(client, table, owner="user-a"):
    items = client.query(
        TableName=table,
        KeyConditionExpression="owner_id = :o",
        ExpressionAttributeValues={":o": {"S": owner}},
        ConsistentRead=True,
    )["Items"]
    return sorted(str(sorted(item.items())) for item in items)


def _item(client, table, key_name, key, owner="user-a"):
    return client.get_item(
        TableName=table,
        Key={"owner_id": {"S": owner}, key_name: {"S": key}},
        ConsistentRead=True,
    ).get("Item")


def _review(e, message_id="msg-r", app_id="app-1"):
    return e.emails.record(
        owner_id="user-a",
        message=ParsedGmailMessage(
            message_id=message_id,
            thread_id="thread-r",
            received_at=NOW - timedelta(days=1),
            subject="Update",
            sender="Recruiting <jobs@acme.test>",
            text="synthetic message",
        ),
        decision=RuleDecision(
            outcome="review_required",
            reason="ambiguous_application",
            application_id=app_id,
            status=ApplicationStatus.INTERVIEWING,
            evidence="interview invitation",
        ),
    )


def test_replaying_a_recorded_message_after_delete_changes_nothing(env) -> None:
    application = _create(env)
    candidates = _linked(application)
    first = process_message("user-a", _gmail("msg-1"), candidates, env.emails)
    assert first.outcome == "applied"
    _delete(env, version=application.version + 1)
    applications_before = _dump(env.client, "Applications")
    events_before = _dump(env.client, "Events")

    replayed = process_message(
        "user-a", _gmail("msg-1"), load_candidates("user-a", env.applications), env.emails
    )
    # A replay that still carries the stale pre-delete candidate list.
    stale = process_message("user-a", _gmail("msg-1"), candidates, env.emails)

    assert replayed == first
    assert stale == first
    assert _dump(env.client, "Applications") == applications_before
    assert _dump(env.client, "Events") == events_before


def test_new_message_for_a_deleted_application_creates_nothing(env) -> None:
    application = _create(env)
    process_message("user-a", _gmail("msg-1"), _linked(application), env.emails)
    _delete(env, version=application.version + 1)
    applications_before = _dump(env.client, "Applications")

    candidates = load_candidates("user-a", env.applications)
    outcome = process_message("user-a", _gmail("msg-2"), candidates, env.emails)

    assert candidates == []
    assert outcome.outcome != "applied"
    assert _dump(env.client, "Applications") == applications_before
    assert env.applications.list(owner_id="user-a", limit=10, cursor=None).items == []


def test_new_message_with_a_stale_candidate_is_reviewed_not_applied(env) -> None:
    application = _create(env)
    stale_candidates = _linked(application)
    _delete(env, version=application.version)
    applications_before = _dump(env.client, "Applications")

    outcome = process_message("user-a", _gmail("msg-2"), stale_candidates, env.emails)

    assert outcome.outcome == "review_required"
    assert outcome.reason == "missing_application"
    assert _dump(env.client, "Applications") == applications_before


@pytest.mark.parametrize("action", ["accept", "attach", "edit"])
def test_review_decisions_on_a_deleted_application_change_nothing(env, action) -> None:
    application = _create(env)
    _review(env)
    _delete(env, version=application.version)
    applications_before = _dump(env.client, "Applications")
    events_before = _dump(env.client, "Events")
    resolutions = {
        "accept": ReviewResolution(action="accept", expected_version=1),
        "attach": ReviewResolution(
            action="attach", application_id="app-1", expected_version=1
        ),
        "edit": ReviewResolution(
            action="edit",
            application_id="app-1",
            patch=ApplicationPatch(expected_version=1, status=ApplicationStatus.OFFER),
        ),
    }

    with pytest.raises(ReviewNotFound):
        env.reviews.resolve(
            owner_id="user-a",
            message_id="msg-r",
            resolution=resolutions[action],
            occurred_at=NOW,
        )

    assert _dump(env.client, "Applications") == applications_before
    assert _dump(env.client, "Events") == events_before
    pending = env.reviews.list_pending(owner_id="user-a", limit=10, cursor=None).items
    assert [item.message_id for item in pending] == ["msg-r"]


def test_soft_deleted_item_holds_only_key_version_and_deleted_at(env) -> None:
    application = _create(env)
    _delete(env, version=application.version)

    item = _item(env.client, "Applications", "application_id", "app-1")

    assert set(item) == {"owner_id", "application_id", "version", "deleted_at"}
    assert item["version"] == {"N": "2"}


def test_account_deletion_removes_every_tombstone(env) -> None:
    application = _create(env)
    process_message("user-a", _gmail("msg-1"), _linked(application), env.emails)
    _delete(env, version=application.version + 1)
    assert _item(env.client, "Events", "event_id", "gmail#msg-1") is not None
    assert _item(env.client, "Applications", "application_id", "app-1") is not None
    accounts = DynamoAccountRepository(client=env.client, users_table="Users")
    accounts.begin_deletion("user-a", None, NOW)
    assert accounts.claim_deletion("user-a", "lease-1", NOW) is not None
    deleter = AccountDeleter(
        client=env.client, accounts=accounts, cipher=None, cognito=None,
        user_pool_id="pool", users_table="Users", applications_table="Applications",
        events_table="Events", sync_runs_table="SyncRuns",
        oauth_states_table="OAuthStates",
    )

    result = deleter.run("user-a", "lease-1", float("inf"))

    assert result == DeletionResult.DONE
    assert _dump(env.client, "Applications") == []
    assert _dump(env.client, "Events") == []
    assert accounts.get_status("user-a").state == AccountState.DELETED
