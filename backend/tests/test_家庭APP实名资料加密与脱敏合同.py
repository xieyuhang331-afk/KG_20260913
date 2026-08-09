from __future__ import annotations

import base64

import pytest


def _crypto():
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCrypto

    return IdentitySubmissionCrypto(
        encryption_key=b"E" * 32, hmac_key=b"H" * 32, key_id="identity-kek-v1"
    )


def test_AES256GCM使用唯一nonce和受控AAD且可往返() -> None:
    crypto = _crypto()
    aad = crypto.aad(
        submission_id="0198a2ef-1234-7abc-8def-0123456789ab",
        user_ref=42, version=1, field="id_card", key_id=crypto.key_id,
    )
    first = crypto.encrypt("11010519491231002X", aad=aad)
    second = crypto.encrypt("11010519491231002X", aad=aad)
    assert len(first.nonce) == 12
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
    assert crypto.decrypt(first, aad=aad) == "11010519491231002X"
    with pytest.raises(Exception):
        crypto.decrypt(first, aad=aad + b":tampered")


def test_密钥缺失无效或复用必须fail_closed(monkeypatch) -> None:
    from app.modules.auth.identity_submission_crypto import (
        IdentitySubmissionCrypto,
        IdentitySubmissionCryptoUnavailable,
    )

    for name in (
        "KG_IDENTITY_PII_KEK_B64",
        "KG_IDENTITY_PII_HMAC_KEY_B64",
        "KG_IDENTITY_PII_KEY_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(IdentitySubmissionCryptoUnavailable):
        IdentitySubmissionCrypto.from_environment()

    encoded = base64.b64encode(b"S" * 32).decode()
    monkeypatch.setenv("KG_IDENTITY_PII_KEK_B64", encoded)
    monkeypatch.setenv("KG_IDENTITY_PII_HMAC_KEY_B64", encoded)
    monkeypatch.setenv("KG_IDENTITY_PII_KEY_ID", "v1")
    with pytest.raises(IdentitySubmissionCryptoUnavailable):
        IdentitySubmissionCrypto.from_environment()


def test_普通输出只允许固定脱敏身份证号() -> None:
    from app.modules.auth.identity_submission_crypto import mask_id_card

    masked = mask_id_card("11010519491231002X")
    assert masked == "110105********002X"
    assert "19491231" not in masked
