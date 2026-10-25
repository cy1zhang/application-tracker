import pytest

from local_tools.api import ApiError
from local_tools.auth import LoginRequired
from local_tools.ollama import OllamaFailed, OllamaUnreachable
from local_tools.worker import run_once

SENTINEL = "SENTINEL-TEXT-9f3a"


class FakeApi:
    def __init__(self, ids, fail_input=None, fail_result=None, deferred=(), executed=()):
        self.queue = list(ids)
        self.fail_input = fail_input or {}
        self.fail_result = fail_result or {}
        self.deferred = set(deferred)
        self.executed = set(executed)
        self.queue_gets = 0
        self.posted = []

    def get(self, path, params=None):
        if path == "/ai-queue":
            self.queue_gets += 1
            limit = params["limit"]
            items = [{"message_id": i, "received_at": "t"} for i in self.queue[:limit]]
            return {"items": items, "remaining": max(0, len(self.queue) - limit)}
        mid = path.split("/")[2]
        if mid in self.fail_input:
            raise self._error(self.fail_input[mid])
        return {
            "system_prompt": f"sys {SENTINEL}", "user_prompt": f"user {SENTINEL}",
            "format": {"type": "object"}, "generation": 7,
        }

    def post(self, path, body):
        mid = path.split("/")[2]
        if mid in self.fail_result:
            raise self._error(self.fail_result[mid])
        self.posted.append((mid, body))
        self.queue.remove(mid)
        return {
            "planned": True, "executed": mid in self.executed, "action": "x",
            **({"deferred": True} if mid in self.deferred else {}),
        }

    @staticmethod
    def _error(spec):
        if isinstance(spec, Exception):
            return spec
        return ApiError(*spec)


class FakeOllama:
    model = "m1"

    def __init__(self, reachable=True, errors=None):
        self._reachable = reachable
        self.errors = errors or {}
        self.calls = 0

    def reachable(self, retries=3, delay=30):
        return self._reachable

    def chat(self, system, user, format):
        self.calls += 1
        error = self.errors.get(self.calls)
        if error:
            raise error
        return {"company": SENTINEL}


def test_empty_queue_makes_one_request_and_no_ollama_call():
    api, ollama = FakeApi([]), FakeOllama()
    summary = run_once(api, ollama, log=lambda line: None)
    assert api.queue_gets == 1 and ollama.calls == 0
    assert (summary.saved, summary.failed, summary.stopped) == (0, 0, None)


def test_processes_queue_in_order_and_counts():
    ids = [f"m{i}" for i in range(30)]
    api = FakeApi(ids, deferred={"m1"}, executed={"m0", "m2"})
    summary = run_once(api, FakeOllama(), log=lambda line: None)
    assert [mid for mid, _ in api.posted] == ids
    assert api.posted[0][1] == {
        "generation": 7, "model": "m1", "extraction": {"company": SENTINEL}
    }
    assert (summary.saved, summary.executed, summary.failed, summary.skipped) == (
        30, 2, 0, 0
    )
    assert summary.stopped is None


def test_limit_caps_items():
    api = FakeApi([f"m{i}" for i in range(10)])
    summary = run_once(api, FakeOllama(), limit=3, log=lambda line: None)
    assert summary.saved == 3 and len(api.queue) == 7


@pytest.mark.parametrize(
    "where,spec,field",
    [
        ("input", (404, "not found"), "skipped"),
        ("result", (404, "not found"), "skipped"),
        ("result", (422, "invalid output"), "failed"),
        ("result", (409, "review_changed"), "skipped"),
        ("input", (503, "message_unavailable"), "skipped"),
        ("result", (500, "other"), "failed"),
        ("result", (504, "other"), "failed"),
    ],
)
def test_per_item_failures_continue(where, spec, field):
    kwargs = {f"fail_{where}": {"m0": spec}}
    api = FakeApi(["m0", "m1"], **kwargs)
    summary = run_once(api, FakeOllama(), log=lambda line: None)
    assert getattr(summary, field) == 1
    assert summary.saved == 1 and summary.stopped is None
    assert api.queue_gets == 2  # failed m0 is not retried; loop ends


@pytest.mark.parametrize(
    "error", [OllamaFailed("timeout"), OllamaFailed("http"), OllamaFailed("invalid_json")]
)
def test_ollama_item_failures_continue(error):
    api = FakeApi(["m0", "m1"])
    summary = run_once(api, FakeOllama(errors={1: error}), log=lambda line: None)
    assert (summary.failed, summary.saved, summary.stopped) == (1, 1, None)


@pytest.mark.parametrize(
    "where,spec,stopped",
    [
        ("input", (401, "other"), "http_401"),
        ("result", (403, "other"), "http_403"),
        ("result", (409, "gmail_connection_changed"), "gmail_connection_changed"),
        ("result", (503, "gmail_connection_unavailable"),
         "gmail_connection_unavailable"),
        ("result", (503, "queue_unavailable"), "queue_unavailable"),
        ("input", (429, "other"), "http_429"),
        ("result", (502, "other"), "http_502"),
        ("input", LoginRequired(), "login_required"),
    ],
)
def test_global_stops(where, spec, stopped):
    api = FakeApi(["m0", "m1"], **{f"fail_{where}": {"m0": spec}})
    summary = run_once(api, FakeOllama(), log=lambda line: None)
    assert summary.stopped == stopped and summary.saved == 0
    assert summary.failed == 0 and summary.skipped == 0
    assert api.queue == ["m0", "m1"] or api.queue == ["m0", "m1"][0:2]


def test_queue_listing_failure_stops():
    class Broken(FakeApi):
        def get(self, path, params=None):
            raise ApiError(503, "queue_unavailable")

    summary = run_once(Broken([]), FakeOllama(), log=lambda line: None)
    assert summary.stopped == "queue_unavailable"


def test_ollama_unreachable_stops():
    api = FakeApi(["m0"])
    assert run_once(api, FakeOllama(reachable=False), log=print).stopped == (
        "ollama_unreachable"
    )
    ollama = FakeOllama(errors={1: OllamaUnreachable()})
    ollama.reachable = lambda retries=3, delay=30: ollama.calls == 0
    summary = run_once(FakeApi(["m0"]), ollama, log=lambda line: None)
    assert summary.stopped == "ollama_unreachable"


def test_auto_apply_off_exits_quietly():
    lines = []
    api = FakeApi(["m0", "m1"], fail_result={"m0": (409, "auto_apply_off")})
    summary = run_once(api, FakeOllama(), log=lines.append)
    assert summary.stopped == "auto_apply_off"
    assert summary.failed == 0 and summary.skipped == 0
    assert not any("error" in line or "stop" in line for line in lines)


def test_all_items_failed_stops_without_spinning():
    api = FakeApi(["m0", "m1"], fail_result={"m0": (422, "x"), "m1": (422, "x")})
    summary = run_once(api, FakeOllama(), log=lambda line: None)
    assert summary.failed == 2 and summary.stopped is None
    assert api.queue_gets == 2


def test_log_lines_contain_no_payload_text():
    lines = []
    api = FakeApi(["m0", "m1", "m2"], fail_result={"m1": (422, "invalid output")})
    run_once(api, FakeOllama(), log=lines.append)
    text = "\n".join(lines)
    assert lines
    for forbidden in (SENTINEL, "m0", "m1", "m2", "sys", "user"):
        assert forbidden not in text
