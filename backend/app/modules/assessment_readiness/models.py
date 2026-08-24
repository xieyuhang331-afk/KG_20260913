from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKeyConstraint, LargeBinary, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDType
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssessmentReadinessPolicyVersionModel(Base):
    __tablename__ = "assessment_readiness_policy_version"
    __table_args__ = (
        UniqueConstraint("version_no", name="uq_assessment_readiness_policy_version_no"),
        CheckConstraint("status IN ('DRAFT','PUBLISHED','RETIRED') AND projection_version>=2", name="ck_assessment_readiness_policy_truth"),
        {"schema": "public"},
    )
    policy_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    required_profile_sections: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    required_indicators: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    allowed_states: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    projection_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    professionally_approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssessmentInputAssemblyModel(Base):
    __tablename__ = "assessment_input_assembly"
    __table_args__ = (
        UniqueConstraint("assembly_seq", name="uq_assessment_input_assembly_seq"),
        UniqueConstraint("assembly_id", "service_case_id", name="uq_assessment_input_assembly_case"),
        UniqueConstraint("service_case_id", "source_vector_digest", name="uq_assessment_input_assembly_source_vector"),
        CheckConstraint("status IN ('DATA_INSUFFICIENT','DATA_SYNC_PENDING','DISPUTED','ASSESSMENT_READY') AND required_max_fact_id>=0 AND required_max_status_event_seq>=0", name="ck_assessment_input_assembly_truth"),
        {"schema": "public"},
    )
    assembly_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    assembly_seq: Mapped[int] = mapped_column(BigInteger, nullable=False, autoincrement=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    primary_therapist_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    profile_revision_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    consent_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    policy_version_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    projection_version: Mapped[int | None] = mapped_column(SmallInteger)
    rule_version: Mapped[str | None] = mapped_column(String(64))
    resolved_generation_id: Mapped[int | None] = mapped_column(BigInteger)
    required_max_fact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    required_max_status_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_snapshot: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    missing_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    expired_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    disputed_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source_vector_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    assembly_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssessmentInputAssemblyFactModel(Base):
    __tablename__ = "assessment_input_assembly_fact"
    __table_args__ = ({"schema": "public"},)
    assembly_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    indicator_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    fact_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    measurement_context: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_state: Mapped[str] = mapped_column(String(24), nullable=False)
    value_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    value_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    row_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class AssessmentReadinessCasePointerModel(Base):
    __tablename__ = "assessment_readiness_case_pointer"
    __table_args__ = (
        ForeignKeyConstraint(
            ("current_assembly_id", "service_case_id"),
            ("public.assessment_input_assembly.assembly_id", "public.assessment_input_assembly.service_case_id"),
            name="fk_assessment_readiness_case_pointer_current",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("version>=1", name="ck_assessment_readiness_case_pointer_version"),
        {"schema": "public"},
    )
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    current_assembly_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    source_vector_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
