import json

import pytest
from pydantic import ValidationError

from evaluation.answer_key.labels import Label, LabelError, load_labels, main

BASE = {
    "id": "E001",
    "kind": "application_update",
    "company": "Acme",
    "role": "Software Engineering Intern",
    "role_type": "internship",
    "season": "summer",
    "year": 2026,
    "status": "applied",
    "requisition_id": None,
}


def _line(**overrides):
    return json.dumps({**BASE, **overrides})


def _setup(tmp_path, lines, index=None, groups=None, batch="batch-01.jsonl"):
    (tmp_path / "labels").mkdir(exist_ok=True)
    (tmp_path / "labels" / batch).write_text("\n".join(lines) + "\n")
    index = index if index is not None else {"E001": "m1", "E002": "m2"}
    (tmp_path / "index.json").write_text(json.dumps(index))
    if groups is not None:
        (tmp_path / "groups.json").write_text(json.dumps(groups))
    return tmp_path


def test_label_rejects_unknown_kind_and_extra_fields():
    assert Label(**BASE).kind == "application_update"
    with pytest.raises(ValidationError):
        Label(**{**BASE, "kind": "spam"})
    with pytest.raises(ValidationError):
        Label(**{**BASE, "extra": 1})
    with pytest.raises(ValidationError):
        Label(**{**BASE, "role_type": "unknown"})
    with pytest.raises(ValidationError):
        Label(**{**BASE, "year": 1999})
    with pytest.raises(ValidationError):
        Label(**{**BASE, "year": "2026"})


def test_load_labels_reports_file_and_line_on_bad_json(tmp_path):
    _setup(tmp_path, [_line(), "{not json"], batch="batch-03.jsonl")
    with pytest.raises(LabelError, match=r"batch-03\.jsonl:2:"):
        load_labels(tmp_path)


def test_load_labels_reports_file_and_line_on_bad_label(tmp_path):
    _setup(tmp_path, [_line(), _line(id="E002", kind="spam")])
    with pytest.raises(LabelError, match=r"batch-01\.jsonl:2:"):
        load_labels(tmp_path)


def test_load_labels_rejects_ids_not_in_index(tmp_path):
    _setup(tmp_path, [_line(id="E999")])
    with pytest.raises(LabelError, match=r"batch-01\.jsonl:1:.*E999"):
        load_labels(tmp_path)


def test_load_labels_rejects_duplicate_ids(tmp_path):
    _setup(tmp_path, [_line(), _line()])
    with pytest.raises(LabelError, match=r"batch-01\.jsonl:2:.*duplicate"):
        load_labels(tmp_path)


def test_groups_assign_application_key_only_to_application_updates(tmp_path):
    lines = [
        _line(),
        _line(id="E002", kind="job_alert", company=None, role=None,
              role_type=None, season=None, year=None, status=None),
    ]
    _setup(tmp_path, lines, groups={"E001": "app-1"})
    labels = load_labels(tmp_path)
    assert labels["E001"].application_key == "app-1"
    assert labels["E002"].application_key is None

    (tmp_path / "groups.json").write_text(json.dumps({"E002": "app-2"}))
    with pytest.raises(LabelError, match=r"groups\.json.*E002"):
        load_labels(tmp_path)


def test_cli_validate_prints_only_counts(tmp_path, capsys):
    _setup(tmp_path, [_line(company="SecretCo")], groups={"E001": "app-1"})
    assert main(["labels", "validate", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "application_update=1" in out
    assert "applied=1" in out
    assert "SecretCo" not in out and "Intern" not in out


def test_cli_validate_reports_error_and_fails(tmp_path, capsys):
    _setup(tmp_path, ["{bad"])
    assert main(["labels", "validate", str(tmp_path)]) == 1
    assert "batch-01.jsonl:1:" in capsys.readouterr().err
