"""Add P3 Canonical Health Fact Foundation V1.

Revision ID: 20260811_0015
Revises: 20260810_0014
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260811_0015"
down_revision = "20260810_0014"
branch_labels = None
depends_on = None


_INDICATORS = (
    "'systolic_bp','diastolic_bp','heart_rate','fasting_glucose',"
    "'postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score'"
)


def upgrade() -> None:
    op.create_table(
        "canonical_health_fact",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subject_user_id", sa.BigInteger(), nullable=False),
        sa.Column("indicator_code", sa.String(64), nullable=False),
        sa.Column("catalog_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("value_kind", sa.String(16), server_default=sa.text("'NUMERIC'"), nullable=False),
        sa.Column("numeric_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_identity_digest", sa.CHAR(64), nullable=False),
        sa.Column("producer_event_key", sa.String(128), nullable=False),
        sa.Column("payload_digest", sa.CHAR(64), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        sa.Column("supersedes_fact_id", sa.BigInteger()),
        sa.Column("correction_reason_code", sa.String(64)),
        sa.Column("created_by", sa.BigInteger()),
        sa.CheckConstraint("catalog_version = 1", name="ck_canonical_health_fact_catalog_v1"),
        sa.CheckConstraint("value_kind = 'NUMERIC'", name="ck_canonical_health_fact_value_kind_v1_numeric"),
        sa.CheckConstraint(f"indicator_code IN ({_INDICATORS})", name="ck_canonical_health_fact_indicator_v1"),
        sa.CheckConstraint(
            "((indicator_code IN ('systolic_bp','diastolic_bp') AND unit = 'mmHg') OR "
            "(indicator_code = 'heart_rate' AND unit = 'bpm') OR "
            "(indicator_code IN ('fasting_glucose','postprandial_glucose_2h',"
            "'total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit = 'mmol/L') OR "
            "(indicator_code IN ('hba1c','spo2') AND unit = '%') OR "
            "(indicator_code = 'weight' AND unit = 'kg') OR "
            "(indicator_code = 'bmi' AND unit = 'kg/m2') OR "
            "(indicator_code = 'uric_acid' AND unit = 'umol/L') OR "
            "(indicator_code = 'bone_density_t_score' AND unit = 'T-score'))",
            name="ck_canonical_health_fact_unit_v1",
        ),
        sa.CheckConstraint("source_type IN ('APP','STORE','DEVICE','REPORT')", name="ck_canonical_health_fact_source_type"),
        sa.CheckConstraint("source_identity_digest ~ '^[0-9a-f]{64}$'", name="ck_canonical_health_fact_source_digest"),
        sa.CheckConstraint("payload_digest ~ '^[0-9a-f]{64}$'", name="ck_canonical_health_fact_payload_digest"),
        sa.CheckConstraint(
            "(supersedes_fact_id IS NULL AND correction_reason_code IS NULL) OR "
            "(supersedes_fact_id IS NOT NULL AND correction_reason_code IS NOT NULL)",
            name="ck_canonical_health_fact_correction_complete",
        ),
        sa.ForeignKeyConstraint(("subject_user_id",), ("public.user.id",), name="fk_canonical_health_fact_subject_user_id_user"),
        sa.ForeignKeyConstraint(("supersedes_fact_id",), ("public.canonical_health_fact.id",), name="fk_canonical_health_fact_supersedes_fact_id"),
        sa.ForeignKeyConstraint(("created_by",), ("public.user.id",), name="fk_canonical_health_fact_created_by_user"),
        sa.PrimaryKeyConstraint("id", name="pk_canonical_health_fact"),
        sa.UniqueConstraint(
            "source_type", "source_identity_digest", "producer_event_key",
            name="uq_canonical_health_fact_source_event",
        ),
        sa.UniqueConstraint("supersedes_fact_id", name="uq_canonical_health_fact_single_successor"),
        schema="public",
    )
    op.create_index(
        "idx_canonical_health_fact_subject_indicator_time",
        "canonical_health_fact",
        ("subject_user_id", "indicator_code", sa.text("measured_at DESC"), sa.text("id DESC")),
        schema="public",
    )
    op.create_index(
        "idx_canonical_health_fact_source_time",
        "canonical_health_fact",
        ("source_type", sa.text("received_at DESC")),
        schema="public",
    )
    op.execute("REVOKE ALL ON TABLE public.canonical_health_fact FROM PUBLIC")
    op.execute("REVOKE ALL ON SEQUENCE public.canonical_health_fact_id_seq FROM PUBLIC")


def _assert_downgrade_safe(connection) -> None:
    connection.execute(sa.text("LOCK TABLE public.canonical_health_fact IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE public.operation_log IN SHARE ROW EXCLUSIVE MODE"))
    exists = connection.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM public.canonical_health_fact "
            "UNION ALL SELECT 1 FROM public.operation_log "
            "WHERE module='health_fact' AND object_type='canonical_health_fact'"
            ")"
        )
    ).scalar_one()
    if exists:
        raise RuntimeError("refusing to downgrade: Canonical Health Fact facts exist")


def downgrade() -> None:
    _assert_downgrade_safe(op.get_bind())
    op.drop_index("idx_canonical_health_fact_source_time", table_name="canonical_health_fact", schema="public")
    op.drop_index("idx_canonical_health_fact_subject_indicator_time", table_name="canonical_health_fact", schema="public")
    op.drop_table("canonical_health_fact", schema="public")
