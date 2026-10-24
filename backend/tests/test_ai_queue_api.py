from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.ai_queue import get_ai_queue_service
from app.api.dependencies import get_current_subject
from app.main import app
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_email import DynamoEmailRepository, extraction_from_plan
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.ai_queue import AiQueueService
from app.services.email_processing import process_message

NOW = datetime(2026, 10, 18, tzinfo=UTC)
TOKEN = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}
BODY = "We invite you to interview for the Software Engineer Intern role at Acme."
FIELDS = ("company", "role", "employment_type", "season", "year", "status", "requisition_id")


def _gmail(message_id, text=BODY, *, received=NOW):
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(int(received.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Update"},
                {"name": "From", "value": "Recruiting <jobs@acme.test>"},
            ],
            "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        },
    }


def _output(**overrides):
    values = {field: None for field in FIELDS}
    evidence = {field: None for field in FIELDS}
    values.update(company="Acme", status="interviewing")
    evidence.update(company="role at Acme", status="invite you to interview")
    return {**values, **overrides, "kind": "application_update", "evidence": evidence}


class Cipher:
    def decrypt_refresh_token(self, owner_id, encrypted):
        return "refresh"


class OAuth:
    def refresh_access_token(self, token):
        return "access"


class Fetch:
    def __init__(self):
        self.messages = {}

    def get_message(self, access_token, message_id):
        return self.messages[message_id]


class Env:
    pass


@pytest.fixture
def env():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users",
        )
        e = Env()
        e.client = client
        e.settings = DynamoAutoApplySettings(client=client, users_table="Users")
        e.applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        e.emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events",
            plan_users_table="Users",
        )
        connections = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        connections.save_connection("user-a", 0, "a@example.test", TOKEN, NOW)
        e.connections = connections
        e.fetch = Fetch()
        e.settings.set_mode("user-a", "plan")
        e.owner = "user-a"
        app.dependency_overrides[get_current_subject] = lambda: e.owner
        app.dependency_overrides[get_ai_queue_service] = lambda: AiQueueService(
            emails=e.emails, settings=e.settings, applications=e.applications,
            connections=connections, cipher=Cipher(), oauth=OAuth(), fetch=e.fetch,
            clock=lambda: NOW + timedelta(days=1),
        )
        with TestClient(app) as api:
            e.api = api
            yield e
        app.dependency_overrides.clear()


def queue(env, message_id, text=BODY, *, received=NOW, owner="user-a"):
    gmail = _gmail(message_id, text, received=received)
    env.fetch.messages[message_id] = gmail
    return process_message(owner, gmail, [], env.emails, mode="plan")


def item(env, message_id, owner="user-a"):
    return env.emails.get_event_item(owner, message_id)


def post_result(env, message_id, generation=1, **kwargs):
    return env.api.post(
        f"/ai-queue/{message_id}/result",
        json={"generation": generation, "model": "gemma4:latest", "extraction": _output(**kwargs)},
    )


def test_backfill_marks_no_event_and_unresolved_reviews_only(env) -> None:
    def put(message_id, outcome, reason, **extra):
        env.client.put_item(TableName="Events", Item={
            "owner_id": {"S": "user-a"}, "event_id": {"S": f"gmail#{message_id}"},
            "source_message_id": {"S": message_id}, "outcome": {"S": outcome},
            "reason": {"S": reason}, "received_at": {"S": NOW.isoformat()}, **extra,
        })

    put("no-event", "ignored", "no_event")
    put("other-ignored", "ignored", "filtered:digest")
    put("review", "review_required", "ambiguous_application")
    put("resolved", "review_required", "ambiguous_application",
        resolution={"M": {"action": {"S": "reject"}}})
    put("unparseable", "review_required", "unparseable_message")
    put("planned", "review_required", "unknown_stage", ai_plan={"M": {}})
    put("applied", "applied", "stage_event")

    response = env.api.post("/ai-queue/backfill")

    assert response.status_code == 200
    assert response.json() == {"marked": 2}
    marked = {
        name for name in ("no-event", "other-ignored", "review", "resolved",
                          "unparseable", "planned", "applied")
        if item(env, name).get("ai_backfill") is True
    }
    assert marked == {"no-event", "review"}


def test_backfill_second_call_marks_zero(env) -> None:
    env.client.put_item(TableName="Events", Item={
        "owner_id": {"S": "user-a"}, "event_id": {"S": "gmail#m1"},
        "source_message_id": {"S": "m1"}, "outcome": {"S": "ignored"},
        "reason": {"S": "no_event"},
    })

    assert env.api.post("/ai-queue/backfill").json() == {"marked": 1}
    assert env.api.post("/ai-queue/backfill").json() == {"marked": 0}


def test_queue_lists_oldest_first_without_plans(env) -> None:
    queue(env, "new", received=NOW)
    queue(env, "old", received=NOW - timedelta(days=3))
    queue(env, "mid", received=NOW - timedelta(days=1))
    queue(env, "planned", received=NOW - timedelta(days=5))
    assert post_result(env, "planned").status_code == 200

    page = env.api.get("/ai-queue?limit=2").json()

    assert page == {
        "items": [
            {"message_id": "old", "received_at": (NOW - timedelta(days=3)).isoformat()},
            {"message_id": "mid", "received_at": (NOW - timedelta(days=1)).isoformat()},
        ],
        "remaining": 3,
    }


