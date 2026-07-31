from __future__ import annotations

from app.core.model_specs import ColumnSpec, TableSpec, register_core_table_specs


HEALTH_PROFILE_TABLE = TableSpec(
    name="health_profile",
    module="user_health",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("user_id", "BIGINT", nullable=False, unique=True, foreign_key="user.id"),
        ColumnSpec("gender", "VARCHAR(5)", nullable=False),
        ColumnSpec("birth_date", "DATE", nullable=False),
        ColumnSpec("height", "DECIMAL(5,1)"),
        ColumnSpec("weight", "DECIMAL(5,1)"),
        ColumnSpec("blood_type", "VARCHAR(5)"),
        ColumnSpec("medical_history", "JSONB"),
        ColumnSpec("allergy_history", "JSONB"),
        ColumnSpec("family_history", "JSONB"),
        ColumnSpec("smoking", "VARCHAR(10)"),
        ColumnSpec("drinking", "VARCHAR(10)"),
        ColumnSpec("symptoms", "JSONB"),
        ColumnSpec("sleep_quality", "VARCHAR(50)"),
        ColumnSpec("bowel_urination", "VARCHAR(100)"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
        ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
)

HEALTH_INDICATOR_TABLE = TableSpec(
    name="health_indicator",
    module="user_health",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("user_id", "BIGINT", nullable=False, foreign_key="user.id"),
        ColumnSpec("plan_id", "BIGINT"),
        ColumnSpec("batch_id", "VARCHAR(36)"),
        ColumnSpec("indicator_type", "VARCHAR(30)", nullable=False),
        ColumnSpec("value", "DECIMAL(10,2)", nullable=False),
        ColumnSpec("unit", "VARCHAR(10)", nullable=False),
        ColumnSpec("source", "VARCHAR(20)", nullable=False, default="APP"),
        ColumnSpec("recorded_at", "TIMESTAMPTZ", nullable=False, primary_key=True),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_hi_user_time", ("user_id", "recorded_at DESC")),
        ("idx_hi_type_time", ("indicator_type", "recorded_at DESC")),
    ),
    hypertable=True,
    hypertable_time_column="recorded_at",
)


class HealthProfile:
    __tablename__ = HEALTH_PROFILE_TABLE.name
    __table_spec__ = HEALTH_PROFILE_TABLE


class HealthIndicator:
    __tablename__ = HEALTH_INDICATOR_TABLE.name
    __table_spec__ = HEALTH_INDICATOR_TABLE


register_core_table_specs(HEALTH_PROFILE_TABLE, HEALTH_INDICATOR_TABLE)
