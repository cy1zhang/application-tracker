# Manual Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver roadmap milestone 3: a locally usable manual tracker where one user can create, edit, search, filter, sort, and inspect applications with their change history.

**Architecture:** The React app loads every page of the owner's applications through the existing `GET /applications` cursor API, then filters, searches, sorts, and counts in memory. A detail view reads one application plus its history events and edits it with optimistic-version `PATCH` requests. Locally, the backend authenticates every request as a fixed subject from the server-side `LOCAL_DEV_SUBJECT` variable, persists to DynamoDB Local in Docker, and is reached through a Vite dev-server proxy so no CORS configuration is needed yet.

**Tech Stack:** Python 3.12.13, FastAPI 0.141.1, boto3 1.43.101, moto 5.2.3, pytest 8, Ruff; React 19.3, TypeScript 6.0, Vite 7.3, Vitest 4.1, Testing Library; DynamoDB Local in Docker Compose.

**Spec:** [`docs/PRD.md`](../../PRD.md) §3 Dashboard and §4 Application model, [`docs/IMPLEMENTATION_ROADMAP.md`](../../IMPLEMENTATION_ROADMAP.md) milestone 3, and the decisions below.

## Decisions (agreed 2026-10-10)

- **Driver/reviewer:** Claude Code drives; Codex reviews the plan and the finished branch (`codex exec review -c sandbox_mode='"read-only"' --base main`).
- **Local identity:** `get_current_subject` returns `LOCAL_DEV_SUBJECT` when that server-side environment variable is set and the request comes from a loopback address (`127.0.0.1` or `::1`); otherwise `401` as before. It raises if set inside AWS Lambda (`AWS_LAMBDA_FUNCTION_NAME` present). Together these keep it from authenticating deployed or network-exposed traffic. Callers still cannot choose an owner. Milestone 4 replaces it with Cognito.
- **Filtering:** client-side. The loader follows `next_cursor` until it is `null`, because `Limit` is applied before the deleted-item filter and pages can be short or empty while more data remains. DynamoDB returns rows in random UUID order, so sorting is also client-side.
- **Local persistence:** DynamoDB Local in Docker Compose on `127.0.0.1:8001` with a named volume. boto3 reads `AWS_ENDPOINT_URL_DYNAMODB` natively, so application code does not change.
- **No CORS yet:** the Vite dev server proxies `/api/*` to `http://127.0.0.1:8000/*`, so browser and API share an origin. Milestone 4 adds CORS restricted to deployed origins.
- **Out of scope:** deleting applications from the UI (not in the roadmap's milestone 3 list), source email links (no Gmail yet), sync and review UI.

## Global Constraints

- Ownership comes only from the authenticated subject. Never read an owner from a header, query parameter, path, or body. `LOCAL_DEV_SUBJECT` is a server environment variable, honored only for loopback clients, and refused inside Lambda.
- Never invent values: creating requires company, role, and status, and status has no default. Blank optional fields are sent as `null` and displayed as "Unknown".
- The dashboard defaults to internships (PRD §2) and offers filters for type, season/year, status, and company/role search (PRD §3).
- Table rows show company, role, role type, cycle, status, application date when known, and last update (PRD §3).
- No new runtime dependencies: no router, data-fetching, or form library in the frontend; none in the backend. Tests use the existing Testing Library `fireEvent`.
- Tests use synthetic data only. `.env.example` contains only fake local values; real values belong in the git-ignored `.env`.
- Commit after each task on branch `milestone-3-manual-tracker` (see `AGENTS.md`).
- Runtime pins: Python 3.12.13, Node 22.23.3, npm 10.9.9.

## Review Focus

- A list page that is short or empty but still has a `next_cursor` must not end loading early; Task 3 tests an empty middle page.
- Records with `null` company, role type, season, year, or status (created by clearing fields) must render as "Unknown", match the "Unknown" filter options, not break search, and stay editable without forcing a value; Tasks 4, 5, and 6 test these.
- A stale edit (the record changed in another tab) must not overwrite silently or lose the user's input before they choose to reload; Task 6 tests the `409` path.
- An unreachable backend must show a readable message with a retry, not a blank page; Task 3 tests the network error and Task 7 tests retry.
- A saved form with no changes must not send an empty `PATCH`, which the API rejects with `422`; Tasks 5 and 6 test the no-op path.

---

### Task 1: Local development subject

**Files:**

- Modify: `backend/app/api/dependencies.py`
- Create: `backend/tests/test_dependencies.py`
- Modify: `backend/tests/test_applications_api.py`

**Interfaces:**

- Produces: `get_current_subject(request: Request) -> str` returns the stripped `LOCAL_DEV_SUBJECT` when it is set and non-blank and `request.client.host` is `127.0.0.1` or `::1`; raises `RuntimeError` when it is set while `AWS_LAMBDA_FUNCTION_NAME` is set; and otherwise raises `HTTPException(401, "Authentication required")`. Existing test overrides of this dependency keep working.

- [ ] **Step 1: Write failing dependency tests**

Create `backend/tests/test_dependencies.py`:

```python
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
```

- [ ] **Step 2: Add a failing API-level test and harden the existing 401 test**

In `backend/tests/test_applications_api.py`, change the existing test so a developer's shell variable cannot break it:

```python
def test_applications_require_authentication(
    client_without_subject, monkeypatch
) -> None:
    monkeypatch.delenv("LOCAL_DEV_SUBJECT", raising=False)
    response = client_without_subject.get("/applications")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
```

Append (the default `TestClient` reports its client host as `testclient`, so the loopback case needs its own client):

```python
def test_local_dev_subject_scopes_requests_to_that_owner(
    repository, monkeypatch
) -> None:
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    fastapi_app.dependency_overrides[get_application_repository] = lambda: repository
    try:
        with TestClient(fastapi_app, client=("127.0.0.1", 50000)) as client:
            monkeypatch.setenv("LOCAL_DEV_SUBJECT", "user-a")
            created = create_application(client)
            path = f"/applications/{created['application_id']}"

            assert client.get(path).status_code == 200

            monkeypatch.setenv("LOCAL_DEV_SUBJECT", "user-b")
            assert client.get(path).status_code == 404
    finally:
        fastapi_app.dependency_overrides.clear()


def test_local_dev_subject_is_ignored_for_non_loopback_clients(
    client_without_subject, monkeypatch
) -> None:
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)
    monkeypatch.setenv("LOCAL_DEV_SUBJECT", "user-a")

    assert client_without_subject.get("/applications").status_code == 401
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_dependencies.py tests/test_applications_api.py -q`
Expected: FAIL. The dependency tests fail with `TypeError` (the function takes no `request` yet), and `test_local_dev_subject_scopes_requests_to_that_owner` gets `401`.

- [ ] **Step 4: Implement the local subject**

In `backend/app/api/dependencies.py`, change the FastAPI import to `from fastapi import HTTPException, Request`, add the constant below the imports, and replace `get_current_subject`:

```python
LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def get_current_subject(request: Request) -> str:
    if subject := os.getenv("LOCAL_DEV_SUBJECT", "").strip():
        if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            raise RuntimeError("LOCAL_DEV_SUBJECT must not be set in AWS Lambda")
        if request.client is not None and request.client.host in LOOPBACK_HOSTS:
            return subject
    raise HTTPException(status_code=401, detail="Authentication required")
```

- [ ] **Step 5: Run the backend checks**

Run: `cd backend && uv run ruff check . && uv run pytest -q`
Expected: Ruff reports no issues; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/dependencies.py backend/tests/test_dependencies.py backend/tests/test_applications_api.py
git commit -m "feat: authenticate local development requests as a fixed subject"
```

---

### Task 2: DynamoDB Local and shared table definitions

**Files:**

- Create: `backend/app/repositories/dynamodb_tables.py`
- Create: `backend/tests/test_dynamodb_tables.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/scripts/create_local_tables.py`
- Create: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**

- Produces: `create_tables(client, *, applications_table: str, events_table: str) -> None`, idempotent, creating the Applications table (`owner_id` + `application_id`) and Events table (`owner_id` + `event_id`, GSI `application-events-index` on `owner_application_id` + `occurred_at_event_id`). Tests and the local script share it so the schemas cannot drift.

- [ ] **Step 1: Write the failing table test**

Create `backend/tests/test_dynamodb_tables.py`:

```python
import boto3
from moto import mock_aws

from app.repositories.dynamodb_tables import create_tables


def test_create_tables_is_idempotent() -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )

        create_tables(client, applications_table="Applications", events_table="Events")
        create_tables(client, applications_table="Applications", events_table="Events")

        assert sorted(client.list_tables()["TableNames"]) == ["Applications", "Events"]
        events = client.describe_table(TableName="Events")["Table"]
        assert [index["IndexName"] for index in events["GlobalSecondaryIndexes"]] == [
            "application-events-index"
        ]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/test_dynamodb_tables.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.repositories.dynamodb_tables'`.

- [ ] **Step 3: Implement the shared table definitions**

Create `backend/app/repositories/dynamodb_tables.py`:

```python
from botocore.exceptions import ClientError


def create_tables(client, *, applications_table: str, events_table: str) -> None:
    _create_if_missing(
        client,
        TableName=applications_table,
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
    _create_if_missing(
        client,
        TableName=events_table,
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


def _create_if_missing(client, **definition) -> None:
    try:
        client.create_table(**definition)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceInUseException":
            raise
```

- [ ] **Step 4: Run the table test**

Run: `cd backend && uv run pytest tests/test_dynamodb_tables.py -q`
Expected: PASS.

