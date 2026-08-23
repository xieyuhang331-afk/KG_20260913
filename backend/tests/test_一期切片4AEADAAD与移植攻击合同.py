import base64
import json
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


DOMAINS = (
    "PROFILE_PHI",
    "ASSEMBLY_PHI",
    "REQUEST_DIGEST",
    "AUDIT_DIGEST",
    "OUTBOX_DIGEST",
    "REPLAY_DIGEST",
    "DELIVERY",
    "COORDINATION",
)


def _secrets(monkeypatch):
    for index, domain in enumerate(DOMAINS, start=1):
        key_id = f"slice4-{index}"
        material = base64.b64encode(bytes([index]) * 32).decode()
        monkeypatch.setenv(f"KG_SLICE4_{domain}_CURRENT_KEY_ID", key_id)
        monkeypatch.setenv(
            f"KG_SLICE4_{domain}_KEYRING_JSON",
            json.dumps({key_id: material}),
        )
    from app.modules.user_health.service import Slice4Secrets

    return Slice4Secrets()


def _uuid(suffix: int) -> UUID:
    return UUID(f"0198f1c0-0000-7000-8000-{suffix:012d}")


def test_D49_Profile_AAD绑定tenant_member_revision_source_version及stored_key(monkeypatch) -> None:
    secrets = _secrets(monkeypatch)
    tenant = UUID("0198f1c0-0000-7000-8000-000000000001")
    member = UUID("0198f1c0-0000-7000-8000-000000000002")
    revision = UUID("0198f1c0-0000-7000-8000-000000000003")
    ciphertext, key_id = secrets.encrypt_profile(
        {"medical_history": []},
        tenant_public_id=tenant,
        subject_member_id=member,
        revision_id=revision,
        identity_source_version=3,
    )
    kwargs = {
        "tenant_public_id": tenant,
        "subject_member_id": member,
        "revision_id": revision,
        "identity_source_version": 3,
    }
    assert secrets.profile_aad(key_id=key_id, **kwargs) == (
        b'{"domain":"slice4.profile.snapshot.v1","identity_source_version":3,'
        b'"key_id":"slice4-1","profile_revision_id":"0198f1c0-0000-7000-8000-000000000003",'
        b'"subject_member_id":"0198f1c0-0000-7000-8000-000000000002",'
        b'"tenant_public_id":"0198f1c0-0000-7000-8000-000000000001"}'
    )
    assert secrets.decrypt_profile(ciphertext, key_id, **kwargs) == {"medical_history": []}
    mutations = (
        {"tenant_public_id": UUID("0198f1c0-0000-7000-8000-000000000011")},
        {"subject_member_id": UUID("0198f1c0-0000-7000-8000-000000000012")},
        {"revision_id": UUID("0198f1c0-0000-7000-8000-000000000013")},
        {"identity_source_version": 4},
    )
    for mutation in mutations:
        with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
            secrets.decrypt_profile(ciphertext, key_id, **{**kwargs, **mutation})
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt_profile(ciphertext, "missing-key", **kwargs)
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt_profile(tampered, key_id, **kwargs)


def test_Profile_retained_old_key可解且错误有效key_id拒绝(monkeypatch) -> None:
    old_material = b"o" * 32
    current_material = b"n" * 32
    for index, domain in enumerate(DOMAINS, start=1):
        key_id = f"slice4-{index}"
        ring = {key_id: base64.b64encode(bytes([index]) * 32).decode()}
        if domain == "PROFILE_PHI":
            key_id = "profile-v2"
            ring = {
                "profile-v1": base64.b64encode(old_material).decode(),
                "profile-v2": base64.b64encode(current_material).decode(),
            }
        monkeypatch.setenv(f"KG_SLICE4_{domain}_CURRENT_KEY_ID", key_id)
        monkeypatch.setenv(f"KG_SLICE4_{domain}_KEYRING_JSON", json.dumps(ring))
    from app.modules.user_health.service import Slice4Secrets

    secrets = Slice4Secrets()
    kwargs = {
        "tenant_public_id": _uuid(41),
        "subject_member_id": _uuid(42),
        "revision_id": _uuid(43),
        "identity_source_version": 2,
    }
    nonce = b"0" * 12
    aad = secrets.profile_aad(key_id="profile-v1", **kwargs)
    ciphertext = nonce + AESGCM(old_material).encrypt(nonce, b'{"value":"old"}', aad)
    assert secrets.decrypt_profile(ciphertext, "profile-v1", **kwargs) == {"value": "old"}
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt_profile(ciphertext, "profile-v2", **kwargs)


