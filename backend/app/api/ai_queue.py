import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import (
    get_application_repository,
    get_auto_apply_settings,
    get_credential_cipher,
    get_current_subject,
    get_gmail_repository,
    get_google_oauth_client,
    get_queue_email_repository,
    get_sync_publisher,
)
from app.domain.reviews import LocalAiSuggestionRequest
from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb_auto_apply import DynamoAutoApplySettings
from app.repositories.dynamodb_email import DynamoEmailRepository
from app.repositories.gmail import GmailRepository
from app.services.ai_extraction import InvalidExtraction
from app.services.ai_queue import AiQueueService, AutoApplyOff, QueueUnavailable
from app.services.credential_crypto import CredentialCipher
from app.services.gmail_fetch import GmailFetchClient
from app.services.google_oauth import GoogleOAuthClient
from app.services.local_review_ai import (
    LocalAiConnectionChanged,
    LocalAiIneligible,
    LocalAiMessageUnavailable,
    LocalAiNotFound,
    LocalAiStale,
    LocalAiUnavailable,
)

router = APIRouter(prefix="/ai-queue", tags=["ai-queue"])

Owner = Annotated[str, Depends(get_current_subject)]


def get_ai_queue_service(
    emails: Annotated[DynamoEmailRepository, Depends(get_queue_email_repository)],
    settings: Annotated[DynamoAutoApplySettings, Depends(get_auto_apply_settings)],
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    connections: Annotated[GmailRepository, Depends(get_gmail_repository)],
    cipher: Annotated[CredentialCipher, Depends(get_credential_cipher)],
    oauth: Annotated[GoogleOAuthClient, Depends(get_google_oauth_client)],
    publisher: Annotated[Any, Depends(get_sync_publisher)],
) -> AiQueueService:
    return AiQueueService(
        emails=emails, settings=settings, applications=applications,
        connections=connections, cipher=cipher, oauth=oauth, fetch=GmailFetchClient(),
        publisher=publisher, queue_url=os.getenv("SYNC_QUEUE_URL"),
    )


Service = Annotated[AiQueueService, Depends(get_ai_queue_service)]


def _translate(call):
    try:
        return call()
    except AutoApplyOff:
        raise HTTPException(status_code=409, detail="auto_apply_off") from None
    except QueueUnavailable:
        raise HTTPException(status_code=503, detail="queue_unavailable") from None
    except LocalAiNotFound:
        raise HTTPException(status_code=404, detail="Message not found") from None
    except LocalAiConnectionChanged:
        raise HTTPException(status_code=409, detail="gmail_connection_changed") from None
    except (LocalAiIneligible, LocalAiStale):
        raise HTTPException(status_code=409, detail="review_changed") from None
    except LocalAiMessageUnavailable:
        raise HTTPException(status_code=503, detail="message_unavailable") from None
    except LocalAiUnavailable:
        raise HTTPException(status_code=503, detail="gmail_connection_unavailable") from None
    except InvalidExtraction:
        raise HTTPException(status_code=422, detail="Invalid model output") from None


@router.post("/backfill")
def backfill(owner_id: Owner, service: Service) -> dict:
    return {"marked": _translate(lambda: service.backfill(owner_id))}


@router.get("")
def list_queue(
    owner_id: Owner, service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict:
    page = _translate(lambda: service.list(owner_id, limit))
    return {"items": page.items, "remaining": page.remaining}


@router.get("/{message_id}/input")
def queue_input(message_id: str, owner_id: Owner, service: Service):
    return _translate(lambda: service.prepare(owner_id, message_id))


@router.post("/{message_id}/result")
def queue_result(
    message_id: str, payload: LocalAiSuggestionRequest, owner_id: Owner, service: Service,
) -> dict:
    result = _translate(lambda: service.submit(
        owner_id, message_id, generation=payload.generation,
        model=payload.model, raw=payload.extraction,
    ))
    body = {"planned": result.planned, "executed": result.executed, "action": result.action}
    if result.deferred:
        body["deferred"] = True
    return body
