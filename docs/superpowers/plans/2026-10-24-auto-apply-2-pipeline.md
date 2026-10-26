# Plan 2: Automatic Pipeline (behind a switch)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn surviving emails into automatic creates and updates of applications, with filters, a "kind" question, deterministic matching, a dry-run plan, an apply action, and undo, all off by default.

**Architecture:**
- **Sync:** sync keeps running in AWS. When the owner's mode is not `off`, emails that pass the hard filters and are not already handled by rules are stored as `pending_ai`.
- **Local model:** the Mac (Plan 3) pulls them from an AI queue, runs Ollama, and posts the extraction. The backend validates it and stores it as an `ai_plan`.
- **Decisions:** a pure `decide()` function and a pure `simulate()` function turn plans into actions in `received_at` order. The plan report and the apply worker use them, and so does the answer-key scorer.
- **Apply and steady state:** apply executes plans in order through one repository method. In mode `on`, new results execute immediately.

**Tech Stack:** FastAPI, DynamoDB/moto, SQS worker, pydantic.

**Spec:** `docs/superpowers/specs/2026-10-24-auto-apply-sheet-design.md` (sections 1-6 and 8; "Calls made"; "What counts as an application").

## October 26 review backlog follow-up

Goal: reduce the 13 ambiguous-match and 9 Rejected-to-Applied reviews seen in development without changing a rejected application to Applied or guessing an application identity.

- [x] Add failing tests for status-free ambiguous mail, older Applied evidence, and a clearly distinct requisition after rejection, including cases that must stay in review.
- [x] Extend owner-scoped candidates with the latest status-event timestamp and adjust only the auto-apply decision rules and in-memory simulation.
- [x] Run focused tests and repository checks, then commit the verified change. Preview existing reviews separately before any account data change.
- [x] Deploy the checked commit to development and verify the hosted app and review counts.

Decision: ignore an ambiguous match when the extraction has no status. Cost if wrong: optional role, cycle, or requisition enrichment from that message is missed, but no status or application identity is invented.

Decision: keep Rejected intact. Ignore an Applied email received before the latest recorded Rejected status event when it has no distinct requisition. Email-written status events use the source email's received timestamp; manual status events use the edit timestamp. A later Applied confirmation with a distinct, globally unused requisition may create a separate application when company and role are explicit and every existing same-company/same-role row has a different recorded requisition. A conflicting requisition outranks a Gmail thread link; identical-subject confirmations can share a thread. Otherwise retain review. Cost if wrong: a manual correction may occur after a newer email, so that email remains ignored as a stale update; an applicant-tracking system could assign two IDs to one application, creating a duplicate. The seven pending confirmations were checked against their source messages, each of which explicitly names a different position number.

Before applying, the read-only development preview in received order showed: 9 status-free ambiguous items to ignore, 4 to retain in review, 7 distinct-requisition confirmations to create as rows, and 2 older Applied messages to ignore. All 22 were unresolved AI-plan reviews with no later review edit.

Development recheck result: the 18 projected automatic outcomes were stored. One of the four remaining messages matched an existing Applied row after removing a `Talent Acquisition Team` suffix from company matching, and it was ignored as unchanged. Two interview-link logistics were resolved as no action after reading the related interview confirmation; neither application changed. The last IBM second-round message names no role and remains in review. The latest read is 297 applications and 89 pending reviews, down from 290 and 110. Do not infer that the other 88 reviews are resolved by this change.

Deployment verification: SAM build and development deploy succeeded for `5f5e481` and `a207746`; the hosted app and API health endpoint returned HTTP 200, and a fresh owner-scoped data read still showed 297 applications and 89 pending reviews.

The frontend lockfile advances the transitive test-only `undici` dependency from 8.11.2 to 8.12.3 so the repository's `npm audit` CI gate passes. The pinned Node 22.23.3 lint, 232 tests, typecheck, build, and audit passed in Docker; local Node 23.7 lacks an API used by `undici` 8.12.3.

## Global Constraints

- The mode is a per-owner Users attribute, `auto_apply_mode`, with values `off` | `plan` | `on`. It defaults to `off`.
  - With `off`, today's behavior is unchanged byte for byte, and all existing tests pass unmodified.
  - The owner can switch between `plan` and `off`. Only `POST /ai-plan/apply` sets `on`.