- [ ] **Step 5: Make the test fixtures use the shared definitions**

In `backend/tests/conftest.py`, delete `create_application_table` and `create_event_table`, add `from app.repositories.dynamodb_tables import create_tables` to the imports, and replace the two creation calls in the `repository` fixture:

```python
        create_tables(client, applications_table="Applications", events_table="Events")
```

Run: `cd backend && uv run ruff check . && uv run pytest -q`
Expected: all tests pass, showing the shared schema matches what the repository tests used before.

- [ ] **Step 6: Add the local table script**

Create `backend/scripts/create_local_tables.py`:

```python
"""Create the DynamoDB Local tables for development. Safe to run repeatedly."""

import os
from urllib.parse import urlparse

import boto3

from app.repositories.dynamodb_tables import create_tables

LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def main() -> None:
    endpoint = os.getenv("AWS_ENDPOINT_URL_DYNAMODB", "")
    if urlparse(endpoint).hostname not in LOCAL_HOSTS:
        raise SystemExit(
            "AWS_ENDPOINT_URL_DYNAMODB must point at DynamoDB Local on localhost "
            f"(got {endpoint!r}); refusing to create tables."
        )
    client = boto3.client(
        "dynamodb", region_name=os.environ["AWS_REGION"], endpoint_url=endpoint
    )
    create_tables(
        client,
        applications_table=os.environ["APPLICATIONS_TABLE"],
        events_table=os.environ["EVENTS_TABLE"],
    )
    print("Local DynamoDB tables are ready.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Add Docker Compose and local environment values**

Create `docker-compose.yml` at the repository root:

```yaml
services:
  dynamodb:
    image: amazon/dynamodb-local:2.5.2
    command: -jar DynamoDBLocal.jar -sharedDb -dbPath ./data
    working_dir: /home/dynamodblocal
    # Root lets DynamoDB Local write to the named volume it does not own.
    user: root
    ports:
      - "127.0.0.1:8001:8000"
    volumes:
      - dynamodb-data:/home/dynamodblocal/data

volumes:
  dynamodb-data:
```

Replace `.env.example` with:

```bash
# Local development values. Copy to .env (git-ignored): cp .env.example .env
# None of these are secrets: DynamoDB Local accepts any credentials.
# Keep real credentials only in .env, never in this file.
LOCAL_DEV_SUBJECT=local-dev-user
APPLICATIONS_TABLE=Applications
EVENTS_TABLE=Events
AWS_REGION=us-east-1
AWS_ENDPOINT_URL_DYNAMODB=http://127.0.0.1:8001
AWS_ACCESS_KEY_ID=local
AWS_SECRET_ACCESS_KEY=local
```

- [ ] **Step 8: Verify against DynamoDB Local**

Docker Desktop must be running. If the `2.5.2` tag cannot be pulled, pin the newest `2.x` tag listed on Docker Hub instead and note it in the commit message.

```bash
cp .env.example .env
docker compose up -d dynamodb
cd backend
uv run --env-file ../.env python -m scripts.create_local_tables
uv run --env-file ../.env python -m scripts.create_local_tables
uv run --env-file ../.env uvicorn app.main:app --port 8000 &
sleep 3
curl -s -X POST http://127.0.0.1:8000/applications \
  -H 'Content-Type: application/json' \
  -d '{"company":"Acme","role":"Software Engineer Intern","status":"applied"}'
curl -s http://127.0.0.1:8000/applications
kill %1
```

Also confirm the script refuses a non-local endpoint, including when a shell variable overrides `.env`:

```bash
AWS_ENDPOINT_URL_DYNAMODB=https://dynamodb.us-east-1.amazonaws.com \
  uv run --env-file ../.env python -m scripts.create_local_tables
```

Expected: this exits with the "must point at DynamoDB Local on localhost" message. Both earlier script runs print `Local DynamoDB tables are ready.`; the `POST` returns the created application with `"version":1`; the `GET` lists it. Restart the container with `docker compose restart dynamodb` and repeat the `GET` (with the API running) to confirm the data persisted. Then delete the synthetic record's data with `docker compose down -v` and run `docker compose up -d dynamodb` plus the table script again.

- [ ] **Step 9: Commit**

```bash
git add backend/app/repositories/dynamodb_tables.py backend/tests/test_dynamodb_tables.py backend/tests/conftest.py backend/scripts/create_local_tables.py docker-compose.yml .env.example
git commit -m "feat: run the API locally against DynamoDB Local"
```

---

### Task 3: Frontend API types, client, and dev proxy

**Files:**

- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/client.test.ts`
- Create: `frontend/src/test/fixtures.ts`
- Modify: `frontend/vite.config.ts`

**Interfaces:**

- Produces types: `RoleType`, `Season`, `ApplicationStatus`, `EventType`, `EventOrigin`, `FieldValue`, `Application`, `ApplicationPage`, `ApplicationEvent`, `ApplicationCreate`, `ApplicationValues`, `ApplicationPatch`.
- Produces functions: `listAllApplications(): Promise<Application[]>`, `getApplication(id: string): Promise<Application>`, `listApplicationEvents(id: string): Promise<ApplicationEvent[]>`, `createApplication(payload: ApplicationCreate): Promise<Application>`, `updateApplication(id: string, patch: ApplicationPatch): Promise<Application>`.
- Produces `class ApiError extends Error { status: number }`. `status` is `0` when the server cannot be reached; `message` is the API's string `detail` or a generic fallback.
- Produces test helper `makeApplication(overrides?: Partial<Application>): Application`.

- [ ] **Step 1: Add the shared types**

Create `frontend/src/api/types.ts`:

```ts
export type RoleType =
  | "internship"
  | "co_op"
  | "full_time_new_grad"
  | "unknown";
export type Season = "summer" | "fall" | "spring" | "winter";
export type ApplicationStatus =
  | "applied"
  | "oa_received"
  | "interviewing"
  | "offer"
  | "rejected"
  | "withdrawn";
export type EventType = "created" | "updated" | "deleted";
export type EventOrigin = "user" | "rules" | "ai";
export type FieldValue = string | number | null;

export interface Application {
  application_id: string;
  company: string | null;
  role: string | null;
  role_type: RoleType | null;
  season: Season | null;
  year: number | null;
  status: ApplicationStatus | null;
  application_date: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ApplicationPage {
  items: Application[];
  next_cursor: string | null;
}

export interface ApplicationEvent {
  event_id: string;
  application_id: string;
  event_type: EventType;
  origin: EventOrigin;
  occurred_at: string;
  changes: Record<string, { before: FieldValue; after: FieldValue }>;
}

export interface ApplicationCreate {
  company: string;
  role: string;
  role_type: RoleType;
  season: Season | null;
  year: number | null;
  status: ApplicationStatus;
  application_date: string | null;
}

// What the form produces. Creating requires company, role, and status; editing
// may leave them null when they were already unknown.
export interface ApplicationValues {
  company: string | null;
  role: string | null;
  role_type: RoleType;
  season: Season | null;
  year: number | null;
  status: ApplicationStatus | null;
  application_date: string | null;
}

export type ApplicationPatch = Partial<ApplicationValues> & {
  expected_version: number;
};
```

Create `frontend/src/test/fixtures.ts`:

```ts
import type { Application } from "../api/types";

export function makeApplication(
  overrides: Partial<Application> = {},
): Application {
  return {
    application_id: "app-1",
    company: "Acme",
    role: "Software Engineer Intern",
    role_type: "internship",
    season: "summer",
    year: 2027,
    status: "applied",
    application_date: "2026-09-20",
    created_at: "2026-09-20T12:00:00+00:00",
    updated_at: "2026-09-20T12:00:00+00:00",
    version: 1,
    ...overrides,
  };
}
```

- [ ] **Step 2: Write failing client tests**

Create `frontend/src/api/client.test.ts`:

```ts
import { afterEach, expect, test, vi } from "vitest";

import { makeApplication } from "../test/fixtures";
import {
  ApiError,
  createApplication,
  listAllApplications,
  updateApplication,
} from "./client";

function fakeResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("follows cursors through short and empty pages", async () => {
  const first = makeApplication({ application_id: "app-1" });
  const second = makeApplication({ application_id: "app-2" });
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(fakeResponse({ items: [first], next_cursor: "c1" }))
    .mockResolvedValueOnce(fakeResponse({ items: [], next_cursor: "c2" }))
    .mockResolvedValueOnce(fakeResponse({ items: [second], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listAllApplications()).resolves.toEqual([first, second]);
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    "/api/applications?limit=100",
    "/api/applications?limit=100&cursor=c1",
    "/api/applications?limit=100&cursor=c2",
  ]);
});

test("creates an application with a JSON body", async () => {
  const created = makeApplication();
  const fetchMock = vi.fn().mockResolvedValue(fakeResponse(created, 201));
  vi.stubGlobal("fetch", fetchMock);
  const payload = {
    company: "Acme",
    role: "Software Engineer Intern",
    role_type: "internship" as const,
    season: null,
    year: null,
    status: "applied" as const,
    application_date: null,
  };

  await expect(createApplication(payload)).resolves.toEqual(created);
  expect(fetchMock).toHaveBeenCalledWith("/api/applications", {
    method: "POST",
    body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" },
  });
});

test("reports the API detail for a stale update", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        fakeResponse({ detail: "Application version is stale" }, 409),
      ),
  );

  const error = await updateApplication("app-1", {
    expected_version: 1,
    status: "offer",
  }).catch((caught: unknown) => caught);

  expect(error).toBeInstanceOf(ApiError);
  expect(error).toMatchObject({
    status: 409,
    message: "Application version is stale",
  });
});

test("falls back to a generic message for structured validation errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(fakeResponse({ detail: [{ msg: "bad year" }] }, 422)),
  );

  await expect(listAllApplications()).rejects.toMatchObject({
    status: 422,
    message: "Request failed with status 422",
  });
});

test("explains when the server cannot be reached", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );

  await expect(listAllApplications()).rejects.toMatchObject({
    status: 0,
    message: "Could not reach the server. Is the backend running?",
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/api/client.test.ts`
Expected: FAIL because `./client` does not exist.

