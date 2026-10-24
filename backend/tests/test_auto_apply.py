from datetime import UTC, datetime

from app.domain.ai import ApplicationKind
from app.domain.applications import (
    Application,
    ApplicationStatus,
    RoleType,
    Season,
)
from app.services.auto_apply import AutoDecision, MessageRef, decide
from app.services.email_rules import ApplicationCandidate, would_regress

NOW = datetime(2026, 10, 18, tzinfo=UTC)
REF = MessageRef(message_id="m1", thread_id="t1", received_at=NOW)


def extraction(**overrides):
    from app.services.ai_extraction import ValidatedExtraction

    values = {
        "company": "Acme",
        "role": "Software Engineering Intern",
        "employment_type": RoleType.INTERNSHIP,
        "season": Season.SUMMER,
        "year": 2027,
        "status": ApplicationStatus.APPLIED,
        "requisition_id": None,
        "evidence": {},
        "status_in_subject_only": False,
        "kind": ApplicationKind.APPLICATION_UPDATE,
    }
    values.update(overrides)
    return ValidatedExtraction(**values)


def candidate(
    application_id="app-1",
    *,
    status=ApplicationStatus.APPLIED,
    season=Season.SUMMER,
    year=2027,
    role_type=RoleType.INTERNSHIP,
    thread_ids=(),
):
    return ApplicationCandidate(
        application=Application(
            application_id=application_id,
            company="Acme",
            role="Software Engineering Intern",
            role_type=role_type,
            season=season,
            year=year,
            status=status,
            application_date=None,
            created_at=NOW,
            updated_at=NOW,
            version=1,
        ),
        thread_ids=frozenset(thread_ids),
    )


def test_non_application_kind_is_ignored():
    d = decide(
        extraction(kind=ApplicationKind.JOB_ALERT), message_ref=REF, candidates=[]
    )
    assert (d.action, d.reason) == ("ignore", "not_application:job_alert")


def test_missing_kind_is_ignored_as_unknown():
    d = decide(extraction(kind=None), message_ref=REF, candidates=[])
    assert (d.action, d.reason) == ("ignore", "not_application:unknown")


def test_status_in_subject_only_needs_look():
    d = decide(
        extraction(status_in_subject_only=True), message_ref=REF, candidates=[]
    )
    assert (d.action, d.reason) == ("needs_look", "status_in_subject_only")


def test_ambiguous_match_needs_look():
    d = decide(
        extraction(),
        message_ref=REF,
        candidates=[candidate("a", season=None), candidate("b", season=None)],
    )
    assert (d.action, d.reason) == ("needs_look", "ambiguous_match")


def test_regression_needs_look():
    d = decide(
        extraction(status=ApplicationStatus.INTERVIEWING),
        message_ref=REF,
        candidates=[candidate(status=ApplicationStatus.OFFER)],
    )
    assert (d.action, d.reason) == ("needs_look", "status_regression")


def test_no_change_is_ignored():
    d = decide(extraction(), message_ref=REF, candidates=[candidate()])
    assert (d.action, d.reason) == ("ignore", "no_change")
    assert d.fields == {}


def test_matched_with_new_status_updates():
    d = decide(
        extraction(status=ApplicationStatus.INTERVIEWING),
        message_ref=REF,
        candidates=[candidate()],
    )
    assert (d.action, d.reason, d.application_id) == (
        "update",
        "ai_update",
        "app-1",
    )
    assert d.fields == {"status": ApplicationStatus.INTERVIEWING}


def test_update_without_status_writes_only_other_fields():
    d = decide(
        extraction(status=None, season=None, year=None, employment_type=None),
        message_ref=REF,
        candidates=[candidate(role_type=None)],
    )
    assert d.action == "ignore"
    d = decide(
        extraction(status=None),
        message_ref=REF,
        candidates=[candidate(role_type=None)],
    )
    assert d.action == "update"
    assert d.fields == {"role_type": RoleType.INTERNSHIP}


