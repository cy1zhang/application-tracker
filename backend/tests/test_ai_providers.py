import json
import logging
import traceback
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

import pytest

from app.domain.ai import EXTRACTION_JSON_SCHEMA, AiProvider
from app.services.ai_extraction import PromptInput
from app.services.ai_providers import (
    MAX_RESPONSE_BYTES,
    ProviderError,
    _NoRedirect,
    create_provider,
)
from app.services.ai_providers.openai_adapter import OpenAiProvider

KEY = "sk-synthetic-key-0123456789"
PROMPT = PromptInput(system="SYSTEM-SECRET-TEXT", user="USER-SECRET-TEXT", sections=())
EXTRACTION = {"company": "Acme", "evidence": {"company": "Acme"}}


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self._data.read(size)


class Stub:
    """Stub urlopen: returns the queued payload or raises the queued error."""

    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.requests: list[Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request, *, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        data = self.outcome if isinstance(self.outcome, bytes) else json.dumps(self.outcome).encode()
        return FakeResponse(data)

    @property
    def headers(self) -> dict[str, str]:
        return {k.lower(): v for k, v in self.requests[0].header_items()}

    @property
    def body(self) -> dict:
        return json.loads(self.requests[0].data)


def http_error(code: int, body: dict | bytes = b"") -> HTTPError:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HTTPError("https://provider.invalid/secret-path", code, "msg", {}, BytesIO(data))


def openai_ok(text: str | None = None) -> dict:
    text = json.dumps(EXTRACTION) if text is None else text
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
    }


# name -> (provider class, response builder for a valid extraction)
ADAPTERS = {"openai": (OpenAiProvider, openai_ok)}


def make(name: str, outcome) -> tuple[object, Stub]:
    stub = Stub(outcome)
    return ADAPTERS[name][0](urlopen=stub), stub


def run(provider) -> dict:
    return provider.extract(KEY, "any-model-id", PROMPT)


def category(provider) -> str:
    with pytest.raises(ProviderError) as info:
        run(provider)
    return info.value.category


COMMON_ERRORS = [
    (http_error(401), "invalid_key"),
    (http_error(403), "invalid_key"),
    (http_error(429), "rate_limited"),
    (http_error(500), "provider_unavailable"),
    (http_error(503), "provider_unavailable"),
    (http_error(404), "provider_unavailable"),
    (http_error(400, {"error": {"message": "unknown model"}}), "provider_unavailable"),
    (http_error(302), "provider_unavailable"),
    (URLError("boom"), "provider_unavailable"),
    (TimeoutError("slow"), "provider_unavailable"),
    (ConnectionResetError("reset"), "provider_unavailable"),
    (b"not json at all", "invalid_output"),
    (b"[1, 2]", "invalid_output"),
    (b"x" * (MAX_RESPONSE_BYTES + 1), "invalid_output"),
]


@pytest.mark.parametrize("name", ADAPTERS)
@pytest.mark.parametrize(("outcome", "expected"), COMMON_ERRORS)
def test_common_error_mapping(name, outcome, expected) -> None:
    provider, _ = make(name, outcome)
    assert category(provider) == expected


@pytest.mark.parametrize("name", ADAPTERS)
def test_valid_response_returns_parsed_object(name) -> None:
    provider, stub = make(name, ADAPTERS[name][1]())
    assert run(provider) == EXTRACTION
    assert stub.timeouts == [20]


@pytest.mark.parametrize("name", ADAPTERS)
def test_non_object_and_non_json_model_output_is_invalid(name) -> None:
    for text in ("not json", "[1]", "null"):
        provider, _ = make(name, ADAPTERS[name][1](text))
        assert category(provider) == "invalid_output"


@pytest.mark.parametrize("name", ADAPTERS)
def test_factory_and_default_opener(name) -> None:
    assert isinstance(create_provider(AiProvider(name)), ADAPTERS[name][0])


def test_default_opener_refuses_redirects() -> None:
    handler = _NoRedirect()
    request = Request("https://api.openai.com/v1/responses", data=b"{}")
    assert (
        handler.redirect_request(request, BytesIO(), 302, "Found", {}, "https://evil.invalid/")
        is None
    )


@pytest.mark.parametrize("name", ADAPTERS)
def test_no_secrets_in_logs_or_exceptions(name, caplog) -> None:
    secret_body = {"error": {"message": f"echo {KEY} SYSTEM-SECRET-TEXT RESPONSE-SECRET"}}
    outcomes = [
        http_error(401, secret_body),
        http_error(400, secret_body),
        http_error(429, secret_body),
        http_error(500, secret_body),
        URLError(f"failed {KEY} SYSTEM-SECRET-TEXT"),
        b"RESPONSE-SECRET not json",
        json.dumps(["RESPONSE-SECRET"]).encode(),
        ADAPTERS[name][1]("RESPONSE-SECRET"),
    ]
    caplog.set_level(logging.DEBUG)
    for outcome in outcomes:
        provider, _ = make(name, outcome)
        with pytest.raises(ProviderError) as info:
            run(provider)
        rendered = "".join(traceback.format_exception(info.value))
        for text in (str(info.value), repr(info.value), rendered):
            for secret in (KEY, "SYSTEM-SECRET-TEXT", "USER-SECRET-TEXT", "RESPONSE-SECRET"):
                assert secret not in text
    for secret in (KEY, "SYSTEM-SECRET-TEXT", "USER-SECRET-TEXT", "RESPONSE-SECRET"):
        assert secret not in caplog.text


# --- OpenAI -----------------------------------------------------------------


def test_openai_request_shape() -> None:
    provider, stub = make("openai", openai_ok())
    run(provider)
    url = urlparse(stub.requests[0].full_url)
    assert (url.scheme, url.netloc, url.path, url.query) == (
        "https", "api.openai.com", "/v1/responses", "",
    )
    assert stub.requests[0].get_method() == "POST"
    assert stub.headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in stub.requests[0].full_url
    assert KEY not in stub.requests[0].data.decode()
    body = stub.body
    assert body["model"] == "any-model-id"
    assert body["instructions"] == "SYSTEM-SECRET-TEXT"
    assert body["input"] == [{"role": "user", "content": "USER-SECRET-TEXT"}]
    assert body["max_output_tokens"] == 1024
    assert body["store"] is False
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": "application_extraction",
        "strict": True,
        "schema": EXTRACTION_JSON_SCHEMA,
    }
    assert not {"tools", "tool_choice", "functions"} & body.keys()


@pytest.mark.parametrize(
    "response",
    [
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}, "output": []},
        {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": []},
        {
            "status": "completed",
            "output": [
                {"type": "message", "content": [{"type": "refusal", "refusal": "REFUSAL-TEXT"}]}
            ],
        },
        {"status": "completed", "output": []},
        {"status": "completed", "output": [{"type": "message", "content": []}]},
        {"status": "completed"},
        {"status": "completed", "output": "oops"},
        {"status": "cancelled", "output": []},
    ],
)
def test_openai_unusable_generation_is_invalid_output(response) -> None:
    provider, _ = make("openai", response)
    assert category(provider) == "invalid_output"


def test_openai_failed_status_is_unavailable() -> None:
    provider, _ = make("openai", {"status": "failed", "error": {"message": "x"}})
    assert category(provider) == "provider_unavailable"
