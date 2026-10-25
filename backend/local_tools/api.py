"""Small authenticated client for the tracker API."""

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from local_tools.auth import access_token
from local_tools.config import AuthConfig

_SAFE_DETAIL = re.compile(r"[a-z0-9_ ]{1,80}")


class ApiError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"API returned HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _safe_detail(raw: bytes) -> str:
    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        return "other"
    if isinstance(detail, str) and _SAFE_DETAIL.fullmatch(detail):
        return detail
    return "other"


class TrackerApi:
    def __init__(self, config: AuthConfig, token: Callable = access_token) -> None:
        self._config = config
        self._token = token

    def get(self, path: str, params: dict | None = None) -> dict:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        return self._request("GET", path + query, None)

    def post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, body)

    def _request(self, method: str, path: str, body: dict | None) -> dict:
        try:
            return self._send(method, path, body, self._bearer())
        except ApiError as error:
            if error.status != 401:
                raise
        return self._send(method, path, body, self._bearer(force=True))

    def _bearer(self, force: bool = False) -> str:
        try:
            return self._token(self._config, force=True) if force else self._token(self._config)
        except (OSError, http.client.HTTPException):
            # A token refresh that cannot reach Cognito is a network failure too.
            raise ApiError(0, "network") from None

    def _send(self, method: str, path: str, body: dict | None, token: str) -> dict:
        headers = {"Authorization": f"Bearer {token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self._config.api_base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, _safe_detail(error.read())) from None
        except (OSError, http.client.HTTPException):
            # URLError, timeouts and connection resets: a sleeping or offline Mac.
            raise ApiError(0, "network") from None
