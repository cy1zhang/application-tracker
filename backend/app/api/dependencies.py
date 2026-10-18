import os
from typing import Annotated, Any

import boto3
from fastapi import Depends, HTTPException, Request

from app.domain.gmail import TrackerIdentity
from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb import DynamoApplicationRepository

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


def get_application_repository() -> ApplicationRepository:
    client_options = {}
    if region := os.getenv("AWS_REGION"):
        client_options["region_name"] = region

    return DynamoApplicationRepository(
        client=boto3.client("dynamodb", **client_options),
        applications_table=os.environ["APPLICATIONS_TABLE"],
        events_table=os.environ["EVENTS_TABLE"],
    )


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
