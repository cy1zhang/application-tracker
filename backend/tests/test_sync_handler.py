import json

import pytest

from app import sync_handler
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.google_oauth import OAuthReconnectRequired


class FakeSyncs:
    def __init__(self):
        self.failures = []

    def fail(self, owner_id, run_id, generation, lease_id, status, now):
        self.failures.append((owner_id, run_id, generation, status))


@pytest.fixture
def setup(monkeypatch):
    for key in ("APPLICATIONS_TABLE", "EVENTS_TABLE", "USERS_TABLE", "SYNC_QUEUE_URL"):
        monkeypatch.setenv(key, key)
    syncs = FakeSyncs()
    monkeypatch.setattr(sync_handler, "_dependencies", lambda: {
        "client": object(), "syncs": syncs,
    })
    return syncs, monkeypatch


def delivery(count="1"):
    return {"Records": [{
        "messageId": "sqs-1",
        "body": json.dumps({"owner_id": "owner-a", "run_id": "run-1", "generation": 1}),
        "attributes": {"ApproximateReceiveCount": count},
    }]}


def test_transient_failure_returns_batch_failure_without_releasing_run(setup) -> None:
    syncs, monkeypatch = setup

    def fail(**kwargs):
        raise RuntimeError("synthetic transient error")

    monkeypatch.setattr(sync_handler, "process_sync_page", fail)

    assert sync_handler.handler(delivery(), None) == {
        "batchItemFailures": [{"itemIdentifier": "sqs-1"}]
    }
    assert syncs.failures == []


def test_exhausted_delivery_marks_partial_and_retries_to_dead_letter(setup) -> None:
    syncs, monkeypatch = setup

    def fail(**kwargs):
        raise RuntimeError("synthetic transient error")

    monkeypatch.setattr(sync_handler, "process_sync_page", fail)

    assert sync_handler.handler(delivery("5"), None)["batchItemFailures"] == [
        {"itemIdentifier": "sqs-1"}
    ]
    assert syncs.failures == [("owner-a", "run-1", 1, "partial")]


@pytest.mark.parametrize("error, status", [
    (OAuthReconnectRequired("revoked"), "reconnect_required"),
    (StaleSyncRun(), None),
])
def test_revoked_or_stale_work_is_acknowledged(setup, error, status) -> None:
    syncs, monkeypatch = setup

    def fail(**kwargs):
        raise error

    monkeypatch.setattr(sync_handler, "process_sync_page", fail)

    assert sync_handler.handler(delivery(), None) == {"batchItemFailures": []}
    assert [item[3] for item in syncs.failures] == ([status] if status else [])
