import json
from collections.abc import Callable
from http.client import HTTPException
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class OAuthError(Exception):
    pass


class GoogleOAuthClient:
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

    def __init__(
        self,
        secrets_client,
        secret_name: str,
        redirect_uri: str,
        *,
        urlopen: Callable[..., Any] = urlopen,
    ) -> None:
        self._secrets = secrets_client
        self._secret_name = secret_name
        self._redirect_uri = redirect_uri
        self._urlopen = urlopen

    def authorization_url(self, state: str, *, prompt_consent: bool) -> str:
        client_id, _ = self._credentials()
        params = {
            "client_id": client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": self.SCOPE,
            "access_type": "offline",
            "state": state,
        }
        if prompt_consent:
            params["prompt"] = "consent"
        return f"{self.AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, str | None]:
        client_id, client_secret = self._credentials()
        request = Request(
            self.TOKEN_URL,
            data=urlencode(
                {
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                }
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = self._fetch_json(request)
        access_token = response.get("access_token")
        refresh_token = response.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthError("Google OAuth request failed")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise OAuthError("Google OAuth request failed")
        return {"access_token": access_token, "refresh_token": refresh_token}

    def profile_email(self, access_token: str) -> str:
        response = self._fetch_json(
            Request(self.PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"})
        )
        email = response.get("emailAddress")
        if not isinstance(email, str) or not email.strip():
            raise OAuthError("Google OAuth request failed")
        return email

    def _credentials(self) -> tuple[str, str]:
        try:
            value = self._secrets.get_secret_value(SecretId=self._secret_name)
            secret = json.loads(value["SecretString"])
            client_id = secret["client_id"]
            client_secret = secret["client_secret"]
            if not isinstance(client_id, str) or not isinstance(client_secret, str):
                raise TypeError("Invalid OAuth secret")
            if not client_id or not client_secret:
                raise ValueError("Invalid OAuth secret")
            return client_id, client_secret
        except (KeyError, TypeError, ValueError):
            raise OAuthError("Google OAuth setup is unavailable") from None

    def _fetch_json(self, request: Request) -> dict:
        try:
            with self._urlopen(request, timeout=5) as response:
                value = json.loads(response.read(16_384))
            if not isinstance(value, dict):
                raise TypeError("Invalid Google response")
            return value
        except (OSError, HTTPException, ValueError, TypeError):
            raise OAuthError("Google OAuth request failed") from None
