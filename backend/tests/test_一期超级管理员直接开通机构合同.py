from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.auth.schemas import UserRegisterRequest
from app.modules.direct_institution_onboarding.domain import (
    DirectInstitutionOnboarding,
    DirectOnboardingConflict,
    DirectOnboardingStatus,
    PhoneClaim,
    PhoneClaimState,
)
from app.modules.direct_institution_onboarding.schemas import (
    ComplianceDecisionRequest,
    CompliancePayloadV1,
    DirectActivationRequest,
    DirectComplianceRequest,
    VersionedStepUpRequest,
)

NOW = datetime(2026, 9, 13, 8, 0, tzinfo=UTC)


def test_直开根只允许冻结状态机且逾期不推导Tenant处置() -> None:
    value = DirectInstitutionOnboarding.create(
        onboarding_id=UUID("019948b0-0000-7000-8000-000000000001"),
        tenant_public_id=UUID("019948b0-0000-7000-8000-000000000002"),
        institution_name="合成机构",
        institution_type="HEALTH_STORE",
        administrative_region_id=1,
        institution_code="SYNTHETIC-001",
        created_by=10,
        now=NOW,
    )
    assert value.status is DirectOnboardingStatus.PENDING_ACTIVATION
    assert value.compliance_due_at is None

    value.activate(now=NOW + timedelta(days=1), expected_version=1)
    assert value.status is DirectOnboardingStatus.ACTIVE_COMPLIANCE_PENDING
    assert value.compliance_due_at == datetime(2026, 10, 13, 16, 0, tzinfo=UTC)

    # 30日届满后的Tenant处置仍为产品待决，领域对象不得自动改状态。
    value.observe(now=NOW + timedelta(days=32))
    assert value.status is DirectOnboardingStatus.ACTIVE_COMPLIANCE_PENDING


def test_RELEASED历史不占活跃手机号且历史行不可复活() -> None:
    first = PhoneClaim.reserve(
        claim_id=UUID("019948b0-0000-7000-8000-000000000011"),
        claim_kind="DIRECT_ORG_ADMIN",
        claim_ref=UUID("019948b0-0000-7000-8000-000000000012"),
        digest_key_id="claim-v1",
        digest="a" * 64,
        now=NOW,
    )
    first.release(expected_version=1, now=NOW + timedelta(minutes=1))
    assert first.state is PhoneClaimState.RELEASED
    assert first.blocks_active_digest is False
    with pytest.raises(DirectOnboardingConflict, match="PHONE_CLAIM_STATE_CONFLICT"):
        first.bind(user_id=101, expected_version=2, now=NOW + timedelta(minutes=2))

    second = PhoneClaim.reserve(
        claim_id=UUID("019948b0-0000-7000-8000-000000000013"),
        claim_kind="DIRECT_ORG_ADMIN",
        claim_ref=UUID("019948b0-0000-7000-8000-000000000014"),
        digest_key_id="claim-v1",
        digest="a" * 64,
        now=NOW + timedelta(minutes=2),
    )
    assert second.state is PhoneClaimState.PENDING
    assert second.blocks_active_digest is True


def test_匿名激活输入拒绝伪造actor新User和Tenant状态() -> None:
    valid = {
        "onboarding_id": "019948b0-0000-7000-8000-000000000021",
        "credential_id": "019948b0-0000-7000-8000-000000000022",
        "activation_code": "synthetic-one-time-code",
        "phone": "13900000001",
        "password": "Synthetic-password-123",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "totp_code": "123456",
        "expected_version": 1,
    }
    DirectActivationRequest.model_validate(valid)
    for forbidden in (
        "actor_user_id",
        "actor_role",
        "actor_scope",
        "new_user_id",
        "tenant_id",
        "tenant_status",
    ):
        with pytest.raises(ValidationError):
            DirectActivationRequest.model_validate({**valid, forbidden: "forged"})


def test_三类PII_AAD使用规范UUID与实际NUL分隔符() -> None:
    from app.modules.direct_institution_onboarding.service import (
        admin_phone_aad,
        compliance_payload_aad,
        handoff_phone_aad,
        license_no_aad,
    )

    tenant_id = UUID("019948b0-0000-7000-8000-000000000101")
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000102")
    revision_id = UUID("019948b0-0000-7000-8000-000000000103")
    handoff_id = UUID("019948b0-0000-7000-8000-000000000104")
    license_id = UUID("019948b0-0000-7000-8000-000000000105")

    assert admin_phone_aad(tenant_id, onboarding_id) == (
        b"phase1/direct-institution/admin-phone/v1\0"
        b"019948b0-0000-7000-8000-000000000101\0"
        b"019948b0-0000-7000-8000-000000000102"
    )
    assert handoff_phone_aad(tenant_id, onboarding_id, handoff_id) == (
        b"phase1/direct-institution/handoff-phone/v1\0"
        b"019948b0-0000-7000-8000-000000000101\0"
        b"019948b0-0000-7000-8000-000000000102\0"
        b"019948b0-0000-7000-8000-000000000104"
    )
    assert license_no_aad(tenant_id, onboarding_id, revision_id, license_id) == (
        b"phase1/direct-institution/license-no/v1\0"
        b"019948b0-0000-7000-8000-000000000101\0"
        b"019948b0-0000-7000-8000-000000000102\0"
        b"019948b0-0000-7000-8000-000000000103\0"
        b"019948b0-0000-7000-8000-000000000105"
    )
    assert compliance_payload_aad(tenant_id, onboarding_id, revision_id, 3) == (
        b"phase1/direct-institution/compliance-pii/v1\0"
        b"019948b0-0000-7000-8000-000000000101\0"
        b"019948b0-0000-7000-8000-000000000102\0"
        b"019948b0-0000-7000-8000-000000000103\0"
        b"3"
    )


def test_AAD拒绝非UUIDv7和非法Revision序号() -> None:
    from app.modules.direct_institution_onboarding.service import compliance_payload_aad

    uuid_v7 = UUID("019948b0-0000-7000-8000-000000000111")
    uuid_v4 = UUID("00000000-0000-4000-8000-000000000112")
    with pytest.raises(ValueError, match="UUID_V7_REQUIRED"):
        compliance_payload_aad(uuid_v4, uuid_v7, uuid_v7, 1)
    with pytest.raises(ValueError, match="REVISION_NO_INVALID"):
        compliance_payload_aad(uuid_v7, uuid_v7, uuid_v7, 0)


def test_平台与直开管理员TOTP_AAD只绑定冻结不可变主体() -> None:
    from app.modules.direct_institution_onboarding.service import (
        direct_org_admin_totp_aad,
        platform_admin_totp_aad,
    )

    assert platform_admin_totp_aad(17) == (
        b"phase1/direct-institution/platform-admin-totp/v1\0" b"17"
    )
    assert direct_org_admin_totp_aad(
        UUID("019948b0-0000-7000-8000-000000000116"),
        UUID("019948b0-0000-7000-8000-000000000117"),
        "DIRECT_ACTIVATION",
        UUID("019948b0-0000-7000-8000-000000000118"),
    ) == (
        b"phase1/direct-institution/org-admin-totp/v2\0"
        b"019948b0-0000-7000-8000-000000000116\0"
        b"019948b0-0000-7000-8000-000000000117\0"
        b"DIRECT_ACTIVATION\0"
        b"019948b0-0000-7000-8000-000000000118"
    )
    with pytest.raises(ValueError, match="TOTP_SOURCE_KIND_INVALID"):
        direct_org_admin_totp_aad(
            UUID("019948b0-0000-7000-8000-000000000116"),
            UUID("019948b0-0000-7000-8000-000000000117"),
            "FORGED",
            UUID("019948b0-0000-7000-8000-000000000118"),
        )
    for invalid_user_id in (0, -1, True, "17"):
        with pytest.raises(ValueError, match="ACTOR_USER_ID_INVALID"):
            platform_admin_totp_aad(invalid_user_id)


def test_TOTP密文按StoredKey和冻结AAD读取且拒绝移植(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        open_totp_secret,
        platform_admin_totp_aad,
        seal_totp_secret,
    )

    _install_direct_keyrings(monkeypatch)
    aad = platform_admin_totp_aad(17)
    key_id, ciphertext = seal_totp_secret("JBSWY3DPEHPK3PXP", aad=aad)
    assert key_id == "direct-purpose-4"
    assert open_totp_secret(ciphertext, key_id=key_id, aad=aad) == (
        "JBSWY3DPEHPK3PXP"
    )
    # profile_version和失败预算变化不进入AAD，因此不会破坏同主体解密。
    assert open_totp_secret(ciphertext, key_id=key_id, aad=platform_admin_totp_aad(17))
    with pytest.raises(ValueError, match="TOTP_DECRYPTION_FAILED"):
        open_totp_secret(ciphertext, key_id=key_id, aad=platform_admin_totp_aad(18))
    with pytest.raises(ValueError, match="TOTP_KEY_UNAVAILABLE"):
        open_totp_secret(ciphertext, key_id="missing", aad=aad)


def _compliance_request() -> DirectComplianceRequest:
    return DirectComplianceRequest.model_validate(
        {
            "expected_version": 2,
            "institution_name": "合成机构",
            "institution_type": "HEALTH_STORE",
            "administrative_region_id": 101,
            "institution_code": "SYNTHETIC-001",
            "legal_representative_name": "合成人员",
            "unified_social_credit_code": "SYNTHETIC-CODE-001",
            "contact_name": "合成联系人",
            "contact_phone": "13900000001",
            "address": "合成地址",
            "service_tags": ["HYPERTENSION"],
            "licenses": [
                {
                    "license_id": "019948b0-0000-7000-8000-000000000121",
                    "license_type": "BUSINESS_LICENSE",
                    "license_no": None,
                    "private_file_id": "019948b0-0000-7000-8000-000000000122",
                    "valid_from": "2026-01-01",
                    "valid_until": "2027-01-01",
                }
            ],
        }
    )


