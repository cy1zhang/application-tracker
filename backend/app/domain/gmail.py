from typing import TypedDict

from pydantic import BaseModel, ConfigDict


class EncryptedCredential(TypedDict):
    ciphertext: str
    nonce: str
    encrypted_data_key: str


class TrackerIdentity(BaseModel):
    owner_id: str
    username: str
    email: str


class GmailConnectionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connected: bool
    email: str | None
