import base64
import json
from uuid import UUID

import pytest

from app.modules.therapist_qualification.service import TherapistSecrets


KEY_DOMAINS = (
    ("KG_THERAPIST_PII_ENCRYPTION_CURRENT_KEY_ID", "KG_THERAPIST_PII_ENCRYPTION_KEYRING_JSON"),
    ("KG_THERAPIST_PII_DIGEST_CURRENT_KEY_ID", "KG_THERAPIST_PII_DIGEST_KEYRING_JSON"),
    ("KG_THERAPIST_TOTP_ENCRYPTION_CURRENT_KEY_ID", "KG_THERAPIST_TOTP_ENCRYPTION_KEYRING_JSON"),
    ("KG_THERAPIST_INVITATION_CODE_HMAC_CURRENT_KEY_ID", "KG_THERAPIST_INVITATION_CODE_HMAC_KEYRING_JSON"),
    ("KG_THERAPIST_REPLAY_ENCRYPTION_CURRENT_KEY_ID", "KG_THERAPIST_REPLAY_ENCRYPTION_KEYRING_JSON"),
    ("KG_THERAPIST_READINESS_DIGEST_CURRENT_KEY_ID", "KG_THERAPIST_READINESS_DIGEST_KEYRING_JSON"),
    ("KG_THERAPIST_DELIVERY_TARGET_HMAC_CURRENT_KEY_ID", "KG_THERAPIST_DELIVERY_TARGET_HMAC_KEYRING_JSON"),
)


def _configure(monkeypatch) -> None:
    for index, (current_name, keyring_name) in enumerate(KEY_DOMAINS, start=1):
        key_id = f"k{index}"
        encoded = base64.b64encode(bytes([index]) * 32).decode()
        monkeypatch.setenv(current_name, key_id)
        monkeypatch.setenv(keyring_name, json.dumps({key_id: encoded}))


def test_PII_TOTP逐字段AAD_stored_key与跨对象移植拒绝(monkeypatch):
    _configure(monkeypatch)
    secrets = TherapistSecrets()
    tenant_public_id = UUID("00000000-0000-7000-8000-000000000001")
    object_id = UUID("00000000-0000-7000-8000-000000000002")
    ciphertext = secrets.encrypt_pii(
        "13800000000",
        field="invitation-phone",
        tenant_public_id=tenant_public_id,
        object_id=object_id,
    )
    assert secrets.decrypt_pii(
        ciphertext,
        secrets.pii_key_id,
        field="invitation-phone",
        tenant_public_id=tenant_public_id,
        object_id=object_id,
    ) == "13800000000"
    with pytest.raises(RuntimeError, match="unavailable"):
        secrets.decrypt_pii(
            ciphertext,
            secrets.pii_key_id,
            field="profile-real-name",
            tenant_public_id=tenant_public_id,
            object_id=object_id,
        )

    totp = secrets.encrypt_totp("JBSWY3DPEHPK3PXP", tenant_public_id, object_id)
    assert secrets.decrypt_totp(totp, secrets.totp_key_id, tenant_public_id, object_id) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(RuntimeError, match="unavailable"):
        secrets.decrypt_totp(totp, secrets.totp_key_id, tenant_public_id, UUID(int=3))


def test_keyring重复kid与跨域key_material均fail_closed(monkeypatch):
    _configure(monkeypatch)
    duplicated = base64.b64encode(bytes([1]) * 32).decode()
    monkeypatch.setenv("KG_THERAPIST_PII_ENCRYPTION_KEYRING_JSON", f'{{"k1":"{duplicated}","k1":"{duplicated}"}}')
    with pytest.raises(RuntimeError, match="configuration"):
        TherapistSecrets()

    _configure(monkeypatch)
    monkeypatch.setenv(
        "KG_THERAPIST_PII_DIGEST_KEYRING_JSON",
        json.dumps({"k2": duplicated}),
    )
    with pytest.raises(RuntimeError, match="configuration"):
        TherapistSecrets()