def test_合规载荷封闭Schema拒绝未知键且使用稳定规范JSON() -> None:
    from app.modules.direct_institution_onboarding.service import (
        canonical_compliance_payload,
    )

    payload = CompliancePayloadV1.from_request(_compliance_request())
    first = canonical_compliance_payload(payload)
    second = canonical_compliance_payload(
        CompliancePayloadV1.model_validate(
            {
                "address": "合成地址",
                "contact_phone": "13900000001",
                "contact_name": "合成联系人",
                "unified_social_credit_code": "SYNTHETIC-CODE-001",
                "legal_representative_name": "合成人员",
                "schema_version": 1,
            }
        )
    )
    assert first == second
    assert first.startswith(b'{"address":')
    with pytest.raises(ValidationError):
        CompliancePayloadV1.model_validate(
            {**payload.model_dump(), "unknown_sensitive_field": "forged"}
        )


def test_合规载荷AEAD验证AAD篡改与旧Key读取新Key写入() -> None:
    from app.modules.direct_institution_onboarding.service import (
        DirectInstitutionCrypto,
        compliance_payload_aad,
    )

    tenant_id = UUID("019948b0-0000-7000-8000-000000000131")
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000132")
    revision_id = UUID("019948b0-0000-7000-8000-000000000133")
    aad = compliance_payload_aad(tenant_id, onboarding_id, revision_id, 1)
    old = b"o" * 32
    current = b"c" * 32
    digest = b"d" * 32
    crypto = DirectInstitutionCrypto(
        pii_current_key_id="pii-v2",
        pii_keys={"pii-v1": old, "pii-v2": current},
        digest_current_key_id="digest-v1",
        digest_keys={"digest-v1": digest},
    )
    payload = CompliancePayloadV1.from_request(_compliance_request())
    sealed = crypto.seal_compliance_payload(payload, aad=aad)
    assert sealed.key_id == "pii-v2"
    assert sealed.digest_key_id == "digest-v1"
    assert len(sealed.ciphertext) >= 28
    assert crypto.open_compliance_payload(sealed, aad=aad) == payload

    old_writer = DirectInstitutionCrypto(
        pii_current_key_id="pii-v1",
        pii_keys={"pii-v1": old},
        digest_current_key_id="digest-v1",
        digest_keys={"digest-v1": digest},
    )
    old_sealed = old_writer.seal_compliance_payload(payload, aad=aad)
    assert crypto.open_compliance_payload(old_sealed, aad=aad) == payload
    with pytest.raises(ValueError, match="PII_DECRYPTION_FAILED"):
        crypto.open_compliance_payload(
            sealed,
            aad=compliance_payload_aad(tenant_id, onboarding_id, revision_id, 2),
        )
    with pytest.raises(ValueError, match="PII_DECRYPTION_FAILED"):
        crypto.open_compliance_payload(
            sealed.model_copy(update={"ciphertext": sealed.ciphertext[:-1] + b"x"}),
            aad=aad,
        )
    with pytest.raises(ValueError, match="PII_KEY_UNAVAILABLE"):
        crypto.open_compliance_payload(
            sealed.model_copy(update={"key_id": "missing"}), aad=aad
        )


def test_合规业务与许可证集合摘要使用独立用途且顺序稳定(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        compliance_business_digest,
        compliance_license_set_digest,
    )

    _install_direct_keyrings(monkeypatch)
    request = _compliance_request()
    credit_key, credit_digest = compliance_business_digest(
        "CREDIT_CODE", request.unified_social_credit_code
    )
    license_key, first = compliance_license_set_digest(request.licenses)
    _, second = compliance_license_set_digest(tuple(reversed(request.licenses)))
    assert credit_key == license_key == "direct-purpose-3"
    assert len(credit_digest) == len(first) == 64
    assert first == second
    assert first != credit_digest
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        compliance_business_digest("UNKNOWN", "synthetic")


@pytest.mark.parametrize(
    ("operation", "expected_status", "expected_revision_no"),
    (
        ("COMPLIANCE_SAVE", "ACTIVE_COMPLIANCE_PENDING", 1),
        ("COMPLIANCE_SUBMIT", "COMPLIANCE_UNDER_REVIEW", 3),
    ),
)
def test_合规保存与提交从同一路径构造密文摘要后像和确认信封(
    monkeypatch, operation: str, expected_status: str, expected_revision_no: int
) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_compliance_mutation,
        compliance_business_digest,
        compliance_license_set_digest,
        compliance_payload_integrity_digest,
    )

    _install_direct_keyrings(monkeypatch)
    compliance_request = _compliance_request()
    payload_key_id, payload_digest = compliance_payload_integrity_digest(
        CompliancePayloadV1.from_request(compliance_request)
    )
    credit_key_id, credit_digest = compliance_business_digest(
        "CREDIT_CODE", compliance_request.unified_social_credit_code
    )
    license_set_key_id, license_set_digest = compliance_license_set_digest(
        compliance_request.licenses
    )
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(201, 207)
    )
    current_revision = (
        None
        if operation == "COMPLIANCE_SAVE"
        else UUID("019948b0-0000-7000-8000-000000000199")
    )
    envelope, confirmation = build_compliance_mutation(
        operation=operation,
        request=compliance_request,
        current={
            "onboarding_id": UUID("019948b0-0000-7000-8000-000000000191"),
            "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000192"),
            "revision_id": current_revision,
            "revision_no": None if current_revision is None else 3,
            "status": "ACTIVE_COMPLIANCE_PENDING",
            "root_version": 2,
            "compliance_payload_digest_key_id": payload_key_id,
            "compliance_payload_digest": payload_digest,
            "unified_social_credit_code_digest_key_id": credit_key_id,
            "unified_social_credit_code_digest": credit_digest,
            "license_set_digest_key_id": license_set_key_id,
            "license_set_digest": license_set_digest,
        },
        stored_licenses=[
            {
                "license_id": compliance_request.licenses[0].license_id,
                "license_no_digest_key_id": None,
                "license_no_digest": None,
            }
        ],
        actor_user_id=17,
        actor_tenant_id=23,
        idempotency_key="synthetic-compliance-key",
        id_factory=lambda: next(ids),
    )

    assert envelope["operation_id"] == confirmation["operation_id"]
    assert envelope["request_digest"] == confirmation["request_digest"]
    assert envelope["expected_postimage_digest"] == confirmation["expected_postimage_digest"]
    assert confirmation["receipt_response_digest"] == envelope["expected_postimage_digest"]
    if operation == "COMPLIANCE_SAVE":
        assert confirmation["onboarding_id"] == envelope["onboarding_id"]
        assert "target_id" not in confirmation
    else:
        assert confirmation["target_id"] == envelope["onboarding_id"]
    assert envelope["revision_no"] == expected_revision_no
    assert confirmation["target_status"] == expected_status
    assert confirmation["target_version"] == 3
    assert envelope["license_set_digest"] == confirmation.get("license_set_digest", envelope["license_set_digest"])
    if operation == "COMPLIANCE_SAVE":
        assert envelope["compliance_payload_ciphertext"]
        assert "event_id" not in envelope
        assert "event_id" not in confirmation
    else:
        assert envelope["event_id"] == confirmation["event_id"]
        assert "compliance_payload_ciphertext" not in envelope
        assert "compliance_payload_key_id" not in envelope


def test_合规提交按草稿历史DigestKey重算而不使用当前写Key(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_compliance_mutation,
    )

    _install_direct_keyrings(monkeypatch)
    request = _compliance_request()
    save_ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(231, 237)
    )
    saved, _ = build_compliance_mutation(
        operation="COMPLIANCE_SAVE",
        request=request,
        current={
            "onboarding_id": UUID("019948b0-0000-7000-8000-000000000221"),
            "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000222"),
            "revision_id": None,
            "revision_no": None,
            "status": "ACTIVE_COMPLIANCE_PENDING",
            "root_version": 2,
        },
        stored_licenses=[],
        actor_user_id=17,
        actor_tenant_id=23,
        idempotency_key="synthetic-save-key",
        id_factory=lambda: next(save_ids),
    )
    old_key = "direct-purpose-3"
    new_key = "direct-purpose-3-next"
    monkeypatch.setenv("KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID", new_key)
    monkeypatch.setenv(
        "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        json.dumps(
            {
                old_key: base64.b64encode(bytes([3]) * 32).decode("ascii"),
                new_key: base64.b64encode(b"n" * 32).decode("ascii"),
            }
        ),
    )
    submit_ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(241, 247)
    )
    submitted, _ = build_compliance_mutation(
        operation="COMPLIANCE_SUBMIT",
        request=request.model_copy(update={"expected_version": 3}),
        current={
            "onboarding_id": UUID(saved["onboarding_id"]),
            "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000222"),
            "revision_id": UUID(saved["revision_id"]),
            "revision_no": saved["revision_no"],
            "status": "ACTIVE_COMPLIANCE_PENDING",
            "root_version": 3,
            "compliance_payload_digest_key_id": saved["compliance_payload_digest_key_id"],
            "compliance_payload_digest": saved["compliance_payload_digest"],
            "unified_social_credit_code_digest_key_id": saved["unified_social_credit_code_digest_key_id"],
            "unified_social_credit_code_digest": saved["unified_social_credit_code_digest"],
            "license_set_digest_key_id": saved["license_set_digest_key_id"],
            "license_set_digest": saved["license_set_digest"],
        },
        stored_licenses=saved["licenses"],
        actor_user_id=17,
        actor_tenant_id=23,
        idempotency_key="synthetic-submit-key",
        id_factory=lambda: next(submit_ids),
    )
    assert submitted["compliance_payload_digest_key_id"] == old_key
    assert submitted["compliance_payload_digest"] == saved["compliance_payload_digest"]
    assert submitted["license_set_digest_key_id"] == old_key
    assert submitted["license_set_digest"] == saved["license_set_digest"]