- Rules run first. A rules `explicit_event` that is `applied` stays as it is today.
- In mode `plan` or `on`, the hosted-provider escalator is not called. Free/local is the owner's choice.
- Status is never defaulted. A row is created only when `kind == application_update`, a company is present, and a status is present, each backed by a validated quote.
- Ordering follows the existing `would_regress`, `email_rules.py:398`. A clear later REJECTED applies after INTERVIEWING, whether that status was set manually or by AI. Reopening REJECTED, WITHDRAWN or OFFER goes to `needs_look`.
- Every change writes an audit event with `origin="ai"` and `source_message_id`. Undo writes an event with `origin="user"`.
- No email body is stored. `subject[:300]` and `sender[:300]` are stored on queue, ignored and needs-look items, as review items already do.
- Owner isolation: every new route and query is scoped to the verified `sub`. Tests cover cross-owner access and stale versions for each owner-scoped write.

## Carried from Plan 1

- `fetch_owner_message(owner_id, message_id)` returns `(ParsedGmailMessage, generation)`. Task 7's input route uses the tuple.
- The answer-key sample-size floors live in `evaluation.answer_key.score.run_cli`. `--pipeline auto` must gate through `run_cli`, not call `score()` and `exit_code()` directly.

## Review Focus

- **Two emails in one backfill for the same new job:** the first creates the row and the second updates it. They must never create two rows. `simulate()` and the apply worker share in-memory or DB candidates in `received_at` order. Covered in Task 5 and Task 8.
- **An email for a job the owner edited by hand after the email arrived:** the older evidence must not overwrite the manual correction. Reuse `field_corrections` and `older_evidence`. Covered in Task 6.
- **Apply pressed twice, or the worker retried mid-page:** each plan executes at most once. Guard with `ai_plan.executed_at` and the `gmail#` condition. Covered in Task 8.
- **Undo after the owner edited the same cell:** the API returns 409 `changed_since` and nothing changes. Covered in Task 9.
- **Turning mode back to `off` during a backfill:** queue routes return 409 `auto_apply_off`, and planned items stay inert. Covered in Task 7.

---

### Task 1: Mode switch and docs

**Files:**
- Create: `backend/app/repositories/dynamodb_auto_apply.py`
- Modify:
  - `backend/app/api/settings.py`: `GET /settings` gains `auto_apply_mode`, and add `PUT /settings/auto-apply` with body `{mode: "off"|"plan"}`. A request with `on` returns 422.
  - `backend/app/api/dependencies.py`.
  - `docs/PRD.md`, sections 4-5: AI may create when the email establishes a stage, and the owner's definition of an application.
  - `docs/TECHNICAL_DESIGN.md`: AI queue, plan, mode, undo.
  - `docs/IMPLEMENTATION_ROADMAP.md`: new milestone.
  - `AGENTS.md` project-status paragraph.
- Test: `backend/tests/test_auto_apply_settings.py`

**Interfaces:**
- Produces:
  - `AutoApplyMode = Literal["off","plan","on"]`.
  - `DynamoAutoApplySettings.get_mode(owner_id) -> AutoApplyMode`.
  - `DynamoAutoApplySettings.set_mode(owner_id, mode) -> None`. It raises `AccountDeleting` when `deletion_state` exists.

- [ ] **Step 1: Write failing tests:**
  - `test_mode_defaults_off`.
  - `test_put_mode_plan_and_off`.
  - `test_put_mode_on_rejected_422`.
  - `test_mode_is_owner_scoped`.
