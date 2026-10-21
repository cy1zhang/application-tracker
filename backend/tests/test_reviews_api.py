from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_subject, get_review_repository
from app.api.reviews import get_local_review_ai_service
from app.domain.applications import ApplicationStatus
from app.main import app
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_reviews import DynamoReviewRepository
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage
from app.services.local_review_ai import LocalAiInput, LocalAiNotFound, LocalAiResult

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
    assert response.json()["items"][0]["subject"] == "Synthetic"
    assert response.json()["items"][0]["sender"] == "jobs@acme.test"
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


def test_review_list_exposes_ai_fields_only_to_the_owner(review_api, repository) -> None:
    from app.repositories.dynamodb_email import DynamoEmailRepository
    from app.services.ai_extraction import ValidatedExtraction
    from app.services.email_rules import AiCall

    emails = DynamoEmailRepository(
        client=repository._client, applications_table="Applications", events_table="Events"
    )
    rule = RuleDecision(outcome="review_required", reason="ambiguous_application")
    for owner in ("user-a", "user-b"):
        message = ParsedGmailMessage(
            message_id=f"ai-{owner}",
            thread_id="thread-ai",
            received_at=NOW - timedelta(hours=1),
            subject="AI",
            sender="jobs@acme.test",
            text="synthetic fixture",
        )
        decision = RuleDecision(
            outcome="review_required",
            reason="ai_proposal",
            status=ApplicationStatus.INTERVIEWING,
            evidence="interview invitation",
            origin="ai",
            rule_reason="ambiguous_application",
            ai_proposal=ValidatedExtraction(
                company="Acme", role=None, employment_type=None, season=None, year=None,
                status=ApplicationStatus.INTERVIEWING, requisition_id=None,
                evidence={"status": "interview invitation"}, status_in_subject_only=False,
            ),
            ai_note="ai_key_invalid",
            ai_call=AiCall(provider="gemini", model="m", settings_version=1, rule_decision=rule),
        )
        item = emails._outcome_item(owner, message, "review_required", "ai_proposal", decision)
        emails._client.put_item(TableName="Events", Item=emails._encode(item))

    items = {item["message_id"]: item for item in review_api.get("/reviews").json()["items"]}
    assert set(items) == {"msg-1", "ai-user-a"}
    ai = items["ai-user-a"]
    assert ai["reason"] == "ai_proposal"
    assert ai["rule_reason"] == "ambiguous_application"
    assert ai["ai_note"] == "ai_key_invalid"
    assert ai["ai_provider"] == "gemini"
    assert ai["ai_proposal"]["company"] == "Acme"
    assert ai["ai_proposal"]["evidence"] == {"status": "interview invitation"}
    assert "ai_model" not in ai
    rule_item = items["msg-1"]
    assert rule_item["reason"] == "ambiguous_application"
    assert rule_item["ai_proposal"] is None
    assert rule_item["ai_note"] is None


def test_local_ai_routes_use_authenticated_owner_and_return_suggestion(review_api):
    class FakeLocalService:
        def prepare(self, owner_id, message_id):
            assert (owner_id, message_id) == ("user-a", "msg-1")
            return LocalAiInput(
                system_prompt="synthetic system", user_prompt="synthetic user",
                format={"type": "object"}, generation=1,
            )

        def suggest(self, owner_id, message_id, *, generation, model, raw):
            assert (owner_id, message_id, generation, model) == (
                "user-a", "msg-1", 1, "gemma4:latest"
            )
            assert raw == {"synthetic": "output"}
            return LocalAiResult(saved=True)

    app.dependency_overrides[get_local_review_ai_service] = FakeLocalService
    prepared = review_api.get("/reviews/msg-1/local-ai-input")
    assert prepared.status_code == 200
    assert prepared.json()["user_prompt"] == "synthetic user"
    assert prepared.json()["generation"] == 1
    saved = review_api.post("/reviews/msg-1/local-ai-suggestion", json={
        "generation": 1, "model": "gemma4:latest",
        "extraction": {"synthetic": "output"},
    })
    assert saved.status_code == 200
    assert saved.json() == {"saved": True, "review": None}


def test_local_ai_route_hides_other_owner_and_redacts_invalid_input(review_api):
    class MissingService:
        def prepare(self, owner_id, message_id):
            raise LocalAiNotFound

    app.dependency_overrides[get_local_review_ai_service] = MissingService
    app.dependency_overrides[get_current_subject] = lambda: "user-b"
    assert review_api.get("/reviews/msg-1/local-ai-input").status_code == 404

    bad = review_api.post("/reviews/msg-1/local-ai-suggestion", json={
        "generation": "private-email-fragment", "model": "gemma4:latest",
        "extraction": {},
    })
    assert bad.status_code == 422
    assert "private-email-fragment" not in bad.text
