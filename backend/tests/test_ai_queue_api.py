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


class Publisher:
    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)


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
        e.publisher = Publisher()
        e.settings.set_mode("user-a", "plan")
        e.owner = "user-a"
        app.dependency_overrides[get_current_subject] = lambda: e.owner
        app.dependency_overrides[get_ai_queue_service] = lambda: AiQueueService(
            emails=e.emails, settings=e.settings, applications=e.applications,
            connections=connections, cipher=Cipher(), oauth=OAuth(), fetch=e.fetch,
            clock=lambda: NOW + timedelta(days=1),
            publisher=e.publisher, queue_url="q",
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


def test_on_mode_retry_executes_saved_unexecuted_plan(env, monkeypatch) -> None:
    env.settings.set_mode("user-a", "on")
    queue(env, "m1")
    real = env.emails.execute
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("synthetic crash")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.emails, "execute", flaky)
    assert post_result(env, "m1").status_code == 500
    assert "executed_at" not in item(env, "m1")["ai_plan"]
    assert env.api.get("/ai-queue").json()["remaining"] == 0

    retry = post_result(env, "m1")

    assert retry.status_code == 200
    assert retry.json() == {"planned": True, "executed": True, "action": "create"}
    assert item(env, "m1")["outcome"] == "applied"
    assert len(env.applications.list(owner_id="user-a", limit=10, cursor=None).items) == 1
    again = post_result(env, "m1")
    assert again.status_code == 409 and again.json() == {"detail": "review_changed"}


def test_response_reports_stored_outcome_when_application_went_stale(env, monkeypatch) -> None:
    from app.domain.applications import ApplicationCreate, ApplicationPatch

    env.settings.set_mode("user-a", "on")
    existing = env.applications.create(
        owner_id="user-a",
        payload=ApplicationCreate(
            company="Acme", role="Software Engineer Intern", status="applied"
        ),
        application_id="app-1", event_id="create-1", occurred_at=NOW - timedelta(days=30),
    )
    queue(env, "m1")
    real = env.emails.execute

    def bump_then_execute(*args, **kwargs):
        env.applications.update(
            owner_id="user-a", application_id="app-1", event_id="bump-1",
            patch=ApplicationPatch(expected_version=existing.version, year=2027),
            occurred_at=NOW,
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(env.emails, "execute", bump_then_execute)

    response = post_result(env, "m1", role="Software Engineer Intern")

    assert response.status_code == 200
    assert response.json() == {"planned": True, "executed": True, "action": "needs_look"}
    assert item(env, "m1")["outcome"] == "review_required"


def _unavailable(env):
    from app.services.gmail_fetch import MessageUnavailable

    def gone(access_token, message_id):
        raise MessageUnavailable

    env.fetch.get_message = gone


def test_unavailable_message_leaves_the_queue_on_input(env) -> None:
    env.client.put_item(TableName="Events", Item={
        "owner_id": {"S": "user-a"}, "event_id": {"S": "gmail#old"},
        "source_message_id": {"S": "old"}, "outcome": {"S": "ignored"},
        "reason": {"S": "no_event"}, "received_at": {"S": NOW.isoformat()},
        "ai_backfill": {"BOOL": True},
    })
    queue(env, "m1")
    _unavailable(env)

    for message_id in ("m1", "old"):
        response = env.api.get(f"/ai-queue/{message_id}/input")
        assert response.status_code == 503
        assert response.json() == {"detail": "message_unavailable"}
        stored = item(env, message_id)
        assert (stored["outcome"], stored["reason"]) == ("ignored", "message_unavailable")
        assert "ai_backfill" not in stored
    assert env.api.get("/ai-queue").json() == {"items": [], "remaining": 0}


def test_unavailable_message_leaves_the_queue_on_result(env) -> None:
    queue(env, "m1")
    _unavailable(env)

    response = post_result(env, "m1")

    assert response.status_code == 503
    assert response.json() == {"detail": "message_unavailable"}
    assert item(env, "m1")["reason"] == "message_unavailable"
    assert env.api.get("/ai-queue").json()["remaining"] == 0


def test_three_invalid_outputs_send_the_item_to_review(env) -> None:
    from app.repositories.dynamodb_reviews import DynamoReviewRepository

    queue(env, "m1")
    bad = {"generation": 1, "model": "m", "extraction": {"company": "x"}}

    for attempt in (1, 2):
        assert env.api.post("/ai-queue/m1/result", json=bad).status_code == 422
        assert item(env, "m1")["ai_attempts"] == attempt
        assert env.api.get("/ai-queue").json()["remaining"] == 1
    assert env.api.post("/ai-queue/m1/result", json=bad).status_code == 422

    stored = item(env, "m1")
    assert stored["outcome"] == "review_required"
    assert (stored["reason"], stored["rule_reason"]) == ("needs_look", "ai_invalid_output")
    assert "ai_backfill" not in stored and "ai_plan" not in stored
    assert env.api.get("/ai-queue").json() == {"items": [], "remaining": 0}
    reviews = DynamoReviewRepository(
        client=env.client, applications_table="Applications", events_table="Events"
    ).list_pending(owner_id="user-a", limit=10, cursor=None)
    assert [review.message_id for review in reviews.items] == ["m1"]


def test_result_defers_behind_an_older_backlog_and_worker_drains_in_order(env) -> None:
    import json

    from app.services.plan_apply import apply_plan_page
    from tests.plan_support import put_plan

    env.settings.set_mode("user-a", "on")
    put_plan(env.client, "user-a", "old", -60, status="applied")
    queue(env, "m1", received=NOW)

    response = post_result(env, "m1")

    assert response.status_code == 200
    assert response.json() == {
        "planned": True, "executed": False, "action": None, "deferred": True,
    }
    assert "ai_plan" in item(env, "m1") and "executed_at" not in item(env, "m1")["ai_plan"]
    assert env.applications.list(owner_id="user-a", limit=10, cursor=None).items == []
    assert [json.loads(m["MessageBody"]) for m in env.publisher.sent] == [
        {"mode": "apply_plan", "owner_id": "user-a"}
    ]

    assert apply_plan_page(
        "user-a", emails=env.emails, applications=env.applications,
        settings=env.settings, publisher=env.publisher, queue_url="q",
    ) is True

    assert item(env, "old")["application_id"] == item(env, "m1")["application_id"]
    assert item(env, "old")["reason"] == "ai_create"
    assert item(env, "m1")["outcome"] == "applied"


def test_result_mode_switched_off_before_write_is_409_and_unexecuted(env, monkeypatch) -> None:
    env.settings.set_mode("user-a", "on")
    queue(env, "m1")
    real = env.emails.execute

    def flip_then_execute(*args, **kwargs):
        env.settings.set_mode("user-a", "off")
        return real(*args, **kwargs)

    monkeypatch.setattr(env.emails, "execute", flip_then_execute)

    response = post_result(env, "m1")

    assert response.status_code == 409
    assert response.json() == {"detail": "auto_apply_off"}
    assert env.applications.list(owner_id="user-a", limit=10, cursor=None).items == []
    assert "executed_at" not in item(env, "m1")["ai_plan"]