def test_合规提交拒绝草稿内容变化与不可用的历史Key(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_compliance_mutation,
    )

    _install_direct_keyrings(monkeypatch)
    request = _compliance_request()
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(251, 271)
    )
    saved, _ = build_compliance_mutation(
        operation="COMPLIANCE_SAVE",
        request=request,
        current={
            "onboarding_id": UUID("019948b0-0000-7000-8000-000000000251"),
            "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000252"),
            "revision_id": None,
            "revision_no": None,
            "status": "ACTIVE_COMPLIANCE_PENDING",
            "root_version": 2,
        },
        actor_user_id=17,
        actor_tenant_id=23,
        idempotency_key="synthetic-save-key",
        id_factory=lambda: next(ids),
    )
    current = {
        "onboarding_id": UUID(saved["onboarding_id"]),
        "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000252"),
        "revision_id": UUID(saved["revision_id"]),
        "revision_no": saved["revision_no"],
        "status": "ACTIVE_COMPLIANCE_PENDING",
        "root_version": 3,
        "compliance_payload_digest_key_id": saved["compliance_payload_digest_key_id"],
        "compliance_payload_digest": saved["compliance_payload_digest"],
        "unified_social_credit_code_digest_key_id": saved["unified_social_credit_code_digest_key_id"],
        "unified_social_credit_code_digest": saved["unified_social_credit_code_digest"],
        "license_set_digest_key_id": saved["license_set_digest_key_id"],
        "license_set_digest": saved["license_set_digest"],
    }
    with pytest.raises(ValueError, match="DIRECT_ONBOARDING_STATE_CONFLICT"):
        build_compliance_mutation(
            operation="COMPLIANCE_SUBMIT",
            request=request.model_copy(
                update={"expected_version": 3, "contact_name": "被篡改联系人"}
            ),
            current=current,
            stored_licenses=saved["licenses"],
            actor_user_id=17,
            actor_tenant_id=23,
            idempotency_key="synthetic-submit-mutated",
            id_factory=lambda: next(ids),
        )

    monkeypatch.setenv(
        "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        json.dumps(
            {
                "direct-purpose-3-next": base64.b64encode(b"n" * 32).decode(
                    "ascii"
                )
            }
        ),
    )
    monkeypatch.setenv(
        "KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID", "direct-purpose-3-next"
    )
    with pytest.raises(
        RuntimeError, match="DIRECT_ONBOARDING_DEPENDENCY_UNAVAILABLE"
    ):
        build_compliance_mutation(
            operation="COMPLIANCE_SUBMIT",
            request=request.model_copy(update={"expected_version": 3}),
            current=current,
            stored_licenses=saved["licenses"],
            actor_user_id=17,
            actor_tenant_id=23,
            idempotency_key="synthetic-submit-missing-key",
            id_factory=lambda: next(ids),
        )


def test_合规提交逐许可证使用各自存储的DigestKey(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_compliance_mutation,
        compliance_business_digest,
    )

    _install_direct_keyrings(monkeypatch)
    request = DirectComplianceRequest.model_validate(
        {
            **_compliance_request().model_dump(mode="json"),
            "licenses": [
                {
                    **_compliance_request().licenses[0].model_dump(mode="json"),
                    "license_no": "SYNTHETIC-LICENSE-1",
                },
                {
                    "license_id": "019948b0-0000-7000-8000-000000000123",
                    "license_type": "MEDICAL_INSTITUTION_LICENSE",
                    "license_no": "SYNTHETIC-LICENSE-2",
                    "private_file_id": "019948b0-0000-7000-8000-000000000124",
                    "valid_from": "2026-01-01",
                    "valid_until": "2027-01-01",
                },
            ],
        }
    )
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(281, 301)
    )
    saved, _ = build_compliance_mutation(
        operation="COMPLIANCE_SAVE",
        request=request,
        current={
            "onboarding_id": UUID("019948b0-0000-7000-8000-000000000281"),
            "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000282"),
            "revision_id": None,
            "revision_no": None,
            "status": "ACTIVE_COMPLIANCE_PENDING",
            "root_version": 2,
        },
        actor_user_id=17,
        actor_tenant_id=23,
        idempotency_key="synthetic-save-multi",
        id_factory=lambda: next(ids),
    )
    old_key = "direct-purpose-3"
    alternate_key = "direct-license-alternate"
    new_key = "direct-purpose-3-next"
    monkeypatch.setenv("KG_DIRECT_INSTITUTION_DIGEST_CURRENT_KEY_ID", new_key)
    monkeypatch.setenv(
        "KG_DIRECT_INSTITUTION_DIGEST_KEYRING_JSON",
        json.dumps(
            {
                old_key: base64.b64encode(bytes([3]) * 32).decode("ascii"),
                alternate_key: base64.b64encode(b"a" * 32).decode("ascii"),
                new_key: base64.b64encode(b"n" * 32).decode("ascii"),
            }
        ),
    )
    _, alternate_digest = compliance_business_digest(
        "LICENSE_NO", request.licenses[1].license_no, key_id=alternate_key
    )
    stored_licenses = [dict(value) for value in saved["licenses"]]
    stored_licenses[1]["license_no_digest_key_id"] = alternate_key
    stored_licenses[1]["license_no_digest"] = alternate_digest
    submitted, _ = build_compliance_mutation(
        operation="COMPLIANCE_SUBMIT",
        request=request.model_copy(update={"expected_version": 3}),
        current={
            "onboarding_id": UUID(saved["onboarding_id"]),
            "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000282"),
            "revision_id": UUID(saved["revision_id"]),
            "revision_no": saved["revision_no"],
            "status": "ACTIVE_COMPLIANCE_PENDING",
            "root_version": 3,
            "compliance_payload_digest_key_id": saved["compliance_payload_digest_key_id"],
            "compliance_payload_digest": saved["compliance_payload_digest"],
            "unified_social_credit_code_digest_key_id": saved["unified_social_credit_code_digest_key_id"],
            "unified_social_credit_code_digest": saved["unified_social_credit_code_digest"],
            "license_set_digest_key_id": saved["license_set_digest_key_id"],
            "license_set_digest": saved["license_set_digest"],
        },
        stored_licenses=stored_licenses,
        actor_user_id=17,
        actor_tenant_id=23,
        idempotency_key="synthetic-submit-multi",
        id_factory=lambda: next(ids),
    )
    assert [value["license_no_digest_key_id"] for value in submitted["licenses"]] == [
        old_key,
        alternate_key,
    ]


def test_批准信用代码只能从已验证的当前合规密文取得() -> None:
    from app.modules.direct_institution_onboarding.service import (
        DirectInstitutionCrypto,
        approved_credit_code,
        compliance_payload_aad,
    )

    crypto = DirectInstitutionCrypto(
        pii_current_key_id="pii-v1",
        pii_keys={"pii-v1": b"p" * 32},
        digest_current_key_id="digest-v1",
        digest_keys={"digest-v1": b"d" * 32},
    )
    request = _compliance_request()
    aad = compliance_payload_aad(
        UUID("019948b0-0000-7000-8000-000000000141"),
        UUID("019948b0-0000-7000-8000-000000000142"),
        UUID("019948b0-0000-7000-8000-000000000143"),
        1,
    )
    sealed = crypto.seal_compliance_payload(
        CompliancePayloadV1.from_request(request), aad=aad
    )

    assert approved_credit_code(crypto, sealed=sealed, aad=aad) == (
        request.unified_social_credit_code
    )
    with pytest.raises(ValueError, match="PII_DECRYPTION_FAILED"):
        approved_credit_code(crypto, sealed=sealed, aad=aad + b"x")


_DIRECT_KEYRINGS = (
    "KG_DIRECT_INSTITUTION_PII",
    "KG_DIRECT_INSTITUTION_CODE",
    "KG_DIRECT_INSTITUTION_DIGEST",
    "KG_PLATFORM_ADMIN_TOTP",
    "KG_ACCOUNT_PHONE_CLAIM_DIGEST",
)


def _install_direct_keyrings(monkeypatch) -> list[bytes]:
    materials = [bytes([index]) * 32 for index in range(1, 6)]
    for index, (prefix, material) in enumerate(
        zip(_DIRECT_KEYRINGS, materials, strict=True), start=1
    ):
        key_id = f"direct-purpose-{index}"
        monkeypatch.setenv(f"{prefix}_CURRENT_KEY_ID", key_id)
        monkeypatch.setenv(
            f"{prefix}_KEYRING_JSON",
            json.dumps({key_id: base64.b64encode(material).decode("ascii")}),
        )
    return materials


