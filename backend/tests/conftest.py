from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import count

import boto3
import pytest
from moto import mock_aws

from app.domain.applications import ApplicationCreate, ApplicationStatus
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_tables import create_tables


@pytest.fixture
def repository() -> Iterator[DynamoApplicationRepository]:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_tables(client, applications_table="Applications", events_table="Events")
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
