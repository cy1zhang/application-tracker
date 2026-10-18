import pytest
from pydantic import ValidationError

from app.domain.gmail import GmailConnectionStatus


def test_status_does_not_accept_credential_fields() -> None:
    with pytest.raises(ValidationError):
        GmailConnectionStatus(
            connected=True,
            email="alex@example.test",
            refresh_token="must-not-appear",
        )
