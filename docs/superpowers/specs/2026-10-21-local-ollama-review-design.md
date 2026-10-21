# Local Ollama suggestions for pending reviews

**Status:** Proposed for owner approval, 2026-10-21.

## Intent

Let a signed-in user run AI analysis on their own computer while the Application
Tracker tab is open. The first version produces suggestions on pending review
items; it never creates, edits, attaches, rejects, or accepts an application
without a separate user action. This gives the owner a no-provider-fee route to
try an open-weight model against the review backlog.

The existing AWS Gmail import and deterministic rules keep running. This
feature only analyzes reviews already in the app; it does not broaden Gmail's
import query or reprocess ignored email. Existing hosted-provider AI remains a
separate setting and should be disabled when the user wants local-only AI.

## User flow

1. On **Needs review**, the user chooses **Analyze with Ollama**. The app checks
   for a local Ollama server at `127.0.0.1:11434`, lists installed models, and
   lets the user select one. No API key is needed. If it cannot connect, it
   gives setup instructions, including allowing the app's exact web origin in
   `OLLAMA_ORIGINS`; it never asks the user to expose Ollama on a public port.
2. The user starts a batch of pending reviews. The browser processes eligible
   reviews one at a time using the existing AI escalation reason allowlist and
   skips reviews that already have an AI suggestion. It shows completed,
   skipped, and failed counts and has a Stop button. Closing the tab stops work;
   reopening and starting again skips suggestions already saved.
3. For each review, the authenticated app API checks that the review belongs to
   the signed-in owner and is still pending, fetches the original message from
   that owner's connected Gmail account, and builds the existing bounded prompt
   and output schema. The browser sends that prompt to the selected **local**
   Ollama model. The Gmail token is never sent to the browser or Ollama.
4. The browser submits the model's JSON response to the app API. The API
   rechecks the pending review and Gmail connection, validates exact evidence
   quotes and allowed values against the original message, and uses the
   existing reconciliation rules to identify a candidate application. Even if
   reconciliation would normally auto-apply, this path stores only an AI
   suggestion on the review. The user uses the existing review controls to
   decide what to do with it.

Only reviews with reasons in the current `ESCALATION_REASONS` set are eligible:
ambiguous application, unknown stage, subject-only event, nonaffirmative event,
and conflicting roles. Other review reasons remain manual. A model result with
no valid supported fields leaves the review unchanged and is counted as
skipped, not resolved.

## Boundaries and safety

- The app's API continues to enforce owner identity from the verified Cognito
  subject. Review IDs do not grant access to another owner's email. A Gmail
  disconnect or reconnect during analysis prevents a stale suggestion write.
- The API never trusts the browser's email text, application match, or claimed
  model result as a decision. It fetches the message again when validating and
  conditionally writes only to a still-pending review without an existing AI
  suggestion. A concurrent resolution or changed review returns a conflict.
- The original message body is used for the prompt, then discarded; the stored
  suggestion contains only validated fields and short evidence quotes. Neither
  email bodies nor prompts are logged. The suggestion records Ollama as its
  source. No application mutation happens on this path.
- Local model calls require the user's browser and computer to stay active.
  Errors from Ollama or Gmail are shown in the UI; no hidden background job or
  automatic retry is introduced. The existing hosted AI toggle remains visible
  with a warning when it is enabled during a local run, because scheduled sync
  may still call the hosted provider.
- This is local *inference*, not an entirely local app: Gmail and application
  records remain in the existing AWS architecture. The app origin alone is
  allowed to call Ollama; `OLLAMA_HOST` stays bound to loopback.

## Implementation shape

- Add an owner-scoped API read for local AI input and an owner-scoped API write
  for a validated suggestion. Reuse Gmail token refresh, message parsing,
  `build_prompt`, `validate_extraction`, `reconcile_extraction`, and the review
  repository's conditional-write pattern. Do not create a second classifier.
- Add a small browser client for Ollama's local `/api/tags` and `/api/chat`
  endpoints. Use the existing extraction JSON schema as Ollama's `format`, with
  `stream: false`. The browser handles one message at a time so model load and
  API rate limits do not pile up.
- Add the batch control and progress state to the review screen. Keep the
  existing review actions and hosted AI settings as they are, except for the
  local-run warning when hosted AI is enabled.
- Begin implementation with a browser feasibility check from the deployed HTTPS
  origin to loopback Ollama, including origin permissions. The first supported
  browser for this development feature is the owner's Firefox on macOS. If that
  check fails there, stop and revisit the design before adding API routes.

## Success checks

- A synthetic local-model test produces a suggestion that appears on the review
  screen without changing an application. Rejecting or accepting remains a
  separate owner action.
- Cross-owner review IDs, stale Gmail generations, resolved reviews, stale
  submissions, malformed model output, and unsupported evidence cannot store a
  suggestion. No raw email or token appears in browser storage or logs.
- The batch can be stopped and restarted without overwriting saved suggestions;
  it explains why some pending reviews are ineligible or unavailable.
- Backend and frontend focused tests, repo CI checks, and an actual browser
  loopback call pass before claiming the feature works in the hosted app. A
  real-mailbox test is a separate explicit user action.

## Product calls made for this proposal

- User chose browser-driven local inference and suggestions only.
- The batch processes the same ambiguous reasons as the existing hosted AI
  path. This avoids expanding classification policy as part of the local
  provider change.
- The local model is selected from installed models each session; no model
  name or email text is persisted in browser storage.
- Existing hosted AI remains independent. The UI warns if it is enabled so
  users understand that scheduled sync could still send email to that provider.
- Firefox on the owner's macOS is the initial browser acceptance target because
  that is the browser used for the current development app. Other browsers can
  be checked later without expanding this first milestone.
