import json
from collections import Counter

import pytest

from app.domain.messages import MessageSummary
from evaluation.answer_key.export import ExportSummary, export, select_sample
from local_tools.api import ApiError


def _summaries(counts):
    out = []
    for (outcome, reason), n in counts.items():
        for i in range(n):
            out.append(
                MessageSummary(
                    message_id=f"{outcome}-{reason}-{i}",
                    outcome=outcome,
                    reason=reason,
                    received_at=None,
                )
            )
    return out


FULL = {
    ("ignored", "no_event"): 300,
    ("review_required", "ai_proposal"): 150,
    ("review_required", "ambiguous"): 40,
    ("applied", "rule"): 30,
}


def _strata(ids):
    c = Counter()
    for i in ids:
        outcome, reason, _ = i.rsplit("-", 2)
        if (outcome, reason) == ("ignored", "no_event"):
            c["ignored"] += 1
        elif (outcome, reason) == ("review_required", "ai_proposal"):
            c["ai"] += 1
        elif outcome == "review_required":
            c["review"] += 1
        else:
            c["other"] += 1
    return c


def test_select_sample_respects_quotas_and_is_deterministic():
    summaries = _summaries(FULL)
    first = select_sample(summaries, size=200, seed=1)
    assert _strata(first) == {"ignored": 90, "ai": 80, "review": 20, "other": 10}
    assert len(set(first)) == 200
    assert select_sample(list(reversed(summaries)), size=200, seed=1) == first
    assert select_sample(summaries, size=200, seed=2) != first


def test_select_sample_redistributes_short_strata():
    counts = {**FULL, ("review_required", "ai_proposal"): 50, ("applied", "rule"): 4}
    ids = select_sample(_summaries(counts), size=200, seed=1)
    assert _strata(ids) == {"ignored": 90 + 30 + 6, "ai": 50, "review": 20, "other": 4}
    assert ids == select_sample(_summaries(counts), size=200, seed=1)


class FakeApi:
    def __init__(self, summaries, unavailable=()):
        self.summaries = summaries
        self.unavailable = set(unavailable)

    def get(self, path, params=None):
        if path == "/messages":
            start = int(params.get("cursor") or 0)
            items = self.summaries[start : start + 100]
            nxt = start + 100
            return {
                "items": [json.loads(s.model_dump_json()) for s in items],
                "next_cursor": str(nxt) if nxt < len(self.summaries) else None,
            }
        message_id = path.split("/")[2]
        if message_id in self.unavailable:
            raise ApiError(503, "message_unavailable")
        return {
            "message_id": message_id,
            "thread_id": "t",
            "received_at": "2026-01-01T00:00:00Z",
            "subject": f"subj {message_id}",
            "sender": "a@example.com",
            "text": f"body {message_id}",
        }


def _small():
    return _summaries({("ignored", "no_event"): 10})


def test_export_writes_numbered_files_and_index(tmp_path, capsys):
    out = tmp_path / ".local" / "key"
    result = export(FakeApi(_small()), out, size=10, seed=1)
    assert result == ExportSummary(written=10, unavailable=0)
    index = json.loads((out / "index.json").read_text())
    assert sorted(index) == [f"E{n:03d}" for n in range(1, 11)]
    first = json.loads((out / "emails" / "E001.json").read_text())
    assert first == {
        "id": "E001",
        "subject": f"subj {index['E001']}",
        "sender": "a@example.com",
        "text": f"body {index['E001']}",
    }
    printed = capsys.readouterr().out
    assert "subj" not in printed and "example.com" not in printed
    assert "ignored" not in printed


def test_export_counts_unavailable_and_skips(tmp_path):
    out = tmp_path / ".local" / "key"
    api = FakeApi(_small(), unavailable={"ignored-no_event-0"})
    result = export(api, out, size=10, seed=1)
    assert result == ExportSummary(written=9, unavailable=1)
    index = json.loads((out / "index.json").read_text())
    assert sorted(index) == [f"E{n:03d}" for n in range(1, 10)]
    assert "ignored-no_event-0" not in index.values()


def test_export_refuses_out_dir_inside_git_tracked_path(tmp_path):
    api = FakeApi(_small())
    with pytest.raises(ValueError):
        export(api, tmp_path / "answer-key")
    with pytest.raises(ValueError):
        export(api, tmp_path / ".local" / ".." / "answer-key")
    assert not (tmp_path / "answer-key").exists()
