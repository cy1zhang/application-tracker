# Milestone 4: Hosted authentication and development deployment

**Status:** Approved 2026-10-17; implementation underway.

## Intent and scope

Make the existing manual tracker usable through a hosted development URL with
Google sign-in. Keep each user's applications and history private to that user.
Deploy deliberately from the developer's laptop so the API and frontend use
matching configuration. This is one development environment, not a public
launch or a production environment.

Milestone 4 ends when the hosted app supports sign-in, application CRUD and
history, and a two-account check proves owner isolation. Gmail authorization,
email synchronization, AI classification, automatic deployment, and a custom
domain belong to later work.

## AWS architecture

Use one AWS SAM/CloudFormation stack named `app-tracker-dev` in `us-east-1`.
`infra/template.yaml` defines:

- A Cognito user pool federated with Google, with no password-based app accounts;
  a public app client uses authorization code with PKCE. Cognito redirects only
  to the Amplify development URL. The Cognito domain sends users directly to
  Google's account picker.
- An API Gateway HTTP API. `/health` is public; every application route has a
  JWT authorizer configured for this user pool and app client. The API permits
  cross-origin requests only from the Amplify URL and permits the
  `Authorization` header. Configure an approximately 10-request-per-second
  rate limit with a burst of 20; repeated requests are checked for 429 after
  deployment.
- A Python 3.12 arm64 Lambda running the existing FastAPI service through
  Mangum. Only this API Gateway may invoke it. Its execution role can access
  the two application tables it needs, and its CloudWatch log group retains
  logs for 14 days. Do not log request bodies. Reserve a small concurrency cap
  if the account's Lambda quota permits one; otherwise rely on the API rate
  limit and record that limitation during deployment.
- The existing Applications and Events DynamoDB table layouts, including the
  `application-events-index`, on-demand billing. Retain both tables if the
  stack is deleted so teardown cannot silently remove user data.
- One Amplify Hosting app for manually uploaded static builds on `main`, with
  single-page-app fallback routing. It is not connected to GitHub.
- A $5 monthly AWS Budget that measures usage before credits, with an actual
  alert at 80% and a forecast alert. The notification address is a deploy-time
  setting and is never committed.

The Google OAuth client ID and secret are stored in AWS Secrets Manager.
CloudFormation reads them when configuring Cognito. Neither value is committed
or printed by the deploy script. This is the selected design even though the
secret incurs a small monthly AWS charge.

## Backend identity boundary

Add `mangum` as the Lambda adapter. The deployed `get_current_subject` reads
the Cognito `sub` from API Gateway's verified JWT claims in the Lambda event
and uses it as the repository's `owner_id`. It fails with 401 when the event
lacks a valid subject, including if a route is accidentally left public.
Headers, query parameters, and request bodies never select an owner. Lambda
has no public function URL. The existing `LOCAL_DEV_SUBJECT` loopback path
remains for local development and cannot run inside Lambda.

Tests call the real Mangum handler with synthetic API Gateway events and a
moto-backed repository. They cover a valid subject, missing claims, a forged
owner header, public `/health`, and cross-user reads. Deployed checks confirm
API Gateway rejects missing and garbage tokens before they reach the app.

## Frontend sign-in

Use `oidc-client-ts` for Cognito's redirect-based code and PKCE flow. Without
the four `VITE_` deployment variables, the existing local frontend continues
to use `/api` through Vite and requires no browser sign-in. With deployed
configuration, an unauthenticated visitor sees a Google sign-in button;
successful sign-in returns them to the dashboard. The header shows their email
and a sign-out action.

Keep tokens in browser `sessionStorage`. Configure one-hour access tokens and
a one-day refresh-token lifetime. The client sends the access token in the
`Authorization: Bearer` header on API calls, renews it while the session is
valid, and returns to sign-in after a 401. Sign-out clears the local session
and ends the Cognito session. Cognito does not sign the user out of Google, so
a later sign-in may reuse their Google account; use separate browser profiles
for the two-account verification. Frontend tests use a fake user manager to cover
local mode, signed-out and signed-in views, token attachment, 401 handling,
and sign-out. The actual Google redirect is verified on the hosted app.

The build receives `VITE_API_BASE_URL`, `VITE_COGNITO_AUTHORITY`,
`VITE_COGNITO_CLIENT_ID`, and `VITE_COGNITO_DOMAIN` from stack outputs. These
are public identifiers, not secrets.

## Deployment and operations

`scripts/deploy.sh` runs from the developer's laptop. It checks for SAM and
working AWS credentials, confirms `us-east-1` and the Google secret, builds
and deploys the stack, reads its outputs, builds the frontend with those
values, manually uploads the build to Amplify, and prints the hosted URL.
Repeated runs update the same stack and hosting app. The stack name and budget
email live in a Git-ignored local config. The Amplify default URL can be
computed from the app ID, allowing Cognito redirects and hosting to be
configured in a single stack deployment.

One-time prerequisites are SAM CLI installation, Docker Desktop running for
Linux-compatible Lambda packaging, a Google Cloud project and
OAuth web client for Cognito, a Google sign-in test account, and a Secrets
Manager secret created without exposing its value in the repo or agent logs.
The developer supplies the secret through their own terminal. The deployment
guide will give exact console and CLI steps, including the Cognito redirect
URI needed in Google Cloud.

After the first deployment, verify `/health` returns 200; missing and invalid
tokens return 401 for `/applications`; account A can create an application
that account B cannot list or open; and edit, history, filters, and stale-edit
handling work on the hosted app. Repeated requests should encounter 429.
Confirm the $5 budget and alerts exist. The deployment guide documents logs,
re-running the script, and `sam delete` teardown with retained tables. CI
validates the SAM template without AWS credentials and continues the existing
backend and frontend checks.

## Existing account state and calls to confirm

The AWS CLI profile `application-tracker-bootstrap` currently authenticates.
A read-only inventory on 2026-10-17 found no application stack, Cognito pool,
Amplify app, DynamoDB tables, or Secrets Manager secret in `us-east-1` or
`us-east-2`. There is already a separate $1 monthly budget named
`application-tracker-zero-spend`. The proposed $5 stack budget would coexist
with it; the $1 alert may arrive first. SAM CLI was installed for implementation.

The AWS account's Free plan panel showed an end date of 2027-03-26 during the
design discussion. Recheck this date in the account before deployment and
document it in the deployment guide. The account login used `us-east-2`, but
the app deployment region selected in the earlier design is `us-east-1`.

**Design call for review:** Keep the existing $1 budget as an extra alert and
create the previously selected $5 budget with the stack. No existing budget
needs to be edited or deleted.
