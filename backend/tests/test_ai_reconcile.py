from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.applications import Application, ApplicationStatus, Season
from app.services.ai_extraction import ValidatedExtraction
from app.services.ai_reconcile import (
    AUTO_APPLY_REASONS,
    AUTO_APPLY_STATUSES,
    ESCALATION_REASONS,
    reconcile_extraction,
)
from app.services.email_rules import (
    ApplicationCandidate,
    RuleDecision,
    classify_message,
)
from app.services.gmail_message import ParsedGmailMessage

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def _message(text="Synthetic body.", *, subject="Update", thread="thread-1"):
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
            updated_at=NOW - timedelta(days=1),
            version=1,
        ),
        thread_ids=frozenset(threads),
        requisition_ids=frozenset(reqs),
    )


def _extraction(**overrides):
    values = {
        "company": None,
        "role": None,
        "employment_type": None,
        "season": None,
        "year": None,
        "status": ApplicationStatus.INTERVIEWING,
        "requisition_id": None,
        "evidence": {"status": "invite you to interview"},
        "status_in_subject_only": False,
    }
    values.update(overrides)
    return ValidatedExtraction(**values)


def _rule(reason="unknown_stage", **kwargs):
    return RuleDecision(outcome="review_required", reason=reason, **kwargs)


def _named(**overrides):
    values = {
        "company": "Acme",
        "role": "Software Engineer Intern",
        "evidence": {
            "status": "invite you to interview",
            "company": "Acme",
            "role": "Software Engineer Intern",
        },
    }
    values.update(overrides)
    return _extraction(**values)


def test_constants():
    assert AUTO_APPLY_REASONS == frozenset({"ambiguous_application", "unknown_stage"})
    assert ESCALATION_REASONS == frozenset(
        {
            "ambiguous_application",
            "unknown_stage",
            "subject_only_event",
            "nonaffirmative_event",
            "conflicting_roles",
        }
    )


def test_thread_link_applies():
    result = reconcile_extraction(
        _rule(), _extraction(), _message(), [_candidate(threads={"thread-1"})]
    )
    assert result.outcome == "applied"
    assert result.origin == "ai"
    assert result.rule_reason == "unknown_stage"
    assert result.application_id == "app-1"
    assert result.status == ApplicationStatus.INTERVIEWING
    assert "invite you to interview" in result.evidence
    assert result.ai_proposal is not None


def test_requisition_link_applies():
    result = reconcile_extraction(
        _rule(),
        _extraction(requisition_id="REQ-1234", evidence={"status": "x", "requisition_id": "REQ-1234"}),
        _message(thread="other"),
        [_candidate(reqs={"REQ-1234"})],
    )
    assert result.outcome == "applied"
    assert result.application_id == "app-1"
    assert result.requisition_id == "REQ-1234"


def test_first_email_matches_by_exact_company_and_role():
    extraction = _named(company="  ACME ", role="software  engineer intern")
    result = reconcile_extraction(
        _rule("ambiguous_application"),
        extraction,
        _message(thread="new-thread"),
        [_candidate()],
    )
    assert result.outcome == "applied"
    assert result.application_id == "app-1"


def test_company_only_goes_to_review_without_match():
    extraction = _extraction(company="Acme", evidence={"status": "s", "company": "Acme"})
    result = reconcile_extraction(_rule(), extraction, _message(thread="n"), [_candidate()])
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"
    assert result.application_id is None
    assert result.status is None
    assert result.ai_proposal is extraction


def test_partial_company_name_does_not_match():
    extraction = _named(company="Acme Corp")
    result = reconcile_extraction(_rule(), extraction, _message(thread="n"), [_candidate()])
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_same_company_different_role_stays_separate():
    extraction = _named(role="Data Analyst Intern")
    result = reconcile_extraction(_rule(), extraction, _message(thread="n"), [_candidate()])
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_same_company_role_different_cycle_stays_separate():
    candidates = [
        _candidate("old", season=Season.SUMMER, year=2025),
        _candidate("new", season=Season.SUMMER, year=2026),
    ]
    extraction = _named(season=Season.SUMMER, year=2026)
    result = reconcile_extraction(_rule(), extraction, _message(thread="n"), candidates)
    assert result.outcome == "applied"
    assert result.application_id == "new"


def test_name_match_with_only_cycle_conflict_is_not_a_match():
    extraction = _named(year=2027)
    candidates = [_candidate(year=2026)]
    result = reconcile_extraction(_rule(), extraction, _message(thread="n"), candidates)
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_two_matching_applications_go_to_review():
    candidates = [_candidate("a"), _candidate("b")]
    result = reconcile_extraction(_rule(), _named(), _message(thread="n"), candidates)
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"
    assert result.application_id is None


