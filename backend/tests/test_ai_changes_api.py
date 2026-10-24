import boto3
import pytest
from boto3.dynamodb.types import TypeSerializer
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_application_repository,
    get_current_subject,
    get_queue_email_repository,
)
from app.domain.applications import ApplicationPatch, ApplicationStatus
from app.main import app
from app.repositories.applications import VersionConflict
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_email import DynamoEmailRepository, extraction_from_plan
from app.repositories.dynamodb_tables import create_tables
from app.services.auto_apply import MessageRef, decide
from app.services.plan_apply import apply_plan_page
from tests.plan_support import T0, put_plan
from tests.test_plan_apply import Publisher

NOW = T0.replace(year=2026, month=11)


class Env:
    def apply(self):
        return apply_plan_page(
            "user-a", emails=self.emails, applications=self.applications,
            settings=self.settings, publisher=Publisher(), queue_url="q",
        )

    def apps(self, owner="user-a"):
        return self.applications.list(owner_id=owner, limit=100, cursor=None).items

    def item(self, message_id, owner="user-a"):
        return self.emails.get_event_item(owner, message_id)

    def raw(self, owner, message_id, minutes, reason, outcome="ignored", **extra):
        item = {
            "owner_id": owner, "event_id": f"gmail#{message_id}",
            "source_message_id": message_id, "thread_id": f"thread-{message_id}",
            "received_at": T0.replace(minute=minutes).isoformat(),
            "outcome": outcome, "reason": reason, "subject": "Hi",
            "sender": "jobs@acme.test", **extra,
        }
        serializer = TypeSerializer()
        self.client.put_item(
            TableName="Events", Item={k: serializer.serialize(v) for k, v in item.items()}
        )


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
        e.settings.set_mode("user-a", "on")
        e.owner = "user-a"
        app.dependency_overrides[get_current_subject] = lambda: e.owner
        app.dependency_overrides[get_queue_email_repository] = lambda: e.emails
        app.dependency_overrides[get_application_repository] = lambda: e.applications
        with TestClient(app) as api:
            e.api = api
            yield e
        app.dependency_overrides.clear()


def created_and_updated(env):
    put_plan(env.client, "user-a", "m1", 0, status="applied")
    put_plan(env.client, "user-a", "m2", 5, status="interviewing")
    env.apply()
    return env.apps()[0]


def test_changes_lists_creates_updates_and_ignores_newest_first(env) -> None:
    created_and_updated(env)
    env.raw("user-a", "f1", 10, "filtered:newsletter")
    env.raw("user-a", "n1", 11, "no_status_no_match")
    env.raw("user-a", "x1", 12, "no_event")  # not an AI change
    env.raw("user-a", "p1", 13, "awaiting_local_ai", outcome="pending_ai")

    body = env.api.get("/ai-changes").json()

    assert [i["message_id"] for i in body["items"]] == ["n1", "f1", "m2", "m1"]
    assert [i["action"] for i in body["items"]] == [
        "ignored", "ignored", "updated", "created"
    ]
    updated = body["items"][2]
    assert updated["reason"] == "ai_update"
    assert updated["changes"]["status"] == {"before": "applied", "after": "interviewing"}
    assert updated["source_url"] == "https://mail.google.com/mail/u/0/#all/thread-m2"
    assert updated["undone_at"] is None
    assert updated["application_id"] == env.apps()[0].application_id

    page = env.api.get("/ai-changes?limit=3").json()
    assert [i["message_id"] for i in page["items"]] == ["n1", "f1", "m2"]
    rest = env.api.get(f"/ai-changes?limit=3&cursor={page['next_cursor']}").json()
    assert [i["message_id"] for i in rest["items"]] == ["m1"]
    assert rest["next_cursor"] is None
    assert env.api.get("/ai-changes?cursor=bad").status_code == 422


def test_undo_update_restores_before_values(env) -> None:
    row = created_and_updated(env)

    response = env.api.post("/ai-changes/m2/undo")

    assert response.status_code == 200
    restored = env.apps()[0]
    assert restored.status == ApplicationStatus.APPLIED
    assert restored.version == row.version + 1
    assert env.item("m2")["undone_at"]
    events = env.applications.list_events(
        owner_id="user-a", application_id=row.application_id
    )
    assert events[-1].origin.value == "user"
    assert events[-1].event_type.value == "updated"
    assert env.api.get("/ai-changes").json()["items"][0]["undone_at"] is not None


def test_undo_update_409_when_cell_changed_since(env) -> None:
    row = created_and_updated(env)
    env.applications.update(
        owner_id="user-a", application_id=row.application_id,
        patch=ApplicationPatch(expected_version=row.version, status="offer"),
        event_id="e1", occurred_at=NOW,
    )

    response = env.api.post("/ai-changes/m2/undo")

    assert response.status_code == 409
    assert response.json() == {"detail": "changed_since"}
    assert env.apps()[0].status == ApplicationStatus.OFFER
    assert "undone_at" not in env.item("m2")


