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

Approved-user addition: `backend/app/api/dependencies.py` checks the verified
group claim; `backend/tests/test_lambda_handler.py` proves denied and approved
requests; `frontend/src/App.tsx` and `frontend/src/App.test.tsx` show the denied
state; `infra/template.yaml` creates the Cognito group and exposes the user
pool ID; `docs/DEVELOPMENT.md` explains grant and revoke.

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

- [x] **Step 1: Write failing tests.** Cover all-or-none Vite configuration; local mode without sign-in; hosted signed-out view; fake user manager redirect callback and sign-out; bearer token on GET/POST; 401 returning to sign-in. Assert local requests still target `/api/applications`.
- [x] **Step 2: Verify red.** Run `cd frontend && npm test -- src/auth src/api/client.auth.test.ts`; the new auth tests failed for missing modules and token handling. A later StrictMode callback test failed with two redemptions.
- [x] **Step 3: Implement.** Add pinned `oidc-client-ts`; use authorization-code PKCE, `sessionStorage`, one-hour access and one-day refresh settings supplied by Cognito. Configure Cognito's direct Google redirect, callback cleanup, renewal, and logout. Set deployed API base and bearer header; keep local mode unmodified when all variables are absent.
- [x] **Step 4: Verify green.** Run pinned Node 22.23.3/npm 10.9.9: `cd frontend && npm ci && npm run lint && npm test && npm run typecheck && npm run build`; 57 tests and all checks passed.
- [x] **Step 5: Commit.** `git commit -m "feat: add Google sign-in to hosted frontend"` with Task 2 files and checked design/plan corrections.

### Task 3: One-stack AWS infrastructure

**Files:** Create `infra/template.yaml`; add only a template-focused test if `sam validate --lint` and `sam build` cannot check a critical route or retention invariant.

**Interfaces:** Parameters `BudgetEmail` and `GoogleOAuthSecretName`; outputs `ApiUrl`, `CognitoAuthority`, `CognitoClientId`, `CognitoDomain`, `AmplifyAppId`, `AmplifyUrl`. The secret JSON keys are `client_id` and `client_secret`. The template uses the Task 1 `app.lambda_handler.handler` and `backend/` build directory.

- [x] **Step 1: Write the template.** Define the two tables with the existing keys/index and `DeletionPolicy: Retain`; Cognito user pool, Google IdP, app client, domain; Amplify app/`main` branch; HTTP API with JWT default authorizer and public `/health`, allowed CORS origin and 10/20 throttling; Python 3.12 arm64 Lambda and scoped IAM; 14-day logs; $5 COST budget with `IncludeCredit: false`, 80% actual and forecast alerts; public outputs. Use a versionless Secrets Manager dynamic reference and do not print secret values.
- [x] **Step 2: Verify locally.** Installed SAM CLI 1.169.0, generated `backend/requirements.txt` from `uv.lock` without committing it, and passed `sam validate --template-file infra/template.yaml --lint` plus `sam build --use-container --template-file infra/template.yaml`. The container build produced Linux arm64 native wheels. SAM translation shows `/{proxy+}` with CognitoJwt and `/health` with the `NONE` override; both tables retain on deletion and logs expire after 14 days.
- [x] **Step 3: Commit.** `git commit -m "feat: define hosted development stack"` with only Task 3 files.

### Task 4: Repeatable manual deployment and operator guide

**Files:** Create `scripts/deploy.sh`, `.env.deploy.example`; modify `.gitignore`, `.github/workflows/ci.yml`, `docs/DEVELOPMENT.md`, `AGENTS.md`; add a focused shell test for prerequisite failure and output parsing if needed.

**Interfaces:** The script reads local `AWS_PROFILE`, `AWS_REGION=us-east-1`, `STACK_NAME=app-tracker-dev`, `BUDGET_EMAIL`, and `GOOGLE_SECRET_NAME`. It consumes Task 3 outputs and builds Task 2's four `VITE_` variables. It uploads a zip of the *contents* of `frontend/dist` using `aws amplify create-deployment`, the returned upload URL, and `start-deployment`.

