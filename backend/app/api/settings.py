from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exception_handlers import (
    request_validation_exception_handler as default_validation_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    get_ai_available_providers,
    get_ai_settings_repository,
    get_credential_cipher,
    get_current_subject,
)
from app.domain.ai import AiProvider, AiSettingsResponse, AiSettingsUpdate
from app.repositories.dynamodb_ai import AiKeyRequired, DynamoAiSettingsRepository
from app.services.credential_crypto import AI_API_KEY, CredentialCipher, CredentialError

router = APIRouter(tags=["settings"])
Owner = Annotated[str, Depends(get_current_subject)]
Repository = Annotated[DynamoAiSettingsRepository, Depends(get_ai_settings_repository)]
Providers = Annotated[list[AiProvider], Depends(get_ai_available_providers)]


async def request_validation_error_handler(
    request: Request, error: RequestValidationError
) -> Response:
    if not request.url.path.startswith("/settings/ai"):
        return await default_validation_handler(request, error)
    # The body of /settings/ai carries an API key; never echo submitted values.
    detail = [
        {"type": item["type"], "loc": list(item["loc"]), "msg": item["msg"]}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def _response(
    repository: DynamoAiSettingsRepository, owner_id: str, providers: list[AiProvider]
) -> AiSettingsResponse:
    today = datetime.now(UTC).date().isoformat()
    view = repository.public_view(owner_id, today)
    return AiSettingsResponse(ai=view.model_copy(update={"available_providers": providers}))


@router.get("/settings", response_model=AiSettingsResponse)
def get_settings(
    owner_id: Owner, repository: Repository, providers: Providers
) -> AiSettingsResponse:
    return _response(repository, owner_id, providers)


@router.put("/settings/ai", response_model=AiSettingsResponse)
def put_ai_settings(
    body: AiSettingsUpdate,
    owner_id: Owner,
    repository: Repository,
    providers: Providers,
    cipher: Annotated[CredentialCipher, Depends(get_credential_cipher)],
) -> AiSettingsResponse:
    if body.provider not in providers:
        raise HTTPException(status_code=422, detail="Provider is not available")
    now = datetime.now(UTC)
    encrypted = hint = None
    if body.api_key is not None:
        key = body.api_key.get_secret_value()
        try:
            encrypted = cipher.encrypt(owner_id, AI_API_KEY, key)
        except CredentialError:
            raise HTTPException(status_code=503, detail="Could not store key") from None
        hint = key[-4:]
    try:
        repository.save(owner_id, body.provider, encrypted, hint, body.enabled, now)
    except AiKeyRequired:
        raise HTTPException(
            status_code=422, detail="An API key is required for this provider"
        ) from None
    return _response(repository, owner_id, providers)


@router.delete("/settings/ai", status_code=204)
def delete_ai_settings(owner_id: Owner, repository: Repository) -> Response:
    repository.delete(owner_id)
    return Response(status_code=204)
