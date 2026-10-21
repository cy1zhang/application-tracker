from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from boto3.dynamodb.types import TypeDeserializer
from moto import mock_aws

from app.domain.account import AccountState
from app.domain.ai import AiProvider
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_tables import create_tables

NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)
TOKEN = {"ciphertext": "opaque", "nonce": "opaque", "encrypted_data_key": "opaque"}
KEY = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}


@pytest.fixture
def env():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        yield (
            client,
            DynamoAccountRepository(client=client, users_table="Users"),
            DynamoGmailRepository(
                client=client, users_table="Users", oauth_states_table="OAuthStates"
            ),
            DynamoAiSettingsRepository(client=client, users_table="Users"),
        )


def user_item(client, owner):
    response = client.get_item(
        TableName="Users", Key={"owner_id": {"S": owner}}, ConsistentRead=True
    )
    decoder = TypeDeserializer()
    return {k: decoder.deserialize(v) for k, v in response.get("Item", {}).items()}


def test_unknown_owner_is_active(env) -> None:
    _, accounts, _, _ = env

    assert accounts.get_status("nobody").state is AccountState.ACTIVE


def test_begin_on_missing_record_upserts_and_bumps_generation(env) -> None:
    client, accounts, _, _ = env

    status = accounts.begin_deletion("owner-a", "user-name-a", NOW)

    assert status.state is AccountState.DELETING
    assert status.phase == "revoke_gmail"
    item = user_item(client, "owner-a")
    assert item["generation"] == 1
    assert item["deletion_state"] == "deleting"
    assert item["cognito_username"] == "user-name-a"
    assert accounts.get_status("owner-a").state is AccountState.DELETING


def test_begin_with_gmail_and_ai_bumps_generation_and_clears_active_run(env) -> None:
    client, accounts, gmail, ai = env
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", True, NOW)
    client.update_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET active_run_id = :r",
        ExpressionAttributeValues={":r": {"S": "run-1"}},
    )

    accounts.begin_deletion("owner-a", "user-name-a", NOW)

    item = user_item(client, "owner-a")
    assert item["generation"] == 2
    assert "active_run_id" not in item
    assert item["encrypted_token"] == TOKEN
    assert item["ai_encrypted_key"] == KEY


def test_second_begin_is_idempotent(env) -> None:
    client, accounts, _, _ = env
    first = accounts.begin_deletion("owner-a", "user-name-a", NOW)
    before = user_item(client, "owner-a")

    second = accounts.begin_deletion("owner-a", "other-name", NOW + timedelta(hours=1))

    assert second == first
    assert user_item(client, "owner-a") == before


def test_concurrent_begins_bump_generation_once(env) -> None:
    client, accounts, _, _ = env

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda i: accounts.begin_deletion("owner-a", f"name-{i}", NOW), range(4)
        ))

    assert {r.state for r in results} == {AccountState.DELETING}
    assert user_item(client, "owner-a")["generation"] == 1


def test_lease_claim_expiry_takeover_and_phase_advance(env) -> None:
    _, accounts, _, _ = env
    accounts.begin_deletion("owner-a", "user-name-a", NOW)

    assert accounts.claim_deletion("owner-a", "lease-1", NOW) == "revoke_gmail"
    assert accounts.claim_deletion("owner-a", "lease-2", NOW + timedelta(minutes=5)) is None
    assert accounts.claim_deletion(
        "owner-a", "lease-2", NOW + timedelta(minutes=11)
    ) == "revoke_gmail"

    # The superseded lease holder can no longer advance.
    assert not accounts.advance_phase(
        "owner-a", "lease-1", "revoke_gmail", "delete_partitions"
    )
    # Wrong from_phase does not advance.
    assert not accounts.advance_phase(
        "owner-a", "lease-2", "wipe_profile", "delete_login"
    )
    assert accounts.advance_phase(
        "owner-a", "lease-2", "revoke_gmail", "delete_partitions"
    )
    assert not accounts.advance_phase(
        "owner-a", "lease-2", "revoke_gmail", "delete_partitions"
    )
    assert accounts.get_status("owner-a").phase == "delete_partitions"


def test_claim_requires_deleting_state(env) -> None:
    _, accounts, gmail, _ = env
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)

    assert accounts.claim_deletion("owner-a", "lease-1", NOW) is None
    assert accounts.claim_deletion("nobody", "lease-1", NOW) is None
    assert accounts.get_status("owner-a").state is AccountState.ACTIVE


def test_finalize_leaves_only_the_tombstone(env) -> None:
    client, accounts, gmail, ai = env
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", True, NOW)
    accounts.begin_deletion("owner-a", "user-name-a", NOW)
    accounts.claim_deletion("owner-a", "lease-1", NOW)

    assert not accounts.finalize("owner-a", "wrong-lease", NOW)
    assert accounts.finalize("owner-a", "lease-1", NOW)

    item = user_item(client, "owner-a")
    assert set(item) == {"owner_id", "deletion_state", "deleted_at", "expires_at"}
    assert item["deletion_state"] == "deleted"
    assert item["expires_at"] == int((NOW + timedelta(hours=24)).timestamp())
    assert accounts.get_status("owner-a").state is AccountState.DELETED


def test_begin_after_finalize_stays_deleted(env) -> None:
    client, accounts, _, _ = env
    accounts.begin_deletion("owner-a", "user-name-a", NOW)
    accounts.claim_deletion("owner-a", "lease-1", NOW)
    accounts.finalize("owner-a", "lease-1", NOW)
    before = user_item(client, "owner-a")

    status = accounts.begin_deletion("owner-a", "user-name-a", NOW + timedelta(hours=1))

    assert status.state is AccountState.DELETED
    assert user_item(client, "owner-a") == before
