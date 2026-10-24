import contextlib
import json
import os
from typing import Annotated, Any
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    get_application_repository,
    get_auto_apply_settings,
    get_current_subject,
    get_queue_email_repository,
    get_sync_publisher,
)
from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.services.plan_report import simulate
from app.services.sync_candidates import load_candidates

router = APIRouter(prefix="/ai-plan", tags=["ai-plan"])

Owner = Annotated[str, Depends(get_current_subject)]
Emails = Annotated[DynamoEmailRepository, Depends(get_queue_email_repository)]
Settings = Annotated[DynamoAutoApplySettings, Depends(get_auto_apply_settings)]
Applications = Annotated[ApplicationRepository, Depends(get_application_repository)]
Publisher = Annotated[Any, Depends(get_sync_publisher)]


def _enum(value):
    return value.value if value is not None else None


@router.get("/report")
def report(owner_id: Owner, emails: Emails, applications: Applications) -> dict:
    planned = emails.list_planned(owner_id)
    result = simulate(planned, load_candidates(owner_id, applications))
    by_id = {item.message_id: item for item in planned}
    sample = []
    for line in result.sample:
        source = by_id[line.message_id]
        sample.append({
            "message_id": line.message_id,
            "action": line.action,
            "reason": line.reason,
            "company": line.company,
            "role": line.role,
            "status": _enum(line.status),
            "season": _enum(line.season),
            "year": line.year,
            "target": line.target,
            "subject": source.subject,
            "sender": source.sender,
            "source_url": (
                f"https://mail.google.com/mail/u/0/#all/{quote(source.thread_id, safe='')}"
                if source.thread_id else None
            ),
        })
    return {
        "analyzed": len(planned),
        "waiting": len(emails.list_ai_queue(owner_id)),
        "report": {"counts": result.counts, "reasons": result.reasons, "sample": sample},
    }


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
def apply(
    owner_id: Owner, emails: Emails, settings: Settings, publisher: Publisher,
) -> dict:
    queue_url = os.getenv("SYNC_QUEUE_URL")
    if not queue_url:
        raise HTTPException(status_code=503, detail="Sync worker is unavailable")
    previous = settings.get_mode(owner_id)
    if previous == "off":
        raise HTTPException(status_code=409, detail="auto_apply_off")
    if not emails.list_planned(owner_id):
        raise HTTPException(status_code=409, detail="nothing_planned")
    if emails.list_ai_queue(owner_id):
        raise HTTPException(status_code=409, detail="analysis_incomplete")
    settings.set_mode(owner_id, "on")
    try:
        publisher.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"mode": "apply_plan", "owner_id": owner_id}),
        )
    except (BotoCoreError, ClientError):
        with contextlib.suppress(Exception):  # best effort; the 503 is still returned
            settings.set_mode(owner_id, previous)
        raise HTTPException(status_code=503, detail="queue_unavailable") from None
    return {"queued": True}