- [ ] **Step 4: Implement the client**

Create `frontend/src/api/client.ts`:

```ts
import type {
  Application,
  ApplicationCreate,
  ApplicationEvent,
  ApplicationPage,
  ApplicationPatch,
} from "./types";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }
  if (!response.ok) {
    throw new ApiError(response.status, await errorMessage(response));
  }
  return (await response.json()) as T;
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      return body.detail;
    }
  } catch {
    // The error body was not JSON; use the generic message.
  }
  return `Request failed with status ${response.status}`;
}

export async function listAllApplications(): Promise<Application[]> {
  const applications: Application[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ limit: "100" });
    if (cursor !== null) {
      query.set("cursor", cursor);
    }
    const page = await request<ApplicationPage>(`/applications?${query}`);
    applications.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor !== null);
  return applications;
}

export function getApplication(id: string): Promise<Application> {
  return request<Application>(`/applications/${encodeURIComponent(id)}`);
}

export function listApplicationEvents(id: string): Promise<ApplicationEvent[]> {
  return request<ApplicationEvent[]>(
    `/applications/${encodeURIComponent(id)}/events`,
  );
}

export function createApplication(
  payload: ApplicationCreate,
): Promise<Application> {
  return request<Application>("/applications", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateApplication(
  id: string,
  patch: ApplicationPatch,
): Promise<Application> {
  return request<Application>(`/applications/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}
