import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

from app.domain.ai import AiProvider, AiRuntime
from app.repositories.dynamodb_ai import (
    AiBudgetExhausted,
    AiKeyInvalid,
    AiSettingsChanged,
    DynamoAiSettingsRepository,
)
from app.repositories.dynamodb_sync import StaleSyncRun
from app.services.ai_extraction import (
    InvalidExtraction,
    ValidatedExtraction,
    build_prompt,
    validate_extraction,
)
from app.services.ai_providers import ExtractionProvider, ProviderError
from app.services.ai_reconcile import ESCALATION_REASONS, reconcile_extraction
from app.services.credential_crypto import AI_API_KEY, CredentialCipher, CredentialError
from app.services.email_rules import (
    AiCall,
    ApplicationCandidate,
    RuleDecision,
)
from app.services.gmail_message import ParsedGmailMessage

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2
_FIELDS = (
    "company", "role", "employment_type", "season", "year", "status",
    "requisition_id",
)


class AiEscalator:
    """Per-page AI tier. Logs only the provider name and a sanitized category."""

    def __init__(
        self,
        *,
        owner_id: str,
        generation: int,
        run_id: str,
        settings: DynamoAiSettingsRepository,
        cipher: CredentialCipher,
        provider_factory: Callable[[AiProvider], ExtractionProvider],
        models: Mapping[AiProvider, str],
        clock: Callable[[], datetime],
    ) -> None:
        self._owner_id = owner_id
        self._generation = generation
        self._run_id = run_id
        self._settings = settings
        self._cipher = cipher
        self._provider_factory = provider_factory
        self._models = models
        self._clock = clock
        self._runtime_loaded = False
        self._runtime: AiRuntime | None = None
        self._provider: ExtractionProvider | None = None
        self._api_key: str | None = None
        self._tripped_note: str | None = None

    def escalate(
        self,
        message: ParsedGmailMessage,
        rule_decision: RuleDecision,
        applications: list[ApplicationCandidate],
    ) -> RuleDecision:
        if (
            rule_decision.outcome != "review_required"
            or rule_decision.reason not in ESCALATION_REASONS
        ):
            return rule_decision
        runtime = self._load_runtime()
        if runtime is None:
            return rule_decision
        if self._tripped_note is not None:
            return self._noted(rule_decision, self._tripped_note)
        if self._api_key is None and not self._prepare(runtime):
            return self._noted(rule_decision, self._tripped_note)

        prompt = build_prompt(message, applications)
        attempts = 0
        while True:
            attempts += 1
            try:
                self._settings.reserve_request(
                    self._owner_id, generation=self._generation, run_id=self._run_id,
                    settings_version=runtime.settings_version, now=self._clock(),
                )
            except StaleSyncRun:
                raise
            except AiBudgetExhausted:
                return self._trip(runtime, rule_decision, "ai_budget_exhausted", "budget")
            except AiKeyInvalid:
                return self._trip(runtime, rule_decision, "ai_key_invalid", "invalid_key")
            except AiSettingsChanged:
                return self._trip(runtime, rule_decision, "ai_unavailable", "settings_changed")
            try:
                raw = self._provider.extract(self._api_key, runtime.model, prompt)
            except ProviderError as failure:
                self._log(runtime, failure.category)
                if failure.category == "provider_unavailable" and attempts < MAX_ATTEMPTS:
                    continue
                if failure.category == "invalid_key":
                    self._settings.mark_key_invalid(
                        self._owner_id, runtime.settings_version
                    )
                    self._tripped_note = "ai_key_invalid"
                    return self._noted(rule_decision, "ai_key_invalid")
                if failure.category in {"provider_unavailable", "rate_limited"}:
                    self._tripped_note = "ai_unavailable"
                return self._noted(rule_decision, "ai_unavailable")
            break

        try:
            extraction = validate_extraction(raw, prompt)
        except InvalidExtraction:
            self._log(runtime, "invalid_output")
            return self._noted(rule_decision, "ai_unavailable")
        if not _has_fields(extraction):
            return rule_decision
        result = reconcile_extraction(rule_decision, extraction, message, applications)
        return replace(
            result,
            ai_call=AiCall(
                provider=runtime.provider.value, model=runtime.model,
                settings_version=runtime.settings_version, rule_decision=rule_decision,
            ),
        )

    def _load_runtime(self) -> AiRuntime | None:
        if not self._runtime_loaded:
            self._runtime = self._settings.load_runtime(self._owner_id, self._models)
            self._runtime_loaded = True
        return self._runtime

    def _prepare(self, runtime: AiRuntime) -> bool:
        try:
            self._api_key = self._cipher.decrypt(
                self._owner_id, AI_API_KEY, runtime.encrypted_key
            )
        except CredentialError:
            self._log(runtime, "credential_unreadable")
            self._tripped_note = "ai_unavailable"
            return False
        self._provider = self._provider_factory(runtime.provider)
        return True

    def _trip(self, runtime, decision, note, category) -> RuleDecision:
        self._log(runtime, category)
        self._tripped_note = note
        return self._noted(decision, note)

    @staticmethod
    def _noted(decision: RuleDecision, note: str) -> RuleDecision:
        return replace(decision, ai_note=note)

    @staticmethod
    def _log(runtime: AiRuntime, category: str) -> None:
        logger.warning(
            "AI escalation failed: provider=%s category=%s",
            runtime.provider.value, category,
        )


def _has_fields(extraction: ValidatedExtraction) -> bool:
    return any(getattr(extraction, field) is not None for field in _FIELDS)
