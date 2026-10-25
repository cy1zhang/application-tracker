import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from local_tools import auth
from local_tools.api import TrackerApi
from local_tools.auth import LoginRequired
from local_tools.config import load_config
from local_tools.lock import DEFAULT_LOCK_PATH, acquire_lock
from local_tools.ollama import OllamaClient
from local_tools.worker import RunSummary, run_once

DEFAULT_LOG_PATH = Path.home() / "Library/Logs/application-tracker/worker.log"
EXIT_STOP, EXIT_LOCKED, EXIT_LOGIN = 2, 3, 4
IDLE_RUNS_LIMIT = 2
REPEAT_SLEEP_SECONDS = 30


def _append_log(log_path: Path, clock, text: str) -> None:
    log_path = Path(log_path)
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a") as handle:
        handle.write(f"{clock().isoformat()} {text}\n")


def _exit_code(stopped: str | None) -> int:
    if stopped in (None, "auto_apply_off"):
        return 0
    return EXIT_LOGIN if stopped == "login_required" else EXIT_STOP


def run_worker(
    api, ollama, *, limit=None, until_empty=False, log_path=DEFAULT_LOG_PATH,
    lock_path=DEFAULT_LOCK_PATH, clock=lambda: datetime.now(UTC), sleep=time.sleep,
    run=run_once, log=print,
) -> int:
    lock = acquire_lock(lock_path)
    if lock is None:
        log("worker already running")
        return EXIT_LOCKED
    idle = 0

    def refreshing_log(line: str) -> None:
        lock.refresh()  # run_once logs after every item
        log(line)

    try:
        while True:
            try:
                summary = run(api, ollama, limit=limit, log=refreshing_log)
            except LoginRequired:
                log("login required")
                _append_log(log_path, clock, "login required")
                return EXIT_LOGIN
            _append_log(
                log_path, clock,
                f"saved={summary.saved} executed={summary.executed} "
                f"skipped={summary.skipped} failed={summary.failed} "
                f"stopped={summary.stopped}",
            )
            code = _exit_code(summary.stopped)
            if summary.stopped == "login_required":
                log("login required")
                _append_log(log_path, clock, "login required")
            if not until_empty or summary.stopped is not None:
                return code
            if summary == RunSummary():  # nothing attempted: queue is empty
                return 0
            idle = idle + 1 if summary.saved == 0 else 0
            if idle >= IDLE_RUNS_LIMIT:
                return 0
            sleep(REPEAT_SLEEP_SECONDS)
    finally:
        lock.release()


def main(argv: list[str]) -> int:
    usage = "usage: python -m local_tools login|logout|worker [--limit N] [--until-empty]"
    if len(argv) >= 2 and argv[1] == "worker":
        limit, until_empty, rest = None, False, argv[2:]
        while rest:
            if rest[0] == "--until-empty":
                until_empty, rest = True, rest[1:]
            elif rest[0] == "--limit" and len(rest) > 1 and rest[1].isdigit():
                limit, rest = int(rest[1]), rest[2:]
            else:
                print(usage, file=sys.stderr)
                return 2
        config = load_config("../.env.local-tools")
        return run_worker(
            TrackerApi(config), OllamaClient(), limit=limit, until_empty=until_empty
        )
    if len(argv) != 2 or argv[1] not in ("login", "logout"):
        print(usage, file=sys.stderr)
        return 2
    config = load_config("../.env.local-tools")
    if argv[1] == "login":
        auth.login(config)
        print("Signed in.")
    else:
        auth.logout(config)
        print("Signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
