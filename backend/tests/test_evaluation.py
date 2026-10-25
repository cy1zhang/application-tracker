import json
import re
import socket
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

import pytest

from app.domain.applications import ApplicationStatus
from app.services import email_rules
from evaluation import run as runner
from evaluation.metrics import (
    Rate,
    ScenarioResult,
    automatic_recall,
    coverage,
    detection_recall,
    precision,
    wilson_interval,
)

FIXTURES = Path(runner.__file__).parent / "fixtures"
THRESHOLDS = Path(runner.__file__).parent / "thresholds.json"

RULES_CATEGORIES = {
    "clear_linked", "wording_variant", "unlinked_identity", "multi_role_company",
    "multi_cycle_company", "requisition_link", "job_ad", "newsletter",
    "recruiter_marketing", "hypothetical", "multi_role_listing", "forwarded",
    "ambiguous_identity", "cycle_explicit_september", "cycle_unspecified",
    "chronological_conflict", "manual_correction_replay", "terminal_reopen",
    "unparseable", "prompt_injection", "quoted_reply",
}
AI_CATEGORIES = {
    "ai_resolves", "ai_long_quote", "ai_unsupported_evidence",
    "ai_fabricated_cycle", "ai_injection", "ai_wrong_application",
    "ai_provider_error", "ai_invalid_output", "ai_proposal_only",
    "ai_not_needed", "ai_guarded",
}
OUTCOMES = {"applied", "ignored", "review_required"}
PROVIDERS = {"openai", "anthropic", "gemini"}


def result(expected, actual, correct=False, origin="rules", scenario_id="s"):
    return ScenarioResult(scenario_id, expected, actual, correct, origin)


# --- metrics ---------------------------------------------------------------


def test_wilson_interval_known_values() -> None:
    low, high = wilson_interval(90, 100)
    assert low == pytest.approx(0.8256, abs=1e-3)
    assert high == pytest.approx(0.9448, abs=1e-3)
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == pytest.approx(1.0)
    assert wilson_interval(0, 0) is None


def test_precision_counts_every_error_type() -> None:
    results = [
        result("applied", "applied", True),
        result("applied", "applied", False),  # wrong application or wrong field
        result("review_required", "applied", False),  # update where review expected
        result("ignored", "applied", False),  # update where ignore expected
        result("applied", "review_required"),  # not an automatic update
        result("applied", "ignored"),  # not an automatic update
    ]
    rate = precision(results)
    assert (rate.successes, rate.total) == (1, 4)
    assert rate.value == 0.25


def test_empty_automatic_updates_are_unmeasured_not_perfect() -> None:
    rate = precision([result("applied", "review_required")])
    assert rate.total == 0 and rate.value is None
    assert rate.display() == "unmeasured"
    assert "100" not in rate.display()
    assert precision([]).display() == "unmeasured"
    assert automatic_recall([result("ignored", "ignored")]).display() == "unmeasured"


def test_coverage_is_share_handled_without_review() -> None:
    results = [
        result("applied", "applied", True),
        result("ignored", "ignored"),
        result("review_required", "review_required"),
        result("applied", "review_required"),
    ]
    rate = coverage(results)
    assert (rate.successes, rate.total) == (2, 4)


def test_detection_recall_edge_cases() -> None:
    results = [
        result("applied", "applied", True),  # hit
        result("applied", "review_required"),  # hit: detected for review
        result("review_required", "review_required"),  # hit
        result("review_required", "ignored"),  # miss: relevant mail ignored
        result("applied", "ignored"),  # miss
        result("review_required", "applied", False),  # miss: wrong update
        result("applied", "applied", False),  # miss: wrong application or cycle
        result("ignored", "ignored"),  # not relevant, not counted
        result("ignored", "applied", False),  # not relevant, not counted
    ]
    rate = detection_recall(results)
    assert (rate.successes, rate.total) == (3, 7)
    auto = automatic_recall(results)
    assert (auto.successes, auto.total) == (1, 4)


