# Application Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the typed application domain, owner-scoped CRUD API, immutable history events, optimistic version checks, and DynamoDB persistence required by roadmap milestone 2.

**Architecture:** FastAPI routes obtain the current subject and an application repository through dependencies, then pass the subject into every repository operation. A direct `boto3` repository stores applications and history events in separate DynamoDB tables, uses transactional writes for each application mutation plus its history event, and is integration-tested with `moto` rather than an in-memory imitation of DynamoDB.

**Tech Stack:** Python 3.12.13, FastAPI 0.141.1, Pydantic 2, boto3 1.43.101, moto 5.2.3, pytest 8.4.2, Ruff 0.16.8

**Spec:** [`docs/PRD.md`](../../PRD.md) and [`docs/TECHNICAL_DESIGN.md`](../../TECHNICAL_DESIGN.md)

## Global Constraints

- Every application and event operation receives the authenticated owner subject; request bodies and path parameters never select an owner.
- Cross-owner reads and mutations return `404` so record existence is not disclosed.
- Company, role, cycle, dates, and status are never inferred. Nullable fields remain `null` when the user explicitly clears them.
- Manual creation requires non-empty company, role, and status values; no endpoint silently defaults a status to `Applied`.
- A mutation of an application and its history event is one DynamoDB transaction.
- Updates and deletes require the caller's expected version and return `409` when it is stale.
- Public tests contain only synthetic values and fake AWS credentials supplied by `moto`.
- Cognito validation remains outside this milestone. The production authentication dependency returns `401` until milestone 4 replaces it; tests override that dependency with a synthetic subject.
- Application deletion replaces the application with a minimal tombstone and hides it from reads and lists. History remains until account deletion so the audit record is not silently lost.

## Review Focus

- A valid application ID belonging to another owner behaves exactly like a missing ID; Task 4 tests `GET` and Task 5 tests `PATCH` and `DELETE`.
- A stale version cannot write an application or a history event; Task 5 verifies both tables after rejected updates and deletes.
- PATCH distinguishes an omitted field from an explicit `null`; Tasks 1 and 5 verify that clearing a nullable field is recorded while omitted values stay unchanged.
- A deleted application cannot reappear in `GET` or `LIST`, and cannot be updated; Task 5 tests all three cases.
- A malformed or tampered pagination cursor cannot change the owner partition and returns `422`; Tasks 3 and 4 cover repository and HTTP behavior.

---

### Task 1: Domain models and validation

**Files:**

- Create: `backend/app/domain/__init__.py`
- Create: `backend/app/domain/applications.py`
- Test: `backend/tests/test_application_models.py`

**Interfaces:**

- Produces: `RoleType`, `Season`, `ApplicationStatus`, `EventOrigin`, `EventType`, `ApplicationCreate`, `ApplicationPatch`, `Application`, `ApplicationPage`, `FieldChange`, and `ApplicationEvent`.
- `ApplicationPatch.updates() -> dict[str, str | int | date | None]` returns only fields explicitly supplied by the caller.

- [ ] **Step 1: Write failing model tests**

```python
from datetime import date

import pytest
from pydantic import ValidationError

from app.domain.applications import (
    ApplicationCreate,
    ApplicationPatch,
    ApplicationStatus,
    Season,
)


def test_create_requires_a_non_empty_company() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="  ", role="Engineer", status=ApplicationStatus.APPLIED)


def test_create_does_not_default_status() -> None:
    with pytest.raises(ValidationError):
        ApplicationCreate(company="Acme", role="Engineer")


def test_patch_distinguishes_null_from_omitted_fields() -> None:
    patch = ApplicationPatch(expected_version=3, season=None)

    assert patch.updates() == {"season": None}


def test_patch_rejects_no_changes() -> None:
    with pytest.raises(ValidationError):
        ApplicationPatch(expected_version=3)


def test_cycle_fields_are_independently_nullable() -> None:
    payload = ApplicationCreate(
        company="Acme",
        role="Engineer",
        status=ApplicationStatus.APPLIED,
        season=Season.SUMMER,
        year=None,
        application_date=date(2026, 9, 24),
    )

    assert payload.season is Season.SUMMER
    assert payload.year is None
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `cd backend && uv run pytest tests/test_application_models.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.domain'`.

