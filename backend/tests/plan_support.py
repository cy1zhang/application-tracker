"""Synthetic helpers that store unexecuted AI plans directly in the Events table."""

from datetime import UTC, datetime, timedelta

from boto3.dynamodb.types import TypeSerializer

T0 = datetime(2026, 10, 1, tzinfo=UTC)
_SERIALIZER = TypeSerializer()


def put_plan(client, owner, message_id, minutes, *, company="Acme",
             status="applied", kind="application_update", thread=None,
             role="Software Engineer Intern", extra=None, plan_extra=None,
             outcome="pending_ai", subject="Update"):
    plan = {
        "company": company, "role": role, "employment_type": "internship",
        "season": "summer", "year": 2027, "status": status,
        "requisition_id": None, "evidence": {}, "status_in_subject_only": False,
        "kind": kind, "model": "m", "generation": 1,
        "planned_at": T0.isoformat(), **(plan_extra or {}),
    }
    item = {
        "owner_id": owner, "event_id": f"gmail#{message_id}",
        "source_message_id": message_id,
        "thread_id": thread or f"thread-{message_id}",
        "received_at": (T0 + timedelta(minutes=minutes)).isoformat(),
        "outcome": outcome, "reason": "awaiting_local_ai",
        "subject": subject, "sender": "jobs@acme.test", "ai_plan": plan,
        **(extra or {}),
    }
    client.put_item(
        TableName="Events",
        Item={k: _SERIALIZER.serialize(v) for k, v in item.items()},
    )