def test_rate_display_includes_counts_and_interval() -> None:
    text = Rate(98, 100).display()
    assert text.startswith("98.0% (98/100, 95% CI ")
    assert Rate(0, 5).display() != "unmeasured"


# --- fixtures --------------------------------------------------------------


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_fixtures_meet_minimum_counts_and_schema() -> None:
    rules, ai = load("rules.json"), load("ai.json")
    thresholds = json.loads(THRESHOLDS.read_text())
    assert len(rules) >= max(70, thresholds["minimum_rules_scenarios"])
    assert len(ai) >= max(20, thresholds["minimum_ai_scenarios"])
    ids = [item["id"] for item in rules + ai]
    assert len(ids) == len(set(ids))
    for item in rules + ai:
        assert item["expected"]["outcome"] in OUTCOMES, item["id"]
        assert set(item) >= {"id", "category", "applications", "message", "expected"}
        if item["expected"]["outcome"] == "applied":
            refs = {app["ref"] for app in item["applications"]}
            assert item["expected"]["application"] in refs, item["id"]
            assert item["expected"]["status"] in {s.value for s in ApplicationStatus}
            assert {"season", "year"} <= set(item["expected"]), item["id"]
    for item in ai:
        assert set(item["ai_outputs"]) <= PROVIDERS, item["id"]


def test_every_required_category_is_present() -> None:
    assert RULES_CATEGORIES <= {item["category"] for item in load("rules.json")}
    assert AI_CATEGORIES <= {item["category"] for item in load("ai.json")}


