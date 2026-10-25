"""Single-instance lock file holding the pid and a timestamp."""

import os
import time
import uuid
from pathlib import Path

GUARD_STALE_SECONDS = 60
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

    def refresh(self) -> bool:
        """Rewrite the timestamp; False when the lock is no longer ours."""
        if not self._owned():
            return False
        _write(self.path, self.token, self._clock())
        return True

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


def _create(path: Path, token: str, now: float) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as handle:
        handle.write(f"{os.getpid()} {now} {token}\n")
    return True


def acquire_lock(
    path: Path = DEFAULT_LOCK_PATH, stale_after: float = 7200, *, clock=time.time
) -> Lock | None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    if _create(path, token, clock()):
        return Lock(path, token, clock)
    if not _is_stale(path, stale_after, clock()):
        return None
    # Takeovers are serialized by a guard file, so a racer that judged the lock stale
    # earlier can never remove a lock another racer just created.
    guard = path.with_name(f"{path.name}.takeover")
    for _ in range(2):
        try:
            os.close(os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            break
        except FileExistsError:
            if not _is_stale(guard, GUARD_STALE_SECONDS, clock()):
                return None
            guard.unlink(missing_ok=True)  # a crashed takeover left it behind
    else:
        return None
    try:
        if not _is_stale(path, stale_after, clock()):
            return None
        path.unlink(missing_ok=True)
        return Lock(path, token, clock) if _create(path, token, clock()) else None
    finally:
        guard.unlink(missing_ok=True)
