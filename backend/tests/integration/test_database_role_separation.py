import asyncio
import os

import asyncpg
import pytest

from tests.integration.conftest import (
    _get_application_database_url,
    _get_readonly_database_url,
    _to_asyncpg_dsn,
)


pytestmark = pytest.mark.integration


async def _assert_restricted_role(database_url: str, expected_role: str, *, readonly: bool) -> None:
    connection = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        assert await connection.fetchval("SELECT current_user") == expected_role
        attributes = await connection.fetchrow(
            "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user"
        )
        assert dict(attributes) == {
            "rolsuper": False,
            "rolcreatedb": False,
            "rolcreaterole": False,
        }
        assert await connection.fetchval(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE')"
        ) is False
        assert await connection.fetchval('SELECT COUNT(*) FROM "user"') >= 0

        transaction = connection.transaction()
        await transaction.start()
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute("CREATE TABLE public.role_separation_forbidden (id integer)")
        finally:
            await transaction.rollback()

        transaction = connection.transaction()
        await transaction.start()
        try:
            statement = (
                'INSERT INTO "user" (id, phone, password_hash, role) '
                "VALUES (-900001, 'role-separation-denied', 'denied', 'member')"
                if readonly
                else "UPDATE alembic_version SET version_num = version_num"
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await connection.execute(statement)
        finally:
            await transaction.rollback()
    finally:
        await connection.close()


def test_application_and_readonly_roles_are_database_restricted(pg_database):
    if os.getenv("KG_TEST_ROLE_SEPARATION") != "1":
        pytest.skip("set KG_TEST_ROLE_SEPARATION=1 to validate database role separation")

    assert pg_database.fetch_value("SELECT current_user") == os.environ["KG_TEST_MIGRATION_ROLE"]
    asyncio.run(
        _assert_restricted_role(
            _get_application_database_url(),
            os.environ["KG_TEST_APPLICATION_ROLE"],
            readonly=False,
        )
    )
    asyncio.run(
        _assert_restricted_role(
            _get_readonly_database_url(),
            os.environ["KG_TEST_READONLY_ROLE"],
            readonly=True,
        )
    )
