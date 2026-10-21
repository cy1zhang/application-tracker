from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.domain.applications import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationStatus,
    MergeRequest,
)
from app.domain.reviews import ReviewApplicationCreate, ReviewResolution
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_reviews import (
    DynamoReviewRepository,
    ReviewAlreadyResolved,
    ReviewNotFound,
    ReviewVersionConflict,
)
from app.repositories.dynamodb_tables import create_tables
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.fixture
def store():
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
        reviews = DynamoReviewRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        yield applications, emails, reviews


def _application(applications, owner="user-a", app_id="app-1"):
    return applications.create(
        owner_id=owner,
        payload=ApplicationCreate(company="Acme", role="Engineer", status="applied"),
        application_id=app_id,
        event_id=f"create-{app_id}",
        occurred_at=NOW - timedelta(days=2),
    )


def _review(emails, owner="user-a", message_id="msg-1", app_id="app-1", status="interviewing"):
    return emails.record(
        owner_id=owner,
        message=ParsedGmailMessage(
            message_id=message_id,
            thread_id=f"thread-{message_id}",
            received_at=NOW - timedelta(days=1),
            subject="Update",
            sender="Recruiting <jobs@acme.test>",
            text="synthetic message",
        ),
        decision=RuleDecision(
            outcome="review_required",
            reason="ambiguous_application",
            application_id=app_id,
            status=ApplicationStatus(status) if status else None,
            evidence="interview invitation",
        ),
    )


def test_pending_reviews_are_owner_scoped_paginated_and_hide_resolved(store) -> None:
    _, emails, reviews = store
    _review(emails, message_id="msg-1")
    _review(emails, message_id="msg-2")
    _review(emails, owner="user-b", message_id="msg-3")

    first = reviews.list_pending(owner_id="user-a", limit=1, cursor=None)
    second = reviews.list_pending(owner_id="user-a", limit=10, cursor=first.next_cursor)
    reviews.resolve(
        owner_id="user-a",
        message_id="msg-1",
        resolution=ReviewResolution(action="reject"),
        occurred_at=NOW,
    )

    assert [item.message_id for item in first.items + second.items] == ["msg-1", "msg-2"]
    assert first.items[0].subject == "Update"
    assert first.items[0].sender == "Recruiting <jobs@acme.test>"
    assert [item.message_id for item in reviews.list_pending(owner_id="user-a", limit=10, cursor=None).items] == ["msg-2"]
    assert reviews.list_pending(owner_id="user-b", limit=10, cursor=None).items[0].message_id == "msg-3"


def test_reject_is_terminal_and_cross_owner_resolution_is_hidden(store) -> None:
    _, emails, reviews = store
    _review(emails)

    with pytest.raises(ReviewNotFound):
        reviews.resolve(owner_id="user-b", message_id="msg-1", resolution=ReviewResolution(action="reject"), occurred_at=NOW)
    result = reviews.resolve(owner_id="user-a", message_id="msg-1", resolution=ReviewResolution(action="reject"), occurred_at=NOW)
    with pytest.raises(ReviewAlreadyResolved):
        reviews.resolve(owner_id="user-a", message_id="msg-1", resolution=ReviewResolution(action="accept", expected_version=1), occurred_at=NOW)

    assert result.action == "reject"
    assert result.application_id is None


def test_accept_updates_application_and_links_email_to_history(store) -> None:
    applications, emails, reviews = store
    _application(applications)
    _review(emails)

    result = reviews.resolve(
        owner_id="user-a",
        message_id="msg-1",
        resolution=ReviewResolution(action="accept", expected_version=1),
        occurred_at=NOW,
    )

    assert result.application_id == "app-1"
    assert applications.get(owner_id="user-a", application_id="app-1").status == ApplicationStatus.INTERVIEWING
    events = applications.list_events(owner_id="user-a", application_id="app-1")
    assert len(events) == 2
    assert events[-1].origin.value == "user"
    assert events[-1].changes["status"].after == "interviewing"
    assert events[-1].thread_id == "thread-msg-1"
    assert reviews.list_pending(owner_id="user-a", limit=10, cursor=None).items == []


def test_edit_and_attach_require_current_version(store) -> None:
    applications, emails, reviews = store
    _application(applications)
    _review(emails, message_id="edit")
    _review(emails, message_id="attach")

    with pytest.raises(ReviewVersionConflict):
        reviews.resolve(
            owner_id="user-a",
            message_id="edit",
            resolution=ReviewResolution(
                action="edit",
                application_id="app-1",
                patch=ApplicationPatch(expected_version=9, status=ApplicationStatus.OFFER),
            ),
            occurred_at=NOW,
        )
    reviews.resolve(
        owner_id="user-a",
        message_id="edit",
        resolution=ReviewResolution(
            action="edit",
            application_id="app-1",
            patch=ApplicationPatch(expected_version=1, status=ApplicationStatus.OFFER),
        ),
        occurred_at=NOW,
    )
    reviews.resolve(
        owner_id="user-a",
        message_id="attach",
        resolution=ReviewResolution(action="attach", application_id="app-1", expected_version=2),
        occurred_at=NOW + timedelta(seconds=1),
    )

    assert applications.get(owner_id="user-a", application_id="app-1").status == ApplicationStatus.OFFER
    assert len(applications.list_events(owner_id="user-a", application_id="app-1")) == 3


def test_create_from_review_can_keep_status_unknown(store) -> None:
    applications, emails, reviews = store
    _review(emails, app_id=None, status=None)

    result = reviews.resolve(
        owner_id="user-a",
        message_id="msg-1",
        resolution=ReviewResolution(
            action="create",
            application=ReviewApplicationCreate(company="Acme", role="Researcher"),
        ),
        occurred_at=NOW,
        new_application_id="new-app",
    )

    assert result.application_id == "new-app"
    created = applications.get(owner_id="user-a", application_id="new-app")
    assert created.status is None
    assert len(applications.list_events(owner_id="user-a", application_id="new-app")) == 1


def test_attach_to_merged_id_uses_retained_application(store) -> None:
    applications, emails, reviews = store
    _application(applications, app_id="keep")
    _application(applications, app_id="duplicate")
    applications.merge(
        owner_id="user-a",
        keep_id="keep",
        payload=MergeRequest(
            duplicate_id="duplicate",
            expected_keep_version=1,
            expected_duplicate_version=1,
        ),
        event_id="merge-1",
        occurred_at=NOW - timedelta(hours=1),
    )
    _review(emails, app_id="duplicate")

    result = reviews.resolve(
        owner_id="user-a",
        message_id="msg-1",
        resolution=ReviewResolution(
            action="attach", application_id="duplicate", expected_version=2
        ),
        occurred_at=NOW,
    )

    assert result.application_id == "keep"
    assert len(applications.list_events(owner_id="user-a", application_id="keep")) == 4
