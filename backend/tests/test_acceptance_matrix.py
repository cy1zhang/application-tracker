"""Guards docs/VERIFICATION.md: the PRD acceptance matrix must stay true.

Parses the matrix and checklist tables, requires PRD criteria 1-14 and live
checks L1-L12, and verifies every cited test still exists. Test files are
parsed with ast, never imported.
"""

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "VERIFICATION.md"
FIXTURES = ROOT / "backend" / "evaluation" / "fixtures"
STATUSES = ("Verified", "Partly verified", "Pending")
TEST_CITE = re.compile(r"`(backend/tests/\w+\.py)::(\w+)`")
EVAL_CITE = re.compile(r"`eval:(\w+)`")
LIVE_CITE = re.compile(r"\bL(\d+)\b")


def _section(title: str) -> list[str]:
    lines = DOC.read_text().splitlines()
    start = lines.index(f"## {title}") + 1
    end = next(
        (i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines)
    )
    return lines[start:end]


def _rows(title: str, first_cell: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in _section(title):
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if re.fullmatch(first_cell, cells[0]):
            assert cells[0] not in rows, f"duplicate row {cells[0]}"
            rows[cells[0]] = cells
    return rows


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_matrix_has_every_prd_criterion_exactly_once() -> None:
    assert sorted(_rows("Acceptance matrix", r"\d+"), key=int) == [
        str(n) for n in range(1, 15)
    ]


def test_every_cited_test_exists() -> None:
    cached: dict[str, set[str]] = {}
    cited = 0
    for number, cells in _rows("Acceptance matrix", r"\d+").items():
        for file, name in TEST_CITE.findall(" ".join(cells)):
            path = ROOT / file
            assert path.is_file(), f"criterion {number}: {file} is missing"
            names = cached.setdefault(file, _test_names(path))
            assert name in names, f"criterion {number}: {file}::{name} does not exist"
            assert name.startswith("test_"), f"criterion {number}: {name} is not a test"
            cited += 1
    assert cited >= 14


def test_every_criterion_has_a_test_or_a_live_check() -> None:
    for number, cells in _rows("Acceptance matrix", r"\d+").items():
        text = " ".join(cells)
        assert TEST_CITE.search(text) or LIVE_CITE.search(text), (
            f"criterion {number} has neither an automated test nor a live check"
        )


def test_cited_evaluation_categories_exist() -> None:
    categories = {
        scenario["category"]
        for name in ("rules", "ai")
        for scenario in json.loads((FIXTURES / f"{name}.json").read_text())
    }
    for number, cells in _rows("Acceptance matrix", r"\d+").items():
        for category in EVAL_CITE.findall(" ".join(cells)):
            assert category in categories, f"criterion {number}: unknown eval:{category}"


def test_cited_live_checks_are_in_the_checklist() -> None:
    checklist = _rows("Live checklist", r"L\d+")
    assert sorted(checklist, key=lambda i: int(i[1:])) == [
        f"L{n}" for n in range(1, 13)
    ]
    for number, cells in _rows("Acceptance matrix", r"\d+").items():
        for live in LIVE_CITE.findall(" ".join(cells)):
            assert f"L{live}" in checklist, f"criterion {number}: unknown L{live}"


def test_live_checks_state_who_runs_them_and_a_known_status() -> None:
    for check, cells in _rows("Live checklist", r"L\d+").items():
        assert len(cells) == 5, f"{check} needs ID, check, who, status, evidence"
        assert cells[2], f"{check} has no runner"
        assert cells[3].startswith(STATUSES), f"{check} status is {cells[3]!r}"
        assert cells[4], f"{check} has no evidence"


def test_owner_only_checks_are_never_agent_run_and_stay_pending() -> None:
    checklist = _rows("Live checklist", r"L\d+")
    for check in ("L6", "L7"):
        assert checklist[check][2].startswith("Owner"), check
        assert checklist[check][3].startswith("Pending"), check
    assert "agents never run" in checklist["L7"][2].lower()
    for check in ("L10", "L11"):
        assert checklist[check][3].startswith("Pending"), check
