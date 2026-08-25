from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PrivateFileModel(Base):
    __tablename__ = "private_file"
    __table_args__ = (
        CheckConstraint("declared_size BETWEEN 1 AND 10485760 AND (actual_size IS NULL OR actual_size BETWEEN 1 AND 10485760)", name="size"),
        CheckConstraint("declared_mime_type IN ('application/pdf','image/jpeg','image/png','application/zip')", name="mime_type"),
        CheckConstraint("status IN ('UPLOAD_INITIATED','PENDING_SCAN','CLEAN','REJECTED','SCAN_FAILED','DELETED')", name="status"),
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
