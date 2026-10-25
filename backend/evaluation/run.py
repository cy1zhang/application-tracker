"""Offline synthetic classification evaluation.

Feeds JSON fixtures through the real ``process_message`` pipeline on moto, with
recorded provider stubs, and reports precision, coverage, and recall. It makes no
network call (sockets are blocked) and its results use synthetic data only, so
they do not establish real-inbox accuracy.
"""

import argparse
import hashlib
import json
import logging
import os
import socket
import subprocess
import sys
from base64 import urlsafe_b64encode
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import boto3
from moto import mock_aws

from app.domain.ai import AiProvider
from app.domain.applications import ApplicationCreate, ApplicationPatch
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.ai_escalation import AiEscalator
from app.services.ai_providers import ProviderError
from app.services.email_processing import process_message
from app.services.email_rules import ApplicationCandidate
from evaluation.metrics import (
    ScenarioResult,
    automatic_recall,
    coverage,
    detection_recall,
    precision,
)

ROOT = Path(__file__).parent
PROVIDERS = ("openai", "anthropic", "gemini")
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
CREATED = datetime(2026, 9, 1, 9, tzinfo=UTC)
TOKEN = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}
EXTRACTION_FIELDS = (
    "company", "role", "employment_type", "season", "year", "status",
    "requisition_id",
)
SYNTHETIC_LABEL = "recorded synthetic outputs; not real provider behavior"


# --- network block ----------------------------------------------------------


class NetworkBlocked(Exception):
    """Raised for any socket use while the evaluation runs."""


class NetworkBlock:
    _TARGETS = (
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (socket, "gethostbyname"),
    )

    def __init__(self) -> None:
        self.attempts = 0
        self._saved: list[tuple[object, str, object]] = []

    def _blocked(self, *args, **kwargs):
        self.attempts += 1
        raise NetworkBlocked("network access is blocked during evaluation")

    def __enter__(self) -> Self:
        for owner, name in self._TARGETS:
            self._saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, self._blocked)
        return self

    def __exit__(self, *exc) -> None:
        for owner, name, original in reversed(self._saved):
            setattr(owner, name, original)
        self._saved.clear()


# --- fixtures ---------------------------------------------------------------


@dataclass(frozen=True)
class Fixtures:
    rules: list
    ai: list
    sha256: str
    auto: list = field(default_factory=list)


def load_fixtures(root: Path | None = None) -> Fixtures:
    folder = (root or ROOT) / "fixtures"
    raw = [
        (folder / name).read_bytes() for name in ("rules.json", "ai.json", "auto.json")
    ]
    digest = hashlib.sha256(b"".join(raw)).hexdigest()
    return Fixtures(json.loads(raw[0]), json.loads(raw[1]), digest, json.loads(raw[2]))


def load_thresholds(path: Path | None = None) -> dict:
    return json.loads((path or ROOT / "thresholds.json").read_text())


def expand_output(values: dict, quotes: dict) -> dict:
    """A full recorded extraction object (every key present)."""
    return {
        **{name: values.get(name) for name in EXTRACTION_FIELDS},
        "kind": values.get("kind", "application_update"),
        "evidence": {name: quotes.get(name) for name in EXTRACTION_FIELDS},
    }


class RecordedProvider:
    """Replays one recorded synthetic output; errors replay as ProviderError."""

    def __init__(self, output: dict) -> None:
        self._output = output

    def extract(self, api_key, model, prompt_input):
        if "error" in self._output:
            raise ProviderError(self._output["error"])
        return self._output


class _Cipher:
    def decrypt(self, owner_id, credential_type, encrypted):
        return "synthetic-evaluation-key"


class AutoFixtureProvider:
    """Replays the recorded output of the message whose body is in the prompt."""

    def __init__(self, messages: list[dict]) -> None:
        self._recorded = [(m["body"].strip(), m.get("ai_output")) for m in messages]

    def extract(self, api_key, model, prompt_input):
        for body, output in self._recorded:
            if output is not None and body in prompt_input.user:
                if "error" in output:
                    raise ProviderError(output["error"])
                return output
        raise ProviderError("no recorded output")


@dataclass(frozen=True)
class AutoResult:
    message_id: str
    expected: str
    actual: str


@dataclass
class AutoEvaluation:
    report: object
    results: list[AutoResult]


