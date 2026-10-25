"""Replay the labeled sample through a pipeline and score it against the labels.

Prints only report numbers, never company names, roles, or ids.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

import boto3
from moto import mock_aws
from pydantic import ValidationError

from app.domain.ai import AiProvider
from app.domain.messages import MessageContent
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.repositories.dynamodb_tables import create_tables
from app.services.ai_escalation import AiEscalator
from app.services.ai_extraction import (
    InvalidExtraction,
    build_prompt,
    validate_extraction,
)
from app.services.ai_providers import ProviderError
from app.services.auto_apply import AutoDecision, MessageRef, decide
from app.services.email_filters import filter_reason
from app.services.email_processing import process_message
from app.services.email_rules import ApplicationCandidate, classify_message
from app.services.gmail_message import MessageParseError, parse_gmail_message
from app.services.plan_report import simulate_items
from evaluation.answer_key.labels import Label, load_labels
from evaluation.answer_key.ollama import OllamaProvider
from evaluation.run import gmail_message
from local_tools.api import ApiError, TrackerApi
from local_tools.config import load_config

MIN_PRECISION = 0.98
MIN_RECALL = 0.90
MIN_AUTOMATIC_CHANGES = 30
MIN_APPLICATION_EMAILS = 30
FIELDS = ("status", "company", "role", "season", "year")
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
TOKEN = {"ciphertext": "c", "nonce": "n", "encrypted_data_key": "k"}
OWNER = "answer-key-owner"

Action = Literal["created", "updated", "ignored", "needs_look"]


@dataclass(frozen=True)
class EmailOutcome:
    id: str
    action: Action
    row: str | None
    status: str | None
    company: str | None
    role: str | None
    season: str | None
    year: int | None
    ai_unavailable: bool = False


class PipelineRun(Protocol):
    def run(self, items: list[tuple[str, MessageContent]]) -> list[EmailOutcome]: ...


@dataclass
class AnswerKeyReport:
    precision: float | None  # None means unmeasured
    recall: float | None
    fake_rows: int
    duplicate_rows: int
    merged_rows: int
    wrong_status: int
    kind_confusion: dict[str, dict[str, int]]
    automatic_changes: int = 0
    emails: int = 0
    unavailable: int = 0
    application_emails: int = 0
    labeled: int = 0
    indexed: int = 0
    ai_unavailable: int = 0
    pipeline: str = ""
    model: str = ""


def apply_floors(report: AnswerKeyReport) -> AnswerKeyReport:
    # Below the floor a gate is a single-item coin flip, so report it unmeasured.
    if report.automatic_changes < MIN_AUTOMATIC_CHANGES:
        report.precision = None
    if report.application_emails < MIN_APPLICATION_EMAILS:
        report.recall = None
    return report


def _value(item, name):
    value = getattr(item, name)
    return getattr(value, "value", value)


def _correct(label: Label, outcome: EmailOutcome, row_keys: dict) -> bool:
    if label.kind != "application_update" or label.application_key is None:
        return False
    if row_keys[outcome.row] != {label.application_key}:
        return False
    return all(
        getattr(outcome, name) in (None, _value(label, name)) for name in FIELDS
    )


def score(labels: dict[str, Label], outcomes: list[EmailOutcome]) -> AnswerKeyReport:
    outcomes = [o for o in outcomes if o.id in labels]
    row_keys: dict[str, set] = defaultdict(set)
    first_label: dict[str, Label] = {}
    key_rows: dict[str, set] = defaultdict(set)
    for outcome in outcomes:
        if outcome.row is None:
            continue
        label = labels[outcome.id]
        first_label.setdefault(outcome.row, label)
        if label.kind == "application_update" and label.application_key:
            row_keys[outcome.row].add(label.application_key)
            key_rows[label.application_key].add(outcome.row)

    automatic = [o for o in outcomes if o.action in ("created", "updated")]
    correct = sum(_correct(labels[o.id], o, row_keys) for o in automatic)
    updates = [o for o in outcomes if labels[o.id].kind == "application_update"]
    caught = sum(
        o.action == "needs_look" or (o in automatic and _correct(labels[o.id], o, row_keys))
        for o in updates
    )
    confusion: dict[str, Counter] = defaultdict(Counter)
    for outcome in outcomes:
        confusion[labels[outcome.id].kind][outcome.action] += 1
    return AnswerKeyReport(
        precision=correct / len(automatic) if automatic else None,
        recall=caught / len(updates) if updates else None,
        fake_rows=sum(1 for l in first_label.values() if l.kind != "application_update"),
        duplicate_rows=sum(len(rows) - 1 for rows in key_rows.values()),
        merged_rows=sum(1 for keys in row_keys.values() if len(keys) >= 2),
        wrong_status=sum(
            1 for o in automatic
            if labels[o.id].kind == "application_update"
            and o.status is not None and o.status != _value(labels[o.id], "status")
        ),
        kind_confusion={k: dict(v) for k, v in confusion.items()},
        automatic_changes=len(automatic), emails=len(outcomes),
        application_emails=len(updates),
        ai_unavailable=sum(o.ai_unavailable for o in outcomes),
    )


def exit_code(report) -> int:
    if report.labeled < report.indexed:
        return 1
    if report.precision is None or report.recall is None:
        return 1
    return 0 if report.precision >= MIN_PRECISION and report.recall >= MIN_RECALL else 1


class _Cipher:
    def decrypt(self, owner_id, credential_type, encrypted):
        return "local-evaluation-key"


class CurrentPipeline:
    """Today's process_message on moto, starting from zero applications."""

    def __init__(self, provider, model: str = "gemma4:latest") -> None:
        self._provider = provider
        self._model = model

    def run(self, items):
        with mock_aws():
            return self._run(items)

    def _run(self, items):
        client = boto3.client("dynamodb", region_name="us-east-1")
        create_tables(
            client, applications_table="Applications", events_table="Events",
            users_table="Users", oauth_states_table="OAuthStates",
            sync_runs_table="SyncRuns",
        )
        applications = DynamoApplicationRepository(
            client=client, applications_table="Applications", events_table="Events"
        )
        settings = DynamoAiSettingsRepository(client=client, users_table="Users")
        settings.save(OWNER, AiProvider.OPENAI, TOKEN, "1234", True, NOW)
        DynamoGmailRepository(
            client=client, users_table="Users", oauth_states_table="OAuthStates"
        ).save_connection(OWNER, 0, "local@example.test", TOKEN, NOW)
        syncs = DynamoSyncRepository(
            client=client, users_table="Users", sync_runs_table="SyncRuns"
        )
        run = syncs.start(OWNER, "run", NOW)
        assert syncs.claim(OWNER, run.run_id, run.generation, "lease", NOW)
        emails = DynamoEmailRepository(
            client=client, applications_table="Applications", events_table="Events",
            users_table="Users", guard_generation=run.generation,
            guard_run_id=run.run_id,
        )
        position = [0]
        escalator = AiEscalator(
            owner_id=OWNER, generation=run.generation, run_id=run.run_id,
            settings=settings, cipher=_Cipher(),
            provider_factory=lambda name: self._provider,
            models={AiProvider.OPENAI: self._model},
            # Daily cap deliberately bypassed (one day per message): offline evaluation.
            clock=lambda: NOW + timedelta(days=position[0]),
        )
        outcomes = []
        for index, (label_id, content) in enumerate(items):
            position[0] = index
            candidates = [
                ApplicationCandidate(application=a, thread_ids=frozenset())
                for a in applications.list(owner_id=OWNER, limit=100, cursor=None).items
            ]
            before = {c.application.application_id: c.application for c in candidates}
            stored = process_message(
                OWNER, _payload(content), candidates, emails, escalator=escalator
            )
            outcome = _outcome(label_id, stored, before, applications)
            note = client.get_item(
                TableName="Events",
                Key={"owner_id": {"S": OWNER}, "event_id": {"S": f"gmail#{content.message_id}"}},
            ).get("Item", {}).get("ai_note", {}).get("S")
            outcomes.append(replace(outcome, ai_unavailable=note == "ai_unavailable"))
        return outcomes


