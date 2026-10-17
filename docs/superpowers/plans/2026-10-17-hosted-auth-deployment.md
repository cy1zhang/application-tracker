# Hosted Authentication and Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Host the manual tracker in one AWS development environment with Google sign-in and verified owner isolation.

**Architecture:** AWS SAM defines Cognito, a JWT-protected HTTP API, a FastAPI Lambda, DynamoDB, Amplify Hosting, and a budget in `us-east-1`. A laptop script deploys the stack and uploads a Vite build; local development keeps its existing behavior.

**Tech Stack:** Python 3.12.13, FastAPI, Mangum, boto3, AWS SAM, Cognito, API Gateway HTTP API, DynamoDB, Amplify Hosting, React/TypeScript/Vite, `oidc-client-ts`, pytest/moto, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-17-hosted-auth-deployment-design.md`

## Global Constraints

- Work only on milestone 4; leave Gmail, scheduled sync, AI, automatic deploys, custom domains, and production out.
- Deploy the `app-tracker-dev` stack in `us-east-1`; local AWS login region does not select deployment region.
- Only verified API Gateway JWT claims may select `owner_id` in Lambda. Missing claims fail closed with 401.
- Preserve `LOCAL_DEV_SUBJECT` for loopback development and refuse it inside Lambda.
- Never commit or print a Google secret, AWS credential, real email, or personal application data.
- Keep the existing $1 budget; add the selected $5 budget, with pre-credit accounting, to the stack.
- Retain DynamoDB tables on stack deletion; CloudWatch logs expire after 14 days.
- Commit each verified task separately; ask before merging and do not push.

## File map

- `backend/app/api/dependencies.py`: trusted Lambda subject extraction and existing local subject path.
- `backend/app/lambda_handler.py`: Mangum entry point.
- `backend/tests/test_lambda_handler.py`: real-handler tests with synthetic API Gateway events.
- `backend/pyproject.toml`, `backend/uv.lock`: Mangum runtime dependency.
- `frontend/src/auth/config.ts`: deployed versus local configuration.
- `frontend/src/auth/session.ts`: `oidc-client-ts` user manager and redirect lifecycle.
- `frontend/src/auth/AuthGate.tsx`: signed-out view, callback handling, and signed-in header state.
- `frontend/src/api/client.ts`: base URL and bearer token for API calls.
- `frontend/src/App.tsx`, `frontend/src/main.tsx`: auth gate integration and sign-out UI.
- `frontend/src/auth/*.test.ts*`, `frontend/src/api/client.test.ts`: auth and API tests.
- `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/vite-env.d.ts`: OIDC dependency and Vite types.
- `infra/template.yaml`: all AWS resources and public stack outputs.
- `scripts/deploy.sh`: prerequisite checks, SAM deploy, Vite build, Amplify upload.
- `.env.deploy.example`, `.gitignore`: nonsecret deploy configuration template and ignore rule.
- `.github/workflows/ci.yml`, `docs/DEVELOPMENT.md`, `AGENTS.md`: validation and operator instructions.

## Review Focus

1. A Lambda event with no authorizer claims returns 401; a Lambda configured with `LOCAL_DEV_SUBJECT` raises a configuration error: Task 1 handler and dependency tests.
2. A forged owner header cannot cross the owner boundary: Task 1 handler test.
3. A deployed browser with incomplete `VITE_` settings fails visibly instead of silently using `/api`: Task 2 config test.
4. An API 401 after an expired session returns the hosted UI to sign-in: Task 2 UI test.
5. A missing Google secret or wrong AWS region stops before stack mutation: Task 4 deploy-script test.

---

### Task 1: Trusted Lambda identity

**Files:** Modify `backend/app/api/dependencies.py`, `backend/pyproject.toml`, `backend/uv.lock`; create `backend/app/lambda_handler.py`, `backend/tests/test_lambda_handler.py`; extend `backend/tests/test_dependencies.py`.

**Interfaces:** `get_current_subject(request: Request) -> str` consumes `request.scope["aws.event"]["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]` only when running through a Lambda event. `handler = Mangum(app)` in `app/lambda_handler.py` is the SAM entry point.

- [x] **Step 1: Write failing tests.** Add `test_lambda_claims_select_owner`, `test_lambda_missing_claims_fail_closed`, `test_lambda_rejects_local_subject_configuration`, `test_forged_owner_header_is_ignored`, `test_other_subject_cannot_read_application`, and `test_public_health_without_claims` using synthetic HTTP API v2 events and moto tables. Assert create returns 201, cross-owner read returns 404, missing claims returns 401, local subject configuration returns 500 through Mangum (the existing direct dependency test checks the raised error), and health returns 200.
- [x] **Step 2: Verify red.** Run `cd backend && uv run pytest tests/test_lambda_handler.py tests/test_dependencies.py -q`; expect the new tests to fail for the missing handler/claim extraction.
- [x] **Step 3: Implement.** Add `mangum` with `uv add mangum`; add `handler = Mangum(app)`; read the subject only from the trusted Lambda event, validate nonblank string, fail closed, then retain the existing local branch. Keep the existing Lambda guard against `LOCAL_DEV_SUBJECT`.
- [x] **Step 4: Verify green.** Run `cd backend && uv run ruff check . && uv run pytest -q`; expect all checks to pass.
- [x] **Step 5: Commit.** `git commit -m "feat: derive hosted owner from verified JWT claims"` with Task 1 files and this checked plan.

### Task 2: Browser sign-in and authenticated API calls

**Files:** Create `frontend/src/auth/config.ts`, `frontend/src/auth/session.ts`, `frontend/src/auth/AuthGate.tsx` and focused tests; modify `frontend/src/api/client.ts`, `frontend/src/api/client.test.ts`, `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/vite-env.d.ts`, `frontend/package.json`, `frontend/package-lock.json`, and only the styles needed for the sign-in view and header.

**Interfaces:** `authConfig` is `null` when no deployment variables are set and otherwise contains `apiBaseUrl`, `authority`, `clientId`, `domain`. `getAccessToken(): Promise<string | null>` supplies the API client; `AuthGate` renders `App` only with a valid hosted user. The local API base remains `/api`.

- [ ] **Step 1: Write failing tests.** Cover all-or-none Vite configuration; local mode without sign-in; hosted signed-out view; fake user manager redirect callback and sign-out; bearer token on GET/POST; 401 returning to sign-in. Assert local requests still target `/api/applications`.
- [ ] **Step 2: Verify red.** Run `cd frontend && npm test -- --run src/auth src/api/client.test.ts`; expect the new auth tests to fail.
- [ ] **Step 3: Implement.** Add pinned `oidc-client-ts`; use authorization-code PKCE, `sessionStorage`, one-hour access and one-day refresh settings supplied by Cognito. Configure Cognito's direct Google redirect, callback cleanup, renewal, and logout. Set deployed API base and bearer header; keep local mode unmodified when all variables are absent.
- [ ] **Step 4: Verify green.** Run `cd frontend && npm run lint && npm test && npm run typecheck && npm run build`; expect all checks to pass.
- [ ] **Step 5: Commit.** `git commit -m "feat: add Google sign-in to hosted frontend"` with only Task 2 files.

### Task 3: One-stack AWS infrastructure

**Files:** Create `infra/template.yaml`; add only a template-focused test if `sam validate --lint` and `sam build` cannot check a critical route or retention invariant.

**Interfaces:** Parameters `BudgetEmail` and `GoogleOAuthSecretName`; outputs `ApiUrl`, `CognitoAuthority`, `CognitoClientId`, `CognitoDomain`, `AmplifyAppId`, `AmplifyUrl`. The secret JSON keys are `client_id` and `client_secret`. The template uses the Task 1 `app.lambda_handler.handler` and `backend/` build directory.

- [ ] **Step 1: Write the template.** Define the two tables with the existing keys/index and `DeletionPolicy: Retain`; Cognito user pool, Google IdP, app client, domain; Amplify app/`main` branch; HTTP API with JWT default authorizer and public `/health`, allowed CORS origin and 10/20 throttling; Python 3.12 arm64 Lambda and scoped IAM; 14-day logs; $5 COST budget with `IncludeCredit: false`, 80% actual and forecast alerts; public outputs. Use a versionless Secrets Manager dynamic reference and do not print secret values.
- [ ] **Step 2: Verify locally.** Install SAM CLI if needed, generate `backend/requirements.txt` from `uv.lock` without committing it, then run `sam validate --template-file infra/template.yaml --lint` and `sam build --template-file infra/template.yaml`; expect success. Inspect the transformed template to confirm application routes have the JWT authorizer and `/health` has none; inspect table retention and log expiry.
- [ ] **Step 3: Commit.** `git commit -m "feat: define hosted development stack"` with only Task 3 files.

### Task 4: Repeatable manual deployment and operator guide

**Files:** Create `scripts/deploy.sh`, `.env.deploy.example`; modify `.gitignore`, `.github/workflows/ci.yml`, `docs/DEVELOPMENT.md`, `AGENTS.md`; add a focused shell test for prerequisite failure and output parsing if needed.

**Interfaces:** The script reads local `AWS_PROFILE`, `AWS_REGION=us-east-1`, `STACK_NAME=app-tracker-dev`, `BUDGET_EMAIL`, and `GOOGLE_SECRET_NAME`. It consumes Task 3 outputs and builds Task 2's four `VITE_` variables. It uploads a zip of the *contents* of `frontend/dist` using `aws amplify create-deployment`, the returned upload URL, and `start-deployment`.

- [ ] **Step 1: Write failing script checks.** With fake `aws`/`sam`/`npm` binaries, show missing secret, missing identity, or wrong region aborts before `sam deploy`; show missing stack output aborts before frontend upload.
- [ ] **Step 2: Verify red.** Run the script checks and expect their assertions to fail before the script exists.
- [ ] **Step 3: Implement.** Add explicit prereq checks, `uv export --locked --no-dev` to a generated ignored requirements file, `sam build`, `sam deploy` with capabilities and parameter overrides, output retrieval, Vite build, zip upload, deployment start/wait, and final URL. Keep secret content out of shell output. Document Google Cloud OAuth client setup, secret creation in the user's terminal, first deploy, verification, reruns, Free plan end date check, logs, and teardown. Add SAM template lint to CI with no AWS credentials.
- [ ] **Step 4: Verify green.** Run script checks, `shellcheck` if installed, CI-equivalent backend/frontend checks, `sam validate --lint`, and a local dry run through the stubbed CLI; expect success. Confirm `.env.deploy` and generated requirements are ignored.
- [ ] **Step 5: Commit.** `git commit -m "feat: add manual development deployment workflow"` with only Task 4 files.

### Task 5: Deploy and verify in AWS

**Files:** Update this plan's checkboxes and `docs/DEVELOPMENT.md` only if actual deployment reveals necessary corrections. No account IDs, test email addresses, or secrets in commits.

**Interfaces:** Requires the user's Google Cloud OAuth web-client ID/secret stored in Secrets Manager and an alert email in ignored local config. Two consenting Google test accounts are required for the owner-isolation check.

- [ ] **Step 1: Complete prerequisites.** Confirm AWS profile and region, SAM CLI, the account's Free plan end date, Google test users and OAuth redirect URI, and the named Secrets Manager JSON secret. Do not read or echo its value.
- [ ] **Step 2: Deploy.** Prepare and inspect the first CloudFormation change set, obtain the user's approval to apply it, then run `./scripts/deploy.sh`. Record only nonsecret stack outputs and the hosted URL.
- [ ] **Step 3: Verify.** Check 200 `/health`; 401 with absent/garbage tokens; Google login; owner A create versus owner B empty list and 404 for A's ID; edit/history/filters/stale edit; 429 under a controlled burst; $5 budget and 14-day logs. Report any checks that could not be performed.
- [ ] **Step 4: Review and handoff.** Run all CI commands, inspect branch diff against `main`, commit verified corrections, and hand the clean branch to the read-only reviewer. Ask before merge; never push.
