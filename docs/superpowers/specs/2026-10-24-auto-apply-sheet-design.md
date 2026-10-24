# Automatic application tracking with a spreadsheet view

**Date:** 2026-10-24
**Status:** Draft for owner approval. Nothing here changes behavior until it is approved.
**Builds on:** `codex/local-ollama-review` (local Ollama suggestions), stacked milestone branches 8-10.

## Why

The owner has applied to more than 200 roles since late July 2026. After a full
import and a complete local-Ollama pass, the tracker has **one** application and
390 items waiting for review. The owner does not want to approve changes one by
one; they want the tracker to keep itself up to date from Gmail, shown as a
simple spreadsheet, with a way to undo mistakes.

Counts measured on the owner's development data (2026-10-21 to 2026-10-22; counts
only, no content):

- The 90-day import found 1,145 candidate emails (Jul 37, Aug 210, Sep 555, Oct 338).
- **749 were dropped as `ignored/no_event`** without any AI call, because the
  rules only recognize a confirmation when the body says "thank you for applying"
  or "we received your application" (`backend/app/services/email_rules.py:14-20`).
- **No path ever creates an application** from email. Rules and AI only update an
  existing application; with none on file, everything becomes review.
- Of 341 Ollama suggestions, 277 have no status, 54 say applied, 10 say rejected.
  They name 199 distinct companies. The owner believes most no-status items are
  job alerts, digests, and recruiter mail, not their own applications.

So the gap is not "the AI is weak"; it is that most application mail never
reaches the AI, and nothing is allowed to create a row.

## Goals

1. Every email about an application the owner sent is reflected in the tracker
   **automatically**: new applications are created, existing ones are updated.
2. Mail that is not about the owner's own applications (job alerts, "roles you
   might like", digests, newsletters, events, marketing, recruiter outreach
   without an application) never becomes a row.
3. The tracker looks and works like a simple spreadsheet.
4. Every automatic change is visible and undoable.
5. Stay free to run: classification uses the local Ollama model on the owner's Mac.
6. Prove quality before anything is applied for real, using an answer key built
   from a sample of the owner's real mail.

## Non-goals

- Importing earlier than the existing 90-day window (owner: not needed).
- Paid or hosted AI for daily use (owner chose free/local). The existing BYOK
  providers stay as they are.
- Updates while the Mac is off. They happen the next time the Mac is on.
- Writing anything to Gmail (labels, archiving). "Labels" in this design means the
  answer key, not Gmail labels.
- Multi-user product polish or public release work.

## Anti-goals (would be a failure even if it technically works)

- Fake rows: job alerts or recruiter mail turned into applications.
- Duplicates for the same job under slightly different names. The owner said
  "SWE Intern" and "Software Engineering Intern" are the same thing.
- A wrong terminal status (a "Rejected" or "Offer" that the email does not say).
- Guessed values: status, cycle, or dates the email does not state (existing PRD rule).
- Silent changes with no way to see or undo them.

## Decisions the owner made

- **Automatic, no Accept step** for normal cases.
- **Spreadsheet layout B:** a grid with tabs at the bottom, like Google Sheets:
  **Applications**, **Needs a look**, **AI changes**.
- **Free:** daily classification runs on local Ollama on the Mac.
- **90-day window stays.**
- **Answer key:** a sample of about 200 real emails may be sent to OpenAI through
  Codex to be labeled, so we can test against it. Labels stay on the Mac.
- **Role names:** "SWE Intern" and "Software Engineering Intern" are the same role.
- **What counts as an application:** any email that states a status for a job
  (applied, OA, interview, offer, rejection, withdrawn) creates a row when nothing
  matches, even with no "thanks for applying" email on file (portal, referral, or
  career-fair applications). Outreach about a job the owner has not applied to
  ("would you be interested in…") never creates a row. The kind question and the
  answer-key labels use this definition.

## How it works

### 1. Hard filters first (deterministic, no AI)

Filters only throw away mail that is clearly **not** about one of the owner's
applications. They no longer try to recognize confirmations by exact wording.

- Drop known job-alert and digest senders/templates (for example LinkedIn job
  alerts, "jobs you may be interested in", Handshake digests), newsletters,
  marketing, and event invitations. The exact patterns come from the answer key's
  real sender/subject data, not guesses.