def test_undo_create_soft_deletes_and_blocks_recreate_on_replay(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    env.apply()
    assert len(env.apps()) == 1

    assert env.api.post("/ai-changes/m1/undo").status_code == 200

    assert env.apps() == []
    assert env.item("m1")["undone_at"]
    env.apply()
    ref = MessageRef("m1", "thread-m1", T0)
    extraction = extraction_from_plan(env.item("m1")["ai_plan"])
    stored = env.emails.execute(
        "user-a", message=ref, subject="Update", sender="s",
        decision=decide(extraction, message_ref=ref, candidates=[]),
        extraction=extraction, application_version=None,
        new_application_id="again", occurred_at=NOW,
    )
    assert stored.outcome == "applied"
    assert env.apps() == []


def test_undo_create_409_after_later_edit(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    env.apply()
    row = env.apps()[0]
    env.applications.update(
        owner_id="user-a", application_id=row.application_id,
        patch=ApplicationPatch(expected_version=1, role="Other"),
        event_id="e1", occurred_at=NOW,
    )

    response = env.api.post("/ai-changes/m1/undo")

    assert response.status_code == 409
    assert response.json() == {"detail": "changed_since"}
    assert len(env.apps()) == 1
    assert "undone_at" not in env.item("m1")


def test_undo_twice_409_already_undone(env) -> None:
    created_and_updated(env)
    assert env.api.post("/ai-changes/m2/undo").status_code == 200

    response = env.api.post("/ai-changes/m2/undo")

    assert response.status_code == 409
    assert response.json() == {"detail": "already_undone"}


def test_undo_double_click_patches_once(env) -> None:
    row = created_and_updated(env)

    first = env.api.post("/ai-changes/m2/undo")
    second = env.api.post("/ai-changes/m2/undo")

    assert (first.status_code, second.status_code) == (200, 409)
    assert env.apps()[0].version == row.version + 1
    updates = [
        e for e in env.applications.list_events(
            owner_id="user-a", application_id=row.application_id
        ) if e.origin.value == "user"
    ]
    assert len(updates) == 1


def test_undo_releases_claim_when_write_loses_a_race(env, monkeypatch) -> None:
    created_and_updated(env)

    def conflict(**kwargs):
        raise VersionConflict

    monkeypatch.setattr(env.applications, "update", conflict)

    response = env.api.post("/ai-changes/m2/undo")

    assert response.status_code == 409
    assert response.json() == {"detail": "changed_since"}
    assert "undone_at" not in env.item("m2")


def test_undo_ignored_409_not_undoable(env) -> None:
    env.raw("user-a", "f1", 1, "filtered:newsletter")

    response = env.api.post("/ai-changes/f1/undo")

    assert response.status_code == 409
    assert response.json() == {"detail": "not_undoable"}
    assert "undone_at" not in env.item("f1")


def test_reconsider_ignored_with_plan_creates_row(env) -> None:
    put_plan(env.client, "user-a", "m1", 0, kind="job_alert")
    env.apply()
    assert env.item("m1")["reason"] == "not_application:job_alert"
    assert env.apps() == []

    response = env.api.post("/ai-changes/m1/reconsider")

    assert response.status_code == 200
    assert response.json() == {"action": "created"}
    assert len(env.apps()) == 1
    item = env.item("m1")
    assert (item["outcome"], item["reason"], item["origin"]) == (
        "applied", "ai_create", "ai"
    )
    assert item["application_id"] == env.apps()[0].application_id
    # It is now an ordinary AI change the owner can undo.
    assert env.api.post("/ai-changes/m1/undo").status_code == 200
    assert env.apps() == []


def test_reconsider_filtered_queues(env) -> None:
    env.raw("user-a", "f1", 1, "filtered:newsletter")

    response = env.api.post("/ai-changes/f1/reconsider")

    assert response.status_code == 202
    assert response.json() == {"queued": True}
    assert env.item("f1")["ai_backfill"] is True
    assert [i["event_id"] for i in env.emails.list_ai_queue("user-a")] == ["gmail#f1"]


def test_reconsider_non_ignored_409(env) -> None:
    created_and_updated(env)
    env.raw("user-a", "r1", 20, "needs_look", outcome="review_required")

    for message_id in ("m1", "r1"):
        response = env.api.post(f"/ai-changes/{message_id}/reconsider")
        assert response.status_code == 409
        assert response.json() == {"detail": "not_reconsiderable"}
    assert env.api.post("/ai-changes/nope/reconsider").status_code == 404


def test_ai_changes_owner_scoped(env) -> None:
    put_plan(env.client, "user-b", "b1", 0)
    env.settings.set_mode("user-b", "on")
    apply_plan_page(
        "user-b", emails=env.emails, applications=env.applications,
        settings=env.settings, publisher=Publisher(), queue_url="q",
    )
    env.raw("user-b", "bf", 1, "filtered:newsletter")

    assert env.api.get("/ai-changes").json()["items"] == []
    assert env.api.post("/ai-changes/b1/undo").status_code == 404
    assert env.api.post("/ai-changes/bf/reconsider").status_code == 404
    assert len(env.apps("user-b")) == 1
    assert "undone_at" not in env.item("b1", "user-b")
