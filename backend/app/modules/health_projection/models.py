from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, CHAR, CheckConstraint, Date, DateTime, ForeignKeyConstraint,
    Index, Numeric, SmallInteger, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.organization_projection.models import (
    _FAILURES, _module_c_generation_state, _shadow_run_checks,
)


class HealthProjectionGeneration(Base):
    __tablename__ = "health_projection_generation"
    __table_args__ = (
        UniqueConstraint("projection_version", "generation_no", name="uq_health_projection_generation_version_no"),
        UniqueConstraint("start_operation_id", name="uq_health_projection_generation_start_operation"),
        UniqueConstraint("id", "digest_key_id", name="uq_health_projection_generation_id_digest_key"),
        CheckConstraint("projection_version IN (1,2) AND generation_no>=1 AND lease_epoch>=0 AND version>=1", name="version"),
        CheckConstraint("input_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="digest"),
        CheckConstraint("jsonb_typeof(high_watermark)='object' AND ((projection_version=1 AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','source_snapshot',high_watermark->'source_snapshot')) OR (projection_version=2 AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','max_status_event_seq',high_watermark->'max_status_event_seq','source_snapshot',high_watermark->'source_snapshot'))) AND (high_watermark->>'max_fact_id') ~ '^(0|[1-9][0-9]*)$' AND (projection_version=1 OR (high_watermark->>'max_status_event_seq') ~ '^(0|[1-9][0-9]*)$') AND jsonb_typeof(high_watermark->'source_snapshot')='string' AND length(high_watermark->>'source_snapshot') BETWEEN 3 AND 512", name="high_watermark"),
        CheckConstraint(_module_c_generation_state(), name="state"),
        ForeignKeyConstraint(("current_shadow_run_id",), ("public.health_projection_shadow_run.run_id",), name="fk_health_projection_generation_current_shadow_run", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        Index("idx_health_projection_generation_status", "status", "projection_version", "generation_no"),
        Index("uq_health_projection_generation_ready_operation", "ready_operation_id", unique=True, postgresql_where=text("ready_operation_id IS NOT NULL")),
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
    current_shadow_run_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    shadow_success_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_operation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False))


class HealthProjectionShadowRun(Base):
    __tablename__ = "health_projection_shadow_run"
    __table_args__ = (
        ForeignKeyConstraint(("generation_id",), ("public.health_projection_generation.id",), name="fk_health_projection_shadow_run_generation", ondelete="RESTRICT"),
        UniqueConstraint("generation_id", "run_sequence", name="uq_health_projection_shadow_run_generation_sequence"),
        UniqueConstraint("start_operation_id", name="uq_health_projection_shadow_run_start_operation"),
        *_shadow_run_checks("health"),
        Index("idx_health_projection_shadow_run_generation_status", "generation_id", "status", "run_sequence"),
        Index("idx_health_projection_shadow_run_generation_completed", "generation_id", "completed_at"),
        Index("uq_health_projection_shadow_run_complete_operation", "complete_operation_id", unique=True, postgresql_where=text("complete_operation_id IS NOT NULL")),
        {"schema":"public"},
    )
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    generation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    projection_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    high_watermark: Mapped[dict] = mapped_column(JSONB, nullable=False)
    high_watermark_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    generation_input_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    source_digest: Mapped[str | None] = mapped_column(CHAR(64)); mapping_digest: Mapped[str | None] = mapped_column(CHAR(64)); projection_digest: Mapped[str | None] = mapped_column(CHAR(64)); coverage_digest: Mapped[str | None] = mapped_column(CHAR(64)); currentness_digest: Mapped[str | None] = mapped_column(CHAR(64)); selection_digest: Mapped[str | None] = mapped_column(CHAR(64)); evidence_digest: Mapped[str | None] = mapped_column(CHAR(64))
    blocker_count: Mapped[int | None] = mapped_column(BigInteger); review_required_count: Mapped[int | None] = mapped_column(BigInteger); informational_count: Mapped[int | None] = mapped_column(BigInteger); category_counts: Mapped[dict | None] = mapped_column(JSONB)
    source_count: Mapped[int | None] = mapped_column(BigInteger); status_event_count: Mapped[int | None] = mapped_column(BigInteger); current_fact_count: Mapped[int | None] = mapped_column(BigInteger); projection_fact_count: Mapped[int | None] = mapped_column(BigInteger); expected_selection_count: Mapped[int | None] = mapped_column(BigInteger); actual_selection_count: Mapped[int | None] = mapped_column(BigInteger); fact_coverage_numerator: Mapped[int | None] = mapped_column(BigInteger); fact_coverage_denominator: Mapped[int | None] = mapped_column(BigInteger); selection_coverage_numerator: Mapped[int | None] = mapped_column(BigInteger); selection_coverage_denominator: Mapped[int | None] = mapped_column(BigInteger)
    start_operation_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False); complete_operation_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False)); validator_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False); lease_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0")); lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()")); completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


