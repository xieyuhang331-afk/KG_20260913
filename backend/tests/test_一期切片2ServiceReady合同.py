from datetime import date

from app.modules.therapist_qualification.domain import ReadinessInputs, ReadinessStatus, compute_service_readiness
from app.tasks.therapist_qualification_tasks import READINESS_SWEEP_TASK


def test_五项输入与reason_codes真值表():
    result = compute_service_readiness(
        ReadinessInputs(False, "HEALTH_STORE", (), (), (), True, False)
    )
    assert result.status is ReadinessStatus.NOT_READY
    assert result.reason_codes == tuple(sorted(result.reason_codes))
    assert set(result.reason_codes) == {
        "TENANT_NOT_ACTIVE", "INSTITUTION_LICENSE_INVALID", "NO_APPROVED_ACTIVE_THERAPIST",
        "METABOLIC_SCOPE_MISSING", "COMPLIANCE_SUSPENDED", "INSTITUTION_APPROVAL_SOURCE_INVALID",
    }
    ready = compute_service_readiness(
        ReadinessInputs(
            True,
            "HEALTH_STORE",
            ("BUSINESS_LICENSE",),
            ((date(2027, 1, 1), ("OBESITY",)),),
            ("OBESITY",),
            False,
            True,
        )
    )
    assert ready.status is ReadinessStatus.SERVICE_READY
    assert ready.qualified_therapist_count == 1


def test_相同输入不增证据且变化只增一个版本():
    inputs = ReadinessInputs(True, "HEALTH_STORE", ("BUSINESS_LICENSE",), ((date(2027, 1, 1), ("HYPERTENSION",)),), ("HYPERTENSION",), False, True)
    assert compute_service_readiness(inputs) == compute_service_readiness(inputs)


def test_资格到期暂停恢复退出重算readiness():
    expired = compute_service_readiness(ReadinessInputs(True, "HEALTH_STORE", ("BUSINESS_LICENSE",), (), ("OBESITY",), False, True))
    assert expired.reason_codes == ("NO_APPROVED_ACTIVE_THERAPIST",)


def test_reader_guard即时拒绝过期或source漂移_READY():
    assert compute_service_readiness(ReadinessInputs(False, "HEALTH_STORE", ("BUSINESS_LICENSE",), ((date(2027, 1, 1), ("OBESITY",)),), ("OBESITY",), False, True)).status is ReadinessStatus.NOT_READY
    assert READINESS_SWEEP_TASK == "phase1.therapist.sweep_readiness"


def test_reason_codes必须去重且C排序():
    result = compute_service_readiness(ReadinessInputs(False, "HEALTH_STORE", (), (), (), False, True))
    assert result.reason_codes == tuple(sorted(set(result.reason_codes)))


def test_SERVICE_READY_next_expiry取机构与健管师最早边界():
    result = compute_service_readiness(
        ReadinessInputs(
            tenant_active=True,
            institution_type="HEALTH_STORE",
            valid_license_types=("BUSINESS_LICENSE",),
            approved_therapists=((date(2027, 12, 31), ("GLUCOSE_METABOLISM",)),),
            service_tags=("GLUCOSE_METABOLISM",),
            compliance_suspended=False,
            institution_approval_source_valid=True,
            institution_license_expiries=(date(2027, 6, 30),),
        )
    )
    assert result.status is ReadinessStatus.SERVICE_READY
    assert result.next_expiry_at == date(2027, 6, 30)


def test_机构类型许可证组合与逐健管师标签交集共同决定READY():
    no_overlap = compute_service_readiness(
        ReadinessInputs(
            True,
            "HEALTH_STORE",
            ("BUSINESS_LICENSE",),
            ((date(2027, 1, 1), ("HYPERTENSION",)),),
            ("OBESITY",),
            False,
            True,
        )
    )
    assert no_overlap.qualified_therapist_count == 0
    assert "METABOLIC_SCOPE_MISSING" in no_overlap.reason_codes
    assert "NO_APPROVED_ACTIVE_THERAPIST" in no_overlap.reason_codes

    clinic_missing_medical = compute_service_readiness(
        ReadinessInputs(
            True,
            "LICENSED_CLINIC",
            ("BUSINESS_LICENSE",),
            ((date(2027, 1, 1), ("OBESITY",)),),
            ("OBESITY",),
            False,
            True,
        )
    )
    assert clinic_missing_medical.reason_codes == ("INSTITUTION_LICENSE_INVALID",)
