"""Export a stratified sample of processed emails for local labeling.

Output goes only under a git-ignored `.local/` folder. Only counts are printed.
"""

import argparse
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from app.domain.messages import MessageSummary
from local_tools.api import ApiError, TrackerApi
from local_tools.config import load_config

DEFAULT_SEED = 20261024


@dataclass(frozen=True)
class ExportSummary:
    written: int
    unavailable: int


def _stratum(summary: MessageSummary) -> str:
    if (summary.outcome, summary.reason) == ("ignored", "no_event"):
        return "ignored"
    if summary.outcome == "review_required":
        return "ai" if summary.reason == "ai_proposal" else "review"
    return "other"


def select_sample(
    summaries: list[MessageSummary], *, size: int, seed: int
) -> list[str]:
    strata: dict[str, list[str]] = {"ignored": [], "ai": [], "review": [], "other": []}
    for summary in summaries:
        strata[_stratum(summary)].append(summary.message_id)
    rng = random.Random(seed)
    quotas = {"ai": size * 2 // 5, "review": size // 10, "other": size // 20}
    quotas["ignored"] = size - sum(quotas.values())
    picked: list[str] = []
    shortfall = 0
    for name in ("ai", "review", "other"):
        pool = sorted(strata[name])
        take = min(quotas[name], len(pool))
        shortfall += quotas[name] - take
        picked += rng.sample(pool, take)
    pool = sorted(strata["ignored"])
    take = min(quotas["ignored"] + shortfall, len(pool))
    return rng.sample(pool, take) + picked


def _list_all(api) -> list[MessageSummary]:
    summaries: list[MessageSummary] = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = api.get("/messages", params)
        summaries += [MessageSummary(**item) for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            return summaries


def export(
    api, out_dir: Path, *, size: int = 200, seed: int = DEFAULT_SEED
) -> ExportSummary:
    out_dir = Path(out_dir).resolve()
    if ".local" not in out_dir.parts:
        raise ValueError("out_dir must be under a .local/ folder")
    ids = select_sample(_list_all(api), size=size, seed=seed)
    (out_dir / "emails").mkdir(parents=True, exist_ok=True)
    index: dict[str, str] = {}
    unavailable = 0
    for message_id in ids:
        try:
            content = api.get(f"/messages/{message_id}/content")
        except ApiError as error:
            if error.status == 503 and error.detail == "message_unavailable":
                unavailable += 1
                continue
            raise
        label = f"E{len(index) + 1:03d}"
        index[label] = message_id
        record = {
            "id": label,
            "subject": content["subject"],
            "sender": content["sender"],
            "text": content["text"],
        }
        (out_dir / "emails" / f"{label}.json").write_text(json.dumps(record, indent=2))
    (out_dir / "index.json").write_text(json.dumps(index, indent=2))
    print(f"written={len(index)} unavailable={unavailable}")
    return ExportSummary(written=len(index), unavailable=unavailable)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation.answer_key.export")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--config", default="../.env.local-tools")
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv[1:])
    api = TrackerApi(load_config(args.config))
    export(api, args.out, size=args.size, seed=args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
