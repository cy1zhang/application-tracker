# Local Ollama Review Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an open browser tab run a local Ollama model on eligible pending reviews and store validated suggestions without changing applications.

**Architecture:** The authenticated API builds the existing bounded extraction prompt from a pending review and its Gmail message. The browser calls loopback Ollama sequentially, then submits JSON; the API re-fetches the message, validates evidence, and conditionally saves a suggestion. The existing scheduled sync and hosted AI path remain separate.

**Tech Stack:** FastAPI, Pydantic, DynamoDB, Gmail API, React/TypeScript, Ollama local HTTP API.

**Spec:** `docs/superpowers/specs/2026-10-21-local-ollama-review-design.md`

## Global Constraints

- The local path never mutates an application or resolves a review.
- Only the five reasons in `ESCALATION_REASONS` are eligible; reviews with `ai_proposal` are skipped.
- The backend owns Gmail tokens and validates every model value and quote. The browser sends no Gmail credentials to Ollama.
- Local calls use `127.0.0.1:11434`; users must allow the exact hosted origin through `OLLAMA_ORIGINS` while leaving `OLLAMA_HOST` on loopback.
- Firefox on the owner's macOS is the initial deployed browser target.
- No raw email body, prompt, or local model output is logged or persisted beyond validated suggestion fields and short evidence quotes.

## Review Focus

- A review resolved while Ollama is running must reject the suggestion write (Task 2).
- A Gmail disconnect/reconnect during the model call must reject the suggestion write (Task 2).
- A browser response that invents an application ID or unsupported quote must not influence the saved suggestion (Task 2).
- An Ollama failure mid-batch must leave previous suggestions intact and show progress accurately (Task 3).
- Starting the batch twice or refreshing the tab must not overwrite a saved suggestion (Tasks 2-3).

---

### Task 1: Loopback feasibility on the hosted origin

**Files:**
- Modify: `docs/DEVELOPMENT.md` (local Ollama setup after proof)

**Interfaces:**
- Consumes: the deployed Amplify origin and the locally installed `gemma4:latest` model.
- Produces: a verified Firefox connection from the hosted HTTPS origin to `http://127.0.0.1:11434/api/tags`, with origin permissions limited to that origin.

- [x] **Step 1: Probe Ollama's loopback service and CORS preflight** with synthetic requests. Verify the service is present and an arbitrary origin is rejected.
- [x] **Step 2: Allow only the exact hosted origin** in the user's local Ollama configuration, keeping `OLLAMA_HOST` at loopback; verify a preflight for that origin succeeds.
- [ ] **Step 3: From Firefox on the hosted app**, make a synthetic local `/api/tags` call and a synthetic structured `/api/chat` call. Record whether browser private-network and mixed-content controls permit them. If this fails, stop and revise the spec before adding API routes.
- [x] **Step 4: Document the verified setup** in `docs/DEVELOPMENT.md`, run `git diff --check`, and commit the setup guide (`c4b6349`).

### Task 2: Owner-scoped local suggestion API

**Files:**
- Modify: `backend/app/api/reviews.py`, `backend/app/domain/reviews.py`, `backend/app/repositories/dynamodb_reviews.py`, `backend/app/api/dependencies.py`
- Create: `backend/app/services/local_review_ai.py`
- Test: `backend/tests/test_reviews_api.py`, `backend/tests/test_dynamodb_reviews.py`, `backend/tests/test_local_review_ai.py`

**Interfaces:**
- Produces: `GET /reviews/{message_id}/local-ai-input` returning `{system_prompt, user_prompt, format, generation}` for a still-pending eligible review; `POST /reviews/{message_id}/local-ai-suggestion` accepting `{generation, model, extraction}` and returning the updated `ReviewItem`.
- The service builds prompts with `build_prompt`, validates with `validate_extraction`, and derives a review-only result with `reconcile_extraction`; it never passes the result to the email auto-apply path.
- The repository exposes a pending-review read and a conditional suggestion update, guarded by pending outcome, no resolution, no existing proposal, original reason, and the Gmail generation.

- [x] **Step 1: Write failing API and repository tests** for the two endpoints, owner isolation, eligible reasons, unresolved-only reads, stale/resolved writes, generation change, invented identity, and invalid evidence. Use synthetic Gmail messages and injected dependencies.
- [x] **Step 2: Run focused backend tests** and confirm failures are from the missing interfaces: `cd backend && uv run pytest tests/test_reviews_api.py tests/test_dynamodb_reviews.py tests/test_local_review_ai.py -q`.
- [x] **Step 3: Implement the smallest service, routes, and conditional repository update.** Re-fetch Gmail on submission; keep model output out of logs. Return 404 for another owner's review, 409 for stale state, and 422 for malformed output.
- [x] **Step 4: Run focused tests and lint**: `cd backend && uv run pytest tests/test_reviews_api.py tests/test_dynamodb_reviews.py tests/test_local_review_ai.py -q && uv run ruff check .`.
- [x] **Step 5: Commit** `feat: save validated local AI review suggestions` (`08163a5`).

### Task 3: Browser Ollama client and review batch control