- [ ] **Step 3: Add the domain models**

Implement `backend/app/domain/applications.py` with these public definitions:

```python
from datetime import date, datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
FieldValue = str | int | None


class RoleType(str, Enum):
    INTERNSHIP = "internship"
    CO_OP = "co_op"
    FULL_TIME_NEW_GRAD = "full_time_new_grad"
    UNKNOWN = "unknown"


class Season(str, Enum):
    SUMMER = "summer"
    FALL = "fall"
    SPRING = "spring"
    WINTER = "winter"


class ApplicationStatus(str, Enum):
    APPLIED = "applied"
    OA_RECEIVED = "oa_received"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class EventOrigin(str, Enum):
    USER = "user"
    RULES = "rules"
    AI = "ai"


class EventType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Name
    role: Name
    role_type: RoleType = RoleType.UNKNOWN
    season: Season | None = None
    year: int | None = None
    status: ApplicationStatus
    application_date: date | None = None


class ApplicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    company: Name | None = None
    role: Name | None = None
    role_type: RoleType | None = None
    season: Season | None = None
    year: int | None = None
    status: ApplicationStatus | None = None
    application_date: date | None = None

    def updates(self) -> dict[str, FieldValue | date]:
        return {
            field: getattr(self, field)
            for field in self.model_fields_set
            if field != "expected_version"
        }

    @model_validator(mode="after")
    def require_change(self) -> "ApplicationPatch":
        if not self.updates():
            raise ValueError("at least one application field is required")
        return self


class Application(BaseModel):
    application_id: str
    company: str | None
    role: str | None
    role_type: RoleType | None
    season: Season | None
    year: int | None
    status: ApplicationStatus | None
    application_date: date | None
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class ApplicationPage(BaseModel):
    items: list[Application]
    next_cursor: str | None


class FieldChange(BaseModel):
    before: FieldValue
    after: FieldValue


class ApplicationEvent(BaseModel):
    event_id: str
    application_id: str
    event_type: EventType
    origin: EventOrigin
    occurred_at: datetime
    changes: dict[str, FieldChange]
```

- [ ] **Step 4: Verify GREEN and lint**

Run: `cd backend && uv run pytest tests/test_application_models.py -q && uv run ruff check app/domain tests/test_application_models.py`

Expected: five tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain backend/tests/test_application_models.py
git commit -m "feat: define application domain models"
```

### Task 2: Repository contract and DynamoDB test foundation

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/app/repositories/__init__.py`
- Create: `backend/app/repositories/applications.py`
- Create: `backend/app/repositories/dynamodb.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_dynamodb_repository.py`

**Interfaces:**

- Consumes: every model from Task 1.
- Produces: `ApplicationRepository`, `ApplicationNotFound`, `VersionConflict`, `InvalidCursor`, and `DynamoApplicationRepository`.
- Repository mutation methods receive deterministic `application_id`, `event_id`, and `occurred_at` values from the caller so tests never patch UUID or clock globals.

- [ ] **Step 1: Add the DynamoDB dependency declarations**

Add `"boto3>=1.43.101,<2.0.0"` to `[project].dependencies` and `"moto[dynamodb]>=5.2.3,<6.0.0"` to `[dependency-groups].dev`, then run:

```bash
cd backend
uv lock
uv sync --locked --dev
```

Expected: the lock resolves boto3 1.43.101 or a compatible later 1.x release and moto 5.2.3 or a compatible later 5.x release.

- [ ] **Step 2: Write a failing repository-construction test**

Create DynamoDB tables inside `mock_aws()` in `backend/tests/conftest.py`. The Applications table has `owner_id` as its partition key and `application_id` as its sort key. The Events table has `owner_id` and `event_id`, plus an `application-events-index` GSI whose keys are `owner_application_id` and `occurred_at_event_id`.

