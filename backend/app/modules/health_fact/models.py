from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CanonicalHealthFactOrmModel(Base):
    __tablename__ = "canonical_health_fact"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_identity_digest",
            "producer_event_key",
            name="uq_canonical_health_fact_source_event",
        ),
        UniqueConstraint(
            "supersedes_fact_id",
            name="uq_canonical_health_fact_single_successor",
        ),
        CheckConstraint("catalog_version = 1", name="catalog_v1"),
        CheckConstraint("value_kind = 'NUMERIC'", name="value_kind_v1_numeric"),
        CheckConstraint(
            "indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
            "'fasting_glucose','postprandial_glucose_2h','hba1c',"
            "'total_cholesterol','triglyceride','hdl_c','ldl_c','weight',"
            "'bmi','uric_acid','spo2','bone_density_t_score')",
            name="indicator_v1",
        ),
        CheckConstraint(
            "((indicator_code IN ('systolic_bp','diastolic_bp') AND unit = 'mmHg') OR "
            "(indicator_code = 'heart_rate' AND unit = 'bpm') OR "
            "(indicator_code IN ('fasting_glucose','postprandial_glucose_2h',"
            "'total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit = 'mmol/L') OR "
            "(indicator_code IN ('hba1c','spo2') AND unit = '%') OR "
            "(indicator_code = 'weight' AND unit = 'kg') OR "
            "(indicator_code = 'bmi' AND unit = 'kg/m2') OR "
            "(indicator_code = 'uric_acid' AND unit = 'umol/L') OR "
            "(indicator_code = 'bone_density_t_score' AND unit = 'T-score'))",
            name="unit_v1",
        ),
        CheckConstraint(
            "source_type IN ('APP','STORE','DEVICE','REPORT')",
            name="source_type",
        ),
        CheckConstraint(
            "source_identity_digest ~ '^[0-9a-f]{64}$'",
            name="source_digest",
        ),
        CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name="payload_digest",
        ),
        CheckConstraint(
            "(supersedes_fact_id IS NULL AND correction_reason_code IS NULL) OR "
            "(supersedes_fact_id IS NOT NULL AND correction_reason_code IS NOT NULL)",
            name="correction_complete",
        ),
        Index(
            "idx_canonical_health_fact_subject_indicator_time",
            "subject_user_id",
            "indicator_code",
            text("measured_at DESC"),
            text("id DESC"),
        ),
        Index(
            "idx_canonical_health_fact_source_time",
            "source_type",
            text("received_at DESC"),
        ),
        {"schema": "public"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user.id"), nullable=False
    )
    indicator_code: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    value_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'NUMERIC'")
    )
    numeric_value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_identity_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    producer_event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_digest: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    digest_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_fact_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("public.canonical_health_fact.id")
    )
    correction_reason_code: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("user.id"))