class HealthProjectionShadowAudit(Base):
    __tablename__="health_projection_shadow_audit"
    __table_args__=(
        ForeignKeyConstraint(("run_id",), ("public.health_projection_shadow_run.run_id",), name="fk_health_projection_shadow_audit_run", ondelete="RESTRICT"),
        ForeignKeyConstraint(("generation_id",), ("public.health_projection_generation.id",), name="fk_health_projection_shadow_audit_generation", ondelete="RESTRICT"),
        UniqueConstraint("operation_id",name="uq_health_projection_shadow_audit_operation"),
        CheckConstraint("action IN ('SHADOW_START','SHADOW_HEARTBEAT','SHADOW_TAKEOVER','SHADOW_COMPLETE','SHADOW_FAIL','GENERATION_READY')", name="action"),
        Index("idx_health_projection_shadow_audit_generation_created", "generation_id", "created_at"),
        {"schema":"public"},
    )
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True); run_id: Mapped[str]=mapped_column(UUID(as_uuid=False),nullable=False); generation_id: Mapped[int]=mapped_column(BigInteger,nullable=False); operation_id: Mapped[str]=mapped_column(UUID(as_uuid=False),nullable=False); action: Mapped[str]=mapped_column(String(32),nullable=False); preimage_digest: Mapped[str]=mapped_column(CHAR(64),nullable=False); postimage_digest: Mapped[str]=mapped_column(CHAR(64),nullable=False); evidence_digest: Mapped[str|None]=mapped_column(CHAR(64)); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False,server_default=text("now()"))


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
        CheckConstraint("(subject_member_id IS NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR (subject_member_id IS NOT NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','hba1c','weight','height','waist'))", name="indicator_v1_v2"),
        CheckConstraint("((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR (indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR (indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR (indicator_code IN ('height','waist') AND unit='cm') OR (indicator_code='bmi' AND unit='kg/m2') OR (indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))", name="unit_v1_v2"),
        CheckConstraint("numeric_value::text NOT IN ('NaN','Infinity','-Infinity')", name="numeric"),
        CheckConstraint("(subject_member_id IS NULL AND source_type IN ('DEVICE','STORE','REPORT','APP')) OR (subject_member_id IS NOT NULL AND source_type IN ('STORE','REPORT','APP'))", name="source"),
        CheckConstraint("business_day=(measured_at AT TIME ZONE 'Asia/Shanghai')::date AND window_start_utc=(business_day::timestamp AT TIME ZONE 'Asia/Shanghai') AND window_end_utc=((business_day+1)::timestamp AT TIME ZONE 'Asia/Shanghai') AND measured_at>=window_start_utc AND measured_at<window_end_utc", name="window"),
        CheckConstraint("row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND fact_id>=1 AND (subject_user_id IS NULL OR subject_user_id>=1)", name="digest"),
        Index("idx_health_projection_fact_subject_indicator_time", "generation_id", "subject_user_id", "indicator_code", "measured_at", "fact_id"),
        Index("idx_health_projection_fact_window", "generation_id", "subject_user_id", "indicator_code", "business_day"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fact_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    subject_member_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), primary_key=True, nullable=True)
    fact_ref: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    status_event_seq: Mapped[int | None] = mapped_column(BigInteger)
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
        CheckConstraint("((subject_member_id IS NULL AND subject_user_id IS NOT NULL AND winner_fact_ref IS NULL AND rule_version='health-daily-selection-v1') OR (subject_member_id IS NOT NULL AND winner_fact_ref IS NOT NULL AND rule_version='health-daily-selection-v2'))", name="rule_v1_v2"),
        CheckConstraint("selection_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND winner_fact_id>=1 AND (subject_user_id IS NULL OR subject_user_id>=1)", name="digest"),
        Index("idx_health_projection_selection_subject_day", "generation_id", "subject_user_id", "business_day", "indicator_code"),
        {"schema": "public"},
    )
    generation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    subject_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    subject_member_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )
    winner_fact_ref: Mapped[str | None] = mapped_column(UUID(as_uuid=False))
    indicator_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_day: Mapped[date] = mapped_column(Date, primary_key=True)
    winner_fact_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, nullable=False
    )
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
