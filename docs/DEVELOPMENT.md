# Local development

The project contains the manual tracker: a FastAPI backend persisting to
DynamoDB and a React/TypeScript dashboard for creating, editing, filtering,
and inspecting applications. Hosted Google authentication is implemented for
milestone 4, but the first AWS deployment still needs live verification.
Gmail access and AI providers are later milestones.

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

## Deploying the development app

The manual deploy targets one SAM stack, `app-tracker-dev`, in `us-east-1`.
It creates two DynamoDB tables, a FastAPI Lambda and HTTP API, a Cognito user
pool with Google sign-in, an Amplify Hosting app, a 14-day Lambda log group,
and a $5 monthly budget. The existing separate $1 budget remains in place.
The Google OAuth secret is a separate Secrets Manager resource and has a small
monthly charge. No automatic deploys, Gmail scopes, or production environment
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

2. In the AWS console, recheck the account's Free plan end date. The design
   discussion showed **March 26, 2027**; that date must be confirmed in the
   account before the first deploy. Check that the separate $1 budget is still
   present if you want that earlier alert.

3. In [Google Cloud Console](https://console.cloud.google.com/), create or
   select a project. Configure the Google Auth Platform consent screen and
   create an OAuth client of type **Web application**. For the consent screen's
   Audience, **Internal** admits members of the project's Google organization,
   while **External** admits Google accounts outside it. This app requests only
   `openid`, `email`, and `profile`, so Google's External Testing test-user list
   does not restrict sign-in. Use **External** for this app; Cognito's
   `tracker-users` group controls who can use the tracker after sign-in. See
   [Google's audience rules](https://support.google.com/cloud/answer/15549945?hl=en).
   Add this **Authorized redirect URI**, replacing the account ID with the
   value from the command below:

   ```bash
   aws sts get-caller-identity --profile application-tracker-bootstrap \
     --region us-east-1 --query Account --output text
   # https://app-tracker-dev-<account-id>.auth.us-east-1.amazoncognito.com/oauth2/idpresponse
   ```

   This is Cognito's Google callback, not the Amplify URL. Request only the
   `openid`, `email`, and `profile` scopes. Save the client ID and secret in a
   password manager; do not paste them into this repository or a chat.
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

5. Copy `.env.deploy.example` to `.env.deploy`, then edit the AWS profile,
   region, stack name, alert email, and secret name. `.env.deploy` is ignored by
   Git. It contains no credentials or OAuth secret values.

   ```bash
   cp .env.deploy.example .env.deploy
   ```

### Build and deploy

From the repository root, run `./scripts/deploy.sh`. It checks Docker, the AWS
identity, region, and named secret before changing the stack. It exports the
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
put the Google secret value in a SAM parameter; pass only its Secrets Manager
name. `sam validate --template-file infra/template.yaml --lint` and
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
  Read `ApiUrl` from the stack outputs in `us-east-1`.
- Sign in as Google account A in one browser profile and confirm **Access
  denied**. Approve A in Cognito, sign out and back in, then create an
  application and record its ID. Sign in as account B in another profile and
  confirm **Access denied**; approve B, sign out and back in, then verify B's
  list is empty and opening A's ID returns 404. Cognito sign-out does not sign
  out the underlying Google account, which is why separate profiles matter.
- On the hosted app, check edit, history, filters, and the stale-edit flow.
  A controlled burst above the API's 10-request-per-second rate and 20-request
  burst setting should eventually return 429.
- Confirm the $5 stack budget and its alert email, the 14-day retention on
  `/aws/lambda/app-tracker-dev-api`, and that no request bodies appear in logs.

For a repeat deploy, run the same script after making code changes. Lambda
logs are in CloudWatch Logs. To remove the stack, run `sam delete --stack-name
app-tracker-dev --region us-east-1 --profile application-tracker-bootstrap`.
The two DynamoDB tables have `DeletionPolicy: Retain`; deleting the stack does
not delete their data. The separate Secrets Manager secret and $1 budget also
remain until you explicitly remove them. A later fresh stack will create new
tables rather than automatically reuse retained ones.
