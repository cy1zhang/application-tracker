import json
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.domain.account import AccountDeleting
from app.domain.ai import AiProvider
from app.domain.applications import ApplicationCreate, ApplicationStatus
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import (
    DynamoSyncRepository,
    StaleSyncRun,
    SyncUnavailable,
)
from app.repositories.dynamodb_tables import create_tables
from app.repositories.gmail import StaleGeneration
from app.services.email_rules import RuleDecision
from app.services.gmail_connection import GmailConnectionService
from app.services.gmail_message import ParsedGmailMessage
from app.services.sync_dispatch import dispatch_daily, recover_unfinished

NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)
TODAY = "2026-10-20"
TOKEN = {"ciphertext": "opaque", "nonce": "opaque", "encrypted_data_key": "opaque"}
KEY = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}


class Publisher:
    def __init__(self):
        self.messages = []

    def send_message(self, *, QueueUrl, MessageBody):
        self.messages.append(json.loads(MessageBody))


class Env:
    pass


@pytest.fixture
def env():
    with mock_aws():
        e = Env()
        e.client = client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        e.accounts = DynamoAccountRepository(client=client, users_table="Users")
        e.gmail = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        e.ai = DynamoAiSettingsRepository(client=client, users_table="Users")
        e.syncs = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        e.applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        e.publisher = Publisher()
        yield e


def connect(e, owner="owner-a", with_ai=False):
    e.gmail.save_connection(owner, 0, f"{owner}@example.test", TOKEN, NOW)
    if with_ai:
        e.ai.save(owner, AiProvider.OPENAI, KEY, "1234", True, NOW)


def count(client, table):
    return client.scan(TableName=table)["Count"]


def user(e, owner="owner-a"):
    return e.client.get_item(
        TableName="Users", Key={"owner_id": {"S": owner}}, ConsistentRead=True
    ).get("Item", {})


def message():
    return ParsedGmailMessage(
        message_id="msg-1", thread_id="t-1", received_at=NOW, subject="Update",
        sender="jobs@example.test", text="synthetic body",
    )


# (a) worker mid-page when deletion begins
def test_worker_commit_after_begin_writes_nothing(env) -> None:
    connect(env)
    run = env.syncs.start("owner-a", "run-1", NOW)
    env.applications.create(
        owner_id="owner-a",
        payload=ApplicationCreate(
            company="Acme", role="Software Engineer Intern", status="applied"
        ),
        application_id="app-1", event_id="create-1", occurred_at=NOW,
    )
    emails = DynamoEmailRepository(
        client=env.client, applications_table="Applications", events_table="Events",
        users_table="Users", guard_generation=run.generation, guard_run_id=run.run_id,
    )
    events_before = count(env.client, "Events")
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    with pytest.raises(StaleSyncRun):
        emails.record(
            owner_id="owner-a", message=message(),
            decision=RuleDecision(
                outcome="applied", reason="explicit_event", application_id="app-1",
                status=ApplicationStatus.INTERVIEWING, evidence="invite you to interview",
            ),
            application_version=1,
        )
    with pytest.raises(StaleSyncRun):
        emails.record(
            owner_id="owner-a", message=message(),
            decision=RuleDecision(outcome="ignored", reason="not_recruiting"),
        )

    assert count(env.client, "Events") == events_before


# (b) queued job delivered after begin
def test_claim_after_begin_is_stale(env) -> None:
    connect(env)
    run = env.syncs.start("owner-a", "run-1", NOW)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    with pytest.raises(StaleSyncRun):
        env.syncs.claim("owner-a", "run-1", run.generation, "lease", NOW)

    status = env.client.get_item(
        TableName="SyncRuns",
        Key={"owner_id": {"S": "owner-a"}, "run_id": {"S": "run-1"}},
    )["Item"]["status"]["S"]
    assert status == "pending"


