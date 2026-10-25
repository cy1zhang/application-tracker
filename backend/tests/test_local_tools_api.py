import io
import json
import urllib.error

import pytest

from local_tools import api
from local_tools.config import AuthConfig

CONFIG = AuthConfig("http://api.invalid", "http://auth.invalid", "client-1")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(status, body):
    return urllib.error.HTTPError(
        "http://api.invalid", status, "x", {}, io.BytesIO(json.dumps(body).encode())
    )


def _install(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(json.dumps(outcome).encode())

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    return calls


def _token(config, force=False):
    return "fresh" if force else "stale"


def test_get_sends_bearer_and_params(monkeypatch):
    calls = _install(monkeypatch, [{"items": []}])
    result = api.TrackerApi(CONFIG, token=_token).get("/messages", {"limit": 1})
    assert result == {"items": []}
    assert calls[0].full_url == "http://api.invalid/messages?limit=1"
    assert calls[0].get_header("Authorization") == "Bearer stale"


def test_api_error_detail_is_sanitized(monkeypatch):
    _install(monkeypatch, [_http_error(404, {"detail": "Some <html> text"})])
    with pytest.raises(api.ApiError) as caught:
        api.TrackerApi(CONFIG, token=_token).get("/x")
    assert (caught.value.status, caught.value.detail) == (404, "other")


def test_api_error_keeps_safe_detail(monkeypatch):
    _install(monkeypatch, [_http_error(409, {"detail": "stale version"})])
    with pytest.raises(api.ApiError) as caught:
        api.TrackerApi(CONFIG, token=_token).post("/x", {"a": 1})
    assert caught.value.detail == "stale version"


def test_api_retries_once_on_401(monkeypatch):
    calls = _install(monkeypatch, [_http_error(401, {}), {"ok": True}])
    assert api.TrackerApi(CONFIG, token=_token).get("/x") == {"ok": True}
    assert calls[1].get_header("Authorization") == "Bearer fresh"


def test_api_gives_up_after_second_401(monkeypatch):
    calls = _install(monkeypatch, [_http_error(401, {}), _http_error(401, {})])
    with pytest.raises(api.ApiError) as caught:
        api.TrackerApi(CONFIG, token=_token).get("/x")
    assert caught.value.status == 401
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("x"), TimeoutError(), ConnectionResetError()],
)
def test_transport_failure_becomes_network_api_error(monkeypatch, error):
    _install(monkeypatch, [error])
    with pytest.raises(api.ApiError) as caught:
        api.TrackerApi(CONFIG, token=_token).get("/x")
    assert (caught.value.status, caught.value.detail) == (0, "network")


@pytest.mark.parametrize(
    "failure", [urllib.error.URLError("down"), TimeoutError(), ConnectionResetError()]
)
def test_token_refresh_transport_failure_is_a_network_error(failure):
    def token(config, force=False):
        raise failure

    with pytest.raises(api.ApiError) as caught:
        api.TrackerApi(CONFIG, token=token).get("/x")
    assert (caught.value.status, caught.value.detail) == (0, "network")


def test_forced_refresh_transport_failure_after_401_is_a_network_error(monkeypatch):
    _install(monkeypatch, [_http_error(401, {"detail": "expired"})])

    def token(config, force=False):
        if force:
            raise urllib.error.URLError("down")
        return "stale"

    with pytest.raises(api.ApiError) as caught:
        api.TrackerApi(CONFIG, token=token).get("/x")
    assert (caught.value.status, caught.value.detail) == (0, "network")
