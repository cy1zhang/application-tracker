from datetime import UTC, datetime, timedelta

from app.domain.ai import ApplicationKind
from app.domain.applications import ApplicationStatus, RoleType, Season
from app.services.ai_extraction import ValidatedExtraction
from app.services.plan_report import PlannedItem, simulate

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def item(message_id, minutes, *, company="Acme", status=ApplicationStatus.APPLIED,
         kind=ApplicationKind.APPLICATION_UPDATE, requisition_id=None):
    return PlannedItem(
        message_id=message_id,
        received_at=T0 + timedelta(minutes=minutes),
        extraction=ValidatedExtraction(
            company=company,
            role="Software Engineering Intern",
            employment_type=RoleType.INTERNSHIP,
            season=Season.SUMMER,
            year=2027,
            status=status,
            requisition_id=requisition_id,
            evidence={},
            status_in_subject_only=False,
            kind=kind,
        ),
        subject="s",
        sender="a@example.com",
        thread_id=None,
    )


def test_simulate_second_email_updates_planned_row():
    report = simulate(
        [item("m1", 0), item("m2", 5, status=ApplicationStatus.INTERVIEWING)], []
    )
    assert report.counts["create"] == 1
    assert report.counts["update"] == 1
    assert [line.target for line in report.sample] == ["planned:m1", "planned:m1"]


def test_simulate_orders_by_received_at_not_input_order():
    report = simulate(
        [item("late", 9, status=ApplicationStatus.INTERVIEWING), item("early", 0)],
        [],
    )
    assert [line.message_id for line in report.sample] == ["early", "late"]
    assert [line.action for line in report.sample] == ["create", "update"]


def test_simulate_counts_by_action_and_reason():
    report = simulate(
        [
            item("m1", 0),
            item("m2", 1, kind=ApplicationKind.JOB_ALERT),
            item("m3", 2, company="Other", status=None),
            item("m4", 3),
        ],
        [],
    )
    assert report.counts == {
        "create": 1,
        "update": 0,
        "ignore": 3,
        "needs_look": 0,
    }
    assert report.reasons == {
        "ai_create": 1,
        "not_application:job_alert": 1,
        "no_status_no_match": 1,
        "no_change": 1,
    }


def test_sample_holds_first_100_creates_and_updates():
    items = [item(f"m{i}", i, company=f"Co{i}") for i in range(120)]
    report = simulate(items, [])
    assert report.counts["create"] == 120
    assert len(report.sample) == 100
    assert report.sample[0].message_id == "m0"


def test_simulate_conflicting_cycle_on_same_thread_creates_separate_row():
    first = item("m1", 0)
    second = item("m2", 5)
    second = PlannedItem(
        message_id="m2",
        received_at=second.received_at,
        extraction=second.extraction.__class__(
            **{**second.extraction.__dict__, "year": 2028}
        ),
        subject="s",
        sender="a@example.com",
        thread_id="t",
    )
    first = PlannedItem(**{**first.__dict__, "thread_id": "t"})
    report = simulate([first, second], [])
    assert report.counts["create"] == 2