def test_result_saves_plan_in_plan_mode_without_executing(env) -> None:
    queue(env, "m1")
    generation = env.api.get("/ai-queue/m1/input").json()["generation"]

    response = post_result(env, "m1", generation)

    assert response.status_code == 200
    assert response.json() == {"planned": True, "executed": False, "action": None}
    stored = item(env, "m1")
    assert stored["outcome"] == "pending_ai"
    plan = stored["ai_plan"]
    assert plan["model"] == "gemma4:latest" and plan["generation"] == generation
    assert plan["planned_at"] == (NOW + timedelta(days=1)).isoformat()
    restored = extraction_from_plan(plan)
    assert restored.company == "Acme" and restored.status.value == "interviewing"
    assert restored.kind.value == "application_update"
    assert restored.evidence["company"] == "role at Acme"
    assert env.api.get("/ai-queue").json() == {"items": [], "remaining": 0}
    assert env.applications.list(owner_id="user-a", limit=10, cursor=None).items == []
    assert post_result(env, "m1", generation).status_code == 409
    assert post_result(env, "m1", generation).json() == {"detail": "review_changed"}


def test_result_executes_in_on_mode(env) -> None:
    env.settings.set_mode("user-a", "on")
    queue(env, "m1")

    response = post_result(env, "m1")

    assert response.json() == {"planned": True, "executed": True, "action": "create"}
    stored = item(env, "m1")
    assert stored["outcome"] == "applied"
    assert stored["ai_plan"]["executed_at"]
    created = env.applications.list(owner_id="user-a", limit=10, cursor=None).items
    assert [(a.company, a.status.value) for a in created] == [("Acme", "interviewing")]


def test_queue_routes_409_when_off(env) -> None:
    env.settings.set_mode("user-a", "off")
    queue(env, "m1")

    responses = [
        env.api.post("/ai-queue/backfill"),
        env.api.get("/ai-queue"),
        env.api.get("/ai-queue/m1/input"),
        post_result(env, "m1"),
    ]

    for response in responses:
        assert response.status_code == 409
        assert response.json() == {"detail": "auto_apply_off"}
    assert "ai_plan" not in item(env, "m1")


def test_queue_routes_owner_scoped(env) -> None:
    queue(env, "m1")
    env.settings.set_mode("user-b", "on")
    env.owner = "user-b"

    assert env.api.get("/ai-queue").json() == {"items": [], "remaining": 0}
    assert env.api.get("/ai-queue/m1/input").status_code == 404
    assert post_result(env, "m1").status_code == 404
    assert env.api.post("/ai-queue/backfill").json() == {"marked": 0}
    assert "ai_plan" not in item(env, "m1")
    assert env.applications.list(owner_id="user-b", limit=10, cursor=None).items == []


def test_result_rejects_generation_change(env) -> None:
    queue(env, "m1")

    response = post_result(env, "m1", generation=7)

    assert response.status_code == 409
    assert response.json() == {"detail": "gmail_connection_changed"}
    assert "ai_plan" not in item(env, "m1")


def test_result_rejects_generation_change_after_fetch(env) -> None:
    """The conditional write, not only the pre-check, refuses a stale generation."""
    from app.repositories.dynamodb_reviews import GmailConnectionChanged

    queue(env, "m1")
    extraction = extraction_from_plan({"evidence": {}, "status_in_subject_only": False})

    with pytest.raises(GmailConnectionChanged):
        env.emails.save_plan(
            "user-a", "m1", extraction=extraction, model="m", generation=9, planned_at=NOW
        )
    assert "ai_plan" not in item(env, "m1")


def test_input_404_for_message_not_in_queue(env) -> None:
    env.fetch.messages["m1"] = _gmail("m1")
    process_message("user-a", _gmail("m1", "Acme invites you to interview."), [], env.emails)
    queue(env, "planned")
    assert post_result(env, "planned").status_code == 200

    assert env.api.get("/ai-queue/missing/input").status_code == 404
    assert env.api.get("/ai-queue/m1/input").status_code == 404
    assert env.api.get("/ai-queue/planned/input").status_code == 404


def test_input_returns_prompt_for_queued_message(env) -> None:
    queue(env, "m1")

    body = env.api.get("/ai-queue/m1/input").json()

    assert set(body) == {"system_prompt", "user_prompt", "format", "generation"}
    assert "role at Acme" in body["user_prompt"]
    assert body["generation"] == 1


def test_invalid_result_is_422_without_echoing_output(env) -> None:
    queue(env, "m1")

    response = env.api.post("/ai-queue/m1/result", json={
        "generation": 1, "model": "gemma4:latest",
        "extraction": {"company": "private-email-fragment"},
    })
    bad = env.api.post("/ai-queue/m1/result", json={
        "generation": "private-email-fragment", "model": "x", "extraction": {},
    })

    assert response.status_code == 422 and bad.status_code == 422
    assert "private-email-fragment" not in response.text + bad.text
    assert "ai_plan" not in item(env, "m1")
