import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime

import pytest

from app.domain.applications import (
    ApplicationCreate,
    ApplicationStatus,
    EventType,
)
from app.repositories.applications import InvalidCursor
from app.repositories.dynamodb import DynamoApplicationRepository


def test_repository_starts_with_no_applications(
    repository: DynamoApplicationRepository,
) -> None:
    page = repository.list(owner_id="user-a", limit=20, cursor=None)

    assert page.items == []
    assert page.next_cursor is None


def test_create_writes_application_and_history(repository) -> None:
    created = repository.create(
        owner_id="user-a",
        payload=ApplicationCreate(
            company="Acme",
            role="Software Engineer Intern",
            status=ApplicationStatus.APPLIED,
        ),
        application_id="app-1",
        event_id="event-1",
        occurred_at=datetime(2026, 9, 24, 16, 0, tzinfo=UTC),
    )

    assert created.application_id == "app-1"
    assert created.version == 1
    assert repository.get(owner_id="user-a", application_id="app-1") == created
    events = repository.list_events(owner_id="user-a", application_id="app-1")
    assert [event.event_type for event in events] == [EventType.CREATED]


def test_get_does_not_cross_owner_boundary(repository, application_factory) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")

    assert repository.get(owner_id="user-b", application_id="app-1") is None


def test_list_is_owner_scoped_and_paginated(repository, application_factory) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")
    application_factory(repository, owner_id="user-a", application_id="app-2")
    application_factory(repository, owner_id="user-b", application_id="app-3")

    first = repository.list(owner_id="user-a", limit=1, cursor=None)
    second = repository.list(owner_id="user-a", limit=1, cursor=first.next_cursor)

    assert [item.application_id for item in first.items + second.items] == [
        "app-1",
        "app-2",
    ]
    assert all(
        item.application_id != "app-3" for item in first.items + second.items
    )


def test_tampered_cursor_cannot_select_another_owner(repository) -> None:
    foreign_cursor = (
        urlsafe_b64encode(
            json.dumps({"application_id": "app-foreign"}).encode()
        )
        .decode()
        .rstrip("=")
    )

    page = repository.list(owner_id="user-a", limit=10, cursor=foreign_cursor)

    assert page.items == []


def test_malformed_cursor_is_rejected(repository) -> None:
    with pytest.raises(InvalidCursor):
        repository.list(owner_id="user-a", limit=10, cursor="not-a-cursor")