def test_合规审批仅将严格公开投影写入Receipt且内部Decision有独立证据绑定(
    monkeypatch,
) -> None:
    from app.modules.direct_institution_onboarding.schemas import DirectComplianceDTO
    from app.modules.direct_institution_onboarding.service import (
        DirectInstitutionCrypto,
        DirectOnboardingSecrets,
        build_compliance_decision_mutation,
        compliance_payload_aad,
    )

    _install_direct_keyrings(monkeypatch)
    secrets = DirectOnboardingSecrets()
    crypto = DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000301")
    tenant_public_id = UUID("019948b0-0000-7000-8000-000000000302")
    revision_id = UUID("019948b0-0000-7000-8000-000000000303")
    compliance = _compliance_request()
    sealed = crypto.seal_compliance_payload(
        CompliancePayloadV1.from_request(compliance),
        aad=compliance_payload_aad(
            tenant_public_id, onboarding_id, revision_id, 1
        ),
    )
    request = ComplianceDecisionRequest(
        expected_version=4,
        revision_id=revision_id,
        decision="APPROVE",
        reason_code="SYNTHETIC_APPROVAL",
        totp_code="123456",
    )
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(304, 308)
    )

    envelope, confirmation = build_compliance_decision_mutation(
        onboarding_id=onboarding_id,
        request=request,
        authority={
            "onboarding_id": str(onboarding_id),
            "tenant_id": 23,
            "tenant_public_id": str(tenant_public_id),
            "revision_id": str(revision_id),
            "revision_no": 1,
            "target_version": 4,
            "institution_name": compliance.institution_name,
            "institution_type": compliance.institution_type,
            "administrative_region_id": compliance.administrative_region_id,
            "institution_code": compliance.institution_code,
            "service_tags": list(compliance.service_tags),
            "licenses": [
                license.model_dump(mode="json", exclude={"license_no"})
                | {"license_no": None}
                for license in compliance.licenses
            ],
            "compliance_payload_ciphertext": base64.b64encode(
                sealed.ciphertext
            ).decode("ascii"),
            "compliance_payload_key_id": sealed.key_id,
            "compliance_payload_digest_key_id": sealed.digest_key_id,
            "compliance_payload_digest": sealed.digest,
        },
        actor_user_id=17,
        idempotency_key="synthetic-compliance-decision",
        accepted_totp_step=123,
        id_factory=lambda: next(ids),
    )

    public_response = DirectComplianceDTO.model_validate(
        {
            "onboarding_id": envelope["onboarding_id"],
            "revision_id": envelope["revision_id"],
            "revision_no": 1,
            "status": "COMPLIANCE_APPROVED",
            "institution_name": compliance.institution_name,
            "institution_type": compliance.institution_type,
            "administrative_region_id": compliance.administrative_region_id,
            "institution_code": compliance.institution_code,
            "service_tags": compliance.service_tags,
            "licenses": [
                license.model_dump(mode="json", exclude={"license_no"})
                | {"license_no": None}
                for license in compliance.licenses
            ],
            "correction_fields": [],
            "version": 5,
        }
    ).model_dump(mode="json")
    assert envelope["decision_id"] == envelope["operation_id"]
    assert envelope["unified_social_credit_code"] == (
        compliance.unified_social_credit_code
    )
    assert confirmation["operation_id"] == envelope["decision_id"]
    assert confirmation["audit_action"] == "DIRECT_COMPLIANCE_DECIDE"
    assert confirmation["outbox_event_type"] == "DIRECT_COMPLIANCE_DECIDED"
    assert "unified_social_credit_code" not in public_response
    assert "decision_id" not in public_response
    assert all(item["license_no"] is None for item in public_response["licenses"])
    assert sealed.digest not in json.dumps(public_response, ensure_ascii=False)


def test_合规审批决定与补正字段必须一致(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_compliance_decision_mutation,
    )

    _install_direct_keyrings(monkeypatch)
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000311")
    revision_id = UUID("019948b0-0000-7000-8000-000000000312")
    base = {
        "expected_version": 4,
        "revision_id": revision_id,
        "reason_code": "SYNTHETIC_DECISION",
        "totp_code": "123456",
    }
    for decision, correction_fields in (
        ("APPROVE", ("institution_name",)),
        ("NEEDS_CORRECTION", ()),
    ):
        with pytest.raises(ValueError, match="INVALID_REQUEST"):
            build_compliance_decision_mutation(
                onboarding_id=onboarding_id,
                request=ComplianceDecisionRequest(
                    **base,
                    decision=decision,
                    correction_fields=correction_fields,
                ),
                authority={},
                actor_user_id=17,
                idempotency_key="synthetic-invalid-decision",
                accepted_totp_step=123,
                id_factory=lambda: onboarding_id,
            )


def test_五组直开密钥完整独立且每个Keyring只含自己的CurrentKey(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        DirectOnboardingSecrets,
    )

    materials = _install_direct_keyrings(monkeypatch)
    secrets = DirectOnboardingSecrets()
    assert [secrets.pii_keys[secrets.pii_key_id], secrets.code_keys[secrets.code_key_id],
            secrets.digest_keys[secrets.digest_key_id], secrets.totp_keys[secrets.totp_key_id],
            secrets.phone_claim_keys[secrets.phone_claim_key_id]] == materials
    assert all(
        len(ring) == 1
        for ring in (
            secrets.pii_keys,
            secrets.code_keys,
            secrets.digest_keys,
            secrets.totp_keys,
            secrets.phone_claim_keys,
        )
    )


@pytest.mark.parametrize("prefix", _DIRECT_KEYRINGS)
@pytest.mark.parametrize("suffix", ("CURRENT_KEY_ID", "KEYRING_JSON"))
def test_五组直开密钥缺少任一配置均fail_closed(monkeypatch, prefix: str, suffix: str) -> None:
    from app.modules.direct_institution_onboarding.service import (
        DirectOnboardingSecrets,
    )

    _install_direct_keyrings(monkeypatch)
    monkeypatch.delenv(f"{prefix}_{suffix}")
    with pytest.raises(RuntimeError, match="DIRECT_ONBOARDING_DEPENDENCY_UNAVAILABLE"):
        DirectOnboardingSecrets()


def test_五组直开用途禁止共享密钥材料(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        DirectOnboardingSecrets,
    )

    _install_direct_keyrings(monkeypatch)
    shared = base64.b64encode(bytes([1]) * 32).decode("ascii")
    monkeypatch.setenv(
        "KG_DIRECT_INSTITUTION_CODE_KEYRING_JSON",
        json.dumps({"direct-purpose-2": shared}),
    )
    with pytest.raises(RuntimeError, match="DIRECT_ONBOARDING_DEPENDENCY_UNAVAILABLE"):
        DirectOnboardingSecrets()


def test_直开列表游标签名且绑定Actor状态和快照上界(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        decode_direct_onboarding_cursor,
        encode_direct_onboarding_cursor,
    )

    _install_direct_keyrings(monkeypatch)
    cursor_id = UUID("019948b0-0000-7000-8000-000000000181")
    ceiling_id = UUID("019948b0-0000-7000-8000-000000000189")
    encoded = encode_direct_onboarding_cursor(
        cursor_id,
        ceiling_id=ceiling_id,
        actor_user_id=7,
        status="PENDING_ACTIVATION",
    )
    assert decode_direct_onboarding_cursor(
        encoded,
        actor_user_id=7,
        status="PENDING_ACTIVATION",
    ) == (cursor_id, ceiling_id)
    for changed_scope in (
        {"actor_user_id": 8, "status": "PENDING_ACTIVATION"},
        {"actor_user_id": 7, "status": "ACTIVE_COMPLIANCE_PENDING"},
    ):
        with pytest.raises(ValueError, match="^INVALID_REQUEST$"):
            decode_direct_onboarding_cursor(encoded, **changed_scope)
    tampered = encoded[:-1] + ("A" if encoded[-1] != "A" else "B")
    with pytest.raises(ValueError, match="^INVALID_REQUEST$"):
        decode_direct_onboarding_cursor(
            tampered,
            actor_user_id=7,
            status="PENDING_ACTIVATION",
        )


def test_一次性凭据只返回高熵明文且摘要按用途隔离(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        credential_digest,
        generate_one_time_credential,
        postimage_digest,
        request_digest,
    )

    _install_direct_keyrings(monkeypatch)
    credential = generate_one_time_credential()
    assert len(credential) == 43
    assert credential.isascii()
    credential_key_id, stored_digest = credential_digest(credential)
    assert credential_key_id == "direct-purpose-2"
    assert len(stored_digest) == 64
    assert credential not in stored_digest
    assert credential_digest(credential) == (credential_key_id, stored_digest)
    assert credential_digest(credential + "x")[1] != stored_digest

    request_value = {"b": 2, "a": [1, None]}
    assert request_digest("CREATE", request_value) == request_digest(
        "CREATE", {"a": [1, None], "b": 2}
    )
    assert request_digest("CREATE", request_value) != request_digest(
        "REGENERATE", request_value
    )
    assert postimage_digest("CREATE", request_value) != request_digest(
        "CREATE", request_value
    )


def test_激活凭据重生成只使用受限Authority活动凭据且不回读旧明文(
    monkeypatch,
) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_direct_regenerate_mutation,
    )

    _install_direct_keyrings(monkeypatch)
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000191")
    tenant_id = UUID("019948b0-0000-7000-8000-000000000192")
    old_credential_id = UUID("019948b0-0000-7000-8000-000000000193")
    generated = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(194, 199)
    )
    request = VersionedStepUpRequest(
        expected_version=1,
        totp_code="123456",
        reason_code="SYNTHETIC_REGENERATE",
    )
    envelope, confirmation, credential = build_direct_regenerate_mutation(
        onboarding_id=onboarding_id,
        request=request,
        current={
            "onboarding_id": onboarding_id,
            "tenant_id": tenant_id,
            "status": "PENDING_ACTIVATION",
            "version": 1,
        },
        step_up={
            "active_credential_id": old_credential_id,
            "target_version": 1,
        },
        actor_user_id=7,
        idempotency_key="synthetic-regenerate",
        accepted_totp_step=99,
        id_factory=lambda: next(generated),
        activation_code="synthetic-new-one-time-code",
    )
    assert envelope["old_credential_id"] == str(old_credential_id)
    assert envelope["new_credential_id"] != envelope["old_credential_id"]
    assert envelope["new_credential_digest"] != credential
    assert credential == "synthetic-new-one-time-code"
    assert confirmation["credential_id"] == envelope["new_credential_id"]
    assert confirmation["target_version"] == 2
    assert "activation_code" not in envelope


