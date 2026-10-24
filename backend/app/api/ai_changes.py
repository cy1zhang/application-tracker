from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.dependencies import (
    get_application_repository,
    get_current_subject,
    get_queue_email_repository,
)
from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb_email import (
    AlreadyUndone,
    DynamoEmailRepository,
    InvalidMessageCursor,
    NotReconsiderable,
)
from app.services.ai_undo import (
    AiChangeNotFound,
    AiUndoService,
    ChangedSince,
    NotUndoable,
    plain,
)

router = APIRouter(prefix="/ai-changes", tags=["ai-changes"])

Owner = Annotated[str, Depends(get_current_subject)]
Emails = Annotated[DynamoEmailRepository, Depends(get_queue_email_repository)]
Applications = Annotated[ApplicationRepository, Depends(get_application_repository)]


class AiChange(BaseModel):
    message_id: str
    received_at: datetime
    action: Literal["created", "updated", "ignored"]
    reason: str
    application_id: str | None
    changes: dict[str, Any]
    subject: str | None
    sender: str | None
    source_url: str | None
    undone_at: datetime | None


class AiChangePage(BaseModel):
    items: list[AiChange]
    next_cursor: str | None


def _as_change(item: dict[str, Any]) -> AiChange:
    reason = item["reason"]
    if item["outcome"] == "applied":
        action = "created" if item.get("event_type") == "created" else "updated"
    else:
        action = "ignored"
    thread_id = item.get("thread_id")
    return AiChange(
        message_id=item["source_message_id"],
        received_at=item["received_at"],
        action=action,
        reason=reason,
        application_id=item.get("application_id"),
        changes={
            field: {key: plain(value) for key, value in change.items()}
            for field, change in item.get("changes", {}).items()
        },
        subject=item.get("subject"),
        sender=item.get("sender"),
        source_url=(
            f"https://mail.google.com/mail/u/0/#all/{quote(thread_id, safe='')}"
            if thread_id else None
        ),
        undone_at=item.get("undone_at"),
    )


def get_ai_undo_service(emails: Emails, applications: Applications) -> AiUndoService:
    return AiUndoService(emails=emails, applications=applications)


Service = Annotated[AiUndoService, Depends(get_ai_undo_service)]


@router.get("", response_model=AiChangePage)
def list_changes(
    owner_id: Owner, emails: Emails,
    limit: Annotated[int, Query(ge=1, le=100)] = 50, cursor: str | None = None,
) -> AiChangePage:
    try:
        items, next_cursor = emails.list_ai_changes(owner_id, limit=limit, cursor=cursor)
    except InvalidMessageCursor:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None
    return AiChangePage(items=[_as_change(item) for item in items], next_cursor=next_cursor)


@router.post("/{message_id}/undo")
def undo(message_id: str, owner_id: Owner, service: Service) -> dict:
    try:
        service.undo(owner_id, message_id)
    except AiChangeNotFound:
        raise HTTPException(status_code=404, detail="Change not found") from None
    except NotUndoable:
        raise HTTPException(status_code=409, detail="not_undoable") from None
    except AlreadyUndone:
        raise HTTPException(status_code=409, detail="already_undone") from None
    except ChangedSince:
        raise HTTPException(status_code=409, detail="changed_since") from None
    return {"undone": True}


@router.post("/{message_id}/reconsider")
def reconsider(message_id: str, owner_id: Owner, service: Service):
    try:
        result = service.reconsider(owner_id, message_id)
    except AiChangeNotFound:
        raise HTTPException(status_code=404, detail="Change not found") from None
    except NotReconsiderable:
        raise HTTPException(status_code=409, detail="not_reconsiderable") from None
    if result.queued:
        return JSONResponse({"queued": True}, status_code=202)
    return {"action": result.action}
