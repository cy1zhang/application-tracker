#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  printf 'Deploy stopped: %s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command missing: $1"
}

[[ -f .env.deploy ]] || fail "create .env.deploy from .env.deploy.example"
# This file is local and git-ignored. It contains names and an alert address, not OAuth credentials.
source .env.deploy

for name in AWS_PROFILE AWS_REGION STACK_NAME BUDGET_EMAIL GOOGLE_SECRET_NAME GMAIL_SECRET_NAME; do
  [[ -n "${!name:-}" ]] || fail "set $name in .env.deploy"
done
[[ "$AWS_REGION" == us-east-1 ]] || fail "AWS_REGION must be us-east-1 for this stack"
export AWS_PROFILE AWS_REGION
export AWS_DEFAULT_REGION="$AWS_REGION"

for command_name in aws sam uv npm zip curl python3 docker; do
  require_command "$command_name"
done
docker info >/dev/null 2>&1 || fail "Docker Desktop must be running for the Linux Lambda build"
aws sts get-caller-identity --query Account --output text >/dev/null 2>&1 ||
  fail "AWS identity check failed for the configured profile"
aws secretsmanager describe-secret --secret-id "$GOOGLE_SECRET_NAME" \
  --query ARN --output text >/dev/null 2>&1 ||
  fail "Google OAuth secret is missing or inaccessible in us-east-1"
aws secretsmanager describe-secret --secret-id "$GMAIL_SECRET_NAME" \
  --query ARN --output text >/dev/null 2>&1 ||
  fail "Gmail OAuth secret is missing or inaccessible in us-east-1"

printf 'Building Lambda package in a Linux container...\n'
(cd backend && uv export --locked --no-dev --format requirements.txt --output-file requirements.txt >/dev/null)
sam build --use-container --template-file infra/template.yaml

# Model IDs are optional. An unset or blank variable is omitted from the overrides,
# so the template default (blank, provider unavailable) applies; sam deploy does not
# reuse previous parameter values, so removing a variable also blanks the stack value.
parameters=("BudgetEmail=$BUDGET_EMAIL" "GoogleOAuthSecretName=$GOOGLE_SECRET_NAME" "GmailOAuthSecretName=$GMAIL_SECRET_NAME")
for pair in OpenAiModelId:OPENAI_MODEL_ID AnthropicModelId:ANTHROPIC_MODEL_ID GeminiModelId:GEMINI_MODEL_ID; do
  variable="${pair#*:}"
  value="${!variable:-}"
  [[ -z "$value" ]] || parameters+=("${pair%%:*}=$value")
done

mkdir -p .aws-sam
printf 'Deploying CloudFormation stack %s...\n' "$STACK_NAME"
if ! sam deploy \
  --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --no-progressbar \
  --parameter-overrides "${parameters[@]}" \
  >.aws-sam/deploy.log 2>&1; then
  fail "SAM deploy failed; inspect the private .aws-sam/deploy.log on this laptop"
fi

stack_output() {
  local key="$1" value
  value="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" --output text)" ||
    fail "could not read stack output $key"
  [[ -n "$value" && "$value" != None ]] || fail "stack output $key is missing"
  printf '%s' "$value"
}

api_url="$(stack_output ApiUrl)"
cognito_authority="$(stack_output CognitoAuthority)"
cognito_client_id="$(stack_output CognitoClientId)"
cognito_domain="$(stack_output CognitoDomain)"
amplify_app_id="$(stack_output AmplifyAppId)"
amplify_url="$(stack_output AmplifyUrl)"

printf 'Building the frontend with stack outputs...\n'
(
  cd frontend
  VITE_API_BASE_URL="$api_url" \
  VITE_COGNITO_AUTHORITY="$cognito_authority" \
  VITE_COGNITO_CLIENT_ID="$cognito_client_id" \
  VITE_COGNITO_DOMAIN="$cognito_domain" \
  npm run build
)
[[ -f frontend/dist/index.html ]] || fail "frontend build did not produce dist/index.html"

temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/app-tracker-deploy.XXXXXX")"
trap 'rm -rf "$temp_dir"' EXIT
archive="$temp_dir/frontend.zip"
(cd frontend/dist && zip -qr "$archive" .)

deployment="$(aws amplify create-deployment --app-id "$amplify_app_id" \
  --branch-name main --output json)" || fail "could not create Amplify deployment"
json_field() {
  printf '%s' "$deployment" | python3 -c \
    'import json, sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$1"
}
upload_url="$(json_field zipUploadUrl)"
job_id="$(json_field jobId)"
[[ -n "$upload_url" && -n "$job_id" ]] || fail "Amplify deployment response is incomplete"
curl --fail --silent --show-error --request PUT \
  --header 'Content-Type: application/zip' --upload-file "$archive" "$upload_url" >/dev/null ||
  fail "Amplify archive upload failed"
aws amplify start-deployment --app-id "$amplify_app_id" --branch-name main \
  --job-id "$job_id" >/dev/null || fail "Amplify deployment could not start"

for attempt in {1..60}; do
  status="$(aws amplify get-job --app-id "$amplify_app_id" --branch-name main \
    --job-id "$job_id" --query 'job.summary.status' --output text)" ||
    fail "could not read Amplify deployment status"
  case "$status" in
    SUCCEED) printf 'Hosted app: %s\n' "$amplify_url"; exit 0 ;;
    FAILED|CANCELLED) fail "Amplify deployment ended with status $status" ;;
  esac
  sleep 5
done
fail "Amplify deployment is still running; check job $job_id in Amplify"
