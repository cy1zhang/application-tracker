import json
from base64 import urlsafe_b64encode
from datetime import UTC, datetime

import boto3
import pytest
from boto3.dynamodb.types import TypeSerializer
from botocore.stub import Stubber

from app.domain.applications import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationStatus,
    EventType,
    FieldChange,
    Season,
)
from app.repositories.applications import (
    ApplicationNotFound,
    InvalidCursor,
    VersionConflict,
)
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


def test_non_ascii_cursor_is_rejected(repository) -> None:
    with pytest.raises(InvalidCursor):
        repository.list(owner_id="user-a", limit=10, cursor="☃")


def test_cursor_with_invalid_suffix_is_rejected(repository) -> None:
    valid_cursor = (
        urlsafe_b64encode(json.dumps({"application_id": "app-1"}).encode())
        .decode()
        .rstrip("=")
    )

    with pytest.raises(InvalidCursor):
        repository.list(owner_id="user-a", limit=10, cursor=f"{valid_cursor}!!!!")


def test_list_uses_a_strongly_consistent_query() -> None:
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    repository = DynamoApplicationRepository(
        client=client,
        applications_table="Applications",
        events_table="Events",
    )
    with Stubber(client) as stubber:
        stubber.add_response(
            "query",
            {"Items": []},
            {
                "TableName": "Applications",
                "KeyConditionExpression": "owner_id = :owner_id",
                "ExpressionAttributeValues": {":owner_id": {"S": "user-a"}},
                "FilterExpression": "attribute_not_exists(deleted_at)",
                "Limit": 20,
                "ConsistentRead": True,
            },
        )

        page = repository.list(owner_id="user-a", limit=20, cursor=None)

    assert page.items == []


def test_list_events_reads_every_dynamodb_page() -> None:
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    repository = DynamoApplicationRepository(
        client=client,
        applications_table="Applications",
        events_table="Events",
    )
    serializer = TypeSerializer()

    def serialize_item(item):
        return {key: serializer.serialize(value) for key, value in item.items()}

    first_event = serialize_item(
        {
            "owner_id": "user-a",
            "event_id": "event-1",
            "application_id": "app-1",
            "owner_application_id": "user-a#app-1",
            "occurred_at_event_id": "2026-09-24T00:00:00+00:00#event-1",
            "event_type": "created",
            "origin": "user",
            "occurred_at": "2026-09-24T00:00:00+00:00",
            "changes": {},
        }
    )
    second_event = serialize_item(
        {
            "owner_id": "user-a",
            "event_id": "event-2",
            "application_id": "app-1",
            "owner_application_id": "user-a#app-1",
            "occurred_at_event_id": "2026-09-25T00:00:00+00:00#event-2",
            "event_type": "updated",
            "origin": "user",
            "occurred_at": "2026-09-25T00:00:00+00:00",
            "changes": {},
        }
    )
    last_key = {
        key: first_event[key]
        for key in (
            "owner_id",
            "event_id",
            "owner_application_id",
            "occurred_at_event_id",
        )
    }
    query = {
        "TableName": "Events",
        "IndexName": "application-events-index",
        "KeyConditionExpression": "owner_application_id = :owner_application_id",
        "ExpressionAttributeValues": {
            ":owner_application_id": {"S": "user-a#app-1"}
        },
        "ScanIndexForward": True,
    }
    with Stubber(client) as stubber:
        stubber.add_response(
            "query",
            {"Items": [first_event], "LastEvaluatedKey": last_key},
            query,
        )
        stubber.add_response(
            "query",
            {"Items": [second_event]},
            {**query, "ExclusiveStartKey": last_key},
        )

        events = repository.list_events(owner_id="user-a", application_id="app-1")

    assert [event.event_id for event in events] == ["event-1", "event-2"]


def test_update_clears_explicit_null_and_records_only_changed_fields(
    repository,
    application_factory,
) -> None:
    application_factory(
        repository,
        owner_id="user-a",
        application_id="app-1",
        season=Season.SUMMER,
    )

    updated = repository.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, season=None),
        event_id="event-update",
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert updated.season is None
    assert updated.role == "Software Engineer Intern"
    assert updated.version == 2
    event = repository.list_events(owner_id="user-a", application_id="app-1")[-1]
    assert event.changes == {"season": FieldChange(before="summer", after=None)}


def test_same_value_update_does_not_increment_or_write_event(
    repository,
    application_factory,
) -> None:
    original = application_factory(
        repository,
        owner_id="user-a",
        application_id="app-1",
    )

    unchanged = repository.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, role=original.role),
        event_id="event-no-op",
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert unchanged == original
    assert [
        event.event_id
        for event in repository.list_events(
            owner_id="user-a",
            application_id="app-1",
        )
    ] == ["event-create-1"]


def test_stale_update_writes_neither_application_nor_event(
    repository,
    application_factory,
) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")

    with pytest.raises(VersionConflict):
        repository.update(
            owner_id="user-a",
            application_id="app-1",
            patch=ApplicationPatch(expected_version=7, role="Changed"),
            event_id="event-stale",
            occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
        )

    assert repository.get(owner_id="user-a", application_id="app-1").version == 1
    assert all(
        event.event_id != "event-stale"
        for event in repository.list_events(
            owner_id="user-a",
            application_id="app-1",
        )
    )


def test_stale_delete_writes_neither_tombstone_nor_event(
    repository,
    application_factory,
) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")

    with pytest.raises(VersionConflict):
        repository.delete(
            owner_id="user-a",
            application_id="app-1",
            expected_version=7,
            event_id="event-stale-delete",
            occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
        )

    assert repository.get(owner_id="user-a", application_id="app-1").version == 1
    assert all(
        event.event_id != "event-stale-delete"
        for event in repository.list_events(
            owner_id="user-a",
            application_id="app-1",
        )
    )


def test_deleted_application_is_hidden_and_cannot_be_updated(
    repository,
    application_factory,
) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")
    repository.delete(
        owner_id="user-a",
        application_id="app-1",
        expected_version=1,
        event_id="event-delete",
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert repository.get(owner_id="user-a", application_id="app-1") is None
    assert repository.list(owner_id="user-a", limit=20, cursor=None).items == []
    assert [
        event.event_type
        for event in repository.list_events(
            owner_id="user-a",
            application_id="app-1",
        )
    ] == [EventType.CREATED, EventType.DELETED]
    with pytest.raises(ApplicationNotFound):
        repository.update(
            owner_id="user-a",
            application_id="app-1",
            patch=ApplicationPatch(expected_version=2, role="Changed"),
            event_id="event-after-delete",
            occurred_at=datetime(2026, 9, 26, tzinfo=UTC),
        )
