import json
import time

import boto3
import pytest
from moto import mock_aws

from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.plan_apply import apply_plan_page
from tests.plan_support import T0, put_plan


class Publisher:
    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)


class Env:
    def apply(self, owner="user-a", **kwargs):
        return apply_plan_page(
            owner, emails=self.emails, applications=self.applications,
            settings=self.settings, publisher=self.publisher, queue_url="q", **kwargs,
        )

    def apps(self, owner="user-a"):
        return self.applications.list(owner_id=owner, limit=100, cursor=None).items

    def item(self, message_id, owner="user-a"):
        return self.emails.get_event_item(owner, message_id)


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
        e.publisher = Publisher()
        e.settings.set_mode("user-a", "on")
        e.settings.set_mode("user-b", "on")
        yield e


def test_apply_page_executes_in_received_order_and_reuses_new_rows(env) -> None:
    # Inserted newest first; the later email must update the row the earlier one made.
    put_plan(env.client, "user-a", "m2", 5, status="interviewing")
    put_plan(env.client, "user-a", "m1", 0, status="applied")

    assert env.apply() is True

    rows = env.apps()
    assert len(rows) == 1
    assert rows[0].status.value == "interviewing"
    assert env.item("m1")["application_id"] == env.item("m2")["application_id"]
    assert "executed_at" in env.item("m1")["ai_plan"]
    assert env.publisher.sent == []


def test_apply_page_retry_is_idempotent(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    put_plan(env.client, "user-a", "m2", 5, status="interviewing")
    env.apply()
    before = (env.apps()[0].version, env.item("m1"), env.item("m2"))

    assert env.apply() is True

    assert len(env.apps()) == 1
    assert (env.apps()[0].version, env.item("m1"), env.item("m2")) == before


def test_apply_owner_scoped(env) -> None:
    put_plan(env.client, "user-a", "a1", 0)
    put_plan(env.client, "user-b", "b1", 0, company="Globex")

    env.apply("user-a")

    assert [row.company for row in env.apps("user-a")] == ["Acme"]
    assert env.apps("user-b") == []
    assert "executed_at" not in env.item("b1", "user-b")["ai_plan"]


def test_apply_page_reenqueues_while_plans_remain(env) -> None:
    for index in range(3):
        put_plan(env.client, "user-a", f"m{index}", index, company=f"Co{index}")

    assert env.apply(page_size=2) is False

    assert [json.loads(m["MessageBody"]) for m in env.publisher.sent] == [
        {"mode": "apply_plan", "owner_id": "user-a"}
    ]
    assert env.publisher.sent[0]["QueueUrl"] == "q"
    assert env.apply(page_size=2) is True
    assert len(env.apps()) == 3


def test_apply_page_stops_when_mode_switched_off(env) -> None:
    put_plan(env.client, "user-a", "m1", 0)
    env.settings.set_mode("user-a", "off")

    assert env.apply() is True

    assert env.apps() == []
    assert env.publisher.sent == []
    assert "executed_at" not in env.item("m1")["ai_plan"]


def test_apply_page_mode_switched_off_mid_page_does_not_reenqueue(env) -> None:
    for index in range(3):
        put_plan(env.client, "user-a", f"m{index}", index, company=f"Co{index}")
    real = env.settings.get_mode
    calls = []

    def flip(owner):
        calls.append(owner)
        if len(calls) > 1:
            env.settings.set_mode(owner, "off")
        return real(owner)

    env.settings.get_mode = flip

    assert env.apply(page_size=2) is True

    assert len(env.apps()) == 1
    assert env.publisher.sent == []


def test_invalid_create_goes_to_review_and_does_not_loop(env, monkeypatch) -> None:
    from app.services import plan_apply
    from app.services.auto_apply import AutoDecision, decide

    def forced(extraction, *, message_ref, candidates):
        if message_ref.message_id == "bad":
            return AutoDecision("create", "forced", None, {"company": "Acme"})
        return decide(extraction, message_ref=message_ref, candidates=candidates)

    monkeypatch.setattr(plan_apply, "decide", forced)
    put_plan(env.client, "user-a", "bad", 0)
    put_plan(env.client, "user-a", "good", 1, company="Globex")

    assert env.apply() is True

    assert [row.company for row in env.apps()] == ["Globex"]
    assert env.item("bad")["outcome"] == "review_required"
    assert "executed_at" in env.item("bad")["ai_plan"]


def test_list_planned_skips_executed_resolved_and_undone(env) -> None:
    put_plan(env.client, "user-a", "ok", 0)
    put_plan(env.client, "user-a", "done", 1, plan_extra={"executed_at": T0.isoformat()})
    put_plan(env.client, "user-a", "resolved", 2, extra={"resolution": {"action": "reject"}})
    put_plan(env.client, "user-a", "undone", 3, extra={"undone_at": T0.isoformat()})
    put_plan(env.client, "user-b", "other", 4)

    planned = env.emails.list_planned("user-a")

    assert [p.message_id for p in planned] == ["ok"]
    assert planned[0].received_at == T0 and planned[0].subject == "Update"
    assert planned[0].thread_id == "thread-ok"


def test_report_performance_1200_plans(env) -> None:
    from app.services.plan_report import simulate
    from app.services.sync_candidates import load_candidates

    for index in range(1200):
        put_plan(
            env.client, "user-a", f"m{index}", index, company=f"Co{index % 300}",
            status="applied" if index < 300 else "interviewing",
        )
    planned = env.emails.list_planned("user-a")

    started = time.monotonic()
    report = simulate(planned, load_candidates("user-a", env.applications))
    elapsed = time.monotonic() - started

    print(f"SIMULATE_SECONDS={elapsed:.3f}")
    assert report.counts["create"] == 300
    assert elapsed < 2
