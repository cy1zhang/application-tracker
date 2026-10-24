import base64
import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import keyring
import keyring.backend
import keyring.errors
import pytest

from local_tools import auth
from local_tools.config import AuthConfig


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def get_password(self, service, username):
        return self.store.get((service, username))

    def delete_password(self, service, username):
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("missing")
        del self.store[(service, username)]


class FakeTokenServer:
    def __init__(self, status=200, payload=None):
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                form = urllib.parse.parse_qs(self.rfile.read(length).decode())
                outer.requests.append({k: v[0] for k, v in form.items()})
                body = json.dumps(outer.payload).encode()
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.status = status
        self.payload = payload or {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
            "expires_in": 3600,
        }
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _free_port() -> int:
    server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = server.server_port
    server.server_close()
    return port


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    auth._cache.clear()
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def token_server():
    server = FakeTokenServer()
    yield server
    server.close()


def _config(token_server, port=8765) -> AuthConfig:
    return AuthConfig(
        api_base_url="http://api.invalid",
        cognito_domain=token_server.url,
        client_id="client-1",
        redirect_uri=f"http://localhost:{port}/callback",
    )


def _browser_returning(state_override=None, code="the-code"):
    def open_browser(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        callback = urllib.parse.urlencode(
            {"code": code, "state": state_override or query["state"][0]}
        )
        redirect = query["redirect_uri"][0]
        urllib.request.urlopen(f"{redirect}?{callback}", timeout=10).read()

    return open_browser


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = auth.pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert "=" not in challenge


def test_login_rejects_mismatched_state(memory_keyring, token_server):
    config = _config(token_server, _free_port())
    with pytest.raises(ValueError):
        auth.login(config, open_browser=_browser_returning("wrong-state"))
    assert memory_keyring.store == {}
    assert token_server.requests == []


def test_login_stores_refresh_token(memory_keyring, token_server):
    config = _config(token_server, _free_port())
    auth.login(config, open_browser=_browser_returning())
    assert memory_keyring.store == {("application-tracker", "client-1"): "refresh-1"}
    exchange = token_server.requests[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code"] == "the-code"
    assert exchange["code_verifier"]


def test_access_token_refreshes_and_caches(memory_keyring, token_server):
    memory_keyring.set_password("application-tracker", "client-1", "refresh-1")
    config = _config(token_server)
    assert auth.access_token(config) == "access-1"
    assert auth.access_token(config) == "access-1"
    assert len(token_server.requests) == 1
    assert token_server.requests[0]["grant_type"] == "refresh_token"


def test_access_token_raises_login_required_on_invalid_grant(
    memory_keyring, token_server
):
    memory_keyring.set_password("application-tracker", "client-1", "refresh-1")
    token_server.status = 400
    token_server.payload = {"error": "invalid_grant"}
    with pytest.raises(auth.LoginRequired) as caught:
        auth.access_token(_config(token_server))
    assert "refresh-1" not in str(caught.value)


def test_access_token_raises_login_required_without_stored_token(
    memory_keyring, token_server
):
    with pytest.raises(auth.LoginRequired):
        auth.access_token(_config(token_server))


def _browser_sending(params, strays=()):
    def open_browser(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        redirect = query["redirect_uri"][0]
        base = redirect.rsplit("/callback", 1)[0]
        for path in strays:
            try:
                urllib.request.urlopen(base + path, timeout=10).read()
            except urllib.error.HTTPError:
                pass
        urllib.request.urlopen(
            f"{redirect}?{urllib.parse.urlencode(params)}", timeout=10
        ).read()

    return open_browser


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"error": "access_denied"}, "access_denied"),
        ({"error": "Bad Value <script>"}, "unknown"),
        ({"state": "s"}, "unknown"),
    ],
)
def test_login_reports_cancelled_callback_without_leaking(
    memory_keyring, token_server, params, expected
):
    config = _config(token_server, _free_port())
    with pytest.raises(ValueError) as caught:
        auth.login(config, open_browser=_browser_sending(params))
    assert str(caught.value) == f"sign-in was cancelled or failed: {expected}"
    assert token_server.requests == []


def test_login_ignores_stray_requests_before_callback(memory_keyring, token_server):
    config = _config(token_server, _free_port())

    def open_browser(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        _browser_sending(
            {"code": "the-code", "state": query["state"][0]}, strays=["/favicon.ico"]
        )(url)

    auth.login(config, open_browser=open_browser)
    assert memory_keyring.store == {("application-tracker", "client-1"): "refresh-1"}


def test_login_times_out_without_callback(memory_keyring, token_server, monkeypatch):
    monkeypatch.setattr(auth, "LOGIN_TIMEOUT_SECONDS", 0.5)
    config = _config(token_server, _free_port())
    with pytest.raises(ValueError, match="cancelled or failed: unknown"):
        auth.login(config, open_browser=lambda url: None)
