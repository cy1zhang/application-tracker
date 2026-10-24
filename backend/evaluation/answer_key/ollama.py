"""Local Ollama extraction provider with an on-disk response cache."""

import hashlib
import json
import os
import urllib.request
from pathlib import Path

from app.domain.ai import EXTRACTION_JSON_SCHEMA
from app.services.ai_providers import ProviderError

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


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
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        result = self._call(model, prompt_input)
        self._cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._cache_dir, 0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(result))
        return result

    def _call(self, model, prompt_input) -> dict:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt_input.system},
                {"role": "user", "content": prompt_input.user},
            ],
            "stream": False,
            "format": EXTRACTION_JSON_SCHEMA,
            "think": False,
            "options": {"temperature": 0, "num_predict": 512},
        }
        request = urllib.request.Request(
            self._base_url + "/api/chat", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                content = json.loads(response.read())["message"]["content"]
            parsed = json.loads(content)
        except Exception:  # noqa: BLE001 - never surface model output
            raise ProviderError("ollama") from None
        if not isinstance(parsed, dict):
            raise ProviderError("ollama")
        return parsed
