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
    candidate = _candidate(status=None, threads={"thread-1"})

    decision = classify_message(_message(text), [candidate])

    assert decision.status == status
    assert decision.application_id == "app-1"
    assert decision.outcome == "applied"


def test_newsletter_or_job_ad_is_ignored() -> None:
    text = "Thank you for applying for Software Engineer Intern at Acme."

    assert classify_message(
        _message(text, subject="Career newsletter"), [_candidate()]
    ).outcome == "ignored"


def test_multiple_job_roles_without_application_event_are_ignored() -> None:
    decision = classify_message(
        _message(
            "Explore openings for Data Analyst at Beta. "
            "Browse roles for Software Engineer Intern at Acme."
        ),
        [],
    )

    assert decision.outcome == "ignored"
    assert decision.reason == "no_event"


def test_application_event_with_multiple_roles_stays_reviewable() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Data Analyst. "
            "Please see the profile for Software Engineer Intern."
        ),
        [],
    )

    assert decision.outcome == "review_required"
    assert decision.reason == "conflicting_roles"


def test_known_application_without_stage_stays_reviewable_with_multiple_roles() -> None:
    decision = classify_message(
        _message(
            "An update for Software Engineer Intern at Acme. "
            "Browse roles for Data Analyst at Beta."
        ),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.outcome == "review_required"
    assert decision.reason == "unknown_stage"
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

    assert classify_message(
        _message(text), [_candidate(threads={"thread-1"})]
    ).outcome == "applied"


def test_unknown_stage_goes_to_review_without_inventing_applied() -> None:
    decision = classify_message(
        _message("We have an update about Software Engineer Intern at Acme."),
        [_candidate(threads={"thread-1"})],
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
        [_candidate(threads={"thread-1"})],
    )

    assert decision.outcome == "applied"
    assert decision.status == ApplicationStatus.INTERVIEWING


def test_unique_thread_link_still_needs_event_role() -> None:
    decision = classify_message(
        _message("We invite you to interview."),
        [_candidate(threads={"thread-1"}), _candidate("app-2", role="Analyst")],
    )

    assert decision.outcome == "review_required"

    named_role = classify_message(
        _message("Acme invites you to interview for Software Engineer Intern."),
        [_candidate(threads={"thread-1"}), _candidate("app-2", role="Analyst")],
    )
    assert named_role.outcome == "applied"
    assert named_role.application_id == "app-1"


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


def test_thread_link_with_two_named_roles_requires_review() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Data Analyst. "
            "Please see the profile for Software Engineer Intern."
        ),
        [
            _candidate(threads={"thread-1"}),
            _candidate("app-2", role="Data Analyst"),
        ],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_thread_link_cannot_take_role_from_unrelated_sentence() -> None:
    decision = classify_message(
        _message(
            "Your Data Analyst interview is scheduled. "
            "Please see the profile for Software Engineer Intern."
        ),
        [
            _candidate(threads={"thread-1"}),
            _candidate("app-2", role="Data Analyst"),
        ],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_unique_requisition_link_still_needs_event_role() -> None:
    decision = classify_message(
        _message("Req ID: REQ-123. We invite you to interview."),
        [_candidate(reqs={"REQ-123"}), _candidate("app-2", role="Analyst")],
    )

    assert decision.outcome == "review_required"

    named_role = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern, "
            "Req ID: REQ-123."
        ),
        [_candidate(reqs={"REQ-123"}), _candidate("app-2", role="Analyst")],
    )
    assert named_role.outcome == "applied"
    assert named_role.application_id == "app-1"
    assert named_role.requisition_id == "REQ-123"


def test_previous_requisition_cannot_link_new_interview() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern. "
            "The previous Req ID OLD-123 is closed.",
            thread="unlinked-thread",
        ),
        [
            _candidate(
                "old", season=Season.SUMMER, year=2026, reqs={"OLD-123"}
            ),
            _candidate(
                "new", season=Season.SUMMER, year=2027, reqs={"NEW-456"}
            ),
        ],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_previous_requisition_in_same_sentence_cannot_link_new_interview() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern, "
            "while your previous Req ID OLD-123 is closed.",
            thread="unlinked-thread",
        ),
        [
            _candidate("old", season=Season.SUMMER, year=2026, reqs={"OLD-123"}),
            _candidate("new", season=Season.SUMMER, year=2027, reqs={"NEW-456"}),
        ],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_closed_requisition_after_role_cannot_link_new_interview() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern, "
            "Req ID OLD-123 is closed.",
            thread="unlinked-thread",
        ),
        [_candidate("old", reqs={"OLD-123"})],
    )

    assert decision.outcome == "review_required"


@pytest.mark.parametrize(
    "text",
    [
        "Acme invites you to interview regarding your Data Analyst application.",
        "Acme invites you to interview with our Data Analyst team.",
    ],
)
def test_linked_event_with_unrecognized_role_context_requires_review(text) -> None:
    decision = classify_message(_message(text), [_candidate(threads={"thread-1"})])

    assert decision.outcome == "review_required"
    assert decision.application_id is None


