# Handoff to Claude Code — review queue repair

**Date:** 2026-10-20 (America/Indiana/Indianapolis)

**Branch:** `codex/milestone-8-resilient-sync`
**Status:** Work in progress. Do not treat the review queue repair as tested, deployed, or complete.

## User intent and authorization

The user opened the development app after a real Gmail import and objected to 809 nearly identical “Needs review” cards, a blank “Create from email” form, and a “Sync complete (0 messages processed)” banner. Explain that the 0 refers to the **repeat** incremental run; the first import processed 1,092 candidate messages. AI extraction is not implemented yet (Milestone 9), so this is still a rules-only classifier.

The user selected **“Yes, fix and clean up”**: fix the false-positive rule, re-read existing pending messages from the designated connected Gmail mailbox, clear only items the corrected rule classifies as irrelevant, preserve uncertain items, and add sender/subject context to reviews. This authorizes the cleanup; do not ask the user to approve the same action again. Do not bulk reject or create applications. The user is switching chats and asked for this handoff.

## Verified diagnosis

- The development import completed with 1,092 candidate messages, 809 `review_required` outcomes, 283 `ignored` outcomes, and one existing application. The repeat incremental run completed with 0 new messages and unchanged stored counts. The Gmail checkpoint was saved and the active run cleared. See `docs/superpowers/plans/2026-10-20-resilient-sync.md`.
- Pending reasons from a read-only DynamoDB aggregate: `conflicting_roles` 600, `subject_only_event` 85, `ambiguous_application` 65, `unparseable_message` 30, `unknown_stage` 15, `conflicting_requisitions` 8, `nonaffirmative_event` 3, `conflicting_cycles` 3.
- A content-free read-only Gmail sample of 25 `conflicting_roles` items found 24 with no body or subject application-stage phrase and one with a body-stage phrase. A sample of 20 `ambiguous_application` items found 20 body-stage phrases; keep these pending unless individually proven irrelevant. No real email text or headers were printed or saved in Git.
- Root cause: `classify_message` previously returned `conflicting_roles` when it found multiple role names **before** checking whether there was an application event. Review records also omitted subject/sender, and the UI rendered every item. The app intentionally does not auto-create an application without supported identity/status.
- The first import paused several times; scheduled recovery resumed it once and operator-triggered recovery resumed later pauses. Queue handoff cause remains unknown. Do not claim fully unattended sync verification.

## Git and current implementation state

- `2af0ea3 fix: ignore multi-role mail without application events` is committed. It adds synthetic regressions for no-event multi-role mail, real conflicting-role events, and a linked unknown-stage case. Focused tests: 42 passed; full backend suite: 216 passed, one existing Mangum deprecation warning; Ruff passed. This fix is **not yet deployed**.
- The repair plan is `docs/superpowers/plans/2026-10-20-review-queue-cleanup.md`. Task 1 is checked. Tasks 2–4 remain.
- Task 2 has partial code and tests for `ReviewItem.subject/sender`, persistence on review outcomes, API exposure, UI labels, reason copy, and an initial 20-card display with “Show more reviews.” These edits are included in the WIP handoff commit, but Task 2 is **not finished**.
- Last focused backend check for Task 2: 3 tests passed. Last focused frontend check: 8 passed, 1 failed because the test searches for the bare sender while the UI displays `From: <sender>`. `npm run typecheck` failed because the `ReviewItem` fixture in `frontend/src/App.test.tsx` lacks the new `subject` and `sender` nullable fields. An attempted patch for these two test lines failed to apply, so they still need editing. No full suite was run after Task 2 edits.
- The partial Task 2 code does **not** yet capture sender/subject for unparseable messages. Existing imported review items also still lack those fields until the backfill runs.

## Next steps

1. Finish Task 2 test-first: fix the two test/fixture mismatches above, add a synthetic test and minimal header extraction for unparseable messages, then run focused and full backend/frontend checks. Commit the verified step and check it off in the plan.
2. Implement Task 3 with synthetic tests and a preview-first operator command. Re-read pending messages using the connected account; only a **fresh corrected-rule `ignored`** decision may conditionally change `review_required` to `ignored`. Preserve the original reason/audit trail, scope by owner, guard the Gmail connection generation, and skip changed/resolved/unavailable items. Backfill bounded subject/sender on retained reviews. Print aggregate counts only. Preview and inspect counts before apply.
3. Apply the authorized cleanup to the designated development mailbox; verify reason counts, application count/version, owner isolation, and that ambiguous messages remain. The user authorized this action in this chat; there is no need for another consent prompt.
4. Run backend lint/tests, frontend lint/tests/typecheck/build, SAM lint, offline deploy tests, and diff checks. Deploy to the development stack, verify CloudFormation/hosted frontend and live aggregate counts. Record exact evidence in the plan and commit. Do not merge without asking the user.

## Environment and privacy

- Repo instructions: `AGENTS.md`. One driver edits; reviewer is read-only. Keep the branch scoped and commit verified steps. No real email content, sender, subject, credentials, or message IDs in Git, tests, logs, or terminal output.
- The ignored `.env.deploy` holds the development AWS profile/region/stack and secret **names**. AWS profile `application-tracker-bootstrap` was authenticated through `aws login` in a Firefox Private Window; it may need renewal. Check identity and region before cloud writes.
- Local boto3 needed `uv run --with "botocore[crt]==1.43.101" python ...` from `backend/` because the AWS CLI login-session provider requires CRT. This does not change `uv.lock`.
- Development stack is `app-tracker-dev` in `us-east-1`; hosted app: https://main.d2pqicgf5e8l1p.amplifyapp.com. Real-mail diagnostics printed counts/categories only.
