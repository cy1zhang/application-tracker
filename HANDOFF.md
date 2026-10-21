# Handoff — local Ollama review suggestions

**Updated:** 2026-10-21 (America/Indiana/Indianapolis)

**Checkout:** `/Users/alexz/application-tracker`

**Branch:** `codex/local-ollama-review` (stacked on `codex/huggingface-provider`; unmerged, not pushed)

**State:** Implemented, deployed to `app-tracker-dev` from `7e1cda6`, and accepted on one owner-approved real review through the deployed API. Working tree clean after this handoff commit.

## User decision and scope

The owner chose a no-fee local Ollama route after Hugging Face returned HTTP 402, with **suggestions only**: the browser analyzes eligible pending reviews with a model on the same Mac, and the model never creates, edits, accepts, rejects, or resolves an application. The owner decides each suggestion. Only the five AI escalation reasons are eligible; this does not clear the rest of the review backlog.

Dev data at handoff (counts only): 1 application, 386 pending reviews, **343 eligible** for Ollama, **1** saved Ollama suggestion (from the acceptance test, still pending for the owner to accept or reject).

## What changed in this session (Claude Code)

- `2526f16`, `c904f8b`, `6bf610b`, `32eb980`, `f37152c` — the batch continues past per-review failures and stops only on global ones (Ollama unreachable, sign-in, Gmail connection unavailable/changed, 429/502, Stop). The API returns stable detail codes (`message_unavailable`, `gmail_connection_unavailable`, `gmail_connection_changed`, `review_changed`). A **Reviews to analyze** cap (default 5) was added. Failed reviews are skipped until page reload.
- `6999052` — **Create application** from a review is prefilled with only the values the suggestion or review carries; unknown values stay empty, status is never defaulted.
- `7e1cda6` — reviews with a suggestion are listed first, with a count line, so new suggestions are visible without paging.
- `a228222` — `docs/DEVELOPMENT.md` explains that `launchctl setenv OLLAMA_ORIGINS` is lost on reboot and gives a LaunchAgent to reapply it; adds a "Using it" section.
- `7cdb8db` — vitest 4.1.12 to clear new dev-only audit advisories.

## Verification

- Backend 813 tests and Ruff; frontend 165 tests, ESLint, TypeScript, build, `npm audit` (0); SAM lint; 14 deploy tests. Hosted app and API health 200; new routes 401 unauthenticated.
- Ollama on this Mac (M3, 16 GB, Ollama.app 0.34.4, `gemma4:latest`): loopback-only, CORS allows only the Amplify origin. About 50 s for the first review (model load), about 27 s warm; the model unloads after ~5 min idle.
- **Real-review acceptance:** the deployed API Lambda was invoked directly with the owner's `sub` (Claude in Chrome was not connected), mirroring the browser's calls. One review: input 200, Ollama 25.7 s with valid JSON, suggestion 200 `saved=true`, review still pending with `ai_provider=ollama`; applications count and version digest unchanged. Details are in the plan's verification notes.
- **Not verified:** the panel driving this from a hosted browser tab end to end, and Firefox. The LaunchAgent is documented, not installed.

## Next

1. Owner: open the dev app in Chrome, **Needs review → Analyze with Ollama**, run 1–5 reviews, and check the saved suggestion (accept/reject/create). This also closes the hosted-browser gap.
2. Owner: try the same page in Firefox; allow local network access only for this app if prompted.
3. Owner decision: install the reboot LaunchAgent from `docs/DEVELOPMENT.md` (changes macOS login configuration).
4. Ask the owner before merging; the milestone branches are stacked and unmerged.

Plan: `docs/superpowers/plans/2026-10-21-local-ollama-review.md`. Spec: `docs/superpowers/specs/2026-10-21-local-ollama-review-design.md`.
