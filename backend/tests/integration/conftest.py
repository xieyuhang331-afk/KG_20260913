import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest
from alembic import command
from alembic.config import Config


BACKEND_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"
PRODUCTION_DATABASE_NAMES = {"postgres", "template0", "template1", "kg", "kg_prod", "kg_production"}
REQUIRED_HEAD_REVISION = "20260728_0006"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: tests requiring external PostgreSQL")


def pytest_collection_modifyitems(config, items):
    if os.getenv("KG_RUN_PG_INTEGRATION") == "1":
        return
    skip_pg = pytest.mark.skip(reason="set KG_RUN_PG_INTEGRATION=1 to run PostgreSQL integration tests")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_pg)


def _get_test_database_url() -> str:
    database_url = os.getenv("KG_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("KG_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    _assert_safe_test_database_url(database_url)
    return database_url


def _assert_safe_test_database_url(database_url: str) -> None:
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2", "postgresql+asyncpg"}:
        raise RuntimeError("KG_TEST_DATABASE_URL must use a PostgreSQL driver")

    database_name = parsed.path.lstrip("/")
    if not database_name:
        raise RuntimeError("KG_TEST_DATABASE_URL must include a database name")
    if database_name in PRODUCTION_DATABASE_NAMES or "test" not in database_name.lower():
        raise RuntimeError("KG_TEST_DATABASE_URL must point to a dedicated test database")


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


@pytest.fixture(scope="session")
def pg_database():
    database_url = _get_test_database_url()
    database = PgDatabase(database_url)

    database.execute("DROP SCHEMA IF EXISTS public CASCADE")
    database.execute("CREATE SCHEMA public")
    command.upgrade(_build_alembic_config(database_url), "head")

    current_revision = database.fetch_value("SELECT version_num FROM alembic_version")
    if current_revision != REQUIRED_HEAD_REVISION:
        raise RuntimeError(f"expected Alembic head {REQUIRED_HEAD_REVISION}, got {current_revision}")

    yield database


@pytest.fixture
def real_db_client(pg_database):
    database_url = _get_test_database_url()

    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_db_session
    from app.main import create_app

    engine = create_async_engine(database_url, pool_pre_ping=True)
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