def evaluate_auto(scenarios: list[dict]) -> AutoEvaluation:
    """Replay each scenario from zero rows through the automatic pipeline.

    Scoring goes through the answer-key ``score`` so CI and the real-inbox
    check share one definition of precision and recall.
    """
    from app.domain.messages import MessageContent
    from evaluation.answer_key.labels import Label
    from evaluation.answer_key.score import AutoPipeline, apply_floors, score

    labels, outcomes, expected = {}, [], {}
    for scenario in scenarios:
        items = []
        for number, message in enumerate(scenario["messages"]):
            label_id = f"{scenario['id']}/{number}"
            want = message["expected"]
            key = f"{scenario['id']}:{want['key']}" if want["key"] else None
            labels[label_id] = Label(
                id=label_id, kind=want["kind"], company=want["company"],
                role=want["role"], role_type=None, season=want["season"],
                year=want["year"], status=want["status"], requisition_id=None,
                application_key=key,
            )
            expected[label_id] = want["action"]
            items.append((label_id, MessageContent(
                message_id=label_id, thread_id=message["thread_id"],
                received_at=_timestamp(message["received_at"]),
                subject=message["subject"], sender=message["sender"],
                text=message["body"],
            )))
        provider = AutoFixtureProvider(scenario["messages"])
        outcomes += AutoPipeline(provider, "synthetic-model").run(items)
    report = apply_floors(score(labels, outcomes))
    return AutoEvaluation(
        report, [AutoResult(o.id, expected[o.id], o.action) for o in outcomes]
    )


# --- scenario execution ------------------------------------------------------


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def gmail_message(message_id: str, scenario: dict) -> dict:
    item = scenario["message"]
    received_ms = int(_timestamp(item["received_at"]).timestamp() * 1000)
    message = {
        "id": message_id,
        "threadId": item["thread_id"],
        "internalDate": str(received_ms),
    }
    if item.get("malformed") == "no_payload":
        message["payload"] = {}
        return message
    html = item.get("html")
    content = html if html else item["body"]
    message["payload"] = {
        "mimeType": "text/html" if html else "text/plain",
        "headers": [
            {"name": "Subject", "value": item["subject"]},
            {"name": "From", "value": item["sender"]},
        ],
        "body": {"data": urlsafe_b64encode(content.encode()).decode().rstrip("=")},
    }
    return message


_CHECKED = ("company", "role", "role_type", "season", "year", "status", "application_date")


def _value(item, name):
    value = getattr(item, name)
    return getattr(value, "value", value)


