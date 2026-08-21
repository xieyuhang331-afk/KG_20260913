import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url


pytestmark = pytest.mark.integration


def test_health_indicator_migration_downgrade_base_then_upgrade_head_lifecycle(pg_database):
    database_url = _get_test_database_url()
    config = _build_alembic_config(database_url)

    command.downgrade(config, "base")
    assert pg_database.fetch_value("SELECT to_regclass('public.health_indicator')") is None

    command.upgrade(config, "head")

    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260821_0023"
    assert pg_database.fetch_value("SELECT to_regclass('public.health_indicator')") == "health_indicator"
    assert (
        pg_database.fetch_value(
            """
            SELECT hypertable_name
            FROM timescaledb_information.hypertables
            WHERE hypertable_schema = 'public'
              AND hypertable_name = 'health_indicator'
            """
        )
        == "health_indicator"
    )
