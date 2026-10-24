from datetime import UTC, datetime

import pytest

from app.services.email_filters import filter_reason
from app.services.gmail_message import ParsedGmailMessage


def msg(sender: str, subject: str, text: str = "Hello") -> ParsedGmailMessage:
    return ParsedGmailMessage(
        message_id="m1", thread_id="t1",
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
        subject=subject, sender=sender, text=text,
    )


def test_linkedin_job_alert_filtered_as_job_alert():
    m = msg("LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
            "\"software engineer\": Acme Corp - Remote and more")
    assert filter_reason(m) == "job_alert"


def test_digest_with_many_roles_filtered():
    m = msg("LinkedIn <jobs-listings@linkedin.com>",
            "Jobs you may be interested in: 25 new jobs for you")
    assert filter_reason(m) == "digest"


def test_application_wording_vetoes_filter():
    m = msg("LinkedIn <jobs-noreply@linkedin.com>", "Thank you for applying to Acme")
    assert filter_reason(m) is None


def test_unknown_sender_not_filtered():
    assert filter_reason(msg("Jane <jane@acme.test>", "Quick question")) is None


@pytest.mark.parametrize("text", [
    "Your application was received.", "Please schedule an interview.",
    "Complete the online assessment.", "We have an offer for you.",
    "Unfortunately we will not proceed.",
])
def test_veto_phrases_in_body(text):
    m = msg("alerts@indeed.com", "Job alert", text)
    assert filter_reason(m) is None


def test_veto_ignores_text_after_first_1000_chars():
    m = msg("alerts@indeed.com", "New job alert", "x" * 1000 + " interview")
    assert filter_reason(m) == "job_alert"


@pytest.mark.parametrize("sender,subject,reason", [
    ("Indeed <alert@indeed.com>", "Software Engineer in Austin", "job_alert"),
    ("Indeed <donotreply@match.indeed.com>", "New jobs for you", "job_alert"),
    ("Glassdoor Jobs <noreply@glassdoor.com>", "5 new data jobs", "job_alert"),
    ("ZipRecruiter <alerts@ziprecruiter.com>", "Jobs near you", "job_alert"),
    ("Handshake <digest@joinhandshake.com>", "Your weekly update", "digest"),
    ("Handshake <no-reply@joinhandshake.com>", "Jobs picked for you this week", "digest"),
    ("Acme <noreply@acme.test>", "Your weekly digest", "digest"),
    ("Acme <newsletter@acme.test>", "March news", "newsletter"),
    ("Acme <noreply@acme.test>", "Our monthly Newsletter", "newsletter"),
    ("Acme <marketing@acme.test>", "Spring savings", "marketing"),
    ("Acme <no-reply@acme.test>", "Join our webinar on careers", "event"),
    ("Acme <events@acme.test>", "You're invited!", "event"),
])
def test_pattern_families(sender, subject, reason):
    assert filter_reason(msg(sender, subject)) == reason


def test_person_sender_with_alert_words_not_filtered_unless_service():
    assert filter_reason(msg("Sam Lee <sam@acme.test>", "Job alert webinar newsletter")) is None
    assert filter_reason(msg("Sam Lee <sam@linkedin.com>", "Hi")) is None
    assert filter_reason(msg("Alerts <alerts@acme.test>", "Job alert: new roles")) == "job_alert"


def test_sender_case_insensitive_and_bare_address():
    assert filter_reason(msg("JOBALERTS-NOREPLY@LINKEDIN.COM", "NEW JOBS FOR YOU")) == "job_alert"
