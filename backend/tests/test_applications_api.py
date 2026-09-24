import json
from base64 import urlsafe_b64encode

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_application_repository, get_current_subject
from app.main import app as fastapi_app


@pytest.fixture
def api_client(repository):
    fastapi_app.dependency_overrides[get_application_repository] = lambda: repository
    with TestClient(fastapi_app) as client:
        yield client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def authenticate_as():
    def set_subject(subject: str) -> None:
        fastapi_app.dependency_overrides[get_current_subject] = lambda: subject

    return set_subject


@pytest.fixture
def client_without_subject(api_client):
    return api_client


@pytest.fixture
def client_as_user_a(api_client, authenticate_as):
    authenticate_as("user-a")
    return api_client


def create_application(client) -> dict:
    response = client.post(
        "/applications",
        json={"company": "Acme", "role": "Engineer", "status": "applied"},
    )
    assert response.status_code == 201
    return response.json()


def test_applications_require_authentication(client_without_subject) -> None:
    response = client_without_subject.get("/applications")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


def test_create_and_read_application(client_as_user_a) -> None:
    create_response = client_as_user_a.post(
        "/applications",
        json={
            "company": "Acme",
            "role": "Software Engineer Intern",
            "status": "applied",
        },
    )

    assert create_response.status_code == 201
    application_id = create_response.json()["application_id"]
    assert (
        client_as_user_a.get(f"/applications/{application_id}").status_code == 200
    )


def test_create_rejects_invalid_values(client_as_user_a) -> None:
    response = client_as_user_a.post(
        "/applications",
        json={"company": "  ", "role": "Engineer", "status": "applied"},
    )

    assert response.status_code == 422


def test_cross_owner_get_returns_not_found(api_client, authenticate_as) -> None:
    authenticate_as("user-a")
    created = api_client.post(
        "/applications",
        json={"company": "Acme", "role": "Engineer", "status": "applied"},
    ).json()
    authenticate_as("user-b")

    response = api_client.get(f"/applications/{created['application_id']}")

    assert response.status_code == 404


def test_malformed_cursor_returns_unprocessable_entity(client_as_user_a) -> None:
    response = client_as_user_a.get("/applications?cursor=not-a-cursor")

    assert response.status_code == 422


@pytest.mark.parametrize("cursor", ["☃", "invalid-suffix"])
def test_additional_invalid_cursors_return_unprocessable_entity(
    client_as_user_a,
    cursor,
) -> None:
    if cursor == "invalid-suffix":
        valid_cursor = (
            urlsafe_b64encode(json.dumps({"application_id": "app-1"}).encode())
            .decode()
            .rstrip("=")
        )
        cursor = f"{valid_cursor}!!!!"

    response = client_as_user_a.get("/applications", params={"cursor": cursor})

    assert response.status_code == 422


def test_patch_updates_application(client_as_user_a) -> None:
    created = create_application(client_as_user_a)

    response = client_as_user_a.patch(
        f"/applications/{created['application_id']}",
        json={"expected_version": 1, "role": "Senior Engineer"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "Senior Engineer"
    assert response.json()["version"] == 2


def test_patch_requires_current_version(client_as_user_a) -> None:
    created = create_application(client_as_user_a)

    response = client_as_user_a.patch(
        f"/applications/{created['application_id']}",
        json={"expected_version": 9, "role": "Changed"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Application version is stale"}


def test_cross_owner_patch_returns_not_found(api_client, authenticate_as) -> None:
    authenticate_as("user-a")
    created = create_application(api_client)
    authenticate_as("user-b")

    response = api_client.patch(
        f"/applications/{created['application_id']}",
        json={"expected_version": 1, "role": "Changed"},
    )

    assert response.status_code == 404


def test_cross_owner_delete_returns_not_found(api_client, authenticate_as) -> None:
    authenticate_as("user-a")
    created = create_application(api_client)
    authenticate_as("user-b")

    response = api_client.delete(
        f"/applications/{created['application_id']}?expected_version=1"
    )

    assert response.status_code == 404


def test_delete_hides_application(client_as_user_a) -> None:
    created = create_application(client_as_user_a)

    response = client_as_user_a.delete(
        f"/applications/{created['application_id']}?expected_version=1"
    )

    assert response.status_code == 204
    assert (
        client_as_user_a.get(f"/applications/{created['application_id']}").status_code
        == 404
    )
