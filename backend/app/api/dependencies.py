import os

import boto3
from fastapi import HTTPException

from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb import DynamoApplicationRepository


def get_current_subject() -> str:
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
