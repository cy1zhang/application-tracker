from datetime import UTC, datetime, timedelta

import boto3
from moto import mock_aws

from app.domain.applications import ApplicationCreate, ApplicationStatus
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.email_rules import RuleDecision
from app.services.gmail_message import ParsedGmailMessage
from app.services.sync_candidates import load_candidates

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def test_candidate_loader_preserves_owner_scoped_thread_and_requisition_links() -> None:
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(client, applications_table="Applications", events_table="Events")
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        for owner, app_id, role in [
            ("owner-a", "app-1", "Engineer"),
            ("owner-a", "app-2", "Designer"),
            ("owner-b", "app-3", "Engineer"),
        ]:
            applications.create(
                owner_id=owner,
                payload=ApplicationCreate(company="Acme", role=role, status="applied"),
                application_id=app_id,
                event_id=f"create-{app_id}",
                occurred_at=NOW - timedelta(days=1),
            )
        emails.record(
            owner_id="owner-a",
            message=ParsedGmailMessage(
                message_id="msg-1", thread_id="thread-1", received_at=NOW,
                subject="Update", sender="jobs@acme.test", text="Interview for Engineer",
            ),
            decision=RuleDecision(
                outcome="applied", reason="explicit_event", application_id="app-1",
                status=ApplicationStatus.INTERVIEWING, requisition_id="req-123",
            ),
            application_version=1,
        )

        candidates = load_candidates("owner-a", applications)

        assert {item.application.application_id for item in candidates} == {"app-1", "app-2"}
        linked = next(item for item in candidates if item.application.application_id == "app-1")
        assert linked.thread_ids == frozenset({"thread-1"})
        assert linked.requisition_ids == frozenset({"req-123"})
        assert linked.last_status_event_at == NOW
        assert all(item.application.application_id != "app-3" for item in candidates)
