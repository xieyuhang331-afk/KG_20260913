from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdentityVerificationSubmissionOrmModel(Base):
    __tablename__ = "identity_verification_submission"
    __table_args__ = (
        UniqueConstraint("user_ref", "version", name="uq_identity_submission_user_version"),
        UniqueConstraint("user_ref", "idempotency_key_digest", name="uq_identity_submission_user_idempotency"),
        Index(
            "uq_identity_submission_current",
            "user_ref",
            unique=True,
            postgresql_where=text("status IN ('submitted', 'verified')"),
        ),
        Index("ix_identity_submission_review_queue", "status", "submitted_at"),
        CheckConstraint("user_ref > 0", name="identity_submission_user_positive"),
        CheckConstraint("version > 0", name="identity_submission_version_positive"),
        CheckConstraint("status IN ('submitted','verified','rejected')", name="identity_submission_status_closed"),
        CheckConstraint("octet_length(real_name_nonce) = 12", name="identity_submission_name_nonce"),
        CheckConstraint("octet_length(id_card_nonce) = 12", name="identity_submission_card_nonce"),
        CheckConstraint("char_length(content_digest) = 64", name="identity_submission_content_digest"),
        CheckConstraint("char_length(id_card_digest) = 64", name="identity_submission_card_digest"),
        CheckConstraint("char_length(idempotency_key_digest) = 64", name="identity_submission_idempotency_digest"),
        {"schema": "public"},
    )

    submission_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    user_ref: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    real_name_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    real_name_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_card_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_card_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_card_masked: Mapped[str] = mapped_column(String(18), nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    id_card_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger)
    decision_basis_code: Mapped[str | None] = mapped_column(String(64))
    evidence_digest: Mapped[str | None] = mapped_column(String(64))
    rejection_reason_code: Mapped[str | None] = mapped_column(String(64))
