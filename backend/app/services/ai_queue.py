from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.ai import EXTRACTION_JSON_SCHEMA
from app.repositories.dynamodb_email import PlanConflict, is_queued
from app.repositories.dynamodb_reviews import GmailConnectionChanged
from app.services.ai_extraction import build_prompt, validate_extraction
from app.services.auto_apply import MessageRef, decide
from app.services.local_review_ai import (
    LocalAiConnectionChanged,
    LocalAiInput,
    LocalAiNotFound,
    LocalAiStale,
    fetch_owner_message,
)
from app.services.sync_candidates import load_candidates


class AutoApplyOff(Exception):
    pass


@dataclass(frozen=True)
class QueuePage:
    items: list[dict]
    remaining: int


@dataclass(frozen=True)
class PlanResult:
    planned: bool
    executed: bool
    action: str | None


class AiQueueService:
    def __init__(
        self, *, emails, settings, applications, connections, cipher, oauth, fetch,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._emails = emails
        self._settings = settings
        self._applications = applications
        self._connections = connections
        self._cipher = cipher
        self._oauth = oauth
        self._fetch = fetch
        self._clock = clock

    def _mode(self, owner_id: str) -> str:
        mode = self._settings.get_mode(owner_id)
        if mode == "off":
            raise AutoApplyOff
        return mode

    def backfill(self, owner_id: str) -> int:
        self._mode(owner_id)
        return self._emails.mark_backfill(owner_id)

    def list(self, owner_id: str, limit: int) -> QueuePage:
        self._mode(owner_id)
        queued = self._emails.list_ai_queue(owner_id)
        return QueuePage(
            items=[
                {"message_id": item["source_message_id"], "received_at": item.get("received_at")}
                for item in queued[:limit]
            ],
            remaining=len(queued),
        )

    def prepare(self, owner_id: str, message_id: str) -> LocalAiInput:
        self._mode(owner_id)
        item = self._queued_item(owner_id, message_id)
        message, generation = self._fetch_message(owner_id, message_id)
        if item.get("thread_id") not in {None, message.thread_id}:
            raise LocalAiStale
        prompt = build_prompt(message, load_candidates(owner_id, self._applications))
        return LocalAiInput(
            system_prompt=prompt.system, user_prompt=prompt.user,
            format=EXTRACTION_JSON_SCHEMA, generation=generation,
        )

    def submit(
        self, owner_id: str, message_id: str, *, generation: int, model: str, raw: object,
    ) -> PlanResult:
        mode = self._mode(owner_id)
        item = self._emails.get_event_item(owner_id, message_id)
        if item is None:
            raise LocalAiNotFound
        if "ai_plan" in item:
            raise LocalAiStale
        if not is_queued(item):
            raise LocalAiNotFound
        message, _ = self._fetch_message(owner_id, message_id, generation)
        if item.get("thread_id") not in {None, message.thread_id}:
            raise LocalAiStale
        candidates = load_candidates(owner_id, self._applications)
        # Raises InvalidExtraction; the route maps it to a body-free 422.
        extraction = validate_extraction(raw, build_prompt(message, candidates))
        planned_at = self._clock()
        try:
            self._emails.save_plan(
                owner_id, message_id, extraction=extraction, model=model,
                generation=generation, planned_at=planned_at,
            )
        except GmailConnectionChanged:
            raise LocalAiConnectionChanged from None
        except PlanConflict:
            raise LocalAiStale from None
        if mode != "on":
            return PlanResult(planned=True, executed=False, action=None)

        ref = MessageRef(message.message_id, message.thread_id, message.received_at)
        decision = decide(extraction, message_ref=ref, candidates=candidates)
        version = next(
            (
                candidate.application.version for candidate in candidates
                if candidate.application.application_id == decision.application_id
            ),
            None,
        )
        self._emails.execute(
            owner_id, message=ref, subject=message.subject, sender=message.sender,
            decision=decision, extraction=extraction, application_version=version,
            new_application_id=uuid4().hex if decision.action == "create" else None,
            occurred_at=planned_at,
        )
        return PlanResult(planned=True, executed=True, action=decision.action)

    def _queued_item(self, owner_id: str, message_id: str) -> dict:
        item = self._emails.get_event_item(owner_id, message_id)
        if item is None or not is_queued(item):
            raise LocalAiNotFound
        return item

    def _fetch_message(self, owner_id, message_id, expected_generation=None):
        return fetch_owner_message(
            connections=self._connections, cipher=self._cipher, oauth=self._oauth,
            fetch=self._fetch, owner_id=owner_id, message_id=message_id,
            expected_generation=expected_generation,
        )
