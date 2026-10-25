"""Overnight loop: pull the AI queue, run local Ollama, post results back.

Privacy: logs carry only an index, HTTP status, detail code, outcome category,
elapsed seconds and totals. Never email text, model output, ids or tokens.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from local_tools.api import ApiError
from local_tools.auth import LoginRequired
from local_tools.ollama import OllamaFailed, OllamaUnreachable

PAGE_SIZE = 25
_GLOBAL_DETAILS = {
    (409, "gmail_connection_changed"): "gmail_connection_changed",
    (503, "gmail_connection_unavailable"): "gmail_connection_unavailable",
    (503, "queue_unavailable"): "queue_unavailable",
    (409, "auto_apply_off"): "auto_apply_off",
}
_GLOBAL_STATUSES = {401, 403, 429, 502}
_SKIP = {(409, "review_changed"), (503, "message_unavailable")}


@dataclass
class RunSummary:
    saved: int = 0
    executed: int = 0
    skipped: int = 0
    failed: int = 0
    stopped: str | None = None


def _stop_reason(error: ApiError) -> str | None:
    reason = _GLOBAL_DETAILS.get((error.status, error.detail))
    if reason is None and error.status in _GLOBAL_STATUSES:
        reason = f"http_{error.status}"
    return reason


def run_once(api, ollama, *, limit: int | None = None, log: Callable = print):
    summary = RunSummary()
    attempted: set[str] = set()
    checked_ollama = False
    index = 0
    started = time.monotonic()

    def finish(stopped: str | None = None) -> RunSummary:
        summary.stopped = stopped
        if stopped != "auto_apply_off":
            log(
                f"done saved={summary.saved} executed={summary.executed} "
                f"skipped={summary.skipped} failed={summary.failed} "
                f"stopped={stopped} elapsed={time.monotonic() - started:.0f}s"
            )
        return summary

    while limit is None or index < limit:
        page = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - index)
        try:
            items = api.get("/ai-queue", {"limit": page})["items"]
        except LoginRequired:
            return finish("login_required")
        except ApiError as error:
            return finish(_stop_reason(error) or f"http_{error.status}")
        fresh = [i["message_id"] for i in items if i["message_id"] not in attempted]
        if not fresh:
            return finish()
        if not checked_ollama:
            checked_ollama = True
            if not ollama.reachable():
                return finish("ollama_unreachable")
        for message_id in fresh:
            attempted.add(message_id)
            index += 1
            began = time.monotonic()
            outcome, status, detail = "saved", "-", "-"
            try:
                prompt = api.get(f"/ai-queue/{message_id}/input")
                extraction = ollama.chat(
                    prompt["system_prompt"], prompt["user_prompt"], prompt["format"]
                )
                result = api.post(
                    f"/ai-queue/{message_id}/result",
                    {
                        "generation": prompt["generation"],
                        "model": ollama.model,
                        "extraction": extraction,
                    },
                )
                summary.saved += 1
                if result.get("executed"):
                    summary.executed += 1
                    outcome = "executed"
            except LoginRequired:
                return finish("login_required")
            except OllamaUnreachable:
                if not ollama.reachable():
                    return finish("ollama_unreachable")
                summary.failed += 1
                outcome, detail = "failed", "ollama_unreachable"
            except OllamaFailed as error:
                summary.failed += 1
                outcome, detail = "failed", f"ollama_{error.kind}"
            except ApiError as error:
                reason = _stop_reason(error)
                if reason:
                    return finish(reason)
                status, detail = str(error.status), error.detail
                if error.status == 404 or (error.status, error.detail) in _SKIP:
                    summary.skipped += 1
                    outcome = "skipped"
                else:
                    summary.failed += 1
                    outcome = "failed"
            except (KeyError, TypeError, ValueError):
                summary.failed += 1
                outcome, detail = "failed", "bad_response"
            log(
                f"item {index} {outcome} status={status} detail={detail} "
                f"elapsed={time.monotonic() - began:.1f}s"
            )
    return finish()