@dataclass(frozen=True)
class _Step:
    label_id: str
    message_id: str
    thread_id: str
    received_at: datetime
    content: MessageContent


class AutoPipeline:
    """Filters, then rules, then the local model, then ``decide``, from zero rows.

    One in-memory candidate list is shared by every step. ``simulate_items``
    owns the loop and its working copy; the rules check and the prompt both see
    that list as earlier items change it, so a rules update can target a row an
    earlier model call created.
    """

    def __init__(self, provider, model: str = "gemma4:latest") -> None:
        self._provider = provider
        self._model = model

    def run(self, items):
        steps = [
            _Step(label_id, c.message_id, c.thread_id, c.received_at, c)
            for label_id, c in items
        ]
        unavailable: set[str] = set()

        def decide_step(step: _Step, working: list[ApplicationCandidate]) -> AutoDecision:
            try:
                parsed = parse_gmail_message(_payload(step.content))
            except MessageParseError:
                return AutoDecision("needs_look", "unparseable", None, {})
            reason = filter_reason(parsed)
            if reason is not None:
                return AutoDecision("ignore", f"filtered:{reason}", None, {})
            rules = classify_message(parsed, working)
            if rules.outcome == "applied" and rules.application_id:
                fields = {
                    name: value for name, value in (
                        ("status", rules.status), ("season", rules.season),
                        ("year", rules.year),
                    ) if value is not None
                }
                return AutoDecision(
                    "update", "rules_update", rules.application_id, fields,
                    rules.requisition_id,
                )
            prompt = build_prompt(parsed, working)
            try:
                extraction = validate_extraction(
                    self._provider.extract("local-evaluation-key", self._model, prompt),
                    prompt,
                )
            except ProviderError:
                unavailable.add(step.label_id)
                return AutoDecision("needs_look", "ai_unavailable", None, {})
            except InvalidExtraction:
                return AutoDecision("needs_look", "invalid_extraction", None, {})
            ref = MessageRef(parsed.message_id, parsed.thread_id, parsed.received_at)
            return decide(extraction, message_ref=ref, candidates=working)

        outcomes = []
        for step, decision, target in simulate_items(steps, [], decide_step):
            empty = {"status": None, "company": None, "role": None, "season": None,
                     "year": None}
            if decision.action == "create":
                outcome = EmailOutcome(
                    step.label_id, "created", target,
                    **{**empty, **{n: _value_of(decision.fields.get(n)) for n in FIELDS}},
                )
            elif decision.action == "update":
                outcome = EmailOutcome(
                    step.label_id, "updated", target,
                    **{**empty, **{n: _value_of(decision.fields[n])
                                   for n in FIELDS if n in decision.fields}},
                )
            else:
                action = "needs_look" if decision.action == "needs_look" else "ignored"
                outcome = EmailOutcome(step.label_id, action, None, **empty)
            outcomes.append(replace(outcome, ai_unavailable=step.label_id in unavailable))
        return outcomes


