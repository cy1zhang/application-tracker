"""Local Ollama extraction provider with an on-disk response cache."""

import hashlib
import json
import os
from pathlib import Path

from app.domain.ai import EXTRACTION_JSON_SCHEMA
from app.services.ai_providers import ProviderError
from local_tools.ollama import (
    DEFAULT_BASE_URL,
    OllamaClient,
    OllamaFailed,
    OllamaUnreachable,
)


class OllamaProvider:
    def __init__(
        self, model: str = "gemma4:latest", *, cache_dir: Path,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self.model = model
        self._cache_dir = Path(cache_dir)
        self._base_url = base_url

    def extract(self, api_key, model, prompt_input) -> dict:
        digest = hashlib.sha256(
            (prompt_input.system + prompt_input.user + model).encode()
        ).hexdigest()
        path = self._cache_dir / f"{digest}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cached, dict):
                return cached
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            pass
        # A damaged entry is a cache miss: drop it and ask Ollama again.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        result = self._call(model, prompt_input)
        self._cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._cache_dir, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(result))
        return result

    def _call(self, model, prompt_input) -> dict:
        client = OllamaClient(self._base_url, model)
        try:
            return client.chat(
                prompt_input.system, prompt_input.user, EXTRACTION_JSON_SCHEMA
            )
        except (OllamaUnreachable, OllamaFailed):
            raise ProviderError("ollama") from None