Define the table helpers explicitly:

```python
def create_application_table(client) -> None:
    client.create_table(
        TableName="Applications",
        KeySchema=[
            {"AttributeName": "owner_id", "KeyType": "HASH"},
            {"AttributeName": "application_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "application_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def create_event_table(client) -> None:
    client.create_table(
        TableName="Events",
        KeySchema=[
            {"AttributeName": "owner_id", "KeyType": "HASH"},
            {"AttributeName": "event_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "event_id", "AttributeType": "S"},
            {"AttributeName": "owner_application_id", "AttributeType": "S"},
            {"AttributeName": "occurred_at_event_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "application-events-index",
                "KeySchema": [
                    {"AttributeName": "owner_application_id", "KeyType": "HASH"},
                    {"AttributeName": "occurred_at_event_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
```

Then define the repository fixture and a deterministic application factory used by later tests:

```python
@pytest.fixture
def repository() -> Iterator[DynamoApplicationRepository]:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        create_application_table(client)
        create_event_table(client)
        yield DynamoApplicationRepository(
            client=client,
            applications_table="Applications",
            events_table="Events",
        )


@pytest.fixture
def application_factory():
    sequence = count(1)

    def create(repository, *, owner_id: str, application_id: str, **overrides):
        number = next(sequence)
        values = {
            "company": "Acme",
            "role": "Software Engineer Intern",
            "status": ApplicationStatus.APPLIED,
        }
        values.update(overrides)
        return repository.create(
            owner_id=owner_id,
            payload=ApplicationCreate(**values),
            application_id=application_id,
            event_id=f"event-create-{number}",
            occurred_at=datetime(2026, 9, 24, tzinfo=UTC)
            + timedelta(seconds=number),
        )

    return create
```

Import `Iterator` from `collections.abc`, `UTC`, `datetime`, and `timedelta` from `datetime`, `count` from `itertools`, `boto3`, `pytest`, and `mock_aws`. Import the domain and repository types used by the fixtures.

Add this initial test:

```python
def test_repository_starts_with_no_applications(
    repository: DynamoApplicationRepository,
) -> None:
    page = repository.list(owner_id="user-a", limit=20, cursor=None)

    assert page.items == []
    assert page.next_cursor is None
```

- [ ] **Step 3: Run the test and verify RED**

Run: `cd backend && uv run pytest tests/test_dynamodb_repository.py -q`

Expected: collection fails because `app.repositories.dynamodb` does not exist.

- [ ] **Step 4: Define the repository contract**

Create `backend/app/repositories/applications.py` with these signatures:

```python
from datetime import datetime
from typing import Protocol

from app.domain.applications import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationPage,
    ApplicationPatch,
)


class ApplicationNotFound(Exception):
    pass


class VersionConflict(Exception):
    pass


class InvalidCursor(Exception):
    pass


class ApplicationRepository(Protocol):
    def create(
        self,
        *,
        owner_id: str,
        payload: ApplicationCreate,
        application_id: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Application: ...

    def list(self, *, owner_id: str, limit: int, cursor: str | None) -> ApplicationPage: ...

    def get(self, *, owner_id: str, application_id: str) -> Application | None: ...

    def list_events(
        self, *, owner_id: str, application_id: str
    ) -> list[ApplicationEvent]: ...

    def update(
        self,
        *,
        owner_id: str,
        application_id: str,
        patch: ApplicationPatch,
        event_id: str,
        occurred_at: datetime,
    ) -> Application: ...

    def delete(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_version: int,
        event_id: str,
        occurred_at: datetime,
    ) -> None: ...
```

- [ ] **Step 5: Add minimal DynamoDB query and serialization helpers**

Create `DynamoApplicationRepository` with constructor fields `client`, `applications_table`, and `events_table`. Use `TypeSerializer` and `TypeDeserializer` for low-level client items. Add URL-safe base64 cursor helpers that encode only `{"application_id": "..."}`; decoding any other shape raises `InvalidCursor`.

