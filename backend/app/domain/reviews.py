from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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
    expected_review_version: int | None = Field(default=None, ge=0)
    application_id: str | None = None
    patch: ApplicationPatch | None = None
    application: ReviewApplicationCreate | None = None

    @model_validator(mode="after")
    def require_action_fields(self) -> "ReviewResolution":
        if self.action == "accept" and (
            self.expected_version is None or self.expected_review_version is None
        ):
            raise ValueError("accept requires expected_version and expected_review_version")
        if self.action == "edit" and (not self.application_id or self.patch is None):
            raise ValueError("edit requires application_id and patch")
        if self.action == "attach" and (not self.application_id or self.expected_version is None):
            raise ValueError("attach requires application_id and expected_version")
        if self.action == "create" and self.application is None:
            raise ValueError("create requires application")
        return self


class AiProposalView(BaseModel):
    company: str | None = None
    role: str | None = None
    employment_type: RoleType | None = None
    season: Season | None = None
    year: int | None = None
    status: ApplicationStatus | None = None
    requisition_id: str | None = None
    evidence: dict[str, str] = {}


class ReviewItem(BaseModel):
    message_id: str
    review_version: int = 0
    reason: str
    subject: str | None = None
    sender: str | None = None
    evidence: str = ""
    received_at: datetime | None = None
    thread_id: str | None = None
    source_url: str | None = None
    application_id: str | None = None
    proposed_status: ApplicationStatus | None = None
    proposed_season: Season | None = None
    proposed_year: int | None = None
    ai_proposal: AiProposalView | None = None
    rule_reason: str | None = None
    ai_note: str | None = None
    ai_provider: str | None = None


class ReviewPage(BaseModel):
    items: list[ReviewItem]
    next_cursor: str | None


class ReviewResolutionResult(BaseModel):
    message_id: str
    action: Literal["accept", "edit", "reject", "attach", "create"]
    application_id: str | None = None


class LocalAiSuggestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: int = Field(ge=0)
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    extraction: dict[str, Any]
