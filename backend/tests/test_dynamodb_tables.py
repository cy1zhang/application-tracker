import boto3
from moto import mock_aws

from app.repositories.dynamodb_tables import create_tables


def test_create_tables_is_idempotent() -> None:
    with mock_aws():
        client = boto3.client(
            "dynamodb",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )

        create_tables(client, applications_table="Applications", events_table="Events")
        create_tables(client, applications_table="Applications", events_table="Events")

        assert sorted(client.list_tables()["TableNames"]) == ["Applications", "Events"]
        events = client.describe_table(TableName="Events")["Table"]
        assert [index["IndexName"] for index in events["GlobalSecondaryIndexes"]] == [
            "application-events-index"
        ]