The initial `list()` implementation queries only the supplied `owner_id`, uses an `ExclusiveStartKey` reconstructed with that same owner, filters out items containing `deleted_at`, and maps results to `ApplicationPage`.

- [ ] **Step 6: Verify GREEN and commit**

Run: `cd backend && uv run pytest tests/test_dynamodb_repository.py -q && uv run ruff check app/repositories tests/conftest.py tests/test_dynamodb_repository.py`

Expected: the empty-list test passes and Ruff is clean.

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/repositories backend/tests/conftest.py backend/tests/test_dynamodb_repository.py
git commit -m "feat: add DynamoDB repository foundation"
```

### Task 3: Owner-scoped create, get, list, pagination, and history

**Files:**

- Modify: `backend/app/repositories/dynamodb.py`
- Modify: `backend/tests/test_dynamodb_repository.py`

**Interfaces:**

- Consumes: Task 1 models and Task 2 repository contract.
- Produces: working `create`, `get`, `list`, and `list_events` methods.
- A created application always starts at version `1` and writes one `created` event in the same transaction.

- [ ] **Step 1: Write failing repository behavior tests**

Add tests using fixed IDs and UTC datetimes:

```python
def test_create_writes_application_and_history(repository) -> None:
    created = repository.create(
        owner_id="user-a",
        payload=ApplicationCreate(
            company="Acme",
            role="Software Engineer Intern",
            status=ApplicationStatus.APPLIED,
        ),
        application_id="app-1",
        event_id="event-1",
        occurred_at=datetime(2026, 9, 24, 16, 0, tzinfo=UTC),
    )

    assert created.application_id == "app-1"
    assert created.version == 1
    assert repository.get(owner_id="user-a", application_id="app-1") == created
    events = repository.list_events(owner_id="user-a", application_id="app-1")
    assert [event.event_type for event in events] == [EventType.CREATED]


def test_get_does_not_cross_owner_boundary(repository, application_factory) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")

    assert repository.get(owner_id="user-b", application_id="app-1") is None


def test_list_is_owner_scoped_and_paginated(repository, application_factory) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")
    application_factory(repository, owner_id="user-a", application_id="app-2")
    application_factory(repository, owner_id="user-b", application_id="app-3")

    first = repository.list(owner_id="user-a", limit=1, cursor=None)
    second = repository.list(owner_id="user-a", limit=1, cursor=first.next_cursor)

    assert [item.application_id for item in first.items + second.items] == ["app-1", "app-2"]
    assert all(item.application_id != "app-3" for item in first.items + second.items)


def test_tampered_cursor_cannot_select_another_owner(repository) -> None:
    foreign_cursor = urlsafe_b64encode(
        json.dumps({"application_id": "app-foreign"}).encode()
    ).decode().rstrip("=")

    page = repository.list(owner_id="user-a", limit=10, cursor=foreign_cursor)

    assert page.items == []


def test_malformed_cursor_is_rejected(repository) -> None:
    with pytest.raises(InvalidCursor):
        repository.list(owner_id="user-a", limit=10, cursor="not-a-cursor")