- [ ] **Step 2: Run** `uv run pytest tests/test_auto_apply_settings.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the repository, routes and doc edits.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: pass.
- [ ] **Step 5: Commit** `feat: add per-owner auto-apply mode` and `docs: allow AI-created applications`.

### Task 2: Hard filters

**Files:**
- Create: `backend/app/services/email_filters.py`
- Test: `backend/tests/test_email_filters.py`

**Interfaces:**
- Produces:
  - `FilterReason = Literal["job_alert","digest","newsletter","marketing","event"]`.
  - `filter_reason(message: ParsedGmailMessage) -> FilterReason | None`.
- Patterns are generic service senders and template subjects only, never people's names or addresses. Source them from the answer key's `job_alert` and `newsletter_or_event` labels, reviewed by hand.
- Any sign that the email is about the owner's own application vetoes a filter match. The signals are "your application", "thank you for applying", "interview", "assessment", "offer" and "unfortunately", in the subject or the first 1000 characters of the body.

- [ ] **Step 1: Write failing tests** with synthetic messages:
  - `test_linkedin_job_alert_filtered_as_job_alert`.
  - `test_digest_with_many_roles_filtered`.
  - `test_application_wording_vetoes_filter`: an alert-like sender plus "Thank you for applying" returns None.
  - `test_unknown_sender_not_filtered`: when in doubt, keep the email.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the filters.
- [ ] **Step 4: Run** the tests and `ruff`. Expected: pass.
- [ ] **Step 5: Commit** `feat: add hard filters for non-application mail`.

### Task 3: Kind in the extraction schema and prompt

**Files:**
- Modify:
  - `backend/app/domain/ai.py`: `ApplicationKind` enum, `ExtractionOutput.kind: ApplicationKind` (required), and `EXTRACTION_JSON_SCHEMA` (`kind` required, enum, not nullable).
  - `backend/app/services/ai_extraction.py`: `ValidatedExtraction.kind`, plus the `SYSTEM_PROMPT` sentence below.
  - `backend/evaluation/fixtures/ai.json`: add `kind` to every recorded output.
  - `frontend/src/api/types.ts`: `AiProposal.kind?`.
- Test: `backend/tests/test_ai_extraction.py`, `backend/tests/test_ai_domain.py` (or the existing equivalents)

**Interfaces:**
- Produces: `ApplicationKind(str, Enum)` with values `APPLICATION_UPDATE="application_update"`, `JOB_ALERT="job_alert"`, `RECRUITER_OUTREACH="recruiter_outreach"`, `NEWSLETTER_OR_EVENT="newsletter_or_event"` and `OTHER="other"`.
- `kind` needs no evidence quote. The validator keeps it as is.

Append this exact text to `SYSTEM_PROMPT`:

> Set kind to application_update only when the email reports the status of a job the recipient applied to (applied, assessment, interview, offer, rejection, or withdrawal), even if no earlier confirmation exists; use recruiter_outreach for unsolicited interest in a role the recipient has not applied to, job_alert for job recommendations or listings, newsletter_or_event for newsletters, events, and marketing, and other for anything else.

- [ ] **Step 1: Write failing tests:**
  - `test_extraction_requires_kind`.
  - `test_schema_lists_kind_enum`.
  - `test_prompt_mentions_kind_definition`.
  - `test_validated_extraction_carries_kind`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the change and update the 32 AI fixtures. The hosted providers send the same schema, so check that `tests/test_ai_providers*.py` still pass.
- [ ] **Step 4: Run** `uv run pytest -q && uv run python -m evaluation.run --check`. Expected: pass.
- [ ] **Step 5: Commit** `feat: classify email kind in AI extraction`.

### Task 4: Normalization and matching

**Files:**
- Create: `backend/app/services/application_matching.py`
- Test: `backend/tests/test_application_matching.py`

**Interfaces:**
- Produces:
  - `normalize_company(name: str) -> str` lowercases, collapses whitespace, strips punctuation, and drops a trailing `inc|llc|ltd|corp|corporation|co`.
  - `normalize_role(role: str) -> str` lowercases, removes punctuation, and maps `swe` to `software engineering` and `software engineer` to `software engineering`. It drops `intern|internship|co-op|coop`, season words, and 4-digit years, then collapses whitespace.
  - `match_application(candidates: list[ApplicationCandidate], *, thread_id: str | None, requisition_id: str | None, company: str | None, role: str | None, season: Season | None, year: int | None) -> MatchResult`.
  - `MatchResult = Matched(candidate) | NoMatch() | Ambiguous(count: int)`, as frozen dataclasses.
- Matching order:
  1. Requisition id equality.
  2. Thread id membership.
  3. Normalized company and normalized role equal, with the cycle compatible. A cycle is compatible when each field is None on either side or equal.
  4. When the company matches, the email has no role, and exactly one row exists for that company, return `Matched`. With more than one such row, return `Ambiguous`.
  5. More than one match at any step returns `Ambiguous`.

- [ ] **Step 1: Write failing tests:**
  - `test_swe_intern_equals_software_engineering_intern`.
  - `test_internship_suffix_and_year_ignored`.
  - `test_company_suffix_ignored`.
  - `test_requisition_beats_name`.
  - `test_thread_match`.
  - `test_different_roles_same_company_no_match`.
  - `test_conflicting_cycle_no_match`.
  - `test_roleless_email_matches_single_company_row`.
  - `test_roleless_email_ambiguous_with_two_rows`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the functions.
- [ ] **Step 4: Run** the tests and `ruff`. Expected: pass.
- [ ] **Step 5: Commit** `feat: normalize and match applications`.

### Task 5: Decisions and simulation (pure)

**Files:**
- Create: `backend/app/services/auto_apply.py`, `backend/app/services/plan_report.py`
- Test: `backend/tests/test_auto_apply.py`, `backend/tests/test_plan_report.py`

**Interfaces:**
- Consumes: `match_application` (Task 4), `would_regress`, `ValidatedExtraction` (Task 3).
- Produces:
  - `AutoDecision(action: Literal["create","update","ignore","needs_look"], reason: str, application_id: str | None, fields: dict[str, object])`, frozen. `fields` holds only the supported values to write.
  - `decide(extraction: ValidatedExtraction, *, message_ref: MessageRef, candidates: list[ApplicationCandidate]) -> AutoDecision`. `MessageRef(message_id, thread_id, received_at)`.
  - `PlannedItem(message_id, received_at, extraction, subject, sender)`.
  - `simulate(items: list[PlannedItem], candidates: list[ApplicationCandidate]) -> PlanReport`. It sorts by `received_at` and applies each decision to an in-memory candidate list. A create adds a synthetic candidate whose `application_id` is `"planned:<message_id>"`.
  - `PlanReport(counts: dict[str, int], reasons: dict[str, int], sample: list[PlanLine])`, where `PlanLine(message_id, action, reason, company, role, status, season, year, target)`. The sample holds the first 100 creates and updates.

Decision table, reasons verbatim:

| Condition | Action | Reason |
|---|---|---|
| kind ≠ application_update | ignore | `not_application:<kind>` |
| status_in_subject_only | needs_look | `status_in_subject_only` |
| Match is Ambiguous | needs_look | `ambiguous_match` |
| Matched and `would_regress(current, status)` | needs_look | `status_regression` |
| Matched, all supported fields equal to current | ignore | `no_change` |
| Matched | update | `ai_update` |
| NoMatch with company and status | create | `ai_create` |
| NoMatch without status or company | ignore | `no_status_no_match` |

- A matched update with `status=None` writes only the non-status fields.
- A cycle that conflicts with the matched row's set values gives NoMatch at the matching step, so it creates a separate row. This follows the PRD rule that two cycles stay separate.

- [ ] **Step 1: Write failing tests**, one per table row, plus:
  - `test_status_never_defaulted_on_create`: an extraction with no status never creates.
  - `test_later_rejection_after_interviewing_updates`.
  - `test_reopen_rejected_needs_look`.
  - `test_simulate_second_email_updates_planned_row`: two emails for the same new job give one create and one update.
  - `test_simulate_orders_by_received_at_not_input_order`.
  - `test_simulate_counts_by_action_and_reason`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** the tests and `ruff`. Expected: pass.
- [ ] **Step 5: Commit** `feat: decide and simulate automatic changes`.

### Task 6: Repository execution (create/update/ignore/needs_look)

**Files:**
- Modify: `backend/app/repositories/dynamodb_email.py`
- Test: `backend/tests/test_dynamodb_auto_apply.py`

**Interfaces:**
- Consumes: `AutoDecision` and `MessageRef` (Task 5).
- Produces: `DynamoEmailRepository.execute(owner_id, *, message: MessageRef, subject: str, sender: str, decision: AutoDecision, extraction: ValidatedExtraction, application_version: int | None, new_application_id: str | None, occurred_at: datetime) -> StoredOutcome`.
- **Create:** a transaction with three parts:
  1. A put of the application, with `version=1`, the fields from `decision.fields`, and `field_updated_at` for each field.
  2. A put of the `gmail#` item as the audit event. It has `event_type="created"`, `origin="ai"`, `outcome="applied"`, `reason="ai_create"`, `changes`, `owner_application_id`, `occurred_at_event_id`, `ai_proposal`, `subject` and `sender`.
  3. A `_guard` ConditionCheck.
