from dataclasses import replace
import asyncio
import inspect
from datetime import date, datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest
from pydantic import ValidationError


def _u(suffix: int) -> UUID:
    return UUID(f"00000000-0000-7000-8000-{suffix:012d}")


def _profile(**changes):
    value = {
        "medical_history": [{"code": "hypertension", "onset_date": "2020-01-01", "resolved_date": None, "status": "ACTIVE", "note": None}],
        "allergies": [], "medications": [], "symptoms": [],
        "pregnancy_status": "NOT_APPLICABLE", "pregnancy_week": None,
        "lactation_status": "NOT_APPLICABLE",
        "reconfirmed_at": "2026-08-20T09:00:00+08:00",
        "source_type": "APP", "expected_version": 0,
    }
    value.update(changes)
    return value


def test_D01_D03_profile是严格全量快照且妊娠真值固定():
    from app.modules.user_health.schemas import FormalHealthProfileSnapshotRequest

    assert FormalHealthProfileSnapshotRequest.model_validate(_profile()).expected_version == 0
    with pytest.raises(ValidationError):
        FormalHealthProfileSnapshotRequest.model_validate(_profile(extra=True))
    for status, week in (("PREGNANT", None), ("NOT_PREGNANT", 3), ("PREGNANT", 46)):
        with pytest.raises(ValidationError):
            FormalHealthProfileSnapshotRequest.model_validate(_profile(pregnancy_status=status, pregnancy_week=week))


def test_D04_D07_正式健康事实拒绝BMI_DEVICE和received_at并冻结时间与初始状态():
    from app.modules.health_fact.domain import compute_bmi, initial_verification_state
    from app.modules.user_health.schemas import FormalHealthFactWriteItem

    base = {"indicator_code": "height", "value": "175.0", "unit": "cm", "measured_at": "2026-08-20T09:00:00+08:00", "source_type": "APP"}
    assert FormalHealthFactWriteItem.model_validate(base).indicator_code == "height"
    for change in ({"indicator_code": "bmi"}, {"source_type": "DEVICE"}, {"received_at": "2026-08-20T09:00:01+08:00"}, {"measured_at": "2026-08-20T09:00:00"}):
        with pytest.raises(ValidationError):
            FormalHealthFactWriteItem.model_validate({**base, **change})
    assert compute_bmi(height_cm=Decimal("175.0"), weight_kg=Decimal("70.0")) == Decimal("22.9")
    assert [initial_verification_state(v) for v in ("APP", "REPORT", "STORE")] == ["SELF_REPORTED", "UNKNOWN", "UNKNOWN"]


def test_D05_D07_member_first事实使用v2真值且不复用代理User():
    from app.modules.health_fact.domain import (
        CanonicalHealthFactDraft,
        HealthFactDigestKeyring,
        HealthFactSourceForbidden,
        prepare_fact,
    )

    keyring = HealthFactDigestKeyring.from_base64(
        current_key_id="health-v2",
        encoded_keys={"health-v2": "YWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWE="},
    )
    draft = CanonicalHealthFactDraft(
        subject_user_id=None,
        subject_member_id=_u(10),
        fact_ref=_u(11),
        catalog_version=2,
        indicator_code="height",
        numeric_value=Decimal("175.0"),
        unit="cm",
        measured_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
        source_type="APP",
        source_identity="member-first-input",
        producer_event_key="slice4:v2:height",
        created_by=7,
    )
    stored = prepare_fact(draft, keyring)
    assert stored.subject_user_id is None
    assert stored.subject_member_id == _u(10)
    assert stored.fact_ref == _u(11)
    assert stored.catalog_version == 2
    with pytest.raises(HealthFactSourceForbidden):
        prepare_fact(replace(draft, source_type="DEVICE"), keyring)


def test_D08_D09_修正计划只追加且保留前事实():
    from app.modules.health_fact.domain import build_status_transition_plan

    old = MappingProxyType({"fact_ref": _u(1), "state": "DISPUTED"})
    plan = build_status_transition_plan(predecessor=old, target_state="SELF_REPORTED", reason_code="MEMBER_CORRECTION", new_fact_ref=_u(2))
    assert plan.supersedes_fact_ref == _u(1)
    assert not plan.updates_predecessor
    assert old["state"] == "DISPUTED"


def test_D10_提交结果只接受完整独立预期后像():
    from app.modules.user_health.service import build_profile_mutation_plan, classify_commit_outcome

    preimage = {"profile_id": str(_u(3)), "current_revision_id": None, "version": 0}
    plan = build_profile_mutation_plan(subject_ref=_u(4), profile_id=_u(3), revision_id=_u(5), actor_user_id=7, created_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc), request_digest="a" * 64, snapshot_digest="b" * 64, preimage=preimage)
    assert classify_commit_outcome(plan=plan, actual=plan.expected_postimage) == "COMMITTED"
    assert classify_commit_outcome(plan=plan, actual=preimage) == "NOT_COMMITTED"
    partial = dict(plan.expected_postimage); partial.pop("audit")
    assert classify_commit_outcome(plan=plan, actual=partial) == "UNKNOWN"


