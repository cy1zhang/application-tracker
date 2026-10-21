from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository, StaleSyncRun
from app.repositories.dynamodb_tables import create_tables
from app.services.gmail_fetch import (
    GmailApiError,
    HistoryExpired,
    HistoryPage,
    MessagePage,
    MessageUnavailable,
)
from app.services.sync_worker import process_sync_page

NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
TOKEN = {
    "ciphertext": "opaque-ciphertext", "nonce": "opaque-nonce",
    "encrypted_data_key": "opaque-key",
}


def message(message_id: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Application update"},
                {"name": "From", "value": "Recruiting <jobs@acme.test>"},
            ],
            "body": {"data": urlsafe_b64encode(
                b"Acme invites you to interview for Engineer."
            ).decode().rstrip("=")},
        },
    }


class FakeCipher:
    def decrypt_refresh_token(self, owner_id, encrypted):
        assert owner_id == "owner-a" and encrypted == TOKEN
        return "synthetic-refresh"


class FakeOAuth:
    def refresh_access_token(self, refresh_token):
        assert refresh_token == "synthetic-refresh"
        return "synthetic-access"


class FakeFetch:
    def __init__(self):
        self.fail_second_once = True
        self.requested = []

    def profile_history_id(self, access_token):
        assert access_token == "synthetic-access"
        return "100"

    def list_import_page(self, access_token, boundary, page_token):
        assert access_token == "synthetic-access"
        assert boundary == datetime(2026, 7, 20, 12, tzinfo=UTC)
        if page_token == "page-2":
            return MessagePage(["msg-3"], None)
        assert page_token is None
        return MessagePage(["msg-1", "msg-2"], "page-2")

    def get_message(self, access_token, message_id):
        assert access_token == "synthetic-access"
        self.requested.append(message_id)
        if message_id == "msg-2" and self.fail_second_once:
            self.fail_second_once = False
            raise GmailApiError("synthetic provider failure")
        return message(message_id)

    def list_history_page(self, access_token, history_id, page_token):
        assert access_token == "synthetic-access"
        assert history_id == "100" and page_token is None
        return HistoryPage(["msg-4"], None, "110")


class FakePublisher:
    def __init__(self):
        self.messages = []

    def send_message(self, *, QueueUrl, MessageBody):
        assert QueueUrl == "synthetic-queue"
        self.messages.append(MessageBody)


@pytest.fixture
def setup():
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
        syncs = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        run = syncs.start("owner-a", "run-1", NOW)
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events",
            users_table="Users", guard_generation=run.generation, guard_run_id=run.run_id,
        )
        fetch = FakeFetch()
        publisher = FakePublisher()
        yield client, connections, syncs, applications, emails, fetch, publisher


def invoke(setup, *, run_id="run-1", lease_id="lease-1", now=NOW):
    client, connections, syncs, applications, _, fetch, publisher = setup
    emails = DynamoEmailRepository(
        client=client, applications_table="Applications", events_table="Events",
        users_table="Users", guard_generation=1, guard_run_id=run_id,
    )
    return process_sync_page(
        owner_id="owner-a", run_id=run_id, generation=1,
        lease_id=lease_id, now=now, syncs=syncs, connections=connections,
        cipher=FakeCipher(), oauth=FakeOAuth(), fetch=fetch,
        applications=applications, emails=emails, publisher=publisher,
        queue_url="synthetic-queue",
    )


def test_interrupted_page_replays_without_skipping_durable_outcomes(setup) -> None:
    client, _, syncs, _, _, fetch, publisher = setup

    with pytest.raises(GmailApiError):
        invoke(setup)
    assert syncs.get_latest("owner-a").processed_count == 0
    assert syncs.get_latest("owner-a").status == "running"
    assert "Item" in client.get_item(
        TableName="Events",
        Key={"owner_id": {"S": "owner-a"}, "event_id": {"S": "gmail#msg-1"}},
    )

    run = invoke(setup, lease_id="lease-2", now=NOW + timedelta(minutes=13))

    assert run.status == "pending"
    assert run.processed_count == 2
    assert fetch.requested == ["msg-1", "msg-2", "msg-1", "msg-2"]
    assert len(publisher.messages) == 1
    assert client.query(
        TableName="Events", KeyConditionExpression="owner_id = :owner",
        ExpressionAttributeValues={":owner": {"S": "owner-a"}},
    )["Count"] == 2


def test_disconnected_run_does_not_fetch_gmail(setup) -> None:
    _, connections, _, _, _, fetch, _ = setup
    connections.disconnect("owner-a")

    with pytest.raises(StaleSyncRun):
        invoke(setup)

    assert fetch.requested == []


def test_duplicate_delivery_does_not_process_or_checkpoint_twice(setup) -> None:
    _, _, syncs, _, _, fetch, publisher = setup
    fetch.fail_second_once = False

    first = invoke(setup)
    duplicate = invoke(setup, lease_id="lease-duplicate")

    assert first.status == "pending"
    assert duplicate.status == "completed"
    assert fetch.requested == ["msg-1", "msg-2", "msg-3"]
    assert syncs.get_latest("owner-a").processed_count == 3
    assert len(publisher.messages) == 1


