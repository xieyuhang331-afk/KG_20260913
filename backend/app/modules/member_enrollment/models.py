from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


UUIDType = PostgreSQLUUID(as_uuid=True)
ACTIVE_ENROLLMENT_STATUSES = (
    "'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',"
    "'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',"
    "'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING','CASE_CREATED'"
)
EVENT_TYPES = (
    "MEMBER_INVITATION_CREATED", "MEMBER_INVITATION_RESENT",
    "MEMBER_INVITATION_REVOKED", "MEMBER_INVITATION_EXPIRED",
    "MEMBER_ENROLLMENT_ACCEPTED", "MEMBER_IDENTITY_SUBMITTED",
    "MEMBER_IDENTITY_RESUBMITTED", "MEMBER_IDENTITY_INSTITUTION_CHECKED",
    "MEMBER_IDENTITY_CORRECTION_REQUESTED", "MEMBER_IDENTITY_REJECTED",
    "MEMBER_IDENTITY_VERIFIED", "PROXY_GRANT_ACTIVATED",
    "PROXY_GRANT_REVOKED", "PROXY_GRANT_EXPIRED",
    "CONSENT_DOCUMENT_PUBLISHED", "CONSENT_DOCUMENT_RETIRED",
    "CONSENT_ACCEPTED", "CONSENT_DECLINED", "CONSENT_WITHDRAWN",
    "CONSENT_SUPERSEDED", "PRIMARY_ASSIGNMENT_CREATED",
    "PRIMARY_ASSIGNMENT_DECLINED", "PRIMARY_ASSIGNMENT_CANCELLED",
    "SERVICE_CASE_PREPARING_CREATED",
)
EVENT_RECIPIENT_SCOPES = {
    **{value: ("TENANT",) for value in (
        "MEMBER_INVITATION_CREATED", "MEMBER_INVITATION_RESENT",
        "MEMBER_INVITATION_REVOKED", "MEMBER_INVITATION_EXPIRED",
        "MEMBER_ENROLLMENT_ACCEPTED", "MEMBER_IDENTITY_SUBMITTED",
        "MEMBER_IDENTITY_RESUBMITTED",
    )},
    "MEMBER_IDENTITY_INSTITUTION_CHECKED": ("PLATFORM",),
    **{value: ("SUBJECT",) for value in (
        "MEMBER_IDENTITY_CORRECTION_REQUESTED", "MEMBER_IDENTITY_REJECTED",
        "MEMBER_IDENTITY_VERIFIED", "PROXY_GRANT_ACTIVATED",
        "PROXY_GRANT_REVOKED", "PROXY_GRANT_EXPIRED", "CONSENT_ACCEPTED",
        "CONSENT_DECLINED", "CONSENT_WITHDRAWN", "CONSENT_SUPERSEDED",
    )},
    "CONSENT_DOCUMENT_PUBLISHED": ("PLATFORM",),
    "CONSENT_DOCUMENT_RETIRED": ("PLATFORM",),
    "PRIMARY_ASSIGNMENT_CREATED": ("THERAPIST",),
    "PRIMARY_ASSIGNMENT_DECLINED": ("THERAPIST",),
    "PRIMARY_ASSIGNMENT_CANCELLED": ("THERAPIST",),
    "SERVICE_CASE_PREPARING_CREATED": ("SUBJECT", "THERAPIST", "TENANT"),
}


