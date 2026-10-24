import pytest
from moto import mock_aws

from app.services.email_rules import RuleDecision
from app.services.gmail_fetch import MessageUnavailable
from app.services.local_review_ai import LocalAiMessageUnavailable, LocalAiUnavailable
from app.services.message_content import MessageContentService, MessageNotFound
from tests.ai_support import Env, FakeCipher, gmail_message, parsed


class _Oauth:
    def refresh_access_token(self, token):
        return "access"


class _Fetch:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def get_message(self, access_token, message_id):
        self.calls.append(message_id)
        if self.error is not None:
            raise self.error
        return self.payload


@pytest.fixture
def env():
    with mock_aws():
        e = Env()
        e.connect("owner-1", ai=False)
        e.connect("owner-2", ai=False)
        yield e


def _record(env, owner, message_id):
    env.emails(owner).record(
        owner_id=owner, message=parsed(message_id, "x"),
        decision=RuleDecision(outcome="ignored", reason="not_job_related"),
    )


def _service(env, fetch):
    return MessageContentService(
        emails=env.emails("owner-1"), connections=env.gmail, cipher=FakeCipher(),
        oauth=_Oauth(), fetch=fetch,
    )


def test_content_returns_parsed_fields_for_processed_message(env):
    _record(env, "owner-1", "a")
    fetch = _Fetch(gmail_message("a", "Hello there", subject="Interview"))
    content = _service(env, fetch).get("owner-1", "a")
    assert content.message_id == "a"
    assert content.thread_id == "thread-a"
    assert content.subject == "Interview"
    assert content.sender == "Recruiting <jobs@acme.test>"
    assert content.text == "Hello there"
    assert content.received_at.year == 2026


def test_content_not_found_for_unprocessed_or_other_owner_without_fetching(env):
    _record(env, "owner-2", "b")
    fetch = _Fetch(gmail_message("b", "secret"))
    service = _service(env, fetch)
    with pytest.raises(MessageNotFound):
        service.get("owner-1", "never-seen")
    with pytest.raises(MessageNotFound):
        service.get("owner-1", "b")
    assert fetch.calls == []


def test_content_error_mapping(env):
    _record(env, "owner-1", "a")
    with pytest.raises(LocalAiMessageUnavailable):
        _service(env, _Fetch(error=MessageUnavailable())).get("owner-1", "a")
    env.gmail.disconnect("owner-1")
    with pytest.raises(LocalAiUnavailable):
        _service(env, _Fetch(gmail_message("a", "x"))).get("owner-1", "a")


def test_content_text_capped_at_body_limit(env):
    _record(env, "owner-1", "a")
    content = _service(env, _Fetch(gmail_message("a", "y" * 9000))).get("owner-1", "a")
    assert len(content.text) == 8000
