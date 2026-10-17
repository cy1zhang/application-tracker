import os

import boto3
from fastapi import HTTPException, Request

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
                groups = groups.strip("[]").replace(",", " ").split()
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
