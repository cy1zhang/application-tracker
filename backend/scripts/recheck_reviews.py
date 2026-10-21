"""One-time operator command: re-classify pending Gmail reviews with current rules.

Default is preview (no writes). Only a fresh ``ignored`` decision flips a pending
review; everything else stays pending. Prints aggregate counts only.
"""
import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from app.repositories.dynamodb import DynamoApplicationRepository
from app.repositories.dynamodb_gmail import DynamoGmailRepository
from app.services.credential_crypto import CredentialCipher
from app.services.email_processing import extract_headers
from app.services.email_rules import classify_message
from app.services.gmail_fetch import GmailApiError, GmailFetchClient, MessageUnavailable
from app.services.gmail_message import MessageParseError, parse_gmail_message
from app.services.google_oauth import GoogleOAuthClient
from app.services.sync_candidates import load_candidates

_MAX_HEADER = 300
_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


class NoActiveConnection(Exception):
    pass


class SyncInProgress(Exception):
    pass


def _enc(values: dict) -> dict:
    return {key: _serializer.serialize(value) for key, value in values.items()}


def _list_pending(client, events_table: str, owner_id: str) -> list[dict]:
    items, start = [], None
    while True:
        query = {
            "TableName": events_table,
            "KeyConditionExpression": "owner_id = :owner AND begins_with(event_id, :prefix)",
            "FilterExpression": "outcome = :review AND attribute_not_exists(resolution)",
            "ExpressionAttributeValues": _enc({
                ":owner": owner_id, ":prefix": "gmail#", ":review": "review_required",
            }),
            "ConsistentRead": True,
        }
        if start is not None:
            query["ExclusiveStartKey"] = start
        response = client.query(**query)
        items.extend(
            {k: _deserializer.deserialize(v) for k, v in item.items()}
            for item in response.get("Items", [])
        )
        start = response.get("LastEvaluatedKey")
        if start is None:
            return items


def _user(client, users_table: str, owner_id: str) -> dict:
    response = client.get_item(
        TableName=users_table, Key=_enc({"owner_id": owner_id}), ConsistentRead=True
    )
    return {
        k: _deserializer.deserialize(v) for k, v in response.get("Item", {}).items()
    }