- [x] **Step 1: Write failing script checks.** Five offline checks cover missing secret, missing identity, wrong region, missing stack output, and a full simulated upload.
- [x] **Step 2: Verify red.** All five checks failed before the deploy script existed.
- [x] **Step 3: Implement.** Added prerequisite checks, locked requirements export, Linux container build, SAM deploy, output retrieval, Vite build, zipped Amplify upload, job polling, operator guide, and credential-free CI validation.
- [x] **Step 4: Verify green.** Five deploy checks pass; `bash -n`, backend lint and 48 tests, frontend audit/lint/57 tests/typecheck/build, and SAM lint pass. `.env.deploy` and `backend/requirements.txt` are ignored. `shellcheck` is not installed.
- [x] **Step 5: Commit.** `git commit -m "feat: add manual development deployment workflow"` with only Task 4 files.

### Task 4a: Gate application access by Cognito group

**Files:** Modify `backend/app/api/dependencies.py`, `backend/tests/test_lambda_handler.py`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `infra/template.yaml`, and `docs/DEVELOPMENT.md`.

**Interfaces:** The hosted API accepts `sub` only when verified claims include `tracker-users` in `cognito:groups`; otherwise it returns 403. Local loopback authentication and public `/health` remain unchanged. The group is created in the user pool, and `UserPoolId` is a stack output for operator access.

- [x] **Step 1: Test backend authorization.** Added Mangum tests for missing, wrong, malformed, and approved group claims. Six denied-path assertions failed before implementation (HTTP 200/201 instead of 403).
- [x] **Step 2: Implement and verify backend.** Require exact group membership in the trusted claims. Backend lint and full tests pass (55 tests; the existing Mangum event-loop deprecation warning remains). Commit this step.
- [x] **Step 3: Test frontend denied state.** Added an App test for an API 403; it failed because the UI showed the generic retry error instead of access denied.
- [x] **Step 4: Implement and verify frontend.** Render the denied view. Frontend lint, 58 tests, typecheck, and build pass. Commit this step.
- [x] **Step 5: Wire AWS and operator instructions.** Added the Cognito group and user pool output, with grant/revoke instructions after first sign-in. SAM lint and container build pass; the built template contains the group and output. Commit this step.

### Task 5: Deploy and verify in AWS

**Files:** Update this plan's checkboxes and `docs/DEVELOPMENT.md` only if actual deployment reveals necessary corrections. No account IDs, test email addresses, or secrets in commits.

**Interfaces:** Requires the user's Google Cloud OAuth web-client ID/secret stored in Secrets Manager and an alert email in ignored local config. Two consenting Google test accounts are required for the owner-isolation check.

**Decision (2026-10-17):** The user selected External Google audience with a Cognito group allowlist. Unapproved people may finish Google sign-in but see access denied in the app. Task 4a implements this before deployment.

- [ ] **Step 1: Complete prerequisites.** Confirm AWS profile and region, SAM CLI, the account's Free plan end date, Google test users and OAuth redirect URI, and the named Secrets Manager JSON secret. Do not read or echo its value.
- [x] **Step 2: Deploy.** Prepared and inspected the 17-addition change set, obtained the user's approval, executed that change set, and ran `./scripts/deploy.sh`. CloudFormation reached `CREATE_COMPLETE` and Amplify reported a successful upload on October 18, 2026. The hosted URL is recorded in the deployment handoff, not in this plan.
- [ ] **Step 3: Verify.** Check 200 `/health`; 401 with absent/garbage tokens; Google login; owner A create versus owner B empty list and 404 for A's ID; edit/history/filters/stale edit; 429 under a controlled burst; $5 budget and 14-day logs. Report any checks that could not be performed.

  Partial live checks (October 18): `/health` returned 200, absent and garbage tokens returned 401, the hosted page returned 200, a sustained low-concurrency burst returned 429, the $5 budget has the configured email alerts, and the Lambda log group retains 14 days. A much larger concurrent burst hit this account's 10-execution Lambda quota and returned 503; keep the 429 check below that concurrency. After Google sign-in exposed a failed browser preflight, the deployed API was corrected to allow unauthenticated `OPTIONS /{proxy+}`; a live authorization-header preflight returned 204 with the hosted origin, while unauthenticated `GET /applications` still returned 401. Browser retry and approved-user checks remain open.
- [ ] **Step 4: Review and handoff.** Run all CI commands, inspect branch diff against `main`, commit verified corrections, and hand the clean branch to the read-only reviewer. Ask before merge; never push.
