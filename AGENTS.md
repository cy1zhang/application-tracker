# AGENTS.md

Guidance for coding agents (Claude Code, Codex, etc.) working in this repository.

## Project status

Application Tracker is a job-application tracker that will eventually sync Gmail
and classify recruiting email. It is built in milestones defined in
`docs/IMPLEMENTATION_ROADMAP.md`; **build only the active milestone and do not
scaffold later subsystems early.** Milestones 0–5 (foundation through Gmail
connection) were implemented and verified in the development deployment.
Milestone 6 deterministic email processing is complete offline and integrated
locally. Milestone 7 review and reconciliation and Milestone 8 Gmail import,
SQS sync, incremental history, daily dispatch, and recovery are deployed in
development from `codex/milestone-8-resilient-sync`. The initial live Gmail
import, empty incremental repeat, and scheduled recovery were verified;
remaining Gmail and review-flow checks are pending. Milestone 9 BYOK AI
escalation (OpenAI, Anthropic, and Gemini providers) is implemented on
`milestone-9-byok-ai` and deployed in development with AI disabled (no model
IDs configured); live provider checks are pending the owner's own key. Milestone 10
(sanitized logging, account deletion, alarms, synthetic evaluation in CI) is
implemented on `milestone-10-harden-evaluate` and deployed in development;
owner-run deletion and revocation checks, live AI, and real-inbox quality are
pending (`docs/VERIFICATION.md`). The three milestone branches are stacked and
unmerged, awaiting the owner. A Hugging Face provider integration is implemented
on `codex/huggingface-provider` and deployed in development; live model
verification is blocked by a Hugging Face credit-related HTTP 402 on the first
synthetic request. A local Ollama review integration is on `codex/local-ollama-review`, and the
automatic tracking milestone (local AI, spreadsheet) is on `claude/auto-apply-sheet`;
both are stacked and unmerged. The dashboard offers review,
merge, sync, AI settings, and account deletion controls.

Key docs: `docs/PRD.md` (product behavior), `docs/TECHNICAL_DESIGN.md` (target AWS
architecture, tables, API surface, sync/classification rules),
`docs/DEVELOPMENT.md` (local setup). Implementation plans live in
`docs/superpowers/plans/`.

## Commands

Pinned toolchain: Python 3.12.13 (`.python-version`) with `uv`; Node 22.23.3
(`.nvmrc`) with npm 10.9.9.

Local stack (from the repo root; Docker must be running):

```bash
cp .env.example .env                       # fake local values only
docker compose up -d dynamodb              # DynamoDB Local on 127.0.0.1:8001
```

Backend (run from `backend/`):

```bash
uv sync --locked --dev
uv run --env-file ../.env python -m scripts.create_local_tables
uv run --env-file ../.env uvicorn app.main:app --reload   # http://127.0.0.1:8000
uv run ruff check .
uv run pytest -q
uv run pytest tests/test_applications_api.py::test_name -q   # single test
uv run python -m evaluation.run --check    # synthetic classification gates
```

Frontend (run from `frontend/`):

```bash
npm ci
npm run dev                                # proxies /api to the backend
npm run lint
npm test                                   # vitest run
npx vitest run src/App.test.tsx -t "name"  # single test
npm run typecheck
npm run build
```

Infrastructure and manual deployment (from the repo root):

```bash
sam validate --template-file infra/template.yaml --lint
python3 scripts/test_deploy.py -q
./scripts/deploy.sh                         # applies AWS changes; see docs/DEVELOPMENT.md
```

