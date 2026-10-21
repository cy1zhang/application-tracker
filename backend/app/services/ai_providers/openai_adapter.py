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

URL = "https://api.openai.com/v1/responses"


class OpenAiProvider:
    def __init__(self, *, urlopen: Callable[..., Any] = default_urlopen) -> None:
        self._urlopen = urlopen

    def extract(self, api_key: str, model: str, prompt_input: PromptInput) -> dict:
        body = {
            "model": model,
            "instructions": prompt_input.system,
            "input": [{"role": "user", "content": prompt_input.user}],
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "application_extraction",
                    "strict": True,
                    "schema": EXTRACTION_JSON_SCHEMA,
                }
            },
        }
        response = post_json(
            self._urlopen, URL, {"Authorization": f"Bearer {api_key}"}, body
        )
        return parse_json_object(_output_text(response))


def _output_text(response: dict) -> str:
    status = response.get("status")
    if status == "failed":
        raise ProviderError("provider_unavailable")
    if status != "completed":  # incomplete, cancelled, queued, in_progress
        raise ProviderError("invalid_output")
    try:
        texts = []
        for item in response["output"]:
            if item.get("type") != "message":
                continue
            for part in item["content"]:
                if part.get("type") == "refusal":
                    raise ProviderError("invalid_output")
                if part.get("type") == "output_text":
                    texts.append(part["text"])
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("invalid_output") from None
    if len(texts) != 1:
        raise ProviderError("invalid_output")
    return texts[0]
