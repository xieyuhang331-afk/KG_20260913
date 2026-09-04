from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PrivateFileModel(Base):
    __tablename__ = "private_file"
    __table_args__ = (
        CheckConstraint("declared_size BETWEEN 1 AND 10485760 AND (actual_size IS NULL OR actual_size BETWEEN 1 AND 10485760)", name="size"),
        CheckConstraint("declared_mime_type IN ('application/pdf','image/jpeg','image/png','application/zip')", name="mime_type"),
        CheckConstraint("status IN ('UPLOAD_INITIATED','PENDING_SCAN','CLEAN','REJECTED','SCAN_FAILED','DELETED')", name="status"),
        CheckConstraint("scan_attempt_count BETWEEN 0 AND 4 AND scan_version>=1", name="scan_attempt"),
        CheckConstraint("upload_version>=1", name="upload_version"),
        CheckConstraint(
            "(upload_lease_token IS NULL)=(upload_lease_until IS NULL)",
            name="upload_lease_pair",
        ),
        CheckConstraint(
            "((actual_size IS NULL AND actual_mime_type IS NULL AND actual_sha256 IS NULL) "
            "OR (actual_size IS NOT NULL AND actual_mime_type IS NOT NULL "
            "AND actual_sha256 IS NOT NULL AND upload_lease_token IS NULL "
            "AND upload_lease_until IS NULL AND upload_operation_ref_digest IS NULL))",
            name="upload_evidence",
        ),
        CheckConstraint(
            "(upload_lease_token IS NULL OR (status='UPLOAD_INITIATED' "
            "AND actual_size IS NULL AND actual_mime_type IS NULL "
            "AND actual_sha256 IS NULL AND "
            "upload_operation_ref_digest ~ '^[0-9a-f]{64}$'))",
            name="upload_lease_state",
        ),
        CheckConstraint("(scan_lease_token IS NULL)=(scan_lease_until IS NULL)", name="scan_lease_pair"),
        CheckConstraint(
            "((status='UPLOAD_INITIATED' AND scan_attempt_count=0 "
            "AND scan_last_error_code IS NULL AND scan_next_retry_at IS NULL "
            "AND scan_lease_token IS NULL AND scan_lease_until IS NULL "
            "AND scan_operation_ref_digest IS NULL) OR "
            "(status='PENDING_SCAN' AND NOT "
            "(scan_next_retry_at IS NOT NULL AND scan_lease_token IS NOT NULL) AND "
            "((scan_lease_token IS NOT NULL AND scan_attempt_count BETWEEN 1 AND 4) OR "
            "(scan_lease_token IS NULL AND scan_attempt_count<4))) OR "
            "(status IN ('CLEAN','REJECTED') AND scan_last_error_code IS NULL "
            "AND scanned_at IS NOT NULL AND scan_next_retry_at IS NULL "
            "AND scan_lease_token IS NULL AND scan_lease_until IS NULL) OR "
            "(status='SCAN_FAILED' AND scan_last_error_code IS NOT NULL "
            "AND scan_last_error_code IN "
            "('OBJECT_MISSING','EVIDENCE_MISMATCH','SCAN_SERVICE_UNAVAILABLE',"
            "'WORKER_LOST','SCAN_STATE_UNKNOWN','COMMIT_OUTCOME_UNKNOWN',"
            "'LEGACY_SCAN_FAILED') AND scanned_at IS NOT NULL "
            "AND scan_next_retry_at IS NULL AND scan_lease_token IS NULL "
            "AND scan_lease_until IS NULL) OR "
            "(status='DELETED' AND scan_next_retry_at IS NULL "
            "AND scan_lease_token IS NULL AND scan_lease_until IS NULL "
            "AND scan_operation_ref_digest IS NULL))",
            name="scan_state",
        ),
        {"schema":"public"},
    )
    file_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(48), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_private_file_owner_user"), nullable=False)
    declared_size: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_size: Mapped[int | None] = mapped_column(Integer)
    actual_mime_type: Mapped[str | None] = mapped_column(String(64))
    actual_sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    bound_application_id: Mapped[str | None] = mapped_column(ForeignKey("public.institution_application.application_id", name="fk_private_file_bound_application"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")
    scan_last_error_code: Mapped[str | None] = mapped_column(String(48))
    scan_next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_lease_token: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    scan_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_operation_ref_digest: Mapped[str | None] = mapped_column(String(64))
    scan_version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    upload_lease_token: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    upload_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upload_operation_ref_digest: Mapped[str | None] = mapped_column(String(64))
    upload_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1"
    )


class PrivateFileDownloadAccessModel(Base):
    __tablename__ = "private_file_download_access"
    __table_args__ = ({"schema": "public"},)

    access_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    private_file_id: Mapped[str] = mapped_column(
        ForeignKey("public.private_file.file_id"), nullable=False
    )
    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"), nullable=False
    )
    access_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    authority_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    content_evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
