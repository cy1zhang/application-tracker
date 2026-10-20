from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class SyncRun:
    owner_id: str
    run_id: str
    generation: int
    mode: Literal["import", "incremental", "rescan"]
    status: Literal["pending", "running", "completed", "partial", "failed", "reconnect_required"]
    started_at: datetime
    import_after: datetime | None
    processed_count: int = 0
    history_id: str | None = None
    history_snapshot_id: str | None = None
