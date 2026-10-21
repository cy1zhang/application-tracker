from collections.abc import Callable
from typing import Any

from app.domain.ai import EXTRACTION_JSON_SCHEMA
from app.services.ai_extraction import PromptInput
from app.services.ai_providers import (
    MAX_OUTPUT_TOKENS,
    ProviderError,
    default_urlopen,
    parse_json_object,
    post_json,
)

URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
# The Messages API rejects numeric bounds in structured-output schemas with a
# 400. ExtractionOutput re-validates the year range after the response.
_UNSUPPORTED_KEYWORDS = frozenset({"minimum", "maximum"})


def _without_unsupported(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {
            key: _without_unsupported(value)
            for key, value in schema.items()
            if key not in _UNSUPPORTED_KEYWORDS
        }
    if isinstance(schema, list):
        return [_without_unsupported(item) for item in schema]
    return schema


class AnthropicProvider:
    def __init__(self, *, urlopen: Callable[..., Any] = default_urlopen) -> None:
        self._urlopen = urlopen

    def extract(self, api_key: str, model: str, prompt_input: PromptInput) -> dict:
        body = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": prompt_input.system,
            "messages": [{"role": "user", "content": prompt_input.user}],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": _without_unsupported(EXTRACTION_JSON_SCHEMA),
                }
            },
        }
        response = post_json(
            self._urlopen,
            URL,
            {"x-api-key": api_key, "anthropic-version": API_VERSION},
            body,
        )
        return parse_json_object(_output_text(response))


def _output_text(response: dict) -> str:
    # refusal (HTTP 200) and max_tokens can both produce text that does not
    # match the schema, so only end_turn is trusted.
    if response.get("stop_reason") != "end_turn":
        raise ProviderError("invalid_output")
    try:
        texts = [
            block["text"] for block in response["content"] if block.get("type") == "text"
        ]
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("invalid_output") from None
    if len(texts) != 1:
        raise ProviderError("invalid_output")
    return texts[0]
