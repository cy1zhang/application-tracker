import builtins
from datetime import datetime
from typing import Protocol

from app.domain.applications import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationPage,
    ApplicationPatch,
)


class ApplicationNotFound(Exception):
    pass


class VersionConflict(Exception):
    pass


class InvalidCursor(Exception):
    pass


class ApplicationRepository(Protocol):
    def create(
        self,
        *,
        owner_id: str,
        payload: ApplicationCreate,
        application_id: str,
        event_id: str,
        occurred_at: datetime,
    ) -> Application: ...

    def list(
        self,
        *,
        owner_id: str,
        limit: int,
        cursor: str | None,
    ) -> ApplicationPage: ...

    def get(self, *, owner_id: str, application_id: str) -> Application | None: ...

    def list_events(
        self,
        *,
        owner_id: str,
        application_id: str,
    ) -> builtins.list[ApplicationEvent]: ...

    def update(
        self,
        *,
        owner_id: str,
        application_id: str,
        patch: ApplicationPatch,
        event_id: str,
        occurred_at: datetime,
    ) -> Application: ...

    def delete(
        self,
        *,
        owner_id: str,
        application_id: str,
        expected_version: int,
        event_id: str,
        occurred_at: datetime,
    ) -> None: ...
