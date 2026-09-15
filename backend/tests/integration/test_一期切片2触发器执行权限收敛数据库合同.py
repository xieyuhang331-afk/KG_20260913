from __future__ import annotations

import ast
import asyncio
import os
import uuid
from datetime import date, timedelta
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.integration.conftest import _to_asyncpg_dsn

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    BACKEND_ROOT
    / "app/migrations/versions/20260914_0046_健管师完整性触发器执行权限收敛.py"
)
_TARGET_FUNCTIONS = (
    "public.enforce_therapist_revision_qualification_v1()",
    "public.enforce_therapist_qualification_attachments_v1()",
    "public.enforce_therapist_review_decision_v1()",
)
_CURRENTNESS_FUNCTIONS = (
    "public.slice2_institution_business_currentness_v1(BIGINT,BIGINT,TEXT)",
    "public.slice2_therapist_onboarding_currentness_v1(BIGINT,BIGINT)",
    "public.slice2_therapist_reviewer_currentness_v1(BIGINT)",
)


def test_0046精确继承正式0044且不占用冻结候选0045() -> None:
    assert MIGRATION.is_file(), "SLICE2_TRIGGER_EXECUTE_ACL_0046_MISSING"
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "20260914_0046",
        "down_revision": "20260913_0044",
    }


def test_0046只收敛三个零参数完整性触发器的PUBLIC执行权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert source.count("REVOKE EXECUTE ON FUNCTION") == 1
    assert source.count("GRANT EXECUTE ON FUNCTION") == 1
    for signature in _TARGET_FUNCTIONS:
        assert f'"{signature}"' in source
    for forbidden in (
        "CREATE FUNCTION",
        "DROP FUNCTION",
        "ALTER FUNCTION",
        "CREATE TRIGGER",
        "DROP TRIGGER",
        "ALTER TABLE",
        "GRANT SELECT",
        "GRANT INSERT",
        "GRANT UPDATE",
        "GRANT DELETE",
        "ALTER DEFAULT PRIVILEGES",
        "DATABASE TEMP",
    ):
        assert forbidden not in source


def _alembic_config() -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(BACKEND_ROOT / "app" / "migrations")
    )
    config.set_main_option(
        "sqlalchemy.url", os.environ["KG_TEST_MIGRATION_DATABASE_URL"]
    )
    return config


def _runtime_roles() -> list[str]:
    return sorted(
        {
            value
            for key, value in os.environ.items()
            if key.startswith("KG_TEST_")
            and key.endswith("_ROLE")
            and value
            and key != "KG_TEST_MIGRATION_ROLE"
        }
    )


def _execute_matrix(pg_database, signatures: tuple[str, ...]) -> dict[str, bool]:
    rows = pg_database.fetch_rows(
        "SELECT role_name,signature,"
        "has_function_privilege(role_name,signature,'EXECUTE') AS allowed "
        "FROM unnest($1::text[]) role_name "
        "CROSS JOIN unnest($2::text[]) signature "
        "ORDER BY role_name,signature",
        _runtime_roles(),
        list(signatures),
    )
    return {
        f"{row['role_name']}|{row['signature']}": row["allowed"] for row in rows
    }


