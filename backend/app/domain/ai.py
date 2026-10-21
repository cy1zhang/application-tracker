import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.domain.applications import ApplicationStatus, Name, RoleType, Season
from app.domain.gmail import EncryptedCredential

Quote = Annotated[str, StringConstraints(max_length=1000)]
RequisitionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class AiProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    HUGGING_FACE = "huggingface"


class ExtractionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Quote | None
    role: Quote | None
    employment_type: Quote | None
    season: Quote | None
    year: Quote | None
    status: Quote | None
    requisition_id: Quote | None


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Name | None
    role: Name | None
    employment_type: RoleType | None
    season: Season | None
    year: int | None = Field(ge=2000, le=2100, strict=True)
    status: ApplicationStatus | None
    requisition_id: RequisitionId | None
    evidence: ExtractionEvidence

    @field_validator("employment_type")
    @classmethod
    def _known_employment_type(cls, value: RoleType | None) -> RoleType | None:
        if value is RoleType.UNKNOWN:
            raise ValueError("employment_type cannot be unknown")
        return value


def _nullable(schema: dict) -> dict:
    return {**schema, "type": [schema["type"], "null"]}


_STRING = {"type": "string"}
_FIELDS = {
    "company": _STRING,
    "role": _STRING,
    "employment_type": {
        "type": "string",
        "enum": [r.value for r in RoleType if r is not RoleType.UNKNOWN] + [None],
    },
    "season": {"type": "string", "enum": [s.value for s in Season] + [None]},
    "year": {"type": "integer", "minimum": 2000, "maximum": 2100},
    "status": {
        "type": "string",
        "enum": [s.value for s in ApplicationStatus] + [None],
    },
    "requisition_id": _STRING,
}

EXTRACTION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        **{name: _nullable(schema) for name, schema in _FIELDS.items()},
        "evidence": {
            "type": "object",
            "properties": {name: _nullable(_STRING) for name in _FIELDS},
            "required": list(_FIELDS),
            "additionalProperties": False,
        },
    },
    "required": [*_FIELDS, "evidence"],
    "additionalProperties": False,
}


AI_DAILY_LIMIT = 50
AI_KEY_PATTERN = r"[\x21-\x7e]{16,400}"


class AiSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiProvider
    api_key: SecretStr | None = None
    enabled: bool
    consent: bool = False

    @field_validator("api_key")
    @classmethod
    def _printable_ascii_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and re.fullmatch(AI_KEY_PATTERN, value.get_secret_value()) is None:
            raise ValueError("api_key must be 16-400 printable ASCII characters")
        return value

    @model_validator(mode="after")
    def _consent_for_key(self) -> "AiSettingsUpdate":
        if self.api_key is not None and not self.consent:
            raise ValueError("consent is required when an api_key is supplied")
        return self


class AiSettingsView(BaseModel):
    available_providers: list[AiProvider] = []
    hugging_face_host: str | None = None
    provider: AiProvider | None
    key_hint: str | None
    enabled: bool
    key_status: Literal["ok", "invalid"] | None
    requests_today: int
    daily_limit: int
    usage_day: str


class AiSettingsResponse(BaseModel):
    ai: AiSettingsView


@dataclass(frozen=True)
class StoredAiSettings:
    provider: AiProvider
    encrypted_key: EncryptedCredential
    key_hint: str
    enabled: bool
    key_status: str
    version: int
    consent_at: datetime | None


@dataclass(frozen=True)
class AiRuntime:
    provider: AiProvider
    model: str
    settings_version: int
    encrypted_key: EncryptedCredential