def test_D13_D15_D32_详细档案只在主体与IdentitySummary版本一致时解密(monkeypatch):
    from app.modules.member_enrollment.ports import VerifiedIdentitySummaryV2
    from app.modules.user_health import service

    subject, case_id, enrollment_id = _u(31), _u(32), _u(33)
    profile_id, revision_id, tenant_id = _u(34), _u(35), _u(36)

    class Repository:
        async def clinical_profile(self, **scope):
            assert scope == {
                "actor_user_id": 7,
                "actor_context": "SELF",
                "subject_member_id": subject,
                "service_case_id": case_id,
                "enrollment_id": enrollment_id,
            }
            return {
                "profile_public_id": profile_id,
                "subject_ref": subject,
                "revision_id": revision_id,
                "revision_no": 1,
                "version": 1,
                "tenant_public_id": tenant_id,
                "identity_revision_ref": _u(37),
                "identity_source_version": 2,
                "snapshot_ciphertext": b"opaque",
                "snapshot_key_id": "stored-profile-key",
                "reconfirmed_at": datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
                "updated_at": datetime(2026, 8, 20, 1, tzinfo=timezone.utc),
            }

        async def latest_profile_metrics(self, **scope):
            return (
                {"indicator_code": "height", "numeric_value": Decimal("175.0")},
                {"indicator_code": "weight", "numeric_value": Decimal("70.0")},
                {"indicator_code": "waist", "numeric_value": Decimal("82.0")},
            )

    class Authority:
        source_version = 2

        async def verified_identity_summary(self, **scope):
            assert scope == {"subject_member_id": subject, "service_case_id": case_id}
            return VerifiedIdentitySummaryV2(
                gender="FEMALE",
                birth_date=date(1950, 1, 2),
                identity_revision_ref=_u(37),
                source_version=self.source_version,
                tenant_public_id=tenant_id,
                evidence_status="VERIFIED",
            )

    class Secrets:
        def decrypt_profile(self, ciphertext, key_id, **context):
            assert ciphertext == b"opaque" and key_id == "stored-profile-key"
            assert context["identity_source_version"] == 2
            return _profile(expected_version=0, reconfirmed_at="2026-08-20T01:00:00+00:00")

    monkeypatch.setattr(service, "Slice4Secrets", Secrets)
    authority = Authority()
    value = asyncio.run(
        service.read_formal_health_profile(
            Repository(), Authority(), actor_user_id=7, actor_context="SELF",
            subject_member_id=subject, service_case_id=case_id, enrollment_id=enrollment_id,
        )
    )
    assert value.subject_ref == subject and value.identity_summary.source_version == 2
    assert (value.height_cm, value.weight_kg, value.waist_cm, value.bmi) == (
        Decimal("175.0"), Decimal("70.0"), Decimal("82.0"), Decimal("22.9")
    )
    assert not ({"snapshot_ciphertext", "snapshot_key_id"} & set(value.model_dump()))
    authority.source_version = 3
    with pytest.raises(RuntimeError, match="^ACTOR_CURRENTNESS_FORBIDDEN$"):
        asyncio.run(
            service.read_formal_health_profile(
                Repository(), authority, actor_user_id=7, actor_context="SELF",
                subject_member_id=subject, service_case_id=case_id, enrollment_id=enrollment_id,
            )
        )


def test_Profile_PUT允许expected_version追加且最新身高体重腰围生成BMI():
    from app.modules.health_fact.domain import compute_bmi
    from app.modules.user_health import service

    source = inspect.getsource(service.create_formal_profile_root)
    assert "payload.expected_version != 0" not in source
    assert "expected_version=payload.expected_version" in source
    assert compute_bmi(height_cm=Decimal("175.0"), weight_kg=Decimal("70.0")) == Decimal("22.9")
    assert "latest_profile_metrics" in inspect.getsource(service.read_formal_health_profile)


def test_REPORT事实必须将report_id写入canonical事实():
    from app.modules.health_fact.domain import CanonicalHealthFactDraft
    from app.modules.health_fact.models import CanonicalHealthFactOrmModel

    assert "report_id" in CanonicalHealthFactDraft.__dataclass_fields__
    assert hasattr(CanonicalHealthFactOrmModel, "report_id")


def test_最终整改_C_REPORT事实写入前必须调用受限主体权威():
    import inspect
    from pathlib import Path
    from app.modules.user_health import service

    assert "require_report_scope" in inspect.getsource(service.create_formal_health_facts)
    migration = (
        Path(__file__).parents[1]
        / "app/migrations/versions/20260823_0028_phase1_slice4_health_record_assessment_readiness.py"
    ).read_text(encoding="utf-8")
    authority = migration[migration.index("slice4_report_fact_authority_v1"):]
    for token in (
        "value_report_id UUID",
        "value_subject_member_id UUID",
        "value_service_case_id UUID",
        "d.tenant_id=c.tenant_id",
        "d.subject_member_id=value_subject_member_id",
        "d.service_case_id=value_service_case_id",
        "FOR SHARE OF d,c",
    ):
        assert token in authority