```

Import `urlsafe_b64encode` from `base64` and `json` for the cursor test.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `cd backend && uv run pytest tests/test_dynamodb_repository.py -q`

Expected: create/history tests fail because the mutation methods are not implemented.

- [ ] **Step 3: Implement transactional create and history mapping**

Build an application item with the current owner, provided IDs, version `1`, and ISO-formatted dates/timestamps. Build a created event with origin `EventOrigin.USER`, `before=None`, and the created value for every user-editable application field. Call `transact_write_items` with:

```python
TransactItems=[
    {
        "Put": {
            "TableName": self._applications_table,
            "Item": serialize_item(application_item),
            "ConditionExpression": "attribute_not_exists(application_id)",
        }
    },
    {
        "Put": {
            "TableName": self._events_table,
            "Item": serialize_item(event_item),
            "ConditionExpression": "attribute_not_exists(event_id)",
        }
    },
]
```

Map stored items back through the Task 1 Pydantic models. Query history through `application-events-index` using `owner_application_id=f"{owner_id}#{application_id}"` and ascending `occurred_at_event_id` ordering.

- [ ] **Step 4: Implement owner-scoped pagination**

Use a DynamoDB `Query` on `owner_id`, never `Scan`. Rebuild the exclusive-start key as `{"owner_id": owner_id, "application_id": decoded_application_id}` so cursor content can never choose another partition. A cursor that is not valid URL-safe base64 JSON with exactly one non-empty string field named `application_id` raises `InvalidCursor`.

- [ ] **Step 5: Verify GREEN and commit**

Run: `cd backend && uv run pytest tests/test_dynamodb_repository.py -q && uv run ruff check app/repositories tests/test_dynamodb_repository.py`

Expected: repository tests pass with owner isolation, chronological history, and pagination.

```bash
git add backend/app/repositories/dynamodb.py backend/tests/test_dynamodb_repository.py
git commit -m "feat: persist owner-scoped applications"
```

### Task 4: Auth seam and create/read HTTP API

**Files:**

- Create: `backend/app/api/__init__.py`
- Create: `backend/app/api/dependencies.py`
- Create: `backend/app/api/applications.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_applications_api.py`

**Interfaces:**

- Consumes: `ApplicationRepository` and Task 1 request/response models.
- Produces: `POST /applications`, `GET /applications`, `GET /applications/{application_id}`, and `GET /applications/{application_id}/events`.
- Produces dependency functions `get_current_subject() -> str` and `get_application_repository() -> ApplicationRepository` for later Cognito and infrastructure replacement.

- [ ] **Step 1: Write failing unauthenticated and CRUD API tests**

Use FastAPI dependency overrides rather than auth headers. Define these fixtures and helper in `backend/tests/test_applications_api.py`:

```python
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
```

Import the application as `fastapi_app`, both dependency functions, `pytest`, and `TestClient`. Then add the behavior tests:

```python
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
    assert client_as_user_a.get(f"/applications/{application_id}").status_code == 200


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
```

- [ ] **Step 2: Run API tests and verify RED**

Run: `cd backend && uv run pytest tests/test_applications_api.py -q`

Expected: tests fail with `404` because the application routes are absent.

- [ ] **Step 3: Add dependency seams**

`get_current_subject()` raises `HTTPException(status_code=401, detail="Authentication required")`. `get_application_repository()` reads `APPLICATIONS_TABLE`, `EVENTS_TABLE`, and optional `AWS_REGION` environment variables only when invoked, constructs a DynamoDB client, and returns `DynamoApplicationRepository`. Do not construct AWS clients during module import or health checks.

- [ ] **Step 4: Add create and read routes**

Create an `APIRouter(prefix="/applications", tags=["applications"])`. Generate IDs with `uuid4().hex` and timestamps with `datetime.now(UTC)`. Route behavior is:

```text
POST   /applications                         -> 201 Application
GET    /applications?limit=50&cursor=...     -> 200 ApplicationPage
GET    /applications/{application_id}        -> 200 Application or 404
GET    /applications/{application_id}/events -> 200 list[ApplicationEvent] or 404
```

Validate `limit` with `Query(default=50, ge=1, le=100)`. Catch `InvalidCursor` and return `422` with `detail="Invalid pagination cursor"`. Before listing events, call `repository.get` and return `404` when the application is missing or tombstoned.

- [ ] **Step 5: Register the router and verify GREEN**

Add `app.include_router(applications.router)` in `backend/app/main.py`.

Run: `cd backend && uv run pytest tests/test_health.py tests/test_applications_api.py -q && uv run ruff check app/api app/main.py tests/test_applications_api.py`

Expected: health remains green; authentication, create, read, cross-owner, and invalid-cursor tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api backend/app/main.py backend/tests/test_applications_api.py
git commit -m "feat: expose owner-scoped application API"
```

### Task 5: Optimistic updates, deletion tombstones, and history

**Files:**

