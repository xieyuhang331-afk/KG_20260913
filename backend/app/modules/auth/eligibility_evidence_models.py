from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.core.database import Base


class _StandardLibraryUuid(TypeDecorator[UUID]):
    impl = PostgreSQLUUID
    cache_ok = True

    @property
    def python_type(self) -> type[UUID]:
        return UUID

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(PostgreSQLUUID(as_uuid=True))

    def process_result_value(self, value: object, dialect: Dialect) -> UUID | None:
        if value is None:
            return None
        if type(value) is UUID:
            return value
        if isinstance(value, UUID):
            return UUID(int=value.int)
        raise ValueError("eligibility evidence UUID database value is invalid")


class IdentityVerificationEvidenceOrmModel(Base):
    __tablename__ = "identity_verification_decision"
    __table_args__ = (
        UniqueConstraint("user_ref", "verification_epoch", name="uq_identity_verification_user_epoch"),
        UniqueConstraint("user_ref", "facts_version", name="uq_identity_verification_user_facts"),
        UniqueConstraint(
            "supersedes_ref",
            name="uq_identity_verification_single_successor",
        ),
        UniqueConstraint(
            "decision_ref",
            "authority_decision_key",
            "registration_event_id",
            "facts_version",
            name="uq_identity_verification_event_identity",
        ),
        Index(
            "uq_identity_verification_authority_key_present",
            "authority_decision_key",
            unique=True,
            postgresql_where=text("authority_decision_key IS NOT NULL"),
        ),
        Index(
            "uq_identity_verification_registration_event_present",
            "registration_event_id",
            unique=True,
            postgresql_where=text("registration_event_id IS NOT NULL"),
        ),
        CheckConstraint("user_ref > 0", name="identity_verification_user_positive"),
        CheckConstraint("facts_version > 0", name="identity_verification_facts_positive"),
        CheckConstraint("verification_epoch > 0", name="identity_verification_epoch_positive"),
        CheckConstraint("outcome IN ('verified', 'failed')", name="identity_verification_outcome_closed"),
        CheckConstraint(
            "(authority_decision_key IS NULL AND registration_event_id IS NULL) "
            "OR (authority_decision_key IS NOT NULL AND registration_event_id IS NOT NULL)",
            name="identity_verification_event_identity_complete",
        ),
        CheckConstraint(
            "authority_decision_key IS NULL OR "
            "authority_decision_key ~ '^[0-9a-f]{64}$'",
            name="identity_verification_authority_key_sha256",
        ),
        {"schema": "public"},
    )

    decision_ref: Mapped[UUID] = mapped_column(_StandardLibraryUuid(), primary_key=True)
    user_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    facts_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verification_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_ref: Mapped[UUID | None] = mapped_column(_StandardLibraryUuid())
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authority_decision_key: Mapped[str | None] = mapped_column(CHAR(64))
    registration_event_id: Mapped[UUID | None] = mapped_column(
        _StandardLibraryUuid()
    )


class UserAccountClassificationEvidenceOrmModel(Base):
    __tablename__ = "user_account_classification_decision"
    __table_args__ = (
        UniqueConstraint("user_ref", "classification_version", name="uq_account_classification_user_version"),
        UniqueConstraint("user_ref", "facts_version", name="uq_account_classification_user_facts"),
        UniqueConstraint(
            "supersedes_ref",
            name="uq_account_classification_single_successor",
        ),
        CheckConstraint("user_ref > 0", name="account_classification_user_positive"),
        CheckConstraint("facts_version > 0", name="account_classification_facts_positive"),
        CheckConstraint("classification_version > 0", name="account_classification_version_positive"),
        CheckConstraint(
            "account_class IN ('natural_person', 'staff', 'service', 'test', 'automation', 'unknown')",
            name="account_classification_class_closed",
        ),
        {"schema": "public"},
    )

    decision_ref: Mapped[UUID] = mapped_column(_StandardLibraryUuid(), primary_key=True)
    user_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    facts_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    classification_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_class: Mapped[str] = mapped_column(String(24), nullable=False)
    decision_basis_code: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_ref: Mapped[UUID | None] = mapped_column(_StandardLibraryUuid())
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RegistrationEligibilityDecisionEvidenceOrmModel(Base):
    __tablename__ = "registration_eligibility_decision"
    __table_args__ = (
        UniqueConstraint(
            "user_ref",
            "facts_version",
            "policy_version",
            name="uq_registration_eligibility_canonical_key",
        ),
        CheckConstraint("user_ref > 0", name="registration_eligibility_user_positive"),
        CheckConstraint("facts_version > 0", name="registration_eligibility_facts_positive"),
        CheckConstraint(
            "char_length(p1_projection_digest) = 64",
            name="registration_eligibility_p1_projection_digest_sha256",
        ),
        CheckConstraint(
            "decision IN ('eligible', 'ineligible', 'indeterminate')",
            name="registration_eligibility_decision_closed",
        ),
        {"schema": "public"},
    )

    decision_ref: Mapped[UUID] = mapped_column(_StandardLibraryUuid(), primary_key=True)
    user_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    facts_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verification_decision_ref: Mapped[UUID] = mapped_column(_StandardLibraryUuid(), nullable=False)
    classification_decision_ref: Mapped[UUID] = mapped_column(_StandardLibraryUuid(), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    facts_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    p1_projection_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
