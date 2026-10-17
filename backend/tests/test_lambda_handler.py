import json
from types import SimpleNamespace

import pytest


def api_event(method, path, *, subject=None, groups=("tracker-users",), body=None, headers=None):
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": path,
        "rawQueryString": "",
        "headers": {"host": "example.execute-api.us-east-1.amazonaws.com", **(headers or {})},
        "requestContext": {
            "http": {
                "method": method,
                "path": path,
                "protocol": "HTTP/1.1",
                "sourceIp": "198.51.100.10",
                "userAgent": "pytest",
            },
            "requestId": "test-request",
            "stage": "$default",
            "timeEpoch": 1,
        },
        "isBase64Encoded": False,
    }
    if subject is not None:
        claims = {"sub": subject}
        if groups is not None:
            claims["cognito:groups"] = groups
        event["requestContext"]["authorizer"] = {
            "jwt": {"claims": claims, "scopes": []}
        }
    if body is not None:
        event["body"] = json.dumps(body)
        event["headers"]["content-type"] = "application/json"
    return event


def invoke(event):
    from app.lambda_handler import handler

    return handler(event, SimpleNamespace())


@pytest.fixture
def lambda_tables(repository, monkeypatch):
    monkeypatch.setenv("APPLICATIONS_TABLE", "Applications")
    monkeypatch.setenv("EVENTS_TABLE", "Events")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "application-tracker-api")
    monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
    return repository


def create_application(subject="user-a", headers=None):
    return invoke(
        api_event(
            "POST",
            "/applications",
            subject=subject,
            headers=headers,
            body={"company": "Acme", "role": "Engineer", "status": "applied"},
        )
    )


def test_lambda_claims_select_owner(lambda_tables) -> None:
    response = create_application()

    assert response["statusCode"] == 201
    application_id = json.loads(response["body"])["application_id"]
    assert lambda_tables.get(owner_id="user-a", application_id=application_id).company == "Acme"


def test_lambda_missing_claims_fail_closed(lambda_tables) -> None:
    response = invoke(api_event("GET", "/applications"))

    assert response["statusCode"] == 401


@pytest.mark.parametrize("groups", [None, [], ["other-users"], "[other-users]", "[tracker-users-extra]"])
def test_signed_in_user_without_approval_cannot_list_applications(lambda_tables, groups) -> None:
    response = invoke(api_event("GET", "/applications", subject="user-a", groups=groups))

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["detail"] == "Access denied"


def test_signed_in_user_without_approval_cannot_create_application(lambda_tables) -> None:
    response = invoke(
        api_event(
            "POST", "/applications", subject="user-a", groups=None,
            body={"company": "Acme", "role": "Engineer", "status": "applied"},
        )
    )

    assert response["statusCode"] == 403
    assert lambda_tables.list(owner_id="user-a", limit=100, cursor=None).items == []


@pytest.mark.parametrize("groups", ["[tracker-users]", '["other-users", "tracker-users"]'])
def test_approved_group_claim_from_gateway_can_list_applications(lambda_tables, groups) -> None:
    response = invoke(api_event("GET", "/applications", subject="user-a", groups=groups))

    assert response["statusCode"] == 200


def test_lambda_rejects_local_subject_configuration(lambda_tables, monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DEV_SUBJECT", "local-dev-user")

    response = invoke(api_event("GET", "/applications"))

    assert response["statusCode"] == 500


def test_forged_owner_header_is_ignored(lambda_tables) -> None:
    response = create_application(headers={"x-owner": "user-b"})

    assert response["statusCode"] == 201
    application_id = json.loads(response["body"])["application_id"]
    assert lambda_tables.get(owner_id="user-a", application_id=application_id).company == "Acme"
    assert lambda_tables.get(owner_id="user-b", application_id=application_id) is None


def test_other_subject_cannot_read_application(lambda_tables) -> None:
    created = create_application()
    application_id = json.loads(created["body"])["application_id"]

    response = invoke(
        api_event("GET", f"/applications/{application_id}", subject="user-b")
    )

    assert response["statusCode"] == 404


def test_public_health_without_claims(lambda_tables) -> None:
    response = invoke(api_event("GET", "/health"))

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"status": "ok"}
