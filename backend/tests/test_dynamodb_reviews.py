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
from app.services.ai_extraction import ValidatedExtraction
from app.services.email_rules import AiCall, RuleDecision
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
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users",
        )
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        reviews = DynamoReviewRepository(
            client=client, applications_table="Applications", events_table="Events",
            users_table="Users",
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


def _local_decision():
    return RuleDecision(
        outcome="review_required", reason="ai_proposal", origin="ai",
        rule_reason="ambiguous_application", application_id="app-1",
        status=ApplicationStatus.INTERVIEWING,
        evidence="invite you to interview",
        ai_proposal=ValidatedExtraction(
            company=None, role=None, employment_type=None, season=None,
            year=None, status=ApplicationStatus.INTERVIEWING,
            requisition_id=None, evidence={"status": "invite you to interview"},
            status_in_subject_only=False,
        ),
    )


def _connect_local_user(reviews, generation=1):
    reviews._client.put_item(TableName="Users", Item={
        "owner_id": {"S": "user-a"},
        "generation": {"N": str(generation)},
        "encrypted_token": {"S": "synthetic-token"},
    })


def test_local_suggestion_is_review_only_and_cannot_overwrite(store):
    applications, emails, reviews = store
    _application(applications)
    _review(emails)
    _connect_local_user(reviews)

    saved = reviews.save_local_suggestion(
        owner_id="user-a", message_id="msg-1", generation=1,
        decision=_local_decision(), model="gemma4:latest",
    )

    assert saved.reason == "ai_proposal"
    assert saved.rule_reason == "ambiguous_application"
    assert saved.ai_provider == "ollama"
    assert saved.ai_proposal.status is ApplicationStatus.INTERVIEWING
    assert applications.get(owner_id="user-a", application_id="app-1").status is ApplicationStatus.APPLIED
    with pytest.raises(ReviewVersionConflict):
        reviews.save_local_suggestion(
            owner_id="user-a", message_id="msg-1", generation=1,
            decision=_local_decision(), model="gemma4:latest",
        )


def test_local_suggestion_rejects_other_owner_and_changed_gmail_generation(store):
    _, emails, reviews = store
    _review(emails)
    _connect_local_user(reviews, generation=2)

    assert reviews.get_pending(owner_id="user-b", message_id="msg-1") is None
    with pytest.raises(ReviewNotFound):
        reviews.save_local_suggestion(
            owner_id="user-b", message_id="msg-1", generation=2,
            decision=_local_decision(), model="gemma4:latest",
        )
    with pytest.raises(ReviewVersionConflict):
        reviews.save_local_suggestion(
            owner_id="user-a", message_id="msg-1", generation=1,
            decision=_local_decision(), model="gemma4:latest",
        )


def test_local_suggestion_rejects_review_resolved_during_inference(store):
    _, emails, reviews = store
    _review(emails)
    _connect_local_user(reviews)
    reviews.resolve(
        owner_id="user-a", message_id="msg-1",
        resolution=ReviewResolution(action="reject"), occurred_at=NOW,
    )
    with pytest.raises(ReviewAlreadyResolved):
        reviews.save_local_suggestion(
            owner_id="user-a", message_id="msg-1", generation=1,
            decision=_local_decision(), model="gemma4:latest",
        )


def test_stale_accept_cannot_apply_a_new_local_suggestion(store):
    applications, emails, reviews = store
    _application(applications)
    _review(emails, status="applied")
    _connect_local_user(reviews)
    before = reviews.list_pending(owner_id="user-a", limit=10, cursor=None).items[0]
    assert before.review_version == 0

    saved = reviews.save_local_suggestion(
        owner_id="user-a", message_id="msg-1", generation=1,
        decision=_local_decision(), model="gemma4:latest",
    )
    assert saved.review_version == 1
    with pytest.raises(ReviewVersionConflict):
        reviews.resolve(
            owner_id="user-a", message_id="msg-1",
            resolution=ReviewResolution(
                action="accept", expected_version=1,
                expected_review_version=before.review_version,
            ),
            occurred_at=NOW,
        )
    assert applications.get(owner_id="user-a", application_id="app-1").status is ApplicationStatus.APPLIED

    reviews.resolve(
        owner_id="user-a", message_id="msg-1",
        resolution=ReviewResolution(
            action="accept", expected_version=1,
            expected_review_version=saved.review_version,
        ),
        occurred_at=NOW,
    )
    assert applications.get(owner_id="user-a", application_id="app-1").status is ApplicationStatus.INTERVIEWING


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
        reviews.resolve(owner_id="user-a", message_id="msg-1", resolution=ReviewResolution(action="accept", expected_version=1, expected_review_version=0), occurred_at=NOW)

    assert result.action == "reject"
    assert result.application_id is None


def test_accept_updates_application_and_links_email_to_history(store) -> None:
    applications, emails, reviews = store
    _application(applications)
    _review(emails)

    result = reviews.resolve(
        owner_id="user-a",
        message_id="msg-1",
        resolution=ReviewResolution(action="accept", expected_version=1, expected_review_version=0),
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


def _ai_review(emails, owner="user-a", message_id="msg-ai"):
    rule = RuleDecision(outcome="review_required", reason="ambiguous_application")
    # record() needs a users table for AI decisions; store the item shape directly.
    message = ParsedGmailMessage(
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        received_at=NOW - timedelta(days=1),
        subject="Update",
        sender="jobs@acme.test",
        text="synthetic message",
    )
    decision = RuleDecision(
        outcome="review_required",
        reason="ai_proposal",
        application_id="app-1",
        status=ApplicationStatus.INTERVIEWING,
        evidence="interview invitation",
        origin="ai",
        rule_reason="ambiguous_application",
        ai_proposal=ValidatedExtraction(
            company="Acme", role="Engineer", employment_type=None, season=None,
            year=None, status=ApplicationStatus.INTERVIEWING, requisition_id=None,
            evidence={"status": "interview invitation"}, status_in_subject_only=False,
        ),
        ai_note="ai_budget_exhausted",
        ai_call=AiCall(provider="openai", model="m", settings_version=1, rule_decision=rule),
    )
    item = emails._outcome_item(owner, message, "review_required", decision.reason, decision)
    emails._client.put_item(TableName="Events", Item=emails._encode(item))


def test_pending_reviews_expose_ai_fields_to_the_owner_only(store) -> None:
    _, emails, reviews = store
    _ai_review(emails)
    _review(emails, message_id="msg-rule")

    items = {
        item.message_id: item
        for item in reviews.list_pending(owner_id="user-a", limit=10, cursor=None).items
    }
    ai = items["msg-ai"]
    assert ai.reason == "ai_proposal"
    assert ai.rule_reason == "ambiguous_application"
    assert ai.ai_note == "ai_budget_exhausted"
    assert ai.ai_provider == "openai"
    assert ai.ai_proposal is not None
    assert ai.ai_proposal.company == "Acme"
    assert ai.ai_proposal.status == ApplicationStatus.INTERVIEWING
    assert ai.ai_proposal.evidence == {"status": "interview invitation"}
    rule = items["msg-rule"]
    assert (rule.ai_proposal, rule.rule_reason, rule.ai_note, rule.ai_provider) == (
        None, None, None, None,
    )
    assert reviews.list_pending(owner_id="user-b", limit=10, cursor=None).items == []
