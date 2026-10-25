"""Minimal local Ollama chat client; errors carry only a category, never text."""

import json
import socket
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class OllamaUnreachable(Exception):
    """Ollama did not accept a connection."""


class OllamaFailed(Exception):
    """Ollama answered badly; `kind` is one of timeout, http, invalid_json."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class OllamaClient:
    def __init__(
        self, base_url: str = DEFAULT_BASE_URL, model: str = "gemma4:latest",
        timeout: float = 300,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    def chat(self, system: str, user: str, format: dict) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": format,
            "think": False,
            "options": {"temperature": 0, "num_predict": 512},
        }
        request = urllib.request.Request(
            self.base_url + "/api/chat", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                content = json.loads(response.read())["message"]["content"]
            parsed = json.loads(content)
        except urllib.error.HTTPError:
            raise OllamaFailed("http") from None
        except TimeoutError:
            raise OllamaFailed("timeout") from None
        except urllib.error.URLError as error:
            reason = error.reason
            if isinstance(reason, TimeoutError):
                raise OllamaFailed("timeout") from None
            if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
                raise OllamaUnreachable from None
            raise OllamaFailed("http") from None
        except ConnectionRefusedError:
            raise OllamaUnreachable from None
        except ConnectionError:
            raise OllamaFailed("http") from None
        except Exception:  # noqa: BLE001 - never surface model output
            raise OllamaFailed("invalid_json") from None
        if not isinstance(parsed, dict):
            raise OllamaFailed("invalid_json")
        return parsed

    def reachable(self, retries: int = 3, delay: float = 30) -> bool:
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(
                    self.base_url + "/api/version", timeout=10
                ):
                    return True
            except Exception:  # noqa: BLE001
                if attempt < retries - 1:
                    time.sleep(delay)
        return False
