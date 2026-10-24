"""Settings for local tools, read from a git-ignored env file."""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"


@dataclass(frozen=True)
class AuthConfig:
    api_base_url: str
    cognito_domain: str
    client_id: str
    redirect_uri: str = DEFAULT_REDIRECT_URI


def load_config(path: str | Path = ".env.local-tools") -> AuthConfig:
    values: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    try:
        return AuthConfig(
            api_base_url=values["TRACKER_API_URL"].rstrip("/"),
            cognito_domain=values["COGNITO_DOMAIN"].rstrip("/"),
            client_id=values["COGNITO_CLIENT_ID"],
        )
    except KeyError as error:
        raise ValueError(f"missing setting {error.args[0]} in {path}") from None
