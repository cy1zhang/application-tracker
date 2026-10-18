import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse

from app.api.dependencies import (
    get_credential_cipher,
    get_current_identity,
    get_gmail_repository,
    get_google_oauth_client,
)
from app.domain.gmail import GmailConnectionStatus, TrackerIdentity
from app.repositories.gmail import GmailRepository
from app.services.credential_crypto import CredentialCipher
from app.services.gmail_connection import GmailConnectionError, GmailConnectionService
from app.services.google_oauth import GoogleOAuthClient

router = APIRouter(tags=["gmail"])

Identity = Annotated[TrackerIdentity, Depends(get_current_identity)]


def get_gmail_service(
    repository: Annotated[GmailRepository, Depends(get_gmail_repository)],
    cipher: Annotated[CredentialCipher, Depends(get_credential_cipher)],
    google: Annotated[GoogleOAuthClient, Depends(get_google_oauth_client)],
) -> GmailConnectionService:
    return GmailConnectionService(repository, cipher, google)


Service = Annotated[GmailConnectionService, Depends(get_gmail_service)]


@router.get("/gmail/connection", response_model=GmailConnectionStatus)
def connection_status(identity: Identity, service: Service) -> GmailConnectionStatus:
    return service.status(identity)


@router.post("/gmail/connect")
def connect(identity: Identity, service: Service) -> dict[str, str]:
    try:
        return {"authorization_url": service.connect(identity)}
    except GmailConnectionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@router.get("/oauth/google/callback")
def callback(
    service: Service,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    result = "connected" if service.callback(state, code, error) else "failed"
    return RedirectResponse(
        url=f"{os.environ['FRONTEND_URL'].rstrip('/')}/?gmail={result}",
        status_code=303,
    )


@router.delete("/gmail/connection", status_code=204)
def disconnect(identity: Identity, service: Service) -> Response:
    service.disconnect(identity)
    return Response(status_code=204)