class IdentityClaimAlgorithmStateModel(Base):
    __tablename__ = "identity_claim_algorithm_state"
    __table_args__ = (
        CheckConstraint(
            "singleton=1 AND version=1 AND fingerprint_domain='SLICE3_IDENTITY_FINGERPRINT_P1_V1'",
            name="singleton",
        ),
        {"schema": "identity"},
    )
    singleton: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    fingerprint_domain: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Slice3DigestAlgorithmStateModel(Base):
    __tablename__ = "slice3_digest_algorithm_state"
    __table_args__ = (
        CheckConstraint(
            "singleton=1 AND version=1 AND request_key_id<>audit_key_id AND request_key_id<>outbox_key_id AND request_key_id<>consent_key_id AND request_key_id<>delivery_key_id AND audit_key_id<>outbox_key_id AND audit_key_id<>consent_key_id AND audit_key_id<>delivery_key_id AND outbox_key_id<>consent_key_id AND outbox_key_id<>delivery_key_id AND consent_key_id<>delivery_key_id",
            name="truth",
        ),
        {"schema": "public"},
    )
    singleton: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    request_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_material_check: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_material_check: Mapped[str] = mapped_column(String(64), nullable=False)
    outbox_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    outbox_material_check: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_material_check: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_material_check: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdentitySubjectClaimRegistryModel(Base):
    __tablename__ = "identity_subject_claim_registry"
    __table_args__ = (
        UniqueConstraint("identity_fingerprint", name="fingerprint"),
        UniqueConstraint("user_ref", name="user"),
        UniqueConstraint("member_id", name="member"),
        CheckConstraint(
            "source_facts_version>=1 AND version>=1 AND "
            "char_length(identity_fingerprint)=64 AND "
            "char_length(source_evidence_digest)=64 AND "
            "((source_kind='P1' AND user_ref IS NOT NULL AND "
            "p1_submission_id IS NOT NULL AND p1_decision_ref IS NOT NULL AND "
            "slice3_revision_id IS NULL AND slice3_decision_id IS NULL AND "
            "adult_eligible IS NULL AND represented_elder_eligible IS NULL) OR "
            "(source_kind='SLICE3' AND member_id IS NOT NULL AND "
            "user_ref IS NULL AND p1_submission_id IS NULL AND "
            "p1_decision_ref IS NULL AND slice3_revision_id IS NOT NULL AND "
            "slice3_decision_id IS NOT NULL))",
            name="source_truth",
        ),
        {"schema": "identity"},
    )
    claim_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    identity_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_ref: Mapped[int | None] = mapped_column(BigInteger)
    member_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("identity.member.member_id", name="member_id_member")
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    p1_submission_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("public.identity_verification_submission.submission_id", name="p1_submission")
    )
    p1_decision_ref: Mapped[UUID | None] = mapped_column(
        ForeignKey("public.identity_verification_decision.decision_ref", name="p1_decision")
    )
    slice3_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("public.member_identity_revision.revision_id", name="slice3_revision")
    )
    slice3_decision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("public.member_identity_review_decision.decision_id", name="slice3_decision")
    )
    source_facts_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    adult_eligible: Mapped[bool | None] = mapped_column(Boolean)
    represented_elder_eligible: Mapped[bool | None] = mapped_column(Boolean)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemberServiceInvitationModel(Base):
    __tablename__ = "member_service_invitation"
    __table_args__ = (
        CheckConstraint("mode IN ('SELF','PROXY_ELDER')", name="mode"),
        CheckConstraint("failed_attempts BETWEEN 0 AND 5 AND version>=1", name="attempts"),
        CheckConstraint(
            "(status='INVITED' AND accepted_at IS NULL AND revoked_at IS NULL AND failed_attempts<5) OR "
            "(status='ACCEPTED' AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR "
            "(status='REVOKED' AND accepted_at IS NULL AND revoked_at IS NOT NULL) OR "
            "(status='EXPIRED' AND accepted_at IS NULL AND revoked_at IS NULL)",
            name="truth",
        ),
        Index(
            "uq_member_service_invitation_open_phone",
            "tenant_id", "phone_digest_key_id", "phone_digest",
            unique=True,
            postgresql_where=text("status='INVITED'"),
        ),
        {"schema": "public"},
    )
    invitation_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_member_service_invitation_tenant_id_tenant"), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    phone_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    phone_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(16), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    code_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_by: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_member_service_invitation_issued_by_user"), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceEnrollmentModel(Base):
    __tablename__ = "service_enrollment"
    __table_args__ = (
        UniqueConstraint("invitation_id", name="uq_service_enrollment_invitation"),
        CheckConstraint("mode IN ('SELF','PROXY_ELDER') AND version>=1", name="mode_version"),
        CheckConstraint(
            "(mode='SELF' AND proxy_member_id IS NULL) OR "
            "(mode='PROXY_ELDER' AND proxy_member_id IS NOT NULL AND proxy_member_id<>subject_member_id)",
            name="subject_proxy",
        ),
        CheckConstraint(
            f"status IN ({ACTIVE_ENROLLMENT_STATUSES},'REJECTED','REVOKED','EXPIRED')",
            name="status",
        ),
        Index(
            "uq_service_enrollment_active_subject", "subject_member_id", unique=True,
            postgresql_where=text(f"status IN ({ACTIVE_ENROLLMENT_STATUSES})"),
        ),
        UniqueConstraint("enrollment_id", "subject_member_id", name="uq_service_enrollment_enrollment_subject_member"),
        UniqueConstraint("enrollment_id", "proxy_member_id", name="uq_service_enrollment_enrollment_proxy_member"),
        UniqueConstraint("enrollment_id", "tenant_id", "subject_member_id", name="uq_service_enrollment_enrollment_tenant_subject"),
        UniqueConstraint("enrollment_id", "current_identity_verification_id", "current_assignment_id", name="uq_service_enrollment_current_case_inputs"),
        ForeignKeyConstraint(("enrollment_id","current_identity_verification_id"),("public.member_identity_verification.enrollment_id","public.member_identity_verification.verification_id"),name="fk_service_enrollment_current_identity_scope"),
        ForeignKeyConstraint(("enrollment_id","current_assignment_id"),("public.primary_therapist_assignment.enrollment_id","public.primary_therapist_assignment.assignment_id"),name="fk_service_enrollment_current_assignment_scope"),
        ForeignKeyConstraint(("enrollment_id","service_case_id"),("public.service_case.enrollment_id","public.service_case.case_id"),name="fk_service_enrollment_service_case_scope"),
        {"schema": "public"},
    )
    enrollment_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    invitation_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_service_invitation.invitation_id", name="fk_service_enrollment_invitation_id_member_service_invitation"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_service_enrollment_tenant_id_tenant"), nullable=False)
    subject_member_id: Mapped[UUID | None] = mapped_column(ForeignKey("identity.member.member_id", name="fk_service_enrollment_subject_member_id_member"))
    proxy_member_id: Mapped[UUID | None] = mapped_column(ForeignKey("identity.member.member_id", name="fk_service_enrollment_proxy_member_id_member"))
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    service_scope_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    current_identity_verification_id: Mapped[UUID | None] = mapped_column(UUIDType)
    current_assignment_id: Mapped[UUID | None] = mapped_column(UUIDType)
    service_case_id: Mapped[UUID | None] = mapped_column(UUIDType)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    identity_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    case_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ControlledMemberBootstrapModel(Base):
    __tablename__ = "controlled_member_bootstrap"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="uq_controlled_member_bootstrap_enrollment"),
        UniqueConstraint("member_id", name="uq_controlled_member_bootstrap_member"),
        UniqueConstraint("member_no", name="uq_controlled_member_bootstrap_member_no"),
        CheckConstraint("creation_source='controlled_proxy_enrollment'", name="source"),
        {"schema": "public"},
    )
    bootstrap_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("public.service_enrollment.enrollment_id", name="fk_controlled_member_bootstrap_enrollment_id_service_enrollment"), nullable=False)
    member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_controlled_member_bootstrap_member_id_member"), nullable=False)
    member_no: Mapped[str] = mapped_column(String(64), nullable=False)
    creation_source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class MemberIdentityVerificationModel(Base):
    __tablename__ = "member_identity_verification"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="uq_member_identity_verification_enrollment"),
        UniqueConstraint("verification_id", "current_revision_id", name="uq_member_identity_verification_current_revision"),
        UniqueConstraint("enrollment_id", "verification_id", name="uq_member_identity_verification_enrollment_verification"),
        ForeignKeyConstraint(("verification_id","institution_decision_id"),("public.member_identity_review_decision.verification_id","public.member_identity_review_decision.decision_id"),name="fk_member_identity_verification_institution_decision_scope"),
        ForeignKeyConstraint(("verification_id","platform_decision_id"),("public.member_identity_review_decision.verification_id","public.member_identity_review_decision.decision_id"),name="fk_member_identity_verification_platform_decision_scope"),
        ForeignKeyConstraint(
            ("verification_id", "current_revision_id"),
            ("public.member_identity_revision.verification_id", "public.member_identity_revision.revision_id"),
            name="fk_member_identity_verification_current_revision",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("version>=1", name="version"),
        {"schema": "public"},
    )
    verification_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("public.service_enrollment.enrollment_id", name="fk_member_identity_verification_enrollment"), nullable=False)
    member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_member_identity_verification_member_id_member"), nullable=False)
    current_revision_id: Mapped[UUID | None] = mapped_column(UUIDType)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    institution_decision_id: Mapped[UUID | None] = mapped_column(UUIDType)
    platform_decision_id: Mapped[UUID | None] = mapped_column(UUIDType)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    institution_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemberIdentityRevisionModel(Base):
    __tablename__ = "member_identity_revision"
    __table_args__ = (
        UniqueConstraint("verification_id", "revision_no", name="uq_member_identity_revision_no"),
        UniqueConstraint("verification_id", "revision_id", name="uq_member_identity_revision_identity"),
        CheckConstraint("revision_no>=1 AND document_type='PRC_RESIDENT_ID'", name="truth"),
        {"schema": "public"},
    )
    revision_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    verification_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_identity_verification.verification_id", name="fk_member_identity_revision_verification"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    document_type: Mapped[str] = mapped_column(String(24), nullable=False)
    real_name_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    real_name_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    id_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    birth_date_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    birth_date_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    id_masked: Mapped[str] = mapped_column(String(24), nullable=False)
    identity_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_by_member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_member_identity_revision_submitted_by_member_id_member"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemberIdentityReviewDecisionModel(Base):
    __tablename__ = "member_identity_review_decision"
    __table_args__ = (
        UniqueConstraint("verification_id", "revision_id", "phase", name="uq_member_identity_review_phase"),
        UniqueConstraint("verification_id", "decision_id", name="uq_member_identity_review_decision_verification_decision"),
        ForeignKeyConstraint(("verification_id","revision_id"),("public.member_identity_revision.verification_id","public.member_identity_revision.revision_id"),name="fk_member_identity_review_decision_revision_scope"),
        CheckConstraint("phase IN ('INSTITUTION','PLATFORM')", name="phase"),
        CheckConstraint("decision IN ('CHECKED','APPROVED','NEEDS_CORRECTION','REJECTED')", name="decision"),
        {"schema": "public"},
    )
    decision_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    verification_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_identity_verification.verification_id", name="fk_member_identity_decision_verification"), nullable=False)
    revision_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_identity_revision.revision_id", name="fk_member_identity_decision_revision"), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_member_identity_review_decision_reviewer_user_id_user"), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    correction_fields: Mapped[list[str] | None] = mapped_column(JSONB)
    attestation_code: Mapped[str | None] = mapped_column(String(64))
    represented_elder_eligible: Mapped[bool | None] = mapped_column(Boolean)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemberIdentityPiiAccessModel(Base):
    __tablename__ = "member_identity_pii_access"
    __table_args__ = (
        UniqueConstraint("nonce", name="uq_member_identity_pii_access_nonce"),
        UniqueConstraint("reviewer_user_id", "idempotency_key", name="uq_member_identity_pii_access_idempotency"),
        ForeignKeyConstraint(
            ["verification_id", "current_revision_id"],
            ["public.member_identity_verification.verification_id", "public.member_identity_verification.current_revision_id"],
            name="fk_member_identity_pii_access_current_revision",
        ),
        CheckConstraint(
            "(status='ISSUED' AND consumed_at IS NULL) OR (status='CONSUMED' AND consumed_at IS NOT NULL)",
            name="truth",
        ),
        {"schema": "public"},
    )
    access_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    verification_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    current_revision_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    reviewer_user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_member_identity_pii_access_reviewer_user_id_user"), nullable=False)
    nonce: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    access_token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    currentness_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    postimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ProxyGrantModel(Base):
    __tablename__ = "proxy_grant"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="uq_proxy_grant_enrollment"),
        ForeignKeyConstraint(("enrollment_id","principal_member_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.subject_member_id"),name="fk_proxy_grant_principal_scope"),
        ForeignKeyConstraint(("enrollment_id","proxy_member_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.proxy_member_id"),name="fk_proxy_grant_proxy_scope"),
        CheckConstraint("slot_no IN (1,2)", name="slot"),
        CheckConstraint("principal_member_id<>proxy_member_id AND version>=1", name="subject"),
        CheckConstraint(
            "(status='CONSENT_PENDING' AND authorization_document_version_id IS NULL AND witness_decision_id IS NULL AND valid_from IS NULL AND valid_until IS NULL AND revoked_at IS NULL) OR "
            "(status='ACTIVE' AND authorization_document_version_id IS NOT NULL AND witness_decision_id IS NOT NULL AND valid_from IS NOT NULL AND revoked_at IS NULL) OR "
            "(status IN ('REVOKED','EXPIRED') AND revoked_at IS NOT NULL)",
            name="truth",
        ),
        Index("uq_proxy_grant_active_principal", "principal_member_id", unique=True, postgresql_where=text("status IN ('CONSENT_PENDING','ACTIVE')")),
        Index("uq_proxy_grant_active_slot", "proxy_member_id", "slot_no", unique=True, postgresql_where=text("status IN ('CONSENT_PENDING','ACTIVE')")),
        {"schema": "public"},
    )
    grant_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("public.service_enrollment.enrollment_id", name="fk_proxy_grant_enrollment_id_service_enrollment"), nullable=False)
    principal_member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_proxy_grant_principal_member_id_member"), nullable=False)
    proxy_member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_proxy_grant_proxy_member_id_member"), nullable=False)
    slot_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    permission_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    authorization_document_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("public.consent_document_version.document_version_id",name="fk_proxy_grant_authorization_document_version"))
    witness_decision_id: Mapped[UUID | None] = mapped_column(ForeignKey("public.member_identity_review_decision.decision_id", name="fk_proxy_grant_witness_decision"))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ConsentDocumentVersionModel(Base):
    __tablename__ = "consent_document_version"
    __table_args__ = (
        UniqueConstraint("document_type", "semantic_version", name="uq_consent_document_semantic_version"),
        CheckConstraint("requires_reconsent=true AND version>=1", name="version"),
        CheckConstraint(
            "(status='DRAFT' AND effective_at IS NULL AND retired_at IS NULL AND published_by IS NULL) OR "
            "(status='PUBLISHED' AND effective_at IS NOT NULL AND retired_at IS NULL AND published_by IS NOT NULL) OR "
            "(status='RETIRED' AND effective_at IS NOT NULL AND retired_at IS NOT NULL AND published_by IS NOT NULL)",
            name="truth",
        ),
        Index("uq_consent_document_published_type", "document_type", unique=True, postgresql_where=text("status='PUBLISHED'")),
        {"schema": "public"},
    )
    document_version_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    semantic_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requires_reconsent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_consent_document_version_published_by_user"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ConsentDocumentRenditionModel(Base):
    __tablename__ = "consent_document_rendition"
    __table_args__ = (
        UniqueConstraint("document_version_id", "locale", name="uq_consent_document_rendition_locale"),
        UniqueConstraint("document_version_id", "rendition_id", name="uq_consent_document_rendition_document_rendition"),
        {"schema": "public"},
    )
    rendition_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("public.consent_document_version.document_version_id", name="fk_consent_rendition_document_version"), nullable=False)
    locale: Mapped[str] = mapped_column(String(35), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ConsentRecordModel(Base):
    __tablename__ = "consent_record"
    __table_args__ = (
        CheckConstraint("choice IN ('ACCEPTED','DECLINED') AND version>=1", name="choice"),
        CheckConstraint(
            "(status='PRESENTED' AND accepted_at IS NULL AND withdrawn_at IS NULL) OR "
            "(status='ACCEPTED' AND choice='ACCEPTED' AND accepted_at IS NOT NULL AND withdrawn_at IS NULL) OR "
            "(status='DECLINED' AND choice='DECLINED' AND accepted_at IS NULL AND withdrawn_at IS NULL) OR "
            "(status IN ('SUPERSEDED','WITHDRAWN') AND accepted_at IS NOT NULL)",
            name="truth",
        ),
        Index("uq_consent_record_current", "enrollment_id", "document_type", unique=True, postgresql_where=text("status='ACCEPTED'")),
        ForeignKeyConstraint(("enrollment_id","subject_member_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.subject_member_id"),name="fk_consent_record_subject_scope"),
        ForeignKeyConstraint(("enrollment_id","proxy_member_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.proxy_member_id"),name="fk_consent_record_proxy_scope"),
        ForeignKeyConstraint(("document_version_id","rendition_id"),("public.consent_document_rendition.document_version_id","public.consent_document_rendition.rendition_id"),name="fk_consent_record_rendition_scope"),
        {"schema": "public"},
    )
    consent_record_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("public.service_enrollment.enrollment_id", name="fk_consent_record_enrollment_id_service_enrollment"), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_consent_record_subject_member_id_member"), nullable=False)
    proxy_member_id: Mapped[UUID | None] = mapped_column(ForeignKey("identity.member.member_id", name="fk_consent_record_proxy_member_id_member"))
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(ForeignKey("public.consent_document_version.document_version_id", name="fk_consent_record_document_version_id_consent_document_version"), nullable=False)
    rendition_id: Mapped[UUID] = mapped_column(ForeignKey("public.consent_document_rendition.rendition_id", name="fk_consent_record_rendition_id_consent_document_rendition"), nullable=False)
    purpose_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    choice: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    predecessor_id: Mapped[UUID | None] = mapped_column(ForeignKey("public.consent_record.consent_record_id", name="fk_consent_record_predecessor_id_consent_record"))
    presented_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PrimaryTherapistAssignmentModel(Base):
    __tablename__ = "primary_therapist_assignment"
    __table_args__ = (
        CheckConstraint(
            "version>=1 AND ((status='PENDING_ACCEPTANCE' AND reason_code IS NULL AND service_case_id IS NULL AND decided_at IS NULL) OR "
            "(status='ACCEPTED' AND reason_code IS NULL AND service_case_id IS NOT NULL AND decided_at IS NOT NULL) OR "
            "(status IN ('DECLINED','CANCELLED') AND reason_code IS NOT NULL AND service_case_id IS NULL AND decided_at IS NOT NULL))",
            name="truth",
        ),
        Index("uq_primary_assignment_current_enrollment", "enrollment_id", unique=True, postgresql_where=text("status='PENDING_ACCEPTANCE'")),
        UniqueConstraint("service_case_id", name="uq_primary_therapist_assignment_case"),
        UniqueConstraint("enrollment_id","assignment_id",name="uq_primary_therapist_assignment_enrollment_assignment"),
        UniqueConstraint("enrollment_id","assignment_id","tenant_id","subject_member_id",name="uq_primary_therapist_assignment_scope"),
        ForeignKeyConstraint(("enrollment_id","tenant_id","subject_member_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.tenant_id","public.service_enrollment.subject_member_id"),name="fk_primary_assignment_enrollment_scope"),
        ForeignKeyConstraint(("assignment_id","service_case_id"),("public.service_case.assignment_id","public.service_case.case_id"),name="fk_primary_assignment_service_case_scope",deferrable=True,initially="DEFERRED"),
        {"schema": "public"},
    )
    assignment_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("public.service_enrollment.enrollment_id", name="fk_primary_assignment_enrollment"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_primary_therapist_assignment_tenant_id_tenant"), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_primary_therapist_assignment_subject_member_id_member"), nullable=False)
    therapist_id: Mapped[UUID] = mapped_column(ForeignKey("public.therapist_profile.therapist_id", name="fk_primary_therapist_assignment_therapist_id_therapist_profile"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    service_scope_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    service_case_id: Mapped[UUID | None] = mapped_column(UUIDType)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_primary_therapist_assignment_created_by_user"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ServiceCaseModel(Base):
    __tablename__ = "service_case"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="uq_service_case_enrollment"),
        UniqueConstraint("assignment_id", name="uq_service_case_assignment"),
        UniqueConstraint("enrollment_id","case_id",name="uq_service_case_enrollment_case"),
        UniqueConstraint("assignment_id","case_id",name="uq_service_case_assignment_case"),
        ForeignKeyConstraint(("enrollment_id","tenant_id","subject_member_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.tenant_id","public.service_enrollment.subject_member_id"),name="fk_service_case_enrollment_scope"),
        ForeignKeyConstraint(("identity_verification_id","identity_revision_id"),("public.member_identity_verification.verification_id","public.member_identity_verification.current_revision_id"),name="fk_service_case_identity_revision_scope"),
        ForeignKeyConstraint(("enrollment_id","identity_verification_id","assignment_id"),("public.service_enrollment.enrollment_id","public.service_enrollment.current_identity_verification_id","public.service_enrollment.current_assignment_id"),name="fk_service_case_current_inputs_scope",deferrable=True,initially="DEFERRED"),
        ForeignKeyConstraint(("enrollment_id","assignment_id","tenant_id","subject_member_id"),("public.primary_therapist_assignment.enrollment_id","public.primary_therapist_assignment.assignment_id","public.primary_therapist_assignment.tenant_id","public.primary_therapist_assignment.subject_member_id"),name="fk_service_case_assignment_scope",deferrable=True,initially="DEFERRED"),
        CheckConstraint("status='PREPARING' AND version>=1", name="truth"),
        Index("uq_service_case_active_subject", "subject_member_id", unique=True, postgresql_where=text("status='PREPARING'")),
        {"schema": "public"},
    )
    case_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    enrollment_id: Mapped[UUID] = mapped_column(ForeignKey("public.service_enrollment.enrollment_id", name="fk_service_case_enrollment_id_service_enrollment"), nullable=False)
    subject_member_id: Mapped[UUID] = mapped_column(ForeignKey("identity.member.member_id", name="fk_service_case_subject_member_id_member"), nullable=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.id", name="fk_service_case_tenant_id_tenant"), nullable=False)
    primary_therapist_id: Mapped[UUID] = mapped_column(ForeignKey("public.therapist_profile.therapist_id", name="fk_service_case_primary_therapist_id_therapist_profile"), nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey("public.primary_therapist_assignment.assignment_id", name="fk_service_case_assignment_id_primary_therapist_assignment", deferrable=True, initially="DEFERRED"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    identity_verification_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_identity_verification.verification_id", name="fk_service_case_identity_verification"), nullable=False)
    identity_revision_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_identity_revision.revision_id", name="fk_service_case_identity_revision_id_member_identity_revision"), nullable=False)
    consent_set_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_evidence_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    readiness_result_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    service_scope_tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemberEnrollmentIdempotencyModel(Base):
    __tablename__ = "member_enrollment_idempotency"
    __table_args__ = ({"schema": "public"},)
    actor_scope: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    target_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    postimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemberEnrollmentAuditModel(Base):
    __tablename__ = "member_enrollment_audit"
    __table_args__ = ({"schema": "public"},)
    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_scope: Mapped[str] = mapped_column(String(160), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    preimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    postimage_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MemberEnrollmentOutboxModel(Base):
    __tablename__ = "member_enrollment_outbox"
    __table_args__ = (
        CheckConstraint("attempts BETWEEN 0 AND 3 AND version>=1", name="attempts"),
        CheckConstraint(
            "(status='PENDING' AND attempts BETWEEN 0 AND 2 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NULL) OR "
            "(status='PROCESSING' AND attempts BETWEEN 1 AND 3 AND processing_at IS NOT NULL AND lease_owner IS NOT NULL AND delivered_at IS NULL AND failed_at IS NULL) OR "
            "(status='DELIVERED' AND attempts BETWEEN 1 AND 3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NOT NULL AND failed_at IS NULL) OR "
            "(status='FAILED' AND attempts=3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NOT NULL)",
            name="truth",
        ),
        CheckConstraint(
            "event_type IN (" + ",".join(repr(value) for value in EVENT_TYPES) + ")",
            name="event_type",
        ),
        {"schema": "public"},
    )
    event_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.id", name="fk_member_enrollment_outbox_tenant_id_tenant"))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[UUID | None] = mapped_column(UUIDType)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)


class MemberEnrollmentDeliveryModel(Base):
    __tablename__ = "member_enrollment_delivery"
    __table_args__ = (
        UniqueConstraint("event_id", "target_key_id", "target_digest", name="uq_member_enrollment_delivery_target"),
        CheckConstraint(
            "((recipient_user_id IS NOT NULL)::int + (recipient_tenant_id IS NOT NULL)::int + "
            "(recipient_platform_code IS NOT NULL)::int)=1",
            name="recipient",
        ),
        {"schema": "public"},
    )
    delivery_id: Mapped[UUID] = mapped_column(UUIDType, primary_key=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("public.member_enrollment_outbox.event_id", name="fk_member_enrollment_delivery_event_id_member_enrollment_outbox"), nullable=False)
    recipient_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id", name="fk_member_enrollment_delivery_recipient_user_id_user"))
    recipient_tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenant.id", name="fk_member_enrollment_delivery_recipient_tenant_id_tenant"))
    recipient_platform_code: Mapped[str | None] = mapped_column(String(48))
    target_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
