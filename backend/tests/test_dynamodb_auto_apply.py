from datetime import timedelta

import pytest
from moto import mock_aws

from app.domain.ai import ApplicationKind
from app.domain.applications import (
    ApplicationPatch,
    ApplicationStatus,
    RoleType,
    Season,
)
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.auto_apply import AutoDecision, MessageRef
from tests.ai_support import NOW, Env
from tests.test_auto_apply import extraction as make_extraction

OWNER = "owner-a"


@pytest.fixture
def env():
    with mock_aws():
        e = Env()
        e.connect(OWNER, ai=False)
        yield e


def ref(message_id="m1", received_at=NOW):
    return MessageRef(message_id, "thread-1", received_at)


def run(env, decision, *, message_id="m1", received_at=NOW, version=None,
        new_id=None, owner=OWNER, extraction=None, emails=None,
        **kwargs):
    return (emails or env.emails(owner)).execute(
        owner, message=ref(message_id, received_at), subject="Subject " + "s" * 400,
        sender="jobs@acme.test", decision=decision,
        extraction=extraction or make_extraction(), application_version=version,
        new_application_id=new_id, occurred_at=received_at, **kwargs,
    )


def create_decision(**kw):
    return AutoDecision(
        "create", "ai_create", None,
        {"company": "Acme", "role": "SWE Intern", "role_type": RoleType.INTERNSHIP,
         "season": Season.SUMMER, "year": 2027, "status": ApplicationStatus.APPLIED},
        **kw,
    )


def update_decision(status=ApplicationStatus.INTERVIEWING, app_id="app-1"):
    return AutoDecision("update", "ai_update", app_id, {"status": status}, "REQ-9")


def raw_app(env, app_id, owner=OWNER):
    return env.applications._client.get_item(
        TableName="Applications", ConsistentRead=True,
        Key={"owner_id": {"S": owner}, "application_id": {"S": app_id}},
    ).get("Item")


def seed_plan(env, message_id="m1", owner=OWNER, **extra):
    """Make the stored item a planned review item (Task 7 will write ai_plan)."""
    env.client.put_item(TableName="Events", Item={
        "owner_id": {"S": owner}, "event_id": {"S": f"gmail#{message_id}"},
        "source_message_id": {"S": message_id}, "outcome": {"S": "review_required"},
        "reason": {"S": "planned"},
        "ai_plan": {"M": {"action": {"S": "create"}}},
        **{k: {"S": v} for k, v in extra.items()},
    })


def test_create_writes_application_and_created_event_atomically(env):
    outcome = run(env, create_decision(), new_id="new-1")
    assert (outcome.outcome, outcome.reason, outcome.application_id) == (
        "applied", "ai_create", "new-1"
    )
    created = env.applications.get(owner_id=OWNER, application_id="new-1")
    assert created.version == 1
    assert created.status == ApplicationStatus.APPLIED
    assert created.application_date is None
    assert created.created_at == created.updated_at == NOW
    stored = raw_app(env, "new-1")
    assert stored["field_updated_at"]["M"]["status"]["S"] == NOW.isoformat()
    event = env.event("m1")
    assert event["event_type"] == "created" and event["origin"] == "ai"
    assert event["outcome"] == "applied" and event["reason"] == "ai_create"
    assert event["owner_application_id"] == f"{OWNER}#new-1"
    assert event["occurred_at_event_id"] == f"{NOW.isoformat()}#gmail#m1"
    assert event["changes"]["company"] == {"before": None, "after": "Acme"}
    assert event["changes"]["status"]["after"] == "applied"
    assert event["ai_proposal"]["kind"] == "application_update"
    assert len(event["subject"]) == 300 and event["sender"] == "jobs@acme.test"
    history = env.applications.list_events(owner_id=OWNER, application_id="new-1")
    assert [e.source_message_id for e in history] == ["m1"]


@pytest.mark.parametrize("fields,kind", [
    ({"company": "Acme"}, None),
    ({"role": "SWE", "status": ApplicationStatus.APPLIED}, None),
    ({"company": "Acme", "status": ApplicationStatus.APPLIED},
     ApplicationKind.JOB_ALERT),
])
def test_create_without_status_or_company_is_refused(env, fields, kind):
    extraction = make_extraction(kind=kind) if kind else make_extraction()
    decision = AutoDecision("create", "ai_create", None, fields)
    with pytest.raises(ValueError, match="invalid create decision"):
        run(env, decision, new_id="new-1", extraction=extraction)
    assert raw_app(env, "new-1") is None
    assert env.event("m1") is None


def test_create_records_requisition_for_later_matching(env):
    run(env, create_decision(requisition_id="REQ-7"), new_id="new-1")
    assert env.event("m1")["requisition_id"] == "REQ-7"
    events = env.applications.list_events(owner_id=OWNER, application_id="new-1")
    assert events[0].requisition_id == "REQ-7"
    assert events[0].thread_id == "thread-1"