def recheck_reviews(
    *, owner_id, apply, client, connections, cipher, oauth, fetch, applications,
    events_table, users_table, now,
) -> dict:
    connection = connections.get_connection(owner_id)
    if connection.email is None or connection.encrypted_token is None:
        raise NoActiveConnection
    generation = connection.generation
    if _user(client, users_table, owner_id).get("active_run_id") is not None:
        raise SyncInProgress

    access_token = oauth.refresh_access_token(
        cipher.decrypt_refresh_token(owner_id, connection.encrypted_token)
    )
    candidates = load_candidates(owner_id, applications)
    pending = _list_pending(client, events_table, owner_id)

    counts: Counter = Counter()
    pending_by_reason: Counter = Counter(item["reason"] for item in pending)
    retained_by_reason: Counter = Counter()

    def transact(item: dict, update: str, values: dict) -> str:
        """Returns 'ok', 'changed', or 'stopped'."""
        try:
            client.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": events_table,
                    "Key": _enc({"owner_id": owner_id, "event_id": item["event_id"]}),
                    "UpdateExpression": update,
                    "ConditionExpression": (
                        "outcome = :review AND reason = :old_reason "
                        "AND attribute_not_exists(resolution)"
                    ),
                    "ExpressionAttributeValues": _enc({
                        ":review": "review_required", ":old_reason": item["reason"],
                        **values,
                    }),
                }},
                {"ConditionCheck": {
                    "TableName": users_table,
                    "Key": _enc({"owner_id": owner_id}),
                    "ConditionExpression": (
                        "generation = :generation AND attribute_exists(encrypted_token) "
                        "AND attribute_not_exists(active_run_id)"
                    ),
                    "ExpressionAttributeValues": _enc({":generation": generation}),
                }},
            ])
        except ClientError as error:
            if error.response["Error"]["Code"] != "TransactionCanceledException":
                raise
            user = _user(client, users_table, owner_id)
            if (
                user.get("generation") != generation
                or "encrypted_token" not in user
                or "active_run_id" in user
            ):
                return "stopped"
            return "changed"
        return "ok"

    def backfill(item: dict, subject, sender) -> None:
        values = {}
        if "subject" not in item and subject:
            values[":subject"] = subject[:_MAX_HEADER]
        if "sender" not in item and sender:
            values[":sender"] = sender[:_MAX_HEADER]
        if not values:
            return
        if not apply:
            return
        assignments = ", ".join(
            f"{name} = if_not_exists({name}, :{name})" for name in ("subject", "sender")
            if f":{name}" in values
        )
        result = transact(item, f"SET {assignments}", values)
        if result == "ok":
            counts["headers_backfilled"] += 1
        elif result == "changed":
            counts["skipped_changed"] += 1
        else:
            counts["stopped_generation_changed"] = 1

    for item in pending:
        if connections.get_active(owner_id, generation) is None:
            counts["stopped_generation_changed"] = 1
            break
        counts["scanned"] += 1
        try:
            raw = fetch.get_message(access_token, item["source_message_id"])
        except MessageUnavailable:
            counts["unavailable"] += 1
            continue
        except GmailApiError:
            counts["fetch_failed"] += 1
            continue
        try:
            message = parse_gmail_message(raw)
        except MessageParseError:
            counts["unparseable"] += 1
            backfill(item, *extract_headers(raw))
            if counts["stopped_generation_changed"]:
                break
            continue

        decision = classify_message(message, candidates)
        if decision.outcome == "ignored":
            if not apply:
                counts["would_ignore"] += 1
                continue
            result = transact(
                item,
                "SET outcome = :ignored, reason = :new_reason, "
                "previous_outcome = :review, previous_reason = :old_reason, "
                "rechecked_at = :now REMOVE subject, sender",
                {
                    ":ignored": "ignored", ":new_reason": decision.reason,
                    ":now": now.isoformat(),
                },
            )
            if result == "ok":
                counts["ignored"] += 1
            elif result == "changed":
                counts["skipped_changed"] += 1
            else:
                counts["stopped_generation_changed"] = 1
                break
            continue

        if decision.outcome == "applied":
            counts["retained_now_actionable"] += 1
        else:
            retained_by_reason[decision.reason] += 1
        backfill(item, message.subject, message.sender)
        if counts["stopped_generation_changed"]:
            break

    report = {
        "mode": "apply" if apply else "preview",
        "pending_by_reason": dict(sorted(pending_by_reason.items())),
        "retained_by_reason": dict(sorted(retained_by_reason.items())),
    }
    for name in (
        "scanned", "would_ignore", "ignored", "retained_now_actionable", "unavailable",
        "unparseable", "fetch_failed", "skipped_changed", "headers_backfilled",
        "stopped_generation_changed",
    ):
        report[name] = counts[name]
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--apply", action="store_true", help="write changes (default: preview)")
    args = parser.parse_args(argv)
    try:
        client = boto3.client("dynamodb")
        report = recheck_reviews(
            owner_id=args.owner_id, apply=args.apply, client=client,
            connections=DynamoGmailRepository(
                client=client, users_table=os.environ["USERS_TABLE"],
                oauth_states_table=os.environ.get("OAUTH_STATES_TABLE", "unused"),
            ),
            cipher=CredentialCipher(boto3.client("kms"), os.environ["GMAIL_KMS_KEY_ARN"]),
            oauth=GoogleOAuthClient(
                boto3.client("secretsmanager"), os.environ["GMAIL_OAUTH_SECRET_NAME"], ""
            ),
            fetch=GmailFetchClient(),
            applications=DynamoApplicationRepository(
                client=client, applications_table=os.environ["APPLICATIONS_TABLE"],
                events_table=os.environ["EVENTS_TABLE"],
            ),
            events_table=os.environ["EVENTS_TABLE"],
            users_table=os.environ["USERS_TABLE"],
            now=datetime.now(UTC),
        )
    except Exception as error:  # noqa: BLE001 - never print messages that may hold mail data
        print(f"recheck failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