class _Harness:
    def __init__(self) -> None:
        self.client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            self.client, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        self.applications = DynamoApplicationRepository(
            client=self.client, applications_table="Applications", events_table="Events"
        )
        self.ai = DynamoAiSettingsRepository(client=self.client, users_table="Users")
        self.gmail = DynamoGmailRepository(
            client=self.client, users_table="Users", oauth_states_table="OAuthStates"
        )
        self.syncs = DynamoSyncRepository(
            client=self.client, users_table="Users", sync_runs_table="SyncRuns"
        )
        self.counter = 0

    def _seed(self, owner: str, scenario: dict):
        ids, candidates = {}, []
        for item in scenario["applications"]:
            created = self.applications.create(
                owner_id=owner,
                payload=ApplicationCreate(
                    company=item["company"], role=item["role"], status=item["status"],
                    season=item["season"], year=item["year"],
                ),
                application_id=f"{owner}-{item['ref']}",
                event_id=f"create-{owner}-{item['ref']}",
                occurred_at=CREATED,
            )
            for number, correction in enumerate(item["corrections"]):
                fields = {k: v for k, v in correction.items() if k != "at"}
                created = self.applications.update(
                    owner_id=owner, application_id=created.application_id,
                    patch=ApplicationPatch(expected_version=created.version, **fields),
                    event_id=f"fix-{owner}-{item['ref']}-{number}",
                    occurred_at=_timestamp(correction["at"]),
                )
            ids[item["ref"]] = created.application_id
            candidates.append(ApplicationCandidate(
                application=created,
                thread_ids=frozenset(item["threads"]),
                requisition_ids=frozenset(item["requisitions"]),
            ))
        return ids, candidates

    def _snapshot(self, owner: str, ids: dict) -> dict:
        return {
            ref: self.applications.get(owner_id=owner, application_id=application_id)
            for ref, application_id in ids.items()
        }

    def run(self, scenario: dict, provider: str | None, process, provider_factory):
        self.counter += 1
        owner = f"eval-{self.counter}"
        ids, candidates = self._seed(owner, scenario)
        before = self._snapshot(owner, ids)
        escalator = None
        if provider is None:
            emails = DynamoEmailRepository(
                client=self.client, applications_table="Applications",
                events_table="Events",
            )
        else:
            self.ai.save(owner, AiProvider(provider), TOKEN, "1234", True, NOW)
            self.gmail.save_connection(owner, 0, "eval@example.test", TOKEN, NOW)
            run = self.syncs.start(owner, f"run-{owner}", NOW)
            lease = f"lease-{owner}"
            assert self.syncs.claim(owner, run.run_id, run.generation, lease, NOW)
            emails = DynamoEmailRepository(
                client=self.client, applications_table="Applications",
                events_table="Events", users_table="Users",
                guard_generation=run.generation, guard_run_id=run.run_id,
            )
            recorded = provider_factory(provider, scenario)
            escalator = AiEscalator(
                owner_id=owner, generation=run.generation, run_id=run.run_id,
                settings=self.ai, cipher=_Cipher(),
                provider_factory=lambda name: recorded,
                models={AiProvider(provider): "synthetic-model"}, clock=lambda: NOW,
            )
        message = gmail_message(f"eval-{scenario['id']}", scenario)
        try:
            stored = process(owner, message, candidates, emails, escalator=escalator)
        except Exception as error:  # noqa: BLE001 - recorded as a failed scenario
            return ScenarioResult(
                scenario["id"], scenario["expected"]["outcome"], "error", False,
                reason=type(error).__name__,
            )
        after = self._snapshot(owner, ids)
        return ScenarioResult(
            scenario["id"], scenario["expected"]["outcome"], stored.outcome,
            self._update_correct(scenario["expected"], stored, ids, before, after),
            origin="ai" if stored.reason == "ai_extraction" else "rules",
            reason=stored.reason,
        )

    @staticmethod
    def _update_correct(expected, stored, ids, before, after) -> bool:
        """Right application, the labeled status, and every changed field equals the label.

        A cycle the email states but the update leaves unset is incomplete, not
        wrong: the PRD keeps uncertain fields unknown.
        """
        if stored.outcome != "applied" or expected["outcome"] != "applied":
            return False
        if stored.application_id != ids[expected["application"]]:
            return False
        label = expected.get("status")
        if label is not None and _value(after[expected["application"]], "status") != label:
            return False
        for ref, item in after.items():
            changed = {n for n in _CHECKED if _value(item, n) != _value(before[ref], n)}
            if ref != expected["application"]:
                if changed:
                    return False
                continue
            for name in changed:
                label = expected.get(name)
                if name not in {"status", "season", "year"} or label is None:
                    return False
                if _value(item, name) != label:
                    return False
        return True


# --- evaluation -------------------------------------------------------------


@dataclass
class Evaluation:
    fixtures_sha256: str
    scenario_counts: dict
    rules: list[ScenarioResult]
    ai_baseline: list[ScenarioResult]
    providers: dict[str, list[ScenarioResult]]
    network_attempts: int = 0
    auto: AutoEvaluation | None = None
    provider_order: tuple = field(default=PROVIDERS)

    def to_dict(self, commit: str = "unknown") -> dict:
        def path(results):
            return {
                "scenarios": len(results),
                "precision": precision(results).to_dict(),
                "coverage": coverage(results).to_dict(),
                "detection_recall": detection_recall(results).to_dict(),
                "automatic_recall": automatic_recall(results).to_dict(),
                "disagreements": [
                    {"id": r.scenario_id, "expected": r.expected,
                     "actual": r.actual, "reason": r.reason}
                    for r in results if r.actual != r.expected
                ],
            }

        return {
            "commit": commit,
            "fixtures_sha256": self.fixtures_sha256,
            "scenario_counts": self.scenario_counts,
            "network_attempts": self.network_attempts,
            "note": "Synthetic results do not establish real-inbox accuracy.",
            "rules": path(self.rules),
            "ai_baseline_rules_only": path(self.ai_baseline),
            "providers": {name: path(self.providers.get(name, []))
                          for name in self.provider_order},
            "auto": {
                "scenarios": self.scenario_counts["auto"],
                "precision": getattr(self.auto.report, "precision", None),
                "detection_recall": getattr(self.auto.report, "recall", None),
                "automatic_changes": self.auto.report.automatic_changes,
                "application_emails": self.auto.report.application_emails,
                "disagreements": [
                    {"id": r.message_id, "expected": r.expected, "actual": r.actual}
                    for r in self.auto.results if r.expected != r.actual
                ],
            },
        }


