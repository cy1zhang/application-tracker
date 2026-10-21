import os
from base64 import b64decode, b64encode
from binascii import Error as Base64Error

from botocore.exceptions import BotoCoreError, ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.gmail import EncryptedCredential

GMAIL_REFRESH_TOKEN = "gmail_refresh_token"
AI_API_KEY = "ai_api_key"


class CredentialError(Exception):
    pass


CRYPTO_FAILURES = (
    BotoCoreError,
    ClientError,
    InvalidTag,
    Base64Error,
    ValueError,
    KeyError,
    TypeError,
    UnicodeError,
)


class CredentialCipher:
    def __init__(self, kms_client, key_id: str) -> None:
        self._kms = kms_client
        self._key_id = key_id

    def encrypt_refresh_token(
        self, owner_id: str, plaintext: str
    ) -> EncryptedCredential:
        return self.encrypt(owner_id, GMAIL_REFRESH_TOKEN, plaintext)

    def decrypt_refresh_token(
        self, owner_id: str, encrypted: EncryptedCredential
    ) -> str:
        return self.decrypt(owner_id, GMAIL_REFRESH_TOKEN, encrypted)

    def encrypt(
        self, owner_id: str, credential_type: str, plaintext: str
    ) -> EncryptedCredential:
        try:
            response = self._kms.generate_data_key(
                KeyId=self._key_id,
                KeySpec="AES_256",
                EncryptionContext=self._context(owner_id, credential_type),
            )
            nonce = os.urandom(12)
            ciphertext = AESGCM(response["Plaintext"]).encrypt(
                nonce,
                plaintext.encode(),
                self._associated_data(owner_id, credential_type),
            )
            return {
                "ciphertext": b64encode(ciphertext).decode(),
                "nonce": b64encode(nonce).decode(),
                "encrypted_data_key": b64encode(response["CiphertextBlob"]).decode(),
            }
        except CRYPTO_FAILURES:
            raise CredentialError("Could not protect credential") from None

    def decrypt(
        self, owner_id: str, credential_type: str, encrypted: EncryptedCredential
    ) -> str:
        try:
            key = self._kms.decrypt(
                KeyId=self._key_id,
                CiphertextBlob=b64decode(encrypted["encrypted_data_key"], validate=True),
                EncryptionContext=self._context(owner_id, credential_type),
            )["Plaintext"]
            plaintext = AESGCM(key).decrypt(
                b64decode(encrypted["nonce"], validate=True),
                b64decode(encrypted["ciphertext"], validate=True),
                self._associated_data(owner_id, credential_type),
            )
            return plaintext.decode()
        except CRYPTO_FAILURES:
            raise CredentialError("Could not read credential") from None

    @staticmethod
    def _context(owner_id: str, credential_type: str) -> dict[str, str]:
        return {"owner_id": owner_id, "credential_type": credential_type}

    @staticmethod
    def _associated_data(owner_id: str, credential_type: str) -> bytes:
        return f"{credential_type}:{owner_id}".encode()
