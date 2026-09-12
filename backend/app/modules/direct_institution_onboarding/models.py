from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class DirectInstitutionOnboardingModel(Base):
    __tablename__ = "direct_institution_onboarding"
    __table_args__ = ({"schema": "public"},)

    onboarding_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id"), unique=True)
    tenant_public_id: Mapped[str] = mapped_column(UUID(as_uuid=False), unique=True)
    institution_code: Mapped[str] = mapped_column(String(32), unique=True)
    institution_name: Mapped[str] = mapped_column(String(100))
    institution_type: Mapped[str] = mapped_column(String(32))
    administrative_region_id: Mapped[int] = mapped_column(ForeignKey("platform_org.id"))
    admin_phone_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    admin_phone_key_id: Mapped[str] = mapped_column(String(64))
    phone_digest_key_id: Mapped[str] = mapped_column(String(64))
    phone_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    activated_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    current_revision_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    compliance_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))


class IdentityPhoneClaimModel(Base):
    __tablename__ = "identity_phone_claim"
    __table_args__ = ({"schema": "public"},)

    claim_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    phone_digest_key_id: Mapped[str] = mapped_column(String(64))
    phone_digest: Mapped[str] = mapped_column(String(64))
    claim_kind: Mapped[str] = mapped_column(String(32))
    claim_ref: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    state: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))


class DirectActivationCredentialModel(Base):
    __tablename__ = "direct_institution_activation_credential"
    __table_args__ = ({"schema": "public"},)

    credential_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    onboarding_id: Mapped[str] = mapped_column(ForeignKey("public.direct_institution_onboarding.onboarding_id"))
    credential_digest_key_id: Mapped[str] = mapped_column(String(64))
    credential_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    failed_attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))


class DirectComplianceRevisionModel(Base):
    __tablename__ = "direct_institution_compliance_revision"
    __table_args__ = ({"schema": "public"},)

    revision_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    onboarding_id: Mapped[str] = mapped_column(ForeignKey("public.direct_institution_onboarding.onboarding_id"))
    revision_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    created_operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), unique=True)
    compliance_schema_version: Mapped[int] = mapped_column(SmallInteger)
    compliance_payload_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    compliance_payload_key_id: Mapped[str] = mapped_column(String(64))
    compliance_payload_digest_key_id: Mapped[str] = mapped_column(String(64))
    compliance_payload_digest: Mapped[str] = mapped_column(String(64))
    unified_social_credit_code_digest_key_id: Mapped[str] = mapped_column(String(64))
    unified_social_credit_code_digest: Mapped[str] = mapped_column(String(64))
    license_count: Mapped[int] = mapped_column(SmallInteger)
    license_set_digest_key_id: Mapped[str] = mapped_column(String(64))
    license_set_digest: Mapped[str] = mapped_column(String(64))
    submitted_by: Mapped[int] = mapped_column(ForeignKey("user.id"))
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    correction_fields: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))


class DirectInstitutionLicenseModel(Base):
    __tablename__ = "direct_institution_license"
    __table_args__ = ({"schema": "public"},)

    license_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    onboarding_id: Mapped[str] = mapped_column(ForeignKey("public.direct_institution_onboarding.onboarding_id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("public.direct_institution_compliance_revision.revision_id"))
    license_type: Mapped[str] = mapped_column(String(32))
    license_no_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    license_no_key_id: Mapped[str | None] = mapped_column(String(64))
    license_no_digest_key_id: Mapped[str | None] = mapped_column(String(64))
    license_no_digest: Mapped[str | None] = mapped_column(String(64))
    private_file_id: Mapped[str] = mapped_column(ForeignKey("public.private_file.file_id"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(BigInteger, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DirectOnboardingReceiptModel(Base):
    __tablename__ = "direct_onboarding_receipt"
    __table_args__ = ({"schema": "public"},)

    receipt_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    actor_scope: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    postimage_digest: Mapped[str] = mapped_column(String(64))
    response_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
