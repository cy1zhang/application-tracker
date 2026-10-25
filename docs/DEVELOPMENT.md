# Local development

The project contains the manual tracker: a FastAPI backend persisting to
DynamoDB and a React/TypeScript dashboard for creating, editing, filtering,
and inspecting applications. Hosted Google sign-in and the manual tracker were
verified in the development deployment. Milestone 5 adds a separate Gmail
connection. Milestone 6 adds offline, deterministic processing of synthetic
Gmail messages. Milestone 7 adds an owner-scoped review queue and duplicate
merge. Milestone 8 adds resumable Gmail import, incremental history, scheduled
dispatch, and sync status controls. Milestones 7 and 8 were deployed to the
development stack on 2026-10-20. The initial Gmail import and empty
incremental repeat were verified live; other Gmail and review-flow checks remain.
AI providers are a later milestone.

## Prerequisites

- Python 3.12.13, pinned in [`.python-version`](../.python-version)
- [`uv`](https://docs.astral.sh/uv/) for Python dependencies
- Node.js 22.23.3, pinned in [`.nvmrc`](../.nvmrc)
- npm 10.9.9, included with the pinned Node.js release
- Docker Desktop, for DynamoDB Local

Local development uses the fake values in `.env.example`, copied to `.env`,
which Git ignores. Real credentials belong only in `.env`; commit only
placeholder values to `.env.example`.

## Run the app locally

```bash
cp .env.example .env             # fake local values only
docker compose up -d dynamodb    # DynamoDB Local on 127.0.0.1:8001

cd backend
uv sync --locked --dev
uv run --env-file ../.env python -m scripts.create_local_tables
uv run --env-file ../.env uvicorn app.main:app --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open the URL Vite prints, normally `http://localhost:5173`. Vite proxies `/api`
requests to the API on `http://127.0.0.1:8000`. Data persists in the
`dynamodb-data` Docker volume; `docker compose down -v` deletes it.

Check the API directly with `curl http://127.0.0.1:8000/health`; the expected
response is `{"status":"ok"}`.

## Run all checks

Backend:

```bash
cd backend
uv sync --locked --dev
uv run ruff check .
uv run pytest -q
uv run python -m evaluation.run --check
```

Frontend:

```bash
cd frontend
npm ci
npm audit
npm run lint
npm test
npm run typecheck
npm run build
```

GitHub Actions runs these same commands for pushes and pull requests. It also
validates the SAM template and tests the deploy script with fake AWS commands;
CI has no AWS credentials and does not deploy.

## Application domain

Application CRUD uses the `APPLICATIONS_TABLE` and `EVENTS_TABLE` DynamoDB
tables and the optional `AWS_REGION` setting. Automated tests create these
tables with `moto`; they do not use an AWS account.

The hosted Lambda uses the verified Cognito `sub` from API Gateway as the owner.
For local development, setting the server-side `LOCAL_DEV_SUBJECT` variable
authenticates requests from loopback addresses (`127.0.0.1`, `::1`) as that
subject; it is ignored for other clients and refused inside AWS Lambda. API
tests inject synthetic user subjects through FastAPI dependency overrides. Do
not add a request header that lets callers choose an owner.

## Offline email processing

`app.services.email_processing.process_message` accepts a Gmail `format=full`
message, the authenticated owner ID, and that owner's candidate applications.
It parses bounded inline text, ignores attachments, applies only explicit
recruiting rules, and records an owner-scoped outcome in the existing Events
table. Clear evidence changes an existing application and writes its history
event in one DynamoDB transaction. Ambiguous messages become review-required
records; duplicates return their first terminal outcome. An automatic change
requires an affirmative event that names both the company and the exact role
in the event sentence, plus an existing unique thread or requisition link.
Requisition IDs count only when directly attached to the named role at the end
of that event clause.
Unlinked messages go to review. A season and year are added only when tied directly to
that role; unsupported wording stays for review. The SQS sync worker supplies
the owner and candidate applications from trusted records; offline tests use
synthetic Gmail responses.
Forwarded messages and messages whose only event evidence is in the subject
also go to review, so old thread text cannot create a new status change.

Use `uv run pytest tests/test_gmail_message.py tests/test_email_rules.py
tests/test_dynamodb_email.py tests/test_email_processing.py -q` from `backend/`
to run the synthetic processing checks. No Gmail account or AWS credentials are
needed.

## Offline synchronization

`POST /syncs` creates one durable run for the authenticated owner and sends its
ID to SQS in the deployed stack. `GET /syncs/latest` returns status and progress.
The first import keeps the 90-day boundary anchored to Gmail connection time;
later runs use Gmail history, with a rescan from the last successful sync after
a stale history cursor. A daily 09:00 UTC dispatcher and a 15-minute recovery
pass run independently of the browser. Worker commits and checkpoints are guarded
by the current connection generation so disconnect or reconnection invalidates
old work. The local `.env.example` has no SQS queue, so `POST /syncs` returns
503 there. Run `uv run pytest tests/test_sync_worker.py tests/test_sync_dispatch.py
tests/test_sync_handler.py -q` from `backend/` for the synthetic worker checks.

## Review and reconciliation

`GET /reviews` lists pending outcomes for the authenticated owner. The dashboard
shows each item's reason, bounded evidence, proposed fields, and a Gmail source
link when available. The owner can accept a proposal, edit the linked
application, reject it, attach the email to an application, or create an
application from it. Creating from review permits an unknown (`null`) status.
The resolution API guards repeated actions and stale application versions;
successful changes and links appear in application history.

Application detail also offers **Merge duplicate**. Choose the duplicate and
resolve every field where both applications have different known values. The
retained application gets the combined history, while the duplicate ID points
to the retained record and cannot be recreated by replaying old evidence.
Manual changes are tracked by field so older email cannot overwrite a corrected
value and a later clear email can update it. These behaviors are covered by
synthetic offline tests. Milestone 7 is deployed, but its live review controls
have not yet been checked.

## Bring-your-own-key AI escalation

Milestone 9 lets an owner supply their own OpenAI, Anthropic, or Gemini API key.
The Hugging Face Inference Providers option was added afterward for a hosted
open-weight model trial. Hugging Face routes candidate email text to the pinned
inference provider, so both services handle it during a request. A fine-grained
Hugging Face token needs the "Make calls to Inference Providers" permission.
The AI settings page shows the pinned model host before the owner saves a key.
Deterministic rules always run first. Only messages the rules leave ambiguous
(the reasons in `ESCALATION_REASONS`) are sent to the owner's chosen provider;
the model's answer is validated against the email text and reconciled by the
server before anything can change an application. Everything is covered offline
with stub providers: `uv run pytest tests/test_ai_*.py tests/test_settings_api.py
-q` from `backend/` needs no network, key, or AWS account.

### Local Ollama review suggestions (development)

The **Needs review → Analyze with Ollama** control runs a model on the computer
holding the browser tab. It handles only pending reviews with one of the five AI
escalation reasons, one email at a time. It saves validated suggestions for a
human decision; it does not change applications or run on a schedule. Gmail and
application data remain in AWS. The browser sends the bounded email prompt to
Ollama at `http://127.0.0.1:11434`; no Gmail token is sent there. The model and
Ollama service must be installed and running on that same computer.

For the current development app on macOS, install an Ollama model and allow only
the app's origin in the local Ollama service:

```bash
ollama pull gemma4:latest
launchctl setenv OLLAMA_ORIGINS 'https://main.d2pqicgf5e8l1p.amplifyapp.com'
```

`launchctl setenv` is lost on reboot, and Ollama.app does not read shell
profiles. Quit and reopen Ollama after setting the variable. Keep `OLLAMA_HOST`
unset (its default binds to loopback); do not expose port 11434 to the network,
and never use `OLLAMA_ORIGINS=*`.

To keep the allowance across reboots, create a user LaunchAgent at
`~/Library/LaunchAgents/com.applicationtracker.ollama-origins.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.applicationtracker.ollama-origins</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/launchctl</string>
    <string>setenv</string>
    <string>OLLAMA_ORIGINS</string>
    <string>https://main.d2pqicgf5e8l1p.amplifyapp.com</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
```

Load it with `launchctl bootstrap gui/$(id -u)
~/Library/LaunchAgents/com.applicationtracker.ollama-origins.plist`, then check
`launchctl getenv OLLAMA_ORIGINS` prints the origin. Ollama.app only reads the
variable at startup, so quit and reopen it afterward. At login the LaunchAgent
may run after Ollama starts; if the app cannot connect after a reboot, quit and
reopen Ollama once. To remove the allowance, run `launchctl bootout
gui/$(id -u)/com.applicationtracker.ollama-origins`, delete the plist, run
`launchctl unsetenv OLLAMA_ORIGINS`, and restart Ollama. This setup is per
computer.

#### Using it

Use Chrome on the same Mac; listing models was verified there after allowing
local-network access for the app site. Firefox is not yet verified and may show
its own local network permission prompt; allow it only for the app's origin.
Sign in, open **Needs review**, choose **Analyze with Ollama**, select the
installed model, set **Reviews to analyze** (start with 1-5; default 5), then
**Start analysis**.

On an Apple M3 with 16 GB, expect about 50 seconds for the first review (model
load) and about 27 seconds for each later one. Ollama unloads the model after
about 5 minutes idle. Keep the tab open for the whole run. **Stop** aborts the
current request; restarting skips suggestions already saved. A review that
fails is counted and skipped for the rest of the session; Ollama being
unreachable, a sign-in problem, or a Gmail connection problem stops the run.
Suggestions are only proposals: the owner accepts or rejects each one, and
nothing changes an application until then.

Hosted AI is separate: if its setting is on, scheduled Gmail sync may still
send candidate email to that provider. Turn it off in AI settings for local-only
analysis. The Ollama path has no provider fee, though it uses local CPU/GPU and
requires the tab to remain open.

### Model-ID parameters

The template has four SAM parameters: `OpenAiModelId`, `AnthropicModelId`,
`GeminiModelId`, and `HuggingFaceModelId`, each defaulting to blank. They become
the matching `AI_MODEL_*` environment variables on the API and sync worker
Lambdas. **A blank ID means that provider is unavailable**: it is
not offered in `GET /settings`, and the worker will not call it. With all four
blank, which is the shipped default, AI is off and sync behaves exactly as
before. No model ID is written in code or committed config. To enable one, set
`OPENAI_MODEL_ID`, `ANTHROPIC_MODEL_ID`, `GEMINI_MODEL_ID`, or
`HUGGING_FACE_MODEL_ID` in the ignored `.env.deploy` (see `.env.deploy.example`)
to an exact ID your provider account can call. For Hugging Face, use
`org/model:provider` so the underlying host is
fixed; automatic `:fastest`, `:cheapest`, and `:preferred` routing is rejected.
Check that this model/provider pair supports structured output and review that
provider's data handling before sending real email. `deploy.sh` always passes
all four overrides, with an explicit empty value (`Name=""`) when a variable is
unset or blank. On an existing stack, `sam deploy` reuses the previous value of
any parameter it is not given, so the
explicit empty value is what turns a provider off when you delete its variable
and redeploy. Lambda environment values are not secrets; never put an API key
in a parameter.

Models that spend output tokens on hidden reasoning ("thinking") may use up the
fixed 1024-output-token cap before producing the JSON. The adapters report that
as `invalid_output`, and the message stays in review. If that happens, choose a
non-thinking model or a lower reasoning setting rather than raising the cap
without review.

### Provider-key lifecycle

- **Save:** `PUT /settings/ai` takes a provider, the key, and consent. The API
  encrypts the key with the stack KMS key (encryption context `owner_id` plus
  `credential_type=ai_api_key`) and stores only the ciphertext and the last four
  characters as a hint on the owner's Users record. The key is never returned.
- **Use:** the worker decrypts it into memory for one invocation, sends it only
  in the provider's auth header over HTTPS, and discards it. Each attempt
  reserves from a 50-requests-per-UTC-day counter first. This is a request cap,
  not a monetary cap; the provider bills the owner separately.
- **Rejected key:** a provider authentication failure marks the key `invalid`
  (guarded by the settings version, so a stale failure cannot invalidate a
  freshly replaced key). Later messages stay in review with an `ai_note` until
  the owner replaces the key.
- **Disable, replace, delete:** disabling keeps the encrypted key but stops use;
  replacing or deleting bumps the settings version, so any in-flight AI result
  is discarded at commit time. Delete removes the ciphertext, hint, and consent.
  Disconnecting Gmail does not delete the AI key.
- **Access:** the KMS policies on the API and worker roles are conditioned on
  `kms:EncryptionContext:credential_type` for `gmail_refresh_token` and
  `ai_api_key`; the dispatcher has no KMS access.

### Privacy

- **Sent to the provider:** for one candidate message, its subject, sender, and
  up to 8,000 characters of parsed body text, plus up to 20 lines of
  `company | role` for the owner's own applications that the message mentions
  or links to. Nothing else is sent: no attachments, other messages, other
  owners' data, or account identifiers. Requests have no tools, use structured
  output, and never follow redirects.
- **Stored:** the encrypted key, hint, provider, consent time, key status,
  settings version, and daily usage counter on the Users record. On a review item:
  the provider name, model ID, the validated proposal fields, and short
  evidence quotes (at most 70 characters each) that were checked against the
  email text. Raw provider responses and prompts are not stored.
- **Never logged:** the API key, prompts, email text, provider response or error
  bodies, and evidence quotes. Logs carry identifiers, counts, and error
  categories only. Tests assert this for every adapter error path.
- The settings panel requires an explicit consent checkbox before a key can be
  saved: email text from candidate messages goes to the chosen provider, which
  bills and retains it under its own terms.

### Verification boundary

- **Verified offline (CI and local):** request/response handling of all three
  adapters against stubs, schema and prompt construction, validation of quotes
  and cue words, budget and key-state transitions in moto, server-side
  reconciliation, pipeline and failure handling, the settings and review APIs,
  the settings and review UI, template lint, the deploy-script parameter
  handling, and the guards in `backend/tests/test_infra_timing.py` (lease longer
  than the worker timeout, queue visibility at least six times the timeout, PUT
  in CORS, model-ID wiring, and KMS conditions).
- **Not verified until the owner supplies their own key and model IDs:** a live
  call to each provider and its real structured-output behavior, a real
  invalid-key response, real rate limiting, whether the real KMS condition
  works for both Gmail connect and AI key save, and an observed auto-apply or
  proposal on a designated test mailbox. These must not be described as tested
  before they are run, and live provider results never enter CI or the
  repository.

### Development deploy note (2026-10-21, Milestone 10)

Milestone 10 was deployed to `app-tracker-dev` with `AlarmNotifications=false`,
so no SNS topic or subscription exists. It adds:
- the account-deletion function, queue and DLQ;
- alarms, metric filters and Users-table TTL;
- `RecursiveLoop: Allow` on the self-continuing worker and deletion functions.

A post-deploy incremental sync completed and changed no application.
The 6 obsolete continuation messages left in the sync DLQ from the first import
were later purged, and `SyncDeadLetterAlarm` returned to OK. Never invoke
`DELETE /account` against the development owner. Live deletion checks use a
separate test identity, and the owner runs them. Results and pending checks are
in `docs/superpowers/plans/2026-10-20-harden-evaluate.md` and
`docs/VERIFICATION.md`.

### Development deploy note (2026-10-20)

Milestone 9 was deployed to `app-tracker-dev` with all three model IDs blank
(`UPDATE_COMPLETE` 2026-10-21T02:50:15Z UTC), so AI is unavailable. Checked
afterward: `/health` 200, `/settings` 401 without a token, CORS preflight for
`PUT /settings/ai` allowed, worker timeout 600 and queue visibility 3600, and a
daily-mode sync that processed 5 messages (the worker decrypted the Gmail
credential under the new KMS condition) with no worker errors. Live provider
calls, real invalid-key and rate-limit behavior, an observed AI proposal, and
the signed-in settings UI remain unverified until the owner supplies a key and
model IDs. Results are recorded in the plan's Results section.

### Timing

The worker timeout is 600 seconds, the SQS visibility timeout is 3600 seconds,
and the sync lease is 12 minutes, so a recovery pass cannot start a second
worker while a slow page is still running. The recovery pass reads the stored
`lease_expires_at`, so it follows the lease automatically.

## Synthetic classification evaluation

`uv run python -m evaluation.run --check` (from `backend/`) feeds the fixtures in
`backend/evaluation/fixtures/` through the real `process_message` pipeline on
moto, with recorded provider outputs replayed through the real escalator,
validation, and reconciliation. Sockets are blocked, so any network attempt
fails the run. It prints a Markdown report and writes `report.md` and
`report.json` to `backend/evaluation/output/` (git-ignored), and appends to
`$GITHUB_STEP_SUMMARY` in CI. `--check` exits non-zero when a gate in
`backend/evaluation/thresholds.json` is breached and names the failing
scenarios. The fixtures are agent-written synthetic data; the results validate
intended behavior only and do not establish real-inbox accuracy. Gmail
candidate-search recall is not modeled. AI rows use recorded synthetic outputs,
not real provider behavior.

Metrics: automatic-update precision (a wrong application or any wrong changed
field is an error; zero updates prints `unmeasured`), coverage (handled without
review), detection recall (relevant mail updated correctly or sent to review),
and automatic-update recall, each with a 95% Wilson interval.

First run, 2026-10-21 (97 rules and 32 AI scenarios): rules precision 96.3%
(26/27, CI 81.7-99.3%), coverage 46.4%, detection recall 97.2% (69/71). AI
precision (updates made by the AI tier): OpenAI 85.7% (6/7), Anthropic 100%
(8/8), Gemini 87.5% (7/8); these samples are tiny. The PRD precision target
(98%) was not met, so the committed gates hold this baseline instead; the gap
and every disagreeing scenario are listed under Open Decisions in
`docs/superpowers/plans/2026-10-20-harden-evaluate.md`.

## Deploying the development app

The manual deploy targets one SAM stack, `app-tracker-dev`, in `us-east-1`.
It creates five DynamoDB tables, API/worker/dispatcher Lambdas, an HTTP API, a Cognito user
pool with Google sign-in, an Amplify Hosting app, a 14-day Lambda log group,
SQS sync and dead-letter queues, two EventBridge schedules, a customer-managed
KMS key for Gmail credentials, and a $5 monthly budget.
The [KMS key currently costs $1 per month](https://aws.amazon.com/kms/pricing/) while active; the budget sends alerts
and does not cap spending. The existing separate $1 budget remains in place.
The two Google OAuth secrets are separate Secrets Manager resources and have
their own small monthly charges. No automatic deploys or production environment
are included.

### One-time setup

1. Install the [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html),
   [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html),
   and Docker Desktop. Keep Docker Desktop running for `sam build --use-container`.
   Use the pinned Python, Node, npm, and `uv` versions above. Sign in with the
   `application-tracker-bootstrap` AWS profile, then check it without sharing
   the returned account number:

   ```bash
   aws sts get-caller-identity --profile application-tracker-bootstrap --region us-east-1
   ```

2. The account's Free plan was active when checked on October 18, 2026, with
   a scheduled expiration of **March 26, 2027** in Eastern time (March 27 at
   01:48 UTC). It can end earlier if credits run out. Recheck before later
   deployments with `aws freetier get-account-plan-state --profile
   application-tracker-bootstrap --region us-east-1`. The separate $1 budget,
   `application-tracker-zero-spend`, was also present.

3. In [Google Cloud Console](https://console.cloud.google.com/), create or
   select a project. Configure the Google Auth Platform consent screen and
   create an OAuth client of type **Web application**. For the consent screen's
   Audience, **Internal** admits members of the project's Google organization,
   while **External** admits Google accounts outside it. This app requests only
   `openid`, `email`, and `profile`, so Google's External Testing test-user list
   does not restrict sign-in. Use **External** for this app; Cognito's
   `tracker-users` group controls who can use the tracker after sign-in. See
   [Google's audience rules](https://support.google.com/cloud/answer/15549945?hl=en).
   Add an **Authorized JavaScript origin** with the Cognito domain and an
   **Authorized redirect URI** with its `/oauth2/idpresponse` path, replacing
   the account ID with the value from the command below:

   ```bash
   aws sts get-caller-identity --profile application-tracker-bootstrap \
     --region us-east-1 --query Account --output text
   # Origin: https://app-tracker-dev-<account-id>.auth.us-east-1.amazoncognito.com
   # https://app-tracker-dev-<account-id>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse
   ```

   This is Cognito's Google callback, not the Amplify URL. In **Data Access →
   Add or remove scopes**, select only `openid`, `.../auth/userinfo.email`, and
   `.../auth/userinfo.profile`, then click **Update** and **Save**. Save the
   client ID and secret in a password manager; do not paste them into this
   repository or a chat.
   Google may show a newly created client's secret only once.
   [Cognito's Google setup guide](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-social-idp-google.html)
   documents the callback format.

4. Create the named Secrets Manager JSON secret in your own terminal. The
   command prompts for both values without placing the secret in shell history
   or printing it. Replace the name if you choose a different
   `GOOGLE_SECRET_NAME` below:

   ```bash
   python3 -c 'import getpass,json,sys; print("Google client ID: ", end="", file=sys.stderr, flush=True); cid=input(); sec=getpass.getpass("Google client secret: "); print(json.dumps({"client_id":cid,"client_secret":sec}))' |
     aws secretsmanager create-secret \
       --name app-tracker/dev/google-oauth \
       --secret-string file:///dev/stdin \
       --profile application-tracker-bootstrap --region us-east-1 \
       --query ARN --output text
   ```

   This is the secret **name**, not its value, in deployment configuration.
   CloudFormation reads the JSON keys `client_id` and `client_secret` through a
   versionless Secrets Manager reference. Credential rotation requires a
   Cognito provider update as well as a new secret value; simply rerunning an
   unchanged stack does not refresh the dynamic reference.

5. For Gmail consent, enable the Gmail API in the same Google Cloud development
   project. On the Google Auth Platform consent screen, add only
   `https://www.googleapis.com/auth/gmail.readonly` under Data Access. Keep the
   audience **External / Testing** and add the designated Gmail account as a
   test user. This account must have the same email address as its Cognito
   sign-in. Create a **second** Web application OAuth client for Gmail, separate
   from the Cognito sign-in client. Set its authorized redirect URI to the
   existing stack's `ApiUrl` followed by `/oauth/google/callback`:

   ```bash
   aws cloudformation describe-stacks --stack-name app-tracker-dev \
     --profile application-tracker-bootstrap --region us-east-1 \
     --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue | [0]' \
     --output text
   # Redirect URI: https://<api-id>.execute-api.us-east-1.amazonaws.com/oauth/google/callback
   ```

   Store this second client's ID and secret under a different Secrets Manager
   name. Run the prompt in your own terminal; it does not print the secret or
   put it in shell history:

   ```bash
   python3 -c 'import getpass,json,sys; print("Gmail client ID: ", end="", file=sys.stderr, flush=True); cid=input(); sec=getpass.getpass("Gmail client secret: "); print(json.dumps({"client_id":cid,"client_secret":sec}))' |
     aws secretsmanager create-secret \
       --name app-tracker/dev/gmail-oauth \
       --secret-string file:///dev/stdin \
       --profile application-tracker-bootstrap --region us-east-1 \
       --query ARN --output text
   ```

   [Google classifies Gmail read-only access as restricted](https://developers.google.com/workspace/gmail/api/auth/scopes).
   External Testing grants with this scope normally expire after seven days;
   reconnect when authorization expires. Public Gmail access requires a
   separate Google verification and security review before launch.

6. Copy `.env.deploy.example` to `.env.deploy`, then edit the AWS profile,
   region, stack name, alert email, sign-in secret name, and Gmail secret name.
   `.env.deploy` is ignored by Git. It contains no credentials or OAuth secret
   values.

   ```bash
   cp .env.deploy.example .env.deploy
   ```

### Build and deploy

From the repository root, run `./scripts/deploy.sh`. It checks Docker, the AWS
identity, region, and both named secrets before changing the stack. It exports the
locked backend dependencies to an ignored `backend/requirements.txt`, builds
the Lambda in a Linux container, and deploys the SAM stack. It then reads the
public stack outputs, builds the Vite frontend with the API and Cognito URLs,
zips the **contents** of `frontend/dist`, and uploads that archive to Amplify.
It prints the hosted URL after Amplify reports success. The command is safe to
rerun for code changes. A private SAM log stays at `.aws-sam/deploy.log` if the
stack update fails; it may contain configuration values, so do not share it.

The first deployment should be reviewed as a CloudFormation change set before
execution. The driver can prepare one with `sam deploy --no-execute-changeset`
using the built template and the same parameters from `.env.deploy`, inspect
the proposed resources, then run `./scripts/deploy.sh` after approval. Do not
put either Google secret value in a SAM parameter; pass only the Secrets Manager
names. `sam validate --template-file infra/template.yaml --lint` and
`python3 scripts/test_deploy.py -q` are offline checks.

### Alarms and operations

All alarms treat missing data as healthy. Lambda `Errors` alarms fire on one
error in five minutes for the sync worker, sync dispatcher, and account
deletion function; these are crashes, since ordinary sync failures are returned
to SQS rather than raised. Log alarms count sanitized `event=<name>` lines over
15 minutes: `sync_worker_failure` (3 or more), `sync_run_failed` (3 or more),
`api_unhandled_error` (3 or more), `deletion_job_failed` (1), and
`deletion_enqueue_failed` (1). `SyncDeadLetterAlarm` and
`AccountDeletionDeadLetterAlarm` fire when either dead-letter queue holds a
message. Best-effort revoke failures (`deletion_revoke_failed`) do not alarm.

Alarm email is off by default. Set `ALARM_NOTIFICATIONS=true` in `.env.deploy`
to create an SNS topic that emails `BUDGET_EMAIL`; the recipient must click the
AWS confirmation link. `deploy.sh` always passes the value, so removing the
variable turns the topic off on the next deploy.

Deletion dead-letter runbook: the daily recovery pass re-publishes any account
still in the `deleting` state, so a dead-lettered job is redundant. Inspect the
message (it holds only an owner ID), check `deletion_job_failed` logs for the
phase and error category, fix the cause, then purge the queue. The Users table
expires deletion tombstones through TTL on `expires_at`, which can lag up to 48
hours; the API checks `deletion_state` regardless.

### Approve Google accounts

The Google audience is External, so anyone can complete Google sign-in. A new
account initially sees **Access denied** and cannot use any application API
route. In the AWS console for `us-east-1`, open **Amazon Cognito → User pools →
app-tracker-dev-users → Groups → tracker-users**. Add the person's Cognito user
profile after they have signed in once. Confirm the profile's email with the
person before granting access. They must sign out and back in to get a token
with the new group membership. The stack's `UserPoolId` output identifies the
pool if you use the AWS CLI instead.

To revoke access, remove the person from `tracker-users` in Cognito. Existing
access tokens may continue working until they expire, at most one hour under
this app's token setting. Each approved person still sees only applications
under their own Cognito subject.

### Verify and operate

- `GET <ApiUrl>/health` should return 200 without a token. `GET
  <ApiUrl>/applications` should return 401 with no token or a garbage token.
  An `OPTIONS <ApiUrl>/applications` browser preflight with the hosted origin
  and `authorization` request header should return 204 with CORS headers.
  Read `ApiUrl` from the stack outputs in `us-east-1`.
- Sign in as Google account A in one browser profile and confirm **Access
  denied**. Approve A in Cognito, sign out and back in, then create an
  application and record its ID. Sign in as account B in another profile and
  confirm **Access denied**; approve B, sign out and back in, then verify B's
  list is empty and opening A's ID returns 404. Cognito sign-out does not sign
  out the underlying Google account, which is why separate profiles matter.
- On the hosted app, check edit, history, filters, and the stale-edit flow.
  A controlled burst above the API's 10-request-per-second rate and 20-request
  burst setting should eventually return 429. Keep concurrent requests below
  this account's 10-execution Lambda quota; a larger simultaneous burst can
  return 503 from Lambda throttling first.
- Confirm the $5 stack budget and its alert email, the 14-day retention on
  `/aws/lambda/app-tracker-dev-api`, and that no request bodies appear in logs.
- Before approving a Gmail deployment, review the new `Users` and `OAuthStates`
  tables, KMS key, Lambda permissions, and public exact callback route in the
  CloudFormation change set. The callback is public so Google can return to it;
  its single-use state binds the result to the original owner.
- After deployment, `GET <ApiUrl>/gmail/connection` and `POST
  <ApiUrl>/gmail/connect` must return 401 without a token. Sign in as an
  approved designated test user, connect the same Gmail address, confirm the
  status shows that address, then disconnect and confirm the status is empty.
  A different mailbox must fail. Browser URLs after the callback, API responses,
  and Lambda logs must not contain authorization codes, access tokens, refresh
  tokens, or client secrets. The Google callback URL necessarily receives a
  short-lived authorization code before redirecting to the clean Amplify URL.
  The deployed sync worker reads messages only after a connected owner starts a
  run or the daily schedule starts one.

The Milestone 8 CloudFormation change set was reviewed and deployed on
2026-10-20. The stack, hosted frontend, health route, unauthenticated access
controls, browser preflight, schedules, SQS trigger, and dead-letter alarm
passed deployment checks. A designated test mailbox completed a 90-day import
of 1,092 candidate messages; a repeat incremental run completed with 0 new
messages and unchanged stored counts. Scheduled recovery resumed a paused
import once, while later pauses needed operator-triggered recovery. The
pauses were Lambda recursive-loop termination (see below). The 09:00 UTC daily start, new-message
incremental update, revoked authorization and reconnect, and dead-letter alarm
activation still need controlled live checks. Inspect Lambda logs for
identifiers and counts only; do not log email bodies or credentials.

On 2026-10-20 the review queue was cleaned up and redeployed to development:
the corrected classifier is live, `scripts.recheck_reviews` moved 424 false-positive
`conflicting_roles` reviews to `ignored` (pending 809 to 384, all with
sender/subject), and the application record was unchanged. The live review UI
was not exercised in a signed-in browser. Details are in
`docs/superpowers/plans/2026-10-20-review-queue-cleanup.md`.

Troubleshooting a paused import: the sync worker and account deletion function
continue themselves through SQS (Lambda -> SQS -> same Lambda). Lambda's
recursive-loop detection drops a message after about 16 hops in one trace, which
paused the first live import (30 `RecursiveInvocationsDropped` datapoints on the
worker, plus an AWS Health notice). The template now sets `RecursiveLoop: Allow`
on those two functions only. If an import pauses again, check the
`RecursiveInvocationsDropped` metric first; the 15-minute recovery pass still
resumes stalled runs.

For a repeat deploy, run the same script after making code changes. Lambda
logs are in CloudWatch Logs. To remove the stack, run `sam delete --stack-name
app-tracker-dev --region us-east-1 --profile application-tracker-bootstrap`.
The Applications, Events, and SyncRuns tables have `DeletionPolicy: Retain`; deleting the
stack does not delete their data. The Gmail `Users` and `OAuthStates` tables are
deleted with the stack. The KMS key enters a seven-day pending-deletion period
and cannot decrypt credentials during that period. Both separate Secrets
Manager secrets and the $1 budget remain until you explicitly remove them. A
later fresh stack will create new tables rather than automatically reuse
retained ones.

## Recheck pending reviews (operator command)

`backend/scripts/recheck_reviews.py` re-reads each pending review's Gmail message,
re-classifies it with the current rules, and changes only items whose fresh
decision is `ignored` (`review_required` becomes `ignored`; the original reason is
kept as `previous_reason`, evidence is kept, and `subject`/`sender` are removed).
Everything else stays pending, never creates or updates applications, and gets a
bounded `subject`/`sender` backfilled when missing. It refuses to run while a
sync run is active, stops if the Gmail connection generation changes, and
skips items that were resolved or changed since listing.

Run it from `backend/` with the Lambda's environment variables (`USERS_TABLE`,
`EVENTS_TABLE`, `APPLICATIONS_TABLE`, `GMAIL_KMS_KEY_ARN`,
`GMAIL_OAUTH_SECRET_NAME`, optional `OAUTH_STATES_TABLE`) and AWS credentials.
Preview first (no writes), inspect the counts, then add `--apply`:

```bash
uv run python -m scripts.recheck_reviews --owner-id <owner-id>
uv run python -m scripts.recheck_reviews --owner-id <owner-id> --apply
```

It prints aggregate counts as JSON only; subjects, senders, evidence, message
IDs, and tokens are never printed, and errors print only the exception type.
Local AWS SSO-style login sessions may need
`uv run --with "botocore[crt]==1.43.101" python -m scripts.recheck_reviews ...`
(this does not change `uv.lock`).

## Automatic local analysis

The local worker (`python -m local_tools worker`) can run on a schedule as a
macOS LaunchAgent. It runs at load and every 30 minutes, and it needs a
signed-in session and Ollama. Run these from `backend/`.

1. Log in once: `uv run --group local python -m local_tools login`.
2. Install, check, and read the log:

   ```bash
   uv run --group local python -m local_tools.launchagent install
   uv run --group local python -m local_tools.launchagent status
   tail -f ~/Library/Logs/application-tracker/worker.log
   ```

   The plist is written to `~/Library/LaunchAgents/com.applicationtracker.worker.plist`
   with the absolute `uv` path and this `backend/` directory, because launchd
   has a minimal `PATH`. Re-running `install` replaces a loaded agent, so do that after moving the
   repo or `uv`.
3. Uninstall: `uv run --group local python -m local_tools.launchagent uninstall`.
   If `launchctl bootout` fails for any reason other than "not loaded", the
   plist is kept so you can retry.
4. A sleeping Mac does not run the job. To let a backlog drain, keep it awake
   (for example `caffeinate -i`) while on power.
