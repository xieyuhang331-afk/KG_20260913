from __future__ import annotations

import asyncio
import importlib.util
import secrets
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def load_seed_module():
    script_path = BACKEND_ROOT / "scripts" / "seed_p1_test_data.py"
    spec = importlib.util.spec_from_file_location("seed_p1_test_data_integration", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def run_seed(module, database_url: str, *, reset: bool) -> None:
    async_database_url = database_url
    if async_database_url.startswith("postgresql://"):
        async_database_url = async_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_database_url, pool_pre_ping=True)
    try:
        context = module.SeedContext(
            settings=module.Settings(
                environment="test",
                database_password=secrets.token_urlsafe(24),
                jwt_secret_key=secrets.token_urlsafe(32),
            ),
            database_url=async_database_url,
            engine=engine,
            session_factory=async_sessionmaker(engine, expire_on_commit=False),
            password_hash=module.hash_password(secrets.token_urlsafe(24)),
        )
        await module.run_identity_seed(context, dry_run=False, reset=reset)
    finally:
        await engine.dispose()


def assert_reset_baseline(pg_database) -> None:
    statuses = pg_database.fetch_rows(
        """
        SELECT id, status::text AS status
        FROM tenant
        WHERE id = ANY($1::bigint[])
        ORDER BY id
        """,
        [8101, 8201],
    )
    assert statuses == [
        {"id": 8101, "status": "pending"},
        {"id": 8201, "status": "pending"},
    ]
    assert (
        pg_database.fetch_value(
            """
            SELECT count(*)
            FROM tenant_review_log
            WHERE tenant_id = ANY(ARRAY[8101, 8201]::bigint[])
            """
        )
        == 0
    )
    assert (
        pg_database.fetch_value(
            """
            SELECT count(*)
            FROM operation_log
            WHERE module = 'tenant'
              AND object_type = 'tenant'
              AND object_id = ANY(ARRAY[8101, 8201]::bigint[])
            """
        )
        == 0
    )


@pytest.mark.integration
def test_seed_reset_clears_review_audit_rows_and_is_repeatable(pg_database):
    module = load_seed_module()
    asyncio.run(run_seed(module, pg_database.database_url, reset=True))

    pg_database.execute(
        """
        UPDATE tenant
        SET status = 'active',
            reviewed_by = 9001,
            reviewed_at = NOW(),
            approved_at = NOW(),
            reject_reason = NULL
        WHERE id = 8101;

        UPDATE tenant
        SET status = 'rejected',
            reviewed_by = 9001,
            reviewed_at = NOW(),
            approved_at = NULL,
            reject_reason = 'P1 seed reset integration test'
        WHERE id = 8201;

        INSERT INTO tenant_review_log (
            tenant_id, reviewer_id, action, grade, comment, created_at
        )
        VALUES
            (8101, 9001, 'approved', 'standard', 'approved before reset', NOW()),
            (8201, 9001, 'rejected', NULL, 'rejected before reset', NOW());

        INSERT INTO operation_log (
            operator_id, module, object_type, object_id, action, payload, created_at
        )
        VALUES
            (
                9001, 'tenant', 'tenant', 8101, 'tenant_onboarding_approved',
                '{"tenant_id": 8101, "status": "active", "action": "approved"}'::jsonb,
                NOW()
            ),
            (
                9001, 'tenant', 'tenant', 8201, 'tenant_onboarding_rejected',
                '{"tenant_id": 8201, "status": "rejected", "action": "rejected"}'::jsonb,
                NOW()
            );
        """
    )

    asyncio.run(run_seed(module, pg_database.database_url, reset=True))
    assert_reset_baseline(pg_database)

    asyncio.run(run_seed(module, pg_database.database_url, reset=True))
    assert_reset_baseline(pg_database)
