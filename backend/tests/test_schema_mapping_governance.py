import re

import pytest


GOVERNED_TABLES = {
    "platform_org",
    "tenant",
    "user",
    "health_profile",
    "health_indicator",
    "tenant_attachment",
    "tenant_review_log",
    "operation_log",
}

EXPECTED_ENUMS = {
    ("tenant", "status"): {
        "name": "tenant_status",
        "values": ("pending", "approved", "rejected", "active", "closed"),
    },
    ("user", "role"): {
        "name": "user_role",
        "values": (
            "super_admin",
            "province_admin",
            "city_admin",
            "sys_admin",
            "expert",
            "org_admin",
            "org_operator",
            "therapist",
            "host",
            "member",
        ),
    },
    ("user", "status"): {
        "name": "user_status",
        "values": ("active", "disabled", "suspended"),
    },
}


@pytest.fixture(scope="module")
def mapping_context():
    from app.core.database import Base, is_sqlalchemy_available

    if not is_sqlalchemy_available():
        pytest.skip("SQLAlchemy dependency is not installed in this Python environment")

    from app.core.model_specs import get_core_table_specs
    from app.core.sqlalchemy_mapping import map_core_model_classes
    from app.modules.models import get_core_model_classes

    map_core_model_classes()

    return {
        "metadata": Base.metadata,
        "specs": get_core_table_specs(),
        "models": get_core_model_classes(),
    }


def _governed_specs(mapping_context):
    specs = mapping_context["specs"]
    return {table_name: specs[table_name] for table_name in GOVERNED_TABLES}


def test_fk_targets_are_registered_and_resolve_in_metadata(mapping_context):
    metadata = mapping_context["metadata"]
    specs = mapping_context["specs"]
    models = mapping_context["models"]

    for source_table, spec in _governed_specs(mapping_context).items():
        table = metadata.tables[source_table]
        for column_spec in spec.columns:
            if column_spec.foreign_key is None:
                continue

            target_table, target_column = column_spec.foreign_key.split(".", 1)

            assert target_table in specs
            assert target_table in models
            assert target_table in metadata.tables

            column = table.c[column_spec.name]
            foreign_keys = list(column.foreign_keys)
            assert foreign_keys, f"{source_table}.{column_spec.name} missing ForeignKey"
            assert any(
                foreign_key.column.table.name == target_table
                and foreign_key.column.name == target_column
                for foreign_key in foreign_keys
            )


def test_postgresql_enum_columns_are_governed(mapping_context):
    from sqlalchemy.dialects import postgresql

    metadata = mapping_context["metadata"]

    for (table_name, column_name), expected in EXPECTED_ENUMS.items():
        column_type = metadata.tables[table_name].c[column_name].type

        assert isinstance(column_type, postgresql.ENUM)
        assert column_type.name == expected["name"]
        assert tuple(column_type.enums) == expected["values"]
        assert column_type.create_type is False


def test_governed_core_tables_are_registered_in_specs_models_and_metadata(mapping_context):
    metadata = mapping_context["metadata"]
    specs = mapping_context["specs"]
    models = mapping_context["models"]

    for table_name in GOVERNED_TABLES:
        assert table_name in specs
        assert table_name in models
        assert table_name in metadata.tables


def test_tablespec_columns_and_constraints_are_preserved(mapping_context):
    metadata = mapping_context["metadata"]

    for table_name, spec in _governed_specs(mapping_context).items():
        table = metadata.tables[table_name]
        for column_spec in spec.columns:
            assert column_spec.name in table.c

            column = table.c[column_spec.name]
            assert column.nullable == column_spec.nullable
            assert column.primary_key == column_spec.primary_key
            assert column.unique == column_spec.unique
            assert column.info["ddl_type"] == column_spec.ddl_type

            if column_spec.foreign_key is not None:
                assert column.foreign_keys


def test_tablespec_indexes_are_preserved(mapping_context):
    metadata = mapping_context["metadata"]

    for table_name, spec in _governed_specs(mapping_context).items():
        table_indexes = {index.name for index in metadata.tables[table_name].indexes}
        expected_indexes = {index_name for index_name, _ in spec.indexes}

        assert expected_indexes <= table_indexes


def test_high_risk_column_types_are_preserved(mapping_context):
    import sqlalchemy as sa
    from sqlalchemy.dialects import postgresql

    metadata = mapping_context["metadata"]

    for table_name, spec in _governed_specs(mapping_context).items():
        table = metadata.tables[table_name]
        for column_spec in spec.columns:
            column_type = table.c[column_spec.name].type
            ddl_type = column_spec.ddl_type.upper()

            if ddl_type in {"TENANT_STATUS", "USER_ROLE", "USER_STATUS"}:
                assert isinstance(column_type, postgresql.ENUM)
                continue

            if ddl_type == "JSONB":
                assert isinstance(column_type, postgresql.JSONB)
                continue

            if ddl_type in {"BIGINT", "BIGSERIAL"}:
                assert isinstance(column_type, sa.BigInteger)
                continue

            if ddl_type == "TIMESTAMPTZ":
                assert isinstance(column_type, sa.DateTime)
                assert column_type.timezone is True
                continue

            varchar_match = re.fullmatch(r"VARCHAR\((\d+)\)", ddl_type)
            if varchar_match:
                assert isinstance(column_type, sa.String)
                assert column_type.length == int(varchar_match.group(1))
