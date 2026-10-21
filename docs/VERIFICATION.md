# Verification

What is proven by automated tests, what has been verified in the controlled
development deployment, and what is still pending. Nothing is labeled verified
unless a dated record exists. A check marked Pending has not been run.

Automated tests use synthetic data only. `backend/tests/test_acceptance_matrix.py`
parses this file in CI: it requires PRD acceptance criteria 1-14 and live checks
L1-L12, and fails when a cited test, evaluation category, or live check no
longer exists.

## Acceptance matrix

Maps each numbered criterion in `docs/PRD.md` section 8 to automated tests
(`file::function`), synthetic evaluation categories (`eval:<category>`, from
`backend/evaluation/fixtures`), and the controlled live checks below.

| # | Criterion | Automated coverage | Live check |
|---|---|---|---|
| 1 | User A cannot reach User B's applications, settings, reviews, syncs, or source references by changing identifiers. | `backend/tests/test_applications_api.py::test_cross_owner_get_returns_not_found`, `backend/tests/test_applications_api.py::test_cross_owner_patch_returns_not_found`, `backend/tests/test_applications_api.py::test_cross_owner_delete_returns_not_found`; `backend/tests/test_dynamodb_repository.py::test_get_does_not_cross_owner_boundary`, `backend/tests/test_dynamodb_repository.py::test_list_is_owner_scoped_and_paginated`, `backend/tests/test_dynamodb_repository.py::test_tampered_cursor_cannot_select_another_owner`; `backend/tests/test_reviews_api.py::test_review_list_and_reject_are_owner_scoped`; `backend/tests/test_dynamodb_reviews.py::test_reject_is_terminal_and_cross_owner_resolution_is_hidden`; `backend/tests/test_dynamodb_sync.py::test_runs_are_owner_scoped`; `backend/tests/test_settings_api.py::test_owner_isolation`; `backend/tests/test_gmail_api.py::test_connection_is_owner_scoped`; `backend/tests/test_dynamodb_email.py::test_message_and_application_keys_are_owner_scoped`; `backend/tests/test_merge.py::test_merge_rejects_self_cross_owner_and_stale_versions`; `backend/tests/test_account_api.py::test_deleting_a_does_not_lock_b`, `backend/tests/test_account_api.py::test_b_delete_only_affects_b_and_no_parameter_selects_an_owner`; `backend/tests/test_deletion_races.py::test_beginning_deletion_does_not_touch_other_owners` | L1 |
| 2 | The initial import excludes mail older than its fixed 90-day boundary; repeat processing creates no duplicate event. | `backend/tests/test_dynamodb_sync.py::test_first_run_uses_connection_boundary_and_overlap_reuses_run`, `backend/tests/test_dynamodb_sync.py::test_import_snapshot_becomes_next_incremental_start`; `backend/tests/test_gmail_fetch.py::test_import_page_has_fixed_epoch_boundary_and_pagination`; `backend/tests/test_sync_worker.py::test_message_older_than_the_import_boundary_is_not_processed`, `backend/tests/test_sync_worker.py::test_interrupted_page_replays_without_skipping_durable_outcomes`, `backend/tests/test_sync_worker.py::test_duplicate_delivery_does_not_process_or_checkpoint_twice`; `backend/tests/test_dynamodb_email.py::test_applied_status_and_outcome_are_one_idempotent_history_event`; `backend/tests/test_email_processing.py::test_processing_is_owner_scoped_idempotent_and_reviewable`; `backend/tests/test_application_tombstone.py::test_replaying_a_recorded_message_after_delete_changes_nothing` | L3, L4 |
| 3 | A September email naming Summer 2027 produces that cycle; an unspecified cycle stays unknown. | `backend/tests/test_email_rules.py::test_multiple_cycles_require_explicit_cycle`, `backend/tests/test_email_rules.py::test_unsupported_cycle_remains_unknown`, `backend/tests/test_email_rules.py::test_year_range_is_not_extracted_as_a_cycle`; `backend/tests/test_dynamodb_email.py::test_explicit_cycle_fills_unknown_fields_without_guessing`; evaluation categories: `eval:cycle_explicit_september`, `eval:cycle_unspecified` | none |
| 4 | Two roles or cycles at one company stay separate unless evidence supports a match. | `backend/tests/test_email_rules.py::test_company_alone_does_not_select_among_roles`, `backend/tests/test_email_rules.py::test_shorter_role_is_not_matched_inside_longer_role`, `backend/tests/test_email_rules.py::test_multiple_cycles_require_explicit_cycle`; `backend/tests/test_ai_reconcile.py::test_same_company_different_role_stays_separate`, `backend/tests/test_ai_reconcile.py::test_same_company_role_different_cycle_stays_separate`, `backend/tests/test_ai_reconcile.py::test_two_matching_applications_go_to_review`; evaluation categories: `eval:multi_role_company`, `eval:multi_cycle_company`, `eval:ambiguous_identity` | none |
| 5 | Clear application, assessment, interview, offer, and rejection fixtures produce the expected status without an AI call. | `backend/tests/test_email_rules.py::test_explicit_status_rules_require_no_ai`; `backend/tests/test_ai_escalation.py::test_other_decisions_never_call_provider`; `backend/tests/test_ai_pipeline.py::test_rule_only_items_are_unchanged_in_shape`; `backend/tests/test_evaluation.py::test_runner_passes_committed_thresholds`; evaluation categories: `eval:clear_linked`, `eval:wording_variant`, `eval:requisition_link` | none |
| 6 | Ambiguous fixtures use only the selected user's configured provider; unresolved cases reach review without an unsupported update. | `backend/tests/test_ai_escalation.py::test_owner_key_and_counter_are_never_shared`, `backend/tests/test_ai_escalation.py::test_each_allowlisted_reason_calls_provider_once`, `backend/tests/test_ai_escalation.py::test_prompt_candidates_come_only_from_callers_applications`; `backend/tests/test_ai_pipeline.py::test_proposal_only_reason_stores_ai_proposal_review`, `backend/tests/test_ai_pipeline.py::test_instruction_derived_status_is_nulled_and_never_applied`, `backend/tests/test_ai_pipeline.py::test_body_named_other_application_is_not_mutated`; `backend/tests/test_ai_reconcile.py::test_null_status_cannot_apply_and_is_not_defaulted`, `backend/tests/test_ai_reconcile.py::test_high_impact_ai_status_goes_to_review_with_match`; `backend/tests/test_evaluation.py::test_runner_passes_committed_thresholds`; evaluation categories: `eval:ai_proposal_only`, `eval:ai_unsupported_evidence`, `eval:ai_injection`, `eval:ai_wrong_application` | L10 |
| 7 | Missing or invalid keys and provider failures preserve deterministic processing and expose actionable review and error states. | `backend/tests/test_ai_pipeline.py::test_invalid_key_page_keeps_rules_and_stops_ai`, `backend/tests/test_ai_pipeline.py::test_exhausted_budget_page_keeps_rules`, `backend/tests/test_ai_pipeline.py::test_provider_timeout_page_retries_once_then_breaker`, `backend/tests/test_ai_pipeline.py::test_malformed_output_goes_to_review_with_rule_reason`; `backend/tests/test_ai_escalation.py::test_invalid_key_marks_key_invalid_and_stops_page`, `backend/tests/test_ai_escalation.py::test_exhausted_budget_leaves_messages_in_review`; `backend/tests/test_settings_api.py::test_replacing_key_resets_status_and_bumps_version`; evaluation categories: `eval:ai_provider_error`, `eval:ai_invalid_output` | L10 |
| 8 | Older or replayed evidence does not undo a manual correction; a clear later rejection can; ambiguous ordering goes to review. | `backend/tests/test_dynamodb_email.py::test_old_email_cannot_undo_manual_status_correction`, `backend/tests/test_dynamodb_email.py::test_later_clear_email_can_update_manually_corrected_status`, `backend/tests/test_dynamodb_email.py::test_older_email_can_fill_unknown_cycle_after_unrelated_role_edit`; `backend/tests/test_email_rules.py::test_ordering_is_checked_per_field_at_persistence_and_terminal_reopening_requires_review`, `backend/tests/test_email_rules.py::test_known_stage_cannot_regress_to_applied`; `backend/tests/test_merge.py::test_merge_keep_choice_is_audited_and_blocks_older_replay`, `backend/tests/test_merge.py::test_merge_carries_equal_field_corrections_into_replay_guard`; evaluation categories: `eval:manual_correction_replay`, `eval:chronological_conflict`, `eval:terminal_reopen` | none |
| 9 | A scheduled sync completes with no browser open; failed batches resume without duplicated changes. | `backend/tests/test_sync_dispatch.py::test_daily_dispatch_pages_connected_owners_and_reuses_active_run`, `backend/tests/test_sync_dispatch.py::test_recovery_pages_pending_and_expired_runs_without_restarting_completed`; `backend/tests/test_sync_handler.py::test_transient_failure_returns_batch_failure_without_releasing_run`, `backend/tests/test_sync_handler.py::test_exhausted_delivery_marks_partial_and_retries_to_dead_letter`; `backend/tests/test_sync_worker.py::test_interrupted_page_replays_without_skipping_durable_outcomes`, `backend/tests/test_sync_worker.py::test_incremental_history_checkpoint_waits_for_final_page`; `backend/tests/test_dynamodb_sync.py::test_expired_lease_can_be_reclaimed_and_old_holder_cannot_checkpoint` | L3, L4, L5, L12 |
| 10 | Gmail disconnection stops queued work from reading mail; account deletion stops queued work from restoring data. | `backend/tests/test_sync_worker.py::test_disconnected_run_does_not_fetch_gmail`; `backend/tests/test_dynamodb_sync.py::test_disconnect_prevents_checkpoint_and_completion`; `backend/tests/test_dynamodb_email.py::test_disconnected_run_cannot_commit_email_outcome`; `backend/tests/test_sync_handler.py::test_revoked_or_stale_work_is_acknowledged`; `backend/tests/test_gmail_api.py::test_disconnect_during_consent_cannot_reconnect`; `backend/tests/test_deletion_races.py::test_worker_commit_after_begin_writes_nothing`, `backend/tests/test_deletion_races.py::test_claim_after_begin_is_stale`, `backend/tests/test_deletion_races.py::test_sync_handler_acknowledges_job_after_begin`, `backend/tests/test_deletion_races.py::test_reserve_request_after_begin_is_rejected`; `backend/tests/test_account_deletion.py::test_full_deletion_leaves_only_tombstone`; `backend/tests/test_account_api.py::test_stale_token_after_completion_cannot_recreate_data`, `backend/tests/test_account_api.py::test_delete_then_deleter_removes_a_and_leaves_b`; `backend/tests/test_application_tombstone.py::test_account_deletion_removes_every_tombstone` | L6, L7 |
| 11 | Corrections and review decisions keep an audit history linked to the originating email. | `backend/tests/test_dynamodb_repository.py::test_create_writes_application_and_history`, `backend/tests/test_dynamodb_repository.py::test_update_clears_explicit_null_and_records_only_changed_fields`; `backend/tests/test_dynamodb_reviews.py::test_accept_updates_application_and_links_email_to_history`; `backend/tests/test_dynamodb_email.py::test_applied_status_and_outcome_are_one_idempotent_history_event`; `backend/tests/test_merge.py::test_merge_preserves_both_histories_and_redirects_old_id`; `backend/tests/test_application_tombstone.py::test_review_decisions_on_a_deleted_application_change_nothing` | none |
| 12 | Repository and build outputs contain no real credentials or user email data. | `backend/tests/test_repository_hygiene.py::test_tracked_files_contain_no_credential_shaped_strings`, `backend/tests/test_repository_hygiene.py::test_scanner_fires_for_each_pattern`; `backend/tests/test_logging_sanitization.py::test_all_logging_goes_through_log_event`, `backend/tests/test_logging_sanitization.py::test_escalator_exception_message_never_logged` | L9 |
| 13 | A user can merge two of their applications; both histories stay, replays do not recreate the duplicate, and another user's records cannot be merged. | `backend/tests/test_merge.py::test_merge_preserves_both_histories_and_redirects_old_id`, `backend/tests/test_merge.py::test_merge_requires_explicit_choices_for_conflicting_fields`, `backend/tests/test_merge.py::test_merge_rejects_self_cross_owner_and_stale_versions`, `backend/tests/test_merge.py::test_replayed_email_for_merged_id_cannot_recreate_duplicate`, `backend/tests/test_merge.py::test_merge_api_enforces_owner_and_payload`; `backend/tests/test_dynamodb_reviews.py::test_attach_to_merged_id_uses_retained_application` | none |
| 14 | An email with a company and role but no supported stage creates a review item, not an assumed Applied record; a matched application keeps its status. | `backend/tests/test_email_rules.py::test_unknown_stage_goes_to_review_without_inventing_applied`, `backend/tests/test_email_rules.py::test_known_application_without_stage_stays_reviewable_with_multiple_roles`; `backend/tests/test_dynamodb_email.py::test_ignored_and_review_outcomes_are_persisted_without_mutation` | none |