def _non_target_acl_snapshot(pg_database) -> list[dict[str, object]]:
    target_names = [
        signature.split(".", 1)[1].split("(", 1)[0]
        for signature in _TARGET_FUNCTIONS
    ]
    return pg_database.fetch_rows(
        "SELECT category,subject,object_name,privilege,is_grantable FROM ("
        "SELECT 'role'::text AS category,r.rolname AS subject,r.rolname AS object_name,"
        "concat_ws(',',r.rolsuper,r.rolinherit,r.rolcreaterole,r.rolcreatedb,"
        "r.rolreplication,r.rolbypassrls) AS privilege,false AS is_grantable "
        "FROM pg_roles r WHERE r.rolname=ANY($1::text[]) UNION ALL "
        "SELECT 'membership',member_role.rolname,parent_role.rolname,'MEMBER',"
        "m.admin_option FROM pg_auth_members m "
        "JOIN pg_roles member_role ON member_role.oid=m.member "
        "JOIN pg_roles parent_role ON parent_role.oid=m.roleid "
        "WHERE member_role.rolname=ANY($1::text[]) OR parent_role.rolname=ANY($1::text[]) "
        "UNION ALL SELECT 'database',role_name,current_database(),"
        "concat('TEMP=',has_database_privilege(role_name,current_database(),'TEMP')),false "
        "FROM unnest($1::text[]) role_name UNION ALL "
        "SELECT 'schema',role_name,'public',"
        "concat('USAGE=',has_schema_privilege(role_name,'public','USAGE'),"
        "',CREATE=',has_schema_privilege(role_name,'public','CREATE')),false "
        "FROM unnest($1::text[]) role_name UNION ALL "
        "SELECT 'table',grantee,table_schema||'.'||table_name,"
        "privilege_type,is_grantable='YES' FROM information_schema.table_privileges "
        "WHERE grantee=ANY($1::text[]) UNION ALL "
        "SELECT 'column',grantee,table_schema||'.'||table_name||'.'||column_name,"
        "privilege_type,is_grantable='YES' FROM information_schema.column_privileges "
        "WHERE grantee=ANY($1::text[]) UNION ALL "
        "SELECT 'routine',grantee,routine_schema||'.'||routine_name,"
        "privilege_type,is_grantable='YES' FROM information_schema.routine_privileges "
        "WHERE grantee=ANY($1::text[]) AND routine_name<>ALL($2::text[]) UNION ALL "
        "SELECT 'usage',grantee,object_schema||'.'||object_name,"
        "privilege_type,is_grantable='YES' FROM information_schema.role_usage_grants "
        "WHERE grantee=ANY($1::text[])) facts "
        "ORDER BY category,subject,object_name,privilege,is_grantable",
        _runtime_roles(),
        target_names,
    )


async def _temporary_binding_results() -> dict[str, str]:
    specifications = (
        (_TARGET_FUNCTIONS[0], "revision_id"),
        (_TARGET_FUNCTIONS[1], "qualification_version_id"),
        (_TARGET_FUNCTIONS[2], "review_item_id"),
    )
    results: dict[str, str] = {}
    for index, (signature, column) in enumerate(specifications):
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_THERAPIST_READER_DATABASE_URL"])
        )
        transaction = connection.transaction()
        try:
            await transaction.start()
            table = f"slice2_trigger_acl_probe_{index}"
            await connection.execute(f"CREATE TEMP TABLE {table}({column} uuid)")
            try:
                await connection.execute(
                    f"CREATE TRIGGER slice2_trigger_acl_probe_{index} "
                    f"AFTER INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {signature}"
                )
                await connection.execute(
                    f"INSERT INTO {table}({column}) VALUES($1)", uuid.uuid4()
                )
            except Exception as exc:
                results[signature] = getattr(exc, "sqlstate", type(exc).__name__)
            else:
                results[signature] = "SUCCESS"
        finally:
            await transaction.rollback()
            await connection.close()
    return results


def _unexpected_persistent_bindings(pg_database) -> list[dict[str, object]]:
    return pg_database.fetch_rows(
        "SELECT p.oid::regprocedure::text AS signature,n.nspname AS table_schema,"
        "c.relname AS table_name,t.tgname "
        "FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE NOT t.tgisinternal AND p.oid=ANY(CAST($1 AS regprocedure[])) "
        "AND (n.nspname,c.relname,t.tgname) NOT IN ("
        "('public','therapist_profile_revision_qualification','cktrg_therapist_revision_qualification_integrity'),"
        "('public','therapist_profile_revision','cktrg_therapist_revision_qualification_revision'),"
        "('public','therapist_qualification_attachment','cktrg_therapist_qualification_attachment_count'),"
        "('public','therapist_qualification_version','cktrg_therapist_qualification_attachment_version'),"
        "('public','therapist_review_item','cktrg_therapist_review_decision_item'),"
        "('public','therapist_review_decision','cktrg_therapist_review_decision_fact'))",
        list(_TARGET_FUNCTIONS),
    )