- Keep the existing injection, hypothetical, and negation guards.
- Everything not dropped goes to the AI. When in doubt, keep it: a dropped
  confirmation is a lost application; an extra AI call only costs time.

Every dropped email is recorded with a reason, as today, so a filter mistake can be
found and re-run.

### 2. AI reads every email that survives the filters

The local model answers two questions per email, with evidence quotes as today:

1. **Kind**: is this about an application the owner sent? One of:
   `application_update` or not (`job_alert`, `recruiter_outreach`,
   `newsletter_or_event`, `other`). Only `application_update` can touch the sheet.
2. **Facts**: company, role, role type, season, year, status, requisition ID.

The prompt keeps the current safety rules (untrusted email, no tools, exact
quotes, null when not stated). The backend keeps validating every value against
its quote. "Kind" is new and is graded against the answer key like everything else.

### 3. Apply automatically

For an `application_update` with a company and a status, each backed by a quote:

- **Match an existing row** using requisition ID, then Gmail thread, then company
  plus normalized role plus cycle. Role normalization treats common variants as
  equal (for example "SWE" = "Software Engineer(ing)", ignore "Intern/Internship",
  punctuation, case, and season/year words already captured as cycle).
- **Match found:** update the row, following the existing PRD rules: no status
  regression from older mail, no undoing a manual correction with older evidence,
  terminal statuses reopen only through Needs a look.
- **No match:** create a new row with only the supported fields. Status comes from
  the email; it is never defaulted.
- **Kind is application_update but no status** (for example a recruiter follow-up
  about an existing application): update supported non-status fields of a matched
  row; never create a row from it.

Goes to **Needs a look** instead of applying (expected to be a handful):

- Two plausible matching rows (same company, roles that might be the same, no
  requisition ID or thread to break the tie).
- A status that would go backward or reopen a terminal status.
- Contradictions inside one email (for example two different roles with
  different statuses).
- Unreadable messages and AI failures after the retry limit.

### 4. Where and when it runs (free)

- Gmail sync stays in AWS exactly as today (scheduled 09:00 UTC, plus Sync now).
  The backend still owns the Gmail token and builds the prompt.
- A small **background job on the Mac** (a LaunchAgent) checks for emails waiting
  for AI, runs local Ollama, and submits results to the backend, which validates and
  applies them. It runs when the Mac is on and awake, for example every 30 minutes
  and at login.
- The job signs in like the web app (the owner's normal Cognito sign-in, with the
  refresh token kept in the macOS Keychain). It must not use the owner's AWS admin
  credentials; those expired every ~15 minutes during the 2026-10-21 run and are
  the wrong trust boundary.
- The existing **Analyze with Ollama** browser panel can stay as a manual "run now".

### 5. The sheet (layout B)

**Applications tab (default):**
- One row per application. Columns: Company, Role, Type, Cycle, Status, Applied,
  Last update.
- Click a cell to edit it in place; the change saves immediately using the existing
  versioned PATCH (a stale edit shows "reload latest" as today).
- Click a header to sort. Search box plus simple filters above the grid.
- A cell last set by the AI shows a small corner marker. Hovering shows the source
  email, **Open email**, and **Undo** for that change.
- Row actions (right-click or a row menu): open history, merge duplicate, delete row.

**Needs a look tab:** the cases listed in section 3, each with the email link and the
proposed change, and the actions Apply, Edit then apply, or Dismiss.