def test_D50_Assembly_AAD逐leaf绑定且与Profile域隔离(monkeypatch) -> None:
    secrets = _secrets(monkeypatch)
    kwargs = {
        "tenant_public_id": UUID("0198f1c0-0000-7000-8000-000000000021"),
        "subject_member_id": UUID("0198f1c0-0000-7000-8000-000000000022"),
        "service_case_id": UUID("0198f1c0-0000-7000-8000-000000000023"),
        "assembly_id": UUID("0198f1c0-0000-7000-8000-000000000024"),
        "fact_ref": UUID("0198f1c0-0000-7000-8000-000000000025"),
        "indicator_code": "weight",
    }
    ciphertext, key_id = secrets.encrypt_assembly_fact({"value": "70.0"}, **kwargs)
    assert secrets.assembly_fact_aad(key_id=key_id, **kwargs) == (
        b'{"assembly_id":"0198f1c0-0000-7000-8000-000000000024",'
        b'"domain":"slice4.assembly.fact.v1",'
        b'"fact_ref":"0198f1c0-0000-7000-8000-000000000025",'
        b'"indicator_code":"weight","key_id":"slice4-2",'
        b'"service_case_id":"0198f1c0-0000-7000-8000-000000000023",'
        b'"subject_member_id":"0198f1c0-0000-7000-8000-000000000022",'
        b'"tenant_public_id":"0198f1c0-0000-7000-8000-000000000021"}'
    )
    assert secrets.decrypt_assembly_fact(ciphertext, key_id, **kwargs) == {"value": "70.0"}
    mutations = {
        "tenant_public_id": UUID("0198f1c0-0000-7000-8000-000000000031"),
        "subject_member_id": UUID("0198f1c0-0000-7000-8000-000000000032"),
        "service_case_id": UUID("0198f1c0-0000-7000-8000-000000000033"),
        "assembly_id": UUID("0198f1c0-0000-7000-8000-000000000034"),
        "fact_ref": UUID("0198f1c0-0000-7000-8000-000000000035"),
        "indicator_code": "height",
    }
    for key, value in mutations.items():
        with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
            secrets.decrypt_assembly_fact(ciphertext, key_id, **{**kwargs, key: value})
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt_assembly_fact(tampered, key_id, **kwargs)
    # A Profile ciphertext can never be accepted by the Assembly domain.
    profile_value, profile_key = secrets.encrypt_profile(
        {"value": "70.0"},
        tenant_public_id=kwargs["tenant_public_id"],
        subject_member_id=kwargs["subject_member_id"],
        revision_id=kwargs["fact_ref"],
        identity_source_version=1,
    )
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt_assembly_fact(profile_value, key_id, **kwargs)
    assert profile_key != key_id


def test_Assembly_retained_old_key可解且错误有效key_id拒绝(monkeypatch) -> None:
    old_material = b"a" * 32
    current_material = b"b" * 32
    for index, domain in enumerate(DOMAINS, start=1):
        key_id = f"slice4-{index}"
        ring = {key_id: base64.b64encode(bytes([index]) * 32).decode()}
        if domain == "ASSEMBLY_PHI":
            key_id = "assembly-v2"
            ring = {
                "assembly-v1": base64.b64encode(old_material).decode(),
                "assembly-v2": base64.b64encode(current_material).decode(),
            }
        monkeypatch.setenv(f"KG_SLICE4_{domain}_CURRENT_KEY_ID", key_id)
        monkeypatch.setenv(f"KG_SLICE4_{domain}_KEYRING_JSON", json.dumps(ring))
    from app.modules.user_health.service import Slice4Secrets

    secrets = Slice4Secrets()
    kwargs = {
        "tenant_public_id": _uuid(61),
        "subject_member_id": _uuid(62),
        "service_case_id": _uuid(63),
        "assembly_id": _uuid(64),
        "fact_ref": _uuid(65),
        "indicator_code": "height",
    }
    nonce = b"1" * 12
    aad = secrets.assembly_fact_aad(key_id="assembly-v1", **kwargs)
    ciphertext = nonce + AESGCM(old_material).encrypt(nonce, b'{"value":"175.0"}', aad)
    assert secrets.decrypt_assembly_fact(ciphertext, "assembly-v1", **kwargs) == {
        "value": "175.0"
    }
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.decrypt_assembly_fact(ciphertext, "assembly-v2", **kwargs)


@pytest.mark.parametrize(
    ("tenant", "source_version"),
    [
        ("0198f1c0-0000-7000-8000-000000000001", 1),
        (_uuid(51), True),
    ],
)
def test_AAD分别拒绝非UUID对象与bool伪装source_version(
    monkeypatch, tenant, source_version
) -> None:
    secrets = _secrets(monkeypatch)
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        secrets.profile_aad(
            tenant_public_id=tenant,
            subject_member_id=_uuid(52),
            revision_id=_uuid(53),
            identity_source_version=source_version,
            key_id="slice4-1",
        )
