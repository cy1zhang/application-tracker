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

URL = "https://router.huggingface.co/v1/chat/completions"
ROUTING_POLICIES = {"fastest", "cheapest", "preferred"}


class HuggingFaceProvider:
    def __init__(self, *, urlopen: Callable[..., Any] = default_urlopen) -> None:
        self._urlopen = urlopen

    def extract(self, api_key: str, model: str, prompt_input: PromptInput) -> dict:
        model_id, separator, provider = model.rpartition(":")
        if not separator or not model_id or not provider or provider in ROUTING_POLICIES:
            raise ProviderError("provider_unavailable")
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt_input.system},
                {"role": "user", "content": prompt_input.user},
            ],
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "application_extraction",
                    "strict": True,
                    "schema": EXTRACTION_JSON_SCHEMA,
                },
            },
        }
        response = post_json(
            self._urlopen, URL, {"Authorization": f"Bearer {api_key}"}, body
        )
        try:
            choices = response["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ProviderError("invalid_output")
            choice = choices[0]
            if choice["finish_reason"] != "stop":
                raise ProviderError("invalid_output")
            message = choice["message"]
            if message.get("refusal"):
                raise ProviderError("invalid_output")
            content = message["content"]
        except (KeyError, TypeError, AttributeError):
            raise ProviderError("invalid_output") from None
        return parse_json_object(content)
