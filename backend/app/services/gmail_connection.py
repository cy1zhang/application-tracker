import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from botocore.exceptions import ClientError

from app.domain.gmail import GmailConnectionStatus, TrackerIdentity
from app.repositories.gmail import GmailRepository, StaleGeneration
from app.services.credential_crypto import CredentialCipher, CredentialError
from app.services.google_oauth import GoogleOAuthClient, OAuthError


class GmailConnectionError(Exception):
    pass


class GmailConnectionService:
    def __init__(
        self,
        repository: GmailRepository,
        cipher: CredentialCipher,
        google: GoogleOAuthClient,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._google = google

    def status(self, identity: TrackerIdentity) -> GmailConnectionStatus:
        connection = self._repository.get_connection(identity.owner_id)
        connected = connection.email is not None and connection.encrypted_token is not None
        return GmailConnectionStatus(
            connected=connected, email=connection.email if connected else None
        )

    def connect(self, identity: TrackerIdentity) -> str:
        connection = self._repository.get_connection(identity.owner_id)
        if connection.email and connection.email.casefold() != identity.email.casefold():
            raise GmailConnectionError("Disconnect the existing mailbox first")
        state = secrets.token_urlsafe(32)
        self._repository.create_state(
            sha256(state.encode()).hexdigest(),
            identity.owner_id,
            identity.email,
            connection.generation,
            datetime.now(UTC) + timedelta(minutes=10),
        )
        return self._google.authorization_url(
            state, prompt_consent=connection.encrypted_token is None
        )

    def callback(self, state: str | None, code: str | None, error: str | None) -> bool:
        if not state:
            return False
        stored = self._repository.consume_state(sha256(state.encode()).hexdigest(), datetime.now(UTC))
        if stored is None or error or not code:
            return False
        try:
            connection = self._repository.get_connection(stored.owner_id)
            if connection.generation != stored.expected_generation:
                return False
            tokens = self._google.exchange_code(code)
            email = self._google.profile_email(tokens["access_token"])
            if email.casefold() != stored.expected_email.casefold():
                return False
            refresh_token = tokens["refresh_token"]
            if refresh_token:
                encrypted = self._cipher.encrypt_refresh_token(
                    stored.owner_id, refresh_token
                )
            elif connection.email and connection.email.casefold() == email.casefold():
                encrypted = connection.encrypted_token
            else:
                return False
            if encrypted is None:
                return False
            self._repository.save_connection(
                stored.owner_id, stored.expected_generation, email, encrypted
            )
            return True
        except (OAuthError, CredentialError, StaleGeneration, ClientError):
            return False

    def disconnect(self, identity: TrackerIdentity) -> None:
        self._repository.disconnect(identity.owner_id)
