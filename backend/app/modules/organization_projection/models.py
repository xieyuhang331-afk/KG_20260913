from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CHAR, CheckConstraint, DateTime, ForeignKeyConstraint,
    Index, Integer, SmallInteger, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


PROJECTION_BUILD_STATUSES = {"BUILDING", "BUILD_COMPLETE", "FAILED", "SUPERSEDED"}
_FAILURES = (
    "'PROJECTION_SOURCE_INVALID','PROJECTION_DIGEST_KEY_UNAVAILABLE',"
    "'PROJECTION_CHECKPOINT_CONFLICT','PROJECTION_LEASE_CONFLICT','PROJECTION_UNAVAILABLE'"
)


class OrganizationProjectionGeneration(Base):
    __tablename__ = "organization_projection_generation"
    __table_args__ = (
        UniqueConstraint("projection_version", "generation_no", name="uq_organization_projection_generation_version_no"),
        UniqueConstraint("start_operation_id", name="uq_organization_projection_generation_start_operation"),
        UniqueConstraint("id", "digest_key_id", name="uq_organization_projection_generation_id_digest_key"),
        CheckConstraint("projection_version=1 AND generation_no>=1 AND lease_epoch>=0 AND version>=1", name="version"),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="digest"),
        CheckConstraint("jsonb_typeof(high_watermark)='object' AND high_watermark=jsonb_build_object('max_organization_id',high_watermark->'max_organization_id') AND jsonb_typeof(high_watermark->'max_organization_id')='number' AND (high_watermark->>'max_organization_id') ~ '^(0|[1-9][0-9]*)$'", name="high_watermark"),
        CheckConstraint(f"(status='BUILDING' AND completed_at IS NULL AND failure_code IS NULL AND builder_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status='BUILD_COMPLETE' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='FAILED' AND completed_at IS NOT NULL AND failure_code IN ({_FAILURES}) AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='SUPERSEDED' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL)", name="state"),
        Index("idx_organization_projection_generation_status", "status", "projection_version", "generation_no"),
        {"schema": "public"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    projection_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    generation_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    high_watermark: Mapped[dict] = mapped_column(JSONB, nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    start_operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    builder_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class OrganizationProjectionCheckpoint(Base):
    __tablename__ = "organization_projection_checkpoint"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id",), ("public.organization_projection_generation.id",), name="fk_organization_projection_checkpoint_generation", ondelete="CASCADE"),
        CheckConstraint("processed_count>=0 AND projected_count>=0 AND skipped_count>=0 AND remaining_count>=0 AND projected_count+skipped_count=processed_count", name="counts"),
        CheckConstraint("last_source_id IS NULL OR last_source_id>=1", name="cursor"),
        CheckConstraint("checkpoint_digest ~ '^[0-9a-f]{64}$' AND version>=1", name="digest"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_source_id: Mapped[int | None] = mapped_column(BigInteger)
    processed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    projected_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    skipped_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    remaining_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_operation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    checkpoint_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class OrganizationProjectionModel(Base):
    __tablename__ = "organization_projection"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id", "digest_key_id"), ("public.organization_projection_generation.id", "public.organization_projection_generation.digest_key_id"), name="fk_organization_projection_generation_key", ondelete="CASCADE"),
        UniqueConstraint("generation_id", "org_code", name="uq_organization_projection_generation_code"),
        CheckConstraint("org_type IN ('headquarter','province','city','county')", name="type"),
        CheckConstraint("status IN ('active','inactive','archived')", name="status"),
        CheckConstraint("source_version>=1 AND organization_id>=1 AND sort_order>=0", name="source_version"),
        CheckConstraint("status='active' OR scope_eligible=false", name="scope"),
        CheckConstraint("row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="digest"),
        CheckConstraint("compatibility_mode='canonical' AND jsonb_typeof(path_ids)='array' AND jsonb_typeof(path_codes)='array' AND jsonb_array_length(path_ids)=4 AND jsonb_array_length(path_codes)=4", name="compatibility"),
        Index("idx_organization_projection_parent_order", "generation_id", "parent_id", "sort_order", "organization_id"),
        Index("idx_organization_projection_type_status", "generation_id", "org_type", "status"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    org_code: Mapped[str] = mapped_column(String(50), nullable=False)
    org_name: Mapped[str] = mapped_column(String(100), nullable=False)
    org_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    source_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    path_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    path_codes: Mapped[list] = mapped_column(JSONB, nullable=False)
    compatibility_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'canonical'"))
    scope_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    row_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
