import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app import observability, sync_handler
from app.main import app
from app.observability import LOG_EVENTS, category_of, log_event
from app.services import email_processing
from app.services.ai_reconcile import ESCALATION_REASONS
from app.services.email_rules import RuleDecision

APP_DIR = Path(__file__).resolve().parents[1] / "app"
CANARIES = (
    "CANARY-EMAIL-BODY we are pleased to offer you",
    "sk-" + "CANARYKEY0123456789abcdefghijklmnop",
    "1/" + "/CANARY-REFRESH-TOKEN-0123456789abcdefghij",
)


def emitted(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records]


def assert_no_canary(caplog, capfd=None, extra="") -> None:
    haystack = caplog.text + extra
    if capfd is not None:
        out, err = capfd.readouterr()
        haystack += out + err
    for record in caplog.records:
        assert record.exc_info is None
        assert record.exc_text is None
    for canary in CANARIES:
        assert canary not in haystack


# --- observability helper -------------------------------------------------


def test_allowed_fields_render_on_one_line(caplog) -> None:
    logger = observability.get_logger("test")
    with caplog.at_level(logging.DEBUG):
        log_event(
            logger, "warning", "sync_page_completed",
            run_id="abc123", mode="import", processed=4, duration_ms=12,
        )

    assert emitted(caplog) == [
        "event=sync_page_completed run_id=abc123 mode=import processed=4 duration_ms=12"
    ]
    assert caplog.records[0].levelno == logging.WARNING


@pytest.mark.parametrize("value", [
    "has space", "line\nbreak", "a=b", "me@example.test", "x" * 65, "", 1.5,
    None, True, ["a"], {"a": 1}, b"bytes",
])
def test_unsafe_values_are_dropped(caplog, value) -> None:
    logger = observability.get_logger("test")
    with caplog.at_level(logging.DEBUG):
        log_event(logger, "info", "dispatch_summary", mode="daily", category=value)

    assert emitted(caplog) == ["event=dispatch_summary mode=daily"]


def test_boundary_length_value_is_kept(caplog) -> None:
    logger = observability.get_logger("test")
    with caplog.at_level(logging.DEBUG):
        log_event(logger, "info", "dispatch_summary", category="x" * 64)

    assert emitted(caplog) == ["event=dispatch_summary category=" + "x" * 64]


def test_unknown_field_names_are_dropped(caplog) -> None:
    logger = observability.get_logger("test")
    with caplog.at_level(logging.DEBUG):
        log_event(
            logger, "info", "dispatch_summary", owner_id="owner-a", email="a",
            body="text", count=2,
        )

    assert emitted(caplog) == ["event=dispatch_summary count=2"]


@pytest.mark.parametrize("event", ["Bad Event", "bad-event", "", "x" * 41, "event=1", "a\nb"])
def test_bad_event_name_is_rejected(event) -> None:
    with pytest.raises(ValueError):
        log_event(observability.get_logger("test"), "info", event)


def test_category_of_is_class_name_only() -> None:
    assert category_of(RuntimeError(CANARIES[0])) == "RuntimeError"


# --- static guard ----------------------------------------------------------

FORBIDDEN_LEVELS = {
    "debug", "info", "warning", "warn", "error", "critical", "exception", "log", "fatal",
}


def app_sources():
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "observability.py":
            continue
        yield path, ast.parse(path.read_text(), filename=str(path))


def test_all_logging_goes_through_log_event() -> None:
    problems = []
    for path, tree in app_sources():
        name = path.relative_to(APP_DIR)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "traceback" for alias in node.names
            ):
                problems.append(f"{name}:{node.lineno} traceback import")
            if isinstance(node, ast.ImportFrom) and node.module in {"traceback", "warnings"}:
                problems.append(f"{name}:{node.lineno} {node.module} import")
            if isinstance(node, ast.Name) and node.id == "traceback":
                problems.append(f"{name}:{node.lineno} traceback")
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "print":
                problems.append(f"{name}:{node.lineno} print")
            if any(keyword.arg == "exc_info" for keyword in node.keywords):
                problems.append(f"{name}:{node.lineno} exc_info")
            if isinstance(func, ast.Attribute):
                owner = func.value.id if isinstance(func.value, ast.Name) else ""
                if func.attr == "getLogger":
                    problems.append(f"{name}:{node.lineno} getLogger")
                if func.attr == "exception":
                    problems.append(f"{name}:{node.lineno} .exception")
                if owner == "warnings" and func.attr == "warn":
                    problems.append(f"{name}:{node.lineno} warnings.warn")
                if owner in {"logger", "logging", "log"} and func.attr in FORBIDDEN_LEVELS:
                    problems.append(f"{name}:{node.lineno} {owner}.{func.attr}")
            if isinstance(func, ast.Name) and func.id == "log_event":
                values = [*node.args, *(keyword.value for keyword in node.keywords)]
                if any(isinstance(value, ast.JoinedStr) for value in values):
                    problems.append(f"{name}:{node.lineno} f-string in log_event")
                event = node.args[2] if len(node.args) > 2 else next(
                    (k.value for k in node.keywords if k.arg == "event"), None
                )
                if not (
                    isinstance(event, ast.Constant) and event.value in LOG_EVENTS
                ):
                    problems.append(f"{name}:{node.lineno} event not in LOG_EVENTS")
    assert problems == []


