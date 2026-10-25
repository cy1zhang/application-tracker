"""Single-instance lock file holding the pid and a timestamp."""

import os
import time
import uuid
from pathlib import Path

DEFAULT_LOCK_PATH = Path.home() / "Library/Application Support/application-tracker/worker.lock"


class Lock:
    def __init__(self, path: Path, token: str, clock=time.time) -> None:
        self.path = path
        self.token = token
        self._clock = clock

    def _owned(self) -> bool:
        try:
            return self.path.read_text().split()[2] == self.token
        except (OSError, IndexError):
            return False

    def refresh(self) -> None:
        """Rewrite the timestamp, only while this lock is still ours."""
        if self._owned():
            _write(self.path, self.token, self._clock())

    def release(self) -> None:
        if self._owned():
            self.path.unlink(missing_ok=True)


def _write(path: Path, token: str, now: float) -> None:
    temp = path.with_name(f"{path.name}.{token}.tmp")
    fd = os.open(temp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{os.getpid()} {now} {token}\n")
    os.replace(temp, path)


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
    token = uuid.uuid4().hex
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if not _is_stale(path, stale_after, clock()):
            return None
        _write(path, token, clock())  # atomic takeover; the last writer wins
        return Lock(path, token, clock) if _confirm(path, token) else None
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{os.getpid()} {clock()} {token}\n")
    return Lock(path, token, clock)


def _confirm(path: Path, token: str) -> bool:
    try:
        return path.read_text().split()[2] == token
    except (OSError, IndexError):
        return False
