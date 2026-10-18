import json
from io import BytesIO
from urllib.parse import parse_qs, urlparse

import pytest

from app.services.google_oauth import GoogleOAuthClient, OAuthError


class FakeSecrets:
    def get_secret_value(self, *, SecretId):
        assert SecretId == "gmail-test-secret"
        return {
            "SecretString": json.dumps(
                {"client_id": "test-client-id", "client_secret": "test-client-secret"}
            )
        }


def test_authorization_url_requests_only_readonly_offline_consent() -> None:
    client = GoogleOAuthClient(
        FakeSecrets(), "gmail-test-secret", "https://api.example.test/oauth/google/callback"
    )

    url = client.authorization_url("random-state", prompt_consent=True)
    query = parse_qs(urlparse(url).query)

    assert urlparse(url).hostname == "accounts.google.com"
    assert query == {
        "client_id": ["test-client-id"],
        "redirect_uri": ["https://api.example.test/oauth/google/callback"],
        "response_type": ["code"],
        "scope": ["https://www.googleapis.com/auth/gmail.readonly"],
        "access_type": ["offline"],
        "state": ["random-state"],
        "prompt": ["consent"],
    }
    assert "test-client-secret" not in url


def test_code_exchange_and_profile_use_server_requests() -> None:
    seen = []

    def open_request(request, *, timeout):
        assert timeout <= 10
        seen.append(request)
        if request.full_url == "https://oauth2.googleapis.com/token":
            return BytesIO(
                json.dumps(
                    {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}
                ).encode()
            )
        return BytesIO(json.dumps({"emailAddress": "alex@example.test"}).encode())

    client = GoogleOAuthClient(
        FakeSecrets(),
        "gmail-test-secret",
        "https://api.example.test/oauth/google/callback",
        urlopen=open_request,
    )

    tokens = client.exchange_code("synthetic-code")
    email = client.profile_email(tokens["access_token"])

    assert tokens == {
        "access_token": "synthetic-access",
        "refresh_token": "synthetic-refresh",
    }
    assert email == "alex@example.test"
    token_body = parse_qs(seen[0].data.decode())
    assert token_body["code"] == ["synthetic-code"]
    assert token_body["client_secret"] == ["test-client-secret"]
    assert seen[1].headers["Authorization"] == "Bearer synthetic-access"


def test_invalid_provider_response_is_sanitized() -> None:
    client = GoogleOAuthClient(
        FakeSecrets(),
        "gmail-test-secret",
        "https://api.example.test/oauth/google/callback",
        urlopen=lambda request, *, timeout: BytesIO(b'{"error":"synthetic-secret"}'),
    )

    with pytest.raises(OAuthError) as error:
        client.exchange_code("synthetic-code")

    assert "synthetic-secret" not in str(error.value)