```

- [ ] **Step 5: Add the dev proxy**

Replace `frontend/vite.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
        // Vite answers a failed proxy request with an empty 500; answer with a
        // 502 and an API-style detail so the UI can explain the backend is down.
        configure: (proxy) => {
          proxy.on("error", (_error, _request, response) => {
            if ("writeHead" in response && !response.headersSent) {
              response.writeHead(502, { "Content-Type": "application/json" });
              response.end(
                JSON.stringify({
                  detail: "Could not reach the server. Is the backend running?",
                }),
              );
            }
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm run lint && npm test && npm run typecheck`
Expected: all pass.

- [ ] **Step 7: Verify the proxy's backend-down response**

With the API **not** running:

```bash
cd frontend
npx vite --port 5173 --strictPort &
sleep 3
curl -s -i http://127.0.0.1:5173/api/health
kill %1
```

Expected: `HTTP/1.1 502` with body `{"detail":"Could not reach the server. Is the backend running?"}`. If Vite still returns an empty `500`, its own error handler ran first: move the handler's logic into a small Vite plugin or adjust until the 502 appears, and record what changed in the commit message.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api frontend/src/test/fixtures.ts frontend/vite.config.ts
git commit -m "feat: add typed frontend API client"
```

---

### Task 4: Display labels, filtering, and sorting

**Files:**

- Create: `frontend/src/applications/labels.ts`
- Create: `frontend/src/applications/labels.test.ts`
- Create: `frontend/src/applications/filtering.ts`
- Create: `frontend/src/applications/filtering.test.ts`

**Interfaces:**

- Consumes: types from `src/api/types.ts`; `makeApplication` from `src/test/fixtures.ts`.
- Produces from `labels.ts`: `ROLE_TYPE_LABELS: Record<RoleType, string>`, `SEASON_LABELS: Record<Season, string>`, `STATUS_LABELS: Record<ApplicationStatus, string>`, `FIELD_LABELS: Record<string, string>`, `UNKNOWN = "Unknown"`, `formatCycle(season, year): string`, `formatFieldValue(field: string, value: FieldValue): string`, `formatTimestamp(iso: string): string`.
- Produces from `filtering.ts`: `ALL = "all"`, `UNKNOWN_VALUE = "unknown"`, `interface Filters { roleType; season; year: string; status; search: string }`, `DEFAULT_FILTERS` (internships), `type SortKey = "updated" | "application_date" | "company"`, `filterApplications(applications, filters)`, `sortApplications(applications, key)`, `countByStatus(applications): Record<ApplicationStatus, number>`, `yearOptions(applications): number[]`.

- [ ] **Step 1: Write failing label tests**

Create `frontend/src/applications/labels.test.ts`:

```ts
import { expect, test } from "vitest";

import { formatCycle, formatFieldValue } from "./labels";

test("formats every combination of known and unknown cycle parts", () => {
  expect(formatCycle("summer", 2027)).toBe("Summer 2027");
  expect(formatCycle("summer", null)).toBe("Summer (year unknown)");
  expect(formatCycle(null, 2027)).toBe("2027 (season unknown)");
  expect(formatCycle(null, null)).toBe("Unknown");
});

test("formats history values with display labels", () => {
  expect(formatFieldValue("status", "oa_received")).toBe("OA received");
  expect(formatFieldValue("role_type", "co_op")).toBe("Co-op");
  expect(formatFieldValue("season", null)).toBe("Unknown");
  expect(formatFieldValue("year", 2027)).toBe("2027");
  expect(formatFieldValue("company", "Acme")).toBe("Acme");
});
```

- [ ] **Step 2: Write failing filtering tests**

Create `frontend/src/applications/filtering.test.ts`:

```ts
import { expect, test } from "vitest";

import type { Application } from "../api/types";
import { makeApplication } from "../test/fixtures";
import {
  DEFAULT_FILTERS,
  countByStatus,
  filterApplications,
  sortApplications,
  yearOptions,
  type Filters,
} from "./filtering";

const ALL_TYPES: Filters = { ...DEFAULT_FILTERS, roleType: "all" };

function ids(applications: Application[]): string[] {
  return applications.map((application) => application.application_id);
}

test("defaults to internships only", () => {
  const applications = [
    makeApplication({ application_id: "intern" }),
    makeApplication({ application_id: "coop", role_type: "co_op" }),
    makeApplication({ application_id: "missing", role_type: null }),
  ];

  expect(ids(filterApplications(applications, DEFAULT_FILTERS))).toEqual([
    "intern",
  ]);
});

test("unknown role type matches explicit unknown and missing values", () => {
  const applications = [
    makeApplication({ application_id: "explicit", role_type: "unknown" }),
    makeApplication({ application_id: "missing", role_type: null }),
    makeApplication({ application_id: "intern" }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, roleType: "unknown" })),
  ).toEqual(["explicit", "missing"]);
});

test("unknown season and year match missing values", () => {
  const applications = [
    makeApplication({ application_id: "known" }),
    makeApplication({ application_id: "no-season", season: null }),
    makeApplication({ application_id: "no-year", year: null }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, season: "unknown" })),
  ).toEqual(["no-season"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, year: "unknown" })),
  ).toEqual(["no-year"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, year: "2027" })),
  ).toEqual(["known", "no-season"]);
});

test("filters by status, including unknown status", () => {
  const applications = [
    makeApplication({ application_id: "applied" }),
    makeApplication({ application_id: "offer", status: "offer" }),
    makeApplication({ application_id: "missing", status: null }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, status: "offer" })),
  ).toEqual(["offer"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, status: "unknown" })),
  ).toEqual(["missing"]);
});

test("search is trimmed, case-insensitive, and covers company and role", () => {
  const applications = [
    makeApplication({ application_id: "a", company: "Acme", role: "Data Intern" }),
    makeApplication({
      application_id: "b",
      company: "Globex",
      role: "Platform Engineer",
    }),
    makeApplication({ application_id: "c", company: null, role: "Acme Labs" }),
  ];

  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, search: "  ACME " })),
  ).toEqual(["a", "c"]);
  expect(
    ids(filterApplications(applications, { ...ALL_TYPES, search: "platform" })),
  ).toEqual(["b"]);
});

test("sorts by last update, newest first", () => {
  const applications = [
    makeApplication({ application_id: "old", updated_at: "2026-09-01T00:00:00+00:00" }),
    makeApplication({
      application_id: "new",
      updated_at: "2026-09-02T00:00:00.500000+00:00",
    }),
  ];

  expect(ids(sortApplications(applications, "updated"))).toEqual(["new", "old"]);
});

test("sorts by application date, newest first, with unknown dates last", () => {
  const applications = [
    makeApplication({ application_id: "early", application_date: "2026-09-01" }),
    makeApplication({ application_id: "unknown", application_date: null }),
    makeApplication({ application_id: "late", application_date: "2026-09-15" }),
  ];

  expect(ids(sortApplications(applications, "application_date"))).toEqual([
    "late",
    "early",
    "unknown",
  ]);
});

test("sorts by company case-insensitively with unknown companies last", () => {
  const applications = [
    makeApplication({ application_id: "beta", company: "beta" }),
    makeApplication({ application_id: "unknown", company: null }),
    makeApplication({ application_id: "alpha", company: "Alpha" }),
  ];

  expect(ids(sortApplications(applications, "company"))).toEqual([
    "alpha",
    "beta",
    "unknown",
  ]);
});

test("counts every status, including zero counts", () => {
  const applications = [
    makeApplication(),
    makeApplication(),
    makeApplication({ status: "offer" }),
    makeApplication({ status: null }),
  ];

  expect(countByStatus(applications)).toEqual({
    applied: 2,
    oa_received: 0,
    interviewing: 0,
    offer: 1,
    rejected: 0,
    withdrawn: 0,
  });
});

test("lists distinct known years, newest first", () => {
  const applications = [
    makeApplication({ year: 2026 }),
    makeApplication({ year: null }),
    makeApplication({ year: 2027 }),
    makeApplication({ year: 2026 }),
  ];

  expect(yearOptions(applications)).toEqual([2027, 2026]);
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/applications`
Expected: FAIL because `./labels` and `./filtering` do not exist.

- [ ] **Step 4: Implement the labels**

Create `frontend/src/applications/labels.ts`:

```ts
import type {
  ApplicationStatus,
  FieldValue,
  RoleType,
  Season,
} from "../api/types";

export const UNKNOWN = "Unknown";

export const ROLE_TYPE_LABELS: Record<RoleType, string> = {
  internship: "Internship",
  co_op: "Co-op",
  full_time_new_grad: "Full-time / new grad",
  unknown: UNKNOWN,
};

export const SEASON_LABELS: Record<Season, string> = {
  summer: "Summer",
  fall: "Fall",
  spring: "Spring",
  winter: "Winter",
};

export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: "Applied",
  oa_received: "OA received",
  interviewing: "Interviewing",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

export const FIELD_LABELS: Record<string, string> = {
  company: "Company",
  role: "Role",
  role_type: "Role type",
  season: "Season",
  year: "Year",
  status: "Status",
  application_date: "Application date",
};

export function formatCycle(season: Season | null, year: number | null): string {
  if (season !== null && year !== null) {
    return `${SEASON_LABELS[season]} ${year}`;
  }
  if (season !== null) {
    return `${SEASON_LABELS[season]} (year unknown)`;
  }
  if (year !== null) {
    return `${year} (season unknown)`;
  }
  return UNKNOWN;
}

const VALUE_LABELS: Partial<Record<string, Record<string, string>>> = {
  role_type: ROLE_TYPE_LABELS,
  season: SEASON_LABELS,
  status: STATUS_LABELS,
};

export function formatFieldValue(field: string, value: FieldValue): string {
  if (value === null) {
    return UNKNOWN;
  }
  return VALUE_LABELS[field]?.[String(value)] ?? String(value);
}

const timestampFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatTimestamp(iso: string): string {
  return timestampFormat.format(new Date(iso));
}
```

- [ ] **Step 5: Implement filtering and sorting**

Create `frontend/src/applications/filtering.ts`:

```ts
import type {
  Application,
  ApplicationStatus,
  RoleType,
  Season,
} from "../api/types";

export const ALL = "all";
export const UNKNOWN_VALUE = "unknown";

export interface Filters {
  roleType: RoleType | typeof ALL;
  season: Season | typeof UNKNOWN_VALUE | typeof ALL;
  year: string;
  status: ApplicationStatus | typeof UNKNOWN_VALUE | typeof ALL;
  search: string;
}

export const DEFAULT_FILTERS: Filters = {
  roleType: "internship",
  season: ALL,
  year: ALL,
  status: ALL,
  search: "",
};

export type SortKey = "updated" | "application_date" | "company";

export function filterApplications(
  applications: Application[],
  filters: Filters,
): Application[] {
  const search = filters.search.trim().toLocaleLowerCase();
  return applications.filter((application) => {
    const roleType = application.role_type ?? "unknown";
    const season = application.season ?? UNKNOWN_VALUE;
    const year = application.year === null ? UNKNOWN_VALUE : String(application.year);
    const status = application.status ?? UNKNOWN_VALUE;
    if (filters.roleType !== ALL && roleType !== filters.roleType) return false;
    if (filters.season !== ALL && season !== filters.season) return false;
    if (filters.year !== ALL && year !== filters.year) return false;
    if (filters.status !== ALL && status !== filters.status) return false;
    if (search) {
      return [application.company, application.role].some((value) =>
        value?.toLocaleLowerCase().includes(search),
      );
    }
    return true;
  });
}

export function sortApplications(
  applications: Application[],
  key: SortKey,
): Application[] {
  return [...applications].sort((a, b) => {
    switch (key) {
      case "updated":
        return Date.parse(b.updated_at) - Date.parse(a.updated_at);
      case "application_date":
        return compareNullsLast(a.application_date, b.application_date, (x, y) =>
          y.localeCompare(x),
        );
      case "company":
        return compareNullsLast(a.company, b.company, (x, y) =>
          x.localeCompare(y, undefined, { sensitivity: "base" }),
        );
    }
  });
}

function compareNullsLast(
  a: string | null,
  b: string | null,
  compareValues: (a: string, b: string) => number,
): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return compareValues(a, b);
}

export function countByStatus(
  applications: Application[],
): Record<ApplicationStatus, number> {
  const counts: Record<ApplicationStatus, number> = {
    applied: 0,
    oa_received: 0,
    interviewing: 0,
    offer: 0,
    rejected: 0,
    withdrawn: 0,
  };
  for (const application of applications) {
    if (application.status !== null) {
      counts[application.status] += 1;
    }
  }
  return counts;
}

export function yearOptions(applications: Application[]): number[] {
  const years = new Set<number>();
  for (const application of applications) {
    if (application.year !== null) {
      years.add(application.year);
    }
  }
  return [...years].sort((a, b) => b - a);
}
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm run lint && npm test && npm run typecheck`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/applications
git commit -m "feat: add application labels, filtering, and sorting"
```

---

### Task 5: Application form

**Files:**

- Create: `frontend/src/applications/ApplicationForm.tsx`
- Create: `frontend/src/applications/ApplicationForm.test.tsx`
- Modify: `frontend/src/test/setup.ts`

**Interfaces:**

- Consumes: `ApiError` from `src/api/client.ts`; labels from `labels.ts`.
- Produces: `ApplicationForm({ initial?: Application; submitLabel: string; onSubmit: (values: ApplicationValues) => Promise<void>; onCancel: () => void })`. Edit mode when `initial` is given. Creating requires company, role, and status, so in create mode the values are always a valid `ApplicationCreate`. Editing allows those fields to stay blank (`null`) only when they were already unknown on `initial`; a known value cannot be blanked. Shows validation errors and the `message` of an `ApiError` thrown by `onSubmit`; errors that the caller handles itself must not be rethrown.
- Produces: `buildPatch(original: Application, values: ApplicationValues): ApplicationPatch | null`, returning only changed fields plus `expected_version`, or `null` when nothing changed. A stored `role_type` of `null` counts as equal to `"unknown"`.
- Form controls are labelled exactly: "Company", "Role", "Role type", "Season", "Year", "Status", "Application date".

- [ ] **Step 1: Clean up the DOM between component tests**

Vitest globals are off, so Testing Library cannot register its automatic cleanup. Replace `frontend/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
```

- [ ] **Step 2: Write failing form tests**

Create `frontend/src/applications/ApplicationForm.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { ApiError } from "../api/client";
import type { ApplicationValues } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { ApplicationForm, buildPatch } from "./ApplicationForm";

function renderCreateForm(
  onSubmit = vi.fn<(values: ApplicationValues) => Promise<void>>(),
) {
  onSubmit.mockResolvedValue(undefined);
  render(
    <ApplicationForm
      submitLabel="Create application"
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  return onSubmit;
}

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

function submit() {
  fireEvent.click(screen.getByRole("button", { name: "Create application" }));
}

const UNCHANGED: ApplicationValues = {
  company: "Acme",
  role: "Software Engineer Intern",
  role_type: "internship",
  season: "summer",
  year: 2027,
  status: "applied",
  application_date: "2026-09-20",
};

test("does not default the status", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent("Choose a status.");
  expect(onSubmit).not.toHaveBeenCalled();
});

test("submits trimmed values and leaves blank optional fields unknown", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "  Acme  ");
  fill("Role", " Software Engineer Intern ");
  fill("Status", "applied");
  submit();

  await waitFor(() =>
    expect(onSubmit).toHaveBeenCalledWith({
      company: "Acme",
      role: "Software Engineer Intern",
      role_type: "unknown",
      season: null,
      year: null,
      status: "applied",
      application_date: null,
    }),
  );
});

test("submits every field when provided", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  fill("Role type", "internship");
  fill("Season", "summer");
  fill("Year", "2027");
  fill("Status", "applied");
  fill("Application date", "2026-09-20");
  submit();

  await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(UNCHANGED));
});

test("rejects a blank company", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "   ");
  fill("Role", "Software Engineer Intern");
  fill("Status", "applied");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a company.");
  expect(onSubmit).not.toHaveBeenCalled();
});

test("rejects a fractional year", async () => {
  const onSubmit = renderCreateForm();
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  fill("Status", "applied");
  fill("Year", "2027.5");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Year must be a whole number.",
  );
  expect(onSubmit).not.toHaveBeenCalled();
});

test("shows the API error when saving fails", async () => {
  const onSubmit = vi.fn<(values: ApplicationValues) => Promise<void>>();
  render(
    <ApplicationForm
      submitLabel="Create application"
      onSubmit={onSubmit.mockRejectedValue(
        new ApiError(0, "Could not reach the server. Is the backend running?"),
      )}
      onCancel={vi.fn()}
    />,
  );
  fill("Company", "Acme");
  fill("Role", "Software Engineer Intern");
  fill("Status", "applied");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not reach the server. Is the backend running?",
  );
});

