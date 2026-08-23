from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.model_specs import ColumnSpec, TableSpec
from app.core.sqlalchemy_mapping import build_sqlalchemy_table
from app.modules.user_health.models import HEALTH_PROFILE_TABLE


def test_UUID_TableSpec映射为PostgreSQL原生UUID对象语义() -> None:
    spec = TableSpec(
        name="slice4_uuid_mapping_contract",
        module="slice4_contract",
        columns=(
            ColumnSpec("public_id", "UUID", nullable=False, primary_key=True),
        ),
    )

    table = build_sqlalchemy_table(spec, sa.MetaData())

    assert isinstance(table.c.public_id.type, postgresql.UUID)
    assert table.c.public_id.type.as_uuid is True
    assert table.c.public_id.info == {"ddl_type": "UUID"}


def test_HealthProfile的V2根UUID列不回退为VARCHAR() -> None:
    table = build_sqlalchemy_table(HEALTH_PROFILE_TABLE, sa.MetaData())

    for column_name in (
        "profile_public_id",
        "subject_member_id",
        "current_revision_id",
    ):
        column_type = table.c[column_name].type
        assert isinstance(column_type, postgresql.UUID)
        assert column_type.as_uuid is True

    assert isinstance(table.c.id.type, sa.BigInteger)
    assert isinstance(table.c.user_id.type, sa.BigInteger)
    assert isinstance(table.c.gender.type, sa.String)
    assert isinstance(table.c.birth_date.type, sa.Date)
    assert isinstance(table.c.created_at.type, sa.DateTime)
    assert table.c.created_at.type.timezone is True
    assert isinstance(table.c.medical_history.type, postgresql.JSONB)


def test_共享解析器的既有类型与未知类型处理保持不变() -> None:
    spec = TableSpec(
        name="slice4_existing_mapping_contract",
        module="slice4_contract",
        columns=(
            ColumnSpec("text_value", "VARCHAR(24)"),
            ColumnSpec("integer_value", "INT"),
            ColumnSpec("flag", "BOOLEAN"),
            ColumnSpec("captured_at", "TIMESTAMPTZ"),
            ColumnSpec("payload", "JSONB"),
            ColumnSpec("legacy_unknown", "LEGACY_UNKNOWN"),
        ),
    )

    table = build_sqlalchemy_table(spec, sa.MetaData())

    assert isinstance(table.c.text_value.type, sa.String)
    assert table.c.text_value.type.length == 24
    assert isinstance(table.c.integer_value.type, sa.Integer)
    assert isinstance(table.c.flag.type, sa.Boolean)
    assert isinstance(table.c.captured_at.type, sa.DateTime)
    assert table.c.captured_at.type.timezone is True
    assert isinstance(table.c.payload.type, postgresql.JSONB)
    assert isinstance(table.c.legacy_unknown.type, sa.String)


def test_v2窗口选择ORM使用非空Winner作为稳定Identity() -> None:
    from app.modules.health_projection.models import (
        HealthProjectionWindowSelectionModel,
    )

    primary_key_columns = {
        column.name
        for column in HealthProjectionWindowSelectionModel.__mapper__.primary_key
    }
    assert primary_key_columns == {
        "generation_id", "winner_fact_id", "indicator_code", "business_day"
    }
    assert "subject_user_id" not in primary_key_columns
    assert "subject_member_id" not in primary_key_columns
    assert HealthProjectionWindowSelectionModel.__table__.c.subject_user_id.nullable
    assert HealthProjectionWindowSelectionModel.__table__.c.subject_member_id.nullable
