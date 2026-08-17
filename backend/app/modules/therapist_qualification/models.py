from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


EVENT_TYPES = (
    "THERAPIST_INVITED",
    "THERAPIST_INVITATION_REVOKED",
    "THERAPIST_INVITATION_EXPIRED",
    "THERAPIST_ACTIVATED",
    "THERAPIST_SUBMITTED",
    "THERAPIST_CORRECTION_REQUESTED",
    "THERAPIST_RESUBMITTED",
    "THERAPIST_REJECTED",
    "THERAPIST_APPROVED_ACTIVE",
    "THERAPIST_SUSPENDED",
    "THERAPIST_RESUMED",
    "THERAPIST_EXITED",
    "THERAPIST_QUALIFICATION_RENEWAL_SUBMITTED",
    "THERAPIST_QUALIFICATION_REVIEWED",
    "THERAPIST_QUALIFICATION_RENEWAL_RESUBMITTED",
    "SERVICE_READINESS_RECOMPUTED",
)


class TherapistInvitationModel(Base):
    __tablename__ = "therapist_invitation"
    __table_args__ = (
        UniqueConstraint("tenant_id", "invitation_id", name="uq_therapist_invitation_tenant_id"),
        CheckConstraint("failed_attempts BETWEEN 0 AND 5 AND version >= 1", name="attempts"),
        CheckConstraint(
            "(status='INVITED' AND activated_at IS NULL AND revoked_at IS NULL) OR "
            "(status='ACTIVATED' AND activated_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status='EXPIRED' AND activated_at IS NULL AND revoked_at IS NULL) OR "
            "(status='REVOKED' AND activated_at IS NULL AND revoked_at IS NOT NULL)",
            name="state",
        ),
        Index(
            "uq_therapist_invitation_open_phone",
            "tenant_id",
            "phone_digest_key_id",
            "phone_digest",
            unique=True,
            postgresql_where=text("status='INVITED'"),
        ),
        Index("ix_therapist_invitation_phone_digest_lookup", "tenant_id", "phone_digest_key_id", "phone_digest", "status"),
        {"schema": "public"},
    )
    invitation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_therapist_invitation_tenant"), nullable=False)
    phone_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    phone_encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(16), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    issued_by: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_therapist_invitation_issued_by"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TherapistProfileModel(Base):
    __tablename__ = "therapist_profile"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_therapist_profile_user"),
        UniqueConstraint("invitation_id", name="uq_therapist_profile_invitation"),
        UniqueConstraint("therapist_id", "current_qualification_version_id", name="uq_therapist_profile_current_qualification"),
        ForeignKeyConstraint(
            ["tenant_id", "invitation_id"],
            ["public.therapist_invitation.tenant_id", "public.therapist_invitation.invitation_id"],
            name="fk_therapist_profile_invitation_tenant",
        ),
        ForeignKeyConstraint(
            ["therapist_id", "current_qualification_version_id"],
            ["public.therapist_qualification_version.therapist_id", "public.therapist_qualification_version.qualification_version_id"],
            name="fk_therapist_profile_current_qualification",
            use_alter=True,
        ),
        CheckConstraint("capacity_limit=30 AND active_case_count BETWEEN 0 AND 30", name="capacity"),
        CheckConstraint(
            "status IN ('ACTIVATED','DRAFT','SUBMITTED','UNDER_REVIEW','NEEDS_CORRECTION','RESUBMITTED','APPROVED_ACTIVE','SUSPENDED','EXITED','REJECTED') "
            "AND version>=1 AND current_revision_no>=0 AND activated_at IS NOT NULL AND "
            "((real_name_ciphertext IS NULL AND real_name_encryption_key_id IS NULL AND real_name_digest IS NULL AND real_name_digest_key_id IS NULL) OR "
            "(real_name_ciphertext IS NOT NULL AND real_name_encryption_key_id IS NOT NULL AND real_name_digest IS NOT NULL AND real_name_digest_key_id IS NOT NULL)) AND "
            "((status IN ('ACTIVATED','DRAFT') AND current_revision_no=0 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NULL AND reviewed_at IS NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL) OR "
            "(status IN ('SUBMITTED','UNDER_REVIEW','RESUBMITTED') AND current_revision_no>=1 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NOT NULL AND reviewed_at IS NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL) OR "
            "(status='NEEDS_CORRECTION' AND current_revision_no>=1 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL) OR "
            "(status='APPROVED_ACTIVE' AND current_revision_no>=1 AND current_qualification_version_id IS NOT NULL AND qualification_valid_until IS NOT NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND exited_at IS NULL AND suspension_reason_code IS NULL AND ((suspended_at IS NULL AND resumed_at IS NULL) OR (suspended_at IS NOT NULL AND resumed_at IS NOT NULL AND resumed_at>suspended_at))) OR "
            "(status='SUSPENDED' AND current_revision_no>=1 AND current_qualification_version_id IS NOT NULL AND qualification_valid_until IS NOT NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND suspended_at IS NOT NULL AND suspension_reason_code IS NOT NULL AND exited_at IS NULL) OR "
            "(status='EXITED' AND current_revision_no>=1 AND current_qualification_version_id IS NOT NULL AND qualification_valid_until IS NOT NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND exited_at IS NOT NULL) OR "
            "(status='REJECTED' AND current_revision_no>=1 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL)) AND "
            "(status IN ('ACTIVATED','DRAFT') OR (real_name_ciphertext IS NOT NULL AND display_name IS NOT NULL AND practice_summary IS NOT NULL AND service_tags IS NOT NULL))",
            name="state",
        ),
        CheckConstraint(
            "service_tags IS NULL OR (jsonb_typeof(service_tags)='array' AND jsonb_array_length(service_tags) BETWEEN 1 AND 4 "
            "AND service_tags <@ '[\"HYPERTENSION\",\"GLUCOSE_METABOLISM\",\"DYSLIPIDEMIA\",\"OBESITY\"]'::jsonb)",
            name="service_tags",
        ),
        Index("ix_therapist_profile_tenant_status", "tenant_id", "status", "therapist_id"),
        {"schema": "public"},
    )
    therapist_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_therapist_profile_user"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_therapist_profile_tenant"), nullable=False)
    invitation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    real_name_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    real_name_encryption_key_id: Mapped[str | None] = mapped_column(String(64))
    real_name_digest: Mapped[str | None] = mapped_column(String(64))
    real_name_digest_key_id: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(50))
    practice_summary: Mapped[str | None] = mapped_column(String(500))
    service_tags: Mapped[list[str] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    capacity_limit: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("30"))
    active_case_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    current_qualification_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    current_revision_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    qualification_valid_until: Mapped[date | None] = mapped_column(Date)
    suspension_reason_code: Mapped[str | None] = mapped_column(String(64))
    totp_secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    totp_encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TherapistProfileRevisionModel(Base):
    __tablename__ = "therapist_profile_revision"
    __table_args__ = (
        UniqueConstraint("therapist_id", "revision_no", name="uq_therapist_profile_revision_no"),
        UniqueConstraint("therapist_id", "revision_id", name="uq_therapist_revision_identity"),
        CheckConstraint("revision_no>=1", name="revision_no"),
        CheckConstraint(
            "jsonb_typeof(profile_snapshot)='object' AND profile_snapshot->'v'='1'::jsonb",
            name="snapshot",
        ),
        {"schema": "public"},
    )
    revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    therapist_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_profile.therapist_id", name="fk_therapist_revision_profile"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistQualificationVersionModel(Base):
    __tablename__ = "therapist_qualification_version"
    __table_args__ = (
        UniqueConstraint("therapist_id", "qualification_type", "version_no", name="uq_therapist_qualification_version_no"),
        UniqueConstraint("therapist_id", "qualification_version_id", name="uq_therapist_qualification_identity"),
        ForeignKeyConstraint(["therapist_id", "profile_revision_id"], ["public.therapist_profile_revision.therapist_id", "public.therapist_profile_revision.revision_id"], name="fk_therapist_qualification_revision"),
        ForeignKeyConstraint(["therapist_id", "previous_version_id"], ["public.therapist_qualification_version.therapist_id", "public.therapist_qualification_version.qualification_version_id"], name="fk_therapist_qualification_previous"),
        CheckConstraint("qualification_type='METABOLIC_HEALTH_PRACTICE'", name="type"),
        CheckConstraint("valid_from<=valid_until AND version_no>=1", name="dates"),
        CheckConstraint("attachment_count BETWEEN 1 AND 3", name="attachment_count"),
        Index("ix_therapist_qualification_expiry", "valid_until", "therapist_id", "qualification_version_id"),
        Index("ix_therapist_qualification_certificate_digest_lookup", "therapist_id", "certificate_digest_key_id", "certificate_no_digest"),
        {"schema": "public"},
    )
    qualification_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    therapist_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_profile.therapist_id", name="fk_therapist_qualification_profile"), nullable=False)
    profile_revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    previous_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    qualification_type: Mapped[str] = mapped_column(String(48), nullable=False)
    certificate_no_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    certificate_encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    certificate_no_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    certificate_digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    certificate_no_masked: Mapped[str] = mapped_column(String(16), nullable=False)
    issuer_name: Mapped[str] = mapped_column(String(100), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_until: Mapped[date] = mapped_column(Date, nullable=False)
    attachment_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistRevisionQualificationModel(Base):
    __tablename__ = "therapist_profile_revision_qualification"
    __table_args__ = (
        UniqueConstraint("revision_id", "position", name="uq_therapist_revision_qualification_position"),
        ForeignKeyConstraint(["therapist_id", "revision_id"], ["public.therapist_profile_revision.therapist_id", "public.therapist_profile_revision.revision_id"], name="fk_therapist_revision_qualification_revision"),
        ForeignKeyConstraint(["therapist_id", "qualification_version_id"], ["public.therapist_qualification_version.therapist_id", "public.therapist_qualification_version.qualification_version_id"], name="fk_therapist_revision_qualification_version"),
        CheckConstraint("position=1", name="position"),
        {"schema": "public"},
    )
    therapist_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    qualification_version_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class TherapistQualificationAttachmentModel(Base):
    __tablename__ = "therapist_qualification_attachment"
    __table_args__ = (
        UniqueConstraint("private_file_id", name="uq_therapist_qualification_attachment_file"),
        CheckConstraint("slot BETWEEN 1 AND 3", name="slot"),
        {"schema": "public"},
    )
    qualification_version_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_qualification_version.qualification_version_id", name="fk_therapist_attachment_qualification"), primary_key=True)
    slot: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    private_file_id: Mapped[str] = mapped_column(ForeignKey("public.private_file.file_id", name="fk_therapist_attachment_private_file"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistReviewItemModel(Base):
    __tablename__ = "therapist_review_item"
    __table_args__ = (
        UniqueConstraint("therapist_id", "review_item_id", name="uq_therapist_review_item_identity"),
        ForeignKeyConstraint(["therapist_id", "revision_id"], ["public.therapist_profile_revision.therapist_id", "public.therapist_profile_revision.revision_id"], name="fk_therapist_review_item_revision"),
        ForeignKeyConstraint(["therapist_id", "qualification_version_id"], ["public.therapist_qualification_version.therapist_id", "public.therapist_qualification_version.qualification_version_id"], name="fk_therapist_review_item_qualification"),
        ForeignKeyConstraint(["therapist_id", "previous_review_item_id"], ["public.therapist_review_item.therapist_id", "public.therapist_review_item.review_item_id"], name="fk_therapist_review_item_previous"),
        CheckConstraint(
            "review_kind IN ('INITIAL','RENEWAL') AND status IN ('QUEUED','UNDER_REVIEW','DECIDED') AND version>=1 AND "
            "((review_kind='INITIAL' AND qualification_version_id IS NULL) OR (review_kind='RENEWAL' AND qualification_version_id IS NOT NULL)) AND "
            "((status='QUEUED' AND reviewer_user_id IS NULL AND claimed_at IS NULL AND decided_at IS NULL) OR "
            "(status='UNDER_REVIEW' AND reviewer_user_id IS NOT NULL AND claimed_at IS NOT NULL AND decided_at IS NULL) OR "
            "(status='DECIDED' AND reviewer_user_id IS NOT NULL AND claimed_at IS NOT NULL AND decided_at IS NOT NULL))",
            name="contract",
        ),
        Index("ix_therapist_review_queue", "status", "created_at", "review_item_id"),
        {"schema": "public"},
    )
    review_item_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    therapist_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_profile.therapist_id", name="fk_therapist_review_item_profile"), nullable=False)
    revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    qualification_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    review_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_user_id: Mapped[int | None] = mapped_column(BigInteger)
    previous_review_item_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TherapistReviewDecisionModel(Base):
    __tablename__ = "therapist_review_decision"
    __table_args__ = (
        UniqueConstraint("review_item_id", name="uq_therapist_review_item_decision"),
        ForeignKeyConstraint(["therapist_id", "review_item_id"], ["public.therapist_review_item.therapist_id", "public.therapist_review_item.review_item_id"], name="fk_therapist_review_decision_item_identity"),
        ForeignKeyConstraint(["therapist_id", "revision_id"], ["public.therapist_profile_revision.therapist_id", "public.therapist_profile_revision.revision_id"], name="fk_therapist_review_decision_revision"),
        CheckConstraint(
            "decision IN ('NEEDS_CORRECTION','REJECTED','APPROVED') AND jsonb_typeof(qualification_outcomes)='object' AND "
            "((decision='NEEDS_CORRECTION' AND reason_code IS NOT NULL AND qualification_outcomes='{}'::jsonb AND jsonb_typeof(correction_fields)='array' AND jsonb_array_length(correction_fields)>0) OR "
            "(decision='REJECTED' AND reason_code IS NOT NULL AND correction_fields IS NULL AND qualification_outcomes<>'{}'::jsonb) OR "
            "(decision='APPROVED' AND reason_code IS NULL AND correction_fields IS NULL AND qualification_outcomes<>'{}'::jsonb))",
            name="contract",
        ),
        {"schema": "public"},
    )
    decision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    review_item_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_review_item.review_item_id", name="fk_therapist_review_decision_item"), nullable=False)
    therapist_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_therapist_review_decision_user"), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    qualification_outcomes: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    correction_fields: Mapped[list[str] | None] = mapped_column(JSONB(none_as_null=True))
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistStatusDecisionModel(Base):
    __tablename__ = "therapist_status_decision"
    __table_args__ = (
        UniqueConstraint("therapist_id", "expected_profile_version", name="uq_therapist_status_decision_profile_version"),
        CheckConstraint(
            "decision IN ('SUSPENDED','RESUMED','EXITED') AND expected_profile_version>=1 AND "
            "((actor_kind='USER' AND actor_user_id IS NOT NULL AND worker_identity IS NULL) OR "
            "(actor_kind='EXPIRY_WORKER' AND actor_user_id IS NULL AND worker_identity='THERAPIST_EXPIRY_WORKER_V1' AND decision='SUSPENDED')) AND "
            "(decision<>'RESUMED' OR reason_code='QUALIFICATION_RENEWED')",
            name="contract",
        ),
        {"schema": "public"},
    )
    status_decision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    therapist_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_profile.therapist_id", name="fk_therapist_status_decision_profile"), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    worker_identity: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_profile_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstitutionServiceReadinessModel(Base):
    __tablename__ = "institution_service_readiness"
    __table_args__ = (
        CheckConstraint("version>=1 AND evidence_version>=1 AND qualified_therapist_count>=0 AND jsonb_typeof(source_versions)='object'", name="truth"),
        CheckConstraint("reason_codes <@ ARRAY['TENANT_NOT_ACTIVE','INSTITUTION_LICENSE_INVALID','NO_APPROVED_ACTIVE_THERAPIST','METABOLIC_SCOPE_MISSING','COMPLIANCE_SUSPENDED','INSTITUTION_APPROVAL_SOURCE_INVALID']::text[]", name="reasons"),
        {"schema": "public"},
    )
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_institution_service_readiness_tenant"), primary_key=True)
    readiness_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    qualified_therapist_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    next_expiry_at: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReadinessEvidenceModel(Base):
    __tablename__ = "readiness_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "evidence_version", name="uq_readiness_evidence_version"),
        UniqueConstraint("trigger_event_id", name="uq_readiness_evidence_trigger"),
        CheckConstraint("evidence_version>=1 AND qualified_therapist_count>=0 AND jsonb_typeof(source_versions)='object'", name="truth"),
        {"schema": "public"},
    )
    evidence_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_readiness_evidence_tenant"), nullable=False)
    evidence_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    readiness_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    qualified_therapist_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_status: Mapped[str] = mapped_column(String(16), nullable=False)
    institution_license_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    therapist_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    service_scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    next_expiry_at: Mapped[date | None] = mapped_column(Date)
    trigger_event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistWorkflowIdempotencyModel(Base):
    __tablename__ = "therapist_workflow_idempotency"
    __table_args__ = ({"schema": "public"},)
    actor_scope: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    postimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistWorkflowAuditModel(Base):
    __tablename__ = "therapist_workflow_audit"
    __table_args__ = ({"schema": "public"},)
    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    preimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    postimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TherapistWorkflowOutboxModel(Base):
    __tablename__ = "therapist_workflow_outbox"
    __table_args__ = (
        CheckConstraint(
            "attempts BETWEEN 0 AND 3 AND version>=1 AND "
            "((status='PENDING' AND attempts BETWEEN 0 AND 2 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NULL) OR "
            "(status='PROCESSING' AND attempts BETWEEN 1 AND 3 AND processing_at IS NOT NULL AND lease_owner IS NOT NULL AND delivered_at IS NULL AND failed_at IS NULL) OR "
            "(status='DELIVERED' AND attempts BETWEEN 1 AND 3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NOT NULL AND failed_at IS NULL) OR "
            "(status='FAILED' AND attempts=3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NOT NULL))",
            name="state",
        ),
        CheckConstraint("event_type IN (" + ",".join(repr(value) for value in EVENT_TYPES) + ")", name="event_type"),
        Index("ix_therapist_workflow_outbox_claim", "status", "processing_at", "created_at", "event_id"),
        {"schema": "public"},
    )
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_therapist_workflow_outbox_tenant"), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TherapistWorkflowDeliveryModel(Base):
    __tablename__ = "therapist_workflow_delivery"
    __table_args__ = (
        UniqueConstraint("event_id", "target_digest_key_id", "target_digest", name="uq_therapist_workflow_delivery_target"),
        CheckConstraint("jsonb_typeof(payload)='object'", name="contract"),
        {"schema": "public"},
    )
    delivery_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("public.therapist_workflow_outbox.event_id", name="fk_therapist_workflow_delivery_event"), nullable=False)
    recipient_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(BigInteger)
    recipient_tenant_id: Mapped[int | None] = mapped_column(BigInteger)
    recipient_platform_code: Mapped[str | None] = mapped_column(String(32))
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
