# Handoff: automatic tracking

**Updated:** 2026-10-26

**Branch:** `claude/auto-apply-sheet`, stacked on `codex/local-ollama-review` and the earlier milestone branches. Unmerged and not pushed.

**Dev app:** <https://main.d2pqicgf5e8l1p.amplifyapp.com/>

## October 26 update

- Gmail connected and content fetch worked; the import completed at 09:28 UTC with 1,144 messages. Mode was `on`. The October 25 counts and reconnect warning below are historical.
- A guarded development recheck of the 13 `ambiguous_match` and 9 `status_regression` reviews created 7 separate applications and cleared 12 reviews without changing a rejected status. Two more interview-link logistics were resolved with no application change. A fresh read found **297 applications and 89 pending reviews**; one IBM interview email remains ambiguous because it names no role among several applications. The other 88 reviews belong to the subject-only and unparseable categories.
- Local code on this branch handles the same decisions for future messages, recognizes a recruiting-team company suffix, and supports conditional rechecks of unchanged AI reviews. It has not yet been deployed. See the October 26 follow-up in `docs/superpowers/plans/2026-10-24-auto-apply-2-pipeline.md`.
- AWS login and Docker Desktop are available again. The pinned Node 22.23.3 frontend checks passed in a Docker container; the Mac's default Node 23 cannot run the updated `undici` test dependency.

## October 25 snapshot (historical)

- **Fill complete at 21:09 UTC.** The tracker holds **291 applications**: 229 applied, 54 rejected, 7 interviewing and 1 withdrawn. They span 227 distinct companies; every row has a status, 161 have a cycle, and 41 have no role.
- **No exact duplicate rows**, comparing normalized company, role and cycle.
- **Needs a look: 110.** 65 are AI cases the system routed for review (ambiguous match, status regression, invalid output after 3 tries). 45 are unparseable messages carried over from the old import.
- **3 queue items** fail on every attempt and stay queued.
- **Mode is `on`.** The worker LaunchAgent `com.applicationtracker.worker` was installed with the owner's OK on 2026-10-25. It runs at login and every 30 minutes, and logs counts to `~/Library/Logs/application-tracker/worker.log`. Remove it with `cd backend && uv run --group local python -m local_tools.launchagent uninstall`.
- **Gmail needs reconnecting.** Since about 21:30 UTC every message read returns 503 `gmail_connection_unavailable`, while `/gmail/connection` still reports connected. This is most likely the 7-day refresh-token expiry for Google OAuth apps in Testing mode. Reconnect in Settings → Gmail. Until then, worker runs stop immediately with no effect.
- **Final answer-key score:** started 21:20 UTC; results go in the plan files.

## What the owner approved

- Apply automatically, with no Accept step. The dry-run review was waived for tonight, as long as quality was good enough.
- Agents decide within the spec and record each decision (`AGENTS.md`, commit 3f9b7ed).

## Quality evidence before switching on

- **Answer key:** 180 of the owner's emails, labeled by Codex. Only the labels and an id index stay on disk, in `.local/answer-key/`; the raw copies were deleted. 127 of the 180 are application mail, covering 105 distinct jobs.
- **First score:** 0 fake rows, 0 duplicates and 1 wrong status, but recall was only about 9%. The status validator was throwing away most of the model's correct statuses: it accepted only narrow phrasings, rejected quotes over 70 characters, and treated "would" as hedging.
- **Fixes:** `010c22b`, `b346d1f` and `11678eb` widened the phrasings, raised the quote limit to 200 characters, kept the hedge guards, and allowed "would like to invite". Each fix was checked against cached model quotes (counts only) and reviewed by Codex.
- **Live checkpoint:** on the first 128 analyzed emails, there were 13 creates and 4 overlaps with the answer key, all 4 with the correct company and status. 96 non-application emails were correctly ignored. Then mode was set to `on`.

## Built tonight (all reviewed: Claude implements and Codex reviews, or Codex implements and Claude reviews)

- **Plan 2, automatic pipeline:** filters, kind, matching (requisition id with company check, role-type compatibility), create/update decisions, AI queue, plan report and apply, undo, and the scorer.
- **Plan 3, Mac worker:** the worker loop, the CLI with lock and exit codes, the LaunchAgent code (**not installed**), and the browser panel on the AI queue.
- **Plan 4, spreadsheet view (layout B):** the grid with sorting and click-to-edit, AI markers with Undo, the Needs a look and AI changes tabs, the row menu (history, merge, delete), the plan banner, and Settings behind a button. The deployed version is `11678eb`; the final fixes are not yet deployed.
- **Tests at head:** about 1,213 backend and 221 frontend, with ruff, ESLint, TypeScript, the build, SAM lint, the deploy tests and the evaluation gate all green.

## Done this afternoon

1. Deployed `e68e4d0` (all reviewed fixes) and `2c627cf` (API Lambda timeout 29 s and 1024 MB; the queue and plan routes had hit the 15 s limit). The deploy included:
   - `88a6005`: narrows the prompt-injection guard so AI company names stop dropping statuses, and accepts "under review" wording.
   - `c973048` and `b3b821e`: Apply is bound to the counts the owner reviewed; save errors survive tab switches; Tab works in Cycle; stale markers are fixed; "Applying…" refreshes.
   - `3e4f020`: safe LaunchAgent uninstall and reinstall.
2. Re-queued 222 `no_status_no_match` emails with the fixed validator. They added roughly 30 rows; most really have no stated status.

## Not done or needs the owner

1. Reconnect Gmail (see above). Then re-run the answer-key score with normalized company and role comparison. The scorer's exact-string comparison made the raw score read precision 0.284 and recall 0.220, but the errors themselves were 0 fake rows, 0 duplicates, 1 merged row and 4 wrong statuses across 81 automatic changes.
2. Plans 2, 3 and 4 are complete. Each task was reviewed, and each plan had a final whole-branch review with a fix wave that was re-reviewed. The parked items are in the ledgers.
3. Known follow-ups:
   - The AI-queue and plan routes scan every processed email on each call. That is fine at about 1,400 items after the timeout bump, but the scans should become indexed queries.
   - `POST /ai-queue/backfill` can still time out on large backlogs. Repeating the call finishes the job.
   - The answer-key score has not been re-run with the final validator.
   - 15 "application, no status, no match" emails in the first 128 deserve a look.
4. Merging: nothing is merged. Ask before merging.

Rulings and deferred items live in the SDD ledgers under `.superpowers/sdd/` (git-ignored) and in the plan files.
