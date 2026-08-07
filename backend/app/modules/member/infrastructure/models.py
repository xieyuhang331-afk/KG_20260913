from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    String,
    UniqueConstraint,
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

    def process_result_value(
        self, value: object, dialect: Dialect
    ) -> UUID | None:
        if value is None:
            return None
        if type(value) is UUID:
            return value
        if isinstance(value, UUID):
            return UUID(int=value.int)
        raise ValueError("member id database value is invalid")


class MemberOrmModel(Base):
    __tablename__ = "member"
    __table_args__ = (
        UniqueConstraint("member_no"),
        {"schema": "identity"},
    )

    member_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(),
        primary_key=True,
        nullable=False,
    )
    member_no: Mapped[str] = mapped_column(String(64), nullable=False)
    creation_source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MemberNoAllocationOrmModel(Base):
    __tablename__ = "member_no_allocation"
    __table_args__ = (
        UniqueConstraint(
            "allocation_scope",
            "source_system",
            "source_ref",
            name="uq_member_no_allocation_source",
        ),
        UniqueConstraint(
            "member_no",
            name="uq_member_no_allocation_member_no",
        ),
        CheckConstraint(
            "allocation_scope = 'registration_bootstrap'",
            name="scope_registration_bootstrap",
        ),
        CheckConstraint(
            "source_system = 'p1_user'",
            name="source_system_p1_user",
        ),
        CheckConstraint(
            "source_ref > 0",
            name="source_ref_positive",
        ),
        CheckConstraint(
            "member_no ~ '^M[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{20}$'",
            name="member_no_format",
        ),
        CheckConstraint(
            "state = 'allocated'",
            name="state_allocated",
        ),
        CheckConstraint(
            "version = 1",
            name="version_one",
        ),
        CheckConstraint(
            "updated_at = created_at",
            name="timestamps_immutable",
        ),
        {"schema": "identity"},
    )

    allocation_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(),
        primary_key=True,
        nullable=False,
    )
    allocation_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_no: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    request_ref: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class UserMemberSelfLinkOrmModel(Base):
    __tablename__ = "user_member_self_link"
    __table_args__ = (
        UniqueConstraint("user_ref", name="uq_user_member_self_link_user"),
        UniqueConstraint("member_id", name="uq_user_member_self_link_member"),
        CheckConstraint(
            "source = 'REGISTRATION_VERIFIED'",
            name="source_registration_verified",
        ),
        CheckConstraint(
            "establishment_basis = 'REGISTRATION_VERIFIED_BOOTSTRAP'",
            name="establishment_basis_registration_verified_bootstrap",
        ),
        CheckConstraint("user_ref > 0", name="user_ref_positive"),
        {"schema": "identity"},
    )

    link_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), primary_key=True, nullable=False
    )
    user_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    eligibility_decision_ref: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    establishment_basis: Mapped[str] = mapped_column(
        String(48), nullable=False
    )
    establishment_record_ref: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RegistrationBootstrapRecordOrmModel(Base):
    __tablename__ = "registration_bootstrap_record"
    __table_args__ = (
        UniqueConstraint(
            "bootstrap_scope",
            "source_system",
            "source_ref",
            name="uq_registration_bootstrap_canonical_source",
        ),
        UniqueConstraint(
            "member_id", name="uq_registration_bootstrap_member"
        ),
        UniqueConstraint(
            "self_link_id", name="uq_registration_bootstrap_self_link"
        ),
        UniqueConstraint(
            "member_no_allocation_ref",
            name="uq_registration_bootstrap_allocation",
        ),
        CheckConstraint(
            "source = 'REGISTRATION_VERIFIED'",
            name="source_registration_verified",
        ),
        CheckConstraint("user_ref > 0", name="user_ref_positive"),
        CheckConstraint(
            "bootstrap_scope = 'REGISTRATION_VERIFIED'",
            name="bootstrap_scope_registration_verified",
        ),
        CheckConstraint(
            "source_system = 'P1_USER'",
            name="source_system_p1_user",
        ),
        CheckConstraint(
            "source_ref = user_ref AND source_ref > 0",
            name="source_ref_matches_user",
        ),
        CheckConstraint(
            "decision = 'APPROVED'", name="decision_approved"
        ),
        {"schema": "identity"},
    )

    record_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), primary_key=True, nullable=False
    )
    user_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    member_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    self_link_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    registration_event_id: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    eligibility_decision_ref: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    member_no_allocation_ref: Mapped[UUID] = mapped_column(
        _StandardLibraryUuid(), nullable=False
    )
    member_no: Mapped[str] = mapped_column(String(64), nullable=False)
    bootstrap_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    source_system: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
