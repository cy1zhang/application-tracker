# Plan 1: Answer Key and Baseline Score

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Codex-labeled answer key from about 200 of the owner's real emails, kept only on the Mac, and score today's pipeline against it.

**Architecture:** The Mac tools sign in with a dedicated Cognito app client (PKCE, loopback redirect, refresh token in the Keychain) and call two new owner-scoped API routes. The routes list processed messages and re-fetch one message's parsed content from Gmail. A local export writes a stratified sample under the git-ignored `.local/answer-key/`. Codex labels it read-only, and a scorer replays the sample through the real pipeline on moto with local Ollama.

**Tech Stack:** FastAPI, DynamoDB/moto, AWS SAM (Cognito), Python 3.12 + uv, `keyring` (macOS Keychain), Ollama `/api/chat`, Codex CLI.

**Spec:** `docs/superpowers/specs/2026-10-24-auto-apply-sheet-design.md` (sections 7 and 8; "Calls made").

## Global Constraints

- Owner identity comes only from the verified Cognito `sub`. No header or parameter chooses the owner.
- No route may read a Gmail message that has no `gmail#<message_id>` item for that owner.
- Real email content, company or role values, and message ids never go into Git, logs, CI, fixtures, or terminal output.
- Answer-key files live only under the repo-root `.local/`, which is git-ignored.
- The raw email copies (`.local/answer-key/emails/`) are deleted after labeling. Labels and the id index stay.
- Mac tools never use AWS admin credentials.
- Local Ollama is called only at `http://127.0.0.1:11434`, with `think: false`, `stream: false`, `temperature: 0` and `num_predict: 512`, matching `frontend/src/reviews/localOllama.ts`.
- Tests use synthetic data only.
- `MessageContent.text` is capped at 8000 characters, while the production rules see up to 16384. Answer-key scoring therefore sees truncated bodies, a rare fidelity gap.

## Review Focus

- **A message deleted from Gmail after import.** The content route returns 503 `message_unavailable`, and the export and scorer skip that message and count it. Covered in Task 1 and Task 5.
- **An expired or revoked refresh token.** `access_token()` raises `LoginRequired`, and the CLI prints "Run: uv run python -m local_tools login". It must not show a traceback containing the token. Covered in Task 2.
- **A Codex batch that returns malformed JSON or unknown ids.** `validate` rejects the batch with the line number. It never merges a partial batch silently. Covered in Task 4.
- **A cross-owner message id.** The content route returns 404, the same as an unknown id, and never reveals that the message exists. Covered in Task 1.
- **An interrupted scorer run.** Ollama outputs are cached by prompt hash, so a re-run resumes without new model calls for cached items. Covered in Task 5.

---

### Task 1: Owner-scoped message list and content routes

**Files:**
- Create: `backend/app/domain/messages.py`, `backend/app/services/message_content.py`, `backend/app/api/messages.py`
- Modify: `backend/app/main.py` (register router), `backend/app/api/dependencies.py` (service factory), `backend/app/repositories/dynamodb_email.py` (list method)
- Test: `backend/tests/test_messages_api.py`, `backend/tests/test_message_content.py`

**Interfaces:**
- Produces:
  - `MessageSummary(message_id: str, outcome: str, reason: str, received_at: datetime | None)`.
  - `MessagePage(items: list[MessageSummary], next_cursor: str | None)`.
  - `MessageContent(message_id, thread_id, received_at, subject, sender, text)`. `text` is capped at `BODY_LIMIT` (8000).
  - `DynamoEmailRepository.list_processed(owner_id, *, limit: int, cursor: str | None) -> tuple[list[dict], str | None]`, which queries `begins_with(event_id, "gmail#")`.
  - `MessageContentService.get(owner_id, message_id) -> MessageContent`, which raises `MessageNotFound`, `LocalAiMessageUnavailable` or `LocalAiUnavailable`. Reuse the token/fetch steps from `LocalReviewAiService._load` (`app/services/local_review_ai.py:~93`). Extract a shared helper `fetch_owner_message(owner_id, message_id) -> ParsedGmailMessage` rather than copying it.
  - Routes:
    - `GET /messages?limit=100&cursor=` returns `MessagePage`.
    - `GET /messages/{message_id}/content` returns `MessageContent`. Its errors are 404 `Message not found`, 503 `message_unavailable` and 503 `gmail_connection_unavailable`, the same detail codes as `reviews.py`.

