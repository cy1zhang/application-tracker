from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from app.domain.ai import AiProvider
from app.repositories.dynamodb_ai import (
    DAILY_LIMIT,
    AiBudgetExhausted,
    AiKeyInvalid,
    AiSettingsChanged,
    DynamoAiSettingsRepository,
)
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository, StaleSyncRun
from app.repositories.dynamodb_tables import create_tables

NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)
TOKEN = {"ciphertext": "opaque", "nonce": "opaque", "encrypted_data_key": "opaque"}
KEY = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}
MODELS = {AiProvider.OPENAI: "model-x"}


class Run:
    def __init__(self, generation, run_id):
        self.generation = generation
        self.run_id = run_id


def usage(client, owner="owner-a"):
    item = client.get_item(
        TableName="Users", Key={"owner_id": {"S": owner}}, ConsistentRead=True
    )["Item"]
    return item.get("ai_usage_day", {}).get("S"), int(
        item.get("ai_requests_today", {"N": "0"})["N"]
    )


class SeedingClient:
    """Delegates to a real client; simulates another worker starting the day."""

    def __init__(self, inner):
        self._inner = inner
        self.seeded = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def update_item(self, **kwargs):
        if not self.seeded and kwargs["UpdateExpression"].startswith("SET ai_usage_day"):
            self.seeded = True
            self._inner.update_item(
                TableName="Users",
                Key=kwargs["Key"],
                UpdateExpression="SET ai_usage_day = :d, ai_requests_today = :n",
                ExpressionAttributeValues={
                    ":d": {"S": "2026-10-20"}, ":n": {"N": "1"},
                },
            )
        return self._inner.update_item(**kwargs)


@pytest.fixture
def env():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            sync_runs_table="SyncRuns",
        )
        ai = DynamoAiSettingsRepository(client=client, users_table="Users")
        gmail = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        syncs = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        yield client, ai, gmail, syncs


def ready(env, owner="owner-a", enabled=True):
    _, ai, gmail, syncs = env
    ai.save(owner, AiProvider.OPENAI, KEY, "1234", enabled, NOW)
    gmail.save_connection(owner, 0, f"{owner}@example.test", TOKEN, NOW)
    run = syncs.start(owner, f"run-{owner}", NOW)
    assert syncs.claim(owner, run.run_id, run.generation, f"lease-{owner}", NOW)
    return Run(run.generation, run.run_id)


def reserve(ai, run, owner="owner-a", version=1, now=NOW):
    ai.reserve_request(
        owner, generation=run.generation, run_id=run.run_id,
        settings_version=version, now=now,
    )


def test_fifty_succeed_and_fifty_first_is_exhausted(env) -> None:
    client, ai, _, _ = env
    run = ready(env)

    for _ in range(DAILY_LIMIT):
        reserve(ai, run)
    with pytest.raises(AiBudgetExhausted):
        reserve(ai, run)

    assert DAILY_LIMIT == 50
    assert usage(client) == ("2026-10-20", 50)


def test_counter_rolls_over_on_next_utc_day(env) -> None:
    client, ai, _, _ = env
    run = ready(env)
    for _ in range(DAILY_LIMIT):
        reserve(ai, run)

    reserve(ai, run, now=NOW + timedelta(days=1))
    assert usage(client) == ("2026-10-21", 1)
    reserve(ai, run, now=NOW + timedelta(days=1))
    assert usage(client) == ("2026-10-21", 2)


def test_utc_day_boundary_uses_now_argument(env) -> None:
    client, ai, _, _ = env
    run = ready(env)

    reserve(ai, run, now=datetime(2026, 10, 20, 23, 59, 59, tzinfo=UTC))
    reserve(ai, run, now=datetime(2026, 10, 21, 0, 0, 0, tzinfo=UTC))

    assert usage(client) == ("2026-10-21", 1)


def test_owners_have_independent_counters(env) -> None:
    client, ai, _, _ = env
    run_a = ready(env, "owner-a")
    run_b = ready(env, "owner-b")
    for _ in range(DAILY_LIMIT):
        reserve(ai, run_a)

    reserve(ai, run_b, owner="owner-b")

    assert usage(client, "owner-a") == ("2026-10-20", 50)
    assert usage(client, "owner-b") == ("2026-10-20", 1)


def test_disabled_key_raises_settings_changed_without_increment(env) -> None:
    client, ai, _, _ = env
    run = ready(env, enabled=False)

    with pytest.raises(AiSettingsChanged):
        reserve(ai, run)

    assert usage(client) == (None, 0)


def test_deleted_key_raises_settings_changed_without_increment(env) -> None:
    client, ai, _, _ = env
    run = ready(env)
    ai.delete("owner-a")

    with pytest.raises(AiSettingsChanged):
        reserve(ai, run)

    assert usage(client) == (None, 0)


def test_never_configured_owner_raises_settings_changed(env) -> None:
    _, ai, gmail, syncs = env
    gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    run = syncs.start("owner-a", "run-1", NOW)
    syncs.claim("owner-a", "run-1", run.generation, "lease", NOW)

    with pytest.raises(AiSettingsChanged):
        reserve(ai, Run(run.generation, "run-1"))


def test_version_mismatch_after_replace_raises_settings_changed(env) -> None:
    client, ai, _, _ = env
    run = ready(env)
    ai.save("owner-a", AiProvider.OPENAI, KEY, "9999", True, NOW)

    with pytest.raises(AiSettingsChanged):
        reserve(ai, run, version=1)

    assert usage(client) == (None, 0)
    reserve(ai, run, version=2)
    assert usage(client) == ("2026-10-20", 1)


