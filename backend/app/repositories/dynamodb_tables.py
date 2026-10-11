from botocore.exceptions import ClientError


def create_tables(client, *, applications_table: str, events_table: str) -> None:
    _create_if_missing(
        client,
        TableName=applications_table,
        KeySchema=[
            {"AttributeName": "owner_id", "KeyType": "HASH"},
            {"AttributeName": "application_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "application_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    _create_if_missing(
        client,
        TableName=events_table,
        KeySchema=[
            {"AttributeName": "owner_id", "KeyType": "HASH"},
            {"AttributeName": "event_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "owner_id", "AttributeType": "S"},
            {"AttributeName": "event_id", "AttributeType": "S"},
            {"AttributeName": "owner_application_id", "AttributeType": "S"},
            {"AttributeName": "occurred_at_event_id", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "application-events-index",
                "KeySchema": [
                    {"AttributeName": "owner_application_id", "KeyType": "HASH"},
                    {"AttributeName": "occurred_at_event_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_if_missing(client, **definition) -> None:
    try:
        client.create_table(**definition)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceInUseException":
            raise
