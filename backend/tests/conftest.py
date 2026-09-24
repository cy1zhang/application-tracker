from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count

import boto3
import pytest
from moto import mock_aws

from app.domain.applications import ApplicationCreate, ApplicationStatus
from app.repositories.dynamodb import DynamoApplicationRepository


def create_application_table(client) -> None:
    client.create_table(
        TableName="Applications",
        KeySchema=[
            {"AttributeName": "owner_id", "KeyType": "HASH"},
            {"AttributeName": "application_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "application_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def create_event_table(client) -> None:
    client.create_table(
        TableName="Events",
        KeySchema=[
            {"AttributeName": "owner_id", "KeyType": "HASH"},
            {"AttributeName": "event_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "event_id", "AttributeType": "S"},
            {"AttributeName": "owner_application_id", "AttributeType": "S"},
            {"AttributeName": "occurred_at_event_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "application-events-index",
                "KeySchema": [
                    {"AttributeName": "owner_application_id", "KeyType": "HASH"},
                    {"AttributeName": "occurred_at_event_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def repository() -> Iterator[DynamoApplicationRepository]:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_application_table(client)
        create_event_table(client)
        yield DynamoApplicationRepository(
            client=client,
            applications_table="Applications",
            events_table="Events",
        )


@pytest.fixture
def application_factory():
    sequence = count(1)

    def create(repository, *, owner_id: str, application_id: str, **overrides):
        number = next(sequence)
        values = {
            "company": "Acme",
            "role": "Software Engineer Intern",
            "status": ApplicationStatus.APPLIED,
        }
        values.update(overrides)
        return repository.create(
            owner_id=owner_id,
            payload=ApplicationCreate(**values),
            application_id=application_id,
            event_id=f"event-create-{number}",
            occurred_at=datetime(2026, 9, 24, tzinfo=UTC)
            + timedelta(seconds=number),
        )

    return create
