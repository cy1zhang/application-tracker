from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_current_subject, get_review_repository
from app.domain.reviews import ReviewPage, ReviewResolution, ReviewResolutionResult
from app.repositories.dynamodb_reviews import (
    DynamoReviewRepository,
    InvalidReviewCursor,
    InvalidReviewResolution,
    ReviewAlreadyResolved,
    ReviewNotFound,
    ReviewVersionConflict,
)

router = APIRouter(prefix="/reviews", tags=["reviews"])

Owner = Annotated[str, Depends(get_current_subject)]
Repository = Annotated[DynamoReviewRepository, Depends(get_review_repository)]


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
