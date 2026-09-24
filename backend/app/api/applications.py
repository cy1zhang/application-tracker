from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import get_application_repository, get_current_subject
from app.domain.applications import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationPage,
    ApplicationPatch,
)
from app.repositories.applications import (
    ApplicationNotFound,
    ApplicationRepository,
    InvalidCursor,
    VersionConflict,
)

router = APIRouter(prefix="/applications", tags=["applications"])

Owner = Annotated[str, Depends(get_current_subject)]
Repository = Annotated[ApplicationRepository, Depends(get_application_repository)]


@router.post("", response_model=Application, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationCreate,
    owner_id: Owner,
    repository: Repository,
) -> Application:
    return repository.create(
        owner_id=owner_id,
        payload=payload,
        application_id=uuid4().hex,
        event_id=uuid4().hex,
        occurred_at=datetime.now(UTC),
    )


@router.get("", response_model=ApplicationPage)
def list_applications(
    owner_id: Owner,
    repository: Repository,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> ApplicationPage:
    try:
        return repository.list(owner_id=owner_id, limit=limit, cursor=cursor)
    except InvalidCursor as error:
        raise HTTPException(
            status_code=422,
            detail="Invalid pagination cursor",
        ) from error


@router.get("/{application_id}", response_model=Application)
def get_application(
    application_id: str,
    owner_id: Owner,
    repository: Repository,
) -> Application:
    application = repository.get(
        owner_id=owner_id,
        application_id=application_id,
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.get("/{application_id}/events", response_model=list[ApplicationEvent])
def list_application_events(
    application_id: str,
    owner_id: Owner,
    repository: Repository,
) -> list[ApplicationEvent]:
    application = repository.get(
        owner_id=owner_id,
        application_id=application_id,
    )
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return repository.list_events(
        owner_id=owner_id,
        application_id=application_id,
    )


@router.patch("/{application_id}", response_model=Application)
def update_application(
    application_id: str,
    payload: ApplicationPatch,
    owner_id: Owner,
    repository: Repository,
) -> Application:
    try:
        return repository.update(
            owner_id=owner_id,
            application_id=application_id,
            patch=payload,
            event_id=uuid4().hex,
            occurred_at=datetime.now(UTC),
        )
    except ApplicationNotFound as error:
        raise HTTPException(status_code=404, detail="Application not found") from error
    except VersionConflict as error:
        raise HTTPException(
            status_code=409,
            detail="Application version is stale",
        ) from error


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_application(
    application_id: str,
    owner_id: Owner,
    repository: Repository,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    try:
        repository.delete(
            owner_id=owner_id,
            application_id=application_id,
            expected_version=expected_version,
            event_id=uuid4().hex,
            occurred_at=datetime.now(UTC),
        )
    except ApplicationNotFound as error:
        raise HTTPException(status_code=404, detail="Application not found") from error
    except VersionConflict as error:
        raise HTTPException(
            status_code=409,
            detail="Application version is stale",
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