def test_create_replay_does_not_duplicate(env):
    first = run(env, create_decision(), new_id="new-1")
    second = run(env, create_decision(), new_id="new-2")
    assert second == first
    assert raw_app(env, "new-2") is None
    assert len(env.applications.list(owner_id=OWNER, limit=10, cursor=None).items) == 1


def test_update_applies_and_records_event(env):
    env.app()
    outcome = run(env, update_decision(), version=1)
    assert (outcome.outcome, outcome.reason) == ("applied", "ai_update")
    updated = env.applications.get(owner_id=OWNER, application_id="app-1")
    assert updated.status == ApplicationStatus.INTERVIEWING and updated.version == 2
    event = env.event("m1")
    assert event["event_type"] == "updated" and event["requisition_id"] == "REQ-9"
    assert event["changes"]["status"] == {"before": "applied", "after": "interviewing"}


def test_update_respects_manual_correction(env):
    env.app()
    env.applications.update(
        owner_id=OWNER, application_id="app-1",
        patch=ApplicationPatch(
            expected_version=1, status=ApplicationStatus.INTERVIEWING
        ),
        event_id="manual-1", occurred_at=NOW + timedelta(days=1),
    )
    outcome = run(
        env, update_decision(ApplicationStatus.REJECTED), version=2, received_at=NOW,
    )
    assert (outcome.outcome, outcome.reason) == ("review_required", "older_evidence")
    assert env.event("m1")["ai_proposal"]["kind"] == "application_update"
    stored = env.applications.get(owner_id=OWNER, application_id="app-1")
    assert stored.status == ApplicationStatus.INTERVIEWING and stored.version == 2


def test_update_stale_version_goes_to_review(env):
    env.app()
    outcome = run(env, update_decision(), version=7)
    assert (outcome.outcome, outcome.reason) == ("review_required", "ai_proposal")


def test_ignore_and_needs_look_leave_application_untouched(env):
    env.app()
    ignored = run(env, AutoDecision("ignore", "no_change", "app-1", {}), message_id="i")
    assert (ignored.outcome, ignored.reason) == ("ignored", "no_change")
    look = run(env, AutoDecision("needs_look", "ambiguous_match", None, {}),
               message_id="n")
    assert (look.outcome, look.reason) == ("review_required", "needs_look")
    event = env.event("n")
    assert event["rule_reason"] == "ambiguous_match"
    assert event["ai_proposal"]["kind"] == "application_update"
    assert len(event["subject"]) == 300 and event["sender"] == "jobs@acme.test"
    assert env.event("i")["subject"].startswith("Subject")
    assert env.applications.get(owner_id=OWNER, application_id="app-1").version == 1


def test_plan_changed_since_read_is_not_executed_on_create(env, monkeypatch):
    seed_plan(env)
    env.client.update_item(
        TableName="Events",
        Key={"owner_id": {"S": OWNER}, "event_id": {"S": "gmail#m1"}},
        UpdateExpression="SET ai_plan.planned_at = :p",
        ExpressionAttributeValues={":p": {"S": "t1"}},
    )
    emails = env.emails()
    original = emails._get_event_item

    def read_then_replan(owner_id, message_id):
        item = original(owner_id, message_id)
        env.client.update_item(
            TableName="Events",
            Key={"owner_id": {"S": owner_id}, "event_id": {"S": f"gmail#{message_id}"}},
            UpdateExpression="SET ai_plan.planned_at = :p",
            ExpressionAttributeValues={":p": {"S": "t2"}},
        )
        return item

    monkeypatch.setattr(emails, "_get_event_item", read_then_replan)

    outcome = run(env, create_decision(), new_id="new-1", emails=emails)

    assert outcome.outcome == "review_required" and outcome.reason == "planned"
    assert raw_app(env, "new-1") is None
    assert env.event("m1")["ai_plan"] == {"action": "create", "planned_at": "t2"}


def test_execute_overwrites_planned_review_item_but_not_resolved_one(env):
    seed_plan(env)
    seed_plan(env, "m2", resolution="dismissed")
    done = run(env, create_decision(), new_id="new-1")
    assert done.outcome == "applied"
    assert "executed_at" in env.event("m1")["ai_plan"]
    assert env.event("m1")["ai_plan"]["action"] == "create"
    kept = run(env, create_decision(), message_id="m2", new_id="new-2")
    assert kept.outcome == "review_required"
    assert raw_app(env, "new-2") is None
    assert env.event("m2")["reason"] == "planned"


def test_execute_does_not_run_a_plan_twice(env):
    seed_plan(env)
    first = run(env, create_decision(), new_id="new-1")
    again = run(env, create_decision(), new_id="new-2")
    assert again == first and raw_app(env, "new-2") is None