- Modify: `backend/app/repositories/dynamodb.py`
- Modify: `backend/app/api/applications.py`
- Modify: `backend/tests/test_dynamodb_repository.py`
- Modify: `backend/tests/test_applications_api.py`

**Interfaces:**

- Consumes: `ApplicationPatch.expected_version` for PATCH and `expected_version` query parameter for DELETE.
- Produces: `PATCH /applications/{application_id}` and `DELETE /applications/{application_id}?expected_version=N`.
- `VersionConflict` maps to HTTP `409`; `ApplicationNotFound` maps to HTTP `404`.

- [ ] **Step 1: Write failing repository mutation tests**

Add tests covering successful updates, explicit null, no-op values, stale versions, cross-owner IDs, deletion, and history:

```python
def test_update_clears_explicit_null_and_records_only_changed_fields(
    repository, application_factory
) -> None:
    application_factory(
        repository,
        owner_id="user-a",
        application_id="app-1",
        season=Season.SUMMER,
    )

    updated = repository.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, season=None),
        event_id="event-update",
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert updated.season is None
    assert updated.version == 2
    event = repository.list_events(owner_id="user-a", application_id="app-1")[-1]
    assert event.changes == {
        "season": FieldChange(before="summer", after=None)
    }


def test_same_value_update_does_not_increment_or_write_event(
    repository, application_factory
) -> None:
    original = application_factory(
        repository,
        owner_id="user-a",
        application_id="app-1",
    )

    unchanged = repository.update(
        owner_id="user-a",
        application_id="app-1",
        patch=ApplicationPatch(expected_version=1, role=original.role),
        event_id="event-no-op",
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert unchanged == original
    assert [event.event_id for event in repository.list_events(
        owner_id="user-a", application_id="app-1"
    )] == ["event-create-1"]


def test_stale_update_writes_neither_application_nor_event(
    repository, application_factory
) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")

    with pytest.raises(VersionConflict):
        repository.update(
            owner_id="user-a",
            application_id="app-1",
            patch=ApplicationPatch(expected_version=7, role="Changed"),
            event_id="event-stale",
            occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
        )

    assert repository.get(owner_id="user-a", application_id="app-1").version == 1
    assert all(
        event.event_id != "event-stale"
        for event in repository.list_events(owner_id="user-a", application_id="app-1")
    )


def test_stale_delete_writes_neither_tombstone_nor_event(
    repository, application_factory
) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")

    with pytest.raises(VersionConflict):
        repository.delete(
            owner_id="user-a",
            application_id="app-1",
            expected_version=7,
            event_id="event-stale-delete",
            occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
        )

    assert repository.get(owner_id="user-a", application_id="app-1").version == 1
    assert all(
        event.event_id != "event-stale-delete"
        for event in repository.list_events(owner_id="user-a", application_id="app-1")
    )


def test_deleted_application_is_hidden_and_cannot_be_updated(
    repository, application_factory
) -> None:
    application_factory(repository, owner_id="user-a", application_id="app-1")
    repository.delete(
        owner_id="user-a",
        application_id="app-1",
        expected_version=1,
        event_id="event-delete",
        occurred_at=datetime(2026, 9, 25, tzinfo=UTC),
    )

    assert repository.get(owner_id="user-a", application_id="app-1") is None
    assert repository.list(owner_id="user-a", limit=20, cursor=None).items == []
    with pytest.raises(ApplicationNotFound):
        repository.update(
            owner_id="user-a",
            application_id="app-1",
            patch=ApplicationPatch(expected_version=2, role="Changed"),
            event_id="event-after-delete",
            occurred_at=datetime(2026, 9, 26, tzinfo=UTC),
        )
```

- [ ] **Step 2: Run repository mutation tests and verify RED**

Run: `cd backend && uv run pytest tests/test_dynamodb_repository.py -q`

Expected: tests fail because `update` and `delete` are not implemented.

- [ ] **Step 3: Implement transactional update**

