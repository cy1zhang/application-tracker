from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.applications import Application, ApplicationStatus
from app.repositories.gmail import GmailConnection
from app.services.ai_extraction import InvalidExtraction
from app.services.email_rules import ApplicationCandidate
from app.services.gmail_message import ParsedGmailMessage
from app.services.local_review_ai import (
    LocalAiStale,
    LocalReviewAiService,
    build_local_suggestion,
    local_review_eligible,
)

NOW = datetime(2026, 10, 21, tzinfo=UTC)
FIELDS = (
    "company", "role", "employment_type", "season", "year", "status", "requisition_id"
)


def _message():
    return ParsedGmailMessage(
        message_id="synthetic-1", thread_id="thread-1", received_at=NOW,
        subject="Acme update", sender="jobs@acme.test",
        text="We invite you to interview for the Software Engineer Intern role at Acme.",
    )


def _candidate():
    return ApplicationCandidate(
        application=Application(
            application_id="app-1", company="Acme", role="Software Engineer Intern",
            role_type="internship", season=None, year=None,
            status=ApplicationStatus.APPLIED, application_date=None,
            created_at=NOW, updated_at=NOW, version=1,
        ),
        thread_ids=frozenset({"thread-1"}),
    )


def _output(status="interviewing", quote="invite you to interview"):
    output = {field: None for field in FIELDS}
    evidence = {field: None for field in FIELDS}
    output["status"] = status
    evidence["status"] = quote
    return {**output, "evidence": evidence}


def _review(reason="unknown_stage"):
    return {
        "owner_id": "user-a", "event_id": "gmail#synthetic-1",
        "source_message_id": "synthetic-1", "outcome": "review_required",
        "reason": reason, "evidence": "", "thread_id": "thread-1",
    }


def test_local_suggestion_never_auto_applies_even_for_safe_stage():
    decision = build_local_suggestion(
        _review(), _message(), [_candidate()], _output()
    )
    assert decision is not None
    assert decision.outcome == "review_required"
    assert decision.reason == "ai_proposal"
    assert decision.application_id == "app-1"
    assert decision.status is ApplicationStatus.INTERVIEWING
    assert decision.ai_proposal.status is ApplicationStatus.INTERVIEWING


def test_unsupported_quote_does_not_create_suggestion():
    assert build_local_suggestion(
        _review(), _message(), [_candidate()],
        _output(status="offer", quote="invite you to interview"),
    ) is None


def test_only_pending_allowlisted_reviews_without_proposal_are_eligible():
    assert local_review_eligible(_review()) is True
    assert local_review_eligible(_review("older_evidence")) is False
    assert local_review_eligible({**_review(), "resolution": "reject"}) is False
    assert local_review_eligible({**_review(), "ai_proposal": {}}) is False


def _gmail_raw():
    return {
        "id": "synthetic-1", "threadId": "thread-1",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Acme update"},
                {"name": "From", "value": "jobs@acme.test"},
            ],
            "body": {"data": urlsafe_b64encode(_message().text.encode()).decode()},
        },
    }


def _service(generation=1):
    class Reviews:
        def __init__(self):
            self.saved = None

        def get_pending(self, *, owner_id, message_id):
            return _review() if owner_id == "user-a" and message_id == "synthetic-1" else None

        def save_local_suggestion(self, **kwargs):
            self.saved = kwargs
            return SimpleNamespace(message_id="synthetic-1", reason="ai_proposal")

    class Connections:
        def get_connection(self, owner_id):
            return GmailConnection(
                generation=generation, email="user-a@example.test",
                encrypted_token={"synthetic": "token"},
            )

        def get_active(self, owner_id, expected_generation):
            return {"synthetic": "token"} if expected_generation == generation else None

    class Apps:
        def list(self, **kwargs):
            return SimpleNamespace(items=[_candidate().application], next_cursor=None)

        def list_events(self, **kwargs):
            return [SimpleNamespace(thread_id="thread-1", requisition_id=None)]

    reviews = Reviews()
    service = LocalReviewAiService(
        reviews=reviews, connections=Connections(), applications=Apps(),
        cipher=SimpleNamespace(decrypt_refresh_token=lambda owner, token: "refresh"),
        oauth=SimpleNamespace(refresh_access_token=lambda token: "access"),
        fetch=SimpleNamespace(get_message=lambda token, message_id: _gmail_raw()),
    )
    return service, reviews


def test_service_returns_bounded_prompt_and_saves_validated_review_only():
    service, reviews = _service()
    prepared = service.prepare("user-a", "synthetic-1")
    assert prepared.generation == 1
    assert "BEGIN EMAIL DATA" in prepared.user_prompt
    assert prepared.format["required"]

    result = service.suggest(
        "user-a", "synthetic-1", generation=prepared.generation,
        model="gemma4:latest", raw=_output(),
    )
    assert result.saved is True
    assert result.review.reason == "ai_proposal"
    assert reviews.saved["decision"].outcome == "review_required"
    assert reviews.saved["decision"].application_id == "app-1"


def test_service_rejects_changed_generation_before_saving():
    service, reviews = _service(generation=2)
    with pytest.raises(LocalAiStale):
        service.suggest(
            "user-a", "synthetic-1", generation=1,
            model="gemma4:latest", raw=_output(),
        )
    assert reviews.saved is None


def test_service_does_not_save_fabricated_evidence():
    service, reviews = _service()
    result = service.suggest(
        "user-a", "synthetic-1", generation=1,
        model="gemma4:latest",
        raw=_output(status="offer", quote="fabricated offer sentence"),
    )
    assert result.saved is False
    assert reviews.saved is None


def test_service_rejects_malformed_model_json():
    service, reviews = _service()
    with pytest.raises(InvalidExtraction):
        service.suggest(
            "user-a", "synthetic-1", generation=1,
            model="gemma4:latest", raw={"status": "offer"},
        )
    assert reviews.saved is None