Coverage notes:

- Criterion 2: the worker-side skip of mail older than the boundary is covered
  by `test_message_older_than_the_import_boundary_is_not_processed`, added during
  Milestone 10 after a mutation check showed no earlier test exercised it.
- Criterion 5 and the evaluation categories are synthetic and recorded; they
  validate intended behavior, not real-inbox accuracy.
- Criteria 6 and 7 are exercised offline against provider stubs only. No live
  provider call has been made (L10).
- Criterion 9 is automated for dispatch, resume, and idempotence. A deployed
  scheduled run with no browser open is partly verified (L5), and completing an
  import without recovery-pass help awaits L12.
- Criterion 10: Gmail disconnection is automated. Account deletion is automated
  against moto and stubs only; it has never run against live Cognito, Google, or
  DynamoDB (L7, owner-run).
- Criterion 12: the scan covers tracked files and log output. Built frontend
  assets are not scanned separately; they are produced from tracked sources.
- Criterion 7's "actionable error states" in the browser UI are covered by the
  frontend Vitest suite, which this guard does not parse.

## Classification quality

Run `cd backend && uv run python -m evaluation.run` for the current report; CI
runs it with `--check` against `backend/evaluation/thresholds.json`.

On the committed synthetic fixtures (97 rules scenarios, 32 AI scenarios,
2026-10-21) automatic-update precision now meets the PRD 98% target for rules
and for all three AI stub providers: rules 100% (27/27), OpenAI 100% (6/6),
Anthropic 100% (8/8), Gemini 100% (7/7). Detection recall is 98.6% (70/71) for
rules and 96.9% (31/32) for each AI path, above the 90% target. The AI samples
are only 6-8 automatic updates each, so their intervals are wide.

