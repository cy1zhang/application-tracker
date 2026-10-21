from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from app.domain.account import AccountState
from app.domain.ai import AiProvider
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.account_deletion import (
    AccountDeleter,
    DeletionResult,
    PhaseFailure,
    StaleLease,
)
from app.services.google_oauth import OAuthError

NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)
FAR = float("inf")
TOKEN = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}
TABLES = {
    "Applications": ("application_id", "app"),
    "Events": ("event_id", "evt"),
    "SyncRuns": ("run_id", "run"),
}
MUTATIONS = {"batch_write_item", "put_item", "update_item", "delete_item"}


class Cipher:
    def decrypt_refresh_token(self, owner_id, encrypted):
        return f"plain-for-{owner_id}"


class Cognito:
    def __init__(self, error_code=None):
        self.calls = []
        self.error_code = error_code
        self.on_call = None

    def admin_delete_user(self, *, UserPoolId, Username):
        if self.on_call:
            self.on_call()
        self.calls.append((UserPoolId, Username))
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code}}, "AdminDeleteUser")


class Crash(Exception):
    pass


class Proxy:
    """Delegates to a boto client; optionally crashes on the nth mutation."""

    def __init__(self, client):
        self._client = client
        self.crash_at = None
        self.mutations = 0
        self.before_verify = None

    def __getattr__(self, name):
        target = getattr(self._client, name)
        if name in MUTATIONS:
            def wrapped(**kwargs):
                self.mutations += 1
                if self.crash_at == self.mutations:
                    raise Crash
                return target(**kwargs)
            return wrapped
        if name == "query" and self.before_verify:
            def query(**kwargs):
                if kwargs.get("Limit") == 1 and self.before_verify:
                    hook, self.before_verify = self.before_verify, None
                    hook()
                return target(**kwargs)
            return query
        return target


class Env:
    pass


@pytest.fixture
def env():
    with mock_aws():
        e = Env()
        e.raw = raw = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            raw, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        e.client = Proxy(raw)
        e.accounts = DynamoAccountRepository(client=e.client, users_table="Users")
        e.gmail = DynamoGmailRepository(
            client=raw, users_table="Users", oauth_states_table="OAuthStates"
        )
        e.ai = DynamoAiSettingsRepository(client=raw, users_table="Users")
        e.cognito = Cognito()
        e.revoked = []

        def revoke(token):
            user = raw.get_item(
                TableName="Users", Key={"owner_id": {"S": "owner-a"}}
            ).get("Item", {})
            assert "encrypted_token" in user  # never after the profile is wiped
            e.revoked.append(token)

        e.revoke = revoke
        e.page_size = 10
        e.sleeps = []
        e.deleter = lambda **kw: AccountDeleter(
            client=e.client, accounts=e.accounts, cipher=Cipher(), cognito=e.cognito,
            user_pool_id="pool", users_table="Users", applications_table="Applications",
            events_table="Events", sync_runs_table="SyncRuns",
            oauth_states_table="OAuthStates", revoke=kw.get("revoke", e.revoke),
            page_size=e.page_size, sleep=e.sleeps.append,
            monotonic=kw.get("monotonic", lambda: 0.0),
        )
        yield e


def seed(e, owner, events=120, apps=3, runs=2, states=2):
    for table, (key, prefix) in TABLES.items():
        n = {"Applications": apps, "Events": events, "SyncRuns": runs}[table]
        for i in range(n):
            e.raw.put_item(TableName=table, Item={
                "owner_id": {"S": owner}, key: {"S": f"{prefix}-{i:04d}"},
                "payload": {"S": "synthetic"},
            })
    # A soft-deleted application and a message tombstone
    e.raw.put_item(TableName="Applications", Item={
        "owner_id": {"S": owner}, "application_id": {"S": "gone"},
        "deleted_at": {"S": "2026-10-01"},
    })
    e.raw.put_item(TableName="Events", Item={
        "owner_id": {"S": owner}, "event_id": {"S": "gmail#m1"},
    })
    for i in range(states):
        e.raw.put_item(TableName="OAuthStates", Item={
            "state_hash": {"S": f"{owner}-state-{i}"}, "owner_id": {"S": owner},
        })
    e.gmail.save_connection(owner, 0, f"{owner}@example.test", TOKEN, NOW)
    e.ai.save(owner, AiProvider.OPENAI, TOKEN, "1234", True, NOW)


