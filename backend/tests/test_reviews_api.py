from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_subject, get_review_repository
from app.domain.applications import ApplicationStatus
from app.main import app
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_reviews import DynamoReviewRepository
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.fixture
def review_api(repository):
    reviews = DynamoReviewRepository(
        client=repository._client,
        applications_table="Applications",
        events_table="Events",
    )
    emails = DynamoEmailRepository(
        client=repository._client,
        applications_table="Applications",
        events_table="Events",
    )
    app.dependency_overrides[get_review_repository] = lambda: reviews
    app.dependency_overrides[get_current_subject] = lambda: "user-a"
    emails.record(
        owner_id="user-a",
        message=ParsedGmailMessage(
            message_id="msg-1",
            thread_id="thread-1",
            received_at=NOW - timedelta(days=1),
            subject="Synthetic",
            sender="jobs@acme.test",
            text="synthetic fixture",
        ),
        decision=RuleDecision(
            outcome="review_required",
            reason="ambiguous_application",
            status=ApplicationStatus.INTERVIEWING,
            evidence="interview invitation",
        ),
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_review_list_and_reject_are_owner_scoped(review_api) -> None:
    response = review_api.get("/reviews")
    assert response.status_code == 200
    assert response.json()["items"][0]["message_id"] == "msg-1"
    assert response.json()["items"][0]["source_url"].startswith("https://mail.google.com/")

    app.dependency_overrides[get_current_subject] = lambda: "user-b"
    assert review_api.get("/reviews").json()["items"] == []
    assert review_api.post("/reviews/msg-1/resolve", json={"action": "reject"}).status_code == 404

    app.dependency_overrides[get_current_subject] = lambda: "user-a"
    assert review_api.post("/reviews/msg-1/resolve", json={"action": "reject"}).status_code == 200
    assert review_api.get("/reviews").json()["items"] == []
    assert review_api.post("/reviews/msg-1/resolve", json={"action": "reject"}).status_code == 409


def test_review_resolution_validates_action_fields(review_api) -> None:
    assert review_api.post("/reviews/msg-1/resolve", json={"action": "accept"}).status_code == 422
    assert review_api.post("/reviews/msg-1/resolve", json={"action": "attach", "application_id": "app-1"}).status_code == 422
    assert review_api.get("/reviews?cursor=bad!").status_code == 422
