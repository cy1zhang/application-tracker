# Resilient Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import a connected Gmail account's recent recruiting messages and keep it synchronized daily, with durable progress and safe retries.

**Architecture:** Keep one active sync run per owner in DynamoDB. An authenticated API request or daily dispatcher records a run and enqueues its ID; an SQS worker reads bounded Gmail pages, invokes the existing offline processor, and checkpoints only after each message has a durable outcome. Recovery re-enqueues unfinished runs, and every worker checks the Gmail connection generation before reading or committing.

**Tech Stack:** Python 3.12, FastAPI, boto3, DynamoDB, SQS, EventBridge Scheduler, AWS SAM, pytest/moto, React/TypeScript/Vitest.

**Spec:** `docs/IMPLEMENTATION_ROADMAP.md` Milestone 8; `docs/PRD.md` section 6; `docs/TECHNICAL_DESIGN.md` Sync and recovery.

## Global Constraints

- Derive the owner only from verified authentication or the server-side connected-account record.
- A first import has one fixed 90-day boundary; a continuation cannot move it.
- Gmail messages and application mutations are at least once, so persisted outcomes and checkpoints must be idempotent.
- A disconnect or new connection generation invalidates queued work before Gmail reads and commits.
- Never store raw email bodies or credentials in a run, queue payload, log, or fixture.
- Keep AI providers, account deletion, and public-launch work outside this milestone.

## Review Focus

- Two manual requests or a daily/manual overlap must return or reuse one active run.
- An interrupted page must resume without skipping its uncommitted messages.
- A stale Gmail history ID must recover from the last successful sync time, not from a fresh 90-day window.
- A disconnected account must not be read by a delayed worker.
- A failed message must leave a visible partial or retryable run state, not a false success.

---

### Task 1: Durable, owner-scoped run admission

**Files:** Create `backend/app/domain/sync.py`, `backend/app/repositories/dynamodb_sync.py`, `backend/tests/test_dynamodb_sync.py`; modify `backend/app/repositories/dynamodb_tables.py`, `infra/template.yaml`, and focused table tests.

**Interfaces:** `DynamoSyncRepository.start(owner_id, run_id, now) -> SyncRun` conditionally claims the connected owner's active-run slot and creates one `SyncRuns` item. It returns the existing active run on an overlapping request. The run stores its owner, connection generation, mode, immutable import boundary, progress, and state; it stores no email content. `get_latest(owner_id) -> SyncRun | None` never crosses owners. Track connection time for new Gmail connections; for preexisting connections with no timestamp, use first-run time as the documented fallback boundary anchor.

- [x] Write moto tests for a connected owner's first run, overlapping requests, owner isolation, and disconnected-owner rejection.
- [x] Run `uv run pytest tests/test_dynamodb_sync.py -q` and confirm the missing repository fails.
- [x] Add the table, domain model, connection timestamp, and conditional run admission.
- [x] Run focused tests, full backend tests, backend lint, SAM lint, offline deploy tests, and `git diff --check`; commit the verified step.

### Task 2: Manual run and status API

**Files:** Create `backend/app/api/syncs.py`, `backend/tests/test_syncs_api.py`; modify `backend/app/api/dependencies.py`, `backend/app/main.py`, and `infra/template.yaml`.

**Interfaces:** `POST /syncs` returns `202` with a run ID or the active run, and enqueues a job only after the run is durable. `GET /syncs/latest` returns the owner's latest state and counts. Queue-send failure leaves a recoverable pending run. Neither endpoint accepts an owner ID.

- [x] Write API tests for authentication, owner isolation, overlap, and queue-send failure.
- [x] Confirm the tests fail for the absent routes.
- [x] Add the routes and small queue publisher; verify focused/full backend checks and commit.

### Task 3: Bounded Gmail import worker and checkpoints

**Files:** Create `backend/app/services/gmail_fetch.py`, `backend/app/services/sync_worker.py`, `backend/app/sync_handler.py`, and focused tests; extend `backend/app/repositories/dynamodb_sync.py`, `backend/app/repositories/dynamodb_email.py`, and `infra/template.yaml`.

**Interfaces:** The worker consumes `{owner_id, run_id, generation}` from SQS, obtains a conditional lease, refreshes the connected credential, and pages a fixed-boundary Gmail query. It fetches candidates and passes synthetic-compatible Gmail payloads to `process_message`. It persists a page cursor only after each preceding message has a durable terminal or explicit retryable outcome. A continuation is represented durably before queue send; exhausted attempts use SQS redrive.

