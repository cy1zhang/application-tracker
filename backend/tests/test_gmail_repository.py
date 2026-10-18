from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.repositories.gmail import StaleGeneration

NOW = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)
TOKEN = {
    "ciphertext": "opaque-ciphertext",
    "nonce": "opaque-nonce",
    "encrypted_data_key": "opaque-key",
}


@pytest.fixture
def repository():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            oauth_states_table="OAuthStates",
        )
        yield DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )


def test_state_consumed_once(repository) -> None:
    repository.create_state(
        state_hash="state-hash",
        owner_id="owner-a",
        expected_email="a@example.test",
        expected_generation=0,
        expires_at=NOW + timedelta(minutes=10),
    )

    state = repository.consume_state("state-hash", NOW)

    assert state is not None
    assert (state.owner_id, state.expected_email, state.expected_generation) == (
        "owner-a",
        "a@example.test",
        0,
    )
    assert repository.consume_state("state-hash", NOW) is None


def test_expired_state_rejected_before_ttl_cleanup(repository) -> None:
    repository.create_state(
        state_hash="expired-state",
        owner_id="owner-a",
        expected_email="a@example.test",
        expected_generation=0,
        expires_at=NOW - timedelta(seconds=1),
    )

    assert repository.consume_state("expired-state", NOW) is None
    assert repository.consume_state("expired-state", NOW) is None


def test_connection_is_owner_scoped(repository) -> None:
    repository.save_connection("owner-a", 0, "a@example.test", TOKEN)

    assert repository.get_connection("owner-a").email == "a@example.test"
    assert repository.get_connection("owner-b").email is None
    assert repository.get_active("owner-b", 0) is None


def test_disconnect_advances_generation(repository) -> None:
    repository.save_connection("owner-a", 0, "a@example.test", TOKEN)

    repository.disconnect("owner-a")

    connection = repository.get_connection("owner-a")
    assert connection.generation == 2
    assert connection.email is None
    assert connection.encrypted_token is None


def test_stale_generation_has_no_active_credential(repository) -> None:
    repository.save_connection("owner-a", 0, "a@example.test", TOKEN)
    assert repository.get_active("owner-a", 1) == TOKEN

    repository.disconnect("owner-a")

    assert repository.get_active("owner-a", 1) is None


def test_disconnect_invalidates_in_flight_consent(repository) -> None:
    repository.create_state(
        state_hash="before-disconnect",
        owner_id="owner-a",
        expected_email="a@example.test",
        expected_generation=0,
        expires_at=NOW + timedelta(minutes=10),
    )
    repository.disconnect("owner-a")

    with pytest.raises(StaleGeneration):
        repository.save_connection("owner-a", 0, "a@example.test", TOKEN)

    assert repository.get_connection("owner-a").email is None
