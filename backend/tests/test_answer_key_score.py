import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import ClassVar

import boto3
import pytest
from moto import mock_aws

from app.domain.applications import ApplicationPatch
from app.domain.messages import MessageContent
from evaluation.answer_key.labels import Label
from evaluation.answer_key.ollama import OllamaProvider
from evaluation.answer_key.score import (
    CurrentPipeline,
    EmailOutcome,
    exit_code,
    score,
)
from tests.ai_support import (
    AMBIGUOUS_TEXT,
    StubProvider,
    extraction,
    interview_extraction,
)

BASE = {"company": None, "role": None, "role_type": None, "season": None,
        "year": None, "status": None, "requisition_id": None}


def label(id, kind="application_update", key=None, **extra):
    return Label(id=id, kind=kind, application_key=key, **{**BASE, **extra})


def out(id, action, row=None, **fields):
    values = {"status": None, "company": None, "role": None, "season": None, "year": None}
    return EmailOutcome(id=id, action=action, row=row, **{**values, **fields})


def test_precision_counts_only_fully_correct_changes():
    labels = {
        "E1": label("E1", key="a", company="Acme", status="applied"),
        "E2": label("E2", key="b", company="Beta", status="rejected"),
        "E3": label("E3", kind="job_alert"),
    }
    outcomes = [
        out("E1", "created", "r1", company="Acme", status="applied"),
        out("E2", "created", "r2", company="Beta", status="interviewing"),
        out("E3", "updated", "r1", status="applied"),
    ]
    report = score(labels, outcomes)
    assert report.precision == pytest.approx(1 / 3)
    assert report.wrong_status == 1


def test_fake_row_counted_when_first_email_not_application():
    labels = {"E1": label("E1", kind="newsletter_or_event"), "E2": label("E2", key="a")}
    outcomes = [out("E1", "created", "r1"), out("E2", "updated", "r1")]
    assert score(labels, outcomes).fake_rows == 1


def test_duplicate_and_merged_rows():
    labels = {
        "E1": label("E1", key="a"), "E2": label("E2", key="a"),
        "E3": label("E3", key="a"), "E4": label("E4", key="b"),
        "E5": label("E5", key="c"),
    }
    outcomes = [
        out("E1", "created", "r1"), out("E2", "created", "r2"),
        out("E3", "created", "r3"), out("E4", "updated", "r3"),
        out("E5", "created", "r5"),
    ]
    report = score(labels, outcomes)
    assert report.duplicate_rows == 2
    assert report.merged_rows == 1


def test_recall_includes_needs_look():
    labels = {f"E{i}": label(f"E{i}", key=f"k{i}") for i in range(4)}
    outcomes = [
        out("E0", "created", "r0"), out("E1", "needs_look"),
        out("E2", "ignored"), out("E3", "ignored"),
    ]
    assert score(labels, outcomes).recall == 0.5


def test_kind_confusion_counts_actions_by_kind():
    labels = {"E1": label("E1", kind="job_alert"), "E2": label("E2", kind="job_alert")}
    outcomes = [out("E1", "ignored"), out("E2", "needs_look")]
    assert score(labels, outcomes).kind_confusion == {
        "job_alert": {"ignored": 1, "needs_look": 1}
    }


def test_check_thresholds_exit_code():
    def report(p, r):
        return SimpleNamespace(precision=p, recall=r)

    assert exit_code(report(0.98, 0.90)) == 0
    assert exit_code(report(0.979, 0.95)) == 1
    assert exit_code(report(1.0, 0.899)) == 1


class _Fake(BaseHTTPRequestHandler):
    bodies: ClassVar[list] = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).bodies.append(json.loads(body))
        reply = json.dumps({"message": {"content": json.dumps({"company": "Acme"})}})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(reply.encode())

    def log_message(self, *args):
        pass


def test_ollama_provider_uses_cache_on_second_call(tmp_path):
    _Fake.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = OllamaProvider(
            cache_dir=tmp_path, base_url=f"http://127.0.0.1:{server.server_port}"
        )
        prompt = SimpleNamespace(system="sys", user="usr")
        first = provider.extract("key", "gemma4:latest", prompt)
        second = provider.extract("key", "gemma4:latest", prompt)
        again = OllamaProvider(
            cache_dir=tmp_path, base_url="http://127.0.0.1:1"
        ).extract("key", "gemma4:latest", prompt)
    finally:
        server.shutdown()
        server.server_close()
    assert first == second == again == {"company": "Acme"}
    assert len(_Fake.bodies) == 1
    sent = _Fake.bodies[0]
    assert sent["stream"] is False and sent["think"] is False
    assert sent["options"] == {"temperature": 0, "num_predict": 512}
    assert sent["messages"] == [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}
    ]
    assert sent["format"]["type"] == "object"