def start(e, owner="owner-a", lease="lease-1", username="name-a"):
    e.accounts.begin_deletion(owner, username, NOW)
    assert e.accounts.claim_deletion(owner, lease, NOW) is not None


def rows(e, owner):
    out = {}
    for table in ("Applications", "Events", "SyncRuns", "Users"):
        pages = e.raw.get_paginator("scan").paginate(
            TableName=table,
            FilterExpression="owner_id = :o",
            ExpressionAttributeValues={":o": {"S": owner}},
        )
        out[table] = sorted(
            str(sorted(i.items())) for p in pages for i in p["Items"]
        )
    states = e.raw.scan(TableName="OAuthStates")["Items"]
    out["OAuthStates"] = [s for s in states if s["owner_id"]["S"] == owner]
    return out


def user(e, owner="owner-a"):
    return e.raw.get_item(
        TableName="Users", Key={"owner_id": {"S": owner}}, ConsistentRead=True
    ).get("Item", {})


def test_full_deletion_leaves_only_tombstone(env) -> None:
    seed(env, "owner-a")
    start(env)

    result = env.deleter().run("owner-a", "lease-1", FAR)

    assert result == DeletionResult.DONE
    remaining = rows(env, "owner-a")
    for table in ("Applications", "Events", "SyncRuns", "OAuthStates"):
        assert remaining[table] == []
    tomb = user(env)
    assert set(tomb) == {"owner_id", "deletion_state", "deleted_at", "expires_at"}
    assert env.accounts.get_status("owner-a").state == AccountState.DELETED
    assert env.revoked == ["plain-for-owner-a"]
    assert env.cognito.calls == [("pool", "name-a")]
    assert env.sleeps == []


def test_revoke_failure_does_not_stop_deletion(env) -> None:
    seed(env, "owner-a")
    start(env)

    def failing(token):
        raise OAuthError("private provider detail")

    assert env.deleter(revoke=failing).run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert env.accounts.get_status("owner-a").state == AccountState.DELETED


def test_missing_token_skips_revoke(env) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    env.accounts.claim_deletion("owner-a", "lease-1", NOW)

    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert env.revoked == []


def test_cognito_runs_only_after_everything_is_empty(env) -> None:
    seed(env, "owner-a")
    start(env)
    observed = []

    def check():
        observed.append(rows(env, "owner-a"))
        profile = user(env)
        observed.append(profile)

    env.cognito.on_call = check
    env.deleter().run("owner-a", "lease-1", FAR)

    snapshot, profile = observed
    assert all(snapshot[t] == [] for t in ("Applications", "Events", "SyncRuns", "OAuthStates"))
    assert "email" not in profile and "encrypted_token" not in profile
    assert "ai_encrypted_key" not in str(profile) and "provider" not in profile


def test_user_not_found_is_success(env) -> None:
    env.cognito.error_code = "UserNotFoundException"
    seed(env, "owner-a")
    start(env)

    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert len(env.cognito.calls) == 1


def test_other_cognito_error_fails_phase_and_resumes(env) -> None:
    seed(env, "owner-a")
    start(env)
    env.cognito.error_code = "InternalErrorException"

    with pytest.raises(PhaseFailure) as info:
        env.deleter().run("owner-a", "lease-1", FAR)

    assert info.value.phase == "delete_login"
    assert info.value.category == "ClientError"
    assert env.accounts.get_status("owner-a").state == AccountState.DELETING
    env.cognito.error_code = None
    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert env.revoked == ["plain-for-owner-a"]  # not repeated


def test_cognito_skipped_without_stored_username(env) -> None:
    seed(env, "owner-a")
    start(env, username=None)

    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert env.cognito.calls == []


def test_unprocessed_items_are_retried_with_bounded_backoff(env) -> None:
    seed(env, "owner-a", events=30)
    start(env)
    real = env.client._client.batch_write_item
    state = {"calls": 0}

    def flaky(**kwargs):
        state["calls"] += 1
        if state["calls"] <= 2:
            table, requests = next(iter(kwargs["RequestItems"].items()))
            return {"UnprocessedItems": {table: requests}}
        return real(**kwargs)

    env.client._client.batch_write_item = flaky
    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert len(env.sleeps) == 2
    assert env.sleeps[1] > env.sleeps[0]


