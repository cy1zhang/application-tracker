import os
from base64 import b64decode, b64encode

import pytest

from app.services.credential_crypto import CredentialCipher, CredentialError


class FakeKms:
    def __init__(self) -> None:
        self.keys: dict[bytes, tuple[bytes, dict[str, str]]] = {}

    def generate_data_key(self, *, KeyId, KeySpec, EncryptionContext):
        assert KeyId == "test-key"
        assert KeySpec == "AES_256"
        key = os.urandom(32)
        wrapped = os.urandom(32)
        self.keys[wrapped] = (key, EncryptionContext)
        return {"Plaintext": key, "CiphertextBlob": wrapped}

    def decrypt(self, *, CiphertextBlob, EncryptionContext, KeyId):
        assert KeyId == "test-key"
        key, context = self.keys[CiphertextBlob]
        if context != EncryptionContext:
            raise ValueError("encryption context mismatch")
        return {"Plaintext": key}


def test_refresh_token_round_trip() -> None:
    cipher = CredentialCipher(FakeKms(), "test-key")

    encrypted = cipher.encrypt_refresh_token("owner-a", "synthetic-refresh-token")

    assert set(encrypted) == {"ciphertext", "nonce", "encrypted_data_key"}
    assert "synthetic-refresh-token" not in str(encrypted)
    assert cipher.decrypt_refresh_token("owner-a", encrypted) == "synthetic-refresh-token"


def test_wrong_owner_cannot_decrypt() -> None:
    cipher = CredentialCipher(FakeKms(), "test-key")
    encrypted = cipher.encrypt_refresh_token("owner-a", "synthetic-refresh-token")

    with pytest.raises(CredentialError):
        cipher.decrypt_refresh_token("owner-b", encrypted)


def test_tampered_ciphertext_fails() -> None:
    cipher = CredentialCipher(FakeKms(), "test-key")
    encrypted = cipher.encrypt_refresh_token("owner-a", "synthetic-refresh-token")
    modified = bytearray(b64decode(encrypted["ciphertext"]))
    modified[0] ^= 1
    encrypted["ciphertext"] = b64encode(modified).decode()

    with pytest.raises(CredentialError):
        cipher.decrypt_refresh_token("owner-a", encrypted)


def test_kms_failure_has_no_secret_in_error() -> None:
    class FailingKms:
        def generate_data_key(self, **_kwargs):
            raise ValueError("synthetic-refresh-token provider failure")

    cipher = CredentialCipher(FailingKms(), "test-key")

    with pytest.raises(CredentialError) as error:
        cipher.encrypt_refresh_token("owner-a", "synthetic-refresh-token")

    assert "synthetic-refresh-token" not in str(error.value)
    assert error.value.__cause__ is None


def test_ai_api_key_round_trip_and_type_binding() -> None:
    cipher = CredentialCipher(FakeKms(), "test-key")

    encrypted = cipher.encrypt("owner-a", "ai_api_key", "synthetic-ai-key")

    assert "synthetic-ai-key" not in str(encrypted)
    assert cipher.decrypt("owner-a", "ai_api_key", encrypted) == "synthetic-ai-key"
    with pytest.raises(CredentialError):
        cipher.decrypt_refresh_token("owner-a", encrypted)
    with pytest.raises(CredentialError):
        cipher.decrypt("owner-b", "ai_api_key", encrypted)


def test_ai_ciphertext_with_tampered_data_fails() -> None:
    cipher = CredentialCipher(FakeKms(), "test-key")
    encrypted = cipher.encrypt("owner-a", "ai_api_key", "synthetic-ai-key")
    modified = bytearray(b64decode(encrypted["ciphertext"]))
    modified[0] ^= 1
    encrypted["ciphertext"] = b64encode(modified).decode()

    with pytest.raises(CredentialError):
        cipher.decrypt("owner-a", "ai_api_key", encrypted)


def test_gmail_type_cannot_decrypt_ai_ciphertext_with_matching_kms_context() -> None:
    """Even if KMS accepted the context, the AES-GCM associated data binds the type."""

    class LenientKms(FakeKms):
        def decrypt(self, *, CiphertextBlob, EncryptionContext, KeyId):
            return {"Plaintext": self.keys[CiphertextBlob][0]}

    cipher = CredentialCipher(LenientKms(), "test-key")
    encrypted = cipher.encrypt("owner-a", "ai_api_key", "synthetic-ai-key")

    with pytest.raises(CredentialError):
        cipher.decrypt("owner-a", "gmail_refresh_token", encrypted)


def test_gmail_wrapper_keeps_the_existing_context_and_associated_data() -> None:
    kms = FakeKms()
    cipher = CredentialCipher(kms, "test-key")

    encrypted = cipher.encrypt_refresh_token("owner-a", "synthetic-refresh-token")

    (_, context), = kms.keys.values()
    assert context == {"owner_id": "owner-a", "credential_type": "gmail_refresh_token"}
    assert cipher.decrypt("owner-a", "gmail_refresh_token", encrypted) == (
        "synthetic-refresh-token"
    )
