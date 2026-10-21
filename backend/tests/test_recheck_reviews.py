import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from moto import mock_aws

from app.domain.applications import Application, ApplicationCreate
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.email_rules import ApplicationCandidate
from app.services.gmail_fetch import GmailApiError, MessageUnavailable
from scripts.recheck_reviews import (
    NoActiveConnection,
    SyncInProgress,
    recheck_reviews,
)

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
TOKEN = {
    "ciphertext": "opaque-ciphertext", "nonce": "opaque-nonce",
    "encrypted_data_key": "opaque-key",
}
MULTI_ROLE_LISTING = (
    "Explore openings for Data Analyst at Beta. "
    "Browse roles for Software Engineer Intern at Acme."
)
REAL_EVENT = (
    "Acme invites you to interview for Data Analyst. "
    "Please see the profile for Software Engineer Intern."
)
SUBJECT = "Synthetic subject marker"
SENDER = "Recruiting <jobs@example.test>"
_ser, _de = TypeSerializer(), TypeDeserializer()


def gmail(message_id: str, text: str, *, headers: bool = True) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": SUBJECT},
                {"name": "From", "value": SENDER},
            ] if headers else [],
            "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        },
    }


class FakeCipher:
    def decrypt_refresh_token(self, owner_id, encrypted):
        return "synthetic-refresh"


class FakeOAuth:
    def refresh_access_token(self, refresh_token):
        return "synthetic-access"


class FakeFetch:
    def __init__(self, messages):
        self.messages = messages
        self.requested = []
        self.on_get = None

    def get_message(self, access_token, message_id):
        self.requested.append(message_id)
        if self.on_get:
            self.on_get(message_id)
        result = self.messages[message_id]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def env():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", sync_runs_table="SyncRuns",
        )
        connections = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        connections.save_connection(
            "owner-a", 0, "a@example.test", TOKEN, NOW - timedelta(days=2)
        )
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        yield client, connections, applications


def seed(client, message_id, *, owner="owner-a", reason="conflicting_roles", **extra):
    item = {
        "owner_id": owner, "event_id": f"gmail#{message_id}",
        "source_message_id": message_id, "thread_id": f"thread-{message_id}",
        "received_at": NOW.isoformat(), "outcome": "review_required",
        "reason": reason, "evidence": f"evidence-{message_id}", **extra,
    }
    client.put_item(
        TableName="Events", Item={k: _ser.serialize(v) for k, v in item.items()}
    )


def get_event(client, message_id, owner="owner-a"):
    response = client.get_item(
        TableName="Events",
        Key={"owner_id": {"S": owner}, "event_id": {"S": f"gmail#{message_id}"}},
    )
    return {k: _de.deserialize(v) for k, v in response["Item"].items()}


def run(env, messages, *, apply=False, owner="owner-a", fetch=None, connections=None):
    client, conns, applications = env
    fetch = fetch or FakeFetch(messages)
    report = recheck_reviews(
        owner_id=owner, apply=apply, client=client,
        connections=connections or conns, cipher=FakeCipher(), oauth=FakeOAuth(),
        fetch=fetch, applications=applications, events_table="Events",
        users_table="Users", now=NOW,
    )
    return report, fetch


def test_apply_flips_ignored_item_and_preserves_audit_trail(env) -> None:
    client = env[0]
    seed(client, "m1", subject="old subject", sender="old sender")
    before = get_event(client, "m1")

    report, _ = run(env, {"m1": gmail("m1", MULTI_ROLE_LISTING)}, apply=True)

    after = get_event(client, "m1")
    assert after["outcome"] == "ignored" and after["reason"] == "no_event"
    assert after["previous_outcome"] == "review_required"
    assert after["previous_reason"] == "conflicting_roles"
    assert after["rechecked_at"] == NOW.isoformat()
    assert "subject" not in after and "sender" not in after
    for key in ("evidence", "thread_id", "received_at", "source_message_id"):
        assert after[key] == before[key]
    assert report["ignored"] == 1 and report["scanned"] == 1
    assert report["pending_by_reason"] == {"conflicting_roles": 1}


