# Handoff: automatic tracking (overnight run)

**Updated:** 2026-10-25 ~08:15 UTC (about 4:15 am Indiana)

**Branch:** `claude/auto-apply-sheet`, stacked on `codex/local-ollama-review` and the earlier milestone branches. Unmerged and not pushed.

**Dev app:** <https://main.d2pqicgf5e8l1p.amplifyapp.com/>

## What is running right now

- The local worker (`python -m local_tools worker --until-empty`) is analyzing the backlog with Ollama on this Mac. The tracker is in mode **`on`**, so each analyzed email is applied straight away, oldest first.
- Progress at 08:12 UTC: **16 applications** (15 applied, 1 rejected), with **1,014 emails** still to analyze. The pace was about 13–20 s per email while the Mac was otherwise idle.
- Check progress:
  ```bash
  cd backend && PYTHONPATH=. uv run --group local python /private/tmp/claude-501/-Users-alexz-application-tracker/aaf4cbf0-9d72-45a3-8783-765a5691b3a5/scratchpad/status.py
  ```
- Stop the run by switching the tracker off: Settings in the app, or `PUT /settings/auto-apply {"mode":"off"}`.

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
- **Plan 4, spreadsheet view (layout B):** the grid with sorting and click-to-edit, AI markers with Undo, the Needs a look and AI changes tabs, the row menu (history, merge, delete), the plan banner, and Settings behind a button.
- **Tests at head:** about 1,213 backend and 221 frontend, with ruff, ESLint, TypeScript, the build, SAM lint, the deploy tests and the evaluation gate all green.

## Not done or needs the owner

1. Install the background worker LaunchAgent (`uv run --group local python -m local_tools launchagent install`). It changes macOS login config, so it waits for the owner's OK.
2. Final whole-branch Codex reviews of Plans 3 and 4, and the Codex review of Plan 3 Task 3. These were deferred so Ollama gets the CPU overnight.
3. Known follow-ups:
   - `POST /ai-queue/backfill` times out at the 15 s Lambda limit on about 1,100 items. Repeating the call finishes the job.
   - The answer-key score has not been re-run with the final validator.
   - 15 "application, no status, no match" emails in the first 128 deserve a look.
4. Merging: nothing is merged. Ask before merging.

Rulings and deferred items live in the SDD ledgers under `.superpowers/sdd/` (git-ignored) and in the plan files.
