# Review Queue Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove proven false-positive reviews from the live import and make remaining reviews identifiable and manageable.

**Architecture:** Require an actual application event before a many-role email enters review solely for role conflict. Store only sender and subject headers with review outcomes and expose them to the owner. A one-time operator command re-reads existing pending messages, conditionally changes only corrected-rule `ignored` outcomes, and backfills headers for retained reviews.

**Tech Stack:** Python 3.12/FastAPI/boto3/moto; React/TypeScript/Vitest; AWS DynamoDB and Gmail API.

**Spec:** `docs/PRD.md` review queue and deterministic-rules sections; the owner's approved cleanup choice on 2026-10-20.

## Global Constraints

- No real email content, sender, subject, tokens, or message IDs in Git, logs, or test fixtures.
- Preserve owner isolation, review decisions, and the source-link/audit trail.
- An uncertain, unavailable, or newly classified actionable message remains pending; cleanup never creates or updates an application.
- Do not process Gmail after the connection generation changes.

## Review Focus

- A many-role job listing with no application event should be ignored; an actual event with conflicting roles should remain reviewable.
- A review item without a subject or sender still renders a useful fallback and link.
- A resolved or concurrently changed review must not be overwritten by cleanup.
- An unavailable Gmail message must remain pending.
- The queue should show a bounded first page rather than hundreds of expanded cards.

---

### Task 1: Stop no-event role conflicts from entering review

**Files:** `backend/app/services/email_rules.py`, `backend/tests/test_email_rules.py`.

**Interfaces:** `classify_message(message, applications) -> RuleDecision` retains its signature.

- [x] Add synthetic tests for a many-role listing with no stage and for a real event with two conflicting roles; verify the first fails on current code.
- [x] Move the no-event decision before role-conflict handling, preserving review for a linked/clearly identified uncertain application.
- [x] Run focused and full backend tests plus Ruff; commit the verified change.

### Task 2: Give reviews context and a bounded display

**Files:** `backend/app/repositories/dynamodb_email.py`, `backend/app/services/email_processing.py`, `backend/app/domain/reviews.py`, `backend/app/repositories/dynamodb_reviews.py`, `frontend/src/api/types.ts`, `frontend/src/reviews/ReviewQueue.tsx`, their focused tests.

**Interfaces:** `ReviewItem` gains nullable `subject` and `sender`; only review outcomes persist these headers. The UI shows them with fallback labels and initially displays 20 items, with an explicit way to show more.

- [x] Add failing repository/API and UI tests for headers, missing-header fallback, and bounded display.
- [x] Persist and expose bounded headers; show them and specific reason copy in the review list.
- [x] Run focused and full backend/frontend checks; commit the verified change.

### Task 3: Recheck the existing live queue safely

**Files:** `backend/scripts/recheck_reviews.py`, `backend/tests/test_recheck_reviews.py`, `docs/DEVELOPMENT.md`.

**Interfaces:** Operator command defaults to preview. Apply mode conditionally changes only a pending item's outcome when a fresh corrected-rule decision is `ignored`; all other items remain pending. It backfills bounded headers when available and prints aggregate counts only.

- [ ] Add failing synthetic tests for ignored, ambiguous, unavailable, resolved, and connection-generation-change cases.
- [ ] Implement the command and test it with moto and a fake Gmail fetcher; commit.
- [ ] Preview the designated mailbox and inspect aggregate counts and pending-run state.
- [ ] Apply the reviewed cleanup; verify new reason counts, preserved audit items, and zero unintended application changes.

### Task 4: Deploy and verify

**Files:** deployment and plan status only.

- [ ] Run backend lint/tests, frontend lint/tests/typecheck/build, SAM lint, offline deploy tests, and `git diff --check`.
- [ ] Deploy the reviewed code to development, verify stack and hosted asset, and re-read owner-scoped aggregate review/application counts.
- [ ] Record the measured results and limitations; commit with a clean worktree.