def test_预激活撤销只携带受限Authority凭据ID并由数据库确认其撤销后像(
    monkeypatch,
) -> None:
    from app.modules.direct_institution_onboarding.service import (
        build_direct_revoke_mutation,
    )

    _install_direct_keyrings(monkeypatch)
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000201")
    tenant_id = UUID("019948b0-0000-7000-8000-000000000202")
    credential_id = UUID("019948b0-0000-7000-8000-000000000203")
    generated = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(204, 208)
    )
    request = VersionedStepUpRequest(
        expected_version=1,
        totp_code="123456",
        reason_code="SYNTHETIC_REVOKE",
    )
    envelope, confirmation = build_direct_revoke_mutation(
        onboarding_id=onboarding_id,
        request=request,
        current={
            "onboarding_id": onboarding_id,
            "tenant_id": tenant_id,
            "status": "PENDING_ACTIVATION",
            "version": 1,
        },
        step_up={"active_credential_id": credential_id, "target_version": 1},
        actor_user_id=7,
        idempotency_key="synthetic-revoke",
        accepted_totp_step=99,
        id_factory=lambda: next(generated),
    )
    assert envelope["active_credential_id"] == str(credential_id)
    assert confirmation["credential_id"] == str(credential_id)
    assert confirmation["credential_digest_key_id"] is None
    assert confirmation["credential_digest"] is None
    assert confirmation["phone_claim_state"] == "RELEASED"
    assert confirmation["target_status"] == "REVOKED_BEFORE_ACTIVATION"
    assert confirmation["target_version"] == 2
    assert "activation_code" not in envelope


