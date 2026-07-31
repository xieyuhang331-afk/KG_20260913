import pytest


@pytest.fixture(scope="module")
def mapping_context():
    from app.core.database import Base, is_sqlalchemy_available

    if not is_sqlalchemy_available():
        pytest.skip("SQLAlchemy dependency is not installed in this Python environment")

    from app.core.model_specs import get_core_table_specs
    from app.core.sqlalchemy_mapping import map_core_model_classes
    from app.modules.models import get_core_model_classes, import_core_models

    import_core_models()
    map_core_model_classes()

    return {
        "metadata": Base.metadata,
        "specs": get_core_table_specs(),
        "models": get_core_model_classes(),
    }


def test_health_indicator_tablespec_matches_timescale_contract(mapping_context):
    spec = mapping_context["specs"]["health_indicator"]

    assert spec.hypertable is True
    assert spec.hypertable_time_column == "recorded_at"

    assert spec.column("id").ddl_type == "BIGSERIAL"
    assert spec.column("id").primary_key is True
    assert spec.column("id").nullable is False

    assert spec.column("recorded_at").ddl_type == "TIMESTAMPTZ"
    assert spec.column("recorded_at").primary_key is True
    assert spec.column("recorded_at").nullable is False

    assert spec.column("user_id").foreign_key == "user.id"
    assert spec.column("plan_id").foreign_key is None


def test_health_indicator_metadata_has_composite_primary_key_and_user_fk(mapping_context):
    table = mapping_context["metadata"].tables["health_indicator"]

    assert {column.name for column in table.primary_key.columns} == {"id", "recorded_at"}
    assert table.c.id.primary_key is True
    assert table.c.recorded_at.primary_key is True
    assert table.c.id.nullable is False
    assert table.c.recorded_at.nullable is False

    user_id_foreign_keys = list(table.c.user_id.foreign_keys)
    assert len(user_id_foreign_keys) == 1
    assert user_id_foreign_keys[0].column.table.name == "user"
    assert user_id_foreign_keys[0].column.name == "id"


def test_health_indicator_bigserial_primary_key_uses_autoincrement(mapping_context):
    table = mapping_context["metadata"].tables["health_indicator"]

    assert table.c.id.autoincrement is True
    assert table.c.recorded_at.autoincrement in (False, "auto")


def test_health_indicator_model_uses_metadata_primary_key(mapping_context):
    from sqlalchemy import inspect

    from app.modules.user_health.models import HealthIndicator

    primary_key_names = [column.name for column in inspect(HealthIndicator).primary_key]

    assert primary_key_names == ["id", "recorded_at"]
