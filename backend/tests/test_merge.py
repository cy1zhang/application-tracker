from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_application_repository, get_current_subject
from app.domain.applications import ApplicationCreate, ApplicationStatus, MergeRequest
from app.main import app
from app.repositories.applications import (
    ApplicationNotFound,
    InvalidMerge,
    VersionConflict,
)
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _create(repository, app_id, *, owner="user-a", **values):
    fields = {"company": "Acme", "role": "Engineer", "status": "applied", **values}
    return repository.create(
        owner_id=owner,
        payload=ApplicationCreate(**fields),
        application_id=app_id,
        event_id=f"create-{app_id}",
        occurred_at=NOW - timedelta(days=2),
    )


def test_merge_preserves_both_histories_and_redirects_old_id(repository) -> None:
    _create(repository, "keep")
    _create(repository, "duplicate", season="summer", year=2027)

    retained = repository.merge(
        owner_id="user-a",
        keep_id="keep",
        payload=MergeRequest(
            duplicate_id="duplicate",
            expected_keep_version=1,
            expected_duplicate_version=1,
            resolutions={},
        ),
        event_id="merge-1",
        occurred_at=NOW,
    )

    assert retained.application_id == "keep"
    assert retained.season.value == "summer"
    assert retained.year == 2027
    assert repository.get(owner_id="user-a", application_id="duplicate").application_id == "keep"
    assert [item.application_id for item in repository.list(owner_id="user-a", limit=10, cursor=None).items] == ["keep"]
    events = repository.list_events(owner_id="user-a", application_id="keep")
    assert {event.event_id for event in events} == {"create-keep", "create-duplicate", "merge-1"}
    assert events[-1].event_type.value == "merged"


def test_merge_requires_explicit_choices_for_conflicting_fields(repository) -> None:
    _create(repository, "keep")
    _create(repository, "duplicate", status="offer")
    request = MergeRequest(
        duplicate_id="duplicate",
        expected_keep_version=1,
        expected_duplicate_version=1,
        resolutions={},
    )

    with pytest.raises(InvalidMerge):
        repository.merge(owner_id="user-a", keep_id="keep", payload=request, event_id="merge-1", occurred_at=NOW)
    retained = repository.merge(
        owner_id="user-a",
        keep_id="keep",
        payload=request.model_copy(update={"resolutions": {"status": "duplicate"}}),
        event_id="merge-2",
        occurred_at=NOW,
    )

    assert retained.status == ApplicationStatus.OFFER


def test_merge_rejects_self_cross_owner_and_stale_versions(repository) -> None:
    _create(repository, "keep")
    _create(repository, "foreign", owner="user-b")
    with pytest.raises(InvalidMerge):
        repository.merge(
            owner_id="user-a", keep_id="keep",
            payload=MergeRequest(duplicate_id="keep", expected_keep_version=1, expected_duplicate_version=1),
            event_id="self", occurred_at=NOW,
        )
    with pytest.raises(ApplicationNotFound):
        repository.merge(
            owner_id="user-a", keep_id="keep",
            payload=MergeRequest(duplicate_id="foreign", expected_keep_version=1, expected_duplicate_version=1),
            event_id="foreign", occurred_at=NOW,
        )
    _create(repository, "duplicate")
    with pytest.raises(VersionConflict):
        repository.merge(
            owner_id="user-a", keep_id="keep",
            payload=MergeRequest(duplicate_id="duplicate", expected_keep_version=9, expected_duplicate_version=1),
            event_id="stale", occurred_at=NOW,
        )


def test_replayed_email_for_merged_id_cannot_recreate_duplicate(repository) -> None:
    _create(repository, "keep")
    _create(repository, "duplicate")
    repository.merge(
        owner_id="user-a", keep_id="keep",
        payload=MergeRequest(duplicate_id="duplicate", expected_keep_version=1, expected_duplicate_version=1),
        event_id="merge-1", occurred_at=NOW,
    )
    emails = DynamoEmailRepository(
        client=repository._client, applications_table="Applications", events_table="Events"
    )
    outcome = emails.record(
        owner_id="user-a",
        message=ParsedGmailMessage(
            message_id="later", thread_id="thread-old", received_at=NOW + timedelta(days=1),
            subject="Synthetic", sender="jobs@acme.test", text="synthetic fixture",
        ),
        decision=RuleDecision(
            outcome="applied", reason="explicit_event", application_id="duplicate",
            status=ApplicationStatus.INTERVIEWING,
        ),
        application_version=2,
    )

    assert outcome.outcome == "review_required"
    assert [item.application_id for item in repository.list(owner_id="user-a", limit=10, cursor=None).items] == ["keep"]


def test_merge_api_enforces_owner_and_payload(repository) -> None:
    _create(repository, "keep")
    _create(repository, "duplicate")
    app.dependency_overrides[get_application_repository] = lambda: repository
    app.dependency_overrides[get_current_subject] = lambda: "user-b"
    try:
        with TestClient(app) as client:
            payload = {"duplicate_id": "duplicate", "expected_keep_version": 1, "expected_duplicate_version": 1, "resolutions": {}}
            assert client.post("/applications/keep/merge", json=payload).status_code == 404
            app.dependency_overrides[get_current_subject] = lambda: "user-a"
            assert client.post("/applications/keep/merge", json={**payload, "expected_keep_version": 9}).status_code == 409
            response = client.post("/applications/keep/merge", json=payload)
            assert response.status_code == 200
            assert response.json()["application_id"] == "keep"
    finally:
        app.dependency_overrides.clear()
