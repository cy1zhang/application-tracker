from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from app.domain.ai import EXTRACTION_JSON_SCHEMA
from app.services.ai_extraction import PromptInput
from app.services.ai_providers import (
    MAX_OUTPUT_TOKENS,
    ProviderError,
    default_urlopen,
    parse_json_object,
    post_json,
)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def _is_invalid_key(body: dict) -> bool:
    # Gemini reports a bad key as HTTP 400 INVALID_ARGUMENT with an ErrorInfo
    # detail whose reason is API_KEY_INVALID.
    details = body.get("error", {}).get("details", [])
    return isinstance(details, list) and any(
        isinstance(item, dict) and item.get("reason") == "API_KEY_INVALID"
        for item in details
    )


class GeminiProvider:
    def __init__(self, *, urlopen: Callable[..., Any] = default_urlopen) -> None:
        self._urlopen = urlopen

    def extract(self, api_key: str, model: str, prompt_input: PromptInput) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": prompt_input.system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt_input.user}]}],
            "generationConfig": {
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseJsonSchema": EXTRACTION_JSON_SCHEMA,
            },
        }
        response = post_json(
            self._urlopen,
            f"{BASE_URL}/{quote(model, safe='')}:generateContent",
            {"x-goog-api-key": api_key},
            body,
            is_invalid_key_400=_is_invalid_key,
        )
        return parse_json_object(_output_text(response))


def _output_text(response: dict) -> str:
    if response.get("promptFeedback", {}).get("blockReason"):
        raise ProviderError("invalid_output")
    try:
        candidates = response["candidates"]
        if len(candidates) != 1:
            raise ProviderError("invalid_output")
        candidate = candidates[0]
        # SAFETY, RECITATION, MAX_TOKENS, MALFORMED_RESPONSE, etc. are unusable.
        if candidate.get("finishReason") != "STOP":
            raise ProviderError("invalid_output")
        texts = [
            part["text"]
            for part in candidate["content"]["parts"]
            if "text" in part and not part.get("thought")
        ]
    except (KeyError, TypeError, AttributeError):
        raise ProviderError("invalid_output") from None
    if not texts:
        raise ProviderError("invalid_output")
    return "".join(texts)