- [ ] **Step 1: Write failing tests**
  - `test_list_messages_returns_only_owner_gmail_items`: seed `gmail#a` for owner-1, `gmail#b` for owner-2 and a non-gmail event for owner-1. Assert owner-1 sees exactly `["a"]` with its outcome and reason.
  - `test_list_messages_paginates_with_opaque_cursor`: with limit 2 over 3 items, the second page has 1 item and `next_cursor is None`. A bad cursor returns 422.
  - `test_content_returns_parsed_fields_for_processed_message`: a stub fetch returns a synthetic Gmail payload. Assert subject, sender, `text`, `thread_id` and `received_at`.
  - `test_content_404_for_unprocessed_or_other_owner`: both cases return 404 with an identical body.
  - `test_content_503_codes`: a `MessageUnavailable` from fetch returns `message_unavailable`. A disconnected connection returns `gmail_connection_unavailable`.
  - `test_content_text_capped_at_body_limit`: a 9000-character body gives `len(text) == 8000`.
- [ ] **Step 2: Run** `cd backend && uv run pytest tests/test_messages_api.py tests/test_message_content.py -q`. Expected: FAIL (module not found).
- [ ] **Step 3: Implement** the domain models, `list_processed`, `fetch_owner_message`, `MessageContentService` and the router. `LocalReviewAiService` should call `fetch_owner_message` so behavior stays identical. Run `tests/test_local_review_ai.py` to confirm.
- [ ] **Step 4: Run** `uv run pytest -q && uv run ruff check .`. Expected: all pass.
- [ ] **Step 5: Commit** `feat: list processed messages and fetch owner message content`.

### Task 2: Mac sign-in and API client

**Files:**
- Modify: `infra/template.yaml`. Add a `LocalToolsClient` (`AWS::Cognito::UserPoolClient`):
  - `GenerateSecret: false`, `AllowedOAuthFlows: [code]`, scopes `openid email`.
  - `CallbackURLs: [http://localhost:8765/callback]`, `LogoutURLs: [http://localhost:8765/]`.
  - `RefreshTokenValidity: 30` (days), same pool and domain. Output it as `LocalToolsClientId`.
  - The API authorizer audience must accept both client ids.
- Modify: `scripts/test_deploy.py` or `backend/tests/test_infra_*.py` (assert the client exists and the audience includes it), `backend/pyproject.toml` (new dependency group `local = ["keyring>=25"]`), `.gitignore` (add `.local/` and `.env.local-tools`).
- Create: `backend/local_tools/__init__.py`, `__main__.py` (subcommands `login`, `logout`), `config.py`, `auth.py`, `api.py`; `.env.local-tools.example` (placeholders: `TRACKER_API_URL`, `COGNITO_DOMAIN`, `COGNITO_CLIENT_ID`).
- Test: `backend/tests/test_local_tools_auth.py`, `backend/tests/test_local_tools_api.py`

**Interfaces:**
- Produces:
  - `AuthConfig(api_base_url: str, cognito_domain: str, client_id: str, redirect_uri: str = "http://localhost:8765/callback")` and `load_config(path=".env.local-tools") -> AuthConfig`.
  - `pkce_pair() -> tuple[str, str]` returns a verifier and an S256 challenge.
  - `login(config, *, open_browser=webbrowser.open) -> None` runs a one-shot loopback server on 8765, checks `state`, exchanges the code at `/oauth2/token` and stores the refresh token with `keyring.set_password("application-tracker", client_id, token)`.
  - `access_token(config) -> str` refreshes and caches in memory until 60 s before expiry. It raises `LoginRequired` when the token is missing or refresh returns `invalid_grant`.
  - `TrackerApi(config, token=access_token)` provides `.get(path, params=None) -> dict` and `.post(path, body) -> dict`. It raises `ApiError(status: int, detail: str)`. `detail` is the response's `detail` string when it matches `[a-z0-9_ ]{1,80}`, otherwise `"other"`. It retries once on 401 after a forced refresh.

- [ ] **Step 1: Write failing tests**
  - `test_pkce_challenge_is_s256_of_verifier`.
  - `test_login_rejects_mismatched_state`: a fake callback with the wrong `state` raises `ValueError`, and the keyring is untouched.
  - `test_login_stores_refresh_token` uses a fake token endpoint and an in-memory keyring backend.
  - `test_access_token_refreshes_and_caches`: two calls hit the token endpoint once.
  - `test_access_token_raises_login_required_on_invalid_grant`.
  - `test_api_error_detail_is_sanitized`: a detail of `"Some <html> text"` becomes `"other"`.
  - `test_api_retries_once_on_401`.
  - Infra: `test_local_tools_client_has_loopback_callback_and_30_day_refresh`.