- **Update:** the same write path as today's `record()` for `applied`, with origin ai. It respects `field_corrections` and `field_updated_at`. Older evidence gives `review_required/older_evidence`, and a stale version gives `review_required/ai_proposal`, as today.
- **Ignore:** `outcome="ignored"`, the reason, `subject` and `sender`.
- **Needs look:** `outcome="review_required"`, `reason="needs_look"`, `rule_reason=decision.reason`, and `ai_proposal`. This lets the existing review resolve flow work unchanged.
- The `gmail#` put condition is: `attribute_not_exists(event_id) OR outcome IN (:pending_ai, :retryable) OR (attribute_exists(ai_plan) AND attribute_not_exists(ai_plan.executed_at) AND attribute_not_exists(resolution) AND attribute_not_exists(undone_at))`. A successful execute sets `ai_plan.executed_at`.

- [ ] **Step 1: Write failing tests:**
  - `test_create_writes_application_and_created_event_atomically`.
  - `test_create_replay_does_not_duplicate`: a second execute for the same message is a no-op returning the stored outcome.
  - `test_update_respects_manual_correction`: older evidence gives `older_evidence`.
  - `test_execute_overwrites_planned_review_item_but_not_resolved_one`.
  - `test_execute_never_touches_undone_item`.
  - `test_guard_failure_raises_stale`.
  - `test_cross_owner_application_id_rejected`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the method, reusing `_guard`, `_outcome_item` and the update builder from `record()`. Refactor the shared pieces into helpers, not copies.
