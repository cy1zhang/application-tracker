import http.client
import json
from collections.abc import Callable
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.domain.ai import AiProvider
from app.services.ai_extraction import PromptInput

TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 64 * 1024
MAX_OUTPUT_TOKENS = 1024

CATEGORIES = ("invalid_key", "rate_limited", "provider_unavailable", "invalid_output")


class ProviderError(Exception):
    """Carries only a sanitized category; never provider text, prompts, or keys."""

    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


class ExtractionProvider(Protocol):
    def extract(self, api_key: str, model: str, prompt_input: PromptInput) -> dict: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Returning None makes urllib raise the 3xx as an HTTPError, so the
        # API key is never forwarded to another host.
        return None


_default_opener = build_opener(_NoRedirect)
default_urlopen = _default_opener.open


def post_json(
    urlopen: Callable[..., Any],
    url: str,
    headers: dict[str, str],
    body: dict,
    *,
    is_invalid_key_400: Callable[[dict], bool] | None = None,
) -> dict:
    """POST a JSON body and return the parsed JSON object.

    Every failure becomes a ProviderError raised ``from None`` so upstream
    bodies and URLs never reach logs or exception messages.
    """
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        category = _status_category(error, is_invalid_key_400)
        raise ProviderError(category) from None
    except (OSError, ValueError, http.client.HTTPException):
        raise ProviderError("provider_unavailable") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise ProviderError("invalid_output")
    try:
        value = json.loads(data)
    except ValueError:
        raise ProviderError("invalid_output") from None
    if not isinstance(value, dict):
        raise ProviderError("invalid_output")
    return value


def _status_category(
    error: HTTPError, is_invalid_key_400: Callable[[dict], bool] | None
) -> str:
    code = error.code
    if code in (401, 403):
        return "invalid_key"
    if code == 429:
        return "rate_limited"
    if code == 400 and is_invalid_key_400 is not None:
        # The body is read only to classify it and is discarded.
        try:
            body = json.loads(error.read(MAX_RESPONSE_BYTES))
            if isinstance(body, dict) and is_invalid_key_400(body):
                return "invalid_key"
        except (OSError, ValueError, http.client.HTTPException):
            pass
    return "provider_unavailable"


def parse_json_object(text: Any) -> dict:
    if not isinstance(text, str):
        raise ProviderError("invalid_output")
    try:
        value = json.loads(text)
    except ValueError:
        raise ProviderError("invalid_output") from None
    if not isinstance(value, dict):
        raise ProviderError("invalid_output")
    return value


def create_provider(provider: AiProvider) -> ExtractionProvider:
    if provider is AiProvider.OPENAI:
        from app.services.ai_providers.openai_adapter import OpenAiProvider

        return OpenAiProvider()
    if provider is AiProvider.ANTHROPIC:
        from app.services.ai_providers.anthropic_adapter import AnthropicProvider

        return AnthropicProvider()
    if provider is AiProvider.GEMINI:
        from app.services.ai_providers.gemini_adapter import GeminiProvider

        return GeminiProvider()
    raise ProviderError("provider_unavailable")
