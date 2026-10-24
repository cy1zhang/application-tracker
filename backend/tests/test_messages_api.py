from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from app.api.dependencies import (
    get_current_subject,
    get_email_repository,
    get_message_content_service,
)
from app.domain.messages import MessageContent
from app.main import app
from app.services.email_rules import RuleDecision
from app.services.local_review_ai import LocalAiMessageUnavailable, LocalAiUnavailable
from app.services.message_content import MessageContentService, MessageNotFound
from tests.ai_support import Env, FakeCipher, parsed


def _record(env, owner, message_id, outcome="ignored", reason="not_job_related"):
    env.emails(owner).record(
        owner_id=owner, message=parsed(message_id, "x"),
        decision=RuleDecision(outcome=outcome, reason=reason),
    )


@pytest.fixture
def env():
    with mock_aws():
        e = Env()
        e.connect("owner-1", ai=False)
        e.connect("owner-2", ai=False)
        app.dependency_overrides[get_current_subject] = lambda: "owner-1"
        app.dependency_overrides[get_email_repository] = lambda: e.emails("owner-1")
        with TestClient(app) as client:
            e.client_api = client
            yield e
        app.dependency_overrides.clear()


def test_list_messages_returns_only_owner_gmail_items(env):
    _record(env, "owner-1", "a", "review_required", "unknown_stage")
    _record(env, "owner-2", "b")
    env.client.put_item(
        TableName="Events",
        Item={"owner_id": {"S": "owner-1"}, "event_id": {"S": "evt-1"}},
    )
    body = env.client_api.get("/messages").json()
    assert [(i["message_id"], i["outcome"], i["reason"]) for i in body["items"]] == [
        ("a", "review_required", "unknown_stage")
    ]
    assert body["next_cursor"] is None


def test_list_messages_paginates_with_opaque_cursor(env):
    for message_id in ("a", "b", "c"):
        _record(env, "owner-1", message_id)
    first = env.client_api.get("/messages?limit=2").json()
    assert len(first["items"]) == 2
    second = env.client_api.get(f"/messages?limit=2&cursor={first['next_cursor']}").json()
    assert [i["message_id"] for i in second["items"]] == ["c"]
    assert second["next_cursor"] is None
    assert env.client_api.get("/messages?cursor=not-a-cursor").status_code == 422


class _Service:
    def __init__(self, error=None):
        self.error = error

    def get(self, owner_id, message_id):
        if self.error is not None:
            raise self.error
        return MessageContent(
            message_id=message_id, thread_id="t", received_at=datetime(2026, 1, 1, tzinfo=UTC),
            subject="S", sender="f@x.test", text="body",
        )


def test_content_route_returns_parsed_content(env):
    app.dependency_overrides[get_message_content_service] = lambda: _Service()
    response = env.client_api.get("/messages/a/content")
    assert response.status_code == 200
    assert response.json()["text"] == "body"


def test_content_404_body_identical_for_unprocessed_and_other_owner(env):
    _record(env, "owner-2", "b")
    class _Oauth:
        def refresh_access_token(self, token):
            return "access"

    class _Fetch:
        def get_message(self, *args):
            raise AssertionError("must not fetch")

    app.dependency_overrides[get_message_content_service] = lambda: MessageContentService(
        emails=env.emails("owner-1"), connections=env.gmail, cipher=FakeCipher(),
        oauth=_Oauth(), fetch=_Fetch(),
    )
    other = env.client_api.get("/messages/b/content")
    unknown = env.client_api.get("/messages/nope/content")
    assert other.status_code == unknown.status_code == 404
    assert other.json() == unknown.json() == {"detail": "Message not found"}


def test_content_503_codes(env):
    app.dependency_overrides[get_message_content_service] = lambda: _Service(
        LocalAiMessageUnavailable()
    )
    response = env.client_api.get("/messages/a/content")
    assert (response.status_code, response.json()["detail"]) == (503, "message_unavailable")
    app.dependency_overrides[get_message_content_service] = lambda: _Service(
        LocalAiUnavailable()
    )
    response = env.client_api.get("/messages/a/content")
    assert (response.status_code, response.json()["detail"]) == (
        503, "gmail_connection_unavailable",
    )
    app.dependency_overrides[get_message_content_service] = lambda: _Service(MessageNotFound())
    assert env.client_api.get("/messages/a/content").status_code == 404