def test_persistent_unprocessed_items_raise_after_bounded_retries(env) -> None:
    seed(env, "owner-a", events=3)
    start(env)

    def stuck(**kwargs):
        table, requests = next(iter(kwargs["RequestItems"].items()))
        return {"UnprocessedItems": {table: requests}}

    env.client._client.batch_write_item = stuck
    with pytest.raises(PhaseFailure) as info:
        env.deleter().run("owner-a", "lease-1", FAR)

    assert info.value.phase == "delete_partitions"
    assert 0 < len(env.sleeps) <= 10


def test_batches_never_exceed_25(env) -> None:
    seed(env, "owner-a", events=100)
    start(env)
    sizes = []
    real = env.client._client.batch_write_item

    def spy(**kwargs):
        sizes.extend(len(v) for v in kwargs["RequestItems"].values())
        return real(**kwargs)

    env.client._client.batch_write_item = spy
    env.page_size = 60
    env.deleter().run("owner-a", "lease-1", FAR)
    assert sizes and max(sizes) <= 25


def test_verification_sweep_catches_row_inserted_after_first_pass(env) -> None:
    seed(env, "owner-a")
    start(env)

    def late():
        env.raw.put_item(TableName="Events", Item={
            "owner_id": {"S": "owner-a"}, "event_id": {"S": "late"},
        })

    env.client.before_verify = late
    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert rows(env, "owner-a")["Events"] == []


# --- resumability ------------------------------------------------------------


def test_crash_at_every_mutation_then_rerun_reaches_same_end_state(env) -> None:
    seed(env, "owner-a", events=45)
    start(env)
    total = env.client.mutations
    env.deleter().run("owner-a", "lease-1", FAR)
    reference_mutations = env.client.mutations - total
    assert reference_mutations > 6

    for crash_point in range(1, reference_mutations + 1):
        env.revoked.clear()
        env.cognito.calls.clear()
        # reset: remove tombstone, reseed
        env.raw.delete_item(TableName="Users", Key={"owner_id": {"S": "owner-a"}})
        seed(env, "owner-a", events=45)
        start(env)
        env.client.mutations = 0
        env.client.crash_at = crash_point
        with pytest.raises(PhaseFailure):
            env.deleter().run("owner-a", "lease-1", FAR)
        env.client.crash_at = None

        assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE

        remaining = rows(env, "owner-a")
        assert all(
            remaining[t] == []
            for t in ("Applications", "Events", "SyncRuns", "OAuthStates")
        )
        assert set(user(env)) == {"owner_id", "deletion_state", "deleted_at", "expires_at"}
        assert 1 <= len(env.revoked) <= 2
        assert 1 <= len(env.cognito.calls) <= 2


def test_rerun_after_done_does_no_work(env) -> None:
    seed(env, "owner-a")
    start(env)
    env.deleter().run("owner-a", "lease-1", FAR)

    assert env.deleter().run("owner-a", "lease-1", FAR) == DeletionResult.DONE
    assert len(env.revoked) == 1 and len(env.cognito.calls) == 1


def test_expired_lease_is_taken_over_and_old_holder_cannot_advance(env) -> None:
    seed(env, "owner-a")
    start(env, lease="old")
    later = NOW + timedelta(minutes=11)
    assert env.accounts.claim_deletion("owner-a", "new", later) is not None

    with pytest.raises(StaleLease):
        env.deleter().run("owner-a", "old", FAR)
    assert env.deleter().run("owner-a", "new", FAR) == DeletionResult.DONE


def test_deadline_returns_continue_with_progress_then_finishes(env) -> None:
    seed(env, "owner-a", events=60)
    start(env)
    deleter = env.deleter()

    results = []
    for _ in range(50):
        result = deleter.run("owner-a", "lease-1", 0.0)  # deadline already passed
        results.append(result)
        if result == DeletionResult.DONE:
            break

    assert results[0] == DeletionResult.CONTINUE
    assert results[-1] == DeletionResult.DONE
    assert rows(env, "owner-a")["Events"] == []


# --- isolation -----------------------------------------------------------------


def test_other_owners_are_untouched_in_every_table(env) -> None:
    for owner in ("owner-a", "owner-b", "owner-ab"):
        seed(env, owner, events=25)
    before = {o: rows(env, o) for o in ("owner-b", "owner-ab")}
    start(env)

    env.deleter().run("owner-a", "lease-1", FAR)

    assert {o: rows(env, o) for o in ("owner-b", "owner-ab")} == before
    assert before["owner-b"]["OAuthStates"] and before["owner-ab"]["Users"]
