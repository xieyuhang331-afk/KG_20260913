import pytest


pytestmark = pytest.mark.integration


def test_alembic_upgrade_head_creates_expected_database_baseline(pg_database):
    revision = pg_database.fetch_value("SELECT version_num FROM alembic_version")
    enum_names = set(
        pg_database.fetch_column(
            """
            SELECT typname
            FROM pg_type
            WHERE typname IN ('user_role', 'tenant_status', 'user_status')
            """
        )
    )
    table_names = set(
        pg_database.fetch_column(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('platform_org', 'tenant', 'user', 'health_profile', 'health_indicator')
            """
        )
    )

    assert revision == "20260810_0014"
    assert enum_names == {"user_role", "tenant_status", "user_status"}
    assert table_names == {"platform_org", "tenant", "user", "health_profile", "health_indicator"}
