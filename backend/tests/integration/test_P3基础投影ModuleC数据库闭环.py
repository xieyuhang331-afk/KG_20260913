import os
import asyncio

import pytest
import asyncpg
from alembic import command

from tests.integration.conftest import (
    _build_alembic_config, _get_role_database_url, _get_test_database_url,
)


pytestmark = pytest.mark.integration


def test_Module_C_migration_and_minimum_privileges(pg_database):
    assert pg_database.fetch_value("SELECT version_num='20260821_0023' FROM alembic_version")
    for relation in (
        "organization_projection_shadow_run",
        "organization_projection_shadow_audit",
        "health_projection_shadow_run",
        "health_projection_shadow_audit",
    ):
        assert pg_database.fetch_value(
            f"SELECT to_regclass('public.{relation}') IS NOT NULL"
        )

    org = os.environ["KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE"]
    health = os.environ["KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE"]
    ready = os.environ["KG_TEST_PROJECTION_READY_GATE_ROLE"]
    confirmation = os.environ["KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE"]
    builder = os.environ["KG_TEST_ORGANIZATION_PROJECTION_BUILDER_ROLE"]

    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{org}','public.platform_org','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{org}','public.platform_org','id','SELECT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{health}','public.canonical_health_fact','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_table_privilege('{health}','public.health_projection_source_visibility_v1','SELECT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{org}','public.health_projection_shadow_run','SELECT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{health}','public.organization_projection_shadow_run','SELECT')"
    )
    for role in (org, health, ready, confirmation):
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.organization_projection','INSERT')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.health_projection_fact','UPDATE')"
        )
    assert not pg_database.fetch_value(
        f"SELECT has_column_privilege('{org}','public.organization_projection_generation','ready_at','UPDATE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{ready}','public.organization_projection_generation','ready_at','UPDATE')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_column_privilege('{confirmation}','public.organization_projection_generation','status','UPDATE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{builder}','public.organization_projection','path_versions','INSERT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{org}','public.organization_projection','path_versions','SELECT')"
    )
    for role in (health, ready, confirmation):
        assert not pg_database.fetch_value(
            f"SELECT has_column_privilege('{role}','public.organization_projection','path_versions','SELECT')"
        )
    checks = pg_database.fetch_rows(
        "SELECT conname, pg_get_constraintdef(oid) AS definition "
        "FROM pg_constraint WHERE conrelid='public.organization_projection'::regclass "
        "AND contype='c' ORDER BY conname"
    )
    path_version_checks = [row for row in checks if "path_versions" in row["definition"]]
    assert len(path_version_checks) == 1
    path_version_check = path_version_checks[0]
    definition = path_version_check["definition"]
    assert path_version_check["conname"] == "ck_organization_projection_compatibility"
    assert not any(
        row["conname"] == "ck_organization_projection_ck_organization_projection_c_41f6"
        for row in checks
    )
    assert "jsonb_typeof(path_versions) = 'array'::text" in definition
    assert "jsonb_array_length(path_versions) = 4" in definition
    assert "jsonb_path_exists(path_versions" in definition
    assert '@ < 1' in definition
    assert "@ % 1 != 0" in definition


@pytest.mark.parametrize(
    "path_versions",
    ([1, 1, 1, 0], [1, 1, 1, -1], [1, 1, 1, 1.5], [1, 1, 1, "1"], [1, 1, 1], [1, 1, 1, 1, 1]),
)
def test_Module_C_path_versions_real_PostgreSQL_rejects_invalid_values(pg_database, path_versions):
    async def verify():
        connection = await asyncpg.connect(pg_database.database_url)
        transaction = connection.transaction()
        await transaction.start()
        try:
            generation_id = await connection.fetchval(
                "INSERT INTO public.organization_projection_generation "
                "(projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,completed_at,version) "
                "VALUES (1,999,'BUILD_COMPLETE','{\"max_organization_id\":4}'::jsonb,'k1',$1,$2,NULL,0,NULL,now(),1) RETURNING id",
                "a" * 64, "00000000-0000-0000-0000-000000000999",
            )
            nested = connection.transaction()
            await nested.start()
            try:
                with pytest.raises(asyncpg.CheckViolationError) as rejected:
                    await connection.execute(
                        "INSERT INTO public.organization_projection "
                        "(generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,path_versions,compatibility_mode,scope_eligible,row_digest,digest_key_id) "
                        "VALUES ($1,4,3,'C-4','county','county','active',0,1,'[1,2,3,4]'::jsonb,'[\"C-1\",\"C-2\",\"C-3\",\"C-4\"]'::jsonb,$2::jsonb,'canonical',true,$3,'k1')",
                        generation_id, __import__("json").dumps(path_versions), "b" * 64,
                    )
                assert rejected.value.constraint_name == "ck_organization_projection_compatibility"
            finally:
                await nested.rollback()
        finally:
            await transaction.rollback()
            await connection.close()

    asyncio.run(verify())


