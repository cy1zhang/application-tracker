import os

import boto3
from fastapi import HTTPException, Request

from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb import DynamoApplicationRepository

LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


def get_current_subject(request: Request) -> str:
    if subject := os.getenv("LOCAL_DEV_SUBJECT", "").strip():
        if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
            raise RuntimeError("LOCAL_DEV_SUBJECT must not be set in AWS Lambda")
        if request.client is not None and request.client.host in LOOPBACK_HOSTS:
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
