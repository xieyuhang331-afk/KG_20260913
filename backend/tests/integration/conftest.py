import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.integration.database_safety import (
    DisposableDatabaseTarget,
    validate_database_sentinel,
    validate_test_database_target,
)


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
REQUIRED_HEAD_REVISION = "20260807_0010"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests requiring external PostgreSQL")


def pytest_collection_modifyitems(config, items):
    if os.getenv("KG_RUN_PG_INTEGRATION") == "1":
        return
    skip_pg = pytest.mark.skip(reason="set KG_RUN_PG_INTEGRATION=1 to run PostgreSQL integration tests")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_pg)


def _get_test_database_target() -> tuple[str, DisposableDatabaseTarget]:
    database_url = os.getenv("KG_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("KG_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    target = validate_test_database_target(
        database_url,
        integration_enabled=os.getenv("KG_RUN_PG_INTEGRATION"),
        destructive_enabled=os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE"),
        environment=os.getenv("KG_TEST_ENVIRONMENT"),
        run_id=os.getenv("KG_TEST_RUN_ID"),
    )
    return database_url, target


def _get_test_database_url() -> str:
    database_url, _ = _get_test_database_target()
    if not os.getenv("KG_TEST_MIGRATION_DATABASE_URL"):
        return database_url
    return _get_role_database_url("KG_TEST_MIGRATION_DATABASE_URL")


def _get_application_database_url() -> str:
    database_url, _ = _get_test_database_target()
    return database_url


def _get_readonly_database_url() -> str:
    return _get_role_database_url("KG_TEST_READONLY_DATABASE_URL")


def _get_role_database_url(environment_name: str) -> str:
    primary_url, primary_target = _get_test_database_target()
    database_url = os.getenv(environment_name)
    if not database_url:
        pytest.skip(f"{environment_name} is required for role separation tests")
    target = validate_test_database_target(
        database_url,
        integration_enabled=os.getenv("KG_RUN_PG_INTEGRATION"),
        destructive_enabled=os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE"),
        environment=os.getenv("KG_TEST_ENVIRONMENT"),
        run_id=os.getenv("KG_TEST_RUN_ID"),
    )
    primary = urlparse(primary_url)
    candidate = urlparse(database_url)
    if target != primary_target or (candidate.hostname, candidate.port) != (primary.hostname, primary.port):
        raise RuntimeError(f"{environment_name} must target the same ephemeral database endpoint")
    return database_url


def _build_alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "app" / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _to_asyncpg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


class PgDatabase:
    def __init__(self, database_url: str):
        self.database_url = _to_asyncpg_dsn(database_url)

    async def _fetch_value(self, sql: str):
        connection = await asyncpg.connect(self.database_url)
        try:
            return await connection.fetchval(sql)
        finally:
            await connection.close()

    async def _fetch_column(self, sql: str):
        connection = await asyncpg.connect(self.database_url)
        try:
            return [row[0] for row in await connection.fetch(sql)]
        finally:
            await connection.close()

    async def _fetch_rows(self, sql: str, *args):
        connection = await asyncpg.connect(self.database_url)
        try:
            return [dict(row) for row in await connection.fetch(sql, *args)]
        finally:
            await connection.close()

    async def _execute(self, sql: str) -> None:
        connection = await asyncpg.connect(self.database_url)
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    def fetch_value(self, sql: str):
        return asyncio.run(self._fetch_value(sql))

    def fetch_column(self, sql: str):
        return asyncio.run(self._fetch_column(sql))

    def fetch_rows(self, sql: str, *args):
        return asyncio.run(self._fetch_rows(sql, *args))

    def execute(self, sql: str) -> None:
        asyncio.run(self._execute(sql))


def _validated_role_name(environment_name: str) -> str:
    role_name = os.getenv(environment_name, "")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role_name):
        raise RuntimeError(f"{environment_name} must contain a safe PostgreSQL role name")
    return role_name