def test_every_allowlisted_event_is_emitted_somewhere() -> None:
    used = set()
    for _path, tree in app_sources():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "log_event" and len(node.args) > 2
                and isinstance(node.args[2], ast.Constant)
            ):
                used.add(node.args[2].value)
    assert used == set(LOG_EVENTS)


# --- canaries --------------------------------------------------------------


def delivery(count="1"):
    return {"Records": [{
        "messageId": "sqs-1",
        "body": json.dumps({"owner_id": "owner-a", "run_id": "run-1", "generation": 1}),
        "attributes": {"ApproximateReceiveCount": count},
    }]}


class FakeSyncs:
    def fail(self, *args):
        pass


@pytest.fixture
def worker(monkeypatch):
    for key in ("APPLICATIONS_TABLE", "EVENTS_TABLE", "USERS_TABLE", "SYNC_QUEUE_URL"):
        monkeypatch.setenv(key, key)
    monkeypatch.setattr(sync_handler, "_dependencies", lambda: {
        "client": object(), "syncs": FakeSyncs(),
    })
    return monkeypatch


def test_worker_failure_log_carries_class_name_only(worker, caplog, capfd) -> None:
    def fail(**kwargs):
        raise RuntimeError(" | ".join(CANARIES))

    worker.setattr(sync_handler, "process_sync_page", fail)
    with caplog.at_level(logging.DEBUG):
        sync_handler.handler(delivery("5"), None)

    messages = emitted(caplog)
    assert "event=sync_worker_failure category=RuntimeError attempt=5" in messages
    assert "event=sync_run_failed run_id=run-1 category=partial" in messages
    assert_no_canary(caplog, capfd)


def test_worker_page_completion_is_logged_without_content(worker, caplog) -> None:
    run = SimpleNamespace(mode="import", processed_count=7)
    worker.setattr(sync_handler, "process_sync_page", lambda **kwargs: run)
    with caplog.at_level(logging.DEBUG):
        assert sync_handler.handler(delivery(), None) == {"batchItemFailures": []}

    [line] = [m for m in emitted(caplog) if m.startswith("event=sync_page_completed")]
    assert "run_id=run-1" in line and "mode=import" in line
    assert "processed=7" in line and "duration_ms=" in line
    assert "owner" not in line


def test_escalator_exception_message_never_logged(monkeypatch, caplog, capfd) -> None:
    decision = RuleDecision("review_required", min(ESCALATION_REASONS))
    monkeypatch.setattr(
        email_processing, "parse_gmail_message",
        lambda message: SimpleNamespace(message_id="m1"),
    )
    monkeypatch.setattr(email_processing, "classify_message", lambda m, a: decision)

    class Repository:
        def get_terminal_outcome(self, owner_id, message_id):
            return None

        def record(self, **kwargs):
            return kwargs["decision"]

    class Exploding:
        def escalate(self, message, decision, applications):
            raise RuntimeError(" | ".join(CANARIES))

    with caplog.at_level(logging.DEBUG):
        result = email_processing.process_message(
            "owner-a", {}, [], Repository(), escalator=Exploding()
        )

    assert result.ai_note == "ai_unavailable"
    assert emitted(caplog) == ["event=ai_escalation_failed category=RuntimeError"]
    assert_no_canary(caplog, capfd)


@pytest.fixture
def exploding_route():
    router = APIRouter()

    @router.get("/__canary")
    def canary():
        raise RuntimeError(" | ".join(CANARIES))

    before = list(app.router.routes)
    app.include_router(router)
    yield
    app.router.routes[:] = before


def test_unhandled_api_error_returns_generic_500(exploding_route, caplog, capfd) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/__canary")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal error"}
    app_records = [r.getMessage() for r in caplog.records if r.name.startswith("app.")]
    assert app_records == ["event=api_unhandled_error category=RuntimeError"]
    assert_no_canary(caplog, capfd, response.text)
