from __future__ import annotations

from app.core.model_specs import ColumnSpec, TableSpec, register_core_table_specs


HEALTH_PROFILE_TABLE = TableSpec(
    name="health_profile",
    module="user_health",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("profile_public_id", "UUID", unique=True),
        ColumnSpec("subject_member_id", "UUID", unique=True),
        ColumnSpec("current_revision_id", "UUID"),
        ColumnSpec("version", "BIGINT", nullable=False, default="1"),
        ColumnSpec("user_id", "BIGINT", unique=True, foreign_key="user.id"),
        ColumnSpec("gender", "VARCHAR(5)"),
        ColumnSpec("birth_date", "DATE"),
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


def health_profile_truth_v2(
    subject_member_id,
    current_revision_id,
    user_id,
    gender,
    birth_date,
    height,
    weight,
) -> bool:
    """Mirror the exact V1/V2 database truth table without coercion."""
    is_v1 = (
        subject_member_id is None
        and current_revision_id is None
        and user_id is not None
        and gender is not None
        and birth_date is not None
    )
    is_v2 = (
        subject_member_id is not None
        and current_revision_id is not None
        and gender is None
        and birth_date is None
        and height is None
        and weight is None
    )
    return is_v1 != is_v2

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

DETECTION_REPORT_TABLE = TableSpec(
    name="detection_report",
    module="user_health",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("user_id", "BIGINT", nullable=False, foreign_key="user.id"),
        ColumnSpec("store_id", "BIGINT", foreign_key="tenant.id"),
        ColumnSpec("report_type", "VARCHAR(32)", nullable=False),
        ColumnSpec("detection_time", "TIMESTAMPTZ", nullable=False),
        ColumnSpec("view_status", "VARCHAR(16)", nullable=False, default="unread"),
        ColumnSpec("summary", "TEXT"),
        ColumnSpec("report_schema_version", "INT", nullable=False, default="1"),
        ColumnSpec("report_data", "JSONB", nullable=False),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_detection_report_user_time", ("user_id", "detection_time DESC", "id DESC")),
        (
            "idx_detection_report_user_type_time",
            ("user_id", "report_type", "detection_time DESC", "id DESC"),
        ),
    ),
)


class HealthProfile:
    __tablename__ = HEALTH_PROFILE_TABLE.name
    __table_spec__ = HEALTH_PROFILE_TABLE


class HealthIndicator:
    __tablename__ = HEALTH_INDICATOR_TABLE.name
    __table_spec__ = HEALTH_INDICATOR_TABLE


class DetectionReport:
    __tablename__ = DETECTION_REPORT_TABLE.name
    __table_spec__ = DETECTION_REPORT_TABLE


register_core_table_specs(HEALTH_PROFILE_TABLE, HEALTH_INDICATOR_TABLE, DETECTION_REPORT_TABLE)
