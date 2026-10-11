"""Create the DynamoDB Local tables for development. Safe to run repeatedly."""

import os
from urllib.parse import urlparse

import boto3

from app.repositories.dynamodb_tables import create_tables

LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def main() -> None:
    endpoint = os.getenv("AWS_ENDPOINT_URL_DYNAMODB", "")
    if urlparse(endpoint).hostname not in LOCAL_HOSTS:
        raise SystemExit(
            "AWS_ENDPOINT_URL_DYNAMODB must point at DynamoDB Local on localhost "
            f"(got {endpoint!r}); refusing to create tables."
        )
    client = boto3.client(
        "dynamodb", region_name=os.environ["AWS_REGION"], endpoint_url=endpoint
    )
    create_tables(
        client,
        applications_table=os.environ["APPLICATIONS_TABLE"],
        events_table=os.environ["EVENTS_TABLE"],
    )
    print("Local DynamoDB tables are ready.")


if __name__ == "__main__":
    main()