test("edit mode starts from the current values", () => {
  render(
    <ApplicationForm
      initial={makeApplication({ season: null })}
      submitLabel="Save changes"
      onSubmit={vi.fn()}
      onCancel={vi.fn()}
    />,
  );

  expect(screen.getByLabelText("Company")).toHaveValue("Acme");
  expect(screen.getByLabelText("Season")).toHaveValue("");
  expect(screen.getByLabelText("Year")).toHaveValue(2027);
  expect(screen.getByLabelText("Status")).toHaveValue("applied");
});

test("edit mode keeps fields that were already unknown", async () => {
  const onSubmit = vi
    .fn<(values: ApplicationValues) => Promise<void>>()
    .mockResolvedValue(undefined);
  render(
    <ApplicationForm
      initial={makeApplication({ company: null, status: null })}
      submitLabel="Save changes"
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  fill("Year", "2028");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() =>
    expect(onSubmit).toHaveBeenCalledWith({
      ...UNCHANGED,
      company: null,
      status: null,
      year: 2028,
    }),
  );
});

test("edit mode does not allow blanking a known company", async () => {
  const onSubmit = vi.fn<(values: ApplicationValues) => Promise<void>>();
  render(
    <ApplicationForm
      initial={makeApplication()}
      submitLabel="Save changes"
      onSubmit={onSubmit}
      onCancel={vi.fn()}
    />,
  );
  fill("Company", "  ");
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Enter a company.");
  expect(onSubmit).not.toHaveBeenCalled();
});

test("buildPatch sends only changed fields with the expected version", () => {
  const original = makeApplication({ version: 3 });

  expect(
    buildPatch(original, { ...UNCHANGED, season: null, status: "interviewing" }),
  ).toEqual({ expected_version: 3, season: null, status: "interviewing" });
});