def evaluate(
    fixtures: Fixtures,
    *,
    providers: tuple = PROVIDERS,
    provider_factory: Callable | None = None,
    process: Callable | None = None,
) -> Evaluation:
    process = process or process_message
    provider_factory = provider_factory or (
        lambda name, scenario: RecordedProvider(scenario["ai_outputs"][name])
    )
    with NetworkBlock() as block, mock_aws():
        harness = _Harness()
        rules = [harness.run(s, None, process, provider_factory) for s in fixtures.rules]
        baseline = [harness.run(s, None, process, provider_factory) for s in fixtures.ai]
        by_provider = {}
        for name in providers:
            by_provider[name] = [
                harness.run(s, name, process, provider_factory)
                for s in fixtures.ai
                if provider_factory is not None and name in s["ai_outputs"]
            ]
        auto = evaluate_auto(fixtures.auto)
    return Evaluation(
        fixtures_sha256=fixtures.sha256,
        scenario_counts={
            "rules": len(fixtures.rules), "ai": len(fixtures.ai),
            "auto": len(fixtures.auto),
        },
        rules=rules, ai_baseline=baseline, providers=by_provider,
        network_attempts=block.attempts, auto=auto, provider_order=PROVIDERS,
    )


# --- gates -------------------------------------------------------------------


def _ids(results, predicate) -> str:
    return ", ".join(r.scenario_id for r in results if predicate(r)) or "none"


def check(evaluation: Evaluation, thresholds: dict) -> list[str]:
    breaches = []
    counts = evaluation.scenario_counts
    for key, label in (("rules", "rules"), ("ai", "AI"), ("auto", "auto")):
        minimum = thresholds[f"minimum_{key}_scenarios"]
        if counts[key] < minimum:
            breaches.append(
                f"{label} scenario count {counts[key]} is below the minimum {minimum}"
            )
    if evaluation.network_attempts:
        breaches.append(
            f"network: {evaluation.network_attempts} attempt(s) were blocked; "
            "the evaluation must make none"
        )
    rules_precision = precision(evaluation.rules)
    if rules_precision.total < thresholds["rules_min_automatic_updates"]:
        breaches.append(
            f"rules automatic updates {rules_precision.total} are below the "
            f"minimum {thresholds['rules_min_automatic_updates']}"
        )
    elif rules_precision.value < thresholds["rules_precision_min"]:
        breaches.append(
            f"rules precision {rules_precision.value:.3f} is below "
            f"{thresholds['rules_precision_min']}; wrong updates: "
            + _ids(evaluation.rules, lambda r: r.actual == "applied" and not r.update_correct)
        )
    recall = detection_recall(evaluation.rules)
    if recall.value is None or recall.value < thresholds["detection_recall_min"]:
        shown = "unmeasured" if recall.value is None else f"{recall.value:.3f}"
        breaches.append(
            f"detection recall {shown} is below {thresholds['detection_recall_min']}; "
            "missed: " + _ids(
                evaluation.rules,
                lambda r: r.expected in {"applied", "review_required"}
                and not (r.actual == "review_required"
                         or (r.actual == "applied" and r.update_correct)),
            )
        )
    floors = thresholds["ai_precision_min"]
    for name, results in evaluation.providers.items():
        rate = precision(ai_origin(results))
        floor = floors.get(name) if isinstance(floors, dict) else floors
        if rate.total and floor is not None and rate.value < floor:
            breaches.append(
                f"AI provider {name} precision {rate.value:.3f} is below {floor}; "
                "wrong updates: "
                + _ids(ai_origin(results), lambda r: not r.update_correct)
            )
    breaches += _check_auto(evaluation.auto, thresholds)
    return breaches


def _check_auto(auto: AutoEvaluation, thresholds: dict) -> list[str]:
    report = auto.report
    missed = ", ".join(r.message_id for r in auto.results if r.expected != r.actual) or "none"
    breaches = []
    for name, value, floor in (
        ("precision", report.precision, thresholds["auto_precision_min"]),
        ("detection recall", report.recall, thresholds["auto_detection_recall_min"]),
    ):
        if value is None:
            breaches.append(f"auto {name} is unmeasured (below the sample-size floor)")
        elif value < floor:
            breaches.append(
                f"auto {name} {value:.3f} is below {floor}; disagreements: {missed}"
            )
    return breaches


