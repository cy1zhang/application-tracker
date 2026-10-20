import json
import os
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import (
    get_current_subject,
    get_sync_publisher,
    get_sync_repository,
)
from app.domain.sync import SyncRun
from app.repositories.dynamodb_sync import DynamoSyncRepository, SyncUnavailable

router = APIRouter(prefix="/syncs", tags=["syncs"])
Owner = Annotated[str, Depends(get_current_subject)]
Repository = Annotated[DynamoSyncRepository, Depends(get_sync_repository)]
Publisher = Annotated[Any, Depends(get_sync_publisher)]


@router.post("", response_model=SyncRun, status_code=status.HTTP_202_ACCEPTED)
def start_sync(owner_id: Owner, repository: Repository, publisher: Publisher) -> SyncRun:
    try:
        run = repository.start(owner_id, uuid4().hex, datetime.now(UTC))
    except SyncUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    try:
        publisher.send_message(
            QueueUrl=os.environ["SYNC_QUEUE_URL"],
            MessageBody=json.dumps(
                {"owner_id": owner_id, "run_id": run.run_id, "generation": run.generation}
            ),
        )
    except ClientError:
        # The pending run is durable; the recovery pass will enqueue it again.
        pass
    return run


@router.get("/latest", response_model=SyncRun)
def latest_sync(owner_id: Owner, repository: Repository) -> SyncRun:
    run = repository.get_latest(owner_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Sync run not found")
    return run
