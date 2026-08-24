from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, LargeBinary, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDType
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssessmentRuleSetVersionModel(Base):
    __tablename__ = "assessment_rule_set_version"
    __table_args__ = (
        UniqueConstraint("version_no", name="uq_assessment_rule_set_version_no"),
        UniqueConstraint("rule_set_code", "content_digest", name="uq_assessment_rule_set_content"),
        CheckConstraint(
            "status IN ('DRAFT','IN_REVIEW','NEEDS_CORRECTION','PUBLISHED','SUSPENDED','RETIRED') AND version>=1",
            name="ck_assessment_rule_set_version_truth",
        ),
        {"schema": "public"},
    )
    rule_set_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    rule_set_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    typed_rule_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reviewer_user_id: Mapped[int | None] = mapped_column(BigInteger)
    approval_evidence_ref: Mapped[str | None] = mapped_column(String(256))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthAssessmentModel(Base):
    __tablename__ = "health_assessment"
    __table_args__ = (
        UniqueConstraint("service_case_id", "sequence_no", name="uq_health_assessment_case_sequence"),
        UniqueConstraint("assessment_id", "service_case_id", name="uq_health_assessment_case"),
        CheckConstraint(
            "status IN ('DRAFT_SNAPSHOT','RUNNING','COMPLETED','FAILED','UNDER_REVIEW','SUPERSEDED') "
            "AND (overall_risk IS NULL OR overall_risk IN ('NOT_ASSESSED','WITHIN_RANGE','ATTENTION','HIGH_RISK')) "
            "AND sequence_no>=1 AND version>=1",
            name="ck_health_assessment_truth",
        ),
        {"schema": "public"},
    )
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False, unique=True)
    rule_set_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    overall_risk: Mapped[str | None] = mapped_column(String(24))
    supersedes_assessment_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    initiated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AssessmentInputSnapshotModel(Base):
    __tablename__ = "assessment_input_snapshot"
    __table_args__ = (
        ForeignKeyConstraint(
            ("assessment_id", "service_case_id"),
            ("public.health_assessment.assessment_id", "public.health_assessment.service_case_id"),
            name="fk_assessment_input_snapshot_assessment",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("required_max_fact_id>=0 AND required_max_status_event_seq>=0", name="ck_assessment_input_snapshot_hwm"),
        {"schema": "public"},
    )
    snapshot_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False, unique=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assembly_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    assembly_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_vector_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    profile_revision_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    consent_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rule_set_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    projection_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    required_max_fact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    required_max_status_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_snapshot: Mapped[str] = mapped_column(String(512), nullable=False)
    fact_ref_manifest_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    snapshot_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssessmentModuleResultModel(Base):
    __tablename__ = "assessment_module_result"
    __table_args__ = (
        UniqueConstraint("assessment_id", "module_code", name="uq_assessment_module_result_module"),
        CheckConstraint(
            "module_code IN ('BLOOD_PRESSURE_CARDIOVASCULAR','GLUCOSE_METABOLISM','LIPID_METABOLISM','WEIGHT_ABDOMINAL_OBESITY') "
            "AND risk_level IN ('NOT_ASSESSED','WITHIN_RANGE','ATTENTION','HIGH_RISK')",
            name="ck_assessment_module_result_truth",
        ),
        {"schema": "public"},
    )
    module_result_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    module_code: Mapped[str] = mapped_column(String(48), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence_manifest: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    message_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rule_fragment_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HighRiskTaskModel(Base):
    __tablename__ = "high_risk_task"
    __table_args__ = (
        UniqueConstraint("assessment_id", name="uq_high_risk_task_assessment"),
        CheckConstraint(
            "status IN ('OPEN','CLAIMED','ESCALATED','REFERRED','RESOLVED') AND version>=1",
            name="ck_high_risk_task_truth",
        ),
        {"schema": "public"},
    )
    task_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_module_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    trigger_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    assigned_actor_id: Mapped[int | None] = mapped_column(BigInteger)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HighRiskTaskActionModel(Base):
    __tablename__ = "high_risk_task_action"
    __table_args__ = (CheckConstraint("actor_user_id>=1", name="ck_high_risk_task_action_actor"), {"schema": "public"})
    action_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action_code: Mapped[str] = mapped_column(String(64), nullable=False)
    contact_outcome_code: Mapped[str | None] = mapped_column(String(64))
    advice_code: Mapped[str | None] = mapped_column(String(64))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class AssessmentDisputeModel(Base):
    __tablename__ = "assessment_dispute"
    __table_args__ = (CheckConstraint("version>=1", name="ck_assessment_dispute_version"), {"schema": "public"})
    dispute_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    raised_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_context: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    statement_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    statement_key_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    resolution_code: Mapped[str | None] = mapped_column(String(64))
    superseding_assessment_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Slice5IdempotencyModel(Base):
    __tablename__ = "slice5_idempotency"
    __table_args__ = (UniqueConstraint("actor_scope", "operation", "target_id", "idempotency_key", name="uq_slice5_idempotency_key"), {"schema": "public"})
    receipt_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    actor_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    postimage_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Slice5AuditModel(Base):
    __tablename__ = "slice5_audit"
    __table_args__ = ({"schema": "public"},)
    audit_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    evidence_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Slice5OutboxModel(Base):
    __tablename__ = "slice5_outbox"
    __table_args__ = (CheckConstraint("attempts>=0", name="ck_slice5_outbox_attempts"), {"schema": "public"})
    event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Slice5DeliveryModel(Base):
    __tablename__ = "slice5_delivery"
    __table_args__ = (UniqueConstraint("event_id", "target_type", "target_ref", name="uq_slice5_delivery_target"), {"schema": "public"})
    delivery_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