- [ ] **Step 2: Run** `uv run --group local pytest tests/test_local_tools_auth.py tests/test_local_tools_api.py -q` and the infra test. Expected: FAIL.
- [ ] **Step 3: Implement** the code. Use `urllib` and `http.server` from the standard library, and do not add an HTTP client dependency. The callback page says only "Signed in. You can close this tab." Error messages must never include tokens.
- [ ] **Step 4: Run** the backend tests, `ruff`, `sam validate --template-file infra/template.yaml --lint` and `python3 scripts/test_deploy.py -q`. Expected: pass.
- [ ] **Step 5: Commit** `feat: add Mac sign-in and API client for local tools`.
- [ ] **Step 6 (operator, owner present):** deploy dev (`./scripts/deploy.sh`). Fill in `.env.local-tools` from the stack outputs. The owner runs `cd backend && uv run --group local python -m local_tools login` once. Verify `uv run --group local python -c "from local_tools.api import TrackerApi; from local_tools.config import load_config; print(len(TrackerApi(load_config('../.env.local-tools')).get('/messages', {'limit': 1})['items']))"` prints `1`.

### Task 3: Stratified export

**Files:**
- Create: `backend/evaluation/answer_key/__init__.py`, `export.py`
- Test: `backend/tests/test_answer_key_export.py`

**Interfaces:**
- Consumes: `TrackerApi` (Task 2), and `GET /messages` and `GET /messages/{id}/content` (Task 1).
- Produces:
  - `select_sample(summaries: list[MessageSummary], *, size: int, seed: int) -> list[str]` returns message ids. Strata quotas for `size=200`:
    - 90 from `ignored/no_event`;
    - 80 from `review_required/ai_proposal`;
    - 20 from other `review_required` reasons;
    - 10 from all other outcomes.
    - A short stratum gives its remainder to `ignored/no_event`.
    - Selection is seeded and deterministic.
  - `export(api, out_dir: Path, *, size=200, seed=20261024) -> ExportSummary(written: int, unavailable: int)`. It writes `out_dir/emails/E001.json` (`{"id","subject","sender","text"}`) and `out_dir/index.json` (`{"E001": "<message_id>"}`). It prints only counts.
  - CLI: `uv run --group local python -m evaluation.answer_key.export --out ../.local/answer-key`.

- [ ] **Step 1: Write failing tests**
  - `test_select_sample_respects_quotas_and_is_deterministic`.
  - `test_select_sample_redistributes_short_strata`.
  - `test_export_writes_numbered_files_and_index`, with a fake api.
  - `test_export_counts_unavailable_and_skips` (503 `message_unavailable`).
  - `test_export_refuses_out_dir_inside_git_tracked_path`: an `out_dir` that is not under `.local/` raises `ValueError`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the code.
- [ ] **Step 4: Run** `uv run --group local pytest tests/test_answer_key_export.py -q && uv run ruff check .`. Expected: pass.
- [ ] **Step 5: Commit** `feat: export stratified answer-key sample locally`.

### Task 4: Rubric, label schema, and Codex labeling

**Files:**
- Create: `backend/evaluation/answer_key/RUBRIC.md`, `labels.py`
- Test: `backend/tests/test_answer_key_labels.py`

**Interfaces:**
- Produces:
  - `Kind = Literal["application_update", "job_alert", "recruiter_outreach", "newsletter_or_event", "other"]`.
  - `Label(id: str, kind: Kind, company: str | None, role: str | None, role_type: RoleType | None, season: Season | None, year: int | None, status: ApplicationStatus | None, requisition_id: str | None, application_key: str | None)` (pydantic, `extra="forbid"`).
  - `load_labels(dir: Path) -> dict[str, Label]` merges `labels/batch-*.jsonl` and `groups.json`. It raises `LabelError("batch-03.jsonl:7: ...")`.
  - CLI: `python -m evaluation.answer_key.labels validate ../.local/answer-key` prints counts per kind and status.

RUBRIC.md must state these points:
- **The owner's definition of an application** (spec, "What counts as an application"):
  - Any email that states a status for a job the owner applied to is `application_update`. This holds even with no confirmation on file.
  - Outreach about a job the owner has not applied to is `recruiter_outreach`.
- **Status** is set only when the email states it.
- **Role names are canonical:** the full title in title case, with "SWE" written out as "Software Engineering", for example "Software Engineering Intern".
- **Cycle** is taken only from the email's own words.
- **Output format** is one JSON object per line, matching `Label` without `application_key`.

- [ ] **Step 1: Write failing tests**
  - `test_label_rejects_unknown_kind_and_extra_fields`.
  - `test_load_labels_reports_file_and_line_on_bad_json`.
  - `test_load_labels_rejects_ids_not_in_index`.
  - `test_groups_assign_application_key_only_to_application_updates`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** `labels.py` and write `RUBRIC.md`.