- [ ] **Step 4: Run** `uv run pytest -q`. Expected: pass, with `record()` tests unchanged.
- [ ] **Step 5: Commit** `feat: execute automatic decisions atomically`.

### Task 7: Sync routing and the AI queue

**Files:**
- Modify: `backend/app/services/email_processing.py` (mode-aware routing), `backend/app/services/sync_worker.py` (pass the mode), `backend/app/repositories/dynamodb_email.py` (`record_pending_ai`, `list_ai_queue`, `save_plan`, `mark_backfill`)
- Create: `backend/app/api/ai_queue.py`, `backend/app/services/ai_queue.py`
- Test: `backend/tests/test_ai_queue_api.py`, `backend/tests/test_email_processing_modes.py`

**Interfaces:**
- **Routing** in `process_message(..., mode: AutoApplyMode = "off")`:
  - With `mode == "off"`, behavior is unchanged.
  - Otherwise:
    1. Parse failures stay `unparseable_message`.
    2. A `filter_reason` hit is stored `ignored` with reason `filtered:<reason>`, plus subject and sender.
    3. A rules `applied` result is recorded as today.
    4. Everything else is stored `pending_ai`, with subject and sender, and the escalator is not called.
- **Repository:**
  - `record_pending_ai(owner_id, message)`.
  - `list_ai_queue(owner_id, limit) -> list[dict]` returns items with `outcome=pending_ai` or `ai_backfill=true` that have no `ai_plan`, oldest `received_at` first.
  - `save_plan(owner_id, message_id, *, extraction, model, generation, planned_at)` sets `ai_plan` and removes `ai_backfill`. Its condition is a matching generation through `_guard`, and that no `ai_plan` exists.
  - `mark_backfill(owner_id) -> int` sets `ai_backfill=true` on every `gmail#` item with `outcome=ignored` and `reason=no_event`, or with `outcome=review_required` and no resolution and no `ai_plan`. It skips `unparseable_message`.
