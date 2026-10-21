import boto3
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_ai_available_providers,
    get_ai_settings_repository,
    get_credential_cipher,
    get_current_subject,
)
from app.domain.ai import AiProvider
from app.main import app
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_tables import create_tables

SECRET = "synthetic-secret-key-ABCD"
BODY = {"provider": "openai", "api_key": SECRET, "enabled": True, "consent": True}


class FakeCipher:
    def encrypt(self, owner_id: str, credential_type: str, plaintext: str) -> dict:
        assert credential_type == "ai_api_key"
        return {
            "ciphertext": f"ct-{owner_id}",
            "nonce": "opaque-nonce",
            "encrypted_data_key": "opaque-key",
        }


@pytest.fixture
def setup(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users",
        )
        repository = DynamoAiSettingsRepository(client=client, users_table="Users")
        app.dependency_overrides[get_ai_settings_repository] = lambda: repository
        app.dependency_overrides[get_credential_cipher] = lambda: FakeCipher()
        app.dependency_overrides[get_ai_available_providers] = lambda: [
            AiProvider.OPENAI, AiProvider.GEMINI,
        ]
        app.dependency_overrides[get_current_subject] = lambda: "owner-a"
        monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
        with TestClient(app) as api:
            yield api, repository
        app.dependency_overrides.clear()


def as_owner(owner: str) -> None:
    app.dependency_overrides[get_current_subject] = lambda: owner


def test_settings_routes_require_authentication(setup) -> None:
    api, _ = setup
    app.dependency_overrides.pop(get_current_subject)

    assert api.get("/settings").status_code == 401
    assert api.put("/settings/ai", json=BODY).status_code == 401
    assert api.delete("/settings/ai").status_code == 401


def test_settings_routes_reject_unapproved_user(setup) -> None:
    api, _ = setup

    def deny():
        raise HTTPException(status_code=403, detail="Access denied")

    app.dependency_overrides[get_current_subject] = deny

    assert api.get("/settings").status_code == 403
    assert api.put("/settings/ai", json=BODY).status_code == 403
    assert api.delete("/settings/ai").status_code == 403


def test_get_with_nothing_saved(setup) -> None:
    api, _ = setup

    response = api.get("/settings")

    assert response.status_code == 200
    assert response.json() == {
        "ai": {
            "available_providers": ["openai", "gemini"],
            "provider": None,
            "key_hint": None,
            "enabled": False,
            "key_status": None,
            "requests_today": 0,
            "daily_limit": 50,
            "usage_day": response.json()["ai"]["usage_day"],
        }
    }


def test_put_saves_key_and_never_returns_it(setup) -> None:
    api, repository = setup

    put = api.put("/settings/ai", json=BODY)
    get = api.get("/settings")

    assert put.status_code == 200
    assert put.json() == get.json()
    ai = get.json()["ai"]
    assert (ai["provider"], ai["key_hint"], ai["enabled"], ai["key_status"]) == (
        "openai", "ABCD", True, "ok",
    )
    for text in (put.text, get.text):
        assert SECRET not in text
        assert "api_key" not in text and "ct-owner-a" not in text
    stored = repository.get("owner-a")
    assert stored.consent_at is not None
    assert stored.encrypted_key["ciphertext"] == "ct-owner-a"


def test_replacing_key_resets_status_and_bumps_version(setup) -> None:
    api, repository = setup
    api.put("/settings/ai", json=BODY)
    repository._client.update_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET ai_key_status = :s",
        ExpressionAttributeValues={":s": {"S": "invalid"}},
    )
    assert api.get("/settings").json()["ai"]["key_status"] == "invalid"

    response = api.put(
        "/settings/ai", json={**BODY, "api_key": "another-synthetic-key-WXYZ"}
    )

    assert response.json()["ai"]["key_status"] == "ok"
    assert response.json()["ai"]["key_hint"] == "WXYZ"
    assert repository.get("owner-a").version == 2


def test_toggle_enabled_without_key(setup) -> None:
    api, repository = setup
    api.put("/settings/ai", json=BODY)

    response = api.put("/settings/ai", json={"provider": "openai", "enabled": False})

    assert response.status_code == 200
    assert response.json()["ai"]["enabled"] is False
    assert response.json()["ai"]["key_hint"] == "ABCD"
    assert repository.get("owner-a").version == 2


def test_toggle_without_stored_key_or_for_other_provider_is_422(setup) -> None:
    api, _ = setup

    assert api.put("/settings/ai", json={"provider": "openai", "enabled": True}).status_code == 422
    api.put("/settings/ai", json=BODY)
    assert api.put("/settings/ai", json={"provider": "gemini", "enabled": True}).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "mistral"},
        {"provider": "anthropic"},
        {"api_key": "short-key"},
        {"api_key": "synthetic key with whitespace 1234"},
        {"api_key": "synthetic-key-with-newline\n1234"},
        {"api_key": "x" * 401},
        {"api_key": "synthetic-key-é-nonascii-1234"},
        {"consent": False},
        {"owner_id": "owner-b"},
        {"extra": 1},
    ],
)
def test_invalid_requests_are_422_and_never_echo_the_key(setup, change) -> None:
    api, repository = setup
    body = {**BODY, **change}

    response = api.put("/settings/ai", json=body)

    assert response.status_code == 422
    assert str(body["api_key"]) not in response.text
    assert SECRET not in response.text
    assert '"input"' not in response.text
    assert repository.get("owner-a") is None


def test_malformed_json_does_not_echo_body(setup) -> None:
    api, _ = setup

    response = api.put(
        "/settings/ai",
        content=f'{{"api_key": "{SECRET}", broken',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert SECRET not in response.text


def test_owner_isolation(setup) -> None:
    api, repository = setup
    api.put("/settings/ai", json=BODY)
    as_owner("owner-b")

    other = api.get("/settings?owner_id=owner-a")
    delete = api.delete("/settings/ai?owner_id=owner-a")

    assert other.json()["ai"]["provider"] is None
    assert delete.status_code == 204
    as_owner("owner-a")
    assert api.get("/settings").json()["ai"]["key_hint"] == "ABCD"
    assert repository.get("owner-a") is not None


def test_delete_is_idempotent_and_keeps_counter(setup) -> None:
    api, repository = setup
    api.put("/settings/ai", json=BODY)
    today = api.get("/settings").json()["ai"]["usage_day"]
    repository._client.update_item(
        TableName="Users", Key={"owner_id": {"S": "owner-a"}},
        UpdateExpression="SET ai_usage_day = :d, ai_requests_today = :n",
        ExpressionAttributeValues={":d": {"S": today}, ":n": {"N": "3"}},
    )

    assert api.delete("/settings/ai").status_code == 204
    assert api.delete("/settings/ai").status_code == 204

    ai = api.get("/settings").json()["ai"]
    assert (ai["provider"], ai["key_hint"], ai["requests_today"]) == (None, None, 3)


def test_available_providers_come_from_model_env(monkeypatch) -> None:
    for name in ("OPENAI", "ANTHROPIC", "GEMINI"):
        monkeypatch.delenv(f"AI_MODEL_{name}", raising=False)
    assert get_ai_available_providers() == []

    monkeypatch.setenv("AI_MODEL_ANTHROPIC", "some-model")
    monkeypatch.setenv("AI_MODEL_OPENAI", "   ")
    monkeypatch.setenv("AI_MODEL_GEMINI", "")

    assert get_ai_available_providers() == [AiProvider.ANTHROPIC]