def _value_of(value):
    return getattr(value, "value", value)


def _payload(content: MessageContent) -> dict:
    return gmail_message(content.message_id, {"message": {
        "received_at": content.received_at.isoformat(),
        "thread_id": content.thread_id,
        "subject": content.subject,
        "sender": content.sender,
        "body": content.text,
    }})


def _outcome(label_id, stored, before, applications) -> EmailOutcome:
    empty = {"status": None, "company": None, "role": None, "season": None, "year": None}
    if stored.outcome == "review_required":
        return EmailOutcome(label_id, "needs_look", None, **empty)
    if stored.outcome == "applied" and stored.application_id:
        after = applications.get(owner_id=OWNER, application_id=stored.application_id)
        old = before.get(stored.application_id)
        changed = {
            name: _value(after, name) for name in FIELDS
            if old is None or _value(after, name) != _value(old, name)
        }
        return EmailOutcome(
            label_id, "updated", stored.application_id, **{**empty, **changed}
        )
    return EmailOutcome(label_id, "ignored", None, **empty)


def load_items(api, directory: Path, labels, index):
    items, unavailable = [], 0
    for label_id in sorted(labels):
        try:
            data = api.get(f"/messages/{index[label_id]}/content")
        except ApiError as error:
            if error.status == 503 and error.detail == "message_unavailable":
                unavailable += 1
                continue
            raise
        try:
            content = MessageContent(**data)
        except ValidationError:
            # Never echo input values: they are real email text.
            raise ValueError(f"malformed content for {label_id}") from None
        items.append((label_id, content))
    items.sort(key=lambda item: item[1].received_at)
    return items, unavailable


def run_cli(api, directory: Path, pipeline: PipelineRun) -> AnswerKeyReport:
    labels = load_labels(directory)
    index = json.loads((Path(directory) / "index.json").read_text(encoding="utf-8"))
    items, unavailable = load_items(api, directory, labels, index)
    report = score(labels, pipeline.run(items))
    report.unavailable = unavailable
    report.labeled, report.indexed = len(labels), len(index)
    return apply_floors(report)


def _print(report: AnswerKeyReport) -> None:
    print("sample-weighted (stratified sample), replayed from empty tracker")
    print(f"pipeline: {report.pipeline}, model: {report.model}")
    print(f"emails={report.emails} unavailable={report.unavailable} "
          f"automatic_changes={report.automatic_changes} "
          f"labeled={report.labeled}/{report.indexed} "
          f"ai_unavailable={report.ai_unavailable}")
    def show(value):
        return "unmeasured" if value is None else f"{value:.3f}"

    print(f"precision={show(report.precision)} recall={show(report.recall)}")
    print(f"fake_rows={report.fake_rows} duplicate_rows={report.duplicate_rows} "
          f"merged_rows={report.merged_rows} wrong_status={report.wrong_status}")
    for kind, actions in sorted(report.kind_confusion.items()):
        print(f"{kind}: " + " ".join(f"{a}={n}" for a, n in sorted(actions.items())))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.answer_key.score")
    parser.add_argument("--dir", required=True, type=Path)
    parser.add_argument("--pipeline", required=True, choices=["current", "auto"])
    parser.add_argument("--config", default="../.env.local-tools")
    parser.add_argument("--model", default="gemma4:latest")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv[1:])
    api = TrackerApi(load_config(args.config))
    provider = OllamaProvider(args.model, cache_dir=args.dir / "cache")
    pipeline_class = AutoPipeline if args.pipeline == "auto" else CurrentPipeline
    report = run_cli(api, args.dir, pipeline_class(provider, args.model))
    report.pipeline, report.model = args.pipeline, args.model
    _print(report)
    return exit_code(report) if args.check else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
