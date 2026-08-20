from sqlalchemy import CheckConstraint, Index

from app.modules.member_enrollment import models
from app.modules.member_enrollment.repository import MemberEnrollmentRepository


EXPECTED_TABLES = {
    "identity_claim_algorithm_state",
    "identity_subject_claim_registry",
    "slice3_digest_algorithm_state",
    "member_service_invitation",
    "service_enrollment",
    "controlled_member_bootstrap",
    "member_identity_verification",
    "member_identity_revision",
    "member_identity_review_decision",
    "member_identity_pii_access",
    "proxy_grant",
    "consent_document_version",
    "consent_document_rendition",
    "consent_record",
    "primary_therapist_assignment",
    "service_case",
    "member_enrollment_idempotency",
    "member_enrollment_audit",
    "member_enrollment_outbox",
    "member_enrollment_delivery",
}


def test_orm精确声明切片3对象() -> None:
    actual = {
        value.__table__.name
        for value in vars(models).values()
        if isinstance(value, type) and hasattr(value, "__table__")
    }
    assert EXPECTED_TABLES <= actual


def test_proxy槽位和service_case_preparing由数据库约束() -> None:
    proxy_sql = " ".join(
        str(item.sqltext)
        for item in models.ProxyGrantModel.__table__.constraints
        if isinstance(item, CheckConstraint)
    )
    case_sql = " ".join(
        str(item.sqltext)
        for item in models.ServiceCaseModel.__table__.constraints
        if isinstance(item, CheckConstraint)
    )
    assert "slot_no IN (1,2)" in proxy_sql
    assert "status='PREPARING'" in case_sql


def test_活动主体和代理槽位使用partial_unique() -> None:
    indexes = {
        item.name: item
        for table in (
            models.ServiceEnrollmentModel.__table__,
            models.ProxyGrantModel.__table__,
            models.ServiceCaseModel.__table__,
        )
        for item in table.indexes
        if isinstance(item, Index)
    }
    for name in (
        "uq_service_enrollment_active_subject",
        "uq_proxy_grant_active_principal",
        "uq_proxy_grant_active_slot",
        "uq_service_case_active_subject",
    ):
        assert indexes[name].unique is True
        assert indexes[name].dialect_options["postgresql"]["where"] is not None


def test_identity_revision不暴露明文字段() -> None:
    columns = set(models.MemberIdentityRevisionModel.__table__.columns.keys())
    assert {
        "real_name_ciphertext",
        "id_ciphertext",
        "birth_date_ciphertext",
        "fingerprint_key_id",
    } <= columns
    assert not {"real_name", "id_number", "birth_date"} & columns


def test_算法状态ORM为singleton且不持久化密钥材料() -> None:
    identity_columns = set(models.IdentityClaimAlgorithmStateModel.__table__.columns.keys())
    digest_columns = set(models.Slice3DigestAlgorithmStateModel.__table__.columns.keys())
    assert identity_columns == {
        "singleton", "fingerprint_domain", "fingerprint_key_id", "version", "created_at"
    }
    assert digest_columns == {
        "singleton",
        "request_key_id", "request_material_check",
        "audit_key_id", "audit_material_check",
        "outbox_key_id", "outbox_material_check",
        "consent_key_id", "consent_material_check",
        "delivery_key_id", "delivery_material_check",
        "version", "created_at",
    }
    assert not any("material" in name and not name.endswith("_check") for name in digest_columns)


def test_机构分配候选Repository只调用受限Boolean函数() -> None:
    import inspect

    source = inspect.getsource(MemberEnrollmentRepository.assignment_candidate_guard)
    assert "slice3_assignment_candidate_guard_v1" in source
    assert "therapist_profile" not in source


def test_F1_Repository只通过受限UUID集合快照接口() -> None:
    import inspect

    source = inspect.getsource(MemberEnrollmentRepository._record_expected_collection)
    assert "slice3_collection_snapshot_v1" in source
    assert "select(table.c[key_field])" not in source
    assert ".with_for_update()" not in source
    for family in (
        "IDENTITY_REVISION",
        "REVIEW_DECISION",
        "PII_ACCESS",
        "IDENTITY_REGISTRY",
        "CONSENT_RENDITION",
        "CONSENT_RECORD_SET",
    ):
        assert family in inspect.getsource(MemberEnrollmentRepository)


def test_B1_ORM复合FK与current_pointer等强() -> None:
    from sqlalchemy import ForeignKeyConstraint, UniqueConstraint

    enrollment = models.ServiceEnrollmentModel.__table__
    case = models.ServiceCaseModel.__table__
    names = {
        constraint.name
        for table in (enrollment, case)
        for constraint in table.constraints
        if isinstance(constraint, (ForeignKeyConstraint, UniqueConstraint))
    }
    assert {
        "uq_service_enrollment_current_case_inputs",
        "fk_service_case_identity_revision_scope",
        "fk_service_case_current_inputs_scope",
    } <= names