- [ ] **Step 4: Run** the tests and `ruff`. Expected: pass.
- [ ] **Step 5: Commit** `feat: define answer-key rubric and label schema`.
- [ ] **Step 6 (operator):** run the export (Task 3). The owner has already approved sending this sample to OpenAI.
- [ ] **Step 7 (operator): label in batches of 20.** For each batch NN:
  ```bash
  codex exec -s read-only --ephemeral -o ../.local/answer-key/labels/batch-NN.jsonl "$(cat evaluation/answer_key/RUBRIC.md) Label these files, one JSON line each: ../.local/answer-key/emails/E0NN1.json ..."
  ```
  Then run `labels validate`. Re-run any rejected batch.
- [ ] **Step 8 (operator): grouping pass.** Write `{id, company, role, season, year}` for the `application_update` labels only, with no email text, to `.local/answer-key/group-input.json`. Ask Codex for `groups.json` (`{"E001": "app-1", ...}`), so that the same job gets the same key. Validate again.
- [ ] **Step 9 (operator):** after labeling and scoring, delete both `.local/answer-key/emails/` and `.local/answer-key/cache/`. Keep `index.json`, `strata.json`, `labels/` and `groups.json`. If an export fails midway, delete `emails/` before re-running. Record the counts (per kind, per status, number of application keys) under "Results" below. Do not record any values.

### Task 5: Scorer and baseline

**Files:**
- Create: `backend/evaluation/answer_key/ollama.py`, `score.py`
- Test: `backend/tests/test_answer_key_score.py`

**Interfaces:**
- Consumes:
  - `load_labels` (Task 4) and the content route (Task 1).
  - `process_message` (`app/services/email_processing.py:32`).
  - The provider protocol `extract(api_key, model, prompt_input) -> object`, as `StubProvider` in `tests/ai_support.py` shows.
- Produces:
  - `OllamaProvider(model="gemma4:latest", cache_dir: Path)`.
    - `.extract(api_key, model, prompt_input) -> dict` sends `{model, messages:[system,user], stream:false, format: EXTRACTION_JSON_SCHEMA, think:false, options:{temperature:0, num_predict:512}}`.
    - The cache key is `sha256(system + user + model)`, and the cache stores the JSON output under `cache_dir`.
  - `EmailOutcome(id: str, action: Literal["created","updated","ignored","needs_look"], row: str | None, status, company, role, season, year)`. `row` is the opaque in-run id of the application the action touched.
  - `PipelineRun` is a protocol: `run(items: list[tuple[str, MessageContent]]) -> list[EmailOutcome]`, where the items are in `received_at` order.
  - `CurrentPipeline(provider)` replays today's `process_message` on moto, using an `Env` like the one in `tests/ai_support.py` with AI enabled and the provider injected. It starts with zero applications and maps stored outcomes to actions.
  - `score(labels, outcomes) -> AnswerKeyReport` returns these figures:
    - `precision`: correct automatic changes divided by automatic changes. An automatic change is a `created` or `updated` action. It is correct when the label is `application_update`, the row maps to that label's `application_key`, and every changed field equals the label's value.
    - `recall`: `application_update` emails that were changed correctly or sent to `needs_look`, divided by all `application_update` emails.
    - `fake_rows`: rows whose first email's label is not `application_update`.
    - `duplicate_rows`: the number of extra rows per `application_key`.
    - `merged_rows`: rows that hold two or more keys.
    - `wrong_status`.
    - `kind_confusion: dict[str, dict[str, int]]`.
  - CLI: `python -m evaluation.answer_key.score --dir ../.local/answer-key --pipeline current [--check]`.
    - It prints only the report numbers.
    - `--check` exits 1 unless precision >= 0.98 and recall >= 0.90.
    - It skips and counts unavailable messages.

- [ ] **Step 1: Write failing tests** on synthetic labels and outcomes, with no Ollama:
  - `test_precision_counts_only_fully_correct_changes`.
  - `test_fake_row_counted_when_first_email_not_application`.
  - `test_duplicate_and_merged_rows`.
  - `test_recall_includes_needs_look`.
  - `test_check_thresholds_exit_code`.
  - `test_ollama_provider_uses_cache_on_second_call`, with a fake HTTP server.
  - `test_current_pipeline_creates_no_rows_from_empty_start`: a synthetic "thank you for applying" email gives `needs_look` or `ignored`, never `created`. This pins today's behavior as the baseline.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the code.
- [ ] **Step 4: Run** `uv run --group local pytest -q && uv run ruff check . && uv run python -m evaluation.run --check`. Expected: pass, with the existing CI gates unchanged.
- [ ] **Step 5: Commit** `feat: score pipelines against the local answer key`.
- [ ] **Step 6 (operator):** run `score --pipeline current` and record the numbers under "Results". The run takes about 90 minutes uncached. Recall is expected to be very low, which is the baseline. After the baseline is recorded, delete `.local/answer-key/cache/`; a later re-score re-fetches the content.

## Results

_(Operator fills in counts only.)_
