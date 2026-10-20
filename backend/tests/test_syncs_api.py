import json
from datetime import UTC, datetime

import boto3
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_current_subject,
    get_sync_publisher,
    get_sync_repository,
)
from app.main import app
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables

TOKEN = {
    "ciphertext": "opaque-ciphertext",
    "nonce": "opaque-nonce",
    "encrypted_data_key": "opaque-key",
}


class RecordingPublisher:
    def __init__(self, fail: bool = False) -> None:
        self.messages: list[dict] = []
        self.fail = fail

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> None:
        assert QueueUrl == "https://sqs.us-east-1.amazonaws.com/123456789012/sync"
        if self.fail:
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "SendMessage")
        self.messages.append(json.loads(MessageBody))


@pytest.fixture
def setup(monkeypatch):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client,
            applications_table="Applications",
            events_table="Events",
            users_table="Users",
            sync_runs_table="SyncRuns",
        )
        gmail = DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        )
        sync = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        publisher = RecordingPublisher()
        app.dependency_overrides[get_sync_repository] = lambda: sync
        app.dependency_overrides[get_sync_publisher] = lambda: publisher
        monkeypatch.setenv(
            "SYNC_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/123456789012/sync"
        )
        monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
        with TestClient(app) as api:
            yield api, gmail, sync, publisher
        app.dependency_overrides.clear()


def test_sync_routes_require_authentication(setup) -> None:
    api, _, _, _ = setup

    assert api.post("/syncs").status_code == 401
    assert api.get("/syncs/latest").status_code == 401


def test_manual_sync_is_durable_and_owner_scoped(setup) -> None:
    api, gmail, _, publisher = setup
    gmail.save_connection(
        "owner-a", 0, "a@example.test", TOKEN, datetime(2026, 10, 18, tzinfo=UTC)
    )
    app.dependency_overrides[get_current_subject] = lambda: "owner-a"

    response = api.post("/syncs", json={"owner_id": "owner-b"})
    latest = api.get("/syncs/latest")
    app.dependency_overrides[get_current_subject] = lambda: "owner-b"
    other = api.get("/syncs/latest")

    assert response.status_code == 202
    assert response.json()["owner_id"] == "owner-a"
    assert response.json()["status"] == "pending"
    assert latest.status_code == 200
    assert latest.json()["run_id"] == response.json()["run_id"]
    assert other.status_code == 404
    assert publisher.messages == [{
        "owner_id": "owner-a",
        "run_id": response.json()["run_id"],
        "generation": 1,
    }]


def test_overlapping_manual_requests_reuse_active_run(setup) -> None:
    api, gmail, _, _ = setup
    gmail.save_connection(
        "owner-a", 0, "a@example.test", TOKEN, datetime(2026, 10, 18, tzinfo=UTC)
    )
    app.dependency_overrides[get_current_subject] = lambda: "owner-a"

    first = api.post("/syncs")
    second = api.post("/syncs")

    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]


def test_queue_failure_leaves_recoverable_pending_run(setup) -> None:
    api, gmail, sync, publisher = setup
    gmail.save_connection(
        "owner-a", 0, "a@example.test", TOKEN, datetime(2026, 10, 18, tzinfo=UTC)
    )
    app.dependency_overrides[get_current_subject] = lambda: "owner-a"
    publisher.fail = True

    response = api.post("/syncs")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert sync.get_latest("owner-a").run_id == response.json()["run_id"]


def test_disconnected_owner_cannot_start_sync(setup) -> None:
    api, _, _, _ = setup
    app.dependency_overrides[get_current_subject] = lambda: "owner-a"

    response = api.post("/syncs")

    assert response.status_code == 409


def test_local_run_without_queue_reports_unavailable(setup, monkeypatch) -> None:
    api, gmail, sync, publisher = setup
    gmail.save_connection(
        "owner-a", 0, "a@example.test", TOKEN, datetime(2026, 10, 18, tzinfo=UTC)
    )
    app.dependency_overrides[get_current_subject] = lambda: "owner-a"
    monkeypatch.delenv("SYNC_QUEUE_URL")

    response = api.post("/syncs")

    assert response.status_code == 503
    assert sync.get_latest("owner-a") is None
    assert publisher.messages == []