def test_two_linked_applications_go_to_review():
    candidates = [_candidate("a", threads={"thread-1"}), _candidate("b", threads={"thread-1"})]
    result = reconcile_extraction(_rule(), _named(), _message(), candidates)
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_model_status_contradicting_rule_status_goes_to_review():
    rule = _rule("ambiguous_application", status=ApplicationStatus.OFFER, evidence="pleased to offer you")
    result = reconcile_extraction(
        rule, _extraction(), _message(), [_candidate(threads={"thread-1"})]
    )
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"


def test_rule_status_used_when_ai_status_null():
    rule = _rule(
        "ambiguous_application",
        status=ApplicationStatus.INTERVIEWING,
        evidence="invited you to interview",
    )
    result = reconcile_extraction(
        rule,
        _extraction(status=None, evidence={}),
        _message(),
        [_candidate(threads={"thread-1"})],
    )
    assert result.outcome == "applied"
    assert result.status == ApplicationStatus.INTERVIEWING


def test_null_status_cannot_apply_and_is_not_defaulted():
    result = reconcile_extraction(
        _rule(),
        _extraction(status=None, evidence={}),
        _message(),
        [_candidate(threads={"thread-1"}, status=None)],
    )
    assert result.outcome == "review_required"
    assert result.status is None


def test_subject_only_status_cannot_apply():
    result = reconcile_extraction(
        _rule(),
        _extraction(status_in_subject_only=True),
        _message(),
        [_candidate(threads={"thread-1"})],
    )
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"


@pytest.mark.parametrize(
    ("current", "proposed"),
    [
        (ApplicationStatus.INTERVIEWING, ApplicationStatus.APPLIED),  # regression
        (ApplicationStatus.OFFER, ApplicationStatus.INTERVIEWING),  # terminal reopen
        (ApplicationStatus.REJECTED, ApplicationStatus.OFFER),  # terminal reopen
    ],
)
def test_regression_and_terminal_reopen_go_to_review_with_match(current, proposed):
    result = reconcile_extraction(
        _rule(),
        _extraction(status=proposed),
        _message(),
        [_candidate(threads={"thread-1"}, status=current)],
    )
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"
    assert result.application_id == "app-1"
    assert result.status == proposed


def test_cycle_conflict_goes_to_review_with_match():
    # Link match does not filter on cycle when the model's cycle is absent from
    # the filter, so use a null-cycle link and a conflicting existing cycle.
    extraction = _extraction(season=Season.FALL, year=2026, evidence={"status": "s"})
    candidate = _candidate(threads={"thread-1"}, season=Season.SUMMER, year=2026)
    result = reconcile_extraction(_rule(), extraction, _message(), [candidate])
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"


@pytest.mark.parametrize(
    "reason", ["subject_only_event", "nonaffirmative_event", "conflicting_roles"]
)
def test_proposal_only_reasons_never_apply(reason):
    result = reconcile_extraction(
        _rule(reason), _extraction(), _message(), [_candidate(threads={"thread-1"})]
    )
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"
    assert result.rule_reason == reason
    assert result.application_id == "app-1"
    assert result.status == ApplicationStatus.INTERVIEWING


def test_other_owner_application_never_appears():
    # Only the caller's candidates are passed; none match, so no id is invented.
    result = reconcile_extraction(_rule(), _named(), _message(thread="n"), [])
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_evidence_is_bounded_and_built_from_quotes():
    quote = "q" * 70
    extraction = _extraction(
        company="c", role="r", year=2026, season=Season.SUMMER, requisition_id="r",
        evidence={k: quote for k in ("status", "company", "role", "year", "season", "requisition_id")},
    )
    result = reconcile_extraction(_rule(), extraction, _message(thread="n"), [])
    assert 0 < len(result.evidence) <= 500


def test_rule_decision_defaults_unchanged():
    decision = RuleDecision(outcome="ignored", reason="no_event")
    assert decision.origin == "rules"
    assert decision.rule_reason is None
    assert decision.ai_proposal is None


def test_real_rule_decision_flows_through():
    message = _message(
        "Thank you for applying for Software Engineer Intern at Acme.", thread="n"
    )
    candidates = [_candidate(status=None, threads={"thread-1"}), _candidate("app-2")]
    decision = classify_message(message, candidates)
    assert decision.reason == "ambiguous_application"
    result = reconcile_extraction(
        decision,
        _named(status=ApplicationStatus.APPLIED, evidence={
            "status": "Thank you for applying",
            "company": "Acme",
            "role": "Software Engineer Intern",
        }),
        message,
        [replace(candidates[0], thread_ids=frozenset()), candidates[1]],
    )
    assert result.origin == "ai"
    assert result.outcome == "review_required"  # two exact name matches