def test_直开PII文本AEAD绑定精确AAD并支持旧Key读取(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.service import (
        DirectInstitutionCrypto,
    )

    crypto = DirectInstitutionCrypto(
        pii_current_key_id="pii-v2",
        pii_keys={"pii-v1": b"o" * 32, "pii-v2": b"n" * 32},
        digest_current_key_id="digest-v1",
        digest_keys={"digest-v1": b"d" * 32},
    )
    aad = b"synthetic-fixed-aad"
    ciphertext = crypto.seal_text("synthetic-sensitive-value", aad=aad)
    assert crypto.open_text(ciphertext, key_id="pii-v2", aad=aad) == (
        "synthetic-sensitive-value"
    )
    with pytest.raises(ValueError, match="PII_DECRYPTION_FAILED"):
        crypto.open_text(ciphertext, key_id="pii-v2", aad=aad + b"x")
    with pytest.raises(ValueError, match="PII_KEY_UNAVAILABLE"):
        crypto.open_text(ciphertext, key_id="missing", aad=aad)

    old_crypto = DirectInstitutionCrypto(
        pii_current_key_id="pii-v1",
        pii_keys={"pii-v1": b"o" * 32},
        digest_current_key_id="digest-v1",
        digest_keys={"digest-v1": b"d" * 32},
    )
    old_ciphertext = old_crypto.seal_text("synthetic-old-value", aad=aad)
    assert crypto.open_text(old_ciphertext, key_id="pii-v1", aad=aad) == (
        "synthetic-old-value"
    )


def test_Slice3批准机构读取改用canonical权威且不直读application() -> None:
    from app.modules.member_enrollment import api

    class Authority:
        def __init__(self) -> None:
            self.statement = ""
            self.parameters = {}

        async def execute(self, statement, parameters):
            self.statement = str(statement)
            self.parameters = parameters

            class Result:
                def mappings(self):
                    return self

                def one_or_none(self):
                    return {
                        "tenant_id": 9,
                        "tenant_public_id": UUID(
                            "019948b0-0000-7000-8000-000000000191"
                        ),
                    }

            return Result()

    authority = Authority()
    actual = asyncio.run(api._approved_institution(authority, tenant_id=9))
    assert actual == (9, UUID("019948b0-0000-7000-8000-000000000191"))
    assert "institution_tenant_origin_current_v1" in authority.statement
    assert "institution_application" not in authority.statement
    assert authority.parameters == {"tenant_id": 9, "tenant_public_id": None}


def test_会员注册生成UUIDv7并通过全局手机号claim原子注册(monkeypatch) -> None:
    from app.modules.auth import repository as auth_repository
    from app.modules.direct_institution_onboarding.service import (
        account_phone_claim_digest,
    )

    key_id = "phone-claim-v1"
    material = b"q" * 32
    monkeypatch.setenv("KG_ACCOUNT_PHONE_CLAIM_DIGEST_CURRENT_KEY_ID", key_id)
    monkeypatch.setenv(
        "KG_ACCOUNT_PHONE_CLAIM_DIGEST_KEYRING_JSON",
        json.dumps({key_id: base64.b64encode(material).decode("ascii")}),
    )
    class Session:
        async def execute(self, statement):
            self.statement = statement

            class Result:
                def mappings(self):
                    return self

                def one(self):
                    return {
                        "id": 99,
                        "phone": "13900000001",
                        "role": "member",
                        "status": "active",
                        "verify_status": "unverified",
                        "tenant_id": None,
                        "created_at": NOW,
                    }

            return Result()

    session = Session()
    payload = UserRegisterRequest(phone="13900000001", password="Synthetic-password-123")
    response = asyncio.run(
        auth_repository.create_registered_member(
            session,
            phone=payload.phone,
            password_hash="synthetic-password-hash",
        )
    )
    expected_key_id, expected_digest = account_phone_claim_digest(payload.phone)
    parameters = session.statement.compile().params

    assert response.id == 99
    assert parameters["claim_id"].version == 7
    assert parameters["phone_digest_key_id"] == expected_key_id == key_id
    assert parameters["phone_digest"] == expected_digest
    assert len(expected_digest) == 64


def test_机构管理员合规读取只传当前User且返回受限行() -> None:
    from app.modules.direct_institution_onboarding.repository import (
        DirectInstitutionOnboardingRepository,
    )

    class Session:
        async def execute(self, statement):
            self.statement = statement

            class Result:
                def mappings(self):
                    return self

                def all(self):
                    return [{"onboarding_id": UUID("019948b0-0000-7000-8000-000000000601")}]

            return Result()

    session = Session()
    rows = asyncio.run(
        DirectInstitutionOnboardingRepository(session).read_current_compliance(
            actor_user_id=91
        )
    )
    assert rows[0]["onboarding_id"].version == 7
    assert "direct_compliance_current_v1" in str(session.statement)
    assert session.statement.compile().params == {"actor_user_id": 91}


def test_直开详情与合规GET接入受限Reader且角色fail_closed(monkeypatch) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api

    onboarding_id = UUID("019948b0-0000-7000-8000-000000000701")
    tenant_id = UUID("019948b0-0000-7000-8000-000000000702")

    class Repository:
        async def read_rows(self, **values):
            assert values["actor_user_id"] == 17
            assert values["resource"] == "DETAIL"
            assert values["target_id"] == onboarding_id
            return [{
                "onboarding_id": onboarding_id,
                "tenant_id": tenant_id,
                "institution_code": "SYNTHETIC-701",
                "institution_name": "合成机构",
                "institution_type": "HEALTH_STORE",
                "administrative_region_id": 701,
                "status": "PENDING_ACTIVATION",
                "compliance_due_at": None,
                "current_revision_id": None,
                "version": 1,
                "snapshot_ceiling": None,
            }]

        async def read_current_compliance(self, **values):
            assert values == {"actor_user_id": 18}
            return [{
                "onboarding_id": onboarding_id,
                "tenant_public_id": tenant_id,
                "revision_id": None,
                "revision_no": None,
                "status": "ACTIVE_COMPLIANCE_PENDING",
                "institution_name": "合成机构",
                "institution_type": "HEALTH_STORE",
                "administrative_region_id": 701,
                "institution_code": "SYNTHETIC-701",
                "service_tags": [],
                "correction_fields": [],
                "root_version": 2,
                "license_id": None,
            }]

    repository = Repository()
    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", lambda _: repository)
    detail = asyncio.run(api.get_direct_onboarding(
        onboarding_id,
        CurrentUser(id=17, role="super_admin"),
        session=object(),
    ))
    assert detail["onboarding_id"] == onboarding_id
    assert "snapshot_ceiling" not in detail

    compliance = asyncio.run(api.get_direct_compliance(
        CurrentUser(id=18, role="org_admin", tenant_id=9),
        session=object(),
    ))
    assert compliance["revision_id"] is None
    assert compliance["licenses"] == []

    with pytest.raises(api.DirectOnboardingError, match="ROLE_FORBIDDEN"):
        asyncio.run(api.get_direct_onboarding(
            onboarding_id,
            CurrentUser(id=18, role="org_admin", tenant_id=9),
            session=object(),
        ))


@pytest.mark.parametrize(
    ("route_name", "operation", "mutation_name"),
    (
        ("put_direct_compliance", "COMPLIANCE_SAVE", "save_compliance"),
        ("submit_direct_compliance", "COMPLIANCE_SUBMIT", "submit_compliance"),
    ),
)
def test_合规PUT与POST使用独立重放写入确认并返回当前受限投影(
    monkeypatch, route_name: str, operation: str, mutation_name: str
) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api

    current = {
        "onboarding_id": UUID("019948b0-0000-7000-8000-000000000681"),
        "tenant_public_id": UUID("019948b0-0000-7000-8000-000000000682"),
        "revision_id": None,
        "revision_no": None,
        "status": "ACTIVE_COMPLIANCE_PENDING",
        "institution_name": "合成机构",
        "institution_type": "HEALTH_STORE",
        "administrative_region_id": 101,
        "institution_code": "SYNTHETIC-001",
        "service_tags": [],
        "correction_fields": [],
        "root_version": 2,
        "license_id": None,
    }
    after = {
        **current,
        "revision_id": UUID("019948b0-0000-7000-8000-000000000683"),
        "revision_no": 1,
        "root_version": 3,
        "status": (
            "ACTIVE_COMPLIANCE_PENDING"
            if operation == "COMPLIANCE_SAVE"
            else "COMPLIANCE_UNDER_REVIEW"
        ),
    }
    calls: list[tuple[str, object]] = []

    class ReaderSession:
        async def rollback(self):
            calls.append(("reader-rollback", None))

    class Repository:
        def __init__(self, session) -> None:
            self.session = session

        async def read_current_compliance(self, **values):
            calls.append(("read", values))
            return [current] if sum(name == "read" for name, _ in calls) == 1 else [after]

        async def compliance_save_replay(self, **values):
            calls.append(("replay-save", values))
            return None

        async def compliance_submit_replay(self, **values):
            calls.append(("replay-submit", values))
            return None

        async def save_compliance(self, envelope):
            calls.append(("save", envelope))
            return {"status": after["status"]}

        async def submit_compliance(self, envelope):
            calls.append(("submit", envelope))
            return {"status": after["status"]}

    async def committed(*args, **kwargs):
        calls.append(("commit", kwargs["confirmation"]))

    def build(**values):
        calls.append(("build", values))
        return (
            {
                "actor_scope": "user:18:tenant:9",
                "request_digest": "a" * 64,
                "request_digest_candidates": [
                    {"key_id": "direct-purpose-3", "digest": "a" * 64}
                ],
                "operation": operation,
            },
            {"operation": operation},
        )

    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", Repository)
    monkeypatch.setattr(api, "build_compliance_mutation", build)
    monkeypatch.setattr(api, "_commit_compliance", committed)
    route = getattr(api, route_name)
    result = asyncio.run(
        route(
            _compliance_request(),
            "synthetic-compliance-key",
            CurrentUser(id=18, role="org_admin", tenant_id=9),
            reader_session=ReaderSession(),
            writer_session=object(),
        )
    )
    assert result["revision_id"] == after["revision_id"]
    assert next(value for name, value in calls if name == "build")["stored_licenses"] == [
        current
    ]
    assert any(name == mutation_name.removesuffix("_compliance") for name, _ in calls)
    assert any(name == "commit" and value["operation"] == operation for name, value in calls)
    assert [name for name, _ in calls[:3]] == ["read", "reader-rollback", "build"]


@pytest.mark.parametrize(
    ("builder_error", "public_error"),
    (
        (ValueError("STALE_VERSION"), "STALE_VERSION"),
        (
            ValueError("DIRECT_ONBOARDING_STATE_CONFLICT"),
            "DIRECT_ONBOARDING_STATE_CONFLICT",
        ),
        (
            RuntimeError("DIRECT_ONBOARDING_DEPENDENCY_UNAVAILABLE"),
            "DEPENDENCY_UNAVAILABLE",
        ),
    ),
)
def test_合规构造期历史摘要拒绝翻译为冻结安全错误(
    monkeypatch, builder_error: Exception, public_error: str
) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api

    class Repository:
        def __init__(self, _session) -> None:
            pass

        async def read_current_compliance(self, **_values):
            return [{"onboarding_id": UUID("019948b0-0000-7000-8000-000000000691")}]

    def reject(**_values):
        raise builder_error

    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", Repository)
    monkeypatch.setattr(api, "build_compliance_mutation", reject)

    class ReaderSession:
        async def rollback(self):
            return None

    with pytest.raises(api.DirectOnboardingError, match=public_error):
        asyncio.run(
            api._mutate_compliance(
                operation="COMPLIANCE_SUBMIT",
                payload=_compliance_request(),
                idempotency_key="synthetic-rejected-submit",
                current_user=CurrentUser(id=18, role="org_admin", tenant_id=9),
                reader_session=ReaderSession(),
                writer_session=object(),
            )
        )


def test_合规Reader回滚失败时不得进入Writer(monkeypatch) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api

    calls: list[str] = []

    class Repository:
        def __init__(self, session) -> None:
            if session == "writer":
                calls.append("writer-created")

        async def read_current_compliance(self, **_values):
            calls.append("read")
            return [{"onboarding_id": UUID("019948b0-0000-7000-8000-000000000692")}]

    class ReaderSession:
        async def rollback(self):
            calls.append("reader-rollback")
            raise RuntimeError("synthetic reader rollback failure")

    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", Repository)
    monkeypatch.setattr(
        api,
        "build_compliance_mutation",
        lambda **_values: (_ for _ in ()).throw(
            AssertionError("builder must not run after reader rollback failure")
        ),
    )
    with pytest.raises(RuntimeError, match="synthetic reader rollback failure"):
        asyncio.run(
            api._mutate_compliance(
                operation="COMPLIANCE_SAVE",
                payload=_compliance_request(),
                idempotency_key="synthetic-reader-rollback-failure",
                current_user=CurrentUser(id=18, role="org_admin", tenant_id=9),
                reader_session=ReaderSession(),
                writer_session="writer",
            )
        )
    assert calls == ["read", "reader-rollback"]

@pytest.mark.parametrize(
    ("function_name", "database_code", "public_code"),
    (
        ("direct_compliance_save_v1", "DIRECT_COMPLIANCE_SAVE_INVALID", "INVALID_REQUEST"),
        ("direct_compliance_save_v1", "DIRECT_COMPLIANCE_SAVE_CONFLICT", "DIRECT_ONBOARDING_STATE_CONFLICT"),
        ("direct_compliance_submit_v1", "DIRECT_COMPLIANCE_SUBMIT_FORBIDDEN", "ROLE_FORBIDDEN"),
        ("direct_compliance_save_replay_v1", "IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_CONFLICT"),
    ),
)
def test_合规数据库拒绝只按注册调用点翻译安全错误(
    function_name: str, database_code: str, public_code: str
) -> None:
    import asyncpg
    from sqlalchemy.exc import DBAPIError

    from app.modules.direct_institution_onboarding.repository import (
        _database_error,
    )

    error = DBAPIError(
        "redacted",
        {},
        asyncpg.exceptions.RaiseError(database_code),
        False,
    )
    assert _database_error(error, function_name) == public_code
    assert _database_error(error, "unregistered_function") is None


def test_直开列表只在权威has_more时签发绑定上界的下一页游标(monkeypatch) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api

    _install_direct_keyrings(monkeypatch)
    first_id = UUID("019948b0-0000-7000-8000-000000000711")
    ceiling_id = UUID("019948b0-0000-7000-8000-000000000719")
    calls = []

    class Repository:
        async def read_rows(self, **values):
            calls.append(values)
            return [{
                "onboarding_id": first_id,
                "tenant_id": UUID("019948b0-0000-7000-8000-000000000712"),
                "institution_code": "SYNTHETIC-711",
                "institution_name": "合成机构",
                "institution_type": "HEALTH_STORE",
                "administrative_region_id": 711,
                "status": "PENDING_ACTIVATION",
                "compliance_due_at": None,
                "current_revision_id": None,
                "version": 1,
                "snapshot_ceiling": ceiling_id,
                "has_more": True,
            }]

    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", lambda _: Repository())
    actor = CurrentUser(id=19, role="super_admin")
    first = asyncio.run(api.list_direct_onboardings(
        cursor=None, limit=1, current_user=actor, session=object()
    ))
    assert len(first["items"]) == 1
    assert first["next_cursor"] is not None
    assert "snapshot_ceiling" not in first["items"][0]
    assert "has_more" not in first["items"][0]

    asyncio.run(api.list_direct_onboardings(
        cursor=first["next_cursor"], limit=1, current_user=actor, session=object()
    ))
    assert calls[1]["cursor_id"] == first_id
    assert calls[1]["ceiling_id"] == ceiling_id


def test_直开机构登录读取使用Application受限函数并保留凭证来源() -> None:
    from app.modules.auth.repository import get_direct_org_admin_login_account

    tenant_public_id = UUID("019948b0-0000-7000-8000-000000000611")
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000612")
    credential_id = UUID("019948b0-0000-7000-8000-000000000613")

    class Session:
        async def execute(self, statement):
            self.statement = statement

            class Result:
                def mappings(self):
                    return self

                def one_or_none(self):
                    return {
                        "source_kind": "DIRECT_ACTIVATION",
                        "tenant_public_id": tenant_public_id,
                        "onboarding_id": onboarding_id,
                        "credential_id": credential_id,
                        "totp_secret_ciphertext": b"synthetic-ciphertext",
                        "totp_key_id": "totp-v1",
                        "totp_enabled": True,
                        "account_version": 1,
                        "user_status": "active",
                        "tenant_status": "active",
                        "root_status": "ACTIVE_COMPLIANCE_PENDING",
                    }

            return Result()

    session = Session()
    account = asyncio.run(get_direct_org_admin_login_account(session, 92))
    assert account is not None
    assert account.source_kind == "DIRECT_ACTIVATION"
    assert account.credential_id == credential_id
    assert "direct_org_admin_login_v1" in str(session.statement)
    assert session.statement.compile().params == {"user_id": 92}


def test_直开机构管理员登录按凭证范围解密并签发TOTP_AMR(monkeypatch) -> None:
    from app.modules.auth import service
    from app.modules.auth.schemas import AuthLoginRequest

    user = SimpleNamespace(
        id=93,
        phone="13900000003",
        password_hash="synthetic-hash",
        role="org_admin",
        status="active",
        tenant_id=17,
        tenant_org_id=170,
        exited_at=None,
        deletion_requested_at=None,
    )
    direct = SimpleNamespace(
        source_kind="DIRECT_ACTIVATION",
        tenant_public_id=UUID("019948b0-0000-7000-8000-000000000621"),
        onboarding_id=UUID("019948b0-0000-7000-8000-000000000622"),
        credential_id=UUID("019948b0-0000-7000-8000-000000000623"),
        totp_secret_ciphertext=b"synthetic-ciphertext",
        totp_key_id="totp-v1",
        totp_enabled=True,
    )

    async def value(*_args, **_kwargs):
        return user

    async def none(*_args, **_kwargs):
        return None

    async def direct_value(*_args, **_kwargs):
        return direct

    observed = {}
    monkeypatch.setattr(service, "get_user_by_phone", value)
    monkeypatch.setattr(service, "verify_password", lambda *_: True)
    monkeypatch.setattr(service, "get_onboarding_account_for_login", none)
    monkeypatch.setattr(service, "get_direct_org_admin_login_account", direct_value)
    monkeypatch.setattr(service, "get_controlled_auth_context", lambda _user: {})
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(jwt_access_token_expire_minutes=30),
    )
    monkeypatch.setattr(service, "open_totp_secret", lambda ciphertext, *, key_id, aad: observed.update(ciphertext=ciphertext, key_id=key_id, aad=aad) or "synthetic-secret")
    monkeypatch.setattr("app.modules.institution_onboarding.domain.verify_totp", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "create_access_token", lambda claims: observed.update(claims=claims) or "synthetic-token")

    response = asyncio.run(
        service.login_user(
            object(),
            AuthLoginRequest(
                phone=user.phone,
                password="Synthetic-password-123",
                totp_code="123456",
            ),
        )
    )
    assert response.access_token == "synthetic-token"
    assert observed["key_id"] == "totp-v1"
    assert observed["aad"] == (
        b"phase1/direct-institution/org-admin-totp/v2\0"
        b"019948b0-0000-7000-8000-000000000621\0"
        b"019948b0-0000-7000-8000-000000000622\0DIRECT_ACTIVATION\0"
        b"019948b0-0000-7000-8000-000000000623"
    )
    assert observed["claims"]["amr"] == ["pwd", "totp"]


