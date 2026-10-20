from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.domain.gmail import EncryptedCredential


class StaleGeneration(Exception):
    pass


@dataclass(frozen=True)
class OAuthState:
    owner_id: str
    expected_email: str
    expected_generation: int
    expires_at: datetime


@dataclass(frozen=True)
class GmailConnection:
    generation: int
    email: str | None
    encrypted_token: EncryptedCredential | None
    reconnect_required: bool = False


class GmailRepository(Protocol):
    def create_state(
        self,
        state_hash: str,
        owner_id: str,
        expected_email: str,
        expected_generation: int,
        expires_at: datetime,
    ) -> None: ...

    def consume_state(self, state_hash: str, now: datetime) -> OAuthState | None: ...

    def get_connection(self, owner_id: str) -> GmailConnection: ...

    def save_connection(
        self,
        owner_id: str,
        expected_generation: int,
        email: str,
        encrypted_token: EncryptedCredential,
        connected_at: datetime,
    ) -> None: ...

    def disconnect(self, owner_id: str) -> None: ...

    def get_active(
        self, owner_id: str, expected_generation: int
    ) -> EncryptedCredential | None: ...
