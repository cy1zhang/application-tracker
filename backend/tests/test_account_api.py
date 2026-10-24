import json
from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_account_repository,
    get_application_repository,
    get_current_subject,
    get_sync_publisher,
    require_active_account,
)
from app.domain.account import AccountState
from app.main import app
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.account_deletion import AccountDeleter, DeletionResult

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/deletion"
NOW = datetime(2026, 10, 20, 9, tzinfo=UTC)


class RecordingPublisher:
    def __init__(self, fail: bool = False) -> None:
        self.messages: list[dict] = []
        self.fail = fail

    def send_message(self, *, QueueUrl: str, MessageBody: str, DelaySeconds: int) -> None:
        assert QueueUrl == QUEUE_URL
        if self.fail:
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "SendMessage")
        self.messages.append({"body": json.loads(MessageBody), "delay": DelaySeconds})


class Env:
    pass


@pytest.fixture
def env(monkeypatch):
    with mock_aws():
        e = Env()
        e.raw = raw = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            raw, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        e.accounts = DynamoAccountRepository(client=raw, users_table="Users")
        e.apps = DynamoApplicationRepository(
            client=raw, applications_table="Applications", events_table="Events"
        )
        e.publisher = RecordingPublisher()
        app.dependency_overrides[get_account_repository] = lambda: e.accounts
        app.dependency_overrides[get_application_repository] = lambda: e.apps
        app.dependency_overrides[get_sync_publisher] = lambda: e.publisher
        app.dependency_overrides[get_current_subject] = lambda: "owner-a"
        monkeypatch.setenv("DELETION_QUEUE_URL", QUEUE_URL)
        monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
        with TestClient(app) as api:
            e.api = api
            yield e
        app.dependency_overrides.clear()


def as_owner(owner: str) -> None:
    app.dependency_overrides[get_current_subject] = lambda: owner


def user(env, owner="owner-a") -> dict:
    return env.raw.get_item(TableName="Users", Key={"owner_id": {"S": owner}}).get("Item", {})


def rows(env, table, owner="owner-a") -> int:
    return env.raw.query(
        TableName=table, KeyConditionExpression="owner_id = :o",
        ExpressionAttributeValues={":o": {"S": owner}},
    )["Count"]


def create_application(env, owner: str) -> dict:
    as_owner(owner)
    response = env.api.post(
        "/applications", json={"company": "Acme", "role": "Engineer", "status": "applied"}
    )
    assert response.status_code == 201
    return response.json()


def with_claims(claims: dict) -> TestClient:
    async def wrapper(scope, receive, send):
        if scope["type"] == "http":
            scope["aws.event"] = {
                "requestContext": {"authorizer": {"jwt": {"claims": claims}}}
            }
        await app(scope, receive, send)

    return TestClient(wrapper)


# --- authentication ---------------------------------------------------------
def test_account_routes_require_authentication(env) -> None:
    app.dependency_overrides.pop(get_current_subject)

    assert env.api.get("/account").status_code == 401
    assert env.api.request("DELETE", "/account", json={"confirm": "DELETE"}).status_code == 401
    assert user(env) == {}
    assert env.publisher.messages == []


def test_account_routes_reject_user_outside_approved_group(env) -> None:
    app.dependency_overrides.pop(get_current_subject)
    client = with_claims({"sub": "owner-a", "username": "name-a", "cognito:groups": ["other"]})

    assert client.get("/account").status_code == 403
    assert client.request("DELETE", "/account", json={"confirm": "DELETE"}).status_code == 403
    assert user(env) == {}


def test_delete_records_verified_username_claim(env) -> None:
    app.dependency_overrides.pop(get_current_subject)
    client = with_claims(
        {"sub": "owner-a", "username": "name-a", "cognito:groups": ["tracker-users"]}
    )

    response = client.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 202
    assert user(env)["cognito_username"] == {"S": "name-a"}


def test_delete_without_username_claim_stores_none(env) -> None:
    response = env.api.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 202
    assert not user(env).get("cognito_username") or "NULL" in user(env)["cognito_username"]


