from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CHAR, CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OrganizationLegacyMappingOrmModel(Base):
    __tablename__ = "organization_legacy_mapping"
    __table_args__ = (
        UniqueConstraint("legacy_tenant_id", "mapping_version", name="uq_organization_legacy_mapping_source_version"),
        CheckConstraint("mapping_version = 1", name="version_v1"),
        CheckConstraint("disposition IN ('MAPPED','UNMAPPED','REVIEW_REQUIRED','CONFLICT','BLOCKED')", name="disposition"),
        CheckConstraint(
            "(disposition='MAPPED' AND reason_code='MAPPED_EXACT') OR "
            "(disposition='UNMAPPED' AND reason_code IN ('SOURCE_ORG_MISSING','TARGET_NOT_FOUND')) OR "
            "(disposition='REVIEW_REQUIRED' AND reason_code IN ('TARGET_LEGACY','TARGET_ARCHIVED')) OR "
            "(disposition='CONFLICT' AND reason_code IN ('SOURCE_DRIFT','TARGET_CONFLICT')) OR "
            "(disposition='BLOCKED' AND reason_code IN ('TARGET_CHAIN_INVALID','SOURCE_CORRUPTED'))",
            name="reason",
        ),
        CheckConstraint("source_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_hex"),
        CheckConstraint(
            "(disposition='MAPPED' AND legacy_org_id IS NOT NULL AND canonical_organization_id IS NOT NULL "
            "AND legacy_org_id=canonical_organization_id) OR "
            "(disposition<>'MAPPED' AND canonical_organization_id IS NULL)",
            name="target_pair",
        ),
        Index("idx_organization_legacy_mapping_batch_source", "batch_id", "legacy_tenant_id"),
        Index("idx_organization_legacy_mapping_disposition", "mapping_version", "disposition", "legacy_tenant_id"),
        Index("idx_organization_legacy_mapping_target", "canonical_organization_id", postgresql_where=text("canonical_organization_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legacy_tenant_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tenant.id", name="fk_organization_legacy_mapping_tenant", ondelete="RESTRICT"), nullable=False)
    legacy_org_id: Mapped[int | None] = mapped_column(BigInteger)
    canonical_organization_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "platform_org.id",
            name="fk_organization_legacy_mapping_canonical_org",
            ondelete="RESTRICT",
        ),
    )
    mapping_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", name="fk_organization_legacy_mapping_created_by", ondelete="RESTRICT"))
