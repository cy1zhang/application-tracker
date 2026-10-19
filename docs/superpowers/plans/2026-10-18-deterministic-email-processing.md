# Deterministic Email Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a synthetic Gmail `format=full` message into a safe, owner-scoped, idempotent rules outcome without AI or live inbox synchronization.

**Architecture:** Decode bounded inline MIME text, then classify only explicit recruiting events. Match an existing application only with a unique existing thread or requisition link, plus exact company-and-role evidence with no cycle conflict. Save a deterministic message outcome in the existing Events table; a rule-applied status change and its outcome are one DynamoDB transaction. A later sync worker will supply messages and candidate applications; this milestone adds no Gmail fetch, queue, schedule, UI, or model hosting.

**Tech Stack:** Python 3.12 standard library, FastAPI backend domain models, boto3/DynamoDB, pytest/moto.

**Spec:** `docs/IMPLEMENTATION_ROADMAP.md` milestone 6; `docs/PRD.md` sections 4–5; `docs/TECHNICAL_DESIGN.md` classification and synchronization sections.

## Global Constraints

- Only synthetic mail in fixtures and tests. Never persist full raw email bodies, log message text, or call an AI provider.
- Unknown company, role, status, season, and year stay unknown. Company alone never selects an application.
- Exclude attachments and remote HTML resources. Bound decoded content before classification.
- Outcomes are keyed by authenticated owner plus Gmail message ID. A duplicate delivery must not create a second mutation or review item.
- Ambiguous identity, multiple competing stages, older evidence, and terminal-state reopening require review rather than an automatic mutation.
- The approved preference for a future open-weight model changes no Milestone 6 behavior.

## Review Focus

- Nested MIME, malformed base64url, and attachment parts cannot leak executable HTML or unbounded content (Task 1 tests).
- Marketing and hypothetical language must not turn into application events (Task 2 tests).
- Same-company roles or cycles must not select the wrong application (Task 2 tests).
- Duplicate messages and racing writes must not duplicate an application update (Task 3 tests).
- One owner's message ID and application ID cannot affect another owner (Task 3 tests).

---

### Task 1: Parse bounded Gmail message text

**Files:** Create `backend/app/services/gmail_message.py` and `backend/tests/test_gmail_message.py`. Keep the small synthetic message builders in the focused tests.

**Interfaces:** `parse_gmail_message(message: dict) -> ParsedGmailMessage` with `message_id`, `thread_id`, `received_at`, `subject`, `sender`, and `text`. Accept Gmail `format=full` payloads with inline base64url `text/plain` or `text/html`; prefer plain text, strip HTML to visible text, ignore attachments, and cap decoded text at 16 KiB. Reject missing IDs/date, bad encoding, or no usable inline text with `MessageParseError`.

- [x] Write synthetic fixtures and tests for plain text, nested alternative parts, HTML fallback, attachment exclusion, quoted-reply stripping, invalid base64, and oversized content.
- [x] Run `uv run pytest tests/test_gmail_message.py -q`; confirm failure because the parser is absent.
- [x] Implement the parser with Python standard-library MIME/base64/HTML utilities and no network reads.
- [x] Run targeted tests and `uv run ruff check .`; confirm pass.

### Task 2: Classify and match conservatively

**Files:** Create `backend/app/services/email_rules.py` and `backend/tests/test_email_rules.py`; use the synthetic fixtures from Task 1.

**Interfaces:** `classify_message(message: ParsedGmailMessage, applications: list[ApplicationCandidate]) -> RuleDecision`. `ApplicationCandidate` carries an existing application plus known Gmail thread IDs and requisition IDs. `RuleDecision` has `outcome` (`ignored`, `review_required`, or `applied`), a nullable application ID/status, and a short reason. “Applied” means eligible for the repository's guarded mutation, not yet persisted.

- [x] Write tests for explicit Applied, OA, Interviewing, Offer, and Rejected messages; newsletters, job ads, hypothetical text, unknown status, and conflicting stages.
- [x] Write tests for unique thread/requisition match with company-and-role evidence, unlinked and company-only messages, two roles/cycles at one company, and cycle extraction only when explicit.
- [x] Write tests for older evidence and terminal-state reopening going to review.
- [x] Run `uv run pytest tests/test_email_rules.py -q`; confirm failure for the absent rules.
- [x] Implement the minimum evidence phrases, matching rules, and decision types; do not infer a date, cycle, or stage.
- [x] Run targeted tests and `uv run ruff check .`; confirm pass.

### Task 3: Persist idempotent outcomes and guarded mutations

**Files:** Create `backend/app/repositories/dynamodb_email.py`, `backend/tests/test_dynamodb_email.py`; modify only the existing application repository if needed for shared atomic transaction behavior.

**Interfaces:** `DynamoEmailRepository.record(owner_id, message, decision, application_version=None) -> StoredOutcome`. Store an outcome at `event_id = gmail#<message_id>` in Events, with only message/thread IDs, received time, bounded evidence excerpt, status/reason, and optional application ID. `ignored` and `review_required` use a conditional put. `applied` conditionally updates the owner-scoped application and writes an `updated` history event in one transaction. On duplicate, return the existing outcome; on a missing/stale application, persist `retryable` (version race) or `review_required` (missing/deleted) without silently mutating. Never overwrite a terminal outcome.

- [x] Write moto tests for applied event plus version bump, duplicate delivery, owner isolation, ignored/review persistence, stale writes, and no raw-body storage.
- [x] Run `uv run pytest tests/test_dynamodb_email.py -q`; confirm failure for the absent repository.
- [x] Implement conditional writes and outcome reads with strongly consistent owner-scoped keys.
- [x] Run targeted tests, full backend tests, and `uv run ruff check .`; confirm pass.

### Task 4: Wire the offline processing boundary and handoff

**Files:** Create `backend/app/services/email_processing.py` and `backend/tests/test_email_processing.py`; update this plan and the relevant development documentation.

**Interfaces:** `process_message(owner_id, gmail_message, applications, repository) -> StoredOutcome` invokes parser, rules, and persistence in that order. The caller supplies candidate records and an owner derived from verified authentication or the future worker's connection record; the Gmail payload never chooses an owner. Malformed content yields a persisted retryable or review outcome when the message ID is available, without leaking the body.

- [x] Write an end-to-end synthetic test that processes a clear message twice, an ambiguous message, and the same Gmail ID for a second owner.
- [x] Run `uv run pytest tests/test_email_processing.py -q`; confirm failure for the absent service.
- [x] Implement the small orchestration function and document the future Milestone 8 worker handoff.
- [x] Run backend lint/tests and `git diff --check`; confirm pass.
- [x] Commit the completed milestone as a focused implementation commit, then request read-only branch review before any merge.

## Open decisions

- Read-only review found misleading quoted or forwarded text, inline attachments, competing roles and cycles, unrelated cycle promotions, negated or future event wording, company names outside the event, and unlinked messages claiming a status. Focused regression tests now require an affirmative body event that names the exact role and directly identifies the company, plus a unique existing thread or requisition link; a cycle is recorded only for `for <role> in <season year>`. Forwarded, subject-only, and unlinked events go to review. The final correction is awaiting read-only recheck.
- No live Gmail read or deployment is part of this milestone. The user asked to reduce manual live testing; synthetic offline tests are the verification gate here.
- Until Milestone 7 adds field-specific correction timestamps, the processor conservatively sends evidence older than the application's last edit to review, even when that edit changed an unrelated field. This can reduce automatic coverage but prevents an older email from undoing a manual correction.
- The future AI provider choice will be revisited around Milestone 9 for the user's open-weight preference. No model infrastructure is added now.