# --- DELETE /account --------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        {"confirm": "delete"},
        {"confirm": ""},
        {"confirm": "DELETE", "owner_id": "owner-b"},
        {},
        None,
    ],
)
def test_bad_confirmation_is_422_and_changes_nothing(env, body) -> None:
    kwargs = {} if body is None else {"json": body}
    response = env.api.request("DELETE", "/account", **kwargs)

    assert response.status_code == 422
    assert user(env) == {}
    assert env.publisher.messages == []


def test_delete_starts_deletion_and_enqueues_once(env) -> None:
    response = env.api.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 202
    assert response.json() == {"state": "deleting"}
    item = user(env)
    assert item["deletion_state"] == {"S": "deleting"}
    assert item["generation"] == {"N": "1"}
    assert env.publisher.messages == [
        {"body": {"kind": "delete_account", "owner_id": "owner-a"}, "delay": 30}
    ]


def test_retry_while_deleting_is_202_resends_and_does_not_bump(env) -> None:
    env.api.request("DELETE", "/account", json={"confirm": "DELETE"})
    requested = user(env)["deletion_requested_at"]

    response = env.api.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 202
    assert response.json() == {"state": "deleting"}
    assert user(env)["generation"] == {"N": "1"}
    assert user(env)["deletion_requested_at"] == requested
    assert len(env.publisher.messages) == 2


def test_send_failure_still_202_and_state_is_durable(env) -> None:
    env.publisher.fail = True

    response = env.api.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 202
    assert user(env)["deletion_state"] == {"S": "deleting"}


def test_missing_queue_url_is_503_with_no_state_change(env, monkeypatch) -> None:
    monkeypatch.delenv("DELETION_QUEUE_URL")

    response = env.api.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 503
    assert user(env) == {}


def test_deleted_tombstone_is_200_and_sends_nothing(env) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    env.accounts.claim_deletion("owner-a", "lease", NOW)
    env.accounts.finalize("owner-a", "lease", NOW)

    response = env.api.request("DELETE", "/account", json={"confirm": "DELETE"})

    assert response.status_code == 200
    assert response.json() == {"state": "deleted"}
    assert env.publisher.messages == []


# --- GET /account -----------------------------------------------------------
def test_get_account_reports_each_state(env) -> None:
    assert env.api.get("/account").json() == {"state": "active"}
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    assert env.api.get("/account").json() == {"state": "deleting"}
    env.accounts.claim_deletion("owner-a", "lease", NOW)
    env.accounts.finalize("owner-a", "lease", NOW)
    assert env.api.get("/account").json() == {"state": "deleted"}


# --- enforcement ------------------------------------------------------------
GUARDED = [
    ("GET", "/applications", None),
    ("POST", "/applications", {"company": "Acme", "role": "Engineer", "status": "applied"}),
    ("GET", "/applications/x", None),
    ("GET", "/applications/x/events", None),
    ("PATCH", "/applications/x", {"expected_version": 1, "company": "New"}),
    ("DELETE", "/applications/x", None),
    ("POST", "/applications/x/merge", {}),
    ("GET", "/reviews", None),
    ("GET", "/reviews/m/local-ai-input", None),
    ("POST", "/reviews/m/local-ai-suggestion", {}),
    ("POST", "/reviews/m/resolve", {}),
    ("POST", "/ai-queue/backfill", None),
    ("GET", "/ai-queue", None),
    ("GET", "/ai-queue/m/input", None),
    ("POST", "/ai-queue/m/result", {}),
    ("GET", "/messages", None),
    ("GET", "/messages/m/content", None),
    ("GET", "/syncs/latest", None),
    ("POST", "/syncs", None),
    ("GET", "/settings", None),
    ("PUT", "/settings/ai", {"provider": "openai", "enabled": False}),
    ("PUT", "/settings/auto-apply", {"mode": "plan"}),
    ("DELETE", "/settings/ai", None),
    ("GET", "/gmail/connection", None),
    ("POST", "/gmail/connect", None),
    ("DELETE", "/gmail/connection", None),
]


@pytest.mark.parametrize(("method", "path", "body"), GUARDED)
def test_deleting_account_is_locked_out_of_every_router(env, method, path, body) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    before = user(env)

    kwargs = {} if body is None else {"json": body}
    response = env.api.request(method, path, **kwargs)

    assert response.status_code == 423
    assert response.json() == {"detail": "account_deleting"}
    assert user(env) == before
    assert rows(env, "Applications") == 0


@pytest.mark.parametrize(("method", "path", "body"), GUARDED)
def test_deleted_account_gets_410_from_every_router(env, method, path, body) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    env.accounts.claim_deletion("owner-a", "lease", NOW)
    env.accounts.finalize("owner-a", "lease", NOW)
    before = user(env)

    kwargs = {} if body is None else {"json": body}
    response = env.api.request(method, path, **kwargs)

    assert response.status_code == 410
    assert response.json() == {"detail": "account_deleted"}
    assert user(env) == before
    assert rows(env, "Applications") == 0


def test_health_preflight_and_account_stay_reachable_while_deleting(env) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    assert env.api.get("/health").status_code == 200
    assert env.api.options("/applications").status_code == 204
    assert env.api.get("/account").status_code == 200
    assert env.api.request("DELETE", "/account", json={"confirm": "DELETE"}).status_code == 202


def test_oauth_callback_is_not_behind_the_active_account_check(env, monkeypatch) -> None:
    monkeypatch.setenv("FRONTEND_URL", "https://app.example.test")
    env.accounts.begin_deletion("owner-a", "name-a", NOW)

    class NeverCalled:
        def callback(self, state, code, error):
            return False

    from app.api.gmail import get_gmail_service

    app.dependency_overrides[get_gmail_service] = lambda: NeverCalled()
    response = env.api.get("/oauth/google/callback", follow_redirects=False)

    assert response.status_code == 303


ALLOWED_UNGUARDED = {
    ("GET", "/health"),
    ("OPTIONS", "/{path:path}"),
    ("GET", "/oauth/google/callback"),
    ("GET", "/account"),
    ("DELETE", "/account"),
}


DOC_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _effective_routes():
    """Every served route with routers flattened (FastAPI keeps includes lazy)."""
    for route in app.routes:
        if hasattr(route, "effective_route_contexts"):
            yield from route.effective_route_contexts()
        else:
            yield route


def _has_check(route) -> bool:
    if any(d.dependency is require_active_account for d in route.dependencies):
        return True

    def walk(dependant) -> bool:
        return any(
            child.call is require_active_account or walk(child)
            for child in dependant.dependencies
        )

    return walk(route.dependant)


def _keyed(route):
    return [(method, route.path) for method in sorted(route.methods)]


def test_every_authenticated_route_carries_the_active_account_check() -> None:
    unguarded, guarded = set(), set()
    for route in _effective_routes():
        if route.path in DOC_PATHS:
            continue
        has_check = hasattr(route, "dependant") and _has_check(route)
        (guarded if has_check else unguarded).update(_keyed(route))

    assert unguarded == ALLOWED_UNGUARDED
    assert len(guarded) >= 17


def test_guarded_list_covers_every_guarded_route() -> None:
    guarded = {
        key
        for route in _effective_routes()
        if hasattr(route, "dependant") and _has_check(route)
        for key in _keyed(route)
    }
    exercised = {
        (method, path.replace("/x", "/{application_id}").replace("/m/", "/{message_id}/"))
        for method, path, _ in GUARDED
    }
    assert exercised == guarded


# --- cross-user -------------------------------------------------------------
def test_deleting_a_does_not_lock_b(env) -> None:
    create_application(env, "owner-b")
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    as_owner("owner-b")

    assert env.api.get("/account").json() == {"state": "active"}
    assert env.api.get("/applications").status_code == 200
    assert create_application(env, "owner-b")["company"] == "Acme"


def test_b_delete_only_affects_b_and_no_parameter_selects_an_owner(env) -> None:
    as_owner("owner-b")

    response = env.api.request(
        "DELETE", "/account?owner_id=owner-a", json={"confirm": "DELETE"}
    )

    assert response.status_code == 202
    assert user(env, "owner-a") == {}
    assert user(env, "owner-b")["deletion_state"] == {"S": "deleting"}
    assert env.publisher.messages[0]["body"]["owner_id"] == "owner-b"
    as_owner("owner-a")
    assert env.api.get("/account?owner_id=owner-b").json() == {"state": "active"}


def test_stale_token_after_completion_cannot_recreate_data(env) -> None:
    env.accounts.begin_deletion("owner-a", "name-a", NOW)
    env.accounts.claim_deletion("owner-a", "lease", NOW)
    env.accounts.finalize("owner-a", "lease", NOW)

    response = env.api.post(
        "/applications", json={"company": "Acme", "role": "Engineer", "status": "applied"}
    )

    assert response.status_code == 410
    assert rows(env, "Applications") == 0
    assert rows(env, "Events") == 0


# --- end to end -------------------------------------------------------------
class _Cipher:
    def decrypt_refresh_token(self, owner_id, encrypted):
        return "unused"


class _Cognito:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def admin_delete_user(self, *, UserPoolId, Username):
        self.calls.append(Username)


def test_delete_then_deleter_removes_a_and_leaves_b(env) -> None:
    create_application(env, "owner-a")
    create_application(env, "owner-a")
    create_application(env, "owner-b")
    as_owner("owner-a")

    assert env.api.request("DELETE", "/account", json={"confirm": "DELETE"}).status_code == 202
    message = env.publisher.messages[0]["body"]
    cognito = _Cognito()
    deleter = AccountDeleter(
        client=env.raw, accounts=env.accounts, cipher=_Cipher(), cognito=cognito,
        user_pool_id="pool", users_table="Users", applications_table="Applications",
        events_table="Events", sync_runs_table="SyncRuns", oauth_states_table="OAuthStates",
        revoke=lambda token: None,
    )
    assert env.accounts.claim_deletion(message["owner_id"], "lease", datetime.now(UTC))
    result = deleter.run(message["owner_id"], "lease", float("inf"))

    assert result == DeletionResult.DONE
    assert rows(env, "Applications", "owner-a") == 0
    assert rows(env, "Events", "owner-a") == 0
    assert rows(env, "Applications", "owner-b") == 1
    assert rows(env, "Events", "owner-b") == 1
    assert env.accounts.get_status("owner-a").state == AccountState.DELETED
    assert env.api.get("/applications").status_code == 410
    as_owner("owner-b")
    assert env.api.get("/applications").status_code == 200


# --- AccountDeleting mapping ------------------------------------------------
def test_settings_routes_map_account_deleting_to_423(env) -> None:
    from app.api.dependencies import get_ai_available_providers, get_credential_cipher
    from app.domain.account import AccountDeleting
    from app.domain.ai import AiProvider

    class Raising:
        def save(self, *args, **kwargs):
            raise AccountDeleting

        def delete(self, *args, **kwargs):
            raise AccountDeleting

    class Cipher:
        def encrypt(self, *args):
            return {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}

    from app.api.dependencies import get_ai_settings_repository, get_auto_apply_settings

    app.dependency_overrides[get_ai_settings_repository] = lambda: Raising()
    app.dependency_overrides[get_auto_apply_settings] = lambda: object()
    app.dependency_overrides[get_ai_available_providers] = lambda: [AiProvider.OPENAI]
    app.dependency_overrides[get_credential_cipher] = lambda: Cipher()

    put = env.api.put(
        "/settings/ai", json={"provider": "openai", "api_key": "k" * 20, "enabled": True, "consent": True}
    )
    delete = env.api.delete("/settings/ai")

    assert put.status_code == 423
    assert delete.status_code == 423


@pytest.mark.parametrize("error_name", ["AccountDeleting", "StaleGeneration"])
def test_gmail_disconnect_maps_deletion_race_to_423(env, error_name) -> None:
    from app.api.dependencies import get_current_identity
    from app.api.gmail import get_gmail_service
    from app.domain.account import AccountDeleting
    from app.domain.gmail import TrackerIdentity
    from app.repositories.gmail import StaleGeneration

    error = {"AccountDeleting": AccountDeleting, "StaleGeneration": StaleGeneration}[
        error_name
    ]

    class Raising:
        def disconnect(self, identity):
            raise error

    app.dependency_overrides[get_current_identity] = lambda: TrackerIdentity(
        owner_id="owner-a", username="name-a", email="a@example.test"
    )
    app.dependency_overrides[get_gmail_service] = lambda: Raising()

    assert env.api.delete("/gmail/connection").status_code == 423
