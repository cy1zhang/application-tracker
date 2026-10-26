from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta

import boto3
import pytest
from moto import mock_aws

from app.domain.applications import ApplicationCreate
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_reviews import DynamoReviewRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.email_processing import process_message
from app.services.email_rules import ApplicationCandidate

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _gmail(message_id: str, text: str) -> dict:
    return {
        "id": message_id,
        "threadId": "thread-1",
        "internalDate": str(int(NOW.timestamp() * 1000)),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": "Application update"},
                {"name": "From", "value": "Recruiting <jobs@acme.test>"},
            ],
            "body": {"data": urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        },
    }


def test_processing_is_owner_scoped_idempotent_and_reviewable() -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_tables(client, applications_table="Applications", events_table="Events")
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        application = applications.create(
            owner_id="user-a",
            payload=ApplicationCreate(
                company="Acme", role="Software Engineer Intern", status="applied"
            ),
            application_id="app-1",
            event_id="create-1",
            occurred_at=NOW - timedelta(days=1),
        )
        candidates = [
            ApplicationCandidate(application=application, thread_ids=frozenset({"thread-1"}))
        ]
        clear = _gmail(
            "msg-1", "Acme invites you to interview for Software Engineer Intern."
        )
        first = process_message("user-a", clear, candidates, emails)
        repeated = process_message("user-a", clear, candidates, emails)
        ambiguous = process_message(
            "user-a", _gmail("msg-2", "Acme invites you to interview."), candidates, emails
        )
        foreign = process_message("user-b", clear, candidates, emails)
        malformed = process_message(
            "user-a", {**_gmail("bad-1", "text"), "payload": {}}, candidates, emails
        )
        process_message(
            "user-a", {**_gmail("bad-2", "text"), "threadId": "../outside", "payload": {}},
            candidates, emails,
        )
        reviews = DynamoReviewRepository(
            client=client, applications_table="Applications", events_table="Events"
        ).list_pending(owner_id="user-a", limit=100, cursor=None).items
        malformed_review = next(item for item in reviews if item.message_id == "bad-1")

        assert first.outcome == "applied"
        assert repeated == first
        assert ambiguous.outcome == "review_required"
        assert foreign.outcome == "review_required"
        assert malformed.outcome == "review_required"
        assert malformed_review.source_url == "https://mail.google.com/mail/u/0/#all/thread-1"
        assert malformed_review.received_at == NOW
        assert next(item for item in reviews if item.message_id == "bad-2").source_url is None
        assert applications.get(owner_id="user-a", application_id="app-1").version == 2
        assert len(applications.list_events(owner_id="user-a", application_id="app-1")) == 2


def test_unparseable_review_keeps_bounded_subject_and_sender_headers() -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_tables(client, applications_table="Applications", events_table="Events")
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        with_headers = {
            **_gmail("bad-h", "text"),
            "payload": {
                "headers": [
                    {"name": "subject", "value": "S" * 400},
                    {"name": "From", "value": "Recruiting <jobs@acme.test>"},
                    {"name": "To", "value": "me@example.test"},
                ]
            },
        }
        without_headers = {**_gmail("bad-n", "text"), "payload": {"headers": "oops"}}
        process_message("user-a", with_headers, [], emails)
        process_message("user-a", without_headers, [], emails)
        reviews = DynamoReviewRepository(
            client=client, applications_table="Applications", events_table="Events"
        ).list_pending(owner_id="user-a", limit=100, cursor=None).items
        with_item = next(item for item in reviews if item.message_id == "bad-h")
        without_item = next(item for item in reviews if item.message_id == "bad-n")

        assert with_item.subject == "S" * 300
        assert with_item.sender == "Recruiting <jobs@acme.test>"
        assert without_item.subject is None
        assert without_item.sender is None


@pytest.mark.parametrize("sender,subject", [
    ("News <newsletter@nytimes.com>", "The Morning: a quiet weekend"),
    ("NVIDIA <news@nvidia.com>", "Your Personalized Developer News from NVIDIA"),
    ("Shop <orders@amazon.com>", "Delivered 1 item: Notebook"),
    ("Amazon Business <offers@business.amazon.com>", "Pat, Amazon Business offers business-only pricing"),
    ("Updates <mailer@substack.com>", "“A thought on weekends”"),
    ("VIZ <news@email.viz.com>", "A new anime arrives on Blu-ray"),
    ("Winner <news@searchdoc.live>", "You Are Our Winner! Free E-Bike Gift"),
    ("Notice <alerts@unknown.test>", "We've blocked your account! Your photos and videos will be deleted"),
    ("Jobright <hello@jobright.ai>", "Welcome to Jobright!"),
    ("Fidelity <news@fidelity.com>", "Upgrade your resume with these tips"),
])
def test_unparseable_non_application_mail_is_ignored_in_auto_mode(sender, subject) -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb", region_name="us-east-1",
            aws_access_key_id="testing", aws_secret_access_key="testing",
        )
        create_tables(client, applications_table="Applications", events_table="Events")
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        malformed = {**_gmail("noise", "text"), "payload": {"headers": [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": sender},
        ]}}

        outcome = process_message("user-a", malformed, [], emails, mode="on")
        repeated = process_message("user-a", malformed, [], emails, mode="on")
        reviews = DynamoReviewRepository(
            client=client, applications_table="Applications", events_table="Events"
        ).list_pending(owner_id="user-a", limit=100, cursor=None)

        assert outcome.outcome == "ignored"
        assert outcome.reason == "filtered:non_application_headers"
        assert repeated == outcome
        assert reviews.items == []


@pytest.mark.parametrize("sender,subject", [
    ("Recruiting <jobs@acme.test>", "Your recent job application for Summer 2027 Internship"),
    ("Recruiting <jobs@oraclecloud.com>", "Your recent job application for Summer 2027 Internship"),
    ("Shop <orders@amazon.com>", "Your application was received"),
    ("News <newsletter@nytimes.com>", "Interview invitation for Software Intern"),
    ("Updates <mailer@substack.com>", "Update on your candidacy"),
    ("Jane <jane@acme.test>", "A quick update"),
])
def test_unparseable_possible_application_stays_reviewable(sender, subject) -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb", region_name="us-east-1",
            aws_access_key_id="testing", aws_secret_access_key="testing",
        )
        create_tables(client, applications_table="Applications", events_table="Events")
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        malformed = {**_gmail("candidate", "text"), "payload": {"headers": [
            {"name": "Subject", "value": subject},
            {"name": "From", "value": sender},
        ]}}

        outcome = process_message("user-a", malformed, [], emails, mode="on")
        reviews = DynamoReviewRepository(
            client=client, applications_table="Applications", events_table="Events"
        ).list_pending(owner_id="user-a", limit=100, cursor=None)

        assert outcome.outcome == "review_required"
        assert outcome.reason == "unparseable_message"
        assert len(reviews.items) == 1
