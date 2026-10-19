from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.applications import (
    ApplicationPatch,
    ApplicationStatus,
    Name,
    RoleType,
    Season,
)


class ReviewApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Name
    role: Name
    role_type: RoleType = RoleType.UNKNOWN
    season: Season | None = None
    year: int | None = None
    status: ApplicationStatus | None = None
    application_date: date | None = None


class ReviewResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["accept", "edit", "reject", "attach", "create"]
    expected_version: int | None = Field(default=None, ge=1)
    application_id: str | None = None
    patch: ApplicationPatch | None = None
    application: ReviewApplicationCreate | None = None

    @model_validator(mode="after")
    def require_action_fields(self) -> "ReviewResolution":
        if self.action == "accept" and self.expected_version is None:
            raise ValueError("accept requires expected_version")
        if self.action == "edit" and (not self.application_id or self.patch is None):
            raise ValueError("edit requires application_id and patch")
        if self.action == "attach" and (not self.application_id or self.expected_version is None):
            raise ValueError("attach requires application_id and expected_version")
        if self.action == "create" and self.application is None:
            raise ValueError("create requires application")
        return self


class ReviewItem(BaseModel):
    message_id: str
    reason: str
    evidence: str = ""
    received_at: datetime | None = None
    thread_id: str | None = None
    source_url: str | None = None
    application_id: str | None = None
    proposed_status: ApplicationStatus | None = None
    proposed_season: Season | None = None
    proposed_year: int | None = None


class ReviewPage(BaseModel):
    items: list[ReviewItem]
    next_cursor: str | None


class ReviewResolutionResult(BaseModel):
    message_id: str
    action: Literal["accept", "edit", "reject", "attach", "create"]
    application_id: str | None = None
