from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import (
    get_application_repository,
    get_credential_cipher,
    get_current_subject,
    get_gmail_repository,
    get_google_oauth_client,
    get_review_repository,
)
from app.domain.reviews import (
    LocalAiSuggestionRequest,
    ReviewPage,
    ReviewResolution,
    ReviewResolutionResult,
)
from app.repositories.applications import ApplicationRepository
from app.repositories.dynamodb_reviews import (
    DynamoReviewRepository,
    InvalidReviewCursor,
    InvalidReviewResolution,
    ReviewAlreadyResolved,
    ReviewNotFound,
    ReviewVersionConflict,
)
from app.repositories.gmail import GmailRepository
from app.services.ai_extraction import InvalidExtraction
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
    LocalReviewAiService,
)

router = APIRouter(prefix="/reviews", tags=["reviews"])

Owner = Annotated[str, Depends(get_current_subject)]
Repository = Annotated[DynamoReviewRepository, Depends(get_review_repository)]


def get_local_review_ai_service(
    reviews: Repository,
    connections: Annotated[GmailRepository, Depends(get_gmail_repository)],
    applications: Annotated[ApplicationRepository, Depends(get_application_repository)],
    cipher: Annotated[CredentialCipher, Depends(get_credential_cipher)],
    oauth: Annotated[GoogleOAuthClient, Depends(get_google_oauth_client)],
) -> LocalReviewAiService:
    return LocalReviewAiService(
        reviews=reviews, connections=connections, applications=applications,
        cipher=cipher, oauth=oauth, fetch=GmailFetchClient(),
    )


LocalService = Annotated[LocalReviewAiService, Depends(get_local_review_ai_service)]


@router.get("", response_model=ReviewPage)
def list_reviews(
    owner_id: Owner,
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ReviewPage:
    try:
        return repository.list_pending(owner_id=owner_id, limit=limit, cursor=cursor)
    except InvalidReviewCursor as error:
        raise HTTPException(status_code=422, detail="Invalid pagination cursor") from error


@router.get("/{message_id}/local-ai-input")
def local_ai_input(message_id: str, owner_id: Owner, service: LocalService):
    try:
        return service.prepare(owner_id, message_id)
    except LocalAiNotFound:
        raise HTTPException(status_code=404, detail="Review not found") from None
    except LocalAiConnectionChanged:
        raise HTTPException(status_code=409, detail="gmail_connection_changed") from None
    except (LocalAiIneligible, LocalAiStale):
        raise HTTPException(status_code=409, detail="review_changed") from None
    except LocalAiMessageUnavailable:
        raise HTTPException(status_code=503, detail="message_unavailable") from None
    except LocalAiUnavailable:
        raise HTTPException(status_code=503, detail="gmail_connection_unavailable") from None


@router.post("/{message_id}/local-ai-suggestion")
def local_ai_suggestion(
    message_id: str, payload: LocalAiSuggestionRequest, owner_id: Owner,
    service: LocalService,
):
    try:
        return service.suggest(
            owner_id, message_id, generation=payload.generation,
            model=payload.model, raw=payload.extraction,
        )
    except (LocalAiNotFound, ReviewNotFound):
        raise HTTPException(status_code=404, detail="Review not found") from None
    except LocalAiConnectionChanged:
        raise HTTPException(status_code=409, detail="gmail_connection_changed") from None
    except (LocalAiIneligible, LocalAiStale, ReviewAlreadyResolved, ReviewVersionConflict):
        raise HTTPException(status_code=409, detail="review_changed") from None
    except LocalAiMessageUnavailable:
        raise HTTPException(status_code=503, detail="message_unavailable") from None
    except LocalAiUnavailable:
        raise HTTPException(status_code=503, detail="gmail_connection_unavailable") from None
    except InvalidExtraction:
        raise HTTPException(status_code=422, detail="Invalid model output") from None


@router.post("/{message_id}/resolve", response_model=ReviewResolutionResult)
def resolve_review(
    message_id: str,
    payload: ReviewResolution,
    owner_id: Owner,
    repository: Repository,
) -> ReviewResolutionResult:
    try:
        return repository.resolve(
            owner_id=owner_id,
            message_id=message_id,
            resolution=payload,
            occurred_at=datetime.now(UTC),
            new_application_id=uuid4().hex if payload.action == "create" else None,
        )
    except ReviewNotFound as error:
        raise HTTPException(status_code=404, detail="Review or application not found") from error
    except (ReviewAlreadyResolved, ReviewVersionConflict) as error:
        raise HTTPException(status_code=409, detail="Review or application changed") from error
    except InvalidReviewResolution as error:
        raise HTTPException(status_code=422, detail="Invalid review resolution") from error
