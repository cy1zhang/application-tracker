# BYOK AI Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user bring their own OpenAI, Anthropic, or Gemini key so that a small, explicit set of ambiguous recruiting emails gets a validated, evidence-backed extraction, which is auto-applied only when the server independently matches one application and every reconciliation guard passes. Everything else stays in manual review with the AI proposal attached.

**Architecture:** Rules run first and are unchanged. Only a `review_required` rule decision whose reason is in an explicit allowlist can reach the AI tier. An escalator reserves one request against an atomic per-owner daily counter on the Users record (which also rechecks the Gmail generation, the active run, and the AI settings version), sends one bounded, delimited, tool-free prompt to the owner's provider through a small stdlib adapter, and validates the JSON against one schema with exact-substring evidence. The server, not the model, matches the extraction to an existing application and re-runs the rule-path guards, producing an ordinary `RuleDecision` that the existing transactional `record` path persists. Any key, budget, provider, or validation problem produces a normal review item and never fails the sync run.

**Tech Stack:** Python 3.12, FastAPI, boto3, DynamoDB, KMS, stdlib `urllib`, AWS SAM, pytest/moto, React/TypeScript/Vitest.

**Spec:** `docs/IMPLEMENTATION_ROADMAP.md` Milestone 9; `docs/PRD.md` sections 2-5 (Settings, Tier 2/Tier 3, acceptance criteria 6-7); `docs/TECHNICAL_DESIGN.md` Storage, API boundaries, Sync and recovery steps 7-9, Classification and reconciliation, Operations and verification.

**Branch note:** `milestone-9-byok-ai` is stacked on the unmerged `codex/milestone-8-resilient-sync`. Do not rebase or merge until the user decides; keep Milestone 8 files untouched except where a task below names them.

## Global Constraints

