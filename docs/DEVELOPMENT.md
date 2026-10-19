# Local development

The project contains the manual tracker: a FastAPI backend persisting to
DynamoDB and a React/TypeScript dashboard for creating, editing, filtering,
and inspecting applications. Hosted Google sign-in and the manual tracker were
verified in the development deployment. Milestone 5 adds a separate Gmail
connection. Milestone 6 adds offline, deterministic processing of synthetic
Gmail messages; live import, scheduling, and AI providers are later milestones.

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
that role; unsupported wording stays for review. The code does not
fetch Gmail messages or run automatically yet. A later sync worker will supply
the owner and candidate applications from trusted records.
Forwarded messages and messages whose only event evidence is in the subject
also go to review, so old thread text cannot create a new status change.

Use `uv run pytest tests/test_gmail_message.py tests/test_email_rules.py
tests/test_dynamodb_email.py tests/test_email_processing.py -q` from `backend/`
to run the synthetic processing checks. No Gmail account or AWS credentials are
needed.

## Deploying the development app

The manual deploy targets one SAM stack, `app-tracker-dev`, in `us-east-1`.
It creates four DynamoDB tables, a FastAPI Lambda and HTTP API, a Cognito user
pool with Google sign-in, an Amplify Hosting app, a 14-day Lambda log group,
a customer-managed KMS key for Gmail credentials, and a $5 monthly budget.
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
  This milestone does not read messages or run a scheduler.

For a repeat deploy, run the same script after making code changes. Lambda
logs are in CloudWatch Logs. To remove the stack, run `sam delete --stack-name
app-tracker-dev --region us-east-1 --profile application-tracker-bootstrap`.
The Applications and Events tables have `DeletionPolicy: Retain`; deleting the
stack does not delete their data. The Gmail `Users` and `OAuthStates` tables are
deleted with the stack. The KMS key enters a seven-day pending-deletion period
and cannot decrypt credentials during that period. Both separate Secrets
Manager secrets and the $1 budget remain until you explicitly remove them. A
later fresh stack will create new tables rather than automatically reuse
retained ones.
