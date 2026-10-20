import json
from datetime import datetime
from uuid import uuid4

from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from app.repositories.dynamodb_sync import SyncUnavailable


def _publish(publisher, queue_url, owner_id, run_id, generation):
    try:
        publisher.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({
                "owner_id": owner_id, "run_id": run_id, "generation": generation,
            }),
        )
    except ClientError:
        # The run remains durable and the next recovery pass can enqueue it.
        pass


def _scan_pages(client, table, page_size, **filters):
    decoder = TypeDeserializer()
    request = {"TableName": table, "Limit": page_size, **filters}
    while True:
        page = client.scan(**request)
        for encoded in page.get("Items", []):
            yield {key: decoder.deserialize(value) for key, value in encoded.items()}
        if "LastEvaluatedKey" not in page:
            return
        request["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def dispatch_daily(
    client, syncs, publisher, users_table, queue_url, now: datetime, *, page_size=100
):
    for user in _scan_pages(
        client, users_table, page_size,
        FilterExpression="attribute_exists(email) AND attribute_exists(encrypted_token)",
        ProjectionExpression="owner_id, email, encrypted_token",
    ):
        try:
            run = syncs.start(user["owner_id"], uuid4().hex, now)
        except SyncUnavailable:
            continue
        _publish(publisher, queue_url, run.owner_id, run.run_id, run.generation)


def recover_unfinished(
    client, publisher, users_table, sync_runs_table, queue_url, now: datetime,
    *, page_size=100,
):
    for run in _scan_pages(
        client, sync_runs_table, page_size,
        FilterExpression=(
            "#state = :pending OR (#state = :running AND lease_expires_at < :now)"
        ),
        ExpressionAttributeNames={"#state": "status"},
        ExpressionAttributeValues={
            ":pending": {"S": "pending"},
            ":running": {"S": "running"},
            ":now": {"S": now.isoformat()},
        },
        ProjectionExpression="owner_id, run_id, generation",
    ):
        user_response = client.get_item(
            TableName=users_table,
            Key={"owner_id": {"S": run["owner_id"]}},
            ConsistentRead=True,
        )
        user = user_response.get("Item", {})
        if (
            user.get("active_run_id", {}).get("S") != run["run_id"]
            or user.get("generation", {}).get("N") != str(run["generation"])
            or "encrypted_token" not in user
        ):
            try:
                client.update_item(
                    TableName=sync_runs_table,
                    Key={
                        "owner_id": {"S": run["owner_id"]},
                        "run_id": {"S": run["run_id"]},
                    },
                    UpdateExpression=(
                        "SET #state = :failed, completed_at = :now "
                        "REMOVE lease_id, lease_expires_at"
                    ),
                    ConditionExpression="#state = :pending OR #state = :running",
                    ExpressionAttributeNames={"#state": "status"},
                    ExpressionAttributeValues={
                        ":failed": {"S": "failed"},
                        ":pending": {"S": "pending"},
                        ":running": {"S": "running"},
                        ":now": {"S": now.isoformat()},
                    },
                )
            except ClientError as error:
                if error.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            continue
        _publish(publisher, queue_url, run["owner_id"], run["run_id"], int(run["generation"]))