def test_机构管理员登录受控与直开双来源必须fail_closed(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.modules.auth import service
    from app.modules.auth.schemas import AuthLoginRequest

    user = SimpleNamespace(
        id=94,
        phone="13900000004",
        password_hash="synthetic-hash",
        role="org_admin",
        status="active",
        tenant_id=18,
        tenant_org_id=180,
        exited_at=None,
        deletion_requested_at=None,
    )

    async def user_value(*_args, **_kwargs):
        return user

    async def account_value(*_args, **_kwargs):
        return SimpleNamespace(totp_enabled=True)

    monkeypatch.setattr(service, "get_user_by_phone", user_value)
    monkeypatch.setattr(service, "verify_password", lambda *_: True)
    monkeypatch.setattr(service, "get_onboarding_account_for_login", account_value)
    monkeypatch.setattr(service, "get_direct_org_admin_login_account", account_value)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            service.login_user(
                object(),
                AuthLoginRequest(
                    phone=user.phone,
                    password="Synthetic-password-123",
                    totp_code="123456",
                ),
            )
        )
    assert caught.value.status_code == 403
    assert caught.value.detail == "Login context is not configured"


def test_直开机构码使用同一UUIDv7的完整32位十六进制() -> None:
    from app.modules.direct_institution_onboarding.service import (
        direct_institution_code,
    )

    onboarding_id = UUID("019948b0-0000-7000-8000-0000000006f1")
    value = direct_institution_code(onboarding_id)
    assert value == onboarding_id.hex.upper()
    assert len(value) == 32
    assert value.isascii()


def test_平台StepUp只返回匹配窗口的精确TOTP时间步() -> None:
    from app.modules.direct_institution_onboarding.service import accepted_totp_step
    from app.modules.institution_onboarding.domain import generate_totp

    secret = "JBSWY3DPEHPK3PXP"
    expected_step = int(NOW.timestamp()) // 30 - 1
    code = generate_totp(secret, at=NOW - timedelta(seconds=30))
    assert accepted_totp_step(secret, code, at=NOW) == expected_step
    assert accepted_totp_step(secret, "000000" if code != "000000" else "999999", at=NOW) is None


def test_CREATE_step_up通过受限函数且UUID与摘要精确绑定() -> None:
    from app.modules.direct_institution_onboarding.repository import (
        DirectInstitutionOnboardingRepository,
    )

    class Result:
        def scalar_one(self):
            return {"profile_version": 7}

    class Session:
        async def execute(self, statement, parameters):
            self.statement = statement
            self.parameters = parameters
            return Result()

    session = Session()
    value = asyncio.run(
        DirectInstitutionOnboardingRepository(session).create_step_up_begin(
            actor_user_id=17,
            actor_scope="user:17:platform",
            idempotency_key="create-key",
            request_digest="a" * 64,
            phone_digest_key_id="phone-v1",
            phone_digest="b" * 64,
        )
    )
    assert value == {"profile_version": 7}
    assert "direct_create_step_up_begin_v1" in str(session.statement)
    assert session.parameters == {
        "actor_user_id": 17,
        "actor_scope": "user:17:platform",
        "idempotency_key": "create-key",
        "request_digest": "a" * 64,
        "phone_digest_key_id": "phone-v1",
        "phone_digest": "b" * 64,
    }


def test_CREATE_mutation不持久TOTP和明文凭据且不伪造内部Tenant(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.schemas import DirectCreateRequest
    from app.modules.direct_institution_onboarding.service import (
        build_direct_create_mutation,
        direct_create_request_digest,
    )

    _install_direct_keyrings(monkeypatch)
    request = DirectCreateRequest(
        institution_name="合成直开机构",
        institution_type="HEALTH_STORE",
        admin_phone="13900000008",
        administrative_region_id=701,
        duplicate_acknowledged=False,
        reason_code="SYNTHETIC_CREATE",
        totp_code="123456",
    )
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(701, 708)
    )
    envelope, confirmation, activation_code = build_direct_create_mutation(
        request=request,
        actor_user_id=17,
        idempotency_key="synthetic-create-key",
        accepted_totp_step=123456,
        id_factory=lambda: next(ids),
        activation_code="synthetic-one-time-credential",
    )
    assert activation_code == "synthetic-one-time-credential"
    assert envelope["institution_code"] == UUID(envelope["onboarding_id"]).hex.upper()
    assert envelope["request_digest"] == direct_create_request_digest(request)
    assert "totp_code" not in json.dumps(envelope)
    assert "synthetic-one-time-credential" not in json.dumps(envelope)
    assert "admin_phone" not in envelope
    assert envelope["admin_phone_ciphertext"]
    assert "tenant_id" not in confirmation
    assert confirmation["tenant_public_id"] == envelope["tenant_public_id"]
    assert confirmation["target_id"] == envelope["onboarding_id"]
    assert confirmation["credential_id"] == envelope["credential_id"]


