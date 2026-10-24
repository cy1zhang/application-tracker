"""Browser sign-in (OAuth code + PKCE) with the refresh token in the Keychain."""

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

import keyring

from local_tools.config import AuthConfig

KEYRING_SERVICE = "application-tracker"
LOGIN_TIMEOUT_SECONDS = 300
EXPIRY_MARGIN_SECONDS = 60

_cache: dict[str, tuple[str, float]] = {}


class LoginRequired(Exception):
    """No usable refresh token; run `python -m local_tools login`."""


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _token_request(config: AuthConfig, form: dict[str, str]) -> dict:
    request = urllib.request.Request(
        f"{config.cognito_domain}/oauth2/token",
        data=urllib.parse.urlencode(form).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        code = None
        try:
            code = json.loads(error.read()).get("error")
        except (ValueError, AttributeError):
            pass
        if code == "invalid_grant":
            raise LoginRequired("sign-in expired; run login again") from None
        raise RuntimeError(f"token endpoint returned HTTP {error.code}") from None


def login(config: AuthConfig, *, open_browser: Callable = webbrowser.open) -> None:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    redirect = urllib.parse.urlparse(config.redirect_uri)
    received: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.urlparse(self.path)
            if query.path != redirect.path:
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(query.query)
            received.update({k: v[0] for k, v in params.items()})
            done.set()
            body = b"Signed in. You can close this tab."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(
        (redirect.hostname or "localhost", redirect.port or 80), Handler
    )
    server.timeout = 0.2

    def serve() -> None:
        # Stray requests (favicon, probes) must not consume the one-shot callback.
        deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
        while not done.is_set() and time.monotonic() < deadline:
            server.handle_request()
    try:
        url = f"{config.cognito_domain}/oauth2/authorize?" + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": config.client_id,
                "redirect_uri": config.redirect_uri,
                "scope": "openid email",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        # Serve in the background so open_browser may block or call back itself.
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        open_browser(url)
        thread.join(LOGIN_TIMEOUT_SECONDS)
    finally:
        server.server_close()

    code = received.get("code")
    if "error" in received or not code:
        reason = received.get("error", "")
        if not re.fullmatch(r"[a-z_]{1,40}", reason):
            reason = "unknown"
        raise ValueError(f"sign-in was cancelled or failed: {reason}")
    if not secrets.compare_digest(received.get("state", ""), state):
        raise ValueError("sign-in callback state did not match")
    tokens = _token_request(
        config,
        {
            "grant_type": "authorization_code",
            "client_id": config.client_id,
            "code": code,
            "redirect_uri": config.redirect_uri,
            "code_verifier": verifier,
        },
    )
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise RuntimeError("token endpoint returned no refresh token")
    keyring.set_password(KEYRING_SERVICE, config.client_id, refresh)
    _cache.pop(config.client_id, None)


def logout(config: AuthConfig) -> None:
    _cache.pop(config.client_id, None)
    try:
        keyring.delete_password(KEYRING_SERVICE, config.client_id)
    except keyring.errors.PasswordDeleteError:
        pass


def access_token(config: AuthConfig, force: bool = False) -> str:
    cached = _cache.get(config.client_id)
    if not force and cached and time.time() < cached[1] - EXPIRY_MARGIN_SECONDS:
        return cached[0]
    refresh = keyring.get_password(KEYRING_SERVICE, config.client_id)
    if not refresh:
        raise LoginRequired("not signed in; run login")
    tokens = _token_request(
        config,
        {
            "grant_type": "refresh_token",
            "client_id": config.client_id,
            "refresh_token": refresh,
        },
    )
    token = tokens.get("access_token")
    if not token:
        raise RuntimeError("token endpoint returned no access token")
    _cache[config.client_id] = (token, time.time() + int(tokens.get("expires_in", 0)))
    return token