- **Routes**, all of which return 409 `auto_apply_off` when the mode is `off`:
  - `POST /ai-queue/backfill` returns `{marked: int}`.
  - `GET /ai-queue?limit=25` returns `{items: [{message_id, received_at}], remaining: int}`.
  - `GET /ai-queue/{message_id}/input` returns the existing `LocalAiInput` shape, built by `build_prompt` with the current candidates. It uses Plan 1's `fetch_owner_message`.
  - `POST /ai-queue/{message_id}/result {generation, model, extraction}` validates with `validate_extraction` and calls `save_plan`. In mode `on`, it then calls `decide` and `execute` immediately. It returns `{planned: true, executed: bool, action: str | null}`.
  - The error codes are the same as `reviews.py`'s local routes: `message_unavailable`, `gmail_connection_unavailable`, `gmail_connection_changed`, `review_changed`, and 422 invalid output.

- [ ] **Step 1: Write failing tests:**
  - `test_mode_off_processing_unchanged`: run the existing `process_message` cases with `mode="off"`.
  - `test_plan_mode_filters_then_queues`.
  - `test_plan_mode_keeps_rules_applied`.
  - `test_plan_mode_does_not_call_escalator`.
  - `test_backfill_marks_no_event_and_unresolved_reviews_only`.
  - `test_queue_lists_oldest_first_without_plans`.
  - `test_result_saves_plan_in_plan_mode_without_executing`.
  - `test_result_executes_in_on_mode`.
  - `test_queue_routes_409_when_off`.
  - `test_queue_routes_owner_scoped`.
  - `test_result_rejects_generation_change`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the routing, repository methods and routes.
- [ ] **Step 4: Run** `uv run pytest -q && uv run python -m evaluation.run --check`. Expected: pass.
- [ ] **Step 5: Commit** `feat: queue emails for local AI and store plans`.

### Task 8: Plan report and apply worker

**Files:**
- Create: `backend/app/api/ai_plan.py`, `backend/app/services/plan_apply.py`
- Modify: `backend/app/sync_handler.py` (handle message `{"mode":"apply_plan","owner_id":...}`), `backend/app/repositories/dynamodb_email.py` (`list_planned(owner_id) -> list[PlannedItem]`, unexecuted only)
- Test: `backend/tests/test_ai_plan_api.py`, `backend/tests/test_plan_apply.py`

**Interfaces:**
- Consumes: `simulate`, `decide` (Task 5), `execute` (Task 6), `list_ai_queue` (Task 7).
- Produces:
  - `GET /ai-plan/report` returns `{analyzed: int, waiting: int, report: PlanReport}`. It calls `simulate(list_planned(owner), current_candidates)`. If the run measures above 10 s on the dev data, move it to the worker and cache the result.
  - `POST /ai-plan/apply` returns 202 `{queued: true}`. It returns 409 `nothing_planned` when there are no plans and 409 `analysis_incomplete` while `waiting > 0`. It sets the mode to `on` and enqueues the job.
  - `apply_plan_page(owner_id, *, page_size=25) -> bool` returns true when it is done. It takes the oldest 25 unexecuted plans, reloads candidates from the DB, and calls `decide` then `execute` for each. It re-enqueues until none remain, and is idempotent through `executed_at`.

- [ ] **Step 1: Write failing tests:**
  - `test_report_counts_match_simulate`.
  - `test_report_sample_shows_planned_creates`.
  - `test_apply_refuses_while_waiting`.
  - `test_apply_sets_mode_on_and_enqueues`.
  - `test_apply_page_executes_in_received_order_and_reuses_new_rows`: email 2 updates the row that email 1 created.
  - `test_apply_page_retry_is_idempotent`.
  - `test_apply_owner_scoped`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the routes and worker.
- [ ] **Step 4: Run** `uv run pytest -q`, then `sam validate --lint` if the template changes. Expected: pass.
- [ ] **Step 5: Commit** `feat: report and apply the AI plan`.

### Task 9: AI changes, undo, and reconsider

**Files:**
- Create: `backend/app/api/ai_changes.py`, `backend/app/services/ai_undo.py`
- Modify: `backend/app/repositories/dynamodb_email.py` (`list_ai_changes`, `undo`)
- Test: `backend/tests/test_ai_changes_api.py`

