# Handoff to Claude Code — local Ollama review suggestions

**Updated:** 2026-10-21 (America/Indiana/Indianapolis)

**Checkout:** `/Users/alexz/application-tracker`

**Branch:** `codex/local-ollama-review` (stacked on `codex/huggingface-provider`; unmerged)

**State at handoff:** Implemented and deployed to the development stack; working tree clean after this handoff commit. Browser-to-saved-suggestion acceptance remains open.

## User decision and scope

The user chose a no-provider-fee local Ollama route after Hugging Face Inference Providers returned a credit-related HTTP 402. They chose **suggestions only**: the browser may analyze eligible pending reviews with a model running on the same Mac, but the model must never create, edit, accept, reject, or resolve an application. The user separately decides what to do with each saved suggestion. They approved Chrome's prompt allowing the hosted app to access local services on this device.

This is an explicit, tab-driven action, not a scheduled job. **No real-mailbox analysis batch has been started.** The last observed development review count was 386. The local path analyzes only the five existing AI escalation reasons and does not clear unrelated review items. Do not represent it as a repair for the whole review backlog. The earlier review-queue cleanup was completed and deployed; its measured results are in `docs/superpowers/plans/2026-10-20-review-queue-cleanup.md`.

## Implementation now on this branch

- `backend/app/services/local_review_ai.py` prepares the bounded extraction prompt from the owner's pending review and Gmail message. On submission it re-fetches Gmail, validates model values and evidence quotes, reconciles candidates, and requests a **review-only** suggestion write.
- `backend/app/api/reviews.py` exposes authenticated `GET /reviews/{message_id}/local-ai-input` and `POST /reviews/{message_id}/local-ai-suggestion`. Another owner's review is hidden; stale state, unavailable Gmail, and invalid output have distinct responses.
- `backend/app/repositories/dynamodb_reviews.py` saves the suggestion conditionally with the Gmail connection generation and increments `review_version`. Accepting a proposal requires the review version the user saw, so a stale tab cannot accept a newly saved AI proposal. The API role has `dynamodb:ConditionCheckItem` on the Users table.
- `frontend/src/reviews/localOllama.ts`, `LocalOllamaPanel.tsx`, and `ReviewQueue.tsx` provide model detection, sequential `/api/chat` calls to `127.0.0.1:11434`, Start/Stop, progress and errors, and a queue refresh. The browser does not receive the Gmail token or persist the prompt/model output in storage.
- Local setup is in `docs/DEVELOPMENT.md`; design and checkboxes are in `docs/superpowers/specs/2026-10-21-local-ollama-review-design.md` and `docs/superpowers/plans/2026-10-21-local-ollama-review.md`.

Key commits: `08163a5` (API), `b7268a3` (browser flow), `53883ce` (disable model thinking for structured output), `de05505` (API IAM permission), `0a2edf3` (stale accept and batch refresh), `225e878` (verification record).

## Verification boundary

- Full local checks passed: **803 backend tests**, **134 frontend tests**, Ruff, ESLint, TypeScript, Vite build, npm audit, SAM lint, and **14 offline deploy tests**. Focused frontend tests passed again after the final progress-copy edit. See the active plan for exact evidence and the one existing Mangum deprecation warning.
- A synthetic local `gemma4:latest` request using the production prompt/schema returned a validated extraction. Ollama CORS accepted the exact Amplify origin and rejected an arbitrary origin; it remained bound to loopback.
- The updated `app-tracker-dev` stack and Amplify frontend deployed successfully. The hosted app and API health returned HTTP 200; both new routes returned HTTP 401 without authentication. The deployed API role's Users-table `ConditionCheckItem` permission was checked in IAM.
- In the signed-in hosted **Chrome** tab, **Needs review → Analyze with Ollama** listed `gemma4:latest` and enabled Start analysis after the owner approved local-service access. Development app: <https://main.d2pqicgf5e8l1p.amplifyapp.com/>.
- **Still unverified:** Firefox access to Ollama and a hosted browser run that saves a suggestion from a synthetic Gmail message while leaving the application unchanged. The Firefox UI controller timed out; do not infer Firefox support from Chrome. No real Gmail message has been sent to Ollama in this feature test.

## What Claude Code should do next

1. Read `AGENTS.md`, the active local Ollama plan, `git status`, and recent commits. Treat this branch as the current driver checkout; avoid parallel edits in the same tree. The roadmap and `AGENTS.md` project-status paragraph predate this local Ollama branch.
2. Close the remaining browser acceptance gap if a synthetic Gmail-backed review can be exercised safely. Record exactly which browser, API path, saved review, and unchanged application state were observed. If the test needs a real mailbox item, get an explicit user action before starting the batch; **do not click Start analysis on the 386-item queue as a smoke test**.
3. Fix any concrete failure with a synthetic regression, run the relevant checks, and commit each verified change. Keep email bodies, tokens, personal sender/subject data, and message IDs out of Git, fixtures, logs, and terminal output.
4. Ask the user before merging. The milestone branches are stacked and unmerged; inspect ancestry before any integration or rebase. The development deployment does not mean the branch is merged or production-ready.