def test_execute_never_touches_undone_item(env):
    seed_plan(env, undone_at=NOW.isoformat())
    outcome = run(env, create_decision(), new_id="new-1")
    assert outcome.outcome == "review_required"
    assert raw_app(env, "new-1") is None
    assert "undone_at" in env.event("m1")


def test_guard_failure_raises_stale(env):
    emails = env.emails()
    env.client.update_item(
        TableName="Users", Key={"owner_id": {"S": OWNER}},
        UpdateExpression="SET active_run_id = :r",
        ExpressionAttributeValues={":r": {"S": "other-run"}},
    )
    with pytest.raises(StaleSyncRun):
        run(env, create_decision(), new_id="new-1", emails=emails)
    assert raw_app(env, "new-1") is None
    assert env.event("m1") is None


def test_cross_owner_application_id_rejected(env):
    env.connect("owner-b", ai=False)
    env.app(owner="owner-b", application_id="b-app")
    outcome = run(env, update_decision(app_id="b-app"), version=1)
    assert (outcome.outcome, outcome.reason) == ("review_required", "missing_application")
    other = env.applications.get(owner_id="owner-b", application_id="b-app")
    assert other.version == 1 and other.status == ApplicationStatus.APPLIED
    assert env.event("m1", owner="owner-b") is None


def test_every_successful_execute_records_executed_at(env):
    env.app()
    run(env, create_decision(), message_id="c", new_id="new-1")
    run(env, update_decision(), message_id="u", version=1)
    run(env, AutoDecision("ignore", "no_change", None, {}), message_id="i")
    run(env, AutoDecision("needs_look", "ambiguous_match", None, {}), message_id="n")
    for message_id in "cuin":
        assert env.event(message_id)["ai_plan"] == {"executed_at": NOW.isoformat()}


def test_plan_changed_since_read_is_not_executed(env, monkeypatch):
    seed_plan(env)
    env.client.update_item(
        TableName="Events",
        Key={"owner_id": {"S": OWNER}, "event_id": {"S": "gmail#m1"}},
        UpdateExpression="SET ai_plan.planned_at = :p",
        ExpressionAttributeValues={":p": {"S": "t1"}},
    )
    emails = env.emails()
    original = emails._get_event_item

    def read_then_replan(owner_id, message_id):
        item = original(owner_id, message_id)
        env.client.update_item(
            TableName="Events",
            Key={"owner_id": {"S": owner_id}, "event_id": {"S": f"gmail#{message_id}"}},
            UpdateExpression="SET ai_plan.planned_at = :p",
            ExpressionAttributeValues={":p": {"S": "t2"}},
        )
        return item

    monkeypatch.setattr(emails, "_get_event_item", read_then_replan)
    # An update and a create both leave the new plan alone.
    env.app()
    outcome = run(env, update_decision(), version=1, emails=emails)
    assert outcome.outcome == "review_required" and outcome.reason == "planned"
    stored = env.event("m1")
    assert stored["ai_plan"] == {"action": "create", "planned_at": "t2"}
    assert env.applications.get(owner_id=OWNER, application_id="app-1").version == 1


def set_mode(env, mode, owner=OWNER):
    env.client.update_item(
        TableName="Users", Key={"owner_id": {"S": owner}},
        UpdateExpression="SET auto_apply_mode = :m",
        ExpressionAttributeValues={":m": {"S": mode}},
    )


@pytest.mark.parametrize("mode", ["off", "plan"])
def test_require_mode_on_writes_nothing_unless_mode_is_on(env, mode):
    from app.repositories.dynamodb_email import AutoApplyOff

    env.app()
    set_mode(env, mode)
    with pytest.raises(AutoApplyOff):
        run(env, create_decision(), message_id="c", new_id="new-1",
            require_mode_on=True)
    with pytest.raises(AutoApplyOff):
        run(env, update_decision(), message_id="u", version=1, require_mode_on=True)
    with pytest.raises(AutoApplyOff):
        run(env, AutoDecision("ignore", "no_change", None, {}), message_id="i",
            require_mode_on=True)
    assert raw_app(env, "new-1") is None
    assert env.applications.get(owner_id=OWNER, application_id="app-1").version == 1
    assert all(env.event(m) is None for m in "cui")


def test_require_mode_on_writes_when_mode_is_on(env):
    set_mode(env, "on")
    outcome = run(env, create_decision(), new_id="new-1", require_mode_on=True)
    assert outcome.outcome == "applied"
    assert raw_app(env, "new-1") is not None


def test_mode_not_required_by_default(env):
    set_mode(env, "off")
    assert run(env, create_decision(), new_id="new-1").outcome == "applied"