def test_preview_makes_no_writes(env) -> None:
    client = env[0]
    seed(client, "m1")
    seed(client, "m2", reason="unknown_stage")
    snapshot = [get_event(client, "m1"), get_event(client, "m2")]

    report, _ = run(env, {
        "m1": gmail("m1", MULTI_ROLE_LISTING), "m2": gmail("m2", REAL_EVENT),
    })

    assert [get_event(client, "m1"), get_event(client, "m2")] == snapshot
    assert report["would_ignore"] == 1 and report["ignored"] == 0
    assert report["headers_backfilled"] == 0


def test_ignored_decision_for_other_reasons_is_kept_and_backfilled(env) -> None:
    client = env[0]
    seed(client, "m1", reason="ambiguous_application")
    seed(client, "m2", reason="subject_only_event")
    seed(client, "m3")
    messages = {n: gmail(n, MULTI_ROLE_LISTING) for n in ("m1", "m2", "m3")}

    report, _ = run(env, messages, apply=True)

    for name, reason in (("m1", "ambiguous_application"), ("m2", "subject_only_event")):
        item = get_event(client, name)
        assert item["outcome"] == "review_required" and item["reason"] == reason
        assert item["subject"] == SUBJECT and "previous_reason" not in item
    assert get_event(client, "m3")["outcome"] == "ignored"
    assert report["retained_by_reason"] == {
        "ignored_but_kept:ambiguous_application": 1,
        "ignored_but_kept:subject_only_event": 1,
    }
    assert report["ignore_by_reason"] == {
        "ambiguous_application->no_event": 1,
        "conflicting_roles->no_event": 1,
        "subject_only_event->no_event": 1,
    }
    assert report["ignored"] == 1 and report["headers_backfilled"] == 2


def test_preview_reports_would_backfill_and_ignore_breakdown(env) -> None:
    client = env[0]
    seed(client, "m1")
    seed(client, "m2", reason="ambiguous_application")
    seed(client, "m3", subject="s", sender="f")
    seed(client, "m4")

    report, _ = run(env, {
        "m1": gmail("m1", MULTI_ROLE_LISTING), "m2": gmail("m2", MULTI_ROLE_LISTING),
        "m3": gmail("m3", REAL_EVENT), "m4": gmail("m4", REAL_EVENT),
    })

    assert report["would_ignore"] == 1 and report["would_backfill"] == 2
    assert report["headers_backfilled"] == 0
    assert report["ignore_by_reason"] == {
        "ambiguous_application->no_event": 1, "conflicting_roles->no_event": 1,
    }


def test_real_event_stays_pending_and_gets_headers_backfilled(env) -> None:
    client = env[0]
    seed(client, "m1")

    report, _ = run(env, {"m1": gmail("m1", REAL_EVENT)}, apply=True)

    item = get_event(client, "m1")
    assert item["outcome"] == "review_required" and item["reason"] == "conflicting_roles"
    assert item["subject"] == SUBJECT and item["sender"] == SENDER
    assert report["retained_by_reason"] == {"conflicting_roles": 1}
    assert report["headers_backfilled"] == 1 and report["ignored"] == 0


def test_backfill_is_bounded_and_never_overwrites_existing_headers(env) -> None:
    client = env[0]
    seed(client, "m1")
    seed(client, "m2", subject="kept subject")
    long_message = gmail("m1", REAL_EVENT)
    long_message["payload"]["headers"][0]["value"] = "s" * 1000

    run(env, {"m1": long_message, "m2": gmail("m2", REAL_EVENT)}, apply=True)

    assert get_event(client, "m1")["subject"] == "s" * 300
    kept = get_event(client, "m2")
    assert kept["subject"] == "kept subject" and kept["sender"] == SENDER


def test_unavailable_and_failed_fetches_stay_pending(env) -> None:
    client = env[0]
    seed(client, "m1")
    seed(client, "m2")
    seed(client, "m3")

    report, _ = run(env, {
        "m1": MessageUnavailable("gone"), "m2": GmailApiError("boom"),
        "m3": gmail("m3", MULTI_ROLE_LISTING),
    }, apply=True)

    assert get_event(client, "m1")["outcome"] == "review_required"
    assert get_event(client, "m2")["outcome"] == "review_required"
    assert get_event(client, "m3")["outcome"] == "ignored"
    assert report["unavailable"] == 1 and report["fetch_failed"] == 1


