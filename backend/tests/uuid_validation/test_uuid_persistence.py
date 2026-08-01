from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import BigInteger, Column, MetaData, Table, Uuid, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

from tests.integration.conftest import _get_test_database_url, _validated_role_name
from tests.integration.database_safety import validate_test_database_target


def test_uuidv7_sqlalchemy_asyncpg_postgresql_round_trip():
    database_url = os.environ["KG_TEST_DATABASE_URL"]
    validate_test_database_target(
        database_url,
        integration_enabled=os.getenv("KG_RUN_PG_INTEGRATION"),
        destructive_enabled=os.getenv("KG_ALLOW_DESTRUCTIVE_TEST_DATABASE"),
        environment=os.getenv("KG_TEST_ENVIRONMENT"),
        run_id=os.getenv("KG_TEST_RUN_ID"),
    )

    async def round_trip() -> None:
        metadata = MetaData()
        validation_table = Table(
            "uuid_library_validation",
            metadata,
            Column("p2_id", Uuid(as_uuid=True), primary_key=True),
            Column("p1_legacy_id", BigInteger, nullable=False),
        )
        migration_database_url = _get_test_database_url()
        migration_engine = create_async_engine(migration_database_url, poolclass=NullPool)
        application_engine = create_async_engine(database_url, poolclass=NullPool)
        generated = uuid7()
        try:
            async with migration_engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
                if os.getenv("KG_TEST_ROLE_SEPARATION") == "1":
                    application_role = _validated_role_name("KG_TEST_APPLICATION_ROLE")
                    await connection.execute(
                        text(
                            'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE uuid_library_validation '
                            f'TO "{application_role}"'
                        )
                    )
            async with application_engine.begin() as connection:
                await connection.execute(insert(validation_table).values(p2_id=generated, p1_legacy_id=9_201))
            async with application_engine.connect() as connection:
                stored = (await connection.execute(select(validation_table))).one()

            assert isinstance(stored.p2_id, uuid.UUID)
            assert stored.p2_id == generated
            assert stored.p2_id.version == 7
            assert stored.p1_legacy_id == 9_201
            normalized = uuid.UUID(int=stored.p2_id.int)
            assert type(normalized) is uuid.UUID
            assert normalized == generated
        finally:
            await application_engine.dispose()
            async with migration_engine.begin() as connection:
                await connection.run_sync(metadata.drop_all)
            await migration_engine.dispose()

    asyncio.run(round_trip())
