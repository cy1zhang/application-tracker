import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_ai_available_providers,
    get_ai_settings_repository,
    get_auto_apply_settings,
    get_current_subject,
)
from app.domain.account import AccountDeleting
from app.main import app
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_tables import create_tables


def no_providers() -> list:
    return []


@pytest.fixture
def setup(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users",
        )
        settings = DynamoAutoApplySettings(client=client, users_table="Users")
        ai = DynamoAiSettingsRepository(client=client, users_table="Users")
        app.dependency_overrides[get_auto_apply_settings] = lambda: settings
        app.dependency_overrides[get_ai_settings_repository] = lambda: ai
        app.dependency_overrides[get_ai_available_providers] = no_providers
        app.dependency_overrides[get_current_subject] = lambda: "owner-a"
        monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
        with TestClient(app) as api:
            yield api, settings, client
        app.dependency_overrides.clear()


def test_mode_defaults_off(setup) -> None:
    api, settings, _ = setup

    assert settings.get_mode("owner-a") == "off"
    assert api.get("/settings").json()["auto_apply_mode"] == "off"


@pytest.mark.parametrize("mode", ["plan", "off"])
def test_put_mode_plan_and_off(setup, mode) -> None:
    api, settings, _ = setup
    api.put("/settings/auto-apply", json={"mode": "plan"})

    response = api.put("/settings/auto-apply", json={"mode": mode})

    assert response.status_code == 200
    assert response.json()["auto_apply_mode"] == mode
    assert settings.get_mode("owner-a") == mode
    assert api.get("/settings").json()["auto_apply_mode"] == mode


def test_put_mode_on_rejected_422(setup) -> None:
    api, settings, _ = setup

    response = api.put("/settings/auto-apply", json={"mode": "on"})

    assert response.status_code == 422
    assert "input" not in response.text
    assert settings.get_mode("owner-a") == "off"


def test_mode_is_owner_scoped(setup) -> None:
    api, settings, _ = setup
    api.put("/settings/auto-apply", json={"mode": "plan"})

    assert settings.get_mode("owner-b") == "off"
    app.dependency_overrides[get_current_subject] = lambda: "owner-b"
    assert api.get("/settings").json()["auto_apply_mode"] == "off"


def test_set_mode_refused_while_deleting(setup) -> None:
    _, settings, client = setup
    client.put_item(
        TableName="Users",
        Item={"owner_id": {"S": "owner-a"}, "deletion_state": {"S": "deleting"}},
    )

    with pytest.raises(AccountDeleting):
        settings.set_mode("owner-a", "plan")
