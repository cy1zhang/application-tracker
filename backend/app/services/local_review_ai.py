from dataclasses import dataclass, replace
from typing import Any

from app.domain.ai import EXTRACTION_JSON_SCHEMA
from app.domain.applications import ApplicationStatus, Season
from app.domain.reviews import ReviewItem
from app.repositories.dynamodb_reviews import GmailConnectionChanged
from app.services.ai_extraction import build_prompt, validate_extraction
from app.services.ai_reconcile import ESCALATION_REASONS, reconcile_extraction
from app.services.credential_crypto import CredentialError
from app.services.email_rules import ApplicationCandidate, RuleDecision
from app.services.gmail_fetch import GmailApiError, MessageUnavailable
from app.services.gmail_message import (
    MessageParseError,
    ParsedGmailMessage,
    parse_gmail_message,
)
from app.services.google_oauth import OAuthError
from app.services.sync_candidates import load_candidates


class LocalAiNotFound(Exception):
    pass


class LocalAiIneligible(Exception):
    pass


class LocalAiStale(Exception):
    pass


class LocalAiConnectionChanged(LocalAiStale):
    """The Gmail connection generation changed; every pending item is affected."""


class LocalAiUnavailable(Exception):
    """The Gmail connection cannot be used; every pending item is affected."""


class LocalAiMessageUnavailable(Exception):
    """This one Gmail message is gone or unreadable."""


@dataclass(frozen=True)
class LocalAiInput:
    system_prompt: str
    user_prompt: str
    format: dict
    generation: int


@dataclass(frozen=True)
class LocalAiResult:
    saved: bool
    review: ReviewItem | None = None


class LocalReviewAiService:
    def __init__(
        self, *, reviews, connections, applications, cipher, oauth, fetch,
    ) -> None:
        self._reviews = reviews
        self._connections = connections
        self._applications = applications
        self._cipher = cipher
        self._oauth = oauth
        self._fetch = fetch

    def prepare(self, owner_id: str, message_id: str) -> LocalAiInput:
        _, message, candidates, generation = self._load(owner_id, message_id)
        prompt = build_prompt(message, candidates)
        return LocalAiInput(
            system_prompt=prompt.system,
            user_prompt=prompt.user,
            format=EXTRACTION_JSON_SCHEMA,
            generation=generation,
        )

    def suggest(
        self, owner_id: str, message_id: str, *, generation: int,
        model: str, raw: object,
    ) -> LocalAiResult:
        review, message, candidates, _ = self._load(
            owner_id, message_id, expected_generation=generation
        )
        decision = build_local_suggestion(review, message, candidates, raw)
        if decision is None:
            return LocalAiResult(saved=False)
        try:
            saved = self._reviews.save_local_suggestion(
                owner_id=owner_id, message_id=message_id, generation=generation,
                decision=decision, model=model,
            )
        except GmailConnectionChanged:
            raise LocalAiConnectionChanged from None
        return LocalAiResult(saved=True, review=saved)

    def _load(self, owner_id: str, message_id: str, expected_generation: int | None = None):
        review = self._reviews.get_pending(owner_id=owner_id, message_id=message_id)
        if review is None:
            raise LocalAiNotFound
        if not local_review_eligible(review):
            raise LocalAiIneligible
        message, generation = fetch_owner_message(
            connections=self._connections, cipher=self._cipher, oauth=self._oauth,
            fetch=self._fetch, owner_id=owner_id, message_id=message_id,
            expected_generation=expected_generation,
        )
        if message.thread_id != review.get("thread_id"):
            raise LocalAiStale
        candidates = load_candidates(owner_id, self._applications)
        return review, message, candidates, generation


def fetch_owner_message(
    *, connections, cipher, oauth, fetch, owner_id: str, message_id: str,
    expected_generation: int | None = None,
) -> tuple[ParsedGmailMessage, int]:
    """Fetch and parse one Gmail message with the owner's connection.

    Returns the message and the connection generation it was fetched under.
    """
    connection = connections.get_connection(owner_id)
    if expected_generation is not None and connection.generation != expected_generation:
        raise LocalAiConnectionChanged
    if connection.encrypted_token is None or connection.reconnect_required:
        raise LocalAiUnavailable
    encrypted = connections.get_active(owner_id, connection.generation)
    if encrypted is None:
        raise LocalAiConnectionChanged
    try:
        token = cipher.decrypt_refresh_token(owner_id, encrypted)
        access_token = oauth.refresh_access_token(token)
        message = parse_gmail_message(fetch.get_message(access_token, message_id))
    except (MessageUnavailable, MessageParseError):
        raise LocalAiMessageUnavailable from None
    except (CredentialError, OAuthError, GmailApiError):
        raise LocalAiUnavailable from None
    return message, connection.generation


def local_review_eligible(review: dict[str, Any]) -> bool:
    return (
        review.get("outcome") == "review_required"
        and "resolution" not in review
        and "ai_proposal" not in review
        and review.get("reason") in ESCALATION_REASONS
    )


def build_local_suggestion(
    review: dict[str, Any],
    message: ParsedGmailMessage,
    applications: list[ApplicationCandidate],
    raw: object,
) -> RuleDecision | None:
    prompt = build_prompt(message, applications)
    extraction = validate_extraction(raw, prompt)
    if not any(
        getattr(extraction, field) is not None
        for field in (
            "company", "role", "employment_type", "season", "year", "status",
            "requisition_id",
        )
    ):
        return None
    rule = RuleDecision(
        outcome="review_required",
        reason=review["reason"],
        application_id=review.get("application_id"),
        status=(
            ApplicationStatus(review["proposed_status"])
            if review.get("proposed_status") else None
        ),
        season=(Season(review["proposed_season"]) if review.get("proposed_season") else None),
        year=review.get("proposed_year"),
        requisition_id=review.get("requisition_id"),
        evidence=review.get("evidence", ""),
    )
    result = reconcile_extraction(rule, extraction, message, applications)
    return replace(result, outcome="review_required", reason="ai_proposal")
