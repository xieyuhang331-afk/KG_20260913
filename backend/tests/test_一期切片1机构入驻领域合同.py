import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID

import pytest

from app.modules.institution_onboarding.domain import (
    ApplicationStatus,
    InstitutionApplication,
    InstitutionInvitation,
    InvitationStatus,
    OnboardingConflict,
    OnboardingForbidden,
    generate_totp,
    verify_totp,
)
from app.modules.institution_onboarding.service import review_draft_projection


NOW = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
INVITATION_ID = UUID("0198a58f-4900-7000-8000-000000000001")
APPLICATION_ID = UUID("0198a58f-4900-7000-8000-000000000002")


def invitation() -> InstitutionInvitation:
    return InstitutionInvitation.issue(
        invitation_id=INVITATION_ID,
        institution_name="康邻健康门店",
        institution_type="HEALTH_STORE",
        applicant_phone_digest="1" * 64,
        pilot_batch_code="PILOT-01",
        administrative_region_id=41,
        code_digest="2" * 64,
        expires_at=NOW + timedelta(hours=1),
        issued_by=7,
        now=NOW,
    )


def complete_draft() -> dict:
    return {
        "credit_code_digest": "3" * 64,
        "legal_representative_name": "张负责人",
        "registered_address": "上海市示范区健康路1号",
        "service_address": "上海市示范区康邻路2号",
        "contact_name": "李联系人",
        "contact_phone_digest": "4" * 64,
        "contact_email": "contact@example.invalid",
        "service_tags": ("HYPERTENSION", "OBESITY"),
    }


def test_邀请冻结字段与激活安全状态机():
    value = invitation()
    assert value.status is InvitationStatus.ISSUED
    assert value.institution_name == "康邻健康门店"
    assert value.institution_type == "HEALTH_STORE"

    with pytest.raises(OnboardingForbidden, match="ONBOARDING_INVITATION_PHONE_MISMATCH"):
        value.activate(
            phone_digest="9" * 64,
            code_digest="2" * 64,
            now=NOW,
        )
    assert value.failed_attempts == 1

    value.activate(phone_digest="1" * 64, code_digest="2" * 64, now=NOW)
    assert value.status is InvitationStatus.ACTIVATED
    with pytest.raises(OnboardingConflict, match="ONBOARDING_INVITATION_STATE_CONFLICT"):
        value.activate(phone_digest="1" * 64, code_digest="2" * 64, now=NOW)


@pytest.mark.parametrize("terminal", ["expired", "revoked"])
def test_过期或撤销邀请不可激活(terminal: str):
    value = invitation()
    if terminal == "revoked":
        value.revoke(now=NOW)
        attempt_at = NOW
    else:
        attempt_at = NOW + timedelta(hours=2)
    with pytest.raises(OnboardingConflict):
        value.activate(phone_digest="1" * 64, code_digest="2" * 64, now=attempt_at)


def test_重发使旧码失效并重置尝试预算():
    value = invitation()
    with pytest.raises(OnboardingForbidden):
        value.activate(phone_digest="1" * 64, code_digest="0" * 64, now=NOW)
    value.resend(code_digest="5" * 64, expires_at=NOW + timedelta(hours=2), now=NOW)
    assert value.failed_attempts == 0
    assert value.version == 2
    with pytest.raises(OnboardingForbidden):
        value.activate(phone_digest="1" * 64, code_digest="2" * 64, now=NOW)
    value.activate(phone_digest="1" * 64, code_digest="5" * 64, now=NOW)


def test_短码尝试上限fail_closed():
    value = invitation()
    for _ in range(5):
        with pytest.raises(OnboardingForbidden):
            value.activate(phone_digest="1" * 64, code_digest="0" * 64, now=NOW)
    with pytest.raises(OnboardingConflict, match="ONBOARDING_INVITATION_ATTEMPT_LIMIT"):
        value.activate(phone_digest="1" * 64, code_digest="2" * 64, now=NOW)


def test_totp_rfc6238窗口与篡改拒绝():
    secret = "JBSWY3DPEHPK3PXP"
    code = generate_totp(secret, at=NOW)
    assert len(code) == 6 and code.isdigit()
    assert verify_totp(secret, code, at=NOW)
    assert not verify_totp(secret, "000000" if code != "000000" else "999999", at=NOW)
    assert not verify_totp(secret, code, at=NOW + timedelta(minutes=2))