def test_completion_releases_run_and_stale_generation_cannot_restart(setup) -> None:
    _, connections, _, _, _, fetch, _ = setup
    fetch.fail_second_once = False
    invoke(setup)

    run = invoke(setup, lease_id="lease-2", now=NOW + timedelta(seconds=1))
    assert run.status == "completed"
    assert run.processed_count == 3
    assert fetch.requested == ["msg-1", "msg-2", "msg-3"]
    with pytest.raises(StaleSyncRun):
        invoke(setup, lease_id="lease-3", now=NOW + timedelta(seconds=2))

    connections.disconnect("owner-a")
    with pytest.raises(StaleSyncRun):
        invoke(setup, lease_id="lease-4", now=NOW + timedelta(seconds=3))


def test_next_run_uses_incremental_history_and_advances_checkpoint(setup) -> None:
    client, _, syncs, _, _, fetch, _ = setup
    fetch.fail_second_once = False
    invoke(setup)
    invoke(setup, lease_id="lease-2", now=NOW + timedelta(seconds=1))
    next_run = syncs.start("owner-a", "run-2", NOW + timedelta(days=1))
    assert next_run.history_id == "100"

    result = invoke(setup, run_id="run-2", now=NOW + timedelta(days=1))

    assert result.mode == "incremental" and result.status == "completed"
    assert fetch.requested == ["msg-1", "msg-2", "msg-3", "msg-4"]
    assert client.get_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}}
    )["Item"]["history_id"]["S"] == "110"


def test_stale_history_rescans_from_last_success_with_overlap(setup) -> None:
    _, _, syncs, _, _, fetch, publisher = setup
    fetch.fail_second_once = False
    invoke(setup)
    invoke(setup, lease_id="lease-2", now=NOW + timedelta(seconds=1))
    syncs.start("owner-a", "run-2", NOW + timedelta(days=14))

    def expired(*args):
        raise HistoryExpired("stale")

    fetch.list_history_page = expired
    result = invoke(setup, run_id="run-2", now=NOW + timedelta(days=14))

    assert result.mode == "rescan" and result.status == "pending"
    assert result.import_after == NOW + timedelta(seconds=1) - timedelta(days=1)
    assert len(publisher.messages) == 2


def test_incremental_history_checkpoint_waits_for_final_page(setup) -> None:
    client, _, syncs, _, _, fetch, _ = setup
    fetch.fail_second_once = False
    invoke(setup)
    invoke(setup, lease_id="lease-2", now=NOW + timedelta(seconds=1))
    syncs.start("owner-a", "run-2", NOW + timedelta(days=1))

    def history_page(access_token, history_id, page_token):
        assert history_id == "100"
        if page_token is None:
            return HistoryPage(["msg-4"], "page-2", "110")
        assert page_token == "page-2"
        return HistoryPage(["msg-5"], None, "120")

    fetch.list_history_page = history_page
    first = invoke(setup, run_id="run-2", now=NOW + timedelta(days=1))
    user = client.get_item(TableName="Users", Key={"owner_id": {"S": "owner-a"}})["Item"]

    assert first.status == "pending" and first.processed_count == 1
    assert user["history_id"]["S"] == "100"

    second = invoke(
        setup, run_id="run-2", lease_id="lease-3",
        now=NOW + timedelta(days=1, seconds=1),
    )
    user = client.get_item(TableName="Users", Key={"owner_id": {"S": "owner-a"}})["Item"]
    assert second.status == "completed" and second.processed_count == 2
    assert user["history_id"]["S"] == "120"


def test_message_removed_after_listing_is_ignored_durably(setup) -> None:
    client, _, _, _, _, fetch, _ = setup
    fetch.fail_second_once = False

    def missing(access_token, message_id):
        if message_id == "msg-2":
            raise MessageUnavailable("removed")
        return message(message_id)

    fetch.get_message = missing
    result = invoke(setup)

    assert result.status == "pending" and result.processed_count == 2
    event = client.get_item(TableName="Events", Key={
        "owner_id": {"S": "owner-a"}, "event_id": {"S": "gmail#msg-2"},
    })["Item"]
    assert event["outcome"]["S"] == "ignored"
    assert event["reason"]["S"] == "message_unavailable"


def test_large_history_record_checkpoints_ten_messages_at_a_time(setup) -> None:
    client, _, syncs, _, _, fetch, _ = setup
    fetch.fail_second_once = False
    invoke(setup)
    invoke(setup, lease_id="lease-2", now=NOW + timedelta(seconds=1))
    syncs.start("owner-a", "run-2", NOW + timedelta(days=1))

    def large_history(access_token, history_id, page_token):
        assert history_id == "100" and page_token is None
        return HistoryPage([f"bulk-{index}" for index in range(25)], None, "120")

    fetch.list_history_page = large_history
    first = invoke(setup, run_id="run-2", now=NOW + timedelta(days=1))
    second = invoke(
        setup, run_id="run-2", lease_id="lease-3",
        now=NOW + timedelta(days=1, seconds=1),
    )
    third = invoke(
        setup, run_id="run-2", lease_id="lease-4",
        now=NOW + timedelta(days=1, seconds=2),
    )

    assert (first.status, first.processed_count) == ("pending", 10)
    assert (second.status, second.processed_count) == ("pending", 20)
    assert (third.status, third.processed_count) == ("completed", 25)
    assert client.get_item(TableName="Users", Key={
        "owner_id": {"S": "owner-a"},
    })["Item"]["history_id"]["S"] == "120"