def test_update_never_overwrites_existing_cycle():
    d = decide(
        extraction(status=ApplicationStatus.INTERVIEWING),
        message_ref=MessageRef("m1", "t1", NOW),
        candidates=[candidate(thread_ids=["t1"])],
    )
    assert "season" not in d.fields and "year" not in d.fields


def test_update_fills_null_cycle():
    d = decide(
        extraction(status=None),
        message_ref=REF,
        candidates=[candidate(season=None, year=None)],
    )
    assert d.fields == {"season": Season.SUMMER, "year": 2027}


def test_no_match_with_company_and_status_creates():
    d = decide(
        extraction(requisition_id="R-1"), message_ref=REF, candidates=[]
    )
    assert (d.action, d.reason, d.application_id) == ("create", "ai_create", None)
    assert d.fields == {
        "company": "Acme",
        "role": "Software Engineering Intern",
        "role_type": RoleType.INTERNSHIP,
        "season": Season.SUMMER,
        "year": 2027,
        "status": ApplicationStatus.APPLIED,
    }
    assert d.requisition_id == "R-1"


def test_no_match_without_status_or_company_ignored():
    for ex in (extraction(status=None), extraction(company=None)):
        d = decide(ex, message_ref=REF, candidates=[])
        assert (d.action, d.reason) == ("ignore", "no_status_no_match")


def test_status_never_defaulted_on_create():
    d = decide(extraction(status=None), message_ref=REF, candidates=[])
    assert d.action != "create"


def test_later_rejection_after_interviewing_updates():
    d = decide(
        extraction(status=ApplicationStatus.REJECTED),
        message_ref=REF,
        candidates=[candidate(status=ApplicationStatus.INTERVIEWING)],
    )
    assert (d.action, d.fields) == (
        "update",
        {"status": ApplicationStatus.REJECTED},
    )


def test_reopen_rejected_needs_look():
    d = decide(
        extraction(status=ApplicationStatus.INTERVIEWING),
        message_ref=REF,
        candidates=[candidate(status=ApplicationStatus.REJECTED)],
    )
    assert (d.action, d.reason) == ("needs_look", "status_regression")


def test_would_regress_none_to_applied_is_false():
    assert would_regress(None, ApplicationStatus.APPLIED) is False


def test_update_from_null_status_to_applied_is_not_regression():
    d = decide(
        extraction(status=ApplicationStatus.APPLIED),
        message_ref=REF,
        candidates=[candidate(status=None)],
    )
    assert (d.action, d.reason) == ("update", "ai_update")
    assert d.fields == {"status": ApplicationStatus.APPLIED}


def test_decision_is_frozen():
    d = AutoDecision("ignore", "x", None, {})
    try:
        d.reason = "y"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("expected frozen")


def test_thread_match_with_conflicting_cycle_creates_separate_row():
    d = decide(
        extraction(year=2028),
        message_ref=REF,
        candidates=[candidate(thread_ids=["t1"])],
    )
    assert (d.action, d.reason) == ("create", "ai_create")


def test_requisition_match_with_conflicting_cycle_creates_separate_row():
    c = ApplicationCandidate(
        application=candidate().application,
        requisition_ids=frozenset({"R-1"}),
    )
    d = decide(
        extraction(season=Season.FALL, requisition_id="R-1"),
        message_ref=MessageRef("m1", None, NOW),
        candidates=[c],
    )
    assert (d.action, d.reason) == ("create", "ai_create")


def test_update_does_not_overwrite_existing_company_or_role():
    d = decide(
        extraction(company="ACME Inc", role="SWE Intern"),
        message_ref=REF,
        candidates=[candidate(thread_ids=["t1"])],
    )
    assert (d.action, d.reason) == ("ignore", "no_change")


def test_update_fills_missing_role():
    c = candidate(thread_ids=["t1"])
    c = ApplicationCandidate(
        application=c.application.model_copy(update={"role": None}),
        thread_ids=c.thread_ids,
    )
    d = decide(extraction(), message_ref=REF, candidates=[c])
    assert d.action == "update"
    assert d.fields == {"role": "Software Engineering Intern"}