test("buildPatch returns null when nothing changed", () => {
  expect(buildPatch(makeApplication(), UNCHANGED)).toBeNull();
  expect(
    buildPatch(makeApplication({ role_type: null }), {
      ...UNCHANGED,
      role_type: "unknown",
    }),
  ).toBeNull();
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/applications/ApplicationForm.test.tsx`
Expected: FAIL because `./ApplicationForm` does not exist.

- [ ] **Step 4: Implement the form**

Create `frontend/src/applications/ApplicationForm.tsx`:

```tsx
import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import type {
  Application,
  ApplicationPatch,
  ApplicationStatus,
  ApplicationValues,
  RoleType,
  Season,
} from "../api/types";
import { ROLE_TYPE_LABELS, SEASON_LABELS, STATUS_LABELS, UNKNOWN } from "./labels";

interface FormState {
  company: string;
  role: string;
  roleType: RoleType;
  season: Season | "";
  year: string;
  status: ApplicationStatus | "";
  applicationDate: string;
}

const EMPTY_FORM: FormState = {
  company: "",
  role: "",
  roleType: "unknown",
  season: "",
  year: "",
  status: "",
  applicationDate: "",
};

const PATCH_FIELDS = [
  "company",
  "role",
  "role_type",
  "season",
  "year",
  "status",
  "application_date",
] as const;

function valuesFromApplication(application: Application): FormState {
  return {
    company: application.company ?? "",
    role: application.role ?? "",
    roleType: application.role_type ?? "unknown",
    season: application.season ?? "",
    year: application.year === null ? "" : String(application.year),
    status: application.status ?? "",
    applicationDate: application.application_date ?? "",
  };
}

// Company, role, and status are required, except that editing may leave one
// blank when it was already unknown on the stored record.
function parseForm(
  state: FormState,
  initial: Application | undefined,
): ApplicationValues | string {
  const company = state.company.trim() || null;
  const role = state.role.trim() || null;
  const status = state.status || null;
  if (company === null && (initial === undefined || initial.company !== null)) {
    return "Enter a company.";
  }
  if (role === null && (initial === undefined || initial.role !== null)) {
    return "Enter a role.";
  }
  if (status === null && (initial === undefined || initial.status !== null)) {
    return "Choose a status.";
  }
  const year = state.year.trim() === "" ? null : Number(state.year);
  if (year !== null && !Number.isInteger(year)) {
    return "Year must be a whole number.";
  }
  return {
    company,
    role,
    role_type: state.roleType,
    season: state.season || null,
    year,
    status,
    application_date: state.applicationDate || null,
  };
}

export function buildPatch(
  original: Application,
  values: ApplicationValues,
): ApplicationPatch | null {
  const comparable = { ...original, role_type: original.role_type ?? "unknown" };
  const changes = Object.fromEntries(
    PATCH_FIELDS.filter((field) => values[field] !== comparable[field]).map(
      (field) => [field, values[field]],
    ),
  ) as Partial<ApplicationValues>;
  if (Object.keys(changes).length === 0) {
    return null;
  }
  return { expected_version: original.version, ...changes };
}

interface ApplicationFormProps {
  initial?: Application;
  submitLabel: string;
  onSubmit: (values: ApplicationValues) => Promise<void>;
  onCancel: () => void;
}

export function ApplicationForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: ApplicationFormProps) {
  const [state, setState] = useState<FormState>(() =>
    initial ? valuesFromApplication(initial) : EMPTY_FORM,
  );
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update<K extends keyof FormState>(field: K, value: FormState[K]) {
    setState((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const parsed = parseForm(state, initial);
    if (typeof parsed === "string") {
      setError(parsed);
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(parsed);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Could not save the application.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="page" onSubmit={handleSubmit} noValidate>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="application-company">Company</label>
          <input
            id="application-company"
            value={state.company}
            maxLength={200}
            onChange={(event) => update("company", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="application-role">Role</label>
          <input
            id="application-role"
            value={state.role}
            maxLength={200}
            onChange={(event) => update("role", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="application-role-type">Role type</label>
          <select
            id="application-role-type"
            value={state.roleType}
            onChange={(event) =>
              update("roleType", event.target.value as RoleType)
            }
          >
            {(Object.keys(ROLE_TYPE_LABELS) as RoleType[]).map((roleType) => (
              <option key={roleType} value={roleType}>
                {ROLE_TYPE_LABELS[roleType]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="application-season">Season</label>
          <select
            id="application-season"
            value={state.season}
            onChange={(event) =>
              update("season", event.target.value as Season | "")
            }
          >
            <option value="">{UNKNOWN}</option>
            {(Object.keys(SEASON_LABELS) as Season[]).map((season) => (
              <option key={season} value={season}>
                {SEASON_LABELS[season]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="application-year">Year</label>
          <input
            id="application-year"
            type="number"
            step={1}
            inputMode="numeric"
            value={state.year}
            onChange={(event) => update("year", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="application-status">Status</label>
          <select
            id="application-status"
            value={state.status}
            onChange={(event) =>
              update("status", event.target.value as ApplicationStatus | "")
            }
          >
            <option value="">Select a status</option>
            {(Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="application-date">Application date</label>
          <input
            id="application-date"
            type="date"
            value={state.applicationDate}
            onChange={(event) => update("applicationDate", event.target.value)}
          />
        </div>
      </div>
      {error !== null && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" className="primary" disabled={submitting}>
          {submitLabel}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
```

- [ ] **Step 5: Run the frontend checks**

Run: `cd frontend && npm run lint && npm test && npm run typecheck`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/applications/ApplicationForm.tsx frontend/src/applications/ApplicationForm.test.tsx frontend/src/test/setup.ts
git commit -m "feat: add application create and edit form"
```

---

### Task 6: Application detail with history and editing

**Files:**

- Create: `frontend/src/applications/ApplicationDetail.tsx`
- Create: `frontend/src/applications/ApplicationDetail.test.tsx`

**Interfaces:**

- Consumes: `getApplication`, `listApplicationEvents`, `updateApplication`, `ApiError`; `ApplicationForm` and `buildPatch`; labels.
- Produces: `ApplicationDetail({ applicationId: string; onBack: () => void })`. Heading is `"{company} · {role}"`. Buttons: "← Back to applications", "Edit", "Reload latest" (after a `409`), "Retry" (after a load error). History is an `<ol>` labelled "History"; created events render `"{Field}: {value}"`, other events `"{Field}: {before} → {after}"`, skipping unchanged fields.

- [ ] **Step 1: Write failing detail tests**

Create `frontend/src/applications/ApplicationDetail.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  ApiError,
  getApplication,
  listApplicationEvents,
  updateApplication,
} from "../api/client";
import type { ApplicationEvent } from "../api/types";
import { makeApplication } from "../test/fixtures";
import { ApplicationDetail } from "./ApplicationDetail";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn(),
    updateApplication: vi.fn(),
  };
});

const created: ApplicationEvent = {
  event_id: "event-1",
  application_id: "app-1",
  event_type: "created",
  origin: "user",
  occurred_at: "2026-09-20T12:00:00+00:00",
  changes: {
    company: { before: null, after: "Acme" },
    status: { before: null, after: "applied" },
    season: { before: null, after: null },
  },
};

const updated: ApplicationEvent = {
  event_id: "event-2",
  application_id: "app-1",
  event_type: "updated",
  origin: "user",
  occurred_at: "2026-09-21T12:00:00+00:00",
  changes: { status: { before: "applied", after: "interviewing" } },
};

afterEach(() => {
  vi.resetAllMocks();
});

function renderDetail() {
  render(<ApplicationDetail applicationId="app-1" onBack={vi.fn()} />);
}

async function startEditingStatus(value: string) {
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
}

test("shows fields, unknown values, and history", async () => {
  vi.mocked(getApplication).mockResolvedValue(makeApplication({ season: null }));
  vi.mocked(listApplicationEvents).mockResolvedValue([created, updated]);
  renderDetail();

  expect(
    await screen.findByRole("heading", { name: "Acme · Software Engineer Intern" }),
  ).toBeInTheDocument();
  expect(screen.getByText("2027 (season unknown)")).toBeInTheDocument();
  expect(screen.getByText("Company: Acme")).toBeInTheDocument();
  expect(screen.getByText("Status: Applied → Interviewing")).toBeInTheDocument();
  expect(screen.queryByText("Season: Unknown")).not.toBeInTheDocument();
  expect(screen.getByText(/^Created by you/)).toBeInTheDocument();
});

test("saves only changed fields and shows the new history", async () => {
  vi.mocked(getApplication)
    .mockResolvedValueOnce(makeApplication())
    .mockResolvedValueOnce(makeApplication({ status: "interviewing", version: 2 }));
  vi.mocked(listApplicationEvents)
    .mockResolvedValueOnce([created])
    .mockResolvedValueOnce([created, updated]);
  vi.mocked(updateApplication).mockResolvedValue(
    makeApplication({ status: "interviewing", version: 2 }),
  );
  renderDetail();

  await startEditingStatus("interviewing");

  await waitFor(() =>
    expect(updateApplication).toHaveBeenCalledWith("app-1", {
      expected_version: 1,
      status: "interviewing",
    }),
  );
  expect(
    await screen.findByText("Status: Applied → Interviewing"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("saving without changes does not call the API", async () => {
  vi.mocked(getApplication).mockResolvedValue(makeApplication());
  vi.mocked(listApplicationEvents).mockResolvedValue([created]);
  renderDetail();

  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(updateApplication).not.toHaveBeenCalled();
});

test("a stale edit keeps the form and offers to reload", async () => {
  vi.mocked(getApplication).mockResolvedValue(makeApplication());
  vi.mocked(listApplicationEvents).mockResolvedValue([created]);
  vi.mocked(updateApplication).mockRejectedValue(
    new ApiError(409, "Application version is stale"),
  );
  renderDetail();

  await startEditingStatus("offer");

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "changed since you opened it",
  );
  expect(screen.getByLabelText("Status")).toHaveValue("offer");

  fireEvent.click(screen.getByRole("button", { name: "Reload latest" }));

  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(getApplication).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("shows a missing application as an error", async () => {
  vi.mocked(getApplication).mockRejectedValue(
    new ApiError(404, "Application not found"),
  );
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  renderDetail();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Application not found",
  );
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/applications/ApplicationDetail.test.tsx`
Expected: FAIL because `./ApplicationDetail` does not exist.

- [ ] **Step 3: Implement the detail view**

Create `frontend/src/applications/ApplicationDetail.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getApplication,
  listApplicationEvents,
  updateApplication,
} from "../api/client";
import type {
  Application,
  ApplicationEvent,
  ApplicationValues,
  EventOrigin,
  EventType,
} from "../api/types";
import { ApplicationForm, buildPatch } from "./ApplicationForm";
import {
  FIELD_LABELS,
  ROLE_TYPE_LABELS,
  STATUS_LABELS,
  UNKNOWN,
  formatCycle,
  formatFieldValue,
  formatTimestamp,
} from "./labels";

const EVENT_LABELS: Record<EventType, string> = {
  created: "Created",
  updated: "Updated",
  deleted: "Deleted",
};

const ORIGIN_LABELS: Record<EventOrigin, string> = {
  user: "you",
  rules: "email rules",
  ai: "AI",
};

const FIELD_ORDER = Object.keys(FIELD_LABELS);

interface Loaded {
  application: Application;
  events: ApplicationEvent[];
}

interface ApplicationDetailProps {
  applicationId: string;
  onBack: () => void;
}

export function ApplicationDetail({ applicationId, onBack }: ApplicationDetailProps) {
  const [data, setData] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [stale, setStale] = useState(false);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const [application, events] = await Promise.all([
        getApplication(applicationId),
        listApplicationEvents(applicationId),
      ]);
      setData({ application, events });
    } catch (error) {
      setLoadError(
        error instanceof ApiError ? error.message : "Could not load the application.",
      );
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(values: ApplicationValues) {
    if (data === null) return;
    const patch = buildPatch(data.application, values);
    if (patch === null) {
      setEditing(false);
      return;
    }
    try {
      await updateApplication(applicationId, patch);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setStale(true);
        return;
      }
      throw error;
    }
    setEditing(false);
    await load();
  }

  async function reloadLatest() {
    setStale(false);
    setEditing(false);
    await load();
  }

  return (
    <section className="page" aria-labelledby="detail-title">
      <button type="button" className="link" onClick={onBack}>
        ← Back to applications
      </button>
      {loadError !== null ? (
        <div role="alert" className="error">
          <p>{loadError}</p>
          <button type="button" onClick={() => void load()}>
            Retry
          </button>
        </div>
      ) : data === null ? (
        <p className="muted">Loading application…</p>
      ) : (
        <>
          <div className="page-header">
            <h1 id="detail-title">
              {data.application.company ?? UNKNOWN} · {data.application.role ?? UNKNOWN}
            </h1>
            {!editing && (
              <button type="button" className="primary" onClick={() => setEditing(true)}>
                Edit
              </button>
            )}
          </div>
          {stale && (
            <div role="alert" className="error">
              <p>
                This application changed since you opened it, so your edits were
                not saved.
              </p>
              <button type="button" onClick={() => void reloadLatest()}>
                Reload latest
              </button>
            </div>
          )}
          {editing ? (
            <ApplicationForm
              initial={data.application}
              submitLabel="Save changes"
              onSubmit={save}
              onCancel={() => setEditing(false)}
            />
          ) : (
            <ApplicationFields application={data.application} />
          )}
          <h2 id="history-title">History</h2>
          <ol className="history" aria-labelledby="history-title">
            {data.events.map((event) => (
              <HistoryEntry key={event.event_id} event={event} />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function ApplicationFields({ application }: { application: Application }) {
  const rows: [string, string][] = [
    ["Company", application.company ?? UNKNOWN],
    ["Role", application.role ?? UNKNOWN],
    ["Role type", ROLE_TYPE_LABELS[application.role_type ?? "unknown"]],
    ["Cycle", formatCycle(application.season, application.year)],
    [
      "Status",
      application.status === null ? UNKNOWN : STATUS_LABELS[application.status],
    ],
    ["Application date", application.application_date ?? UNKNOWN],
    ["Last update", formatTimestamp(application.updated_at)],
  ];
  return (
    <dl className="fields">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function HistoryEntry({ event }: { event: ApplicationEvent }) {
  const changes = Object.entries(event.changes)
    .filter(([, change]) => change.before !== change.after)
    .sort(([a], [b]) => FIELD_ORDER.indexOf(a) - FIELD_ORDER.indexOf(b));
  return (
    <li>
      <p className="history-title">
        {EVENT_LABELS[event.event_type]} by {ORIGIN_LABELS[event.origin]}{" "}
        <time dateTime={event.occurred_at}>{formatTimestamp(event.occurred_at)}</time>
      </p>
      <ul>
        {changes.map(([field, change]) => {
          const label = FIELD_LABELS[field] ?? field;
          const after = formatFieldValue(field, change.after);
          return (
            <li key={field}>
              {event.event_type === "created"
                ? `${label}: ${after}`
                : `${label}: ${formatFieldValue(field, change.before)} → ${after}`}
            </li>
          );
        })}
      </ul>
    </li>
  );
}
```

- [ ] **Step 4: Run the frontend checks**

Run: `cd frontend && npm run lint && npm test && npm run typecheck`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/applications/ApplicationDetail.tsx frontend/src/applications/ApplicationDetail.test.tsx
git commit -m "feat: add application detail with history and editing"
```

---

### Task 7: Application list and app shell

**Files:**

- Create: `frontend/src/applications/ApplicationList.tsx`
- Modify: `frontend/src/App.tsx` (replace the placeholder page)
- Modify: `frontend/src/App.test.tsx` (replace the placeholder test)
- Modify: `frontend/src/styles.css` (replace placeholder-only styles)

**Interfaces:**

- Consumes: everything from Tasks 3–6.
- Produces: `ApplicationList({ applications, filters, onFiltersChange, sortKey, onSortChange, onSelect, onCreate })`. Filter controls are labelled "Role type", "Season", "Year", "Status", "Search", "Sort by"; the summary is a `<dl aria-label="Status summary">`; each company cell is a button that opens the detail view.
- `App` owns the view (`list` / `create` / `detail`), the loaded applications, and the filter and sort state, so filters survive opening a detail and going back. Returning to the list reloads it.

- [ ] **Step 1: Write failing app tests**

Replace `frontend/src/App.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import App from "./App";
import {
  ApiError,
  createApplication,
  getApplication,
  listAllApplications,
  listApplicationEvents,
} from "./api/client";
import { makeApplication } from "./test/fixtures";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    listAllApplications: vi.fn(),
    createApplication: vi.fn(),
    getApplication: vi.fn(),
    listApplicationEvents: vi.fn(),
    updateApplication: vi.fn(),
  };
});

const internship = makeApplication({ application_id: "app-1" });
const coop = makeApplication({
  application_id: "app-2",
  company: "Globex",
  role: "Platform Co-op",
  role_type: "co_op",
  status: "interviewing",
});

afterEach(() => {
  vi.resetAllMocks();
});

test("shows internships by default with a status summary", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship, coop]);
  render(<App />);

  expect(await screen.findByRole("button", { name: "Acme" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Globex" })).not.toBeInTheDocument();
  const summary = screen.getByLabelText("Status summary");
  expect(within(summary).getByText("1 of 2")).toBeInTheDocument();
});

test("filters, searches, and explains empty results", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship, coop]);
  render(<App />);
  await screen.findByRole("button", { name: "Acme" });

  fireEvent.change(screen.getByLabelText("Role type"), {
    target: { value: "all" },
  });
  expect(screen.getByRole("button", { name: "Globex" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search"), {
    target: { value: "platform" },
  });
  expect(screen.queryByRole("button", { name: "Acme" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Globex" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Search"), {
    target: { value: "no such company" },
  });
  expect(
    screen.getByText("No applications match these filters."),
  ).toBeInTheDocument();
});

test("shows an empty state before any applications exist", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([]);
  render(<App />);

  expect(await screen.findByText("No applications yet.")).toBeInTheDocument();
});

test("shows a load error and retries", async () => {
  vi.mocked(listAllApplications)
    .mockRejectedValueOnce(
      new ApiError(0, "Could not reach the server. Is the backend running?"),
    )
    .mockResolvedValueOnce([internship]);
  render(<App />);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Could not reach the server. Is the backend running?",
  );
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));

  expect(await screen.findByRole("button", { name: "Acme" })).toBeInTheDocument();
});

test("creates an application and opens its detail", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([]);
  vi.mocked(createApplication).mockResolvedValue(internship);
  vi.mocked(getApplication).mockResolvedValue(internship);
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "New application" }));
  fireEvent.change(screen.getByLabelText("Company"), { target: { value: "Acme" } });
  fireEvent.change(screen.getByLabelText("Role"), {
    target: { value: "Software Engineer Intern" },
  });
  fireEvent.change(screen.getByLabelText("Status"), {
    target: { value: "applied" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create application" }));

  expect(
    await screen.findByRole("heading", { name: "Acme · Software Engineer Intern" }),
  ).toBeInTheDocument();
  expect(createApplication).toHaveBeenCalledWith({
    company: "Acme",
    role: "Software Engineer Intern",
    role_type: "unknown",
    season: null,
    year: null,
    status: "applied",
    application_date: null,
  });
});

test("keeps filters after opening a detail and going back", async () => {
  vi.mocked(listAllApplications).mockResolvedValue([internship]);
  vi.mocked(getApplication).mockResolvedValue(internship);
  vi.mocked(listApplicationEvents).mockResolvedValue([]);
  render(<App />);

  fireEvent.change(await screen.findByLabelText("Search"), {
    target: { value: "acme" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Acme" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "← Back to applications" }),
  );

  expect(await screen.findByLabelText("Search")).toHaveValue("acme");
  expect(listAllApplications).toHaveBeenCalledTimes(2);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/App.test.tsx`
Expected: FAIL because `App` still renders the placeholder page.

- [ ] **Step 3: Implement the list**

Create `frontend/src/applications/ApplicationList.tsx`:

```tsx
import type {
  Application,
  ApplicationStatus,
  RoleType,
  Season,
} from "../api/types";
import {
  ALL,
  UNKNOWN_VALUE,
  countByStatus,
  filterApplications,
  sortApplications,
  yearOptions,
  type Filters,
  type SortKey,
} from "./filtering";
import {
  ROLE_TYPE_LABELS,
  SEASON_LABELS,
  STATUS_LABELS,
  UNKNOWN,
  formatCycle,
  formatTimestamp,
} from "./labels";

const SORT_LABELS: Record<SortKey, string> = {
  updated: "Last update",
  application_date: "Application date",
  company: "Company",
};

interface ApplicationListProps {
  applications: Application[];
  filters: Filters;
  onFiltersChange: (filters: Filters) => void;
  sortKey: SortKey;
  onSortChange: (sortKey: SortKey) => void;
  onSelect: (applicationId: string) => void;
  onCreate: () => void;
}

export function ApplicationList({
  applications,
  filters,
  onFiltersChange,
  sortKey,
  onSortChange,
  onSelect,
  onCreate,
}: ApplicationListProps) {
  const visible = sortApplications(filterApplications(applications, filters), sortKey);
  const counts = countByStatus(visible);

  function setFilter<K extends keyof Filters>(field: K, value: Filters[K]) {
    onFiltersChange({ ...filters, [field]: value });
  }

  return (
    <section className="page" aria-labelledby="applications-title">
      <div className="page-header">
        <h1 id="applications-title">Applications</h1>
        <button type="button" className="primary" onClick={onCreate}>
          New application
        </button>
      </div>

      <dl className="summary" aria-label="Status summary">
        <div>
          <dt>Showing</dt>
          <dd>
            {visible.length} of {applications.length}
          </dd>
        </div>
        {(Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((status) => (
          <div key={status}>
            <dt>{STATUS_LABELS[status]}</dt>
            <dd>{counts[status]}</dd>
          </div>
        ))}
      </dl>

      <div className="filters">
        <div className="field">
          <label htmlFor="filter-role-type">Role type</label>
          <select
            id="filter-role-type"
            value={filters.roleType}
            onChange={(event) =>
              setFilter("roleType", event.target.value as Filters["roleType"])
            }
          >
            <option value={ALL}>All role types</option>
            {(Object.keys(ROLE_TYPE_LABELS) as RoleType[]).map((roleType) => (
              <option key={roleType} value={roleType}>
                {ROLE_TYPE_LABELS[roleType]}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-season">Season</label>
          <select
            id="filter-season"
            value={filters.season}
            onChange={(event) =>
              setFilter("season", event.target.value as Filters["season"])
            }
          >
            <option value={ALL}>All seasons</option>
            {(Object.keys(SEASON_LABELS) as Season[]).map((season) => (
              <option key={season} value={season}>
                {SEASON_LABELS[season]}
              </option>
            ))}
            <option value={UNKNOWN_VALUE}>{UNKNOWN}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-year">Year</label>
          <select
            id="filter-year"
            value={filters.year}
            onChange={(event) => setFilter("year", event.target.value)}
          >
            <option value={ALL}>All years</option>
            {yearOptions(applications).map((year) => (
              <option key={year} value={String(year)}>
                {year}
              </option>
            ))}
            <option value={UNKNOWN_VALUE}>{UNKNOWN}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-status">Status</label>
          <select
            id="filter-status"
            value={filters.status}
            onChange={(event) =>
              setFilter("status", event.target.value as Filters["status"])
            }
          >
            <option value={ALL}>All statuses</option>
            {(Object.keys(STATUS_LABELS) as ApplicationStatus[]).map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
            <option value={UNKNOWN_VALUE}>{UNKNOWN}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="filter-search">Search</label>
          <input
            id="filter-search"
            type="search"
            placeholder="Company or role"
            value={filters.search}
            onChange={(event) => setFilter("search", event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="sort-by">Sort by</label>
          <select
            id="sort-by"
            value={sortKey}
            onChange={(event) => onSortChange(event.target.value as SortKey)}
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((key) => (
              <option key={key} value={key}>
                {SORT_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
      </div>

      {visible.length === 0 ? (
        <p className="empty">
          {applications.length === 0
            ? "No applications yet."
            : "No applications match these filters."}
        </p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Company</th>
                <th scope="col">Role</th>
                <th scope="col">Role type</th>
                <th scope="col">Cycle</th>
                <th scope="col">Status</th>
                <th scope="col">Applied</th>
                <th scope="col">Last update</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((application) => (
                <tr key={application.application_id}>
                  <td>
                    <button
                      type="button"
                      className="link"
                      onClick={() => onSelect(application.application_id)}
                    >
                      {application.company ?? UNKNOWN}
                    </button>
                  </td>
                  <td>{application.role ?? UNKNOWN}</td>
                  <td>{ROLE_TYPE_LABELS[application.role_type ?? "unknown"]}</td>
                  <td>{formatCycle(application.season, application.year)}</td>
                  <td>
                    {application.status === null
                      ? UNKNOWN
                      : STATUS_LABELS[application.status]}
                  </td>
                  <td>{application.application_date ?? UNKNOWN}</td>
                  <td>{formatTimestamp(application.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Replace the app shell**

Replace `frontend/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";

import { ApiError, createApplication, listAllApplications } from "./api/client";
import type {
  Application,
  ApplicationCreate,
  ApplicationValues,
} from "./api/types";
import { ApplicationDetail } from "./applications/ApplicationDetail";
import { ApplicationForm } from "./applications/ApplicationForm";
import { ApplicationList } from "./applications/ApplicationList";
import {
  DEFAULT_FILTERS,
  type Filters,
  type SortKey,
} from "./applications/filtering";
import "./styles.css";

type View =
  | { kind: "list" }
  | { kind: "create" }
  | { kind: "detail"; applicationId: string };

function App() {
  const [view, setView] = useState<View>({ kind: "list" });
  const [applications, setApplications] = useState<Application[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<SortKey>("updated");

  async function loadApplications() {
    setLoadError(null);
    try {
      setApplications(await listAllApplications());
    } catch (error) {
      setLoadError(
        error instanceof ApiError ? error.message : "Could not load applications.",
      );
    }
  }

  useEffect(() => {
    void loadApplications();
  }, []);

  function showList() {
    setView({ kind: "list" });
    void loadApplications();
  }

  async function handleCreate(values: ApplicationValues) {
    // In create mode the form requires company, role, and status.
    const created = await createApplication(values as ApplicationCreate);
    setView({ kind: "detail", applicationId: created.application_id });
  }

  return (
    <div className="app-shell">
      <header className="site-header">
        <span className="wordmark-mark" aria-hidden="true">
          AT
        </span>
        <span className="wordmark-name">Application Tracker</span>
      </header>

      <main className="content">
        {view.kind === "create" && (
          <section className="page" aria-labelledby="create-title">
            <h1 id="create-title">New application</h1>
            <ApplicationForm
              submitLabel="Create application"
              onSubmit={handleCreate}
              onCancel={showList}
            />
          </section>
        )}
        {view.kind === "detail" && (
          <ApplicationDetail applicationId={view.applicationId} onBack={showList} />
        )}
        {view.kind === "list" &&
          (loadError !== null ? (
            <div role="alert" className="error">
              <p>Could not load applications: {loadError}</p>
              <button type="button" onClick={() => void loadApplications()}>
                Retry
              </button>
            </div>
          ) : applications === null ? (
            <p className="muted">Loading applications…</p>
          ) : (
            <ApplicationList
              applications={applications}
              filters={filters}
              onFiltersChange={setFilters}
              sortKey={sortKey}
              onSortChange={setSortKey}
              onSelect={(applicationId) =>
                setView({ kind: "detail", applicationId })
              }
              onCreate={() => setView({ kind: "create" })}
            />
          ))}
      </main>
    </div>
  );
}

export default App;
```

- [ ] **Step 5: Replace the placeholder styles**

Replace `frontend/src/styles.css`. The shell, header, and wordmark rules are unchanged; the placeholder-page rules (`.foundation-layout`, `.milestone*`, `.status-*`, `.scope-note`) are removed because nothing uses them any more:

```css
:root {
  color: #1b2923;
  background: #eef2ef;
  font-family: "Avenir Next", Avenir, "Segoe UI", sans-serif;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
  min-height: 100vh;
}

button,
input,
textarea,
select {
  font: inherit;
}

.app-shell {
  width: min(1080px, calc(100% - 48px));
  min-height: 100vh;
  margin: 0 auto;
  background: #f9faf8;
  border-right: 1px solid #d4ddd7;
  border-left: 1px solid #d4ddd7;
}

.site-header {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 76px;
  padding: 0 48px;
  border-bottom: 1px solid #d4ddd7;
}

.wordmark-mark {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #f9faf8;
  background: #276146;
  border-radius: 4px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.04em;
}

.wordmark-name {
  font-size: 0.96rem;
  font-weight: 600;
}

.content {
  padding: 40px 48px 64px;
}

.page {
  display: grid;
  gap: 24px;
}

.page-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

h1 {
  margin: 0;
  color: #14231c;
  font-size: 1.8rem;
  font-weight: 600;
  letter-spacing: -0.02em;
}

h2 {
  margin: 16px 0 0;
  font-size: 1.1rem;
  font-weight: 600;
}

button {
  padding: 8px 14px;
  color: #1b2923;
  background: #f9faf8;
  border: 1px solid #b9c7be;
  border-radius: 4px;
  cursor: pointer;
}

button:hover {
  background: #eef2ef;
}

button:disabled {
  cursor: default;
  opacity: 0.6;
}

button.primary {
  color: #f9faf8;
  background: #276146;
  border-color: #276146;
}

button.primary:hover {
  background: #1f4f39;
}

button.link {
  justify-self: start;
  padding: 0;
  color: #276146;
  background: none;
  border: 0;
  text-align: left;
  text-decoration: underline;
  text-underline-offset: 2px;
}

input,
select {
  padding: 7px 10px;
  color: #1b2923;
  background: #fff;
  border: 1px solid #b9c7be;
  border-radius: 4px;
}

button:focus-visible,
input:focus-visible,
select:focus-visible {
  outline: 2px solid #348060;
  outline-offset: 2px;
}

.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0;
}

.summary div {
  min-width: 96px;
  padding: 10px 14px;
  background: #f3f6f3;
  border: 1px solid #d4ddd7;
  border-radius: 4px;
}

.summary dt,
.fields dt {
  color: #5e6b64;
  font-size: 0.8rem;
}

.summary dd {
  margin: 2px 0 0;
  font-size: 1.1rem;
  font-weight: 600;
}

.filters,
.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 14px;
}

.field {
  display: grid;
  gap: 6px;
}

.field label {
  color: #52625a;
  font-size: 0.85rem;
  font-weight: 600;
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid #d4ddd7;
  border-radius: 4px;
}

table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}

th,
td {
  padding: 10px 14px;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid #e3e9e5;
}

th {
  color: #52625a;
  background: #f3f6f3;
  font-size: 0.8rem;
  font-weight: 600;
}

tbody tr:last-child td {
  border-bottom: 0;
}

.fields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 14px;
  margin: 0;
}

.fields dd {
  margin: 2px 0 0;
}

.form-actions {
  display: flex;
  gap: 10px;
}

.history {
  display: grid;
  gap: 14px;
  margin: 0;
  padding-left: 20px;
}

.history ul {
  margin: 6px 0 0;
  padding-left: 18px;
  color: #3b4a43;
}

.history-title {
  margin: 0;
  font-weight: 600;
}

.history-title time {
  margin-left: 8px;
  color: #5e6b64;
  font-size: 0.85rem;
  font-weight: 400;
}

.error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 0;
  padding: 12px 16px;
  color: #7a1f1f;
  background: #fbeeee;
  border: 1px solid #e9c3c3;
  border-radius: 4px;
}

.error p {
  margin: 0;
}

.muted,
.empty {
  color: #5e6b64;
}

@media (max-width: 760px) {
  .app-shell {
    width: 100%;
    border: 0;
  }

  .site-header {
    padding: 0 24px;
  }

  .content {
    padding: 28px 20px 48px;
  }
}
```

- [ ] **Step 6: Run the frontend checks**

Run: `cd frontend && npm run lint && npm test && npm run typecheck && npm run build`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/applications/ApplicationList.tsx frontend/src/styles.css
git commit -m "feat: add application dashboard and wire the manual tracker"
```

---

### Task 8: Documentation, end-to-end verification, and review

**Files:**

- Modify: `docs/DEVELOPMENT.md`
- Modify: `docs/IMPLEMENTATION_ROADMAP.md`
- Modify: `AGENTS.md`
- Modify: this plan (check off steps)

- [ ] **Step 1: Update the development guide**

In `docs/DEVELOPMENT.md`:

- Change the opening paragraph to say the project contains the manual tracker (FastAPI + DynamoDB backend, React dashboard) and that hosted authentication, Gmail access, and AI providers are not implemented yet.
- Add Docker Desktop to the prerequisites, for DynamoDB Local.
- Replace the "Milestones 0 and 1 need no credentials" paragraph: local development uses the fake values in `.env.example`, copied to the git-ignored `.env`; real credentials never go in `.env.example`.
- Replace "Run the backend" and "Run the frontend" with:

````markdown
## Run the app locally

```bash
cp .env.example .env             # fake local values only
docker compose up -d dynamodb    # DynamoDB Local on 127.0.0.1:8001

cd backend
uv sync --locked --dev
uv run --env-file ../.env python -m scripts.create_local_tables
uv run --env-file ../.env uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints, normally `http://127.0.0.1:5173`. Vite proxies `/api`
requests to the API on `http://127.0.0.1:8000`. Data persists in the
`dynamodb-data` Docker volume; `docker compose down -v` deletes it.
````

- In "Application domain", explain that `get_current_subject` returns `401` unless the server-side `LOCAL_DEV_SUBJECT` variable is set, that it refuses to run inside AWS Lambda, and that milestone 4 replaces it with Cognito. Keep the existing sentence forbidding owner-selecting request headers.

- [ ] **Step 2: Update the roadmap and agent instructions**

- In `docs/IMPLEMENTATION_ROADMAP.md`, add `**Status: complete**` under the milestone 3 heading, matching milestone 2.
- In `AGENTS.md`, update "Project status" (milestones 0–3 done; the frontend is the manual-tracker dashboard), add the local-run commands from Step 1 to "Commands", and in the `api/dependencies.py` bullet describe the `LOCAL_DEV_SUBJECT` behavior.

- [ ] **Step 3: Run every CI check**

```bash
cd backend && uv sync --locked --dev && uv run ruff check . && uv run pytest -q
cd ../frontend && npm ci && npm audit && npm run lint && npm test && npm run typecheck && npm run build
```

Expected: everything passes, and `npm audit` reports no vulnerabilities.

- [ ] **Step 4: Verify the milestone end to end in a browser**

With DynamoDB Local, the API, and Vite running as in Step 1, use the browser (Playwright) to check the roadmap's verification statement with synthetic data:

1. Create an internship (Acme, Software Engineer Intern, Summer 2027, Applied) and a co-op (Globex, Platform Co-op, no season or year, Interviewing).
2. The list shows only Acme by default; "All role types" shows both; the summary counts match.
3. Search "platform" shows only Globex; Season "Unknown" shows only Globex.
4. Open Globex, edit its status to Offer, and confirm the history shows "Status: Interviewing → Offer".
5. Open Acme in two tabs, save an edit in one, then save in the other and confirm the stale-edit message and "Reload latest".
6. Stop the API and reload the page; confirm the "Could not reach the server" message and that "Retry" recovers after restarting the API.

Then remove the synthetic data with `docker compose down -v`.

- [ ] **Step 5: Commit the documentation**

```bash
git add docs/DEVELOPMENT.md docs/IMPLEMENTATION_ROADMAP.md AGENTS.md docs/superpowers/plans/2026-10-10-manual-tracker.md
git commit -m "docs: record manual tracker milestone"
```

- [ ] **Step 6: Codex review of the branch**

```bash
codex exec review -c sandbox_mode='"read-only"' --base main --ephemeral -o <scratchpad>/milestone-3-review.txt
```

Report the findings to the user. Fix only the findings the user accepts, committing each fix, then ask the user before merging `milestone-3-manual-tracker` into `main`. Do not push.
