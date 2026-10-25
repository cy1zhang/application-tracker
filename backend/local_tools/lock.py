"""Single-instance lock file holding the pid and a timestamp."""

import os
import time
from pathlib import Path

DEFAULT_LOCK_PATH = Path.home() / "Library/Application Support/application-tracker/worker.lock"


class Lock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def _is_stale(path: Path, stale_after: float, now: float) -> bool:
    try:
        stamp = float(path.read_text().split()[1])
    except (OSError, IndexError, ValueError):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return False  # vanished: the next create attempt decides
    return now - stamp > stale_after


def acquire_lock(
    path: Path = DEFAULT_LOCK_PATH, stale_after: float = 7200, *, clock=time.time
) -> Lock | None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not _is_stale(path, stale_after, clock()):
                return None
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w") as handle:
            handle.write(f"{os.getpid()} {clock()}\n")
        return Lock(path)
    return None