def test_sync_handler_acknowledges_job_after_begin(env, monkeypatch) -> None:
    from app import sync_handler

    connect(env)
    run = env.syncs.start("owner-a", "run-1", NOW)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    for key in ("APPLICATIONS_TABLE", "EVENTS_TABLE", "USERS_TABLE", "SYNC_QUEUE_URL"):
        monkeypatch.setenv(key, key)
    monkeypatch.setattr(sync_handler, "_dependencies", lambda: {
        "client": env.client, "syncs": env.syncs,
    })

    def process(*, owner_id, run_id, generation, lease_id, now, syncs, **_):
        # The real worker's first step is the claim.
        syncs.claim(owner_id, run_id, generation, lease_id, now)

    monkeypatch.setattr(sync_handler, "process_sync_page", process)
    event = {"Records": [{
        "messageId": "sqs-1",
        "body": json.dumps({
            "owner_id": "owner-a", "run_id": "run-1", "generation": run.generation,
        }),
        "attributes": {"ApproximateReceiveCount": "1"},
    }]}

    assert sync_handler.handler(event, None) == {"batchItemFailures": []}


# (c) recovery
def test_recovery_fails_run_of_deleting_owner_without_publishing(env) -> None:
    connect(env)
    env.syncs.start("owner-a", "run-1", NOW)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    recover_unfinished(
        env.client, env.publisher, "Users", "SyncRuns", "queue", NOW, page_size=1
    )

    assert env.publisher.messages == []
    status = env.client.get_item(
        TableName="SyncRuns",
        Key={"owner_id": {"S": "owner-a"}, "run_id": {"S": "run-1"}},
    )["Item"]["status"]["S"]
    assert status == "failed"


# (d) dispatcher scan and start
def test_dispatch_skips_deleting_owner(env) -> None:
    connect(env, "owner-a")
    connect(env, "owner-b")
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    dispatch_daily(env.client, env.syncs, env.publisher, "Users", "queue", NOW)

    assert [m["owner_id"] for m in env.publisher.messages] == ["owner-b"]
    assert count(env.client, "SyncRuns") == 1


def test_start_after_flag_appears_between_scan_and_start(env, monkeypatch) -> None:
    connect(env)
    stale_user = env.syncs._get_user("owner-a")
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    monkeypatch.setattr(env.syncs, "_get_user", lambda owner_id: stale_user)

    with pytest.raises(SyncUnavailable):
        env.syncs.start("owner-a", "run-1", NOW)

    assert count(env.client, "SyncRuns") == 0
    assert "active_run_id" not in user(env)


def test_start_on_deleting_owner_is_unavailable(env) -> None:
    connect(env)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    with pytest.raises(SyncUnavailable):
        env.syncs.start("owner-a", "run-1", NOW)


# (e) AI reservation
def test_reserve_request_after_begin_is_rejected(env) -> None:
    connect(env, with_ai=True)
    run = env.syncs.start("owner-a", "run-1", NOW)
    version = env.ai.get("owner-a").version
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    with pytest.raises(StaleSyncRun):
        env.ai.reserve_request(
            "owner-a", generation=run.generation, run_id="run-1",
            settings_version=version, now=NOW,
        )

    assert "ai_requests_today" not in user(env)
    assert "ai_usage_day" not in user(env)


# (f) AI settings and Gmail writers
def test_ai_save_and_delete_after_begin_raise_and_do_not_resurrect(env) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    before = user(env)

    with pytest.raises(AccountDeleting):
        env.ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", True, NOW)
    with pytest.raises(AccountDeleting):
        env.ai.save("owner-a", AiProvider.OPENAI, None, None, False, NOW)
    with pytest.raises(AccountDeleting):
        env.ai.delete("owner-a")

    assert user(env) == before
    assert env.ai.get("owner-a") is None


def test_ai_delete_of_existing_settings_after_begin_keeps_them_for_sweeper(env) -> None:
    connect(env, with_ai=True)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    with pytest.raises(AccountDeleting):
        env.ai.delete("owner-a")

    assert env.ai.get("owner-a").encrypted_key == KEY


def test_save_connection_and_disconnect_after_begin_raise(env) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    before = user(env)
    current = int(before["generation"]["N"])

    with pytest.raises(StaleGeneration):
        env.gmail.save_connection("owner-a", current, "a@example.test", TOKEN, NOW)
    with pytest.raises(StaleGeneration):
        env.gmail.disconnect("owner-a")

    assert user(env) == before
    assert env.gmail.get_connection("owner-a").email is None