def test_unparseable_message_stays_pending_but_headers_are_backfilled(env) -> None:
    client = env[0]
    seed(client, "m1", reason="unparseable_message")
    broken = {
        "id": "m1", "threadId": "thread-m1", "internalDate": "not-a-number",
        "payload": {"headers": [
            {"name": "Subject", "value": SUBJECT}, {"name": "From", "value": SENDER},
        ]},
    }

    report, _ = run(env, {"m1": broken}, apply=True)

    item = get_event(client, "m1")
    assert item["outcome"] == "review_required"
    assert item["subject"] == SUBJECT and item["sender"] == SENDER
    assert report["unparseable"] == 1


def test_resolved_review_is_not_listed_or_touched(env) -> None:
    client = env[0]
    seed(client, "m1", resolution="reject", resolved_at=NOW.isoformat())
    before = get_event(client, "m1")

    report, fetch = run(env, {"m1": gmail("m1", MULTI_ROLE_LISTING)}, apply=True)

    assert get_event(client, "m1") == before
    assert fetch.requested == [] and report["scanned"] == 0


def test_item_changed_between_listing_and_update_is_skipped(env) -> None:
    client = env[0]
    seed(client, "m1")
    seed(client, "m2")
    fetch = FakeFetch({
        "m1": gmail("m1", MULTI_ROLE_LISTING), "m2": gmail("m2", MULTI_ROLE_LISTING),
    })

    def mutate(message_id):
        if message_id == "m1":
            client.update_item(
                TableName="Events",
                Key={"owner_id": {"S": "owner-a"}, "event_id": {"S": "gmail#m1"}},
                UpdateExpression="SET reason = :r",
                ExpressionAttributeValues={":r": {"S": "unknown_stage"}},
            )

    fetch.on_get = mutate
    report, _ = run(env, {}, apply=True, fetch=fetch)

    assert get_event(client, "m1")["outcome"] == "review_required"
    assert get_event(client, "m1")["reason"] == "unknown_stage"
    assert get_event(client, "m2")["outcome"] == "ignored"
    assert report["skipped_changed"] == 1 and report["ignored"] == 1


def test_item_resolved_between_listing_and_update_is_not_overwritten(env) -> None:
    client = env[0]
    seed(client, "m1")
    fetch = FakeFetch({"m1": gmail("m1", MULTI_ROLE_LISTING)})
    fetch.on_get = lambda _id: client.update_item(
        TableName="Events",
        Key={"owner_id": {"S": "owner-a"}, "event_id": {"S": "gmail#m1"}},
        UpdateExpression="SET resolution = :r",
        ExpressionAttributeValues={":r": {"S": "reject"}},
    )

    report, _ = run(env, {}, apply=True, fetch=fetch)

    item = get_event(client, "m1")
    assert item["outcome"] == "review_required" and item["resolution"] == "reject"
    assert report["skipped_changed"] == 1


def test_generation_change_mid_run_stops_before_further_work(env) -> None:
    client, connections, _ = env
    for name in ("m1", "m2", "m3"):
        seed(client, name)
    fetch = FakeFetch({n: gmail(n, MULTI_ROLE_LISTING) for n in ("m1", "m2", "m3")})
    fetch.on_get = lambda message_id: (
        connections.disconnect("owner-a") if message_id == "m1" else None
    )

    report, _ = run(env, {}, apply=True, fetch=fetch)

    assert fetch.requested == ["m1"]
    assert report["stopped_generation_changed"] == 1
    for name in ("m1", "m2", "m3"):
        assert get_event(client, name)["outcome"] == "review_required"


