from datetime import UTC, datetime, timedelta

import pytest

from app.domain.applications import Application, ApplicationStatus, Season
from app.services.email_rules import ApplicationCandidate, classify_message
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _message(text: str, *, subject: str = "Application update", thread="thread-1"):
    return ParsedGmailMessage(
        message_id="msg-1",
        thread_id=thread,
        received_at=NOW,
        subject=subject,
        sender="Recruiting <jobs@acme.test>",
        text=text,
    )


def _candidate(
    application_id="app-1",
    *,
    company="Acme",
    role="Software Engineer Intern",
    status=ApplicationStatus.APPLIED,
    season=None,
    year=None,
    threads=frozenset(),
    reqs=frozenset(),
    updated_at=None,
):
    return ApplicationCandidate(
        application=Application(
            application_id=application_id,
            company=company,
            role=role,
            role_type="internship",
            season=season,
            year=year,
            status=status,
            application_date=None,
            created_at=NOW - timedelta(days=10),
            updated_at=updated_at or NOW - timedelta(days=1),
            version=1,
        ),
        thread_ids=frozenset(threads),
        requisition_ids=frozenset(reqs),
    )


@pytest.mark.parametrize(
    ("text", "status"),
    [
        (
            "Thank you for applying for Software Engineer Intern at Acme.",
            ApplicationStatus.APPLIED,
        ),
        (
            (
                "Acme invites you to complete an online assessment for "
                "Software Engineer Intern."
            ),
            ApplicationStatus.OA_RECEIVED,
        ),
        (
            "Acme invites you to interview for Software Engineer Intern.",
            ApplicationStatus.INTERVIEWING,
        ),
        (
            "Acme is pleased to offer you the Software Engineer Intern position.",
            ApplicationStatus.OFFER,
        ),
        (
            (
                "Acme has decided not to move forward with your application "
                "for Software Engineer Intern."
            ),
            ApplicationStatus.REJECTED,
        ),
    ],
)
def test_explicit_status_rules_require_no_ai(text, status) -> None:
    candidate = _candidate(status=None)

    decision = classify_message(_message(text), [candidate])

    assert decision.status == status
    assert decision.application_id == "app-1"
    assert decision.outcome == "applied"


def test_newsletter_or_job_ad_is_ignored() -> None:
    text = "Thank you for applying for Software Engineer Intern at Acme."

    assert classify_message(
        _message(text, subject="Career newsletter"), [_candidate()]
    ).outcome == "ignored"
    assert classify_message(
        _message("Apply now for Software Engineer Intern at Acme."), [_candidate()]
    ).outcome == "ignored"


def test_hypothetical_language_is_not_an_event() -> None:
    text = "If we invite you to interview for Software Engineer Intern at Acme, "
    text += "we will contact you."

    assert classify_message(_message(text), [_candidate()]).outcome == "ignored"


def test_later_if_you_have_questions_does_not_hide_an_event() -> None:
    text = (
        "Acme invites you to interview for Software Engineer Intern. "
        "If you have questions, contact us."
    )

    assert classify_message(_message(text), [_candidate()]).outcome == "applied"


def test_unknown_stage_goes_to_review_without_inventing_applied() -> None:
    decision = classify_message(
        _message("We have an update about Software Engineer Intern at Acme."),
        [_candidate()],
    )

    assert decision.outcome == "review_required"
    assert decision.status is None


def test_competing_stage_phrases_go_to_review() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern. "
            "We have decided not to move forward with your application."
        ),
        [_candidate()],
    )

    assert decision.outcome == "review_required"


def test_new_body_event_takes_precedence_over_old_thread_subject() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern.",
            subject="Re: Thank you for applying",
        ),
        [_candidate()],
    )

    assert decision.outcome == "applied"
    assert decision.status == ApplicationStatus.INTERVIEWING


def test_unique_thread_link_is_strong_match() -> None:
    decision = classify_message(
        _message("We invite you to interview."),
        [_candidate(threads={"thread-1"}), _candidate("app-2", role="Analyst")],
    )

    assert decision.outcome == "applied"
    assert decision.application_id == "app-1"


def test_thread_link_cannot_override_explicit_different_role() -> None:
    decision = classify_message(
        _message("Acme invites you to interview for Data Analyst."),
        [
            _candidate(threads={"thread-1"}),
            _candidate("app-2", role="Data Analyst"),
        ],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_unique_requisition_link_is_strong_match() -> None:
    decision = classify_message(
        _message("Req ID: REQ-123. We invite you to interview."),
        [_candidate(reqs={"REQ-123"}), _candidate("app-2", role="Analyst")],
    )

    assert decision.outcome == "applied"
    assert decision.application_id == "app-1"
    assert decision.requisition_id == "REQ-123"


def test_company_alone_does_not_select_among_roles() -> None:
    decision = classify_message(
        _message("Acme invites you to interview."),
        [_candidate(), _candidate("app-2", role="Data Analyst")],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_shorter_role_is_not_matched_inside_longer_role() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Senior Software Engineer Intern."
        ),
        [_candidate(role="Software Engineer Intern")],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_multiple_cycles_require_explicit_cycle() -> None:
    candidates = [
        _candidate("summer", season=Season.SUMMER, year=2027),
        _candidate("fall", season=Season.FALL, year=2027),
    ]
    no_cycle = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern."
        ),
        candidates,
    )
    with_cycle = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern "
            "in Summer 2027."
        ),
        candidates,
    )

    assert no_cycle.outcome == "review_required"
    assert with_cycle.outcome == "applied"
    assert with_cycle.application_id == "summer"
    assert with_cycle.season == Season.SUMMER
    assert with_cycle.year == 2027


def test_unsupported_cycle_remains_unknown() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern."
        ),
        [_candidate()],
    )

    assert decision.season is None
    assert decision.year is None


def test_older_evidence_and_terminal_reopening_require_review() -> None:
    text = "Acme invites you to interview for Software Engineer Intern."
    old = classify_message(
        _message(text), [_candidate(updated_at=NOW + timedelta(days=1))]
    )
    terminal = classify_message(
        _message(text), [_candidate(status=ApplicationStatus.REJECTED)]
    )

    assert old.outcome == "review_required"
    assert terminal.outcome == "review_required"


def test_known_stage_cannot_regress_to_applied() -> None:
    decision = classify_message(
        _message("Thank you for applying for Software Engineer Intern at Acme."),
        [_candidate(status=ApplicationStatus.INTERVIEWING)],
    )

    assert decision.outcome == "review_required"