**AI changes tab:** newest first, one line per automatic change (created row,
updated fields, ignored as not-an-application), with **Undo**. Ignored emails are
listed so a filter or kind mistake can be found and reversed ("This was an
application").

Gmail, AI, and account settings move behind a Settings button so the sheet gets the
whole page.

### 6. Undo

- Undo of an update restores the previous field values (the audit event already
  stores before/after), using the version check so it cannot overwrite a newer edit.
- Undo of a created row removes the row (soft delete, as today) and remembers the
  decision so replaying the same email does not recreate it.
- "This was an application" on an ignored email re-runs it through step 3.
- Every undo is itself recorded in history.

### 7. Answer key (built before anything applies for real)

- A local script selects a stratified sample of about 200 of the owner's processed
  emails: from the 749 dropped, the 341 with suggestions, and the 49 other review
  items, plus every distinct sender domain that appears often.
- The script fetches each email through the backend (no Gmail token on the Mac),
  writes them into a **git-ignored folder on the Mac** (for example
  `.local/answer-key/`), and Codex labels them (owner approved sending this sample
  to OpenAI). Labels: kind, company, role, role type, season, year, status,
  requisition ID, and which other sampled emails are the same application.
- The owner can skim and correct labels; the system should not require it.
- Raw email copies are deleted after labeling; the labels file stays local only.
  Nothing from it goes into Git, logs, fixtures, or CI.
- The existing synthetic evaluation suite in CI stays and gains synthetic cases for
  the new kinds and role-normalization rules.

### 8. Rollout

1. Build the answer key.
2. Build filters, kind classification, matching, and apply logic behind an
   **off** switch. Score them against the answer key locally. The PRD targets apply:
   at least **98% precision for automatic changes** and at least **90% recall** for
   application emails. Report fake rows, duplicates, and wrong statuses separately.
3. **Dry-run report** on the full 1,145 emails plus the 341 saved suggestions:
   "would create N rows, update M, ignore K, Needs a look L", with a sample list.
   The 341 saved suggestions are re-checked by the new kind question, not applied
   as-is. The owner approves the report once.
4. Turn the switch on: apply the backlog, then keep running on new mail.
5. Ship the sheet (it can be built in parallel with steps 1-3).

## Constraints

- All data stays owner-scoped; no header or parameter chooses the owner.
- Real email content never goes into Git, logs, test fixtures, or terminal output.
  The answer key is the one approved exception, and it stays on the Mac.
- Local Ollama stays on loopback with `OLLAMA_ORIGINS` limited to the app origin.
- Current hardware: Apple M3, 16 GB, `gemma4:latest`, about 27 s per email warm.
  The backlog is about 8 hours of local compute, done in several sittings; daily new
  mail is a few minutes.
- The PRD changes: AI may create an application when the email establishes a
  stage; the "AI may only apply to one unambiguously matched application" rule is
  extended to creation. `docs/PRD.md` and `docs/TECHNICAL_DESIGN.md` are updated
  with this design.

## Calls made while writing this (not owner decisions; check these)

- **Email content storage:** the backend still never stores bodies. The Mac job
  gets each email's prompt by having the backend re-fetch it from Gmail, as the
  current `local-ai-input` route does; this is extended from review items to any
  processed message, including the 749 dropped ones. A message deleted from Gmail
  becomes unavailable and is reported as such. Subject and sender are stored for
  ignored emails as they already are for review items, so the AI changes tab can
  show them.
- **Status transitions:** reuse the existing rules (`would_regress` and the PRD).
  A clear later rejection after Interviewing (manual or automatic) applies
  automatically, as the PRD says. Reopening Rejected, Withdrawn, or Offer goes to
  Needs a look.
- **Undo is per change:** it restores that change's fields only if they have not
  changed since; otherwise it says so and the owner edits the cell instead. Undo of
  a created row is blocked once the row has later changes, for the same reason.
  "Do not recreate" is remembered per source email, so a different, later email
  about the same job can still create it.
- **Existing review items:** the 390 current items (including the 341 saved
  suggestions) are re-run through the new pipeline in the dry run and replaced by
  Needs a look. The old review queue is retired once the switch is on.
- **Mac job sign-in** is in scope: a one-time browser sign-in on the Mac (Cognito
  authorization code with PKCE and a loopback redirect, which may need a callback
  URL added to the app client), refresh token in the Keychain. Until it exists, the
  browser panel is the way to run Ollama.
- **Quality gate:** precision and recall are measured on the answer key (the sample
  is stratified so it has enough real application emails), plus the owner's read of
  the dry-run sample. The owner's one approval turns the switch on; the scores are
  re-run whenever filters, prompt, or matching change.
- **Branching:** this is a new milestone on `claude/auto-apply-sheet`, stacked on
  `codex/local-ollama-review`. Its first step updates `docs/PRD.md`,
  `docs/TECHNICAL_DESIGN.md`, and the roadmap.

## Left to the builder

- Exact filter patterns, derived from the answer key.
- Prompt wording for the kind question and schema changes, within the existing
  safety rules.
- The role-normalization rule set, tested against answer-key duplicates.
- The Mac job's schedule, Keychain storage, and how it finds pending work.
- Grid implementation (plain table plus inline editing is fine; no table library
  unless clearly needed).
- How the dry-run report is shown (a page or a local file of counts plus a sample
  the owner can read).
