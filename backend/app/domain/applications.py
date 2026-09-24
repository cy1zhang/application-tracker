from datetime import date, datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
FieldValue = str | int | None


class RoleType(str, Enum):
    INTERNSHIP = "internship"
    CO_OP = "co_op"
    FULL_TIME_NEW_GRAD = "full_time_new_grad"
    UNKNOWN = "unknown"


class Season(str, Enum):
    SUMMER = "summer"
    FALL = "fall"
    SPRING = "spring"
    WINTER = "winter"


class ApplicationStatus(str, Enum):
    APPLIED = "applied"
    OA_RECEIVED = "oa_received"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class EventOrigin(str, Enum):
    USER = "user"
    RULES = "rules"
    AI = "ai"


class EventType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Name
    role: Name
    role_type: RoleType = RoleType.UNKNOWN
    season: Season | None = None
    year: int | None = None
    status: ApplicationStatus
    application_date: date | None = None


class ApplicationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    company: Name | None = None
    role: Name | None = None
    role_type: RoleType | None = None
    season: Season | None = None
    year: int | None = None
    status: ApplicationStatus | None = None
    application_date: date | None = None

    def updates(self) -> dict[str, FieldValue | date]:
        return {
            field: getattr(self, field)
            for field in self.model_fields_set
            if field != "expected_version"
        }

    @model_validator(mode="after")
    def require_change(self) -> "ApplicationPatch":
        if not self.updates():
            raise ValueError("at least one application field is required")
        return self


class Application(BaseModel):
    application_id: str
    company: str | None
    role: str | None
    role_type: RoleType | None
    season: Season | None
    year: int | None
    status: ApplicationStatus | None
    application_date: date | None
    created_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)


class ApplicationPage(BaseModel):
    items: list[Application]
    next_cursor: str | None


class FieldChange(BaseModel):
    before: FieldValue
    after: FieldValue


class ApplicationEvent(BaseModel):
    event_id: str
    application_id: str
    event_type: EventType
    origin: EventOrigin
    occurred_at: datetime
    changes: dict[str, FieldChange]
