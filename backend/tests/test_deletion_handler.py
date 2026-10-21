import json

import pytest

from app import deletion_handler
from app.services.account_deletion import DeletionResult, PhaseFailure, StaleLease


class Accounts:
    def __init__(self, claim="revoke_gmail"):
        self.claim = claim
        self.claims = []
        self.released = []

    def claim_deletion(self, owner_id, lease_id, now):
        self.claims.append((owner_id, lease_id))
        return self.claim

    def release_deletion(self, owner_id, lease_id):
        self.released.append((owner_id, lease_id))


class Deleter:
    def __init__(self, outcome=DeletionResult.DONE):
        self.outcome = outcome
        self.runs = []

    def run(self, owner_id, lease_id, deadline):
        self.runs.append((owner_id, lease_id, deadline))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class Publisher:
    def __init__(self):
        self.messages = []

    def send_message(self, *, QueueUrl, MessageBody, **kwargs):
        assert QueueUrl == "deletion-queue"
        self.messages.append(json.loads(MessageBody))


@pytest.fixture
def setup(monkeypatch):
    accounts, deleter, publisher = Accounts(), Deleter(), Publisher()
    monkeypatch.setenv("DELETION_QUEUE_URL", "deletion-queue")
    monkeypatch.setattr(deletion_handler, "_dependencies", lambda: {
        "accounts": accounts, "deleter": deleter, "publisher": publisher,
    })
    return accounts, deleter, publisher


def delivery(body=None):
    body = {"kind": "delete_account", "owner_id": "owner-a"} if body is None else body
    return {"Records": [{
        "messageId": "sqs-1",
        "body": body if isinstance(body, str) else json.dumps(body),
    }]}


class Context:
    def get_remaining_time_in_millis(self):
        return 300_000


def test_finished_deletion_returns_nothing_to_retry(setup) -> None:
    _, deleter, publisher = setup

    assert deletion_handler.handler(delivery(), Context()) == {"batchItemFailures": []}
    assert len(deleter.runs) == 1 and deleter.runs[0][0] == "owner-a"
    assert publisher.messages == []


def test_deadline_leaves_buffer_before_lambda_timeout(setup, monkeypatch) -> None:
    _, deleter, _ = setup
    monkeypatch.setattr(deletion_handler.time, "monotonic", lambda: 1000.0)

    deletion_handler.handler(delivery(), Context())

    assert deleter.runs[0][2] == 1000.0 + 300 - 60


def test_continue_releases_lease_and_republishes_exactly_one(setup) -> None:
    accounts, deleter, publisher = setup
    deleter.outcome = DeletionResult.CONTINUE

    assert deletion_handler.handler(delivery(), Context()) == {"batchItemFailures": []}
    assert publisher.messages == [{"kind": "delete_account", "owner_id": "owner-a"}]
    assert len(accounts.released) == 1


@pytest.mark.parametrize("claim", [None])
def test_claimed_elsewhere_or_deleted_is_acknowledged_without_work(setup, claim) -> None:
    accounts, deleter, publisher = setup
    accounts.claim = claim

    assert deletion_handler.handler(delivery(), Context()) == {"batchItemFailures": []}
    assert deleter.runs == [] and publisher.messages == []


def test_stale_lease_is_acknowledged(setup) -> None:
    _, deleter, publisher = setup
    deleter.outcome = StaleLease()

    assert deletion_handler.handler(delivery(), Context()) == {"batchItemFailures": []}
    assert publisher.messages == []


@pytest.mark.parametrize("body", [
    {"kind": "sync", "owner_id": "owner-a"},
    {"kind": "delete_account"},
    {"kind": "delete_account", "owner_id": 7},
    "not json",
    [],
])
def test_wrong_kind_or_malformed_message_is_acknowledged_without_work(setup, body) -> None:
    accounts, deleter, _ = setup

    assert deletion_handler.handler(delivery(body), Context()) == {"batchItemFailures": []}
    assert accounts.claims == [] and deleter.runs == []


def test_failure_returns_batch_item_and_logs_phase_and_category_only(setup, caplog) -> None:
    accounts, deleter, _ = setup
    deleter.outcome = PhaseFailure("delete_login", "ClientError")

    result = deletion_handler.handler(delivery(), Context())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert "phase=delete_login" in caplog.text and "category=ClientError" in caplog.text
    assert "owner-a" not in caplog.text
    assert len(accounts.released) == 1


def test_unexpected_error_is_retried_with_class_name_only(setup, caplog) -> None:
    _, deleter, _ = setup
    deleter.outcome = RuntimeError("private detail owner-a")

    result = deletion_handler.handler(delivery(), Context())

    assert result == {"batchItemFailures": [{"itemIdentifier": "sqs-1"}]}
    assert "RuntimeError" in caplog.text
    assert "private detail" not in caplog.text and "owner-a" not in caplog.text
