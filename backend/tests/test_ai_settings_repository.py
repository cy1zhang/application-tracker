import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from app.domain.ai import AiProvider
from app.repositories.dynamodb_ai import AiKeyRequired, DynamoAiSettingsRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.sync_dispatch import dispatch_daily

NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)
TODAY = "2026-10-20"
TOKEN = {"ciphertext": "opaque", "nonce": "opaque", "encrypted_data_key": "opaque"}
KEY = {
    "ciphertext": "opaque-ai-ciphertext",
    "nonce": "opaque-ai-nonce",
    "encrypted_data_key": "opaque-ai-key",
}
SECRET = "synthetic-secret-key-1234"


class Publisher:
    def __init__(self):
        self.messages = []

    def send_message(self, *, QueueUrl, MessageBody):
        self.messages.append(json.loads(MessageBody))


@pytest.fixture
def setup():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            sync_runs_table="SyncRuns",
        )
        yield (
            client,
            DynamoAiSettingsRepository(client=client, users_table="Users"),
            DynamoGmailRepository(
                client=client, users_table="Users", oauth_states_table="OAuthStates"
            ),
            DynamoSyncRepository(
                client=client, users_table="Users", sync_runs_table="SyncRuns"
            ),
        )


def save(repository, owner="owner-a", provider=AiProvider.OPENAI, key=KEY, hint="1234",
         enabled=True):
    repository.save(owner, provider, key, hint, enabled, NOW)


def test_get_is_none_before_anything_is_saved(setup) -> None:
    _, ai, _, _ = setup

    assert ai.get("owner-a") is None
    view = ai.public_view("owner-a", TODAY)
    assert (view.provider, view.key_hint, view.enabled, view.key_status) == (
        None, None, False, None,
    )
    assert (view.requests_today, view.daily_limit, view.usage_day) == (0, 50, TODAY)


def test_save_stores_settings_and_bumps_version(setup) -> None:
    _, ai, _, _ = setup

    save(ai)
    first = ai.get("owner-a")
    save(ai, provider=AiProvider.GEMINI, key={**KEY, "ciphertext": "second"}, hint="9999")
    second = ai.get("owner-a")

    assert first.provider is AiProvider.OPENAI
    assert first.encrypted_key == KEY
    assert (first.key_hint, first.enabled, first.key_status) == ("1234", True, "ok")
    assert first.version == 1
    assert first.consent_at == NOW
    assert second.provider is AiProvider.GEMINI
    assert second.encrypted_key["ciphertext"] == "second"
    assert second.version == 2


def test_replacing_key_resets_invalid_status(setup) -> None:
    client, ai, _, _ = setup
    save(ai)
    client.update_item(
        TableName="Users",
        Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET ai_key_status = :s",
        ExpressionAttributeValues={":s": {"S": "invalid"}},
    )

    assert ai.get("owner-a").key_status == "invalid"
    save(ai, key={**KEY, "ciphertext": "new"})

    assert ai.get("owner-a").key_status == "ok"


def test_toggle_without_key_keeps_key_and_bumps_version(setup) -> None:
    _, ai, _, _ = setup
    save(ai)

    ai.save("owner-a", AiProvider.OPENAI, None, None, False, NOW)

    stored = ai.get("owner-a")
    assert stored.enabled is False
    assert stored.encrypted_key == KEY
    assert stored.key_hint == "1234"
    assert stored.version == 2


def test_update_without_key_requires_existing_key_and_same_provider(setup) -> None:
    _, ai, _, _ = setup

    with pytest.raises(AiKeyRequired):
        ai.save("owner-a", AiProvider.OPENAI, None, None, True, NOW)
    save(ai)
    with pytest.raises(AiKeyRequired):
        ai.save("owner-a", AiProvider.GEMINI, None, None, True, NOW)

    assert ai.get("owner-a").provider is AiProvider.OPENAI
    assert ai.get("owner-a").version == 1


def test_delete_removes_key_bumps_version_and_is_idempotent(setup) -> None:
    client, ai, _, _ = setup
    save(ai)

    ai.delete("owner-a")
    ai.delete("owner-a")
    ai.delete("owner-never-saved")

    assert ai.get("owner-a") is None
    item = client.get_item(TableName="Users", Key={"owner_id": {"S": "owner-a"}})["Item"]
    assert "ai_encrypted_key" not in item and "ai_key_hint" not in item
    assert item["ai_settings_version"] == {"N": "3"}
    assert "Item" not in client.get_item(
        TableName="Users", Key={"owner_id": {"S": "owner-never-saved"}}
    )


def test_hint_is_last_four_characters_and_view_hides_secrets(setup) -> None:
    _, ai, _, _ = setup
    save(ai, hint=SECRET[-4:])

    view = ai.public_view("owner-a", TODAY)

    assert view.key_hint == "1234"
    dumped = view.model_dump_json()
    assert SECRET not in dumped
    assert "ciphertext" not in dumped and "opaque-ai" not in dumped
    assert (view.provider, view.enabled, view.key_status) == (AiProvider.OPENAI, True, "ok")


def test_daily_counter_survives_delete_and_is_day_scoped(setup) -> None:
    client, ai, _, _ = setup
    save(ai)
    client.update_item(
        TableName="Users",
        Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET ai_usage_day = :d, ai_requests_today = :n",
        ExpressionAttributeValues={":d": {"S": TODAY}, ":n": {"N": "7"}},
    )

    ai.delete("owner-a")

    assert ai.public_view("owner-a", TODAY).requests_today == 7
    tomorrow = ai.public_view("owner-a", "2026-10-21")
    assert (tomorrow.requests_today, tomorrow.usage_day) == (0, "2026-10-21")


def test_ai_fields_survive_gmail_connect_disconnect_and_reconnect(setup) -> None:
    _, ai, gmail, _ = setup
    save(ai)

    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    assert ai.get("owner-a").encrypted_key == KEY
    gmail.disconnect("owner-a")
    assert ai.get("owner-a").encrypted_key == KEY
    gmail.save_connection("owner-a", 2, "a@example.test", TOKEN, NOW)

    stored = ai.get("owner-a")
    assert stored.encrypted_key == KEY and stored.version == 1
    assert gmail.get_connection("owner-a").generation == 3


def test_ai_settings_before_gmail_do_not_enable_sync_or_dispatch(setup) -> None:
    client, ai, gmail, syncs = setup
    save(ai)
    publisher = Publisher()

    from app.repositories.dynamodb_sync import SyncUnavailable

    with pytest.raises(SyncUnavailable):
        syncs.start("owner-a", "run-1", NOW)
    dispatch_daily(client, syncs, publisher, "Users", "queue", NOW)

    assert publisher.messages == []
    assert gmail.get_connection("owner-a").email is None
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    assert syncs.start("owner-a", "run-1", NOW).generation == 1


def test_owners_are_isolated(setup) -> None:
    _, ai, _, _ = setup
    save(ai, owner="owner-a")

    assert ai.get("owner-b") is None
    ai.delete("owner-b")
    assert ai.get("owner-a").encrypted_key == KEY