CI (`.github/workflows/ci.yml`) runs the install, lint, test, typecheck, and
build commands above plus `npm audit`, SAM lint, and the offline deploy tests (not the `uvicorn`/`npm run dev` servers,
which don't exit). Run those checks locally before claiming work is done.

## Backend architecture

FastAPI app in `backend/app/`, intended to run on Lambda behind API Gateway.
Layers:

- `domain/` — Pydantic models and enums. `ApplicationCreate`/`ApplicationPatch`
  forbid extra fields; `ApplicationPatch` requires `expected_version` and at
  least one changed field (`updates()` returns only fields explicitly set, so
  setting a field to `null` is a real change).
- `repositories/applications.py` — the `ApplicationRepository` Protocol and the
  domain exceptions (`ApplicationNotFound`, `VersionConflict`, `InvalidCursor`).
- `repositories/dynamodb.py` — the only implementation. Every mutation is a
  `transact_write_items` that writes the application item **and** an audit event
  (`created`/`updated`/`deleted` with per-field `before`/`after`) atomically,
  guarded by a version condition. Deletes are soft (`deleted_at` + version bump);
  reads and lists filter deleted items out. List cursors are opaque base64-encoded
  application IDs.
- `api/` — routers translate repository exceptions to HTTP (404 / 409 stale
  version / 422 bad cursor). IDs and timestamps (`uuid4().hex`,
  `datetime.now(UTC)`) are generated in the router and passed into the
  repository, which keeps the repository deterministic in tests.
- `api/dependencies.py` — `get_current_subject` reads the verified Cognito
  `sub` from API Gateway in Lambda, with 401 for missing claims. The server-side
  `LOCAL_DEV_SUBJECT` variable authenticates loopback clients locally and is
  refused inside AWS Lambda;
  `get_application_repository` builds the DynamoDB repo from
  `APPLICATIONS_TABLE`, `EVENTS_TABLE`, and optional `AWS_REGION`.

DynamoDB layout: Applications table keyed `owner_id` + `application_id`; Events
table keyed `owner_id` + `event_id` with GSI `application-events-index`
(`owner_application_id` = `"{owner}#{app}"`, `occurred_at_event_id`) for
chronological history. `app/repositories/dynamodb_tables.py` defines these
tables for both the tests and `scripts/create_local_tables.py`.

## Testing conventions

- Tests use `moto` (`mock_aws`) for DynamoDB; no AWS account or credentials.
- API tests inject the repository and a synthetic owner via
  `app.dependency_overrides[get_application_repository]` and
  `[get_current_subject]`. **Never add a header/param that lets callers choose an
  owner** — ownership must come only from the verified auth subject.
- Use synthetic data only; no real email content or personal data in fixtures.
- Cover cross-user access and stale-version writes for any new owner-scoped
  operation.

## Agent coordination

Claude Code and Codex both work in this repo. The repo, not chat history, carries
shared context.

- **Decisions.** Agents make implementation and product-detail decisions
  themselves, within the approved spec. Record each one in the active plan file
  with its reason and what it costs if wrong. Do not stop to ask. Still ask the
  owner before you:
  - merge to `main`;
  - deploy to production;
  - delete data permanently;
  - change the owner's machine or accounts (for example, LaunchAgents or OAuth
    grants);
  - send anything to a new external service.

- **Roles.** For each milestone, one agent is the *driver* and the other is the
  *reviewer*. The driver writes the plan and implements it. The reviewer reviews
  diffs and does not edit files unless the user asks.
- **Plan file.** Each milestone gets a plan in `docs/superpowers/plans/`
  (`YYYY-MM-DD-<topic>.md`). The driver checks off steps as they land and records
  open decisions there. To pick up work, read the active plan and `git log`;
  don't rely on the user to relay context.
- **Commit often.** Commit after each completed, verified step (small, focused
  commits with conventional prefixes like `feat:`/`fix:`/`docs:`) so every step
  can be inspected or reverted. Don't batch a whole milestone into one commit.
- **One branch per milestone.** Milestone work happens on a branch such as
  `milestone-3-manual-tracker`; the reviewer reviews the branch against `main`
  before it merges. Ask the user before merging.
- **Handoff = commit.** Leave a clean working tree with a commit before handing
  off. Never hand off uncommitted changes.
- **One agent edits at a time.** Agents take turns in the same checkout; the
  reviewer only reads. Parallel work in separate branches/worktrees happens only
  when the user asks for it.
- **Calling Codex from Claude Code.** Use read-only mode for reviews so Codex
  can't edit files:
  - `codex exec review -c sandbox_mode='"read-only"' --base main` reviews the
    current branch
  - `codex exec review -c sandbox_mode='"read-only"' --commit <sha>` reviews one
    commit
  - `codex exec -s read-only --ephemeral -o <file> "<prompt>"` for a question;
    the final answer is written to `<file>`

  Always pass `</dev/null` to `codex exec`; otherwise it can block waiting on
  stdin. The driver triages review findings: fix them, or record a ruling in the
  plan file explaining why not.

## Product invariants (from PRD / technical design)

- All data is isolated per authenticated owner.
- Do not infer missing dates, cycles, statuses, or application identity; unknown
  stays `null` (status is never defaulted to "applied" from extraction).
- Deterministic rules before AI; ambiguous cases go to manual review.
- Credentials and real email content never go in Git, logs, or test fixtures.
  Local secrets belong in the git-ignored `.env`; `.env.example` holds
  placeholders only.
