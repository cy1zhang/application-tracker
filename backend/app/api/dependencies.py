import os
import re
from typing import Annotated, Any

import boto3
from fastapi import Depends, HTTPException, Request

from app.domain.account import AccountState
from app.domain.ai import AiProvider
from app.domain.gmail import TrackerIdentity
from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_account import DynamoAccountRepository
from app.repositories.dynamodb_ai import DynamoAiSettingsRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.repositories.dynamodb_reviews import DynamoReviewRepository
from app.repositories.dynamodb_sync import DynamoSyncRepository
from app.services.credential_crypto import CredentialCipher
from app.services.google_oauth import GoogleOAuthClient

LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
APPROVED_GROUP = "tracker-users"


def get_current_subject(request: Request) -> str:
    subject = os.getenv("LOCAL_DEV_SUBJECT", "").strip()
    if subject and os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        raise RuntimeError("LOCAL_DEV_SUBJECT must not be set in AWS Lambda")

    if event := request.scope.get("aws.event"):
        claims = (
            event.get("requestContext", {})
            .get("authorizer", {})
            .get("jwt", {})
            .get("claims", {})
        )
        verified_subject = claims.get("sub")
        if isinstance(verified_subject, str) and verified_subject.strip():
            groups = claims.get("cognito:groups")
            if isinstance(groups, str):
                groups = groups.strip("[]").replace(",", " ").replace('"', "").split()
            if not isinstance(groups, (list, tuple)) or APPROVED_GROUP not in groups:
                raise HTTPException(status_code=403, detail="Access denied")
            return verified_subject
        raise HTTPException(status_code=401, detail="Authentication required")

    if subject and request.client is not None and request.client.host in LOOPBACK_HOSTS:
        return subject
    raise HTTPException(status_code=401, detail="Authentication required")


def get_cognito_username(request: Request) -> str | None:
    """The verified Cognito `username` claim; None outside API Gateway (local dev)."""
    claims = (
        request.scope.get("aws.event", {})
        .get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    username = claims.get("username")
    if isinstance(username, str) and username.strip():
        return username
    return None


def get_account_repository() -> DynamoAccountRepository:
    client_options = {}
    if region := os.getenv("AWS_REGION"):
        client_options["region_name"] = region
    return DynamoAccountRepository(
        client=boto3.client("dynamodb", **client_options),
        users_table=os.environ["USERS_TABLE"],
    )


def require_active_account(
    subject: Annotated[str, Depends(get_current_subject)],
    accounts: Annotated[DynamoAccountRepository, Depends(get_account_repository)],
) -> None:
    # One consistent Users read per authenticated request.
    state = accounts.get_status(subject).state
    if state == AccountState.DELETING:
        raise HTTPException(status_code=423, detail="account_deleting")
    if state == AccountState.DELETED:
        raise HTTPException(status_code=410, detail="account_deleted")


def get_application_repository() -> ApplicationRepository:
    client_options = {}
    if region := os.getenv("AWS_REGION"):
        client_options["region_name"] = region

    return DynamoApplicationRepository(
        client=boto3.client("dynamodb", **client_options),
        applications_table=os.environ["APPLICATIONS_TABLE"],
        events_table=os.environ["EVENTS_TABLE"],
    )


def get_review_repository() -> DynamoReviewRepository:
    client_options = {}
    if region := os.getenv("AWS_REGION"):
        client_options["region_name"] = region
    return DynamoReviewRepository(
        client=boto3.client("dynamodb", **client_options),
        applications_table=os.environ["APPLICATIONS_TABLE"],
        events_table=os.environ["EVENTS_TABLE"],
        users_table=os.environ["USERS_TABLE"],
    )


def get_sync_repository() -> DynamoSyncRepository:
    client_options = {}
    if region := os.getenv("AWS_REGION"):
        client_options["region_name"] = region
    return DynamoSyncRepository(
        client=boto3.client("dynamodb", **client_options),
        users_table=os.environ["USERS_TABLE"],
        sync_runs_table=os.environ["SYNC_RUNS_TABLE"],
    )


def get_sync_publisher() -> Any:
    return boto3.client("sqs")


def get_cognito_client() -> Any:
    return boto3.client("cognito-idp")


def get_current_identity(
    request: Request,
    subject: Annotated[str, Depends(get_current_subject)],
    cognito_client: Annotated[Any, Depends(get_cognito_client)],
) -> TrackerIdentity:
    claims = (
        request.scope.get("aws.event", {})
        .get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    username = claims.get("username")
    if not isinstance(username, str) or not username.strip():
        raise HTTPException(status_code=401, detail="Authentication required")

    user = cognito_client.admin_get_user(
        UserPoolId=os.environ["COGNITO_USER_POOL_ID"], Username=username
    )
    attributes = {item["Name"]: item["Value"] for item in user["UserAttributes"]}
    email = attributes.get("email")
    if (
        attributes.get("sub") != subject
        or attributes.get("email_verified") != "true"
        or not isinstance(email, str)
        or not email.strip()
    ):
        raise HTTPException(status_code=401, detail="Authentication required")

    return TrackerIdentity(owner_id=subject, username=username, email=email)


def get_gmail_repository() -> DynamoGmailRepository:
    return DynamoGmailRepository(
        client=boto3.client("dynamodb"),
        users_table=os.environ["USERS_TABLE"],
        oauth_states_table=os.environ["OAUTH_STATES_TABLE"],
    )


def get_ai_settings_repository() -> DynamoAiSettingsRepository:
    client_options = {}
    if region := os.getenv("AWS_REGION"):
        client_options["region_name"] = region
    return DynamoAiSettingsRepository(
        client=boto3.client("dynamodb", **client_options),
        users_table=os.environ["USERS_TABLE"],
    )


def get_ai_available_providers() -> list[AiProvider]:
    return [
        provider
        for provider in AiProvider
        if os.getenv(f"AI_MODEL_{provider.name}", "").strip()
    ]


def get_credential_cipher() -> CredentialCipher:
    return CredentialCipher(boto3.client("kms"), os.environ["GMAIL_KMS_KEY_ARN"])


def get_gmail_redirect_uri(request: Request) -> str:
    context = request.scope.get("aws.event", {}).get("requestContext", {})
    api_id = context.get("apiId")
    if not isinstance(api_id, str) or re.fullmatch(r"[a-z0-9]+", api_id) is None:
        raise RuntimeError("Gmail OAuth requires an API Gateway request")
    return (
        f"https://{api_id}.execute-api.{os.environ['AWS_REGION']}"
        ".amazonaws.com/oauth/google/callback"
    )


def get_google_oauth_client(request: Request) -> GoogleOAuthClient:
    return GoogleOAuthClient(
        boto3.client("secretsmanager"),
        os.environ["GMAIL_OAUTH_SECRET_NAME"],
        get_gmail_redirect_uri(request),
    )
