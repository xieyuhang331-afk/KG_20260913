from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDType
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HealthPlanTemplateVersionModel(Base):
    __tablename__ = "health_plan_template_version"
    __table_args__ = (
        UniqueConstraint("template_code", "version_no", name="uq_health_plan_template_version"),
        UniqueConstraint("template_code", "content_digest", name="uq_health_plan_template_content"),
        CheckConstraint("status IN ('DRAFT','PUBLISHED','RETIRED') AND version>=1", name="ck_health_plan_template_truth"),
        Index("uq_health_plan_template_published", "template_code", unique=True, postgresql_where=text("status='PUBLISHED'")),
        {"schema": "public"},
    )
    template_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    template_code: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    medical_approval_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    author_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthPlanGenerationRequestModel(Base):
    __tablename__ = "health_plan_generation_request"
    __table_args__ = (
        UniqueConstraint("request_id", "service_case_id", name="uq_health_plan_generation_request_case"),
        CheckConstraint(
            "status IN ('REQUESTED','GENERATING','GENERATION_FAILED','IN_REVIEW','NEEDS_CORRECTION','USER_DECISION_PENDING','NEEDS_EXPLANATION','DECLINED','REJECTED','ACTIVE','SUPERSEDED') AND version>=1",
            name="ck_health_plan_generation_truth",
        ),
        Index(
            "uq_health_plan_generation_active_case",
            "service_case_id",
            unique=True,
            postgresql_where=text("status IN ('REQUESTED','GENERATING','IN_REVIEW','NEEDS_CORRECTION','USER_DECISION_PENDING','NEEDS_EXPLANATION','ACTIVE')"),
        ),
        {"schema": "public"},
    )
    request_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    assessment_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assembly_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    template_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    template_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_plan_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    authority_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    initiated_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    initiated_role: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthPlanVersionModel(Base):
    __tablename__ = "health_plan_version"
    __table_args__ = (
        UniqueConstraint("request_id", "version_no", name="uq_health_plan_request_version"),
        UniqueConstraint("plan_id", "service_case_id", name="uq_health_plan_case"),
        ForeignKeyConstraint(
            ("request_id", "service_case_id"),
            ("public.health_plan_generation_request.request_id", "public.health_plan_generation_request.service_case_id"),
            name="fk_health_plan_request_scope",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "status IN ('IN_REVIEW','USER_DECISION_PENDING','NEEDS_EXPLANATION','DECLINED','REJECTED','ACTIVE','SUPERSEDED') AND version_no>=1 AND version>=1",
            name="ck_health_plan_version_truth",
        ),
        Index("uq_health_plan_active_case", "service_case_id", unique=True, postgresql_where=text("status='ACTIVE'")),
        {"schema": "public"},
    )
    plan_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    template_version_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    supersedes_plan_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthPlanReviewModel(Base):
    __tablename__ = "health_plan_review"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_health_plan_review_plan"),
        ForeignKeyConstraint(("plan_id", "service_case_id"), ("public.health_plan_version.plan_id", "public.health_plan_version.service_case_id"), name="fk_health_plan_review_scope"),
        CheckConstraint("status IN ('PENDING','CLAIMED','APPROVED','NEEDS_CORRECTION','REJECTED') AND version>=1", name="ck_health_plan_review_truth"),
        {"schema": "public"},
    )
    review_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    request_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    claimed_by: Mapped[int | None] = mapped_column(BigInteger)
    claim_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthPlanExplanationModel(Base):
    __tablename__ = "health_plan_explanation"
    __table_args__ = (
        ForeignKeyConstraint(("plan_id", "service_case_id"), ("public.health_plan_version.plan_id", "public.health_plan_version.service_case_id"), name="fk_health_plan_explanation_scope"),
        {"schema": "public"},
    )
    explanation_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    therapist_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    explanation_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HealthPlanUserDecisionModel(Base):
    __tablename__ = "health_plan_user_decision"
    __table_args__ = (
        UniqueConstraint("plan_id", "decision_id", name="uq_health_plan_user_decision_plan"),
        ForeignKeyConstraint(("plan_id", "service_case_id"), ("public.health_plan_version.plan_id", "public.health_plan_version.service_case_id"), name="fk_health_plan_user_decision_scope"),
        CheckConstraint("decision IN ('ACCEPT','NEEDS_EXPLANATION','DECLINE') AND version>=1", name="ck_health_plan_user_decision_truth"),
        {"schema": "public"},
    )
    decision_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    service_case_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_context: Mapped[str] = mapped_column(String(32), nullable=False)
    proxy_grant_id: Mapped[UUID | None] = mapped_column(UUIDType(as_uuid=True))
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthPlanReceiptModel(Base):
    __tablename__ = "health_plan_receipt"
    __table_args__ = (
        UniqueConstraint("actor_scope", "operation", "idempotency_key", name="uq_health_plan_receipt_operation"),
        {"schema": "public"},
    )
    receipt_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    actor_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    postimage_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HealthPlanAuditModel(Base):
    __tablename__ = "health_plan_audit"
    __table_args__ = ({"schema": "public"},)
    audit_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HealthPlanOutboxModel(Base):
    __tablename__ = "health_plan_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','PROCESSING','DELIVERED','FAILED') AND attempts>=0 AND version>=1", name="ck_health_plan_outbox_truth"),
        {"schema": "public"},
    )
    event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class HealthPlanDeliveryModel(Base):
    __tablename__ = "health_plan_delivery"
    __table_args__ = (
        UniqueConstraint("event_id", "target_type", "target_ref", name="uq_health_plan_delivery_target"),
        {"schema": "public"},
    )
    delivery_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_ref: Mapped[UUID] = mapped_column(UUIDType(as_uuid=True), nullable=False)
    target_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
