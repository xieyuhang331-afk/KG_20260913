from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class IdentitySubmissionCryptoUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedIdentityValue:
    ciphertext: bytes
    nonce: bytes


class IdentitySubmissionCrypto:
    def __init__(self, *, encryption_key: bytes, hmac_key: bytes, key_id: str) -> None:
        if len(encryption_key) != 32 or len(hmac_key) < 32:
            raise IdentitySubmissionCryptoUnavailable("identity PII crypto is unavailable")
        if hmac.compare_digest(encryption_key, hmac_key[:32]):
            raise IdentitySubmissionCryptoUnavailable("identity PII crypto is unavailable")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", key_id):
            raise IdentitySubmissionCryptoUnavailable("identity PII crypto is unavailable")
        self._encryption_key = bytes(encryption_key)
        self._hmac_key = bytes(hmac_key)
        self.key_id = key_id

    @classmethod
    def from_environment(cls) -> "IdentitySubmissionCrypto":
        try:
            encryption_key = base64.b64decode(
                os.environ["KG_IDENTITY_PII_KEK_B64"], validate=True
            )
            hmac_key = base64.b64decode(
                os.environ["KG_IDENTITY_PII_HMAC_KEY_B64"], validate=True
            )
            key_id = os.environ["KG_IDENTITY_PII_KEY_ID"]
            return cls(
                encryption_key=encryption_key,
                hmac_key=hmac_key,
                key_id=key_id,
            )
        except (KeyError, ValueError, TypeError):
            raise IdentitySubmissionCryptoUnavailable(
                "identity PII crypto is unavailable"
            ) from None

    @staticmethod
    def aad(*, submission_id: str, user_ref: int, version: int, field: str, key_id: str) -> bytes:
        return f"identity-submission:v1:{submission_id}:{user_ref}:{version}:{field}:{key_id}".encode()

    def encrypt(self, plaintext: str, *, aad: bytes) -> EncryptedIdentityValue:
        nonce = os.urandom(12)
        return EncryptedIdentityValue(
            ciphertext=AESGCM(self._encryption_key).encrypt(nonce, plaintext.encode(), aad),
            nonce=nonce,
        )

    def decrypt(self, value: EncryptedIdentityValue, *, aad: bytes) -> str:
        try:
            return AESGCM(self._encryption_key).decrypt(
                value.nonce, value.ciphertext, aad
            ).decode()
        except Exception:
            raise IdentitySubmissionCryptoUnavailable(
                "identity PII crypto is unavailable"
            ) from None

    def digest(self, domain: str, value: str) -> str:
        return hmac.new(
            self._hmac_key,
            f"identity-submission:{domain}:v1:{value}".encode(),
            hashlib.sha256,
        ).hexdigest()


def mask_id_card(id_card: str) -> str:
    return f"{id_card[:6]}********{id_card[-4:]}"
