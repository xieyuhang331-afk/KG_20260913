from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CHAR, CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HealthIndicatorLegacyMappingOrmModel(Base):
    __tablename__ = "health_indicator_legacy_mapping"
    __table_args__ = (
        UniqueConstraint("legacy_indicator_id", "legacy_recorded_at", "mapping_version", name="uq_health_indicator_legacy_mapping_source_version"),
        CheckConstraint("mapping_version = 1", name="version_v1"),
        CheckConstraint("disposition IN ('MAPPED','UNMAPPED','REVIEW_REQUIRED','CONFLICT','BLOCKED')", name="disposition"),
        CheckConstraint(
            "(disposition='MAPPED' AND reason_code='MAPPED_EXACT') OR "
            "(disposition='UNMAPPED' AND reason_code IN ('INDICATOR_NOT_OPEN','UNIT_MISMATCH','VALUE_INVALID','SOURCE_INVALID','TIME_INVALID','SUBJECT_MISSING')) OR "
            "(disposition='REVIEW_REQUIRED' AND reason_code='SOURCE_AUTHORITY_UNVERIFIED') OR "
            "(disposition='CONFLICT' AND reason_code IN ('SOURCE_DRIFT','CANONICAL_CONFLICT')) OR "
            "(disposition='BLOCKED' AND reason_code='SOURCE_CORRUPTED')",
            name="reason",
        ),
        CheckConstraint("source_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_hex"),
        CheckConstraint(
            "(disposition='MAPPED' AND canonical_fact_id IS NOT NULL) OR "
            "(disposition<>'MAPPED' AND canonical_fact_id IS NULL)",
            name="target_pair",
        ),
        Index("idx_health_indicator_legacy_mapping_batch_source", "batch_id", "legacy_recorded_at", "legacy_indicator_id"),
        Index("idx_health_indicator_legacy_mapping_disposition", "mapping_version", "disposition", "legacy_recorded_at", "legacy_indicator_id"),
        Index("uq_health_indicator_legacy_mapping_fact_version", "canonical_fact_id", "mapping_version", unique=True, postgresql_where=text("canonical_fact_id IS NOT NULL")),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    legacy_indicator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    legacy_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canonical_fact_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("public.canonical_health_fact.id", name="fk_health_indicator_legacy_mapping_fact", ondelete="RESTRICT"))
    mapping_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    batch_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id", name="fk_health_indicator_legacy_mapping_created_by", ondelete="RESTRICT"))