**Interfaces:**
- `GET /ai-changes?limit=50&cursor=` returns `{items: [AiChange], next_cursor}`, newest `received_at` first.
  - The list includes `gmail#` items with `origin="ai"`, `reason` starting `filtered:` or `not_application:`, or `reason="no_status_no_match"`.
  - `AiChange(message_id, received_at, action: "created"|"updated"|"ignored", reason, application_id, changes, subject, sender, source_url, undone_at)`.
- `POST /ai-changes/{message_id}/undo`:
  - For `updated`, it builds an `ApplicationPatch` of the `before` values with `expected_version=current`. It returns 409 `changed_since` if any current value is not the event's `after`.
  - For `created`, it soft-deletes when `version == 1`. Otherwise it returns 409 `changed_since`.
  - It sets `undone_at` on the `gmail#` item, writes a `user` event, and returns 409 `already_undone` on repeat.
- `POST /ai-changes/{message_id}/reconsider`, for ignored items only:
  - If an `ai_plan` exists, it re-decides with `kind` forced to `application_update` and executes. It returns `{action}`.
  - If the item was filtered without a plan, it sets `ai_backfill=true` and returns 202 `{queued: true}`.

- [ ] **Step 1: Write failing tests:**
  - `test_changes_lists_creates_updates_and_ignores_newest_first`.
  - `test_undo_update_restores_before_values`.
  - `test_undo_update_409_when_cell_changed_since`.
  - `test_undo_create_soft_deletes_and_blocks_recreate_on_replay`.
  - `test_undo_create_409_after_later_edit`.
  - `test_undo_twice_409_already_undone`.
  - `test_reconsider_ignored_with_plan_creates_row`.
  - `test_reconsider_filtered_queues`.
  - `test_ai_changes_owner_scoped`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the routes and services.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: pass.
- [ ] **Step 5: Commit** `feat: list, undo, and reconsider AI changes`.

### Task 10: Score the new pipeline

**Files:**
- Modify: `backend/evaluation/answer_key/score.py` (add `AutoPipeline`), `backend/evaluation/fixtures/` (new synthetic `auto.json` scenarios), `backend/evaluation/run.py` and `thresholds.json` (gate the new scenarios in CI)
- Test: `backend/tests/test_answer_key_score.py`, `backend/tests/test_evaluation.py`

**Interfaces:**
- Consumes: `filter_reason`, `OllamaProvider`, `validate_extraction`, `simulate`.
- Produces:
  - `AutoPipeline(provider)` runs `filter_reason`, then the rules (`classify_message`), then the provider for the rest, then `simulate` from zero applications. It maps `PlanLine` and the ignored or needs-look reasons to `EmailOutcome`.
  - `--pipeline auto`.
  - CI fixture `auto.json` has at least 30 synthetic scenarios, with gates `auto_precision_min: 0.98` and `auto_detection_recall_min: 0.90`. The scenarios cover:
    - alerts and digests;
    - outreach;
    - a portal rejection with no confirmation on file, which creates a row;
    - SWE vs Software Engineering;
    - two cycles at one company;
    - a later rejection after an interview;
    - reopening a rejection;
    - a role-less follow-up.

- [ ] **Step 1: Write failing tests:**
  - `test_auto_pipeline_creates_then_updates_same_job`.
  - `test_auto_pipeline_filters_alert`.
  - `test_evaluation_gates_auto_fixtures`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the scorer changes and fixtures.
- [ ] **Step 4: Run** `uv run pytest -q && uv run python -m evaluation.run --check`. Expected: pass.
- [ ] **Step 5: Commit** `feat: score and gate the automatic pipeline`.
- [ ] **Step 6 (operator):** run `score --pipeline auto --check` on the answer key. Iterate the filters (Task 2), the prompt (Task 3) and the normalization (Task 4) until precision is at least 0.98 and recall at least 0.90. Commit each change separately. Record the counts in the "Results" section of the Plan 1 file.
  - Note: the answer-key report is sample-weighted over a stratified sample, so its precision and recall are not population rates. Stratum-weighting the gate (using `strata.json`) is Plan 2 work.
- [ ] **Step 7 (operator):** full CI-equivalent checks, then deploy dev. With mode `off`, verify unchanged behavior with a `Sync now` that produces no new `pending_ai` items.