def test_fixtures_use_only_synthetic_domains() -> None:
    text = (FIXTURES / "rules.json").read_text() + (FIXTURES / "ai.json").read_text()
    hosts = re.findall(r"(?:https?://|@)([A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
    assert hosts
    assert all(host.endswith(".test") for host in hosts), sorted(set(hosts))
    assert "gmail.com" not in text.lower()


# --- runner on the repository fixtures -------------------------------------


@pytest.fixture(scope="module")
def baseline():
    fixtures = runner.load_fixtures()
    return fixtures, runner.evaluate(fixtures)


def test_runner_passes_committed_thresholds(baseline) -> None:
    _, evaluation = baseline
    thresholds = runner.load_thresholds()
    assert runner.check(evaluation, thresholds) == []
    assert evaluation.network_attempts == 0


def test_report_labels_synthetic_results_and_unmeasured_scope(baseline) -> None:
    fixtures, evaluation = baseline
    text = runner.render_markdown(evaluation, commit="abc123")
    assert fixtures.sha256 in text and "abc123" in text
    assert "recorded synthetic outputs; not real provider behavior" in text
    assert "do not establish real-inbox accuracy" in text
    assert "candidate-search recall is not modeled" in text
    payload = evaluation.to_dict(commit="abc123")
    assert payload["fixtures_sha256"] == fixtures.sha256
    assert payload["commit"] == "abc123"


def test_report_flags_a_provider_with_no_recorded_outputs(baseline) -> None:
    fixtures, _ = baseline
    stripped = replace(fixtures, ai=[
        {**item, "ai_outputs": {
            name: out for name, out in item["ai_outputs"].items() if name != "gemini"
        }}
        for item in fixtures.ai
    ])
    evaluation = runner.evaluate(stripped)
    text = runner.render_markdown(evaluation, commit="abc123")
    gemini_row = next(line for line in text.splitlines() if line.startswith("| gemini"))
    assert "unmeasured" in gemini_row
    assert not any("gemini" in breach for breach in
                   runner.check(evaluation, runner.load_thresholds()))


# --- degraded classifiers must trip --check --------------------------------


def test_disabled_rejection_rule_trips_check(baseline, monkeypatch) -> None:
    fixtures, _ = baseline
    original = email_rules.classify_message

    def without_rejections(message, applications):
        decision = original(message, applications)
        if decision.status is ApplicationStatus.REJECTED:
            return replace(decision, outcome="ignored", reason="no_event")
        return decision

    monkeypatch.setattr(
        "app.services.email_processing.classify_message", without_rejections
    )
    evaluation = runner.evaluate(fixtures, providers=())
    breaches = runner.check(evaluation, runner.load_thresholds())
    assert breaches and any("recall" in breach for breach in breaches)


def test_wrong_status_updates_trip_check(baseline, monkeypatch) -> None:
    fixtures, _ = baseline
    original = email_rules.classify_message

    def always_offer(message, applications):
        decision = original(message, applications)
        if decision.outcome == "applied":
            return replace(decision, status=ApplicationStatus.OFFER)
        return decision

    monkeypatch.setattr("app.services.email_processing.classify_message", always_offer)
    evaluation = runner.evaluate(fixtures, providers=())
    breaches = runner.check(evaluation, runner.load_thresholds())
    assert any("precision" in breach for breach in breaches)


def test_cycle_only_update_on_a_status_change_scenario_is_incorrect() -> None:
    from types import SimpleNamespace

    def app(status, season=None, year=None):
        return SimpleNamespace(
            company="Acme", role="Intern", role_type=None, season=season,
            year=year, status=status, application_date=None,
        )

    expected = {
        "outcome": "applied", "application": "a", "status": "interviewing",
        "season": "summer", "year": 2026,
    }
    stored = SimpleNamespace(outcome="applied", application_id="id-a")
    before = {"a": app("applied")}
    ids = {"a": "id-a"}
    degraded = {"a": app("applied", "summer", 2026)}
    correct = {"a": app("interviewing", "summer", 2026)}
    check = runner._Harness._update_correct
    assert check(expected, stored, ids, before, degraded) is False
    assert check(expected, stored, ids, before, correct) is True


def test_provider_returning_unsupported_status_with_validation_off_trips_check(
    baseline, monkeypatch
) -> None:
    fixtures, _ = baseline
    monkeypatch.setattr("app.services.ai_extraction._supports", lambda *a: True)
    unsupported = [
        {**item, "ai_outputs": {
            "openai": {
                **runner.expand_output(
                    {"status": "interviewing", "company": "Acme", "role": "Software Engineer Intern"},
                    {"status": "Acme", "company": "Acme", "role": "Software Engineer Intern"},
                )
            }
        }}
        for item in fixtures.ai
    ]
    evaluation = runner.evaluate(replace(fixtures, ai=unsupported), providers=("openai",))
    breaches = runner.check(evaluation, runner.load_thresholds())
    assert any("openai" in breach and "precision" in breach for breach in breaches)


def test_minimum_scenario_counts_are_enforced(baseline) -> None:
    fixtures, _ = baseline
    thresholds = runner.load_thresholds()
    evaluation = runner.evaluate(
        replace(fixtures, rules=fixtures.rules[:5]), providers=()
    )
    breaches = runner.check(evaluation, thresholds)
    assert any("minimum" in breach and "rules" in breach for breach in breaches)


def test_check_exit_code_and_failing_scenarios_are_named(tmp_path, capsys) -> None:
    thresholds = tmp_path / "thresholds.json"
    strict = {**runner.load_thresholds(), "detection_recall_min": 1.01}
    thresholds.write_text(json.dumps(strict))
    code = runner.main(
        ["--check", "--thresholds", str(thresholds), "--report-dir", str(tmp_path)]
    )
    assert code == 1
    output = capsys.readouterr()
    assert "recall" in (output.out + output.err)
    assert (tmp_path / "report.md").exists() and (tmp_path / "report.json").exists()


# --- network block ---------------------------------------------------------


def test_network_block_raises_and_counts_attempts() -> None:
    with runner.NetworkBlock() as block:
        with pytest.raises(runner.NetworkBlocked):
            socket.create_connection(("api.example.test", 443))
        with pytest.raises(runner.NetworkBlocked):
            socket.socket().connect(("127.0.0.1", 9))
        with pytest.raises(runner.NetworkBlocked):
            socket.getaddrinfo("api.example.test", 443)
    assert block.attempts == 3
    # Restored afterwards.
    assert socket.create_connection.__module__ == "socket"


def test_a_provider_that_touches_the_network_fails_the_run(baseline) -> None:
    fixtures, _ = baseline

    class Leaky:
        def extract(self, api_key, model, prompt_input):
            urlopen("https://api.example.test/v1/extract")
            raise AssertionError("unreachable")

    evaluation = runner.evaluate(
        fixtures, providers=("openai",), provider_factory=lambda name, scenario: Leaky()
    )
    assert evaluation.network_attempts > 0
    breaches = runner.check(evaluation, runner.load_thresholds())
    assert any("network" in breach for breach in breaches)


# --- automatic pipeline fixtures --------------------------------------------

AUTO_CATEGORIES = {
    "job_alert_filtered", "digest_filtered", "outreach",
    "portal_rejection_no_confirmation", "swe_vs_software_engineering",
    "two_cycles_one_company", "later_rejection_after_interview",
    "reopening_a_rejection", "roleless_followup",
}
# Job-board sender domains the filters recognise; every other host is .test.
AUTO_REAL_HOSTS = {"linkedin.com", "indeed.com"}


def test_auto_fixtures_meet_minimum_and_cover_the_brief() -> None:
    auto = load("auto.json")
    thresholds = json.loads(THRESHOLDS.read_text())
    assert len(auto) >= max(30, thresholds["minimum_auto_scenarios"])
    assert AUTO_CATEGORIES <= {item["category"] for item in auto}
    ids = [item["id"] for item in auto]
    assert len(ids) == len(set(ids))
    for item in auto:
        bodies = [m["body"] for m in item["messages"]]
        assert len(bodies) == len(set(bodies)), item["id"]  # recorded outputs key on body
        for message in item["messages"]:
            assert message["expected"]["action"] in {
                "created", "updated", "ignored", "needs_look"}
            output = message.get("ai_output")
            if output and "error" not in output and "evidence" in output:
                assert "kind" in output, item["id"]  # a full recorded extraction


def test_auto_fixtures_use_only_synthetic_domains() -> None:
    text = (FIXTURES / "auto.json").read_text()
    hosts = set(re.findall(r"(?:https?://|@)([A-Za-z0-9.-]+\.[A-Za-z]{2,})", text))
    assert hosts
    assert all(h.endswith(".test") or h in AUTO_REAL_HOSTS for h in hosts), sorted(hosts)
    assert "gmail.com" not in text.lower()


def test_evaluation_gates_auto_fixtures(baseline) -> None:
    fixtures, evaluation = baseline
    assert evaluation.scenario_counts["auto"] == len(fixtures.auto) >= 30
    auto = evaluation.auto
    assert [r for r in auto.results if r.expected != r.actual] == []
    assert auto.report.precision >= 0.98 and auto.report.recall >= 0.90
    assert runner.check(evaluation, runner.load_thresholds()) == []
    assert evaluation.network_attempts == 0
    assert "auto" in evaluation.to_dict()


def test_auto_gate_trips_when_decisions_degrade(baseline, monkeypatch) -> None:
    fixtures, _ = baseline
    from app.services import auto_apply

    monkeypatch.setattr(
        "evaluation.answer_key.score.decide",
        lambda extraction, *, message_ref, candidates: auto_apply.AutoDecision(
            "create", "always", None,
            {"company": extraction.company, "status": extraction.status},
        ),
    )
    breaches = runner.check(runner.evaluate(fixtures), runner.load_thresholds())
    assert any("auto precision" in breach for breach in breaches)


def test_auto_scenario_count_minimum_is_enforced(baseline) -> None:
    fixtures, _ = baseline
    evaluation = runner.evaluate(replace(fixtures, auto=fixtures.auto[:5]))
    breaches = runner.check(evaluation, runner.load_thresholds())
    assert any("auto scenario count" in breach for breach in breaches)