def test_草稿冻结字段_revision不可变与补正范围():
    application = InstitutionApplication.create(
        application_id=APPLICATION_ID,
        invitation_id=INVITATION_ID,
        applicant_user_id=81,
        now=NOW,
    )
    with pytest.raises(OnboardingForbidden, match="ONBOARDING_FROZEN_FIELD_MUTATION"):
        application.save_draft(
            {**complete_draft(), "institution_name": "被篡改"}, expected_version=1, now=NOW
        )

    application.save_draft(complete_draft(), expected_version=1, now=NOW)
    revision = application.submit(
        clean_file_purposes=("BUSINESS_LICENSE",), expected_version=2, now=NOW
    )
    assert application.status is ApplicationStatus.SUBMITTED
    assert revision.revision_no == 1
    assert revision.snapshot["registered_address"] == "上海市示范区健康路1号"

    application.start_review(expected_version=3, now=NOW)
    with pytest.raises(OnboardingForbidden, match="ONBOARDING_CORRECTION_FIELD_FORBIDDEN"):
        application.request_correction(
            fields=("does_not_exist",), reason_code="INVALID_FIELD", expected_version=4, now=NOW
        )
    application.request_correction(
        fields=("registered_address",), reason_code="ADDRESS_UNCLEAR", expected_version=4, now=NOW
    )
    with pytest.raises(OnboardingForbidden, match="ONBOARDING_CORRECTION_FIELD_FORBIDDEN"):
        application.save_correction(
            {"service_address": "不可修改"}, expected_version=5, now=NOW
        )
    application.save_correction(
        {"registered_address": "上海市示范区健康路8号"}, expected_version=5, now=NOW
    )
    revised = application.resubmit(
        clean_file_purposes=("BUSINESS_LICENSE",), expected_version=6, now=NOW
    )
    assert revised.revision_no == 2
    assert revision.snapshot["registered_address"] == "上海市示范区健康路1号"
    assert revised.snapshot["registered_address"] == "上海市示范区健康路8号"


def test_诊所提交必须同时具备两类clean证照():
    application = InstitutionApplication.create(
        application_id=APPLICATION_ID,
        invitation_id=INVITATION_ID,
        applicant_user_id=81,
        institution_type="LICENSED_CLINIC",
        now=NOW,
    )
    application.save_draft(complete_draft(), expected_version=1, now=NOW)
    with pytest.raises(OnboardingConflict, match="ONBOARDING_REQUIRED_LICENSE_MISSING"):
        application.submit(
            clean_file_purposes=("BUSINESS_LICENSE",), expected_version=2, now=NOW
        )


def test_审核员可要求仅替换材料并保持草稿不变():
    application = InstitutionApplication.create(
        application_id=APPLICATION_ID,
        invitation_id=INVITATION_ID,
        applicant_user_id=81,
        now=NOW,
    )
    application.save_draft(complete_draft(), expected_version=1, now=NOW)
    application.submit(
        clean_file_purposes=("BUSINESS_LICENSE",), expected_version=2, now=NOW
    )
    application.start_review(expected_version=3, now=NOW)
    application.request_correction(
        fields=("business_license",),
        reason_code="BUSINESS_LICENSE_REQUIRES_CORRECTION",
        expected_version=4,
        now=NOW,
    )
    original = dict(application.draft)
    application.save_correction({}, expected_version=5, now=NOW)
    revised = application.resubmit(
        clean_file_purposes=("BUSINESS_LICENSE",),
        expected_version=6,
        now=NOW,
    )
    assert dict(revised.snapshot) == original
    assert application.status is ApplicationStatus.SUBMITTED


def test_乐观锁冲突与拒绝终态():
    application = InstitutionApplication.create(
        application_id=APPLICATION_ID,
        invitation_id=INVITATION_ID,
        applicant_user_id=81,
        now=NOW,
    )
    with pytest.raises(OnboardingConflict, match="ONBOARDING_VERSION_CONFLICT"):
        application.save_draft(complete_draft(), expected_version=9, now=NOW)
    application.save_draft(complete_draft(), expected_version=1, now=NOW)
    application.submit(clean_file_purposes=("BUSINESS_LICENSE",), expected_version=2, now=NOW)
    application.start_review(expected_version=3, now=NOW)
    application.reject(reason_code="OUT_OF_SCOPE", expected_version=4, now=NOW)
    assert application.status is ApplicationStatus.REJECTED
    with pytest.raises(OnboardingConflict):
        application.save_draft(complete_draft(), expected_version=5, now=NOW)


def test_审核详情只解密并返回显式业务字段():
    cipher = type(
        "Cipher",
        (),
        {"decrypt": staticmethod(lambda value: value.decode())},
    )()
    payload = {
        "credit_code_ciphertext": base64.b64encode(b"TEST-CREDIT").decode(),
        "credit_code_digest": "a" * 64,
        "contact_phone_ciphertext": base64.b64encode(b"synthetic-contact").decode(),
        "contact_phone_digest": "b" * 64,
        "registered_address": "测试注册地址",
        "unknown_internal_field": "must-not-leak",
    }
    with patch(
        "app.modules.institution_onboarding.service.OnboardingSecrets",
        return_value=cipher,
    ):
        result = review_draft_projection(payload)
    assert result == {
        "credit_code": "TEST-CREDIT",
        "contact_phone": "synthetic-contact",
        "registered_address": "测试注册地址",
    }
