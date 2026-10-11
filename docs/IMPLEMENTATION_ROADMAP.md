# Application Tracker Implementation Roadmap

**Goal:** Deliver the application as small, testable vertical slices, beginning
with a locally usable manual tracker before Gmail synchronization or AI.

**Architecture:** The frontend is a React/TypeScript application. The backend is
a Python FastAPI service designed to run on AWS Lambda behind API Gateway, with
DynamoDB persistence added in the manual-tracker milestone. Gmail processing,
scheduled work, and AI escalation are layered on only after application identity,
ownership, history, and correction behavior are tested.

**Tech stack:** Python 3.12.13, Node.js 22.23.3, npm 10.9.9,
React/TypeScript/Vite, FastAPI, pytest, Vitest, and GitHub Actions.

**Product specification:** [PRD.md](PRD.md)

**Technical design:** [TECHNICAL_DESIGN.md](TECHNICAL_DESIGN.md)

## Global constraints

- Keep each user's data isolated by the authenticated owner.
- Keep credentials, real email content, and personal application data out of Git,
  logs, browser analytics, and test fixtures.
- Use deterministic rules before AI; send unresolved cases to manual review.
- Do not infer missing dates, cycles, statuses, or application identity.
- Use synthetic data in the public test suite.
- Build only the active milestone; do not scaffold later subsystems early.

## Milestones

### 0. Establish the baseline

- Publish the accepted PRD and technical-design revision.
- Work on a short-lived feature branch.
- Pin the local Python, Node.js, and npm versions.
- Document the rule that local environment files must not contain committed
  credentials.

**Verification:** The remote documentation is current, the working tree is on a
feature branch, runtime pins are present, and Git ignores local secret files.

### 1. Scaffold the development environment

- Create a minimal FastAPI service with a tested `GET /health` endpoint.
- Create a minimal React/TypeScript application with one rendered-page test.
- Add backend linting and tests.
- Add frontend linting, tests, type checking, and a production build.
- Run the same checks in GitHub Actions.

**Verification:** Both applications start locally and every backend/frontend CI
command passes locally.

### 2. Build the application domain

**Status: complete**

- Define application and history-event schemas.
- Add owner-scoped create, list, read, update, and delete operations.
- Add optimistic version checks.
- Add persistence backed by DynamoDB.

**Verification:** API tests cover CRUD, invalid values, stale writes, and
cross-user access.

### 3. Deliver the manual-tracker vertical slice

**Status: complete**

- Add the application table and filters.
- Add manual application creation and editing.
- Add application details and chronological history.
- Preserve unknown values instead of inventing defaults.

**Verification:** A local user can create, edit, search, filter, and inspect an
application with an audit history.

### 4. Add hosted authentication and deployment

- Define backend AWS resources with SAM/CloudFormation.
- Add Cognito Google federation and JWT validation.
- Derive ownership only from verified authentication claims.
- Deploy the API and frontend to a controlled development environment.

**Verification:** Google sign-in works and one user cannot access another user's
records.

### 5. Connect Gmail safely

- Implement the separate Gmail OAuth flow.
- Store single-use OAuth state and encrypted refresh tokens.
- Add connection status and disconnect behavior.
- Reject queued work after disconnect.

**Verification:** A designated test account connects and disconnects without
credentials appearing in logs, URLs, or API responses.

### 6. Add deterministic email processing

- Create synthetic recruiting-email fixtures.
- Parse safe text from Gmail messages.
- Add deterministic event rules and strong application matching.
- Persist applied, ignored, review-required, or retryable outcomes idempotently.

**Verification:** Explicit fixtures classify without AI, while unrelated or
ambiguous messages cannot silently mutate an application.

### 7. Add review and reconciliation

- Build review queue endpoints and UI.
- Support accept, edit, reject, attach, and create-new actions.
- Add manual-correction timestamps and replay protection.
- Add duplicate merging with explicit conflict resolution.

**Verification:** Replayed evidence cannot undo corrections or recreate merged
duplicates.

### 8. Add resilient synchronization

- Add SQS workers and durable sync runs.
- Implement the fixed-boundary 90-day import.
- Add resumable pagination, leases, checkpoints, and deduplication.
- Add incremental Gmail history synchronization and stale-history recovery.
- Add scheduled and manual synchronization entry points.

**Verification:** Interrupted jobs resume, repeated jobs do not duplicate data,
and scheduled synchronization runs without a browser.

### 9. Add BYOK AI escalation

- Define one validated extraction schema.
- Store provider keys encrypted.
- Add OpenAI, Anthropic, and Gemini adapters.
- Enforce evidence validation, request limits, and bounded retries.
- Preserve rules and review behavior during provider failures.

**Verification:** AI runs only for ambiguous cases and cannot act on prompt
injection, tools, or credentials.

### 10. Harden and evaluate

- Complete account deletion and stale-work protection.
- Add sanitized logs, alarms, dead-letter handling, and retention.
- Measure synthetic classification behavior in CI.
- Run controlled live import, deduplication, scheduling, revocation, and deletion
  tests.
- Complete public-launch privacy and Google verification work separately.

**Verification:** Every PRD acceptance criterion maps to an automated test or a
documented controlled-deployment check.

## Current implementation batch: milestones 0 and 1

### Task 1: Baseline and roadmap

**Files:**

- Create `.python-version` with `3.12.13`.
- Create `.nvmrc` with `22.23.3`.
- Create `.env.example` containing only safe local-development documentation.
- Create this roadmap.

**Steps:**

1. Publish the accepted documentation commit without overwriting remote work.
2. Create `feat/project-foundation`.
3. Add the runtime pins and safe environment example.
4. Run `git diff --check`.

### Task 2: FastAPI foundation

**Files:**

- Create `backend/pyproject.toml` and `backend/uv.lock` for dependencies.
- Create `backend/app/main.py` for the FastAPI application.
- Create `backend/tests/test_health.py` for the public health contract.

**Interface:** `GET /health` returns status `200` and JSON `{"status": "ok"}`.

**Steps:**

1. Write the endpoint test before the application module exists.
2. Run the test and confirm it fails because `app.main` is absent.
3. Add the minimal FastAPI application and health route.
4. Run `uv run pytest` and `uv run ruff check .` from `backend/`.

### Task 3: React foundation

**Files:**

- Create `frontend/package.json` and its lockfile.
- Create the Vite/TypeScript configuration and HTML entry point.
- Create `frontend/src/App.tsx`, `main.tsx`, and `styles.css`.
- Create `frontend/src/App.test.tsx` and the test setup.

**Interface:** The initial page identifies the product as `Application Tracker`
and states that manual tracking is the first milestone.

**Steps:**

1. Install the pinned frontend dependencies.
2. Write the page test before `App.tsx` exists.
3. Run the test and confirm it fails because the component is absent.
4. Add the minimal application shell.
5. Run frontend linting, tests, type checking, and the production build.

### Task 4: Continuous integration and development instructions

**Files:**

- Create `.github/workflows/ci.yml` with separate backend and frontend jobs.
- Create `docs/DEVELOPMENT.md` with exact local commands.

**Steps:**

1. Configure CI to use the pinned Python and Node.js versions.
2. Run every CI command locally.
3. Run `git diff --check` and inspect the staged file list for secrets or build
   output.

## Review focus for milestones 0 and 1

- The health endpoint must use the documented path and response shape.
- The frontend test must render the real application component.
- The production frontend build must succeed, not only the unit test.
- CI commands must match the locally verified commands.
- No credential-shaped values or generated dependency/build directories may be
  committed.
