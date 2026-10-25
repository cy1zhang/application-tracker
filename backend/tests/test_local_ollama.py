import socket
import urllib.error

import pytest

from local_tools import ollama
from local_tools.ollama import OllamaClient, OllamaFailed, OllamaUnreachable


@pytest.mark.parametrize(
    "error,expected",
    [
        (urllib.error.URLError(TimeoutError()), OllamaFailed),
        (TimeoutError(), OllamaFailed),
        (urllib.error.URLError(ConnectionRefusedError()), OllamaUnreachable),
        (urllib.error.URLError(socket.gaierror()), OllamaUnreachable),
    ],
)
def test_chat_classifies_transport_errors(monkeypatch, error, expected):
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(ollama.urllib.request, "urlopen", boom)
    with pytest.raises(expected) as caught:
        OllamaClient().chat("s", "u", {})
    if expected is OllamaFailed:
        assert caught.value.kind == "timeout"