def test_CREATE_request_digest不因新TOTP时间步变化以便同Key识别原请求(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.schemas import DirectCreateRequest
    from app.modules.direct_institution_onboarding.service import (
        direct_create_request_digest,
    )

    _install_direct_keyrings(monkeypatch)
    values = {
        "institution_name": "合成直开机构",
        "institution_type": "HEALTH_STORE",
        "admin_phone": "13900000008",
        "administrative_region_id": 701,
        "duplicate_acknowledged": False,
        "reason_code": "SYNTHETIC_CREATE",
    }
    first = DirectCreateRequest(**values, totp_code="123456")
    second = DirectCreateRequest(**values, totp_code="654321")
    assert direct_create_request_digest(first) == direct_create_request_digest(second)


def test_ACTIVATE只信任受限Authority并且Mutation不持久化明文凭据和TOTP(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.schemas import (
        DirectActivationRequest,
    )
    from app.modules.direct_institution_onboarding.service import (
        build_direct_activation_mutation,
    )

    _install_direct_keyrings(monkeypatch)
    onboarding_id = UUID("019948b0-0000-7000-8000-000000000901")
    tenant_id = UUID("019948b0-0000-7000-8000-000000000902")
    credential_id = UUID("019948b0-0000-7000-8000-000000000903")
    request = DirectActivationRequest(
        onboarding_id=onboarding_id,
        credential_id=credential_id,
        activation_code="synthetic-activation-code",
        phone="13900000008",
        password="SyntheticPassword123!",
        totp_secret="JBSWY3DPEHPK3PXP",
        totp_code="123456",
        expected_version=1,
    )
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(904, 908)
    )
    envelope, confirmation = build_direct_activation_mutation(
        request=request,
        authority={
            "tenant_public_id": tenant_id,
            "onboarding_id": onboarding_id,
            "credential_id": credential_id,
            "source_kind": "DIRECT_ACTIVATION",
            "root_version": 1,
            "phone_digest_key_id": "direct-purpose-5",
            "credential_digest_key_id": "direct-purpose-2",
        },
        idempotency_key="synthetic-activate-key",
        accepted_totp_step=123456,
        id_factory=lambda: next(ids),
    )
    serialized = json.dumps(envelope)
    assert envelope["onboarding_id"] == str(onboarding_id)
    assert envelope["credential_id"] == str(credential_id)
    assert envelope["expected_version"] == 1
    assert envelope["phone"] == request.phone
    for secret in (
        request.activation_code,
        request.password,
        request.totp_secret,
    ):
        assert secret not in serialized
    assert "totp_code" not in envelope
    assert confirmation["target_id"] == str(onboarding_id)
    assert confirmation["target_status"] == "ACTIVE_COMPLIANCE_PENDING"
    assert "tenant_id" not in confirmation
    assert "user_id" not in confirmation


def test_管理员交接创建重生与激活只使用受限前像且不持久明文凭据(monkeypatch) -> None:
    from app.modules.direct_institution_onboarding.schemas import (
        AdminHandoffActivationRequest,
        AdminHandoffCreateRequest,
    )
    from app.modules.direct_institution_onboarding.service import (
        DirectInstitutionCrypto,
        DirectOnboardingSecrets,
        build_admin_handoff_activation_mutation,
        build_admin_handoff_create_mutation,
        build_admin_handoff_regenerate_mutation,
        handoff_phone_aad,
    )

    _install_direct_keyrings(monkeypatch)
    onboarding_id = UUID("019948b0-0000-7000-8000-000000001001")
    tenant_public_id = UUID("019948b0-0000-7000-8000-000000001002")
    create_request = AdminHandoffCreateRequest(
        new_phone="13900001001",
        expected_version=8,
        reason_code="SYNTHETIC_HANDOFF",
        totp_code="123456",
    )
    ids = iter(
        UUID(f"019948b0-0000-7000-8000-{value:012d}")
        for value in range(1003, 1018)
    )
    created, create_confirmation, credential = build_admin_handoff_create_mutation(
        onboarding_id=onboarding_id,
        request=create_request,
        authority={
            "onboarding_id": onboarding_id,
            "tenant_id": 77,
            "tenant_public_id": tenant_public_id,
            "target_version": 8,
        },
        actor_user_id=17,
        idempotency_key="synthetic-handoff-create",
        accepted_totp_step=123,
        id_factory=lambda: next(ids),
        activation_code="synthetic-handoff-credential",
    )
    serialized = json.dumps(created)
    assert credential == "synthetic-handoff-credential"
    assert create_request.new_phone not in serialized
    assert credential not in serialized
    assert "totp_code" not in created
    assert create_confirmation["tenant_id"] == 77
    assert create_confirmation["phone_claim_state"] == "PENDING"
    secrets = DirectOnboardingSecrets()
    crypto = DirectInstitutionCrypto(
        pii_current_key_id=secrets.pii_key_id,
        pii_keys=secrets.pii_keys,
        digest_current_key_id=secrets.digest_key_id,
        digest_keys=secrets.digest_keys,
    )
    assert crypto.open_text(
        base64.b64decode(created["new_phone_ciphertext"], validate=True),
        key_id=created["new_phone_key_id"],
        aad=handoff_phone_aad(
            tenant_public_id, onboarding_id, UUID(created["handoff_id"])
        ),
    ) == create_request.new_phone

    regenerate_request = VersionedStepUpRequest(
        expected_version=1,
        reason_code="SYNTHETIC_REGENERATE",
        totp_code="654321",
    )
    regenerated, regenerate_confirmation, regenerated_credential = (
        build_admin_handoff_regenerate_mutation(
            onboarding_id=onboarding_id,
            handoff_id=UUID(created["handoff_id"]),
            request=regenerate_request,
            authority={
                "onboarding_id": onboarding_id,
                "handoff_id": created["handoff_id"],
                "tenant_id": 77,
                "target_version": 1,
                "active_credential_id": created["credential_id"],
            },
            actor_user_id=18,
            idempotency_key="synthetic-handoff-regenerate",
            accepted_totp_step=124,
            id_factory=lambda: next(ids),
            activation_code="synthetic-regenerated-credential",
        )
    )
    assert regenerated_credential == "synthetic-regenerated-credential"
    assert regenerated_credential not in json.dumps(regenerated)
    assert regenerated["old_credential_id"] == created["credential_id"]
    assert regenerate_confirmation["target_version"] == 2

    activation_request = AdminHandoffActivationRequest(
        onboarding_id=onboarding_id,
        handoff_id=UUID(created["handoff_id"]),
        credential_id=UUID(regenerated["new_credential_id"]),
        activation_code=regenerated_credential,
        phone=create_request.new_phone,
        password="SyntheticPassword123!",
        totp_secret="JBSWY3DPEHPK3PXP",
        totp_code="123456",
        expected_version=2,
    )
    activated, activation_confirmation = build_admin_handoff_activation_mutation(
        request=activation_request,
        authority={
            "tenant_public_id": tenant_public_id,
            "onboarding_id": onboarding_id,
            "handoff_id": created["handoff_id"],
            "credential_id": regenerated["new_credential_id"],
            "handoff_version": 2,
            "phone_digest_key_id": "direct-purpose-5",
            "credential_digest_key_id": "direct-purpose-2",
        },
        idempotency_key="synthetic-handoff-activate",
        accepted_totp_step=125,
        id_factory=lambda: next(ids),
    )
    activation_serialized = json.dumps(activated)
    assert regenerated_credential not in activation_serialized
    assert activation_request.password not in activation_serialized
    assert activation_request.totp_secret not in activation_serialized
    assert "totp_code" not in activated
    assert activation_confirmation["phone_claim_state"] == "BOUND"
    assert activation_confirmation["target_status"] == "ACTIVATED"


def test_CREATE同Key已签发时在生成新明文和StepUp之前拒绝(monkeypatch) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api
    from app.modules.direct_institution_onboarding.schemas import DirectCreateRequest

    _install_direct_keyrings(monkeypatch)
    calls: list[str] = []

    class Repository:
        def __init__(self, _session):
            pass

        async def review_replay(self, _envelope):
            calls.append("replay")
            return {
                "onboarding_id": "019948b0-0000-7000-8000-000000000801",
                "credential_delivery_state": "ALREADY_ISSUED",
            }

        async def create_step_up_begin(self, **_values):
            calls.append("step-up")
            raise AssertionError("replay must reject before step-up")

    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", Repository)
    monkeypatch.setattr(
        api,
        "build_direct_create_mutation",
        lambda **_values: (_ for _ in ()).throw(
            AssertionError("replay must not generate a new credential")
        ),
    )
    payload = DirectCreateRequest(
        institution_name="合成直开机构",
        institution_type="HEALTH_STORE",
        admin_phone="13900000008",
        administrative_region_id=701,
        duplicate_acknowledged=False,
        reason_code="SYNTHETIC_CREATE",
        totp_code="123456",
    )
    with pytest.raises(
        api.DirectOnboardingError,
        match="ONE_TIME_CREDENTIAL_ALREADY_ISSUED",
    ):
        asyncio.run(
            api.create_direct_onboarding(
                payload=payload,
                response=SimpleNamespace(headers={}),
                idempotency_key="synthetic-create-key",
                current_user=CurrentUser(id=17, role="super_admin"),
                session=object(),
            )
        )
    assert calls == ["replay"]


def test_CREATE首次成功返回可直接激活的凭据引用且no_store(monkeypatch) -> None:
    from app.core.security import CurrentUser
    from app.modules.direct_institution_onboarding import api
    from app.modules.direct_institution_onboarding.schemas import DirectCreateRequest

    _install_direct_keyrings(monkeypatch)
    onboarding_id = "019948b0-0000-7000-8000-000000000811"
    tenant_id = "019948b0-0000-7000-8000-000000000812"
    credential_id = "019948b0-0000-7000-8000-000000000813"
    expires_at = datetime(2026, 10, 14, tzinfo=UTC)
    calls: list[str] = []

    class Session:
        async def commit(self):
            calls.append("commit")

        async def rollback(self):
            calls.append("rollback")

    class Repository:
        def __init__(self, _session):
            pass

        async def review_replay(self, _envelope):
            calls.append("replay")
            return None

        async def create_step_up_begin(self, **_values):
            calls.append("step-up")
            return {
                "profile_secret_ciphertext": base64.b64encode(b"x" * 32).decode(),
                "profile_key_id": "totp-v1",
                "profile_version": 3,
            }

        async def create(self, envelope):
            calls.append("create")
            return {
                "onboarding_id": envelope["onboarding_id"],
                "tenant_id": envelope["tenant_public_id"],
                "credential_id": envelope["credential_id"],
                "credential_delivery_state": "ISSUED",
                "credential_expires_at": expires_at,
                "status": "PENDING_ACTIVATION",
                "version": 1,
            }

    envelope = {
        "onboarding_id": onboarding_id,
        "tenant_public_id": tenant_id,
        "credential_id": credential_id,
        "institution_code": UUID(onboarding_id).hex.upper(),
    }
    monkeypatch.setattr(api, "DirectInstitutionOnboardingRepository", Repository)
    monkeypatch.setattr(api, "open_totp_secret", lambda *_args, **_kwargs: "secret")
    monkeypatch.setattr(api, "accepted_totp_step", lambda *_args, **_kwargs: 123)
    monkeypatch.setattr(
        api,
        "build_direct_create_mutation",
        lambda **_values: (envelope, {"operation": "CREATE"}, "one-time-code"),
    )
    payload = DirectCreateRequest(
        institution_name="合成直开机构",
        institution_type="HEALTH_STORE",
        admin_phone="13900000008",
        administrative_region_id=701,
        duplicate_acknowledged=False,
        reason_code="SYNTHETIC_CREATE",
        totp_code="123456",
    )
    response = SimpleNamespace(headers={})
    result = asyncio.run(
        api.create_direct_onboarding(
            payload=payload,
            response=response,
            idempotency_key="synthetic-create-key",
            current_user=CurrentUser(id=17, role="super_admin"),
            session=Session(),
        )
    )
    assert result["credential_id"] == credential_id
    assert result["activation_code"] == "one-time-code"
    assert result["credential_expires_at"] == expires_at
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == ["replay", "step-up", "replay", "create", "commit"]
