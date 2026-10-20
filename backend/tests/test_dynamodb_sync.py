from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository, SyncUnavailable
from app.repositories.dynamodb_tables import create_tables

CONNECTED_AT = datetime(2026, 10, 18, 12, tzinfo=UTC)
STARTED_AT = datetime(2026, 10, 20, 12, tzinfo=UTC)
TOKEN = {
    "ciphertext": "opaque-ciphertext",
    "nonce": "opaque-nonce",
    "encrypted_data_key": "opaque-key",
}


@pytest.fixture
def repositories():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        yield (
            DynamoGmailRepository(
                client=client, users_table="Users", oauth_states_table="OAuthStates"
            ),
            DynamoSyncRepository(
                client=client, users_table="Users", sync_runs_table="SyncRuns"
            ),
            client,
        )


def test_first_run_uses_connection_boundary_and_overlap_reuses_run(repositories) -> None:
    gmail, sync, client = repositories
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, CONNECTED_AT)

    first = sync.start("owner-a", "run-1", STARTED_AT)
    overlapping = sync.start("owner-a", "run-2", STARTED_AT)

    assert first.run_id == overlapping.run_id == "run-1"
    assert first.mode == "import"
    assert first.status == "pending"
    assert first.generation == 1
    assert first.import_after == datetime(2026, 7, 20, 12, tzinfo=UTC)
    assert sync.get_latest("owner-a") == first
    assert client.scan(TableName="SyncRuns")["Count"] == 1


def test_runs_are_owner_scoped(repositories) -> None:
    gmail, sync, _ = repositories
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, CONNECTED_AT)
    gmail.save_connection("owner-b", 0, "b@example.test", TOKEN, CONNECTED_AT)

    first = sync.start("owner-a", "run-a", STARTED_AT)
    second = sync.start("owner-b", "run-b", STARTED_AT)

    assert sync.get_latest("owner-a") == first
    assert sync.get_latest("owner-b") == second
    assert sync.get_latest("owner-c") is None


def test_disconnected_owner_cannot_start_run(repositories) -> None:
    gmail, sync, client = repositories
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, CONNECTED_AT)
    gmail.disconnect("owner-a")

    with pytest.raises(SyncUnavailable):
        sync.start("owner-a", "run-1", STARTED_AT)

    assert client.scan(TableName="SyncRuns")["Count"] == 0


def test_legacy_connection_uses_first_run_as_fixed_boundary(repositories) -> None:
    _, sync, client = repositories
    client.put_item(
        TableName="Users",
        Item={
            "owner_id": {"S": "owner-a"},
            "generation": {"N": "1"},
            "email": {"S": "a@example.test"},
            "encrypted_token": {"M": {key: {"S": value} for key, value in TOKEN.items()}},
        },
    )

    run = sync.start("owner-a", "run-1", STARTED_AT)

    assert run.import_after == datetime(2026, 7, 22, 12, tzinfo=UTC)
