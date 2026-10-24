import os
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exception_handlers import (
    request_validation_exception_handler as default_validation_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    get_ai_available_providers,
    get_ai_settings_repository,
    get_auto_apply_settings,
    get_credential_cipher,
    get_current_subject,
)
from app.domain.account import AccountDeleting
from app.domain.ai import AiProvider, AiSettingsResponse, AiSettingsUpdate
from app.repositories.dynamodb_ai import AiKeyRequired, DynamoAiSettingsRepository
from app.repositories.dynamodb_auto_apply import (
    AutoApplyMode,
    DynamoAutoApplySettings,
)
from app.services.credential_crypto import AI_API_KEY, CredentialCipher, CredentialError

router = APIRouter(tags=["settings"])
Owner = Annotated[str, Depends(get_current_subject)]
Repository = Annotated[DynamoAiSettingsRepository, Depends(get_ai_settings_repository)]
Providers = Annotated[list[AiProvider], Depends(get_ai_available_providers)]
AutoApply = Annotated[DynamoAutoApplySettings, Depends(get_auto_apply_settings)]


class AutoApplyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "plan"]


class SettingsResponse(AiSettingsResponse):
    auto_apply_mode: AutoApplyMode


async def request_validation_error_handler(
    request: Request, error: RequestValidationError
) -> Response:
    path = request.url.path
    if not (
        path.startswith("/settings/")
        or (path.startswith("/reviews/") and path.endswith("/local-ai-suggestion"))
        or (path.startswith("/ai-queue/") and path.endswith("/result"))
    ):
        return await default_validation_handler(request, error)
    # Key-bearing and model-output bodies may contain private text; never echo them.
    detail = [
        {"type": item["type"], "loc": list(item["loc"]), "msg": item["msg"]}
        for item in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


def _response(
    repository: DynamoAiSettingsRepository,
    owner_id: str,
    providers: list[AiProvider],
    auto_apply: DynamoAutoApplySettings,
) -> SettingsResponse:
    today = datetime.now(UTC).date().isoformat()
    view = repository.public_view(owner_id, today)
    model = os.getenv("AI_MODEL_HUGGING_FACE", "")
    host = model.rpartition(":")[2] if AiProvider.HUGGING_FACE in providers else None
    return SettingsResponse(
        ai=view.model_copy(update={
            "available_providers": providers,
            "hugging_face_host": host or None,
        }),
        auto_apply_mode=auto_apply.get_mode(owner_id),
    )


@router.get("/settings", response_model=SettingsResponse)
def get_settings(
    owner_id: Owner, repository: Repository, providers: Providers, auto_apply: AutoApply
) -> SettingsResponse:
    return _response(repository, owner_id, providers, auto_apply)


@router.put("/settings/ai", response_model=SettingsResponse)
def put_ai_settings(
    body: AiSettingsUpdate,
    owner_id: Owner,
    repository: Repository,
    providers: Providers,
    auto_apply: AutoApply,
    cipher: Annotated[CredentialCipher, Depends(get_credential_cipher)],
) -> SettingsResponse:
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
    except AccountDeleting:
        raise HTTPException(status_code=423, detail="account_deleting") from None
    except AiKeyRequired:
        raise HTTPException(
            status_code=422, detail="An API key is required for this provider"
        ) from None
    return _response(repository, owner_id, providers, auto_apply)


@router.put("/settings/auto-apply", response_model=SettingsResponse)
def put_auto_apply(
    body: AutoApplyUpdate,
    owner_id: Owner,
    repository: Repository,
    providers: Providers,
    auto_apply: AutoApply,
) -> SettingsResponse:
    try:
        auto_apply.set_mode(owner_id, body.mode)
    except AccountDeleting:
        raise HTTPException(status_code=423, detail="account_deleting") from None
    return _response(repository, owner_id, providers, auto_apply)


@router.delete("/settings/ai", status_code=204)
def delete_ai_settings(owner_id: Owner, repository: Repository) -> Response:
    try:
        repository.delete(owner_id)
    except AccountDeleting:
        raise HTTPException(status_code=423, detail="account_deleting") from None
    return Response(status_code=204)