- Rules always run first. Clear `applied` and `ignored` rule outcomes never call a provider.
- AI may only be called for the allowlist below. Unparseable, forwarded, unavailable messages and reconciliation conflicts never reach AI.
- Derive the owner only from verified authentication (API) or the sync job's owner (worker). Nothing the model returns selects an owner, application ID, table, URL, or action.
- The model gets no tools, no function calling, no URLs to fetch, and no application IDs. It returns only the extraction schema.
- Email text is untrusted data inside a per-request random delimiter with a fixed system instruction. Model-reported confidence is ignored.
- Every non-null extracted field needs an evidence quote that is an exact, whitespace-normalized substring of the text actually sent. Unsupported fields are nulled, never guessed. Invalid JSON or schema violations go to review.
- AI never creates applications and never writes fields other than `status`, `season`, and `year` on an existing application (the same fields the rule path writes). Company, role, employment type, and requisition ID are used only for matching and display.
- Never log, persist outside the review item, or return prompts, model responses, API keys, or email text. Logs carry provider name and sanitized error category only. A stored proposal holds only validated fields and evidence quotes (each at most 70 characters, so one event stays under the PRD's 500-character evidence limit).
- The API key is KMS-encrypted with context `{owner_id, credential_type: "ai_api_key"}`, never returned in full, never echoed in an error body, and never placed in a URL.
- Provider model IDs come only from deployment configuration. Tests use stubs; CI makes no live provider call.
- A provider, key, or budget problem must never fail or block a sync run or any rule-based update.
- Keep account deletion, evaluation-in-CI, alarms, and explicit "retry with AI" outside this milestone.

## Escalation Allowlist (verified against `email_rules.classify_message`)

AI is considered only when `decision.outcome == "review_required"` and `decision.reason` is one of these. `ESCALATION_REASONS` and `AUTO_APPLY_REASONS` are two named constants so the policy is one reviewable line each.

| Reason | Where it fires | Why extraction can help | May auto-apply |
|---|---|---|---|
| `ambiguous_application` | A regex stage was found but `_match_application` returned no unique thread/requisition match. This is the largest live bucket, because a manually created application has no thread or requisition link before its first email. | The model can read company, role, and requisition; the server can then match by an exact company+role pair. | Yes |
| `unknown_stage` | Company/role context is present (a known application is mentioned or "for X at Y" appears) but no stage regex matched. | Stage wording outside the fixed regexes ("next steps", "coding challenge"). | Yes, with a status-cue check |
| `subject_only_event` | Stage regex matched only in the subject. | The body may state the stage in other words. | Proposal only (the rule deliberately distrusts subject-only evidence) |
| `nonaffirmative_event` | Stage regex matched in a sentence with hedging (`would`, `if`, `pending`, ...). | A reader can tell a real update from boilerplate. | Proposal only (this is where model judgment is weakest and an injection payload would aim) |
| `conflicting_roles` | More than one explicit role phrase **and** a stage was found (the no-stage multi-role case returns earlier as `unknown_stage`/`no_event`). | The model can pick the role the event sentence is about. This is the false-positive class the recheck script already targets. | Proposal only |

Never escalated, with the reason in the code that decides it:

- `forwarded_message`, `unparseable_message`, `message_unavailable`: the content is not the owner's own event, or no text exists to send.
- `conflicting_stages`, `conflicting_cycles`, `conflicting_requisitions`: the email contradicts itself; PRD section 5 sends contradictory evidence to review.
- `ambiguous_event_identity`: a unique candidate was found but the event sentence disagrees on role or company. AI would be asserting identity against a deterministic mismatch.
- `status_regression`, `cycle_conflict`, `older_evidence`, `missing_application`, `stale_application`: reconciliation conflicts, not extraction gaps.
- `applied`, and `ignored` reasons (`non_application_message`, `hypothetical_event`, `no_event`, `no_change`).

## Review Focus

- Prompt injection: an email that says to ignore instructions, change another application, set a status, call a tool, or reveal a key must produce either a nulled/rejected field or a review item. It must never produce a mutation the rules and guards would not allow.
- Fabricated or irrelevant evidence: a quote not present in the sent text, spanning section delimiters, or present but unrelated to the claimed status must be nulled.
- A provider outage, invalid key, or exhausted budget must leave the rule outcome intact and the run `completed`, with the message visibly in review.
- A replaced, deleted, disabled, or invalidated key, or a changed Gmail generation, must not be used for a provider call or for the commit.
- The budget counter must be atomic, per owner, per UTC day, and charge every attempt including retries; replay of an already-recorded message must not spend budget.
- Cross-user: settings, counters, candidate names in prompts, and decrypt contexts never cross owners.

---

### Task 1: Extraction schema, bounded prompt, and evidence validation

**Files:** Create `backend/app/domain/ai.py`, `backend/app/services/ai_extraction.py`, `backend/tests/test_ai_extraction.py`.

**Interfaces:**

- `domain/ai.py`: `AiProvider` enum (`openai`, `anthropic`, `gemini`); `ExtractionOutput` (Pydantic, `extra="forbid"`) with nullable `company`, `role`, `employment_type` (existing `RoleType`, excluding `unknown`), `season` (`Season`), `year` (2000-2100), `status` (`ApplicationStatus`), `requisition_id`, and an `evidence` object with one nullable quote per field; `EXTRACTION_JSON_SCHEMA` (flat, every key required and nullable, `additionalProperties: false`) shared by all adapters.
- `ai_extraction.py`: `build_prompt(message: ParsedGmailMessage, applications: list[ApplicationCandidate]) -> PromptInput` returns the fixed `system` text, the `user` text, and the exact `sections` that were sent (subject, sender display, body). `validate_extraction(raw: dict, prompt_input) -> ValidatedExtraction` returns only supported fields plus their quotes, or raises `InvalidExtraction`.
- Prompt rules: body truncated to **8,000 characters** (about 2k tokens; recruiting status language is in the first paragraphs, `MAX_TEXT` already caps parsed text at 16,384, and a smaller cap bounds cost and the injection surface; the cap is one constant, and truncation is noted in the prompt). Subject and sender header only (already at most 512 characters); no recipient, date, thread ID, message ID, or application ID. Candidate list: at most 20 `company | role` strings, each at most 100 characters, taken only from the owner's applications whose company appears in the subject, sender, or body or that are linked to this thread/requisition. No candidates are sent when none qualify. The delimiter is a fresh `secrets.token_hex(8)` per request and any occurrence in the email is removed.
- Evidence rules: a quote is valid when its whitespace-normalized text is a substring of exactly one normalized section (it cannot span the subject/sender/body boundary) and is at most 70 characters. A field is kept only if (a) its quote is valid and (b) the value is supported by the quote: company, role, requisition ID, and year must appear in the quote (case-insensitive); season must appear in the quote; status and employment type need a cue match from a small per-value lexicon (for example `offer` needs "offer", `rejected` needs "not moving forward|unfortunately|regret|other candidates|decided not", `interviewing` needs "interview", `oa_received` needs "assessment|coding challenge|online test"). Otherwise the field is nulled. Candidate names in the prompt are never valid evidence.
- A status whose only valid quote is in the subject is flagged `status_in_subject_only`; reconciliation (Task 5) will not auto-apply it.

- [x] Write tests for: a valid full extraction; each field nulled when its quote is absent, fabricated, whitespace-reflowed (still valid), longer than 70 characters, or spanning sections; a quote that exists but does not support the status (an "interview" quote for `offer`); a candidate name used as a quote; unknown keys, wrong types, out-of-range year, and non-object JSON raising `InvalidExtraction`; a model-supplied `confidence` key being rejected as an unknown key.
- [x] Write prompt tests with an email containing "ignore previous instructions", a fake closing delimiter, a request to reveal the API key, and a request to update another company: assert the injected text appears only inside the delimited data block, the fixed system text is unchanged, no tools/function fields exist in `PromptInput`, the candidate list holds no application IDs or other-owner names, the body is capped at 8,000 characters with a truncation note, and the sent sections are what validation checks.
- [x] Confirm the tests fail for the missing modules.
- [x] Implement the schema, prompt builder, and validator; run focused tests, full backend tests, `uv run ruff check .`, and `git diff --check`; commit.

---

### Task 2: Encrypted AI settings storage and settings API

**Files:** Modify `backend/app/services/credential_crypto.py`, `backend/app/repositories/dynamodb_gmail.py`, `backend/app/api/dependencies.py`, `backend/app/main.py`, `backend/tests/test_credential_crypto.py`, `backend/tests/test_gmail_repository.py`; create `backend/app/repositories/dynamodb_ai.py`, `backend/app/api/settings.py`, domain models in `backend/app/domain/ai.py`, `backend/tests/test_ai_settings_repository.py`, `backend/tests/test_settings_api.py`.

**Interfaces:**

- `CredentialCipher.encrypt(owner_id, credential_type, plaintext)` and `decrypt(owner_id, credential_type, encrypted)` take the type into both the KMS encryption context and the AES-GCM associated data; `encrypt_refresh_token`/`decrypt_refresh_token` become wrappers with the existing `gmail_refresh_token` type so Gmail ciphertexts stay readable. The KMS key is the existing one (its logical name stays `GmailKmsKey` to avoid a replacement); the env var stays `GMAIL_KMS_KEY_ARN`.
- Users record fields (separate from Gmail fields, never touched by `save_connection`/`disconnect`): `ai_provider`, `ai_encrypted_key`, `ai_key_hint` (last 4 characters), `ai_enabled`, `ai_key_status` (`ok`/`invalid`), `ai_settings_version` (incremented by every PUT and DELETE), `ai_consent_at`.
- `DynamoAiSettingsRepository`: `get(owner_id) -> StoredAiSettings | None` (includes ciphertext, internal use only), `save(owner_id, provider, encrypted_key | None, hint | None, enabled, now)` (an update with no key keeps the existing key and requires the same provider), `delete(owner_id)` (removes the key, bumps the version, keeps the daily counter), `public_view(owner_id, today) -> AiSettingsView`.
- API: `GET /settings` returns `{ai: {available_providers, provider, key_hint, enabled, key_status, requests_today, daily_limit, usage_day}}`; `available_providers` lists only providers whose model ID env var is non-empty, so a deployment with no model IDs offers none. `PUT /settings/ai` takes `{provider, api_key?, enabled, consent}`; `consent` must be `true` whenever a key is supplied and is recorded as `ai_consent_at`; `api_key` is 16-400 printable ASCII characters with no whitespace, supplied as `SecretStr`, and required unless only `enabled` changes for the stored provider. `DELETE /settings/ai` is idempotent (204). Saving makes no provider call.
- A key-bearing request failing validation must not echo the key: scope a `RequestValidationError` handler (or manual body parse) for `/settings/ai` that strips `input` values from the 422 body.
- The settings routes also fix an existing hazard: a Users item can now exist before Gmail is connected. `get_connection` must treat a missing `generation` as 0, and `save_connection`'s condition must accept a record with no `generation` (`attribute_not_exists(generation) OR generation = :expected`).

- [x] Write crypto tests: an `ai_api_key` ciphertext cannot be decrypted with the Gmail type, with another owner's ID, or with tampered data; existing Gmail tests still pass unchanged.
- [x] Write repository tests (moto): save/replace/delete/enable-disable, version increments, the hint holds only the last 4 characters, public view never contains ciphertext or the key, daily counter survives delete, AI fields survive Gmail connect, disconnect, and reconnect, and saving AI settings before connecting Gmail does not break `get_connection`, `save_connection`, `SyncRepository.start`, or the dispatcher filter.
- [x] Write API tests: authentication required (401), unapproved group (403), owner isolation (user B's GET shows nothing and B's DELETE leaves A's settings), unknown provider/unconfigured provider/short key/whitespace key/missing consent/unknown field return 422 and the 422 body never contains the submitted key, replacing a key resets `key_status` to `ok` and bumps the version, toggling `enabled` without a key works, no endpoint accepts an owner ID, and no response contains `api_key`, ciphertext, or the full key.
- [x] Confirm the tests fail; implement the smallest cipher extension, repository, routes, and wiring; run focused and full backend tests, lint, and `git diff --check`; commit.

---

### Task 3: Provider interface and the three stdlib adapters

**Files:** Create `backend/app/services/ai_providers/__init__.py`, `openai_adapter.py`, `anthropic_adapter.py`, `gemini_adapter.py`, `backend/tests/test_ai_providers.py`.

**Interfaces:** `ExtractionProvider.extract(api_key: str, model: str, prompt_input: PromptInput) -> dict` returns the parsed JSON object or raises `ProviderError(category)` with `category` in `invalid_key`, `rate_limited`, `provider_unavailable`, `invalid_output`. Each adapter takes an injectable `urlopen` like `GmailFetchClient`, uses a fixed HTTPS URL, a **20-second** timeout, a response read capped at 64 KB, a small `max` output-token limit, structured/JSON output mode with `EXTRACTION_JSON_SCHEMA`, and no tools. The key travels only in the provider's auth header (never in a URL, so Gemini uses its header, not a query parameter). The default opener refuses redirects (a 3xx is `provider_unavailable`), so credentials are never forwarded to another host. `create_provider(provider) -> ExtractionProvider` is the factory. Error mapping: 401/403 (and a Gemini 400 whose error reason is an invalid key) to `invalid_key`; 429 to `rate_limited`; 5xx, timeout, network error, redirect, and other 4xx such as an unknown model to `provider_unavailable`; non-JSON, a refusal, a truncated generation, an empty candidate, or a non-object result to `invalid_output`. Error bodies are read only to classify them and are never stored or logged.

**Implementer notes (required):**

- Anthropic: load the `claude-api` skill before writing the adapter and follow its current Messages API request shape, version header, and structured-output mechanism. Do not rely on memory of the API.
- OpenAI and Gemini: check each provider's current official documentation for the request path, auth header, structured-output option (and the schema subset it accepts, such as strict-mode required/nullable rules), refusal/finish-reason fields, and error codes. Record the docs consulted and their dates in the commit message body.
- Do not hardcode model IDs anywhere; tests pass an arbitrary string.

- [x] Write per-adapter tests with a stub `urlopen`: the request goes to the expected host over HTTPS with the key only in the auth header, the body contains the schema and no tools/functions, the timeout is 20 seconds, a valid response returns the parsed object, and each error class above maps to the right category, including a 302, an oversized body, invalid JSON, and a refusal/truncated finish reason.
- [x] Write a test that the default opener's redirect handler refuses redirects, and a log-capture test proving no key, prompt, or response text appears in logs or exception strings on any error path.
- [x] Confirm the tests fail; implement the interface plus OpenAI, run tests, lint, and commit. Repeat for Anthropic (commit), then Gemini (commit), each with its own focused tests.

---

### Task 4: Atomic daily budget and key-state transitions

**Files:** Extend `backend/app/repositories/dynamodb_ai.py`; add `backend/tests/test_ai_budget.py`.

**Interfaces:**

- `reserve_request(owner_id, *, generation, run_id, settings_version, now) -> None` performs one conditional `UpdateItem` on the Users record whose condition requires the Gmail guard (`generation`, `active_run_id`, `attribute_exists(encrypted_token)`), `ai_enabled = true`, `ai_key_status = ok`, `ai_settings_version = :version`, and the budget. Same-day increment: `ai_usage_day = :today AND ai_requests_today < :limit` with `ADD ai_requests_today 1`. New day: `attribute_not_exists(ai_usage_day) OR ai_usage_day <> :today` sets the day and the counter to 1. A race on the first request of a day retries the increment once. On failure it re-reads and raises exactly one of `StaleSyncRun` (generation/run changed), `AiSettingsChanged` (version, enabled, or key replaced/deleted), `AiKeyInvalid`, or `AiBudgetExhausted`. The limit is 50 per UTC day and counts every attempt, retries included; there is no refund.
- `mark_key_invalid(owner_id, settings_version)` sets `ai_key_status = invalid` only if `ai_settings_version` still equals the version that was used, so a stale failure cannot invalidate a freshly replaced key.
- `AiRuntime` loader: `load_runtime(owner_id) -> AiRuntime | None` returns provider, model ID (from env), settings version, and the encrypted key only when the owner has an enabled, `ok` key and the provider's model ID is configured.

- [ ] Write moto tests: 50 reservations succeed and the 51st raises `AiBudgetExhausted`; the counter rolls over on the next UTC day; two owners have independent counters; a disabled, deleted, invalid, or version-mismatched key raises the right error without incrementing; a changed generation or run ID raises `StaleSyncRun` without incrementing; `mark_key_invalid` with a stale version is a no-op; a pre-seeded "other worker already started today" state takes the retry path.
- [ ] Confirm the tests fail; implement; run focused and full backend tests, lint, and `git diff --check`; commit.

---

### Task 5: Server-side identity matching and reconciliation

**Files:** Create `backend/app/services/ai_reconcile.py`, `backend/tests/test_ai_reconcile.py`; modify `backend/app/services/email_rules.py` only to expose existing private helpers (`would_regress`, and a public wrapper of the thread/requisition filtering in `_match_application`) with no behavior change.

**Interfaces:** `reconcile_extraction(rule_decision, extraction, message, applications) -> RuleDecision` returns a `RuleDecision` with `origin="ai"`, the original rule reason kept in `rule_reason`, and the validated proposal attached. It returns `applied` only when all hold: the reason is in `AUTO_APPLY_REASONS`; the status is evidenced (validated AI status, or the rule's own regex-evidenced status when the AI status is null; a conflict between the two means review); the status is not subject-only; and the server matched exactly one application. Matching uses the same identity rules as `email_rules`: thread link or requisition first (with the existing season/year/role filters); otherwise an exact case-folded company **and** role equality where both have valid evidence and exactly one application matches. Company equality alone, a partial name, or a different role/cycle at the same company never matches. Then the same guards as rule decisions: `would_regress` (covers terminal reopen), season/year conflict. Per-field correction timestamps versus email receipt time are enforced unchanged by the repository `record` path. Anything else is `review_required` with reason `ai_proposal`; `application_id` and `proposed_*` are set on the review only when exactly one application matched (so Accept works), otherwise the proposal alone is stored. The AI path never returns an outcome that creates an application.

- [ ] Write tests: link match applies; requisition match applies; a first email for a manually created application matches by exact company+role and applies; company-only match goes to review; same company with a different role or cycle stays separate; two matching applications go to review; model status contradicting the rule's regex status goes to review; status regression, terminal reopen, and cycle conflict go to review with the match recorded; `subject_only_event`, `nonaffirmative_event`, and `conflicting_roles` never return `applied`; a null status cannot apply (no defaulting to Applied); an application belonging to another owner never appears because only the caller's candidates are passed; the rules' existing test suite passes unchanged after the helper exposure.
- [ ] Confirm the tests fail; implement; run focused and full backend tests, lint, and `git diff --check`; commit.

---

### Task 6: Escalation orchestration and pipeline integration

**Files:** Create `backend/app/services/ai_escalation.py`, `backend/tests/test_ai_escalation.py`, `backend/tests/test_ai_pipeline.py`; modify `backend/app/services/email_processing.py`, `backend/app/services/email_rules.py` (add `origin`, optional `ai` annotation, and `rule_reason` fields to `RuleDecision`, defaulting to current behavior), `backend/app/repositories/dynamodb_email.py`, `backend/app/services/sync_worker.py`, `backend/app/sync_handler.py`, and the existing worker/handler tests only where a new optional parameter is threaded through.

**Interfaces:**

- `AiEscalator.escalate(message, rule_decision, applications) -> RuleDecision`: returns the rule decision unchanged unless the reason is in `ESCALATION_REASONS` and an `AiRuntime` exists. Flow: reserve one request (Task 4) then call the adapter, with at most **2 attempts per message** (a second attempt only after `provider_unavailable`; each attempt reserves again; no retry after `rate_limited` or `invalid_output`); on `invalid_key` mark the key invalid (version-guarded) and stop; validate (Task 1); reconcile (Task 5). A per-invocation circuit breaker stops further provider calls for the rest of the page after a `provider_unavailable`, `rate_limited`, or `invalid_key`, so an outage costs at most one message's attempts rather than ten. The decrypted key is held only in memory for the invocation.
- Failure outcomes keep the rule outcome and reason and add an `ai_note` of `ai_unavailable`, `ai_budget_exhausted`, or `ai_key_invalid` on the stored review item (the `reason` field keeps the rule reason so the UI explanation and `recheck_reviews` stay valid; see Open Decisions). When no key is configured nothing changes and no note is added.
- `process_message` checks for an existing terminal outcome **before** escalating, so replay and duplicate delivery never spend budget (a small public `get_terminal_outcome` on the email repository).
- `DynamoEmailRepository.record` writes `origin = decision.origin`, stores `ai_proposal` (validated fields and quotes only), `ai_provider`, `ai_model`, `rule_reason`, and `ai_note`, and for AI decisions adds a `ConditionCheck` on the Users record for the AI settings version in the same transaction as the application and event writes. If that check fails, the AI result is discarded and a plain rule-reason review is stored with `ai_note = ai_unavailable`. A stale application version on an AI decision is stored as `review_required` `ai_proposal` rather than `retryable`, so an SQS redelivery cannot re-run the provider and re-spend budget.
- The worker passes the escalator (built lazily from `AiRuntime`, the cipher, and the provider factory) through `process_sync_page`; a stale Gmail generation or run still raises `StaleSyncRun` and discards the page; any escalator exception other than `StaleSyncRun` is caught and downgraded to the rule outcome.

- [ ] Write escalator tests with a stub provider: each allowlisted reason calls it once; `forwarded_message`, `unparseable_message`, `status_regression`, `cycle_conflict`, `missing_application`, and every `applied`/`ignored` outcome never call it; a message already recorded does not call it again.
- [ ] Write safety tests: prompt injection text in an email whose obedient stub returns a fabricated quote, an instruction-derived status, and another application's name yields nulled fields and a review item, with the application unchanged and the provider request containing no tools; fabricated evidence and malformed output (invalid JSON, wrong types, extra keys, a `confidence` field) route to review with the rule reason; an `ai_proposal` review carries only validated fields.
- [ ] Write failure tests: an invalid key marks the key invalid, stops AI for later messages in the page, and leaves messages in review; an exhausted budget leaves later messages in review with `ai_budget_exhausted`; a provider timeout (one retry, two reservations) then the breaker stops further calls; in all three cases a clear rule-applied message in the same page is still applied and the sync run completes.
- [ ] Write stale-state tests: the key replaced or deleted between reservation and commit discards the AI result (no application change, review recorded); a Gmail disconnect or generation change during the provider call raises `StaleSyncRun` and nothing is written; a stale application version stores a proposal review and does not raise `RetryableSyncOutcome`.
- [ ] Write isolation tests: owner A's key and counter are never used for owner B's messages; candidate names in the prompt come only from the caller's applications.
- [ ] Confirm the tests fail; implement; run focused and full backend tests (all existing email, worker, handler, and review tests must pass), lint, and `git diff --check`; commit.

---

### Task 7: Settings panel and AI suggestion in the review list

**Files:** Create `frontend/src/settings/AiSettings.tsx`, `frontend/src/settings/AiSettings.test.tsx`; modify `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/App.tsx`, `frontend/src/reviews/ReviewQueue.tsx`, `frontend/src/reviews/ReviewQueue.test.tsx`, `frontend/src/styles.css`, `backend/app/domain/reviews.py`, `backend/app/repositories/dynamodb_reviews.py`, `backend/tests/test_reviews_api.py`, `backend/tests/test_dynamodb_reviews.py`.

**Interfaces:** Client functions `getSettings`, `saveAiSettings`, `deleteAiSettings`. `ReviewItem` gains optional `ai_proposal` (validated fields plus `evidence` quotes), `rule_reason`, `ai_note`, and `ai_provider`; the API exposes them only from the owner's own review item. The panel sits next to `GmailConnection` under the same signed-in condition: provider select (only `available_providers`), a `type="password"` key input with `autocomplete="off"`, an unchecked consent checkbox ("Email text from candidate messages is sent to your chosen provider and billed by them"), Save disabled until consent and a key are present, the hint ("ends in 1234"), key status with an actionable "Key rejected, replace it" state, an enable/disable toggle, Replace and Delete (with confirmation), and usage ("12 of 50 AI requests used today (UTC). This is a request cap, not a monetary cap; your provider bills you separately."). The key input is cleared from component state after a save attempt and never written to storage. In the review list, an `ai_proposal` renders as a block labeled "AI suggestion" with the suggested fields and each evidence quote, as plain text only, plus a short note when `ai_note` is set ("AI was not used: daily limit reached"). The existing Accept proposal button works only when `application_id` and `proposed_*` are present; there is no new write path.

- [ ] Write backend tests that the review API returns the AI fields for the owner's items only and that existing review fields are unchanged.
- [ ] Write UI tests: no provider offered when `available_providers` is empty (panel explains AI is unavailable); password field type and cleared-after-save; consent gating; hint and status rendering; invalid-key banner; toggle without re-entering a key; replace and delete confirmations; usage text includes "request cap, not a monetary cap"; API errors shown without echoing the key.
- [ ] Write review tests: the "AI suggestion" label, evidence quotes, and `ai_note` render; markup-like evidence text is shown as text, not HTML; a rule-only item looks as before.
- [ ] Confirm the tests fail; implement; run frontend lint, tests, typecheck, build, `npm audit`, and backend tests; commit.

---

### Task 8: Infrastructure, timing, documentation, and handoff

**Files:** Modify `infra/template.yaml`, `scripts/deploy.sh`, `scripts/test_deploy.py`, `.env.deploy.example`, `backend/app/repositories/dynamodb_sync.py` (lease duration), `backend/tests/test_dynamodb_sync.py`, `docs/DEVELOPMENT.md`, `docs/IMPLEMENTATION_ROADMAP.md`, `AGENTS.md`, and this plan's checkboxes.

**Interfaces and decisions to implement:**

- SAM parameters `OpenAiModelId`, `AnthropicModelId`, `GeminiModelId`, each default `''` (a provider is offered only when its ID is set, so the deployment ships with AI unavailable). They become `AI_MODEL_OPENAI`, `AI_MODEL_ANTHROPIC`, `AI_MODEL_GEMINI` on the API and worker functions. `deploy.sh` passes them from optional `.env.deploy` variables; `.env.deploy.example` documents them; `test_deploy.py` covers present and absent values. No model ID is written in code or committed config.
- CORS `AllowMethods` currently omits `PUT`; add it, or `PUT /settings/ai` fails in the browser.
- KMS: replace the single Gmail KMS statement with two per role, conditioned on `kms:EncryptionContext:credential_type` (`gmail_refresh_token` and `ai_api_key`). The API role keeps `GenerateDataKey` and `Decrypt`; the worker role gets `Decrypt` for both types. Dispatcher gets neither. Confirm in the change set that Gmail connect still works after the condition is added.
- Users table permissions already allow the needed `GetItem`/`UpdateItem` on the API and worker; no new table or permission beyond KMS.
- Timing: the worker processes 10 messages per page. Worst case is 10 messages x 2 attempts x 20 seconds = 400 seconds of provider time, plus up to 10 Gmail fetches at a 10-second timeout, and the circuit breaker makes the outage case far shorter. Set worker `Timeout: 600`, SQS `VisibilityTimeout: 3600` (AWS guidance of at least 6x the function timeout), and raise the sync lease in `DynamoSyncRepository.claim` from 3 minutes to **12 minutes** so a recovery pass cannot start a second worker (and a second round of budget spend) while a slow page is still running. `BatchSize: 1` is unchanged. Add a small offline guard test that reads the template and the lease constant and fails if lease <= worker timeout or visibility < 6x timeout.
- Docs: `DEVELOPMENT.md` gets the model-ID parameters, the provider-key lifecycle, the privacy statement (what is sent, what is stored, what is never logged), and the exact offline versus live verification boundary; `IMPLEMENTATION_ROADMAP.md` and `AGENTS.md` are updated only after verification.

- [ ] Write the template guard test and the `test_deploy.py` cases first; confirm they fail.
- [ ] Implement template, deploy script, example env, lease change; run `sam validate --template-file infra/template.yaml --lint`, `python3 scripts/test_deploy.py -q`, and backend sync tests; commit.
- [ ] Update `DEVELOPMENT.md`; run every CI command (backend sync/lint/pytest, frontend lint/test/typecheck/build/`npm audit`, SAM lint, offline deploy tests); commit.
- [ ] Request read-only branch review (`codex exec review -c sandbox_mode='"read-only"' --base main`, or against `codex/milestone-8-resilient-sync` while stacked), pass findings to the user, resolve those the user accepts, and commit.
- [ ] After user approval: review the CloudFormation change set (expect no replacements; new parameters, CORS method, KMS statements, worker timeout, queue visibility), deploy to development with all three model IDs blank, and verify `UPDATE_COMPLETE`, health 200, protected `/settings` 401 without a token, CORS preflight allows `PUT`, Gmail connect and sync still work, and `GET /settings` shows no available providers.
- [ ] Update the roadmap status and `AGENTS.md` to the verified state, and commit the clean Milestone 9 handoff.

## Verification Boundary

Complete offline: schema, prompt, validation, three adapters against stubs, budget, reconciliation, pipeline, settings API, UI, and infrastructure lint. Complete deployed: stack updated with AI unavailable by default and existing sync behavior unchanged. **Pending until the owner supplies their own key and model IDs:** a live call to each provider (including structured-output behavior against the real services), a real invalid-key response, real rate-limit behavior, and an observed auto-apply/proposal on a designated test mailbox. These must not be labeled tested before they are run, and live provider results never enter CI or the repository.

## Open Decisions

- **Review reason shape.** Decision 7 suggests reasons like `ai_unavailable`/`ai_budget_exhausted`. This plan keeps the rule reason in `reason` and stores the AI condition in a separate `ai_note`, because replacing `reason` would hide the existing UI explanation and the `recheck_reviews` filter (`conflicting_roles`). Only `ai_proposal` changes `reason`, with the original in `rule_reason`. Confirm or ask for the literal reasons.
- **Which reasons may auto-apply.** Per the table, only `ambiguous_application` and `unknown_stage` can auto-apply; `subject_only_event`, `nonaffirmative_event`, and `conflicting_roles` produce proposals only. This is stricter than decision 3 requires, chosen for the 98% precision target and because these are the cases injected text would target. Widening it is one constant (`AUTO_APPLY_REASONS`).
- **Name-only matching.** Today's rules never match an application by company and role alone without a thread or requisition link. Task 5 allows an exact company+role equality (both with valid evidence, exactly one match) for AI, because otherwise the first email for any manually created application can never auto-apply. This is slightly looser than the rule path; confirm, or restrict AI to link-based matches.
- **Status cue lexicon.** The substring check proves a quote exists, not that it supports the claimed status, so Task 1 adds a small deterministic per-status cue check. Its word lists need the user's review.
- **Retry policy.** Only `provider_unavailable` is retried (once). `rate_limited` and `invalid_output` are not, to avoid spending requests on deterministic failures. Confirm.
- **Quote length.** The 70-character per-quote cap (7 fields stays within the 500-character evidence limit) may null legitimately long status sentences. It is a constant to tune against the synthetic fixtures.
- **No automatic retry of budget-limited reviews.** Messages that hit `ai_budget_exhausted` stay in review; there is no automatic retry the next day. The follow-up "retry with AI" action is the intended fix (see below).
- **Queue visibility.** 3600 seconds follows AWS's 6x guidance but delays SQS-level retries after a worker exception; the 15-minute recovery pass and 12-minute lease compensate. 720 seconds is the alternative (valid because it is at least the timeout).
- **Out of scope, listed as follow-ups:** an explicit "retry with AI" action for existing pending reviews (not small enough to include safely: it needs its own owner-scoped endpoint, the same budget and generation guards, and replay semantics); synthetic classification evaluation in CI and alarms (Milestone 10); a Users-table GSI for connected accounts; renaming the Gmail-named KMS key and env var; prefilling the create-application form from an AI suggestion.
- **Who drives.** The user has not named the Milestone 9 driver and reviewer; AGENTS.md requires one of each.

## Manager Resolutions (2026-10-20)

The owner delegated execution while away. These resolutions are reversible defaults; the owner can revisit any of them.

- **Driver/reviewer:** Claude Code drives, with implementation delegated to subagents that are graded per task. Codex reviews read-only.
- **Review reason shape:** accepted. `reason` keeps the rule reason, AI conditions go in `ai_note`, and only `ai_proposal` replaces `reason` (with `rule_reason` kept).
- **Auto-apply reasons:** accepted as drafted. Only `ambiguous_application` and `unknown_stage` may auto-apply.
- **Name-only matching:** accepted only as specified. It requires an exact case-folded company **and** role, both with valid evidence, exactly one match, and no season/year conflict.
- **Status cue lexicon:** accept the drafted lists for now. The owner should review them before any live key is enabled.
- **Retry policy:** accepted. Only `provider_unavailable` is retried, once.
- **Quote length:** keep 70 characters and tune later against synthetic fixtures in Milestone 10.
- **Budget-limited reviews:** accepted. They stay in review until the follow-up retry action exists.
- **Queue visibility:** 3600 seconds, relying on the 15-minute recovery pass and 12-minute lease.
- **Deploy:** deploy to development with all model IDs blank, so AI is unavailable. That is the same environment the owner already authorized, and it leaves behavior unchanged for the live mailbox. Codex findings are triaged by the manager: clear defects are fixed, and design choices are recorded for the owner.