def test_Module_C_nonempty_projection_refuses_downgrade_without_data_loss(pg_database):
    generation_id = pg_database.fetch_value(
        "INSERT INTO public.organization_projection_generation "
        "(projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES (1,998,'BUILD_COMPLETE','{\"max_organization_id\":4}'::jsonb,'k1',repeat('a',64),'00000000-0000-0000-0000-000000000998',NULL,0,NULL,now(),1) RETURNING id"
    )
    pg_database.execute(
        "INSERT INTO public.organization_projection "
        "(generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,path_versions,compatibility_mode,scope_eligible,row_digest,digest_key_id) "
        f"VALUES ({generation_id},4,3,'C-4','county','county','active',0,1,'[1,2,3,4]'::jsonb,'[\"C-1\",\"C-2\",\"C-3\",\"C-4\"]'::jsonb,'[1,1,1,1]'::jsonb,'canonical',true,repeat('b',64),'k1')"
    )
    config = _build_alembic_config(_get_test_database_url())
    try:
        with pytest.raises(RuntimeError, match="organization projection rows exist"):
            command.downgrade(config, "20260813_0017")
        assert pg_database.fetch_value("SELECT version_num='20260821_0023' FROM alembic_version")
        assert pg_database.fetch_value(
            f"SELECT path_versions='[1,1,1,1]'::jsonb FROM public.organization_projection WHERE generation_id={generation_id} AND organization_id=4"
        )
    finally:
        pg_database.execute(f"DELETE FROM public.organization_projection WHERE generation_id={generation_id}")
        pg_database.execute(f"DELETE FROM public.organization_projection_checkpoint WHERE generation_id={generation_id}")
        pg_database.execute(f"DELETE FROM public.organization_projection_generation WHERE id={generation_id}")


@pytest.mark.parametrize(
    "environment_name,expected_role_environment",
    (
        ("KG_TEST_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL", "KG_TEST_ORGANIZATION_PROJECTION_SHADOW_ROLE"),
        ("KG_TEST_HEALTH_PROJECTION_SHADOW_DATABASE_URL", "KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE"),
        ("KG_TEST_PROJECTION_READY_GATE_DATABASE_URL", "KG_TEST_PROJECTION_READY_GATE_ROLE"),
        ("KG_TEST_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL", "KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE"),
    ),
)
def test_Module_C_runtime_identities_are_distinct(environment_name, expected_role_environment):
    import asyncio
    import asyncpg
    from sqlalchemy.engine import make_url

    async def verify():
        parsed = make_url(_get_role_database_url(environment_name))
        connection = await asyncpg.connect(
            user=parsed.username, password=parsed.password, host=parsed.host,
            port=parsed.port, database=parsed.database,
        )
        try:
            assert await connection.fetchval("SELECT current_user") == os.environ[expected_role_environment]
        finally:
            await connection.close()

    asyncio.run(verify())


def test_Module_C_Shadow_truth_table与0017_downgrade精确恢复(pg_database):
    for domain in ("organization", "health"):
        definitions = pg_database.fetch_column(
            f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.{domain}_projection_shadow_run'::regclass"
        )
        joined = " ".join(definitions)
        assert "category_counts" in joined
        assert "[0-9A-F]{64}" in joined
        assert "PASSED" in joined and "blocker_count = 0" in joined
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260813_0017")
    for domain in ("organization", "health"):
        state = pg_database.fetch_value(
            f"SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.{domain}_projection_generation'::regclass AND contype='c' AND pg_get_constraintdef(oid) LIKE '%PROJECTION_SOURCE_INVALID%'"
        )
        assert state is not None
        for code in (
            "PROJECTION_SOURCE_INVALID", "PROJECTION_DIGEST_KEY_UNAVAILABLE",
            "PROJECTION_CHECKPOINT_CONFLICT", "PROJECTION_LEASE_CONFLICT",
            "PROJECTION_UNAVAILABLE",
        ):
            assert code in state
        assert "failure_code IS NOT NULL" not in state
    command.upgrade(config, "20260818_0022")
    names = pg_database.fetch_column(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid='public.organization_projection'::regclass AND contype='c' "
        "AND pg_get_constraintdef(oid) LIKE '%path_versions%'"
    )
    assert names == ["ck_organization_projection_compatibility"]