**This is synthetic evidence only.** The fixtures were written and labeled by the
agent and the provider outputs are recorded stubs. Real-inbox precision and
recall are unmeasured (L11), as is Gmail candidate-search recall. See
`docs/superpowers/plans/2026-10-20-harden-evaluate.md` for the fixes made and
the owner-review items.

## Live checklist

Status values: Verified, Partly verified, Pending. "Evidence" cites the plan or
doc where the result is recorded. Agents never run L6 or L7.

| ID | Check | Who runs it | Status | Evidence |
|---|---|---|---|---|
| L1 | Google sign-in, approved-group gate, and two-account isolation in the hosted app | Owner-run for isolation; agents cannot sign in interactively | Partly verified | Sign-in and the manual tracker were verified in Milestone 4 (`2026-10-17-hosted-auth-deployment.md`). Only one owner exists in development, so cross-owner isolation is proven offline only |
| L2 | Separate Gmail consent, connect, mismatched mailbox rejected, disconnect clears status | Consent is owner-run; an agent can read status | Partly verified | Connect verified in Milestone 5 (`2026-10-18-gmail-connection.md`, hosted verification update 2026-10-18). Re-check after the Milestone 10 deploy by reading status only |
| L3 | 90-day import of the designated test mailbox | Done | Verified | 2026-10-20: fixed boundary 2026-07-22T23:09:25Z, completed 23:32:48Z after 1,092 candidate messages, history checkpoint saved, active-run slot cleared (`2026-10-20-resilient-sync.md`) |
| L4 | Repeat sync creates no duplicates | Agent, non-destructive: invoke the dispatcher in `daily` mode, compare Events and Applications counts and the application id/version hash before and after | Verified | 2026-10-20: the repeat incremental run added 0 messages and left counts unchanged (1 application, 809 review-required, 283 ignored, 3 other events). 2026-10-21 Milestone 9 post-deploy daily-mode run processed 5 new messages with the application id/version hash unchanged (`2026-10-20-byok-ai-escalation.md`). Re-run after the Milestone 10 deploy is pending |
| L5 | Scheduled 09:00 UTC run with no browser open | Agent, read-only: after the next 09:00 UTC read dispatcher logs and the new run record | Partly verified | Manual `daily`-mode invocations (2026-10-20, 2026-10-21) and the scheduled 15-minute recovery are verified. An actual 09:00 UTC firing has not been observed |
| L6 | Revocation recovery: revoke the app at Google, next sync shows reconnect-required, reconnect restores sync | Owner-run (changes the live connection); agents never run it | Pending | Offline only: `test_dynamodb_sync::test_revoked_token_marks_connection_for_fresh_consent` |
| L7 | Account deletion on a separate test identity: zero rows in all five tables for that owner, Cognito user gone, Google permission removed, tombstone present then expiring, a still-valid token gets 410 and writes nothing, the other identity unchanged, deletion queue and DLQ empty | Owner-run, or an owner-created test identity; agents never run it | Pending | Offline only (moto and stubs): `test_account_deletion`, `test_deletion_races`, `test_account_api` |
| L8 | Dead-letter and error alarms fire and reach the optional email | Agent after owner approval of an AWS write: send one synthetic message to the sync DLQ, confirm ALARM, purge it. Email delivery needs the owner to confirm the SNS subscription | Pending | The DLQ alarm exists; activation has not been observed. New alarms and `AlarmNotifications` (default off) are template-tested offline only. Alarm email delivery is unverified |
| L9 | Log hygiene: 14-day retention on every log group and no email text, keys, or tokens in recent logs | Agent, read-only | Partly verified | API log group retention checked in Milestone 4; worker logs scanned clean 2026-10-20; no WARNING/ERROR/Traceback in worker logs for the 30 minutes before the 2026-10-21 post-deploy check. Retention on the new deletion log group is checked after deploy |
| L10 | Live AI provider checks with the owner's own key and model IDs | Owner-supplied key; results never enter CI or Git | Pending | Pending since Milestone 9: no model IDs are configured, so no live provider call has been made |
| L11 | Held-out real-inbox quality sample: precision and recall, including non-application mail and candidate-search misses | Owner-supplied authorized sample, kept outside the repository | Pending | Not started. Synthetic results do not establish real-inbox accuracy |
| L12 | A full import completes without recovery-pass intervention | Owner decision: needs a fresh import or rescan of the designated mailbox; agent observes run records and the `RecursiveInvocationsDropped` metric | Pending | The first live import paused repeatedly because Lambda recursive-loop detection dropped 30 worker continuations (`2026-10-20-resilient-sync.md`). `RecursiveLoop: Allow` (commit `627103a`) should resolve this once deployed; it is not yet deployed or observed |

## Owner follow-ups

Out of scope for Milestone 10 and needed before unrestricted onboarding:

- Public-launch privacy policy and deletion pages.
- Google restricted-scope verification and any security assessment.
- Limited Use review, including AI-provider data handling.
- Verified OAuth domain and redirects.
- Provider data-use terms review.
- Testing-mode seven-day refresh-token expiry.
- Backups and their expiry, if ever enabled.
- Budget and concurrency settings for launch.
- Review of the status cue lexicon before any live key is enabled.
- Decide whether Gmail disconnect should also revoke the Google grant (today only account deletion revokes).
- Decide whether to scrub a deleted application's history before account deletion; there is no application-delete control in the UI yet.
