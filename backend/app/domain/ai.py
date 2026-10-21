from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.applications import ApplicationStatus, Name, RoleType, Season

Quote = Annotated[str, StringConstraints(max_length=1000)]
RequisitionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class AiProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


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
