import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.sync_dispatch import dispatch_daily, recover_unfinished

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
        syncs.start(f"owner-{index}", f"run-{index}", NOW - timedelta(minutes=4))
    syncs.claim("owner-1", "run-1", 1, "lease", NOW - timedelta(minutes=4))
    syncs.claim("owner-2", "run-2", 1, "lease", NOW - timedelta(minutes=4))
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
