from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.sqlalchemy_mapping import build_sqlalchemy_table
from app.modules.system.models import PLATFORM_ORG_TABLE

build_sqlalchemy_table(PLATFORM_ORG_TABLE)


class InstitutionInvitationModel(Base):
    __tablename__ = "institution_invitation"
    __table_args__ = (
        CheckConstraint("institution_type IN ('HEALTH_STORE','LICENSED_CLINIC')", name="institution_type"),
        CheckConstraint("status IN ('ISSUED','ACTIVATED','REVOKED') AND failed_attempts BETWEEN 0 AND 5 AND version>=1", name="state"),
        {"schema": "public"},
    )
    invitation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    institution_name: Mapped[str] = mapped_column(String(100), nullable=False)
    institution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    applicant_phone_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    applicant_phone_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    pilot_batch_code: Mapped[str] = mapped_column(String(32), nullable=False)
    administrative_region_id: Mapped[int] = mapped_column(
        ForeignKey(
            "platform_org.id",
            name="fk_institution_invitation_administrative_region",
        ),
        nullable=False,
    )
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class InstitutionOnboardingAccountModel(Base):
    __tablename__ = "institution_onboarding_account"
    __table_args__ = ({"schema": "public"},)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_institution_account_user"), primary_key=True)
    invitation_id: Mapped[str] = mapped_column(ForeignKey("public.institution_invitation.invitation_id", name="fk_institution_account_invitation"), unique=True, nullable=False)
    totp_secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstitutionApplicationModel(Base):
    __tablename__ = "institution_application"
    __table_args__ = (
        UniqueConstraint("invitation_id", name="uq_institution_application_invitation"),
        CheckConstraint("institution_type IN ('HEALTH_STORE','LICENSED_CLINIC')", name="institution_type"),
        CheckConstraint("status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','NEEDS_CORRECTION','APPROVED','REJECTED') AND version>=1 AND service_ready = false", name="state"),
        {"schema": "public"},
    )
    application_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    invitation_id: Mapped[str] = mapped_column(ForeignKey("public.institution_invitation.invitation_id", name="fk_institution_application_invitation"), nullable=False)
    applicant_user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_institution_application_user"), nullable=False)
    institution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    draft_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    correction_fields: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    correction_reason_code: Mapped[str | None] = mapped_column(String(64))
    current_revision_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    tenant_internal_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.id", name="fk_institution_application_tenant"))
    tenant_public_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), unique=True)
    service_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class InstitutionApplicationRevisionModel(Base):
    __tablename__ = "institution_application_revision"
    __table_args__ = (
        UniqueConstraint("application_id", "revision_no", name="uq_institution_application_revision_no"),
        {"schema": "public"},
    )
    revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("public.institution_application.application_id", name="fk_institution_revision_application"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstitutionLicenseModel(Base):
    __tablename__ = "institution_license"
    __table_args__ = (
        UniqueConstraint("application_id", "license_type", name="uq_institution_license_application_type"),
        CheckConstraint("license_type IN ('BUSINESS_LICENSE','MEDICAL_INSTITUTION_LICENSE')", name="license_type"),
        CheckConstraint("(valid_from IS NULL AND valid_until IS NULL) OR (valid_from IS NOT NULL AND valid_until IS NOT NULL AND valid_from<=valid_until)", name="validity"),
        {"schema": "public"},
    )
    license_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("public.institution_application.application_id", name="fk_institution_license_application"), nullable=False)
    license_type: Mapped[str] = mapped_column(String(48), nullable=False)
    license_no_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    license_no_digest: Mapped[str | None] = mapped_column(String(64))
    private_file_id: Mapped[str] = mapped_column(ForeignKey("public.private_file.file_id", name="fk_institution_license_private_file"), nullable=False)
    valid_from: Mapped[date | None] = mapped_column()
    valid_until: Mapped[date | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstitutionOnboardingIdempotencyModel(Base):
    __tablename__ = "institution_onboarding_idempotency"
    __table_args__ = ({"schema":"public"},)
    actor_scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    operation: Mapped[str] = mapped_column(String(48), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstitutionOnboardingAuditModel(Base):
    __tablename__ = "institution_onboarding_audit"
    __table_args__ = ({"schema":"public"},)
    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(48), nullable=False)
    object_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InstitutionOnboardingOutboxModel(Base):
    __tablename__ = "institution_onboarding_outbox"
    __table_args__ = (
        CheckConstraint(
            "attempts BETWEEN 0 AND 3 AND ("
            "(status='PENDING' AND processing_at IS NULL AND delivered_at IS NULL) OR "
            "(status='PROCESSING' AND processing_at IS NOT NULL AND delivered_at IS NULL) OR "
            "(status='DELIVERED' AND processing_at IS NOT NULL AND delivered_at IS NOT NULL) OR "
            "(status='FAILED' AND processing_at IS NULL AND delivered_at IS NULL))",
            name="status",
        ),
        {"schema":"public"},
    )
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.institution_application.application_id", name="fk_onboarding_outbox_application"),
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InstitutionOnboardingDeliveryModel(Base):
    __tablename__ = "institution_onboarding_delivery"
    __table_args__ = (
        CheckConstraint(
            "event_type = 'INSTITUTION_APPROVED' AND "
            "recipient_scope = 'INSTITUTION_ADMIN'",
            name="contract",
        ),
        {"schema": "public"},
    )
    event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey(
            "public.institution_onboarding_outbox.event_id",
            name="fk_onboarding_delivery_outbox",
        ),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient_user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", name="fk_onboarding_delivery_user"), nullable=False
    )
    recipient_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
