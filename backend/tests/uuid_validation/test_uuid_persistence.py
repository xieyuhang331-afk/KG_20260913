from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import BigInteger, Column, MetaData, Table, Uuid, insert, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from uuid_utils.compat import uuid7

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
        engine = create_async_engine(database_url, poolclass=NullPool)
        generated = uuid7()
        try:
            async with engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
                await connection.execute(insert(validation_table).values(p2_id=generated, p1_legacy_id=9_201))
            async with engine.connect() as connection:
                stored = (await connection.execute(select(validation_table))).one()

            assert isinstance(stored.p2_id, uuid.UUID)
            assert stored.p2_id == generated
            assert stored.p2_id.version == 7
            assert stored.p1_legacy_id == 9_201
            normalized = uuid.UUID(int=stored.p2_id.int)
            assert type(normalized) is uuid.UUID
            assert normalized == generated
        finally:
            async with engine.begin() as connection:
                await connection.run_sync(metadata.drop_all)
            await engine.dispose()

    asyncio.run(round_trip())
