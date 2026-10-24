from datetime import UTC, datetime

from app.domain.applications import Application, ApplicationStatus, Season
from app.services.application_matching import (
    Ambiguous,
    Matched,
    NoMatch,
    match_application,
    normalize_company,
    normalize_role,
)
from app.services.email_rules import ApplicationCandidate

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _candidate(
    application_id="app-1",
    *,
    company="Acme",
    role="Software Engineering Intern",
    season=None,
    year=None,
    thread_ids=(),
    requisition_ids=(),
):
    return ApplicationCandidate(
        application=Application(
            application_id=application_id,
            company=company,
            role=role,
            role_type=None,
            season=season,
            year=year,
            status=ApplicationStatus.APPLIED,
            application_date=None,
            created_at=NOW,
            updated_at=NOW,
            version=1,
        ),
        thread_ids=frozenset(thread_ids),
        requisition_ids=frozenset(requisition_ids),
    )


def _match(candidates, **kwargs):
    values = {
        "thread_id": None,
        "requisition_id": None,
        "company": None,
        "role": None,
        "season": None,
        "year": None,
    }
    values.update(kwargs)
    return match_application(candidates, **values)


def test_swe_intern_equals_software_engineering_intern():
    assert normalize_role("SWE Intern") == normalize_role(
        "Software Engineering Intern"
    )
    assert normalize_role("Software Engineer Intern") == "software engineering"
    result = _match([_candidate()], company="Acme", role="SWE Intern")
    assert isinstance(result, Matched)


def test_internship_suffix_and_year_ignored():
    assert normalize_role("Software Engineering Internship 2027") == (
        "software engineering"
    )
    assert normalize_role("Summer 2027 Software Engineering Co-op") == (
        "software engineering"
    )
    result = _match(
        [_candidate()], company="Acme", role="Software Engineering Internship 2027"
    )
    assert isinstance(result, Matched)


def test_company_suffix_ignored():
    assert normalize_company("Acme, Inc.") == "acme"
    assert normalize_company("  ACME   Corporation ") == "acme"
    result = _match(
        [_candidate(company="Acme Inc")],
        company="ACME",
        role="Software Engineering Intern",
    )
    assert isinstance(result, Matched)


def test_requisition_beats_name():
    target = _candidate("app-1", company="Other", role="Data Intern",
                        requisition_ids={"R-123"})
    decoy = _candidate("app-2")
    result = _match(
        [decoy, target],
        requisition_id=" r-123 ",
        company="Acme",
        role="Software Engineering Intern",
    )
    assert result == Matched(target)


def test_thread_match():
    target = _candidate("app-1", company="Other", thread_ids={"t-1"})
    result = _match([_candidate("app-2"), target], thread_id="t-1")
    assert result == Matched(target)


def test_different_roles_same_company_no_match():
    result = _match(
        [_candidate(role="Data Science Intern")],
        company="Acme",
        role="Software Engineering Intern",
    )
    assert result == NoMatch()


def test_conflicting_cycle_no_match():
    candidate = _candidate(season=Season.SUMMER, year=2026)
    kwargs = {"company": "Acme", "role": "Software Engineering Intern"}
    assert _match([candidate], year=2027, **kwargs) == NoMatch()
    assert _match([candidate], season=Season.FALL, **kwargs) == NoMatch()
    assert isinstance(_match([candidate], year=2026, **kwargs), Matched)
    assert isinstance(_match([candidate], **kwargs), Matched)


def test_roleless_email_matches_single_company_row():
    candidate = _candidate()
    assert _match([candidate, _candidate("app-2", company="Zed")],
                  company="Acme") == Matched(candidate)


def test_roleless_email_ambiguous_with_two_rows():
    rows = [_candidate("app-1"), _candidate("app-2", role="Data Intern")]
    assert _match(rows, company="Acme") == Ambiguous(2)


def test_empty_normalized_role_treated_as_roleless():
    assert normalize_role("Summer 2027 Internship") == ""
    rows = [_candidate("app-1"), _candidate("app-2", role="Data Intern")]
    assert _match(rows, company="Acme", role="Internship") == Ambiguous(2)
    # A row whose role normalizes to empty must not match every role.
    result = _match(
        [_candidate(role="Internship")],
        company="Acme",
        role="Software Engineering Intern",
    )
    assert result == NoMatch()


def test_swe_word_boundary():
    assert normalize_role("answer") == "answer"
    assert normalize_role("SWE") == "software engineering"
    assert normalize_role("Software Engineering Intern") == "software engineering"


def test_none_company_or_role_never_matches_by_name_but_matches_by_thread():
    nameless = _candidate(company=None, role=None, thread_ids={"t-1"})
    assert _match([nameless], company="Acme", role="SWE") == NoMatch()
    assert _match([nameless], company="Acme") == NoMatch()
    assert _match([nameless], thread_id="t-1") == Matched(nameless)


def test_multiple_requisition_matches_ambiguous():
    rows = [_candidate("a", requisition_ids={"R1"}),
            _candidate("b", requisition_ids={"R1"})]
    assert _match(rows, requisition_id="r1") == Ambiguous(2)
