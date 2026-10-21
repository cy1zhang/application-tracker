import json
import os
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import (
    get_account_repository,
    get_cognito_username,
    get_current_subject,
    get_sync_publisher,
)
from app.domain.account import AccountState
from app.observability import category_of, get_logger, log_event
from app.repositories.dynamodb_account import DynamoAccountRepository

logger = get_logger(__name__)
router = APIRouter(prefix="/account", tags=["account"])
Owner = Annotated[str, Depends(get_current_subject)]
Username = Annotated[str | None, Depends(get_cognito_username)]
Accounts = Annotated[DynamoAccountRepository, Depends(get_account_repository)]
Publisher = Annotated[Any, Depends(get_sync_publisher)]


class DeleteAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal["DELETE"]


class AccountStateResponse(BaseModel):
    state: AccountState


@router.get("", response_model=AccountStateResponse)
def get_account(owner_id: Owner, accounts: Accounts) -> AccountStateResponse:
    return AccountStateResponse(state=accounts.get_status(owner_id).state)


@router.delete("", response_model=AccountStateResponse, status_code=202)
def delete_account(
    body: DeleteAccountRequest,
    response: Response,
    owner_id: Owner,
    username: Username,
    accounts: Accounts,
    publisher: Publisher,
) -> AccountStateResponse:
    queue_url = os.getenv("DELETION_QUEUE_URL")
    if not queue_url:
        raise HTTPException(status_code=503, detail="Deletion worker is unavailable")
    status = accounts.begin_deletion(owner_id, username, datetime.now(UTC))
    if status.state == AccountState.DELETED:
        response.status_code = 200
        return AccountStateResponse(state=AccountState.DELETED)
    try:
        publisher.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"kind": "delete_account", "owner_id": owner_id}),
            DelaySeconds=30,
        )
    except (BotoCoreError, ClientError) as error:
        # The deleting state is durable; the recovery pass re-publishes the job.
        log_event(logger, "warning", "deletion_enqueue_failed", category=category_of(error))
    return AccountStateResponse(state=AccountState.DELETING)
