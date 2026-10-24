from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import (
    get_current_subject,
    get_email_repository,
    get_message_content_service,
)
from app.domain.messages import MessageContent, MessagePage, MessageSummary
from app.repositories.dynamodb_email import DynamoEmailRepository, InvalidMessageCursor
from app.services.local_review_ai import (
    LocalAiMessageUnavailable,
    LocalAiStale,
    LocalAiUnavailable,
)
from app.services.message_content import MessageContentService, MessageNotFound

router = APIRouter(prefix="/messages", tags=["messages"])

Owner = Annotated[str, Depends(get_current_subject)]


@router.get("", response_model=MessagePage)
def list_messages(
    owner_id: Owner,
    emails: Annotated[DynamoEmailRepository, Depends(get_email_repository)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    cursor: str | None = None,
) -> MessagePage:
    try:
        items, next_cursor = emails.list_processed(owner_id, limit=limit, cursor=cursor)
    except InvalidMessageCursor:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from None
    return MessagePage(
        items=[
            MessageSummary(
                message_id=item["source_message_id"],
                outcome=item["outcome"],
                reason=item["reason"],
                received_at=item.get("received_at"),
            )
            for item in items
        ],
        next_cursor=next_cursor,
    )


@router.get("/{message_id}/content", response_model=MessageContent)
def message_content(
    message_id: str,
    owner_id: Owner,
    service: Annotated[MessageContentService, Depends(get_message_content_service)],
) -> MessageContent:
    try:
        return service.get(owner_id, message_id)
    except MessageNotFound:
        raise HTTPException(status_code=404, detail="Message not found") from None
    except LocalAiMessageUnavailable:
        raise HTTPException(status_code=503, detail="message_unavailable") from None
    except (LocalAiUnavailable, LocalAiStale):
        raise HTTPException(status_code=503, detail="gmail_connection_unavailable") from None
