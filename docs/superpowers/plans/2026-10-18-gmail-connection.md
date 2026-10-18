# Gmail Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an approved tracker user separately authorize one Gmail mailbox, see its connection status, and disconnect without exposing or retaining usable credentials afterward.

**Architecture:** Keep Cognito sign-in as the app identity and use a second Google OAuth web client for Gmail read-only consent. The FastAPI Lambda owns the OAuth exchange, verifies that the Gmail address matches the Cognito profile, and stores only an envelope-encrypted refresh token. DynamoDB holds one owner-scoped record in `Users` and short-lived, single-use OAuth states; disconnect advances a generation so future queued work cannot use the old connection. No mail import, queue, scheduler, or classification is built in this milestone.

**Tech Stack:** Python 3.12/FastAPI, boto3, DynamoDB, KMS plus AES-GCM, Google OAuth HTTPS endpoints, SAM, React/TypeScript/Vite, pytest/moto, Vitest.

**Spec:** `docs/IMPLEMENTATION_ROADMAP.md` milestone 5; `docs/PRD.md` onboarding, settings, privacy, and disconnect requirements; `docs/TECHNICAL_DESIGN.md` identity, storage, and API boundaries.

## Global Constraints

- Use the existing `app-tracker-dev` stack in `us-east-1`, existing Cognito allowlist, and owner ID from verified API Gateway JWT claims only.
- Request only [`https://www.googleapis.com/auth/gmail.readonly`](https://developers.google.com/workspace/gmail/api/auth/scopes) for Gmail; use a separate web OAuth client from Cognito sign-in and request offline access.
- The first Gmail address must match the signed-in Cognito user's verified email after case-insensitive comparison. Support one mailbox per owner; do not silently replace a different mailbox.
- Never commit, return, log, or put client secrets, refresh tokens, authorization codes, or mail content in frontend storage or a frontend URL. Only the Google callback receives a code, and its redirect to Amplify contains a generic result.
- Keep the Google OAuth client secret in Secrets Manager and refresh tokens envelope-encrypted with a customer-managed KMS key and owner/credential encryption context. [New key storage currently costs $1/month](https://aws.amazon.com/kms/pricing/); review the CloudFormation change set and budget before deployment.
- Keep existing manual tracker behavior working if Gmail is unconnected. No actual mail reads beyond `users.getProfile` for identity verification in this milestone.
- Use synthetic accounts and responses in automated tests. Live consent requires a designated test Gmail account and the user's explicit consent.

## Review Focus

- Missing, expired, or replayed OAuth state must never exchange a code or store credentials (Task 2 and Task 4 tests).
- A Gmail mailbox different from the signed-in Cognito email must fail without changing an existing connection (Task 4 test).
- A callback without a new refresh token may retain an existing token only for the same owner and mailbox; a first connection without one fails (Task 4 tests).
- A disconnected or replaced generation must not return an active credential to future queued work or let an in-flight consent callback reconnect after disconnect (Task 2 and Task 4 tests).
- Invalid ciphertext or mismatched owner encryption context must fail closed without leaking token material (Task 3 tests).

---

### Task 1: Define the connection contract and identity boundary

**Files:** Create `backend/app/domain/gmail.py`; modify `backend/app/api/dependencies.py`; test in `backend/tests/test_dependencies.py` and `backend/tests/test_gmail_models.py`.

**Interfaces:** `GmailConnectionStatus` exposes only `connected` and `email`. `get_current_identity(request) -> TrackerIdentity` supplies `owner_id`, Cognito `username`, and verified sign-in email. In Lambda, resolve the email with `AdminGetUser` using the verified JWT username and require its `sub` to equal the verified JWT `sub`; local tests inject this dependency. Do not accept an email or owner from a request body/header.

- [x] Write `test_gmail_identity_from_approved_claims`, `test_gmail_identity_rejects_missing_username`, `test_gmail_identity_rejects_mismatched_sub`, and `test_gmail_identity_rejects_unverified_email` with synthetic Cognito responses.
- [x] Run `uv run pytest tests/test_dependencies.py tests/test_gmail_models.py -q` from `backend/`; confirm the new tests fail for the absent interface.
- [x] Add the models and `get_current_identity` using the existing owner dependency and injected Cognito client.
- [x] Run the targeted tests and `uv run ruff check .`; confirm both pass.
- [x] Commit as `feat: bind Gmail identity to verified Cognito user`.

### Task 2: Store single-use states and generation-gated connections

**Files:** Create `backend/app/repositories/gmail.py` and `backend/app/repositories/dynamodb_gmail.py`; modify `backend/app/repositories/dynamodb_tables.py` and `backend/scripts/create_local_tables.py`; test in `backend/tests/test_gmail_repository.py`.

**Interfaces:** `create_state(state_hash, owner_id, expected_email, expected_generation, expires_at)`, `consume_state(state_hash, now) -> OAuthState | None`, `get_connection(owner_id)`, `save_connection(owner_id, expected_generation, email, encrypted_token)`, `disconnect(owner_id)`, and `get_active(owner_id, expected_generation)`. `OAuthStates` is keyed by a SHA-256 hash of a 32-byte random state with a ten-minute TTL and atomic conditional delete. `Users` is keyed by owner ID and contains only the Gmail connection fields needed now; connect/reconnect and disconnect each advance a monotonic generation, and disconnect removes the encrypted token. Saving a callback is conditional on the generation recorded when consent started, so disconnect invalidates outstanding consent. DynamoDB TTL is cleanup only; expiry is checked on consume. A connected-user enumeration index belongs with the later scheduled-sync milestone.

- [x] Write `test_state_consumed_once`, `test_expired_state_rejected_before_ttl_cleanup`, `test_connection_is_owner_scoped`, `test_disconnect_advances_generation`, and `test_stale_generation_has_no_active_credential` in `test_gmail_repository.py`.
- [x] Run `uv run pytest tests/test_gmail_repository.py -q`; confirm the tests fail for the absent repository.
- [x] Add table definitions and repository methods with conditional writes.
- [x] Run the targeted tests and `uv run ruff check .`; confirm both pass.
- [x] Commit as `feat: persist Gmail OAuth state and connection generations`.

### Task 3: Encrypt stored refresh tokens

**Files:** Create `backend/app/services/credential_crypto.py`; modify `backend/pyproject.toml` and `backend/uv.lock`; test in `backend/tests/test_credential_crypto.py`.

**Interfaces:** `encrypt_refresh_token(owner_id, plaintext) -> EncryptedCredential` and `decrypt_refresh_token(owner_id, encrypted) -> str`. Generate an AES-256 data key through KMS with encryption context `{owner_id, credential_type: gmail_refresh_token}`; use AES-GCM with a fresh nonce to encrypt the token and store ciphertext, nonce, and encrypted data key. Decrypt with the same context. No plaintext token or data key appears in exceptions or logs.

- [x] Write `test_refresh_token_round_trip`, `test_wrong_owner_cannot_decrypt`, `test_tampered_ciphertext_fails`, and `test_kms_failure_has_no_secret_in_error` with a fake KMS client.
- [x] Run `uv run pytest tests/test_credential_crypto.py -q`; confirm failures for the absent cipher.
- [x] Add the cryptography dependency and envelope-encryption implementation.
- [x] Run the targeted tests and `uv run ruff check .`; confirm both pass.
- [x] Commit as `feat: envelope-encrypt Gmail refresh tokens`.

### Task 4: Complete server-side Gmail consent and disconnect

**Files:** Create `backend/app/services/google_oauth.py`, `backend/app/services/gmail_connection.py`, and `backend/app/api/gmail.py`; modify `backend/app/main.py` and `backend/app/api/dependencies.py`; test in `backend/tests/test_gmail_api.py` and `backend/tests/test_google_oauth.py`.

**Interfaces:** `GET /gmail/connection` returns `GmailConnectionStatus`; `POST /gmail/connect` returns `{authorization_url}`; public `GET /oauth/google/callback` consumes state, exchanges the code on the server, uses Gmail `users.getProfile("me")` to verify the address, and redirects to the fixed Amplify origin with only `gmail=connected|failed`; `DELETE /gmail/connection` returns 204. Connect, status, and disconnect require the approved owner. The Google client reads a distinct Secrets Manager client ID/secret at runtime and never returns them. `POST /gmail/connect` uses `access_type=offline`, requests consent for a first connection, and uses a fixed redirect URI. Callback errors are sanitized; no external response body enters logs or API errors.

- [x] Write `test_gmail_api.py` and `test_google_oauth.py` cases for the authorized flow, 401/403 callers, public callback with state, missing/expired/replayed state, Google error, wrong mailbox, missing refresh token, same-mailbox reconnect, owner isolation, disconnect during consent, and redirect with no secret-bearing query values.
- [x] Run `uv run pytest tests/test_gmail_api.py tests/test_google_oauth.py -q`; confirm failures for the absent service/routes.
- [x] Implement the Google client, service, and routes using injected clients for offline tests.
- [x] Run the targeted tests and `uv run ruff check .`; confirm both pass.
- [x] Commit as `feat: add Gmail connection API`.

### Task 5: Show connection controls in the dashboard

**Files:** Create `frontend/src/gmail/GmailConnection.tsx` and `frontend/src/gmail/GmailConnection.test.tsx`; modify `frontend/src/api/client.ts`, `frontend/src/App.tsx`, and their focused tests.

**Interfaces:** The signed-in dashboard shows disconnected, connected address, and failed-consent states, plus a short Testing-mode reconnect note. Connect requests the backend authorization URL then navigates to it; disconnect confirms the action, calls the API, and refreshes status. Clear the generic `gmail` result query parameter after display. Never handle a Google code or refresh token in the browser.

- [ ] Write focused `GmailConnection.test.tsx` and API-client tests for status, connect navigation, cancellation, disconnect, result query cleanup, and API errors.
- [ ] Run `npx vitest run src/gmail/GmailConnection.test.tsx` from `frontend/`; confirm failures for the absent component.
- [ ] Implement a small connection section using existing dashboard styles.
- [ ] Run targeted Vitest, `npm run lint`, `npm run typecheck`, and `npm run build`; confirm all pass.
- [ ] Commit as `feat: show Gmail connection controls`.

### Task 6: Package and verify the hosted development deployment

**Files:** Modify `infra/template.yaml`, `scripts/deploy.sh`, `scripts/test_deploy.py`, `docs/DEVELOPMENT.md`, `.env.deploy.example`, and this plan.

**Interfaces:** SAM adds the `Users` and `OAuthStates` DynamoDB tables, customer-managed KMS key, scoped Lambda IAM for those tables, the KMS key, `AdminGetUser`, and the Gmail OAuth secret, a public exact callback route, and environment variables with resource names/ARNs and fixed URLs only. The new tables are deleted on stack teardown; application/history tables retain their existing policy. Document KMS key deletion behavior in the teardown guide. `GmailOAuthSecretName` names a separate manually created Secrets Manager secret; the deploy script checks its existence without reading its value. The Google Cloud setup uses the existing development project with a second web OAuth client, Gmail API enabled, read-only scope, a designated test user, and the exact API callback URI.

- [ ] Add offline deploy tests for missing Gmail secret, new parameter wiring, and safe output; run `python3 scripts/test_deploy.py -q` and confirm the new tests fail.
- [ ] Add the SAM resources, scoped permissions, deploy-script wiring, and operator documentation.
- [ ] Run `sam validate --template-file infra/template.yaml --lint`, container build, offline deploy tests, backend tests/Ruff, and frontend install/audit/lint/tests/typecheck/build; confirm all pass.
- [ ] Commit verified infrastructure and documentation changes, leaving the branch clean for review.
- [ ] Prepare and inspect a CloudFormation change set and document the new KMS cost; obtain deployment approval before applying it. Do not read or print secret values.
- [ ] After manual Google setup and approved deployment, verify no-token 401s, public callback handling, approved test-user connect/status/disconnect, mismatched-mailbox rejection, absence of token material in URLs/logs/responses, and stale-generation rejection. Record any unavailable live check precisely.
- [ ] Commit the live verification result, leave a clean branch, and request read-only review before asking to merge.

## Open decisions and deployment boundary

- The designated live Gmail test mailbox is not selected. Automated work uses synthetic accounts; no real Gmail consent is initiated until the user chooses one and authorizes the consent screen.
- The new customer-managed KMS key has an ongoing charge. Infrastructure can be coded and validated offline; applying the stack change requires review of the exact change set and budget impact.
- [Google classifies `gmail.readonly` as restricted](https://developers.google.com/workspace/gmail/api/auth/scopes), and [External Testing grants with Gmail scopes expire after seven days](https://support.google.com/cloud/answer/15549945). The UI explains that test authorization may need repeating. Detecting a revoked/expired token during background reads belongs to the later sync milestone; this milestone does not include public verification or a production Gmail connection.
