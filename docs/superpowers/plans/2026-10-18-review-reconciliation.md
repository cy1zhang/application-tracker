# Review and Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an owner resolve ambiguous email outcomes and merge duplicate applications without losing history or letting replay undo manual corrections.

**Architecture:** Keep review outcomes in the existing Events table and list unresolved owner-scoped `gmail#` items with pagination. Resolve a review and any application mutation in one DynamoDB transaction with optimistic versions. Track the last change time per application field; keep merged records as owner-scoped tombstones and show their histories under the retained record. Add a compact review queue and merge flow to the existing React dashboard.

**Tech Stack:** Python 3.12/FastAPI, boto3/DynamoDB, pytest/moto; React/TypeScript/Vite/Vitest.

**Spec:** `docs/IMPLEMENTATION_ROADMAP.md` Milestone 7, `docs/PRD.md` sections 3–5, and `docs/TECHNICAL_DESIGN.md` API boundaries and classification/reconciliation sections.

## Global Constraints

- Derive owner from verified auth; never accept an owner in a request body.
- Do not store or return raw email bodies, credentials, or real email in fixtures.
- Unknown status/cycle remains null; a review-created application may have no status.
- Every resolution and merge is idempotent or guarded by expected versions, and creates an audit trail.
- Do not add Gmail fetch, scheduled sync, AI providers, or new infrastructure in this milestone.

## Review Focus

- A second owner must not see or resolve another owner's review or applications.
- Repeated or racing resolution must not repeat mutations or reopen a rejected item.
- An old email must not change a field after a manual correction, while a later clear email may.
- Merge requires explicit choices for conflicting fields and cannot lose either history.
- Replaying evidence or accessing a merged ID must not recreate a duplicate.

---

### Task 1: Field-level correction time and replay protection

**Files:** Modify `backend/app/repositories/dynamodb.py`, `backend/app/repositories/dynamodb_email.py`, `backend/app/services/email_rules.py`; add focused repository tests.

**Interface:** Store `field_updated_at` and `field_corrections` maps on application items. User updates set both for changed fields; rule updates set `field_updated_at` using email receipt time. Compare each proposed changed field against its own timestamp. Legacy items use their last known update time until first touched. Keep `updated_at` as the latest activity time.

- [x] Write failing tests for old replay after status correction, later clear status evidence, and old cycle evidence after unrelated role edit.
- [x] Implement field timestamps and remove the classifier's whole-record `updated_at` veto.
- [x] Run focused tests and backend lint; commit the verified backend step.

### Task 2: Owner-scoped review queue and resolution API

**Files:** Create `backend/app/domain/reviews.py`, `backend/app/repositories/dynamodb_reviews.py`, `backend/app/api/reviews.py`; update `backend/app/api/dependencies.py` and `backend/app/main.py`; add repository/API tests.

**Interface:** `GET /reviews?limit=&cursor=` returns pending review items and an opaque cursor. `POST /reviews/{message_id}/resolve` accepts one of accept/edit/reject/attach/create with action-specific fields and an expected application version where needed. Resolve the existing `gmail#` outcome atomically with application creation/update/linking. A rejected item stays terminal; accept/edit use only explicit proposed or user-supplied fields. Include a Gmail thread link built from stored thread ID, never email content.

- [x] Write failing moto/API tests for pagination, every action, stale versions, duplicate resolution, and owner isolation.
- [x] Implement typed input/output, repository transactions, and API exception mapping.
- [x] Run focused tests and backend lint; commit the verified backend step.

### Task 3: Explicit duplicate merge and history preservation

**Files:** Modify `backend/app/domain/applications.py`, `backend/app/repositories/applications.py`, `backend/app/repositories/dynamodb.py`, `backend/app/api/applications.py`; add merge repository/API tests.

**Interface:** `POST /applications/{keep_id}/merge` names the duplicate, both expected versions, and a choice for each conflicting field. Combine compatible nullable fields, require choices for conflicting values, atomically update the retained record and a `merged_into` tombstone, and write a merge event. Resolve merged IDs for reads and review attachment; include both event histories in retained history. Never automatically merge distinct roles/cycles.

- [x] Write failing tests for happy path, explicit conflict choices, stale/self/cross-owner merges, merged-ID reads, combined history, and replay protection.
- [x] Implement the guarded transaction, redirect, and history query.
- [x] Run focused tests and backend lint; commit the verified backend step.

### Task 4: Review and merge controls in the dashboard

**Files:** Modify `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/App.tsx`, `frontend/src/applications/ApplicationDetail.tsx`, and `frontend/src/styles.css`; create focused components/tests under `frontend/src/reviews/`.

**Interface:** Show pending review count and list with reason, proposal, and source link. Each review offers accept, edit, reject, attach, and create actions with version-conflict reload. Application detail offers a manual merge form that previews field differences and requires a choice for every conflict. Refresh application/review lists after successful actions.

- [x] Write failing UI tests for list/action states, validation, stale conflict, and merge preview.
- [x] Implement the smallest controls that satisfy the tests and current visual style.
- [x] Run frontend lint, tests, typecheck, build, and audit; commit the verified UI step.

### Task 5: Verification and handoff

**Files:** Update `docs/DEVELOPMENT.md`, `docs/IMPLEMENTATION_ROADMAP.md`, and `AGENTS.md` only after implementation is verified.

- [ ] Run all backend/frontend CI commands, SAM lint, offline deploy tests, and `git diff --check`.
- [ ] Request read-only branch review against `main`; fix material findings and rerun affected checks.
- [ ] Document remaining live-sync boundary, mark Milestone 7 complete offline, and commit a clean handoff.

## Open decisions

- The existing Events table suffices for the development queue; if real sync volume later makes owner-partition filtering costly, revisit indexing in Milestone 8 with measurements.
- Manual review creation permits null status to preserve unknown stage; ordinary manual creation keeps its current required status.
- A merged duplicate remains a tombstone pointing to the retained record. New evidence for an old ID must resolve to the retained record or require review; it must never recreate the duplicate.