**Files:**
- Create: `frontend/src/reviews/localOllama.ts`, `frontend/src/reviews/localOllama.test.ts`
- Modify: `frontend/src/reviews/ReviewQueue.tsx`, `frontend/src/reviews/ReviewQueue.test.tsx`, `frontend/src/api/client.ts`, `frontend/src/api/types.ts`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 2 API. Calls Ollama `/api/tags` for installed model names and `/api/chat` with `{model, messages, stream:false, format}`. Sends parsed JSON and the generation to the API.
- Produces: an explicit **Analyze with Ollama** control, model choice, sequential progress, Stop, setup/errors, and a warning if hosted AI is enabled.

- [x] **Step 1: Write failing frontend tests** for model detection, JSON response parsing, eligible/skipped counts, sequential calls, stop/restart, saved-suggestion refresh, offline/CORS error, and hosted-AI warning.
- [x] **Step 2: Run focused frontend tests** and confirm missing behavior: `cd frontend && npx vitest run src/reviews/localOllama.test.ts src/reviews/ReviewQueue.test.tsx`.
- [x] **Step 3: Implement the local client and minimal review controls.** Use `AbortController` for Stop; keep prompt/model response in component memory only; never use localStorage/sessionStorage for them.
- [x] **Step 4: Run focused tests, lint, typecheck, and build**: `cd frontend && npx vitest run src/reviews/localOllama.test.ts src/reviews/ReviewQueue.test.tsx && npm run lint && npm run typecheck && npm run build`.
- [x] **Step 5: Commit** `feat: analyze pending reviews with local Ollama` (`b7268a3`).

### Task 4: Whole-feature verification and handoff

**Files:**
- Modify: this plan's checkboxes and verification notes; `docs/IMPLEMENTATION_ROADMAP.md` only after verified implementation.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: a clean branch with exact local/browser evidence and unresolved live-test boundary.

- [x] **Step 1: Run repo CI-equivalent checks**: backend `uv run ruff check . && uv run pytest -q`; frontend `npm run lint && npm test && npm run typecheck && npm run build && npm audit`; `sam validate --template-file infra/template.yaml --lint`; `python3 scripts/test_deploy.py -q`; `git diff --check`.
- [ ] **Step 2: Test a synthetic message end to end** against local Ollama in the hosted Firefox app. Verify a suggestion appears and no application changes. Do not run real-mailbox batch analysis without a new explicit user action.
- [x] **Step 3: Record results and limits**, request one read-only branch review, fix accepted defects, and commit each focused change.
- [x] **Step 4: Leave a clean tree and report the branch and exact verification results.** Do not merge without the owner's approval.

## Verification notes (2026-10-21)

- Backend: 803 tests passed; Ruff passed. Frontend: 134 tests passed; ESLint, TypeScript, Vite build, and npm audit passed. SAM lint and 14 offline deploy tests passed. The focused frontend tests also passed after the last progress-copy change.
- Synthetic local model request using the production prompt/schema returned a valid extraction from `gemma4:latest`. The loopback Ollama service accepted only the configured Amplify origin in the CORS preflight. The hosted Chrome tab listed the installed model after the owner allowed local service access.
- Read-only review found missing API `ConditionCheckItem` permission, stale review acceptance, and batch error/refresh issues. Fixed in `de05505` and `0a2edf3` and covered by focused tests. The accept action now requires the version of the review proposal shown to the user.
- Redeployed `app-tracker-dev` and the Amplify frontend. Hosted app and API health returned 200; both new API routes returned 401 without authentication. The deployed API IAM policy includes `dynamodb:ConditionCheckItem` on the Users table. After reloading the hosted Chrome page, **Needs review → Analyze with Ollama** showed the installed `gemma4:latest` model and an enabled Start analysis control.
- Firefox browser access could not be observed through the available UI controller. A hosted browser test that saves a suggestion from a synthetic Gmail message is still open. No real-mailbox batch was started.

### Follow-up fixes after audit (2026-10-21)

- **Batch continues past per-review failures.** The API now returns stable `detail` codes. Per-review: 503 `message_unavailable` (Gmail message deleted or unparseable), 409 `review_changed` (no longer eligible, thread mismatch, version conflict), 422 invalid model output, 404 review not found. Batch-level: 503 `gmail_connection_unavailable` (disconnected, reconnect required, token/OAuth/Gmail API failure) and 409 `gmail_connection_changed` (generation changed). The panel counts per-review failures, remembers their ids in component memory only (cleared on reload), and keeps going; it stops only on Ollama unreachable, 401/auth or server unreachable, the two batch-level Gmail codes, or Stop. Invalid model JSON is a per-review failure.
- **Analysis count.** "Reviews to analyze" (default 5, min 1, max eligible count) limits how many eligible, not-previously-failed reviews Start processes, in queue order. `num_predict` and other Ollama options are unchanged.
- Evidence: backend 810 passed and Ruff clean; frontend 146 tests passed with ESLint, TypeScript, and build clean. New tests cover continue past 503/422/409/invalid JSON, stop on Ollama unreachable/401/Gmail disconnect/connection change, failed ids skipped on the next Start, and the limit (default, cap of 5, exactly 1, failed ids not counted).
- **Review fixes.** A Gmail generation change during the save now returns 409 `gmail_connection_changed` (not `review_changed`). Only failures about one review (422, `review_changed`, `message_unavailable`, invalid Ollama JSON or HTTP error) are remembered as failed; 429 and 502 stop the batch, and other API errors count as failed but are retried. Failed ids now live in `ReviewQueue`, so they survive closing the panel. Evidence: backend 813 passed, Ruff clean; frontend 157 tests passed, lint, typecheck, build clean.
