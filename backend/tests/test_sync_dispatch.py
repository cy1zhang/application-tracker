import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.sync_dispatch import (
    dispatch_daily,
    recover_deletions,
    recover_stranded_plans,
    recover_unfinished,
)

NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)
TOKEN = {"ciphertext": "opaque", "nonce": "opaque", "encrypted_data_key": "opaque"}


class Publisher:
    def __init__(self):
        self.messages = []

    def send_message(self, *, QueueUrl, MessageBody):
        assert QueueUrl == "queue"
        self.messages.append(json.loads(MessageBody))


@pytest.fixture
def setup():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", sync_runs_table="SyncRuns",
        )
        gmail = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        syncs = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        publisher = Publisher()
        yield client, gmail, syncs, publisher


def test_daily_dispatch_pages_connected_owners_and_reuses_active_run(setup) -> None:
    client, gmail, syncs, publisher = setup
    for index in range(3):
        gmail.save_connection(
            f"owner-{index}", 0, f"{index}@example.test", TOKEN, NOW
        )
    gmail.disconnect("owner-1")
    existing = syncs.start("owner-0", "existing", NOW)

    dispatch_daily(client, syncs, publisher, "Users", "queue", NOW, page_size=1)

    assert len(publisher.messages) == 2
    assert {message["owner_id"] for message in publisher.messages} == {
        "owner-0", "owner-2"
    }
    assert syncs.get_latest("owner-0") == existing
    assert client.scan(TableName="SyncRuns")["Count"] == 2


def test_recovery_pages_pending_and_expired_runs_without_restarting_completed(setup) -> None:
    client, gmail, syncs, publisher = setup
    for index in range(3):
        gmail.save_connection(
            f"owner-{index}", 0, f"{index}@example.test", TOKEN, NOW
        )
        syncs.start(f"owner-{index}", f"run-{index}", NOW - timedelta(minutes=13))
    syncs.claim("owner-1", "run-1", 1, "lease", NOW - timedelta(minutes=13))
    syncs.claim("owner-2", "run-2", 1, "lease", NOW - timedelta(minutes=13))
    syncs.complete("owner-2", "run-2", 1, "lease", 0, NOW)

    recover_unfinished(client, publisher, "Users", "SyncRuns", "queue", NOW, page_size=1)

    assert {message["owner_id"] for message in publisher.messages} == {
        "owner-0", "owner-1"
    }


def test_recovery_does_not_reenqueue_disconnected_run(setup) -> None:
    client, gmail, syncs, publisher = setup
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    syncs.start("owner-a", "run-1", NOW)
    gmail.disconnect("owner-a")

    recover_unfinished(client, publisher, "Users", "SyncRuns", "queue", NOW, page_size=1)

    assert publisher.messages == []
    assert client.get_item(TableName="SyncRuns", Key={
        "owner_id": {"S": "owner-a"}, "run_id": {"S": "run-1"},
    })["Item"]["status"]["S"] == "failed"


def test_recover_deletions_republishes_only_stalled_deleting_users(setup) -> None:
    from app.repositories.dynamodb_account import DynamoAccountRepository

    client, gmail, _, publisher = setup
    accounts = DynamoAccountRepository(client=client, users_table="Users")
    for owner in ("expired", "live", "fresh", "done", "active"):
        gmail.save_connection(owner, 0, f"{owner}@example.test", TOKEN, NOW)
    for owner in ("expired", "live", "fresh", "done"):
        accounts.begin_deletion(owner, "name", NOW)
    accounts.claim_deletion("expired", "lease", NOW - timedelta(minutes=11))
    accounts.claim_deletion("live", "lease", NOW)
    accounts.claim_deletion("done", "lease", NOW)
    accounts.finalize("done", "lease", NOW)

    count = recover_deletions(client, publisher, "Users", "queue", NOW, page_size=1)

    assert count == 2
    assert sorted(m["owner_id"] for m in publisher.messages) == ["expired", "fresh"]
    assert all(m["kind"] == "delete_account" for m in publisher.messages)


def test_daily_dispatch_still_skips_deleting_users(setup) -> None:
    from app.repositories.dynamodb_account import DynamoAccountRepository

    client, gmail, syncs, publisher = setup
    for owner in ("owner-0", "owner-1"):
        gmail.save_connection(owner, 0, f"{owner}@example.test", TOKEN, NOW)
    DynamoAccountRepository(client=client, users_table="Users").begin_deletion(
        "owner-1", "name", NOW
    )

    dispatch_daily(client, syncs, publisher, "Users", "queue", NOW)

    assert [m["owner_id"] for m in publisher.messages] == ["owner-0"]


def test_dispatch_handler_recover_also_recovers_deletions(setup, monkeypatch) -> None:
    from app import dispatch_handler
    from app.repositories.dynamodb_account import DynamoAccountRepository

    client, _, _, publisher = setup
    DynamoAccountRepository(client=client, users_table="Users").begin_deletion(
        "owner-x", "name", NOW
    )
    for key, value in {
        "SYNC_QUEUE_URL": "queue", "USERS_TABLE": "Users",
        "SYNC_RUNS_TABLE": "SyncRuns", "DELETION_QUEUE_URL": "queue",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(dispatch_handler.boto3, "client", lambda name: client if name == "dynamodb" else publisher)

    dispatch_handler.handler({"mode": "recover"}, None)

    assert publisher.messages == [{"kind": "delete_account", "owner_id": "owner-x"}]


def _owner_with_plan(client, owner, mode, *, planned=True, executed=False):
    client.put_item(TableName="Users", Item={
        "owner_id": {"S": owner}, "auto_apply_mode": {"S": mode},
    })
    plan = {"planned_at": {"S": "t"}}
    if executed:
        plan["executed_at"] = {"S": "t"}
    item = {"owner_id": {"S": owner}, "event_id": {"S": f"gmail#{owner}-m"}}
    if planned:
        item["ai_plan"] = {"M": plan}
    client.put_item(TableName="Events", Item=item)


def test_recover_stranded_plans_enqueues_only_owners_on_with_unexecuted_plans(setup) -> None:
    client, _, _, publisher = setup
    _owner_with_plan(client, "on-stranded", "on")
    _owner_with_plan(client, "plan-mode", "plan")
    _owner_with_plan(client, "on-none", "on", planned=False)
    _owner_with_plan(client, "on-executed", "on", executed=True)

    count = recover_stranded_plans(client, publisher, "Users", "Events", "queue", page_size=1)

    assert count == 1
    assert publisher.messages == [{"mode": "apply_plan", "owner_id": "on-stranded"}]


def test_dispatch_handler_recover_also_recovers_stranded_plans(setup, monkeypatch) -> None:
    from app import dispatch_handler

    client, _, _, publisher = setup
    _owner_with_plan(client, "owner-s", "on")
    for key, value in {
        "SYNC_QUEUE_URL": "queue", "USERS_TABLE": "Users",
        "SYNC_RUNS_TABLE": "SyncRuns", "EVENTS_TABLE": "Events",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(dispatch_handler.boto3, "client", lambda name: client if name == "dynamodb" else publisher)

    dispatch_handler.handler({"mode": "recover"}, None)

    assert publisher.messages == [{"mode": "apply_plan", "owner_id": "owner-s"}]
