from pathlib import Path

import pytest
from pydantic import ValidationError

from app.modules.therapist_qualification.schemas import (
    InvitationDTO,
    ProfileDTO,
    SelfProfileDTO,
    TherapistInvitationCreate,
    TherapistProfilePatch,
    TherapistResubmit,
)
from app.modules.therapist_qualification.service import _mask_phone


def test_平台机构与健管师API无延期能力():
    api = (Path(__file__).parents[1] / "app/modules/therapist_qualification/api.py").read_text(encoding="utf-8")
    for token in ("therapist-invitations", "qualifications/submit", "therapist-reviews", "service-readiness"):
        assert token in api
    for forbidden in ("sms", "ocr", "identity-card", "service-case"):
        assert forbidden not in api.lower()


def test_手机号规范化掩码与补正字段保持严格typed():
    assert TherapistInvitationCreate(phone="138 0000-0000", expires_in_minutes=30).phone == "13800000000"
    assert _mask_phone("13800000000") == "*******0000"
    for invalid in ("12800000000", "1380000000", "138000000000", "١٣٨٠٠٠٠٠٠٠٠"):
        with pytest.raises(ValidationError):
            TherapistInvitationCreate(phone=invalid, expires_in_minutes=30)

    patch = TherapistProfilePatch(display_name="新名称", service_tags=("OBESITY",))
    value = TherapistResubmit(
        expected_version=2,
        profile_changes=patch,
        qualification={
            "qualification_type": "METABOLIC_HEALTH_PRACTICE",
            "certificate_no": "ABCD1234",
            "issuer_name": "issuer",
            "valid_from": "2026-01-01",
            "valid_until": "2027-01-01",
            "attachment_file_ids": ["00000000-0000-7000-8000-000000000001"],
        },
        decision_id="00000000-0000-7000-8000-000000000002",
    )
    assert value.profile_changes.model_dump(exclude_unset=True) == {
        "display_name": "新名称",
        "service_tags": ("OBESITY",),
    }
    with pytest.raises(ValidationError):
        TherapistProfilePatch(service_tags=("UNKNOWN",))


def test_邀请列表显式映射masked_phone且不暴露敏感列():
    from app.modules.therapist_qualification.api import _invitation_dto

    value = _invitation_dto({
        "invitation_id": "00000000-0000-7000-8000-000000000001",
        "phone_masked": "*******0000",
        "status": "INVITED",
        "expires_at": "2026-08-17T00:00:00+08:00",
        "issued_at": "2026-08-16T00:00:00+08:00",
        "activated_at": None,
        "revoked_at": None,
        "version": 1,
    })
    assert InvitationDTO.model_validate(value).masked_phone == "*******0000"
    assert set(value) == set(InvitationDTO.model_fields)
    assert not {"phone", "phone_digest", "phone_ciphertext"} & set(value)


def test_ACTIVATED空profile可读但不虚构资料():
    public = ProfileDTO.model_validate({
        "therapist_id": "00000000-0000-7000-8000-000000000001",
        "tenant_id": "00000000-0000-7000-8000-000000000002",
        "display_name": None,
        "practice_summary": None,
        "status": "ACTIVATED",
        "service_tags": None,
        "capacity_limit": 30,
        "active_case_count": 0,
        "qualification_valid_until": None,
        "current_revision_no": 0,
        "version": 1,
        "updated_at": "2026-08-16T00:00:00+08:00",
    })
    private = SelfProfileDTO.model_validate({**public.model_dump(), "real_name": None})
    assert private.display_name is None
    assert private.practice_summary is None
    assert private.service_tags is None
    assert private.real_name is None


@pytest.mark.parametrize("field", ("real_name", "display_name", "practice_summary", "service_tags"))
def test_resubmit显式null在schema边界拒绝(field: str):
    with pytest.raises(ValidationError):
        TherapistProfilePatch.model_validate({field: None})