def _grant_test_role_permissions(database: PgDatabase) -> None:
    if os.getenv("KG_TEST_ROLE_SEPARATION") != "1":
        return

    application_role = _validated_role_name("KG_TEST_APPLICATION_ROLE")
    readonly_role = _validated_role_name("KG_TEST_READONLY_ROLE")
    database.execute(f'GRANT USAGE ON SCHEMA public TO "{application_role}"')
    database.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{application_role}"')
    database.execute(f'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO "{application_role}"')
    database.execute(f'REVOKE INSERT, UPDATE, DELETE ON TABLE alembic_version FROM "{application_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{application_role}"')
    database.execute(f'GRANT USAGE ON SCHEMA public TO "{readonly_role}"')
    database.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{readonly_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA public FROM "{readonly_role}"')
    database.execute("REVOKE ALL ON SCHEMA identity FROM PUBLIC")
    database.execute(f'GRANT USAGE ON SCHEMA identity TO "{application_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA identity FROM "{application_role}"')
    database.execute(f'GRANT SELECT, INSERT, UPDATE ON TABLE identity.member TO "{application_role}"')
    database.execute(
        f'REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE identity.member FROM "{application_role}"'
    )
    database.execute(
        f'GRANT SELECT, INSERT ON TABLE identity.member_no_allocation TO "{application_role}"'
    )
    database.execute(
        'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE identity.member_no_allocation FROM "{application_role}"'
    )
    database.execute(f'GRANT USAGE ON SCHEMA identity TO "{readonly_role}"')
    database.execute(f'REVOKE CREATE ON SCHEMA identity FROM "{readonly_role}"')
    database.execute(f'GRANT SELECT ON TABLE identity.member TO "{readonly_role}"')
    database.execute(
        f'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE identity.member FROM "{readonly_role}"'
    )
    database.execute(
        f'GRANT SELECT ON TABLE identity.member_no_allocation TO "{readonly_role}"'
    )
    database.execute(
        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
        f'ON TABLE identity.member_no_allocation FROM "{readonly_role}"'
    )
    bootstrap_tables = (
        "user_member_self_link",
        "registration_bootstrap_record",
    )
    for table_name in bootstrap_tables:
        database.execute(
            f'GRANT SELECT, INSERT ON TABLE identity."{table_name}" '
            f'TO "{application_role}"'
        )
        database.execute(
            'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE identity."{table_name}" FROM "{application_role}"'
        )
        database.execute(
            f'GRANT SELECT ON TABLE identity."{table_name}" '
            f'TO "{readonly_role}"'
        )
        database.execute(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE identity."{table_name}" FROM "{readonly_role}"'
        )
    evidence_tables = (
        "identity_verification_decision",
        "user_account_classification_decision",
        "registration_eligibility_decision",
    )
    for table_name in evidence_tables:
        database.execute(
            f'GRANT SELECT, INSERT ON TABLE public."{table_name}" '
            f'TO "{application_role}"'
        )
        database.execute(
            'REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE public."{table_name}" FROM "{application_role}"'
        )
        database.execute(
            f'GRANT SELECT ON TABLE public."{table_name}" '
            f'TO "{readonly_role}"'
        )
        database.execute(
            'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
            f'ON TABLE public."{table_name}" FROM "{readonly_role}"'
        )


@pytest.fixture(scope="module")
def pg_database():
    _, target = _get_test_database_target()
    migration_database_url = _get_test_database_url()
    database = PgDatabase(migration_database_url)

    migration_role = None
    if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
        application_role = _validated_role_name("KG_TEST_APPLICATION_ROLE")
        migration_role = _validated_role_name("KG_TEST_MIGRATION_ROLE")
        readonly_role = _validated_role_name("KG_TEST_READONLY_ROLE")
        roles = (application_role, migration_role, readonly_role)
        if len(set(roles)) != len(roles):
            raise RuntimeError("database validation roles must be distinct")
        if "postgres" in roles:
            raise RuntimeError("database validation roles must not use postgres")

    sentinel = database.fetch_value(
        "SELECT shobj_description(oid, 'pg_database') "
        "FROM pg_database WHERE datname = current_database()"
    )
    validate_database_sentinel(sentinel, target)

    if migration_role is not None:
        connected_role = database.fetch_value("SELECT current_user")
        if connected_role != migration_role:
            raise RuntimeError(
                "migration database URL role does not match KG_TEST_MIGRATION_ROLE"
            )

    database.execute("DROP SCHEMA IF EXISTS identity CASCADE")
    database.execute("DROP SCHEMA IF EXISTS public CASCADE")
    database.execute("CREATE SCHEMA public")
    command.upgrade(_build_alembic_config(migration_database_url), "head")

    current_revision = database.fetch_value("SELECT version_num FROM alembic_version")
    if current_revision != REQUIRED_HEAD_REVISION:
        raise RuntimeError(f"expected Alembic head {REQUIRED_HEAD_REVISION}, got {current_revision}")
    _grant_test_role_permissions(database)

    yield database


@pytest.fixture
def real_db_client(pg_database):
    database_url = _get_application_database_url()

    from fastapi.testclient import TestClient
    from sqlalchemy.pool import NullPool
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_db_session
    from app.main import create_app

    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def override_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
