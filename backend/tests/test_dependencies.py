import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies import get_current_subject


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
