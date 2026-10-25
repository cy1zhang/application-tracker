import json

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_application_repository,
    get_auto_apply_settings,
    get_current_subject,
    get_queue_email_repository,
    get_sync_publisher,
)
from app.main import app
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.plan_report import simulate
from app.services.sync_candidates import load_candidates
from tests.plan_support import put_plan
from tests.test_plan_apply import Publisher


class Env:
    pass


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("SYNC_QUEUE_URL", "queue-url")
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
        e.publisher = Publisher()
        e.settings.set_mode("user-a", "plan")
        e.owner = "user-a"
        app.dependency_overrides[get_current_subject] = lambda: e.owner
        app.dependency_overrides[get_queue_email_repository] = lambda: e.emails
        app.dependency_overrides[get_application_repository] = lambda: e.applications
        app.dependency_overrides[get_auto_apply_settings] = lambda: e.settings
        app.dependency_overrides[get_sync_publisher] = lambda: e.publisher
        with TestClient(app) as api:
            e.api = api
            yield e
        app.dependency_overrides.clear()


def queued(env, message_id, minutes=0):
    env.client.put_item(TableName="Events", Item={
        "owner_id": {"S": "user-a"}, "event_id": {"S": f"gmail#{message_id}"},
        "source_message_id": {"S": message_id}, "outcome": {"S": "pending_ai"},
        "reason": {"S": "awaiting_local_ai"},
    })


def test_report_counts_match_simulate(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    put_plan(env.client, "user-a", "m2", 5, status="interviewing")
    put_plan(env.client, "user-a", "m3", 6, kind="job_alert")
    queued(env, "waiting-1")
    expected = simulate(
        env.emails.list_planned("user-a"), load_candidates("user-a", env.applications)
    )

    body = env.api.get("/ai-plan/report").json()

    assert body["analyzed"] == 3
    assert body["waiting"] == 1
    assert body["report"]["counts"] == expected.counts
    assert body["report"]["reasons"] == expected.reasons
    assert body["report"]["counts"]["create"] == 1
    assert body["report"]["counts"]["update"] == 1


def test_report_sample_shows_planned_creates(env) -> None:
    put_plan(env.client, "user-a", "m1", 0, subject="Hello Acme", thread="t 1")

    line = env.api.get("/ai-plan/report").json()["report"]["sample"][0]

    assert line["action"] == "create"
    assert line["company"] == "Acme"
    assert line["status"] == "applied"
    assert line["target"] == "planned:m1"
    assert line["subject"] == "Hello Acme"
    assert line["sender"] == "jobs@acme.test"
    assert line["source_url"] == "https://mail.google.com/mail/u/0/#all/t%201"


def test_report_is_owner_scoped(env) -> None:
    put_plan(env.client, "user-b", "b1", 0)

    assert env.api.get("/ai-plan/report").json()["analyzed"] == 0


def test_apply_refuses_while_waiting(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    queued(env, "w1")

    response = env.api.post("/ai-plan/apply")

    assert response.status_code == 409
    assert response.json() == {"detail": "analysis_incomplete"}
    assert env.settings.get_mode("user-a") == "plan"
    assert env.publisher.sent == []


def test_apply_nothing_planned_409(env) -> None:
    response = env.api.post("/ai-plan/apply")

    assert response.status_code == 409
    assert response.json() == {"detail": "nothing_planned"}


def test_apply_when_off_409(env) -> None:
    env.settings.set_mode("user-a", "off")
    put_plan(env.client, "user-a", "m1", 0)

    response = env.api.post("/ai-plan/apply")

    assert response.status_code == 409
    assert response.json() == {"detail": "auto_apply_off"}
    assert env.settings.get_mode("user-a") == "off"
    assert env.publisher.sent == []


def test_apply_sets_mode_on_and_enqueues(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)

    response = env.api.post("/ai-plan/apply")

    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert env.settings.get_mode("user-a") == "on"
    assert env.publisher.sent[0]["QueueUrl"] == "queue-url"
    assert json.loads(env.publisher.sent[0]["MessageBody"]) == {
        "mode": "apply_plan", "owner_id": "user-a",
    }


def test_apply_when_already_on_is_idempotent(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    env.settings.set_mode("user-a", "on")

    assert env.api.post("/ai-plan/apply").status_code == 202
    assert env.api.post("/ai-plan/apply").status_code == 202
    assert env.settings.get_mode("user-a") == "on"


def test_apply_is_owner_scoped(env) -> None:
    put_plan(env.client, "user-b", "b1", 0)

    assert env.api.post("/ai-plan/apply").json() == {"detail": "nothing_planned"}


def test_apply_enqueue_failure_restores_mode(env) -> None:
    from botocore.exceptions import ClientError

    put_plan(env.client, "user-a", "m1", 0)

    def fail(**kwargs):
        raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "SendMessage")

    env.publisher.send_message = fail

    response = env.api.post("/ai-plan/apply")

    assert response.status_code == 503
    assert env.settings.get_mode("user-a") == "plan"


def test_apply_enqueue_failure_keeps_mode_switched_off_meanwhile(env) -> None:
    from botocore.exceptions import ClientError

    put_plan(env.client, "user-a", "m1", 0)

    def switch_off_then_fail(**kwargs):
        env.settings.set_mode("user-a", "off")
        raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "SendMessage")

    env.publisher.send_message = switch_off_then_fail

    assert env.api.post("/ai-plan/apply").status_code == 503
    assert env.settings.get_mode("user-a") == "off"


def test_apply_with_matching_expected_counts_queues(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    put_plan(env.client, "user-a", "m2", 5, status="interviewing")

    response = env.api.post(
        "/ai-plan/apply", json={"expected": {"analyzed": 2, "create": 1, "update": 1}}
    )

    assert response.status_code == 202
    assert env.settings.get_mode("user-a") == "on"
    assert len(env.publisher.sent) == 1


def test_apply_with_stale_expected_counts_409_plan_changed(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    put_plan(env.client, "user-a", "m2", 5, status="interviewing")

    response = env.api.post(
        "/ai-plan/apply", json={"expected": {"analyzed": 1, "create": 1, "update": 0}}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "plan_changed"}
    assert env.settings.get_mode("user-a") == "plan"
    assert env.publisher.sent == []


def test_apply_without_expected_keeps_old_behavior(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)

    assert env.api.post("/ai-plan/apply", json={}).status_code == 202
    assert env.api.post("/ai-plan/apply").status_code == 202


def test_apply_expected_rejects_extra_fields_422(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)

    response = env.api.post(
        "/ai-plan/apply",
        json={"expected": {"analyzed": 1, "create": 1, "update": 0, "secret": "x-private"}},
    )

    assert response.status_code == 422
    assert "x-private" not in response.text
    assert env.publisher.sent == []