def content(message_id, text, minutes=0):
    return MessageContent(
        message_id=message_id, thread_id=f"t-{message_id}",
        received_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes),
        subject="Application update", sender="Recruiting <jobs@acme.test>", text=text,
    )


def test_current_pipeline_creates_no_rows_from_empty_start():
    thanks = "Thank you for applying to Software Engineer Intern at Acme."
    provider = StubProvider(interview_extraction())
    outcomes = CurrentPipeline(provider).run(
        [("E1", content("m1", thanks)), ("E2", content("m2", AMBIGUOUS_TEXT, 1))]
    )
    assert [o.id for o in outcomes] == ["E1", "E2"]
    assert [o.action for o in outcomes] == ["needs_look", "needs_look"]
    assert all(o.row is None for o in outcomes)


def test_current_pipeline_does_not_hit_daily_ai_limit():
    provider = StubProvider(extraction())
    items = [(f"E{i}", content(f"m{i}", AMBIGUOUS_TEXT, i)) for i in range(60)]
    CurrentPipeline(provider).run(items)
    assert len(provider.calls) == 60


def test_cli_skips_and_counts_unavailable_messages(tmp_path, capsys):
    from evaluation.answer_key.score import run_cli
    from local_tools.api import ApiError

    (tmp_path / "index.json").write_text(json.dumps({"E1": "m1", "E2": "m2"}))
    (tmp_path / "labels").mkdir()
    rows = [dict(id=i, kind="other", **BASE) for i in ("E1", "E2")]
    (tmp_path / "labels" / "batch-1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows)
    )

    class Api:
        def get(self, path, params=None):
            if "m2" in path:
                raise ApiError(503, "message_unavailable")
            return content("m1", "Thank you for applying.").model_dump(mode="json")

    class Pipeline:
        def run(self, items):
            return [out(i, "ignored") for i, _ in items]

    report = run_cli(Api(), tmp_path, Pipeline())
    assert (report.emails, report.unavailable) == (1, 1)
    assert capsys.readouterr().out == ""


def test_precision_unmeasured_without_automatic_changes():
    labels = {"E1": label("E1", key="a")}
    assert score(labels, [out("E1", "needs_look")]).precision is None


def test_recall_unmeasured_without_application_labels():
    labels = {"E1": label("E1", kind="job_alert")}
    assert score(labels, [out("E1", "ignored")]).recall is None


def test_check_fails_when_unmeasured():
    assert exit_code(SimpleNamespace(precision=None, recall=1.0)) == 1
    assert exit_code(SimpleNamespace(precision=1.0, recall=None)) == 1


@mock_aws
def test_applied_outcome_maps_to_updated_with_changed_fields():
    from app.domain.applications import ApplicationCreate, ApplicationStatus
    from app.repositories.dynamodb import DynamoApplicationRepository
    from app.repositories.dynamodb_email import StoredOutcome
    from app.repositories.dynamodb_tables import create_tables
    from evaluation.answer_key import score as module

    client = boto3.client("dynamodb", region_name="us-east-1")
    create_tables(
        client, applications_table="Applications", events_table="Events",
        users_table="Users", oauth_states_table="OAuthStates",
        sync_runs_table="SyncRuns",
    )
    repo = DynamoApplicationRepository(
        client=client, applications_table="Applications", events_table="Events"
    )
    now = datetime(2026, 10, 1, tzinfo=UTC)
    created = repo.create(
        owner_id=module.OWNER,
        payload=ApplicationCreate(company="Acme", role="Intern", status="applied"),
        application_id="app-1", event_id="e1", occurred_at=now,
    )
    before = {"app-1": created}
    repo.update(
        owner_id=module.OWNER, application_id="app-1",
        patch=ApplicationPatch(
            expected_version=created.version, status=ApplicationStatus.INTERVIEWING
        ),
        event_id="e2", occurred_at=now + timedelta(days=1),
    )
    stored = StoredOutcome("m1", "applied", "rule", "app-1")
    outcome = module._outcome("E1", stored, before, repo)
    assert outcome == EmailOutcome(
        "E1", "updated", "app-1", status="interviewing", company=None,
        role=None, season=None, year=None,
    )
    labels = {"E1": label("E1", key="a", status="rejected")}
    report = score(labels, [outcome])
    assert (report.precision, report.wrong_status) == (0.0, 1)
