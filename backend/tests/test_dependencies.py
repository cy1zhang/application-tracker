import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies import get_current_identity, get_current_subject


def request_from(host: str) -> Request:
    return Request({"type": "http", "client": (host, 50000)})


@pytest.fixture(autouse=True)
def clear_auth_environment(monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)


def test_subject_requires_authentication_by_default() -> None:
    with pytest.raises(HTTPException) as error:
        get_current_subject(request_from("127.0.0.1"))

    assert error.value.status_code == 401


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_local_dev_subject_is_used_for_loopback_clients(monkeypatch, host) -> None:
    monkeypatch.setenv("LOCAL_DEV_SUBJECT", " local-dev-user ")

    assert get_current_subject(request_from(host)) == "local-dev-user"


def test_local_dev_subject_is_refused_for_remote_clients(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_SUBJECT", "local-dev-user")

    with pytest.raises(HTTPException) as error:
        get_current_subject(request_from("203.0.113.7"))

    assert error.value.status_code == 401


def test_blank_local_dev_subject_still_requires_authentication(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_SUBJECT", "   ")

    with pytest.raises(HTTPException) as error:
        get_current_subject(request_from("127.0.0.1"))

    assert error.value.status_code == 401


def test_local_dev_subject_is_refused_inside_lambda(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_SUBJECT", "local-dev-user")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "application-tracker-api")

    with pytest.raises(RuntimeError, match="LOCAL_DEV_SUBJECT"):
        get_current_subject(request_from("127.0.0.1"))


def gmail_request(claims: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "client": ("203.0.113.7", 50000),
            "aws.event": {"requestContext": {"authorizer": {"jwt": {"claims": claims}}}},
        }
    )


class FakeCognito:
    def __init__(self, attributes: list[dict[str, str]]) -> None:
        self.attributes = attributes

    def admin_get_user(self, *, UserPoolId: str, Username: str) -> dict:
        assert UserPoolId == "test-pool"
        assert Username == "google_alex"
        return {"Username": Username, "UserAttributes": self.attributes}


def test_gmail_identity_from_approved_claims(monkeypatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "test-pool")
    request = gmail_request(
        {"sub": "owner-a", "username": "google_alex", "cognito:groups": "tracker-users"}
    )
    client = FakeCognito(
        [
            {"Name": "sub", "Value": "owner-a"},
            {"Name": "email", "Value": "alex@example.test"},
            {"Name": "email_verified", "Value": "true"},
        ]
    )

    identity = get_current_identity(request, get_current_subject(request), client)

    assert identity.owner_id == "owner-a"
    assert identity.username == "google_alex"
    assert identity.email == "alex@example.test"


def test_gmail_identity_rejects_missing_username(monkeypatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "test-pool")
    request = gmail_request({"sub": "owner-a", "cognito:groups": "tracker-users"})

    with pytest.raises(HTTPException) as error:
        get_current_identity(request, get_current_subject(request), FakeCognito([]))

    assert error.value.status_code == 401


def test_gmail_identity_rejects_mismatched_sub(monkeypatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "test-pool")
    request = gmail_request(
        {"sub": "owner-a", "username": "google_alex", "cognito:groups": "tracker-users"}
    )
    client = FakeCognito(
        [
            {"Name": "sub", "Value": "owner-b"},
            {"Name": "email", "Value": "alex@example.test"},
            {"Name": "email_verified", "Value": "true"},
        ]
    )

    with pytest.raises(HTTPException) as error:
        get_current_identity(request, get_current_subject(request), client)

    assert error.value.status_code == 401


def test_gmail_identity_rejects_unverified_email(monkeypatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "test-pool")
    request = gmail_request(
        {"sub": "owner-a", "username": "google_alex", "cognito:groups": "tracker-users"}
    )
    client = FakeCognito(
        [
            {"Name": "sub", "Value": "owner-a"},
            {"Name": "email", "Value": "alex@example.test"},
            {"Name": "email_verified", "Value": "false"},
        ]
    )

    with pytest.raises(HTTPException) as error:
        get_current_identity(request, get_current_subject(request), client)

    assert error.value.status_code == 401