def test_disconnect_of_existing_connection_after_begin_keeps_token(env) -> None:
    connect(env)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    before = user(env)

    with pytest.raises(StaleGeneration):
        env.gmail.disconnect("owner-a")

    assert user(env) == before


def test_save_connection_with_no_generation_record_cannot_resurrect(env) -> None:
    # A tombstoned owner has no generation attribute at all.
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    env.accounts.claim_deletion("owner-a", "lease", NOW)
    env.accounts.finalize("owner-a", "lease", NOW)
    before = user(env)

    with pytest.raises(StaleGeneration):
        env.gmail.save_connection("owner-a", 0, "a@example.test", TOKEN, NOW)
    with pytest.raises(AccountDeleting):
        env.ai.save("owner-a", AiProvider.OPENAI, KEY, "1234", True, NOW)

    assert user(env) == before


# (g) OAuth callback whose state predates begin
class FakeGoogle:
    def exchange_code(self, code):
        return {"access_token": "at", "refresh_token": "rt"}

    def profile_email(self, access_token):
        return "owner-a@example.test"


class FakeCipher:
    def encrypt_refresh_token(self, owner_id, token):
        return TOKEN


def test_callback_whose_state_predates_begin_fails(env) -> None:
    service = GmailConnectionService(env.gmail, FakeCipher(), FakeGoogle())
    env.gmail.create_state(
        "state-hash", "owner-a", "owner-a@example.test", 0, NOW + timedelta(minutes=10)
    )
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    before = user(env)
    stored = env.gmail.consume_state("state-hash", NOW)
    assert stored is not None

    # Generation no longer matches (bumped by begin).
    assert env.gmail.get_connection("owner-a").generation != stored.expected_generation
    # Even a state minted at the current generation cannot save: the flag blocks it.
    with pytest.raises(StaleGeneration):
        env.gmail.save_connection(
            "owner-a", env.gmail.get_connection("owner-a").generation,
            "owner-a@example.test", TOKEN, NOW,
        )
    assert user(env) == before
    assert service is not None


def test_callback_service_returns_false_after_begin(env, monkeypatch) -> None:
    from hashlib import sha256

    service = GmailConnectionService(env.gmail, FakeCipher(), FakeGoogle())
    env.gmail.create_state(
        sha256(b"raw-state").hexdigest(), "owner-a", "owner-a@example.test", 0,
        datetime.now(UTC) + timedelta(minutes=10),
    )
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    before = user(env)

    assert service.callback("raw-state", "code", None) is False
    assert user(env) == before


# Isolation
def test_beginning_deletion_does_not_touch_other_owners(env) -> None:
    for owner in ("owner-a", "owner-ab", "owner-b"):
        connect(env, owner, with_ai=True)
    others_before = {o: user(env, o) for o in ("owner-ab", "owner-b")}

    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    assert {o: user(env, o) for o in others_before} == others_before
    assert env.accounts.get_status("owner-b").state.value == "active"
    assert env.accounts.get_status("owner-ab").state.value == "active"


def test_other_owners_can_still_sync_reserve_save_and_dispatch(env) -> None:
    for owner in ("owner-a", "owner-ab", "owner-b"):
        connect(env, owner, with_ai=True)
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    run = env.syncs.start("owner-b", "run-b", NOW)
    version = env.ai.get("owner-b").version
    env.ai.reserve_request(
        "owner-b", generation=run.generation, run_id="run-b",
        settings_version=version, now=NOW,
    )
    env.ai.save("owner-b", AiProvider.GEMINI, KEY, "9999", True, NOW)
    env.ai.delete("owner-ab")
    env.gmail.disconnect("owner-ab")
    env.gmail.save_connection(
        "owner-ab", env.gmail.get_connection("owner-ab").generation,
        "owner-ab@example.test", TOKEN, NOW,
    )
    dispatch_daily(env.client, env.syncs, env.publisher, "Users", "queue", NOW)

    assert {m["owner_id"] for m in env.publisher.messages} == {"owner-b", "owner-ab"}
    assert int(user(env, "owner-b")["ai_requests_today"]["N"]) == 1
    assert env.syncs.get_latest("owner-a") is None
