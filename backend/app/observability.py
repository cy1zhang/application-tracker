import logging
import re

# Every event name `log_event` may emit. A test checks call sites against this set.
LOG_EVENTS = frozenset({
    "sync_worker_failure",
    "sync_run_failed",
    "sync_page_completed",
    "dispatch_summary",
    "ai_escalation_failed",
    "api_unhandled_error",
    "deletion_revoke_failed",
    "deletion_job_failed",
    "deletion_completed",
    "deletion_continued",
    "deletion_enqueue_failed",
})

_FIELDS = frozenset({
    "run_id", "mode", "phase", "provider", "category", "attempt", "count",
    "processed", "duration_ms", "status",
})
_EVENT = re.compile(r"[a-z_]{1,40}")
_VALUE = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def category_of(error: BaseException) -> str:
    return type(error).__name__


def log_event(logger: logging.Logger, level: str, event: str, **fields) -> None:
    """Emit `event=<name> key=value ...` with only allow-listed, sanitized fields.

    Unsafe fields are dropped rather than raised so logging can never fail a job.
    """
    if not _EVENT.fullmatch(event):
        raise ValueError("Invalid log event name")
    parts = [f"event={event}"]
    for key, value in fields.items():
        if key not in _FIELDS or isinstance(value, bool):
            continue
        if isinstance(value, int) or (
            isinstance(value, str) and _VALUE.fullmatch(value)
        ):
            parts.append(f"{key}={value}")
    logger.log(_LEVELS[level], "%s", " ".join(parts))
