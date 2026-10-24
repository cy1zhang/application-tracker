"""Answer-key label schema, loader, and validator CLI.

Prints only counts, never company names or roles.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.domain.applications import ApplicationStatus, RoleType, Season

Kind = Literal[
    "application_update",
    "job_alert",
    "recruiter_outreach",
    "newsletter_or_event",
    "other",
]


class LabelError(ValueError):
    pass


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Kind
    company: str | None
    role: str | None
    role_type: RoleType | None
    season: Season | None
    year: int | None = Field(ge=2000, le=2100, strict=True)
    status: ApplicationStatus | None
    requisition_id: str | None
    application_key: str | None = None

    @field_validator("role_type")
    @classmethod
    def _no_unknown(cls, value: RoleType | None) -> RoleType | None:
        if value is RoleType.UNKNOWN:
            raise ValueError("role_type must be null, not 'unknown'")
        return value


def _describe(error: ValidationError) -> str:
    # Omit input values so company names and roles never reach the output.
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'label'}: {e['msg']}"
        for e in error.errors()
    )


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise LabelError(f"{path.name}: {what} not found") from None
    except json.JSONDecodeError as error:
        raise LabelError(f"{path.name}:{error.lineno}: invalid JSON") from None


def load_labels(directory: Path) -> dict[str, Label]:
    directory = Path(directory)
    index = _read_json(directory / "index.json", "index")
    labels: dict[str, Label] = {}
    for path in sorted((directory / "labels").glob("batch-*.jsonl")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            where = f"{path.name}:{number}"
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                raise LabelError(f"{where}: invalid JSON") from None
            if isinstance(data, dict) and "application_key" in data:
                raise LabelError(f"{where}: application_key is not allowed in batches")
            try:
                label = Label(**data) if isinstance(data, dict) else None
            except ValidationError as error:
                raise LabelError(f"{where}: {_describe(error)}") from None
            if label is None:
                raise LabelError(f"{where}: expected a JSON object")
            if label.id not in index:
                raise LabelError(f"{where}: id {label.id} is not in index.json")
            if label.id in labels:
                raise LabelError(f"{where}: duplicate id {label.id}")
            labels[label.id] = label
    groups_path = directory / "groups.json"
    if groups_path.exists():
        groups = _read_json(groups_path, "groups")
        if not isinstance(groups, dict):
            raise LabelError("groups.json: expected an object")
        for label_id, key in groups.items():
            label = labels.get(label_id)
            if label is None or label.kind != "application_update":
                raise LabelError(
                    f"groups.json: {label_id} is not a labeled application_update"
                )
            if not isinstance(key, str) or not key:
                raise LabelError(f"groups.json: {label_id} needs a non-empty key")
            label.application_key = key
    return labels


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.answer_key.labels")
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv[1:])
    try:
        labels = load_labels(args.directory)
    except LabelError as error:
        print(error, file=sys.stderr)
        return 1
    kinds = Counter(label.kind for label in labels.values())
    statuses = Counter(
        label.status.value for label in labels.values() if label.status
    )
    keys = {label.application_key for label in labels.values() if label.application_key}
    print(f"labels={len(labels)} application_keys={len(keys)}")
    print("kinds: " + " ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    print("statuses: " + " ".join(f"{k}={v}" for k, v in sorted(statuses.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
