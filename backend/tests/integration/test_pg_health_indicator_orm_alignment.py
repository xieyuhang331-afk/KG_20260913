import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.conftest import _get_test_database_url


@pytest.mark.integration
def test_health_indicator_orm_flushes_against_timescale_hypertable():
    asyncio.run(_assert_health_indicator_orm_flushes_against_timescale_hypertable())


async def _assert_health_indicator_orm_flushes_against_timescale_hypertable():
    database_url = _get_test_database_url()
    schema_name = f"f003_orm_alignment_{uuid4().hex}"
    engine = create_async_engine(
        database_url,
        connect_args={"server_settings": {"search_path": f"{schema_name},public"}},
        pool_pre_ping=True,
    )

    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            await connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
            await connection.execute(
                text(
                    f'''
                    CREATE TYPE "{schema_name}".user_role AS ENUM (
                        'super_admin',
                        'province_admin',
                        'city_admin',
                        'sys_admin',
                        'expert',
                        'org_admin',
                        'org_operator',
                        'therapist',
                        'host',
                        'member'
                    )
                    '''
                )
            )
            await connection.execute(
                text(
                    f'''
                    CREATE TYPE "{schema_name}".user_status AS ENUM (
                        'active',
                        'disabled',
                        'suspended'
                    )
                    '''
                )
            )
            await connection.execute(
                text(
                    f'''
                    CREATE TABLE "{schema_name}"."user" (
                        id BIGSERIAL PRIMARY KEY,
                        phone VARCHAR(11) NOT NULL UNIQUE,
                        password_hash VARCHAR(255) NOT NULL,
                        role user_role NOT NULL DEFAULT 'member',
                        status user_status NOT NULL DEFAULT 'active',
                        verify_status VARCHAR(20) NOT NULL DEFAULT 'unverified',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    '''
                )
            )
            await connection.execute(
                text(
                    f'''
                    CREATE TABLE "{schema_name}".health_indicator (
                        id BIGSERIAL NOT NULL,
                        user_id BIGINT NOT NULL REFERENCES "{schema_name}"."user"(id),
                        plan_id BIGINT,
                        batch_id VARCHAR(36),
                        indicator_type VARCHAR(30) NOT NULL,
                        value DECIMAL(10,2) NOT NULL,
                        unit VARCHAR(10) NOT NULL,
                        source VARCHAR(20) NOT NULL DEFAULT 'APP',
                        recorded_at TIMESTAMPTZ NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT pk_health_indicator PRIMARY KEY (id, recorded_at),
                        CONSTRAINT ck_health_indicator_source
                            CHECK (source IN ('APP', 'STORE', 'DEVICE', 'REPORT'))
                    )
                    '''
                )
            )
            await connection.execute(
                text(
                    f"""
                    SELECT create_hypertable(
                        '"{schema_name}".health_indicator',
                        'recorded_at',
                        chunk_time_interval => INTERVAL '7 days',
                        if_not_exists => TRUE
                    )
                    """
                )
            )
            user_id = (
                await connection.execute(
                    text(
                        f'''
                        INSERT INTO "{schema_name}"."user" (phone, password_hash)
                        VALUES ('13900000001', 'hash')
                        RETURNING id
                        '''
                    )
                )
            ).scalar_one()

        from app.core.sqlalchemy_mapping import map_core_model_classes
        from app.modules.user_health.models import HealthIndicator

        map_core_model_classes()
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        recorded_at = datetime.now(timezone.utc)

        async with session_factory() as session:
            indicator = HealthIndicator()
            indicator.user_id = user_id
            indicator.batch_id = "batch-orm-alignment"
            indicator.indicator_type = "systolic_bp"
            indicator.value = Decimal("120.50")
            indicator.unit = "mmHg"
            indicator.source = "STORE"
            indicator.recorded_at = recorded_at

            session.add(indicator)
            await session.flush()

            assert indicator.id is not None
            inserted_id = indicator.id
            await session.commit()

        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        f'''
                        SELECT id, user_id, indicator_type, value, unit, source
                        FROM "{schema_name}".health_indicator
                        WHERE id = :indicator_id
                        '''
                    ),
                    {"indicator_id": inserted_id},
                )
            ).mappings().one()

        assert row["user_id"] == user_id
        assert row["indicator_type"] == "systolic_bp"
        assert row["value"] == Decimal("120.50")
        assert row["unit"] == "mmHg"
        assert row["source"] == "STORE"
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        await engine.dispose()