def test_generation_check_inside_write_stops_the_run(env) -> None:
    client, _, _ = env
    seed(client, "m1")
    seed(client, "m2")
    fetch = FakeFetch({n: gmail(n, MULTI_ROLE_LISTING) for n in ("m1", "m2")})
    # Disconnect after the pre-fetch check but before the write: the users-table
    # guard in the transaction must still refuse the update.
    class Racing(DynamoGmailRepository):
        def get_active(self, owner_id, expected_generation):
            result = super().get_active(owner_id, expected_generation)
            super().disconnect(owner_id)
            return result

    racing = Racing(client=client, users_table="Users", oauth_states_table="OAuthStates")
    report, _ = run(env, {}, apply=True, fetch=fetch, connections=racing)

    assert fetch.requested == ["m1"]
    assert report["stopped_generation_changed"] == 1 and report["ignored"] == 0
    assert get_event(client, "m1")["outcome"] == "review_required"


def test_other_owners_items_are_never_fetched_or_modified(env) -> None:
    client = env[0]
    seed(client, "m1")
    seed(client, "other", owner="owner-b")
    before = get_event(client, "other", owner="owner-b")

    report, fetch = run(env, {"m1": gmail("m1", MULTI_ROLE_LISTING)}, apply=True)

    assert fetch.requested == ["m1"] and report["scanned"] == 1
    assert get_event(client, "other", owner="owner-b") == before


def test_refuses_while_a_sync_run_is_active(env) -> None:
    client = env[0]
    seed(client, "m1")
    client.update_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET active_run_id = :r",
        ExpressionAttributeValues={":r": {"S": "run-1"}},
    )

    with pytest.raises(SyncInProgress):
        run(env, {"m1": gmail("m1", MULTI_ROLE_LISTING)}, apply=True)

    assert get_event(client, "m1")["outcome"] == "review_required"


def test_refuses_without_active_connection(env) -> None:
    client, connections, _ = env
    connections.disconnect("owner-a")
    seed(client, "m1")

    with pytest.raises(NoActiveConnection):
        run(env, {"m1": gmail("m1", MULTI_ROLE_LISTING)}, apply=True)


def test_applications_are_unchanged_by_apply(env) -> None:
    client, _, applications = env
    applications.create(
        owner_id="owner-a", application_id="app-1", event_id="event-1",
        occurred_at=NOW - timedelta(days=5),
        payload=ApplicationCreate(
            company="Acme", role="Software Engineer Intern", status="applied"
        ),
    )
    seed(client, "m1")
    seed(client, "m2", reason="unknown_stage")
    snapshot = client.scan(TableName="Applications")["Items"]

    run(env, {
        "m1": gmail("m1", MULTI_ROLE_LISTING), "m2": gmail("m2", REAL_EVENT),
    }, apply=True)

    assert client.scan(TableName="Applications")["Items"] == snapshot
    assert applications.get(owner_id="owner-a", application_id="app-1").version == 1


def test_applied_decision_is_retained_not_applied(env, monkeypatch) -> None:
    client = env[0]
    candidate = ApplicationCandidate(
        application=Application(
            application_id="app-1", company="Acme", role="Software Engineer Intern",
            role_type="internship", season=None, year=None, status=None,
            application_date=None, created_at=NOW - timedelta(days=10),
            updated_at=NOW - timedelta(days=1), version=1,
        ),
        thread_ids=frozenset({"thread-m1"}), requisition_ids=frozenset(),
    )
    monkeypatch.setattr(
        "scripts.recheck_reviews.load_candidates", lambda owner, repo: [candidate]
    )
    seed(client, "m1", reason="unknown_stage")
    text = "Acme invites you to interview for Software Engineer Intern."

    report, _ = run(env, {"m1": gmail("m1", text)}, apply=True)

    assert get_event(client, "m1")["outcome"] == "review_required"
    assert report["ignored"] == 0 and report["retained_now_actionable"] == 1


def test_report_contains_only_aggregate_counts(env, capsys) -> None:
    client = env[0]
    seed(client, "m1", subject="stored-subject-marker")
    seed(client, "m2")

    report, _ = run(env, {
        "m1": gmail("m1", MULTI_ROLE_LISTING), "m2": gmail("m2", REAL_EVENT),
    }, apply=True)

    text = json.dumps(report)
    for forbidden in (
        SUBJECT, SENDER, "example.test", "m1", "m2", "thread-", "evidence-",
        "stored-subject-marker", "synthetic-access",
    ):
        assert forbidden not in text
    assert capsys.readouterr().out == ""