Read the current owner-scoped record consistently. Missing or tombstoned records raise `ApplicationNotFound`; a mismatched version raises `VersionConflict`. Build changes only from `patch.updates()` whose new value differs from the stored value. A same-value patch returns the existing record without incrementing the version or writing an event.

For a real change, write the new complete application item and an `EventOrigin.USER` update event in one transaction. The application put condition is:

```text
attribute_exists(application_id) AND attribute_not_exists(deleted_at) AND version = :expected_version
```

If DynamoDB cancels the transaction, re-read consistently and map the result to `ApplicationNotFound` or `VersionConflict`; never retry with a newer version automatically.

- [ ] **Step 4: Implement transactional delete tombstone**

Replace the application item with exactly `owner_id`, `application_id`, incremented `version`, and `deleted_at`, using the same existence/deleted/version condition. Put an `EventOrigin.USER` deleted event in the transaction whose changes map every previously stored user-editable application field from its old value to `None`. Keep events queryable inside the repository for future account deletion, while HTTP event access first checks that the application is active.

- [ ] **Step 5: Write failing HTTP mutation tests**

```python
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
    assert client_as_user_a.get(
        f"/applications/{created['application_id']}"
    ).status_code == 404
```

- [ ] **Step 6: Add PATCH and DELETE routes**

PATCH passes `ApplicationPatch` to the repository and returns the updated application. DELETE validates `expected_version` with `Query(ge=1)`, returns an empty `204` response, and never accepts an owner value from the request. Map repository exceptions to the exact status/detail pairs in the tests.

- [ ] **Step 7: Verify GREEN and commit**

Run: `cd backend && uv run pytest tests/test_dynamodb_repository.py tests/test_applications_api.py -q && uv run ruff check app tests`

Expected: repository and HTTP mutation tests pass; stale and cross-owner attempts write nothing.

```bash
git add backend/app/repositories/dynamodb.py backend/app/api/applications.py backend/tests/test_dynamodb_repository.py backend/tests/test_applications_api.py
git commit -m "feat: add versioned application mutations"
```

### Task 6: Documentation and whole-milestone verification

**Files:**

- Modify: `docs/DEVELOPMENT.md`
- Modify: `docs/IMPLEMENTATION_ROADMAP.md`

**Interfaces:**

- Consumes: all backend commands and environment variable names introduced by Tasks 1–5.
- Produces: accurate development documentation and a checked milestone 2 status in the roadmap.

- [ ] **Step 1: Document the current domain boundary**

Add a concise section to `docs/DEVELOPMENT.md` that states:

```markdown
## Application domain

Application CRUD uses the `APPLICATIONS_TABLE` and `EVENTS_TABLE` DynamoDB
tables and the optional `AWS_REGION` setting. Automated tests create these
tables with `moto`; they do not use an AWS account.

The authentication dependency intentionally returns `401` until Cognito is
implemented. API tests inject synthetic user subjects through FastAPI dependency
overrides. Do not add a request header that lets callers choose an owner.
```

- [ ] **Step 2: Update roadmap milestone 2 status**

Add `**Status: complete**` below the milestone 2 heading only after every Task 1–5 check is green. Do not mark milestones 3–10 complete.

- [ ] **Step 3: Run the full backend and frontend checks**

```bash
cd backend
uv sync --locked --dev
uv run ruff check .
uv run pytest -q

cd ../frontend
npm ci
npm audit
npm run lint
npm test
npm run typecheck
npm run build
```

Expected: all backend tests pass; Ruff is clean; npm reports zero vulnerabilities; frontend lint, test, typecheck, and build pass.

- [ ] **Step 4: Verify repository hygiene**

Run from the repository root:

```bash
git diff --check main..HEAD
git status --short
```

Expected: `git diff --check` prints nothing. Status contains only the two planned documentation files before commit, with no `.env`, AWS credential, real-email, `.venv`, `node_modules`, or `dist` files.

- [ ] **Step 5: Commit**

```bash
git add docs/DEVELOPMENT.md docs/IMPLEMENTATION_ROADMAP.md
git commit -m "docs: record application domain milestone"
```