def test_0044到0046往返只收敛PUBLIC且阻断Reader临时绑定(pg_database) -> None:
    config = _alembic_config()
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260914_0047"
    post_currentness = _execute_matrix(pg_database, _CURRENTNESS_FUNCTIONS)
    assert not any(_execute_matrix(pg_database, _TARGET_FUNCTIONS).values())
    assert _unexpected_persistent_bindings(pg_database) == []
    assert asyncio.run(_temporary_binding_results()) == {
        signature: "42501" for signature in _TARGET_FUNCTIONS
    }

    try:
        command.downgrade(config, "20260913_0044")
        assert pg_database.fetch_value(
            "SELECT version_num FROM alembic_version"
        ) == "20260913_0044"
        pre_acl = _non_target_acl_snapshot(pg_database)
        pre_currentness = _execute_matrix(pg_database, _CURRENTNESS_FUNCTIONS)
        assert all(_execute_matrix(pg_database, _TARGET_FUNCTIONS).values())
        assert asyncio.run(_temporary_binding_results()) == {
            signature: "SUCCESS" for signature in _TARGET_FUNCTIONS
        }

        command.upgrade(config, "20260914_0046")
        assert pg_database.fetch_value(
            "SELECT version_num FROM alembic_version"
        ) == "20260914_0046"
        assert _non_target_acl_snapshot(pg_database) == pre_acl
        assert pre_currentness == post_currentness
        assert _execute_matrix(pg_database, _CURRENTNESS_FUNCTIONS) == post_currentness
        assert not any(_execute_matrix(pg_database, _TARGET_FUNCTIONS).values())
        assert asyncio.run(_temporary_binding_results()) == {
            signature: "42501" for signature in _TARGET_FUNCTIONS
        }
        assert _unexpected_persistent_bindings(pg_database) == []
    finally:
        if pg_database.fetch_value(
            "SELECT version_num FROM alembic_version"
        ) != "20260914_0047":
            command.upgrade(config, "20260914_0047")


@pytest.mark.asyncio
async def test_0046撤权后正式Writer既有约束触发器仍在提交阶段拒绝非法写入(
    pg_database,
) -> None:
    from tests.integration.test_一期切片2健管师资质与ServiceReady数据库闭环 import (
        _cleanup_slice2_seed,
        _seed_approved_therapist,
        _seed_ready_institution,
    )

    offset = uuid.uuid4().int % 4_000_000 + 40_000_000
    institution = await _seed_ready_institution(pg_database, offset=offset)
    tenant_id, org_admin_id, reviewer_id, _, county_id = institution
    therapist = await _seed_approved_therapist(
        pg_database,
        tenant_id=tenant_id,
        issued_by=org_admin_id,
        offset=offset,
        valid_until=date.today() + timedelta(days=180),
    )
    revision_id = await pg_database._fetch_value(
        "SELECT revision_id FROM public.therapist_profile_revision "
        f"WHERE therapist_id='{therapist['therapist_id']}'"
    )
    before_revision_count = await pg_database._fetch_value(
        "SELECT count(*) FROM public.therapist_profile_revision "
        f"WHERE therapist_id='{therapist['therapist_id']}'"
    )
    before_review_count = await pg_database._fetch_value(
        "SELECT count(*) FROM public.therapist_review_item "
        f"WHERE therapist_id='{therapist['therapist_id']}'"
    )

    async def invalid_revision() -> str | None:
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(
                os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"]
            )
        )
        try:
            try:
                async with connection.transaction():
                    await connection.execute(
                        "INSERT INTO public.therapist_profile_revision("
                        "revision_id,therapist_id,revision_no,profile_snapshot,"
                        "input_digest,created_at) VALUES($1,$2,2,'{}'::jsonb,"
                        "repeat('7',64),now())",
                        uuid.uuid4(),
                        uuid.UUID(str(therapist["therapist_id"])),
                    )
            except Exception as exc:
                return getattr(exc, "sqlstate", type(exc).__name__)
            return None
        finally:
            await connection.close()

    async def invalid_review() -> str | None:
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(
                os.environ["KG_TEST_THERAPIST_REVIEW_WRITER_DATABASE_URL"]
            )
        )
        try:
            try:
                async with connection.transaction():
                    await connection.execute(
                        "INSERT INTO public.therapist_review_item("
                        "review_item_id,therapist_id,revision_id,qualification_version_id,"
                        "review_kind,status,reviewer_user_id,previous_review_item_id,"
                        "created_at,claimed_at,decided_at,version) VALUES("
                        "$1,$2,$3,NULL,'INITIAL','DECIDED',$4,NULL,now(),now(),now(),1)",
                        uuid.uuid4(),
                        uuid.UUID(str(therapist["therapist_id"])),
                        uuid.UUID(str(revision_id)),
                        reviewer_id,
                    )
            except Exception as exc:
                return getattr(exc, "sqlstate", type(exc).__name__)
            return None
        finally:
            await connection.close()

    try:
        assert await invalid_revision() == "23514"
        assert await invalid_review() == "23514"
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.therapist_profile_revision "
            f"WHERE therapist_id='{therapist['therapist_id']}'"
        ) == before_revision_count
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.therapist_review_item "
            f"WHERE therapist_id='{therapist['therapist_id']}'"
        ) == before_review_count
    finally:
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )
