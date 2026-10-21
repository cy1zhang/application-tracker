from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import get_current_identity, get_current_subject
from app.api.gmail import get_gmail_service
from app.domain.gmail import TrackerIdentity
from app.main import app
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.gmail_connection import GmailConnectionService
from app.services.google_oauth import GoogleOAuthClient


class FakeGoogle:
    def __init__(self) -> None:
        self.email = "alex@example.test"
        self.refresh_token: str | None = "synthetic-refresh"
        self.exchanges = 0
        self.last_prompt_consent = None

    def authorization_url(self, state: str, *, prompt_consent: bool) -> str:
        self.last_prompt_consent = prompt_consent
        return f"https://accounts.google.test/consent?state={state}"

    def exchange_code(self, code: str) -> dict:
        self.exchanges += 1
        assert code == "synthetic-code"
        return {"access_token": "synthetic-access", "refresh_token": self.refresh_token}

    def profile_email(self, access_token: str) -> str:
        assert access_token == "synthetic-access"
        return self.email


class FakeCipher:
    def encrypt_refresh_token(self, owner_id: str, plaintext: str) -> dict:
        assert owner_id == "owner-a"
        assert plaintext == "synthetic-refresh"
        return {
            "ciphertext": "opaque-ciphertext",
            "nonce": "opaque-nonce",
            "encrypted_data_key": "opaque-key",
        }


@pytest.fixture
def setup(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            oauth_states_table="OAuthStates",
        )
        repository = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        google = FakeGoogle()
        service = GmailConnectionService(repository, FakeCipher(), google)
        identity = TrackerIdentity(
            owner_id="owner-a", username="google_alex", email="alex@example.test"
        )
        app.dependency_overrides[get_gmail_service] = lambda: service
        app.dependency_overrides[get_current_identity] = lambda: identity
        app.dependency_overrides[get_current_subject] = lambda: "owner-a"
        monkeypatch.setenv("FRONTEND_URL", "https://main.example.test")
        with TestClient(app) as api:
            yield api, repository, google, identity
        app.dependency_overrides.clear()


def start_consent(api) -> str:
    response = api.post("/gmail/connect")
    assert response.status_code == 200
    url = response.json()["authorization_url"]
    return parse_qs(urlparse(url).query)["state"][0]


def callback(api, state: str, **params):
    return api.get(
        "/oauth/google/callback",
        params={"state": state, **params},
        follow_redirects=False,
    )


def test_authorized_connect_status_and_disconnect(setup) -> None:
    api, repository, _google, _identity = setup
    assert api.get("/gmail/connection").json() == {"connected": False, "email": None}

    state = start_consent(api)
    response = callback(api, state, code="synthetic-code")

    assert response.status_code == 303
    assert response.headers["location"] == "https://main.example.test/?gmail=connected"
    assert api.get("/gmail/connection").json() == {
        "connected": True,
        "email": "alex@example.test",
    }
    assert api.delete("/gmail/connection").status_code == 204
    assert api.get("/gmail/connection").json() == {"connected": False, "email": None}
    assert repository.get_active("owner-a", 1) is None


def test_gmail_routes_require_authentication(setup, monkeypatch) -> None:
    api, _repository, _google, _identity = setup
    app.dependency_overrides.pop(get_current_identity)
    app.dependency_overrides.pop(get_current_subject)
    monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)

    assert api.get("/gmail/connection").status_code == 401
    assert api.post("/gmail/connect").status_code == 401
    assert api.delete("/gmail/connection").status_code == 401


def test_gmail_routes_reject_unapproved_user(setup) -> None:
    api, _repository, _google, _identity = setup
    app.dependency_overrides[get_current_identity] = lambda: (_ for _ in ()).throw(
        HTTPException(status_code=403, detail="Access denied")
    )

    assert api.get("/gmail/connection").status_code == 403
    assert api.post("/gmail/connect").status_code == 403
    assert api.delete("/gmail/connection").status_code == 403


def test_callback_rejects_missing_expired_and_replayed_state(setup) -> None:
    api, repository, google, _identity = setup
    assert callback(api, "unknown", code="synthetic-code").headers["location"].endswith(
        "gmail=failed"
    )
    repository.create_state(
        sha256(b"expired").hexdigest(),
        "owner-a",
        "alex@example.test",
        0,
        datetime.now(UTC) - timedelta(seconds=1),
    )
    assert callback(api, "expired", code="synthetic-code").headers["location"].endswith(
        "gmail=failed"
    )
    state = start_consent(api)
    assert callback(api, state, code="synthetic-code").headers["location"].endswith(
        "gmail=connected"
    )
    assert callback(api, state, code="synthetic-code").headers["location"].endswith(
        "gmail=failed"
    )
    assert google.exchanges == 1


def test_google_denial_burns_state(setup) -> None:
    api, _repository, google, _identity = setup
    state = start_consent(api)

    assert callback(api, state, error="access_denied").headers["location"].endswith(
        "gmail=failed"
    )
    assert callback(api, state, code="synthetic-code").headers["location"].endswith(
        "gmail=failed"
    )
    assert google.exchanges == 0


def test_google_response_read_timeout_redirects_to_failure(setup) -> None:
    api, repository, _google, _identity = setup
    state = start_consent(api)

    class TimeoutResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            raise TimeoutError("synthetic token response timeout")

    secrets = Mock()
    secrets.get_secret_value.return_value = {
        "SecretString": '{"client_id":"synthetic-id","client_secret":"synthetic-secret"}'
    }
    service = app.dependency_overrides[get_gmail_service]()
    service._google = GoogleOAuthClient(
        secrets,
        "gmail-test-secret",
        "https://api.example.test/oauth/google/callback",
        urlopen=lambda _request, *, timeout: TimeoutResponse(),
    )

    response = callback(api, state, code="synthetic-code")

    assert response.status_code == 303
    assert response.headers["location"] == "https://main.example.test/?gmail=failed"
    assert repository.get_connection("owner-a").email is None


def test_wrong_mailbox_is_not_saved(setup) -> None:
    api, repository, google, _identity = setup
    google.email = "other@example.test"

    assert callback(api, start_consent(api), code="synthetic-code").headers[
        "location"
    ].endswith("gmail=failed")
    assert repository.get_connection("owner-a").email is None


def test_first_connection_requires_refresh_token(setup) -> None:
    api, repository, google, _identity = setup
    google.refresh_token = None

    assert callback(api, start_consent(api), code="synthetic-code").headers[
        "location"
    ].endswith("gmail=failed")
    assert repository.get_connection("owner-a").email is None


def test_same_mailbox_reconnect_preserves_refresh_token(setup) -> None:
    api, repository, google, _identity = setup
    callback(api, start_consent(api), code="synthetic-code")
    original = repository.get_active("owner-a", 1)
    google.refresh_token = None

    response = callback(api, start_consent(api), code="synthetic-code")

    assert response.headers["location"].endswith("gmail=connected")
    assert repository.get_active("owner-a", 2) == original


def test_revoked_connection_requires_fresh_refresh_token(setup) -> None:
    api, repository, google, _identity = setup
    callback(api, start_consent(api), code="synthetic-code")
    repository._client.update_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET reconnect_required = :yes",
        ExpressionAttributeValues={":yes": {"BOOL": True}},
    )
    google.refresh_token = None

    failed = callback(api, start_consent(api), code="synthetic-code")

    assert google.last_prompt_consent is True
    assert failed.headers["location"].endswith("gmail=failed")
    assert repository.get_connection("owner-a").generation == 1

    google.refresh_token = "synthetic-refresh"
    succeeded = callback(api, start_consent(api), code="synthetic-code")

    assert succeeded.headers["location"].endswith("gmail=connected")
    assert repository.get_connection("owner-a").generation == 2
    user = repository._client.get_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}}
    )["Item"]
    assert "reconnect_required" not in user


def test_connection_is_owner_scoped(setup) -> None:
    api, repository, _google, _identity = setup
    callback(api, start_consent(api), code="synthetic-code")
    app.dependency_overrides[get_current_identity] = lambda: TrackerIdentity(
        owner_id="owner-b", username="google_b", email="b@example.test"
    )

    assert api.get("/gmail/connection").json() == {"connected": False, "email": None}
    assert api.delete("/gmail/connection").status_code == 204
    assert repository.get_connection("owner-a").email == "alex@example.test"


def test_disconnect_during_consent_cannot_reconnect(setup) -> None:
    api, repository, google, _identity = setup
    state = start_consent(api)
    api.delete("/gmail/connection")

    response = callback(api, state, code="synthetic-code")

    assert response.headers["location"].endswith("gmail=failed")
    assert repository.get_connection("owner-a").email is None
    assert google.exchanges == 0
