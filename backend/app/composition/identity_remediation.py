from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.modules.auth.identity_remediation import (
    InventoryReport,
    build_inventory_report,
)
from app.modules.auth.identity_remediation_repository import (
    IdentityRemediationInventoryRepository,
)

_DATABASE_NAME = re.compile(r"^kg_(?:it|mt)_[a-z0-9][a-z0-9_]{5,63}$")
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9_]{5,63}$")
_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_INVENTORY_TABLES = (
    ("public", "user"),
    ("public", "identity_verification_submission"),
    ("public", "identity_verification_decision"),
    ("identity", "identity_subject_claim_registry"),
    ("identity", "user_member_self_link"),
    ("public", "service_enrollment"),
)


class IdentityInventoryConfigurationError(RuntimeError):
    """Raised without echoing connection details."""


def _validated_database_url() -> str:
    environment = os.getenv("KG_TEST_ENVIRONMENT", "")
    if environment not in {"ci_ephemeral", "local_ephemeral"}:
        raise IdentityInventoryConfigurationError("A2_INVENTORY_EPHEMERAL_ENV_REQUIRED")
    run_id = os.getenv("KG_TEST_RUN_ID", "")
    if not _RUN_ID.fullmatch(run_id):
        raise IdentityInventoryConfigurationError("A2_INVENTORY_RUN_ID_REQUIRED")
    database_url = os.getenv("KG_A2_IDENTITY_INVENTORY_DATABASE_URL", "")
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise IdentityInventoryConfigurationError("A2_INVENTORY_DATABASE_INVALID")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.port is None:
        raise IdentityInventoryConfigurationError("A2_INVENTORY_DATABASE_NOT_LOCAL")
    database_name = parsed.path.removeprefix("/")
    if not _DATABASE_NAME.fullmatch(database_name) or not database_name.endswith(run_id):
        raise IdentityInventoryConfigurationError("A2_INVENTORY_DATABASE_SCOPE_INVALID")
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


def _validated_database_role() -> str:
    role = os.getenv("KG_A2_IDENTITY_INVENTORY_ROLE", "")
    if not _ROLE_NAME.fullmatch(role):
        raise IdentityInventoryConfigurationError("A2_INVENTORY_DATABASE_ROLE_REQUIRED")
    return role


async def _engine() -> AsyncEngine:
    return create_async_engine(_validated_database_url(), pool_pre_ping=True)


async def _assert_disposable_database(connection) -> None:
    run_id = os.environ["KG_TEST_RUN_ID"]
    description = await connection.scalar(
        text(
            "SELECT shobj_description(oid, 'pg_database') "
            "FROM pg_database WHERE datname = current_database()"
        )
    )
    if description != f"kg-test-disposable:{run_id}":
        raise IdentityInventoryConfigurationError("A2_INVENTORY_SENTINEL_MISMATCH")


async def _begin_read_only(connection) -> None:
    await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


async def _assert_read_only_authority(connection) -> None:
    expected_role = _validated_database_role()
    current_role = str(await connection.scalar(text("SELECT current_user")))
    if current_role != expected_role:
        raise IdentityInventoryConfigurationError("A2_INVENTORY_DATABASE_ROLE_MISMATCH")
    for schema_name, table_name in _INVENTORY_TABLES:
        has_base_table_authority = await connection.scalar(
            text(
                "SELECT COALESCE(bool_or("
                "has_table_privilege(current_user, relation.oid, 'SELECT') "
                "OR has_table_privilege(current_user, relation.oid, 'INSERT') "
                "OR has_table_privilege(current_user, relation.oid, 'UPDATE') "
                "OR has_table_privilege(current_user, relation.oid, 'DELETE') "
                "OR has_table_privilege(current_user, relation.oid, 'TRUNCATE')"
                "), FALSE) "
                "FROM pg_catalog.pg_class relation "
                "JOIN pg_catalog.pg_namespace namespace "
                "ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = :schema_name "
                "AND relation.relname = :table_name"
            ),
            {"schema_name": schema_name, "table_name": table_name},
        )
        if has_base_table_authority:
            raise IdentityInventoryConfigurationError(
                "A2_INVENTORY_BASE_TABLE_AUTHORITY_FORBIDDEN"
            )
    for schema_name in ("public", "identity"):
        if await connection.scalar(
            text("SELECT has_schema_privilege(current_user, :schema_name, 'CREATE')"),
            {"schema_name": schema_name},
        ):
            raise IdentityInventoryConfigurationError("A2_INVENTORY_DDL_AUTHORITY_FORBIDDEN")
    if not await connection.scalar(
        text(
            "SELECT has_function_privilege("
            "current_user, "
            "'identity.a2_identity_inventory_snapshot_v1()', 'EXECUTE')"
        )
    ):
        raise IdentityInventoryConfigurationError("A2_INVENTORY_EXECUTE_REQUIRED")


async def run_identity_inventory() -> InventoryReport:
    engine = await _engine()
    try:
        async with engine.connect() as connection, connection.begin():
            await _begin_read_only(connection)
            await _assert_disposable_database(connection)
            await _assert_read_only_authority(connection)
            facts = await IdentityRemediationInventoryRepository().fetch_snapshot(connection)
            return build_inventory_report(facts)
    finally:
        await engine.dispose()


async def inspect_read_only_transaction() -> dict[str, str]:
    engine = await _engine()
    try:
        async with engine.connect() as connection, connection.begin():
            await _begin_read_only(connection)
            await _assert_disposable_database(connection)
            await _assert_read_only_authority(connection)
            return {
                "transaction_read_only": str(
                    await connection.scalar(text("SHOW transaction_read_only"))
                ),
                "transaction_isolation": str(
                    await connection.scalar(text("SHOW transaction_isolation"))
                ),
                "database_role": "inventory_reader_verified",
            }
    finally:
        await engine.dispose()