- [x] Guard every worker-origin email outcome transaction by the current connection generation and active run ID; test that disconnect blocks both applied and review/ignore writes.
- [x] Add a bounded Gmail message-list/full-fetch client with fixed epoch boundary, paging, and sanitized response tests.
- [x] Refresh a stored Gmail token server-side and distinguish revoked consent from transient OAuth failure without exposing provider data.
- [x] Add conditional run leases, durable page checkpoints, and completion that releases the active slot; test expiry and disconnect races.
- [x] Load current owner-scoped application candidates with persisted thread and requisition links for deterministic matching.
- [x] Test page interruption, duplicate delivery, stale generation, lease contention, and checkpoint ordering with provider stubs.
- [x] Confirm focused tests fail for missing worker behavior.
- [x] Add the smallest fetch/worker path, SQS trigger, visibility/redrive settings, and IAM; verify tests, lint, SAM, and commit.

### Task 4: Incremental history, stale-history recovery, and daily dispatch

**Files:** Extend `backend/app/services/gmail_fetch.py`, `backend/app/services/sync_worker.py`, `backend/app/repositories/dynamodb_sync.py`, and `infra/template.yaml`; add focused tests.

**Interfaces:** A completed initial import records Gmail's history checkpoint. Later runs page Gmail history and advance the checkpoint only after all listed messages have durable outcomes. A stale history ID triggers a paginated rescan from the last successful sync time with overlap. A daily 09:00 UTC dispatcher pages connected accounts and starts/enqueues runs; a recovery pass finds pending or expired runs and re-enqueues them idempotently.

- [x] Test history pagination, stale-history recovery, dispatcher pagination, duplicate dispatch, and expired-lease recovery.
- [x] Confirm the tests fail for missing behavior.
- [x] Implement history/dispatch/recovery, verify backend and infrastructure checks, and commit.

### Task 5: Dashboard status and handoff

**Files:** Modify `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/App.tsx`, and focused UI tests; update `docs/DEVELOPMENT.md`, `docs/IMPLEMENTATION_ROADMAP.md`, and `AGENTS.md` only after verification.

**Interfaces:** The owner can start a sync and see the latest run's progress, completion, partial failure, or reconnect-required state. The UI does not claim completion when work was merely queued.

- [x] Write failing UI tests for start, overlap, progress, and failure states; implement the compact controls.
- [x] Run frontend lint, tests, typecheck, build, and audit.
- [x] Run all backend and infrastructure CI checks, request read-only branch review, resolve material findings, and document the exact offline/live verification boundary.
- [x] Commit the clean Milestone 8 handoff; keep deployment and live Gmail tests explicitly pending until performed.

## Open decisions

- The current Users record has no connection timestamp. New connections will record one; for a connection created before this schema, the first run's start time anchors its fixed 90-day import window, which must be visible in the run record.
- The candidate search terms and history response handling should be checked against current official Gmail API documentation before Task 3 implementation.
- The dispatcher uses a paginated Users scan so Gmail connections created before Milestone 8 are included without a migration. A connected-account index remains a scaling improvement if account volume grows.

## Development deployment (2026-10-20)

- [x] Review the CloudFormation change set: no replacements or deletions; adds SyncRuns, SQS worker and dispatcher, schedules, and alarm.
- [x] Deploy the stack and frontend; verify `UPDATE_COMPLETE`, active Lambda functions, enabled schedules and SQS mapping, health 200, protected routes 401 without a token, CORS preflight 204, and hosted frontend 200.
- [x] Verify a real import and repeat/incremental sync with the designated test mailbox; inspect sanitized progress and recovery evidence.

The designated test mailbox's first run used a fixed 90-day boundary of
`2026-07-22T23:09:25.393041+00:00` and completed at
`2026-10-20T23:32:48.126644+00:00` after 1,092 candidate messages. The
history checkpoint was saved and the active-run slot cleared. The immediately
repeated run selected incremental mode, completed with 0 new messages, and
left the stored counts unchanged: 1 application, 809 review-required email
outcomes, 283 ignored email outcomes, and 3 other audit events. The dead-letter
queue remained empty. Both runs were started through the repository and SQS
from an AWS-authenticated operator shell; the browser/API start flow was not part
of this check.

The first run paused in `pending` several times despite an enabled SQS mapping
and no worker errors or throttles in the inspected metrics. The scheduled
15-minute recovery invocation resumed it once; operator-triggered recovery
invocations resumed later pauses. Cause (diagnosed 2026-10-21): Lambda
recursive-loop detection. The worker continues Lambda -> SQS -> same Lambda, and
after about 16 hops in one trace Lambda drops the message. CloudWatch showed 30
`RecursiveInvocationsDropped` on the sync worker (23 in one hour, 7 in the next)
and AWS sent a recursive-loop Health notice. Fixed by setting `RecursiveLoop:
Allow` on the sync worker and account deletion functions. Deployed 2026-10-21; a
fresh 90-day import then completed in about 7 minutes with no dropped invocations
and no recovery-pass intervention (`docs/VERIFICATION.md` L12).
A new-message incremental update, the 09:00 UTC daily start,
revocation/reconnect, dead-letter alarm activation, and live review actions
still need controlled verification.