def test_invalid_key_raises_key_invalid_without_increment(env) -> None:
    client, ai, _, _ = env
    run = ready(env)
    ai.mark_key_invalid("owner-a", 1)

    with pytest.raises(AiKeyInvalid):
        reserve(ai, run)

    assert usage(client) == (None, 0)


def test_changed_generation_or_run_raises_stale_sync_run(env) -> None:
    client, ai, _, _ = env
    run = ready(env)

    with pytest.raises(StaleSyncRun):
        reserve(ai, Run(run.generation + 1, run.run_id))
    with pytest.raises(StaleSyncRun):
        reserve(ai, Run(run.generation, "other-run"))

    assert usage(client) == (None, 0)


def test_disconnected_gmail_raises_stale_sync_run(env) -> None:
    client, ai, gmail, _ = env
    run = ready(env)
    gmail.disconnect("owner-a")

    with pytest.raises(StaleSyncRun):
        reserve(ai, run)

    assert usage(client) == (None, 0)


def test_precedence_stale_then_changed_then_invalid_then_budget(env) -> None:
    client, ai, _, _ = env
    run = ready(env)
    for _ in range(DAILY_LIMIT):
        reserve(ai, run)
    ai.mark_key_invalid("owner-a", 1)

    # Everything wrong at once: exhausted + invalid + stale version + stale run.
    stale = Run(run.generation + 1, run.run_id)
    with pytest.raises(StaleSyncRun):
        reserve(ai, stale, version=0)
    with pytest.raises(AiSettingsChanged):
        reserve(ai, run, version=0)
    with pytest.raises(AiKeyInvalid):
        reserve(ai, run, version=1)

    ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", True, NOW)  # ok again, version 2
    with pytest.raises(AiBudgetExhausted):
        reserve(ai, run, version=2)
    assert usage(client) == ("2026-10-20", 50)


def test_mark_key_invalid_with_stale_version_is_noop(env) -> None:
    _, ai, _, _ = env
    ready(env)
    ai.save("owner-a", AiProvider.OPENAI, KEY, "9999", True, NOW)

    ai.mark_key_invalid("owner-a", 1)
    assert ai.get("owner-a").key_status == "ok"

    ai.mark_key_invalid("owner-a", 2)
    assert ai.get("owner-a").key_status == "invalid"


def test_mark_key_invalid_does_not_create_or_bump(env) -> None:
    client, ai, _, _ = env

    ai.mark_key_invalid("nobody", 1)

    assert "Item" not in client.get_item(
        TableName="Users", Key={"owner_id": {"S": "nobody"}}
    )
    ready(env)
    ai.mark_key_invalid("owner-a", 1)
    assert ai.get("owner-a").version == 1


def test_first_request_race_retries_into_same_day_path(env) -> None:
    client, _, _, _ = env
    racing = SeedingClient(client)
    ai = DynamoAiSettingsRepository(client=racing, users_table="Users")
    run = ready(env)

    reserve(ai, run)

    assert racing.seeded
    assert usage(client) == ("2026-10-20", 2)


def test_race_retry_at_limit_raises_budget_exhausted(env) -> None:
    client, _, _, _ = env
    racing = SeedingClient(client)
    ai = DynamoAiSettingsRepository(client=racing, users_table="Users")
    run = ready(env)

    def seed_at_limit(**kwargs):
        if kwargs["UpdateExpression"].startswith("SET ai_usage_day") and not racing.seeded:
            racing.seeded = True
            client.update_item(
                TableName="Users",
                Key=kwargs["Key"],
                UpdateExpression="SET ai_usage_day = :d, ai_requests_today = :n",
                ExpressionAttributeValues={
                    ":d": {"S": "2026-10-20"}, ":n": {"N": "50"},
                },
            )
        return client.update_item(**kwargs)

    racing.update_item = seed_at_limit
    with pytest.raises(AiBudgetExhausted):
        reserve(ai, run)
    assert usage(client) == ("2026-10-20", 50)


def test_unexpected_client_error_propagates() -> None:
    class Broken:
        def update_item(self, **kwargs):
            raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException"}}, "x")

    ai = DynamoAiSettingsRepository(client=Broken(), users_table="Users")
    with pytest.raises(ClientError):
        reserve(ai, Run(1, "r"))


def test_error_messages_carry_no_owner_or_key_data(env) -> None:
    _, ai, _, _ = env
    run = ready(env, "owner-secret-id")
    ai.mark_key_invalid("owner-secret-id", 1)

    with pytest.raises(AiKeyInvalid) as caught:
        reserve(ai, run, owner="owner-secret-id")

    assert "owner-secret-id" not in str(caught.value)
    assert "owner-secret-id" not in repr(caught.value)


def test_load_runtime_returns_runtime_only_when_usable(env) -> None:
    _, ai, _, _ = env
    assert ai.load_runtime("owner-a", MODELS) is None

    ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", True, NOW)
    runtime = ai.load_runtime("owner-a", MODELS)
    assert runtime.provider is AiProvider.OPENAI
    assert runtime.model == "model-x"
    assert runtime.settings_version == 1
    assert runtime.encrypted_key == KEY

    assert ai.load_runtime("owner-a", {}) is None
    assert ai.load_runtime("owner-a", {AiProvider.OPENAI: "  "}) is None
    assert ai.load_runtime("owner-a", {AiProvider.GEMINI: "g"}) is None
    assert ai.load_runtime("owner-b", MODELS) is None

    ai.mark_key_invalid("owner-a", 1)
    assert ai.load_runtime("owner-a", MODELS) is None

    ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", False, NOW)
    assert ai.load_runtime("owner-a", MODELS) is None
    ai.save("owner-a", AiProvider.OPENAI, None, None, True, NOW)
    assert ai.load_runtime("owner-a", MODELS).settings_version == 3
