import os
from base64 import b64decode, b64encode
from binascii import Error as Base64Error

from botocore.exceptions import BotoCoreError, ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.gmail import EncryptedCredential


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
        try:
            response = self._kms.generate_data_key(
                KeyId=self._key_id,
                KeySpec="AES_256",
                EncryptionContext=self._context(owner_id),
            )
            nonce = os.urandom(12)
            ciphertext = AESGCM(response["Plaintext"]).encrypt(
                nonce, plaintext.encode(), self._associated_data(owner_id)
            )
            return {
                "ciphertext": b64encode(ciphertext).decode(),
                "nonce": b64encode(nonce).decode(),
                "encrypted_data_key": b64encode(response["CiphertextBlob"]).decode(),
            }
        except CRYPTO_FAILURES:
            raise CredentialError("Could not protect Gmail credential") from None

    def decrypt_refresh_token(
        self, owner_id: str, encrypted: EncryptedCredential
    ) -> str:
        try:
            key = self._kms.decrypt(
                KeyId=self._key_id,
                CiphertextBlob=b64decode(encrypted["encrypted_data_key"], validate=True),
                EncryptionContext=self._context(owner_id),
            )["Plaintext"]
            plaintext = AESGCM(key).decrypt(
                b64decode(encrypted["nonce"], validate=True),
                b64decode(encrypted["ciphertext"], validate=True),
                self._associated_data(owner_id),
            )
            return plaintext.decode()
        except CRYPTO_FAILURES:
            raise CredentialError("Could not read Gmail credential") from None

    @staticmethod
    def _context(owner_id: str) -> dict[str, str]:
        return {"owner_id": owner_id, "credential_type": "gmail_refresh_token"}

    @staticmethod
    def _associated_data(owner_id: str) -> bytes:
        return f"gmail_refresh_token:{owner_id}".encode()
