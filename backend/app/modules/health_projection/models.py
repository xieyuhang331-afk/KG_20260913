from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CHAR, CheckConstraint, Date, DateTime, ForeignKeyConstraint,
    Index, Numeric, SmallInteger, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.organization_projection.models import _FAILURES


class HealthProjectionGeneration(Base):
    __tablename__ = "health_projection_generation"
    __table_args__ = (
        UniqueConstraint("projection_version", "generation_no", name="uq_health_projection_generation_version_no"),
        UniqueConstraint("start_operation_id", name="uq_health_projection_generation_start_operation"),
        UniqueConstraint("id", "digest_key_id", name="uq_health_projection_generation_id_digest_key"),
        CheckConstraint("projection_version=1 AND generation_no>=1 AND lease_epoch>=0 AND version>=1", name="version"),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="digest"),
        CheckConstraint("jsonb_typeof(high_watermark)='object' AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','source_snapshot',high_watermark->'source_snapshot') AND jsonb_typeof(high_watermark->'max_fact_id')='number' AND (high_watermark->>'max_fact_id') ~ '^(0|[1-9][0-9]*)$' AND jsonb_typeof(high_watermark->'source_snapshot')='string' AND length(high_watermark->>'source_snapshot') BETWEEN 3 AND 512", name="high_watermark"),
        CheckConstraint(f"(status='BUILDING' AND completed_at IS NULL AND failure_code IS NULL AND builder_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status='BUILD_COMPLETE' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='FAILED' AND completed_at IS NOT NULL AND failure_code IN ({_FAILURES}) AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='SUPERSEDED' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL)", name="state"),
        Index("idx_health_projection_generation_status", "status", "projection_version", "generation_no"),
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


class HealthProjectionCheckpoint(Base):
    __tablename__ = "health_projection_checkpoint"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id",), ("public.health_projection_generation.id",), name="fk_health_projection_checkpoint_generation", ondelete="CASCADE"),
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


class HealthProjectionFactModel(Base):
    __tablename__ = "health_projection_fact"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id", "digest_key_id"), ("public.health_projection_generation.id", "public.health_projection_generation.digest_key_id"), name="fk_health_projection_fact_generation_key", ondelete="CASCADE"),
        UniqueConstraint("generation_id", "fact_id", "subject_user_id", "indicator_code", "business_day", "digest_key_id", name="uq_health_projection_fact_winner_identity"),
        CheckConstraint("indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')", name="indicator_v1"),
        CheckConstraint("((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR (indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR (indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR (indicator_code='bmi' AND unit='kg/m2') OR (indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))", name="unit_v1"),
        CheckConstraint("numeric_value::text NOT IN ('NaN','Infinity','-Infinity')", name="numeric"),
        CheckConstraint("source_type IN ('DEVICE','STORE','REPORT','APP')", name="source"),
        CheckConstraint("business_day=(measured_at AT TIME ZONE 'Asia/Shanghai')::date AND window_start_utc=(business_day::timestamp AT TIME ZONE 'Asia/Shanghai') AND window_end_utc=((business_day+1)::timestamp AT TIME ZONE 'Asia/Shanghai') AND measured_at>=window_start_utc AND measured_at<window_end_utc", name="window"),
        CheckConstraint("row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND fact_id>=1 AND subject_user_id>=1", name="digest"),
        Index("idx_health_projection_fact_subject_indicator_time", "generation_id", "subject_user_id", "indicator_code", "measured_at", "fact_id"),
        Index("idx_health_projection_fact_window", "generation_id", "subject_user_id", "indicator_code", "business_day"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fact_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    indicator_code: Mapped[str] = mapped_column(String(64), nullable=False)
    numeric_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    business_day: Mapped[date] = mapped_column(Date, nullable=False)
    window_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))


class HealthProjectionWindowSelectionModel(Base):
    __tablename__ = "health_projection_window_selection"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id", "winner_fact_id", "subject_user_id", "indicator_code", "business_day", "digest_key_id"), ("public.health_projection_fact.generation_id", "public.health_projection_fact.fact_id", "public.health_projection_fact.subject_user_id", "public.health_projection_fact.indicator_code", "public.health_projection_fact.business_day", "public.health_projection_fact.digest_key_id"), name="fk_health_projection_selection_winner_identity", ondelete="RESTRICT"),
        CheckConstraint("rule_version='health-daily-selection-v1'", name="rule_v1"),
        CheckConstraint("selection_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND winner_fact_id>=1 AND subject_user_id>=1", name="digest"),
        Index("idx_health_projection_selection_subject_day", "generation_id", "subject_user_id", "business_day", "indicator_code"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indicator_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_day: Mapped[date] = mapped_column(Date, primary_key=True)
    winner_fact_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