# --- reporting ----------------------------------------------------------------


def ai_origin(results: list[ScenarioResult]) -> list[ScenarioResult]:
    return [r for r in results if r.origin == "ai"]


def _row(label: str, results: list[ScenarioResult], updates) -> str:
    return (
        f"| {label} | {len(results)} | {precision(updates).display()} | "
        f"{coverage(results).display()} | {detection_recall(results).display()} | "
        f"{automatic_recall(results).display()} |"
    )


_HEADER = (
    "| Path | Scenarios | Automatic-update precision | Coverage | "
    "Detection recall | Automatic-update recall |\n|---|---|---|---|---|---|"
)


def render_markdown(evaluation: Evaluation, commit: str = "unknown") -> str:
    lines = [
        "# Synthetic classification evaluation",
        "",
        f"Commit: `{commit}`  ",
        f"Fixture SHA-256: `{evaluation.fixtures_sha256}`  ",
        (
            f"Scenarios: {evaluation.scenario_counts['rules']} rules, "
            f"{evaluation.scenario_counts['ai']} AI, "
            f"{evaluation.scenario_counts['auto']} automatic  "
        ),
        f"Network attempts blocked: {evaluation.network_attempts}",
        "",
        (
            "Synthetic scenarios validate intended behavior only. These results do "
            "not establish real-inbox accuracy. Gmail candidate-search recall is not "
            "modeled and stays unmeasured until the authorized real-inbox sample "
            "exists."
        ),
        "",
        "## Rules only (rules fixtures)",
        "",
        _HEADER,
        _row("rules", evaluation.rules, evaluation.rules),
        "",
        f"## AI fixtures ({SYNTHETIC_LABEL})",
        "",
        "Precision for providers counts automatic updates made through the AI tier.",
        "",
        _HEADER,
        _row("rules baseline", evaluation.ai_baseline, evaluation.ai_baseline),
    ]
    for name in evaluation.provider_order:
        results = evaluation.providers.get(name, [])
        lines.append(
            _row(name, results, ai_origin(results)) if results
            else f"| {name} | 0 | unmeasured | unmeasured | unmeasured | unmeasured |"
        )
    auto = evaluation.auto
    lines += [
        "", f"## Automatic pipeline ({SYNTHETIC_LABEL})", "",
        "Filters, rules, recorded model output, then the plan simulation, from zero rows.",
        "",
        (
            f"precision={_show(auto.report.precision)} "
            f"recall={_show(auto.report.recall)} "
            f"automatic_changes={auto.report.automatic_changes} "
            f"application_emails={auto.report.application_emails}"
        ),
    ]
    lines += ["", "## Scenarios whose outcome differs from the label", ""]
    sections = [("rules", evaluation.rules), ("rules baseline on AI fixtures", evaluation.ai_baseline)]
    sections += [(n, evaluation.providers.get(n, [])) for n in evaluation.provider_order]
    for label, results in sections:
        misses = [r for r in results if r.actual != r.expected]
        if not misses:
            continue
        lines.append(f"**{label}**")
        lines += [
            f"- `{r.scenario_id}`: expected {r.expected}, got {r.actual} ({r.reason})"
            for r in misses
        ]
        lines.append("")
    return "\n".join(lines) + "\n"


def _show(value) -> str:
    return "unmeasured" if value is None else f"{value:.3f}"


def _commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("GITHUB_SHA", "unknown")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluation.run")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero when a committed threshold is breached")
    parser.add_argument("--thresholds", type=Path, default=ROOT / "thresholds.json")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "output")
    args = parser.parse_args(argv)

    fixtures = load_fixtures()
    logging.disable(logging.CRITICAL)  # sanitized provider-failure warnings
    try:
        evaluation = evaluate(fixtures)
    finally:
        logging.disable(logging.NOTSET)
    commit = _commit()
    markdown = render_markdown(evaluation, commit)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "report.md").write_text(markdown)
    (args.report_dir / "report.json").write_text(
        json.dumps(evaluation.to_dict(commit), indent=2) + "\n"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as handle:
            handle.write(markdown)
    print(markdown)
    if not args.check:
        return 0
    breaches = check(evaluation, load_thresholds(args.thresholds))
    for breach in breaches:
        print(f"EVALUATION GATE FAILED: {breach}", file=sys.stderr)
    return 1 if breaches else 0


if __name__ == "__main__":
    raise SystemExit(main())