@pytest.mark.parametrize(
    "text",
    [
        "We are unable to invite you to interview for Software Engineer Intern at Acme.",
        "We cannot offer you the Software Engineer Intern role at Acme.",
        "We will invite you to interview for Software Engineer Intern at Acme if a slot opens.",
    ],
)
def test_nonaffirmative_event_cannot_advance_status(text) -> None:
    decision = classify_message(_message(text), [_candidate()])

    assert decision.outcome != "applied"


def test_undecided_rejection_does_not_reject_application() -> None:
    decision = classify_message(
        _message(
            "We have not decided not to move forward with your application "
            "for Software Engineer Intern at Acme."
        ),
        [_candidate()],
    )

    assert decision.outcome != "applied"


def test_company_elsewhere_in_message_cannot_select_application() -> None:
    decision = classify_message(
        _message(
            "Beta invites you to interview for Software Engineer Intern. "
            "Acme is listed below."
        ),
        [_candidate()],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_unlinked_offer_cannot_update_application() -> None:
    message = ParsedGmailMessage(
        **{
            **_message("Acme is pleased to offer you the Software Engineer Intern role.").__dict__,
            "sender": "Unknown <random@unrelated.test>",
            "thread_id": "unrelated-thread",
        }
    )
    decision = classify_message(message, [_candidate()])

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_referral_company_in_event_cannot_replace_employer() -> None:
    decision = classify_message(
        _message(
            "Beta invites you to interview for Software Engineer Intern at Beta, "
            "and Acme handled the referral."
        ),
        [_candidate()],
    )

    assert decision.outcome == "review_required"
    assert decision.application_id is None


def test_forwarded_old_event_requires_review() -> None:
    message = _message("Acme invites you to interview for Software Engineer Intern.")
    message = ParsedGmailMessage(**{**message.__dict__, "is_forwarded": True})

    assert classify_message(message, [_candidate()]).outcome == "review_required"


def test_old_event_in_reply_subject_cannot_advance_status() -> None:
    decision = classify_message(
        _message(
            "FYI, sharing the update.",
            subject="Re: Acme invites you to interview for Software Engineer Intern",
        ),
        [_candidate()],
    )

    assert decision.outcome != "applied"


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
        _candidate("summer", season=Season.SUMMER, year=2027, threads={"thread-1"}),
        _candidate("fall", season=Season.FALL, year=2027, threads={"thread-1"}),
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


def test_conflicting_subject_and_body_cycles_require_review() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern. "
            "Browse our Fall 2028 openings.",
            subject="Summer 2027 interview invitation",
        ),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.outcome == "review_required"


def test_unrelated_promotion_does_not_set_application_cycle() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern. "
            "Browse our Fall 2028 openings.",
            subject="Interview invitation",
        ),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.outcome == "applied"
    assert decision.season is None
    assert decision.year is None


def test_unrelated_cycle_in_same_sentence_does_not_set_application_cycle() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern; "
            "browse our Fall 2028 openings.",
            subject="Interview invitation",
        ),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.outcome == "applied"
    assert decision.season is None
    assert decision.year is None


def test_unsupported_cycle_remains_unknown() -> None:
    decision = classify_message(
        _message(
            "Acme invites you to interview for Software Engineer Intern."
        ),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.season is None
    assert decision.year is None


def test_ordering_is_checked_per_field_at_persistence_and_terminal_reopening_requires_review() -> None:
    text = "Acme invites you to interview for Software Engineer Intern."
    old = classify_message(
        _message(text), [_candidate(updated_at=NOW + timedelta(days=1), threads={"thread-1"})]
    )
    terminal = classify_message(
        _message(text), [_candidate(status=ApplicationStatus.REJECTED, threads={"thread-1"})]
    )

    assert old.outcome == "applied"
    assert terminal.outcome == "review_required"


def test_known_stage_cannot_regress_to_applied() -> None:
    decision = classify_message(
        _message("Thank you for applying for Software Engineer Intern at Acme."),
        [_candidate(status=ApplicationStatus.INTERVIEWING, threads={"thread-1"})],
    )

    assert decision.outcome == "review_required"


@pytest.mark.parametrize("cycle", ["Winter 2026-27", "Fall 2026/2027", "Winter 2026–27"])
def test_year_range_is_not_extracted_as_a_cycle(cycle) -> None:
    decision = classify_message(
        _message(f"Acme invites you to interview for Software Engineer Intern in {cycle} at Acme."),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.outcome == "applied"
    assert decision.status == ApplicationStatus.INTERVIEWING
    assert decision.season is None
    assert decision.year is None


def test_single_year_cycle_before_punctuation_is_still_extracted() -> None:
    decision = classify_message(
        _message("Acme invites you to interview for Software Engineer Intern in Summer 2026."),
        [_candidate(threads={"thread-1"})],
    )

    assert decision.season == Season.SUMMER
    assert decision.year == 2026