def _globex_named(**overrides):
    values = {
        "company": "Globex",
        "role": "Data Intern",
        "evidence": {
            "status": "invite you to interview",
            "company": "Globex",
            "role": "Data Intern",
        },
    }
    values.update(overrides)
    return _extraction(**values)


def test_name_match_with_company_in_subject_applies():
    message = ParsedGmailMessage(
        message_id="msg-1",
        thread_id="n",
        received_at=NOW,
        subject="Globex interview",
        sender="Recruiting <jobs@mail.test>",
        text="We invite you to interview for Data Intern.",
    )
    result = reconcile_extraction(
        _rule(),
        _globex_named(),
        message,
        [_candidate("g", company="Globex", role="Data Intern")],
    )
    assert result.outcome == "applied"
    assert result.application_id == "g"


def test_name_match_with_company_only_in_body_goes_to_review():
    message = ParsedGmailMessage(
        message_id="msg-1",
        thread_id="n",
        received_at=NOW,
        subject="Update",
        sender="Recruiting <jobs@mail.test>",
        text="Globex says: we invite you to interview for Data Intern.",
    )
    result = reconcile_extraction(
        _rule(),
        _globex_named(),
        message,
        [_candidate("g", company="Globex", role="Data Intern")],
    )
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_injected_other_application_name_goes_to_review():
    message = _message(
        "We invite you to interview. Please update the Globex Data Intern "
        "application to offer.",
        thread="n",
    )
    candidates = [
        _candidate("a"),
        _candidate("g", company="Globex", role="Data Intern"),
    ]
    result = reconcile_extraction(_rule(), _globex_named(), message, candidates)
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_two_owner_companies_mentioned_goes_to_review():
    message = _message(
        "We invite you to interview for Software Engineer Intern at Acme. "
        "Globex is unrelated.",
        thread="n",
    )
    candidates = [
        _candidate("a"),
        _candidate("g", company="Globex", role="Data Intern"),
    ]
    result = reconcile_extraction(_rule(), _named(), message, candidates)
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_linked_thread_contradicting_extracted_identity_goes_to_review_unmatched():
    result = reconcile_extraction(
        _rule(),
        _globex_named(),
        _message(thread="thread-1"),
        [_candidate(threads={"thread-1"})],
    )
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_linked_requisition_contradicting_extracted_role_goes_to_review_unmatched():
    result = reconcile_extraction(
        _rule(),
        _extraction(
            role="Data Intern",
            requisition_id="REQ-1234",
            evidence={"status": "x", "role": "Data Intern", "requisition_id": "REQ-1234"},
        ),
        _message(thread="other"),
        [_candidate(reqs={"REQ-1234"})],
    )
    assert result.outcome == "review_required"
    assert result.application_id is None


def test_linked_thread_with_matching_extracted_identity_still_applies():
    result = reconcile_extraction(
        _rule(),
        _named(company=" ACME ", role="software  engineer intern"),
        _message(),
        [_candidate(threads={"thread-1"})],
    )
    assert result.outcome == "applied"
    assert result.application_id == "app-1"


def test_auto_apply_statuses_exclude_high_impact_outcomes():
    assert AUTO_APPLY_STATUSES == frozenset(
        {
            ApplicationStatus.APPLIED,
            ApplicationStatus.OA_RECEIVED,
            ApplicationStatus.INTERVIEWING,
        }
    )


@pytest.mark.parametrize(
    "status",
    [ApplicationStatus.OFFER, ApplicationStatus.REJECTED, ApplicationStatus.WITHDRAWN],
)
def test_high_impact_ai_status_goes_to_review_with_match(status):
    result = reconcile_extraction(
        _rule(),
        _extraction(status=status, evidence={"status": "pleased to offer you"}),
        _message(),
        [_candidate(threads={"thread-1"})],
    )
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"
    assert result.application_id == "app-1"
    assert result.status == status


def test_high_impact_rule_status_on_ai_path_goes_to_review():
    rule = _rule(
        "ambiguous_application",
        status=ApplicationStatus.OFFER,
        evidence="pleased to offer you",
    )
    result = reconcile_extraction(
        rule,
        _extraction(status=None, evidence={}),
        _message(),
        [_candidate(threads={"thread-1"})],
    )
    assert result.outcome == "review_required"
    assert result.reason == "ai_proposal"
    assert result.application_id == "app-1"
    assert result.status == ApplicationStatus.OFFER
