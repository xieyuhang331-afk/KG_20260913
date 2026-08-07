from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

from .eligibility_evidence_models import _StandardLibraryUuid


class RegistrationVerifiedOutboxOrmModel(Base):
    __tablename__ = "registration_verified_outbox"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            name="uq_registration_verified_outbox_event",
        ),
        UniqueConstraint(
            "semantic_idempotency_key",
            name="uq_registration_verified_outbox_semantic_key",
        ),
        UniqueConstraint(
            "verification_decision_ref",
            name="uq_registration_verified_outbox_verification",
        ),
        UniqueConstraint(
            "authority_decision_key",
            name="uq_registration_verified_outbox_authority",
        ),
        UniqueConstraint(
            "event_type",
            "source_system",
            "source_ref",
            "verification_decision_ref",
            name="uq_registration_verified_outbox_source_decision",
        ),
        ForeignKeyConstraint(
            (
                "verification_decision_ref",
                "authority_decision_key",
                "event_id",
                "facts_version",
            ),
            (
                "public.identity_verification_decision.decision_ref",
                "public.identity_verification_decision.authority_decision_key",
                "public.identity_verification_decision.registration_event_id",
                "public.identity_verification_decision.facts_version",
            ),
            name="fk_registration_verified_outbox_verification_identity",
        ),
        CheckConstraint(
            "event_type = 'identity.registration.verification_verified'",
            name="event_type",
        ),
        CheckConstraint(
            "event_schema_version = 1",
            name="schema_version",
        ),
        CheckConstraint(
            "source_system = 'P1_USER'",
            name="source_system",
        ),
        CheckConstraint(
            "source_ref > 0",
            name="source_positive",
        ),
        CheckConstraint(
            "facts_version > 0",
            name="facts_positive",
        ),
        CheckConstraint(
            "authority_decision_key ~ '^[0-9a-f]{64}$'",
            name="authority_sha256",
        ),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="payload_sha256",
        ),
        CheckConstraint(
            "semantic_idempotency_key = "
            "'identity.registration.verification_verified:v1:p1_user:' "
            "|| source_ref::text || ':authority:' || authority_decision_key",
            name="semantic_key",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'delivered', "
            "'review_required', 'dead_letter')",
            name="status_closed",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 8",
            name="attempt_budget",
        ),
        CheckConstraint(
            "lease_generation >= 0",
            name="lease_generation",
        ),
        CheckConstraint(
            "(status = 'processing' AND lease_owner IS NOT NULL "
            "AND locked_until IS NOT NULL AND lease_generation > 0) OR "
            "(status <> 'processing' AND lease_owner IS NULL "
            "AND locked_until IS NULL)",
            name="lease_shape",
        ),
        CheckConstraint(
            "(status = 'delivered' AND delivered_at IS NOT NULL) OR "
            "(status <> 'delivered' AND delivered_at IS NULL)",
            name="delivered_shape",
        ),
        CheckConstraint(
            "(last_error_category IS NULL AND last_error_code IS NULL "
            "AND last_error_digest IS NULL) OR "
            "(last_error_category IS NOT NULL AND last_error_code IS NOT NULL "
            "AND last_error_digest IS NOT NULL)",
            name="error_shape",
        ),
        CheckConstraint(
            "last_error_category IS NULL OR last_error_category IN "
            "('RETRYABLE', 'REVIEW_REQUIRED', 'PERMANENT', "
            "'INTERNAL_UNKNOWN', 'LEASE')",
            name="error_category",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('DEPENDENCY_UNAVAILABLE', 'RATE_LIMITED', "
            "'TRANSIENT_TRANSPORT', 'ORCHESTRATOR_RETRYABLE', "
            "'RETRY_EXHAUSTED', 'ELIGIBILITY_PROOF_MISSING', "
            "'ELIGIBILITY_STALE', 'ELIGIBILITY_INCONSISTENT', "
            "'BOOTSTRAP_CONFLICT', 'OUTCOME_UNKNOWN_UNCONFIRMED', "
            "'P1_VERIFICATION_OUTBOX_INCONSISTENT', "
            "'UNSUPPORTED_EVENT_TYPE', 'UNSUPPORTED_SCHEMA_VERSION', "
            "'INVALID_ENVELOPE', 'UNEXPECTED_INTERNAL', 'LEASE_LOST')",
            name="error_code",
        ),
        CheckConstraint(
            "last_error_digest IS NULL OR "
            "last_error_digest ~ '^[0-9a-f]{64}$'",
            name="error_digest",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="timestamps",
        ),
        Index(
            "ix_registration_verified_outbox_schedule",
            "status",
            "available_at",
            "created_at",
        ),
        Index(
            "ix_registration_verified_outbox_expired_lease",
            "status",
            "locked_until",
        ),
        Index(
            "ix_registration_verified_outbox_source_verification",
            "source_ref",
            "verification_decision_ref",
        ),
        {"schema": "public"},
    )

    outbox_record_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), primary_key=True
    )
    event_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    semantic_idempotency_key: Mapped[str] = mapped_column(
        String(192), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_schema_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verification_decision_ref: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    authority_decision_key: Mapped[str] = mapped_column(
        CHAR(64), nullable=False
    )
    facts_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    trace_ref: Mapped[UUID | None] = mapped_column(_StandardLibraryUuid())
    payload_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'pending'")
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    lease_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_error_category: Mapped[str | None] = mapped_column(String(40))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_digest: Mapped[str | None] = mapped_column(CHAR(64))
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
