from __future__ import annotations

import asyncio
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import asyncpg
import pytest

from tests.integration.conftest import _to_asyncpg_dsn

pytestmark = pytest.mark.integration


def _disable_refresh_dispatch(monkeypatch) -> None:
    from app.modules.therapist_qualification import api

    async def no_dispatch(_tenant_id: int) -> None:
        return None

    monkeypatch.setattr(api, "_request_readiness_refresh", no_dispatch)
    monkeypatch.setattr(api.celery_app, "send_task", lambda *args, **kwargs: None)


def _headers(
    *,
    user_id: int,
    role: str,
    tenant_id: int | None = None,
    org_id: int | None = None,
) -> dict[str, str]:
    from app.core.security import create_access_token

    claims: dict[str, object] = {"sub": str(user_id), "role": role}
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if org_id is not None:
        claims["org_id"] = org_id
    token = create_access_token(claims)
    return {"Authorization": f"Bearer {token}"}


def _seed_readiness(pg_database, *, with_approved_therapist: bool):
    from tests.integration.test_一期切片2健管师资质与ServiceReady数据库闭环 import (
        _seed_approved_therapist,
        _seed_ready_institution,
    )

    offset = secrets.randbelow(4_000_000) + 31_000_000

    async def seed():
        institution = await _seed_ready_institution(pg_database, offset=offset)
        tenant_id, org_admin_id, _, _, _ = institution
        if with_approved_therapist:
            await _seed_approved_therapist(
                pg_database,
                tenant_id=tenant_id,
                issued_by=org_admin_id,
                offset=offset,
                valid_until=date.today() + timedelta(days=180),
            )
        return institution

    institution = asyncio.run(seed())
    tenant_id = institution[0]
    readiness_status = "SERVICE_READY" if with_approved_therapist else "NOT_READY"
    reason_codes = (
        "ARRAY[]::text[]"
        if with_approved_therapist
        else "ARRAY['NO_APPROVED_ACTIVE_THERAPIST']::text[]"
    )
    qualified_count = 1 if with_approved_therapist else 0
    pg_database.execute(
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) "
        "SELECT g.tenant_id,"
        f"'{readiness_status}',{reason_codes},{qualified_count},now(),1,repeat('a',64),repeat('b',64),"
        "jsonb_build_object("
        "'tenant_public_id',g.tenant_public_id::text,'tenant_status',g.tenant_status,"
        "'application_id',g.application_id::text,'application_version',g.application_version,"
        "'license_versions',g.license_versions,'current_therapist_versions',g.current_therapist_versions,"
        "'digest_key_id','synthetic-v1'),g.next_expiry_at,1 "
        "FROM public.institution_readiness_guard_v1 g "
        f"WHERE g.tenant_id={tenant_id}"
    )
    return institution


def _cleanup(real_db_client, pg_database, institution) -> None:
    from app.core.database import dispose_database_runtimes
    from tests.integration.test_一期切片2健管师资质与ServiceReady数据库闭环 import (
        _cleanup_slice2_seed,
    )

    tenant_id, org_admin_id, reviewer_id, _, county_id = institution

    async def clean():
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )

    real_db_client.portal.call(dispose_database_runtimes)
    asyncio.run(clean())


@pytest.mark.parametrize(
    ("with_approved_therapist", "expected_status"),
    ((True, "SERVICE_READY"), (False, "NOT_READY")),
    ids=("ready", "not-ready"),
)
def test_机构服务就绪查询在同一RR快照内先校验当前性再读取就绪状态(
    real_db_client,
    pg_database,
    monkeypatch,
    with_approved_therapist: bool,
    expected_status: str,
) -> None:
    _disable_refresh_dispatch(monkeypatch)
    institution = _seed_readiness(
        pg_database, with_approved_therapist=with_approved_therapist
    )
    tenant_id, org_admin_id, _, _, county_id = institution
    try:
        response = real_db_client.get(
            "/api/v1/institution/service-readiness",
            headers=_headers(
                user_id=org_admin_id,
                role="org_admin",
                tenant_id=tenant_id,
                org_id=county_id,
            ),
        )
        assert response.status_code == 200, (
            "SERVICE_READINESS_HTTP_TRANSACTION_ORDER_INVALID"
        )
        assert response.json()["data"]["readiness_status"] == expected_status
        assert response.headers.get("Cache-Control") == "no-store, private"
    finally:
        _cleanup(real_db_client, pg_database, institution)


def test_机构服务就绪查询认证与当前性边界保持拒绝(
    real_db_client, pg_database, monkeypatch
) -> None:
    _disable_refresh_dispatch(monkeypatch)
    institution = _seed_readiness(pg_database, with_approved_therapist=False)
    tenant_id, org_admin_id, reviewer_id, _, county_id = institution
    try:
        missing = real_db_client.get("/api/v1/institution/service-readiness")
        assert missing.status_code == 401
        assert missing.json()["code"] == "AUTHENTICATION_REQUIRED"

        wrong_role = real_db_client.get(
            "/api/v1/institution/service-readiness",
            headers=_headers(user_id=reviewer_id, role="super_admin"),
        )
        assert wrong_role.status_code == 403
        assert wrong_role.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"

        pg_database.execute(
            "UPDATE public.institution_application SET status='REJECTED' "
            f"WHERE tenant_internal_id={tenant_id}"
        )
        inactive = real_db_client.get(
            "/api/v1/institution/service-readiness",
            headers=_headers(
                user_id=org_admin_id,
                role="org_admin",
                tenant_id=tenant_id,
                org_id=county_id,
            ),
        )
        assert inactive.status_code == 403
        assert inactive.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"
    finally:
        _cleanup(real_db_client, pg_database, institution)


def test_同一正式reader事务直接完成当前性与就绪后像读取(pg_database) -> None:
    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.core.security import CurrentUser
    from app.modules.therapist_qualification.api import (
        _institution_reader_read_currentness,
    )
    from app.modules.therapist_qualification.service import read_readiness_fail_closed

    institution = _seed_readiness(pg_database, with_approved_therapist=True)
    tenant_id, org_admin_id, _, _, county_id = institution

    async def read():
        try:
            factory = get_slice2_session_factory("reader")
            async with factory() as session:
                actor = CurrentUser(
                    id=org_admin_id,
                    role="org_admin",
                    tenant_id=tenant_id,
                    org_id=county_id,
                )
                await _institution_reader_read_currentness(session, actor)
                isolation = (await session.execute(
                    __import__("sqlalchemy").text("SHOW transaction_isolation")
                )).scalar_one()
                read_only = (await session.execute(
                    __import__("sqlalchemy").text("SHOW transaction_read_only")
                )).scalar_one()
                value, stale = await read_readiness_fail_closed(
                    session, tenant_id, establish_transaction=False
                )
                return value, stale, isolation, read_only
        finally:
            await dispose_database_runtimes()

    try:
        value, stale, isolation, read_only = asyncio.run(read())
        assert isolation == "repeatable read"
        assert read_only == "off"
        assert stale is False
        assert value is not None
        assert value["readiness_status"] == "SERVICE_READY"
    finally:
        from tests.integration.test_一期切片2健管师资质与ServiceReady数据库闭环 import (
            _cleanup_slice2_seed,
        )

        asyncio.run(_cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=institution[2],
            county_id=county_id,
        ))


def test_正式reader移除数据库只读属性后仍保持最小权限(pg_database) -> None:
    role = os.environ["KG_TEST_THERAPIST_READER_ROLE"]
    assert role.replace("_", "").isalnum()

    def role_value(sql: str):
        return pg_database.fetch_rows(sql, role)[0]["value"]

    role_facts = pg_database.fetch_rows(
        "SELECT rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolreplication,"
        "rolbypassrls FROM pg_roles WHERE rolname=$1",
        role,
    )
    assert role_facts == [{
        "rolsuper": False,
        "rolinherit": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }]
    assert role_value(
        "SELECT count(*) AS value FROM pg_auth_members m "
        "JOIN pg_roles member_role ON member_role.oid=m.member "
        "JOIN pg_roles granted_role ON granted_role.oid=m.roleid "
        "WHERE member_role.rolname=$1 OR granted_role.rolname=$1"
    ) == 0
    assert role_value(
        "SELECT count(*) AS value FROM information_schema.table_privileges "
        "WHERE grantee=$1 AND privilege_type IN "
        "('INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')"
    ) == 0
    assert role_value(
        "SELECT count(*) AS value FROM information_schema.column_privileges "
        "WHERE grantee=$1 AND privilege_type IN ('INSERT','UPDATE','REFERENCES')"
    ) == 0
    assert not role_value(
        "SELECT has_schema_privilege($1,'public','CREATE') AS value"
    )
    assert role_value(
        "SELECT count(*) AS value FROM information_schema.role_usage_grants "
        "WHERE grantee=$1 AND object_type='SEQUENCE'"
    ) == 0

    executable_definers = set(pg_database.fetch_column(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='public' AND p.prosecdef "
        f"AND has_function_privilege('{role}',p.oid,'EXECUTE')"
    ))
    assert executable_definers == {
        "slice2_institution_business_currentness_v1",
        "slice2_therapist_onboarding_currentness_v1",
        "slice2_therapist_reviewer_currentness_v1",
    }

    async def rejected(statement: str) -> str | None:
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_THERAPIST_READER_DATABASE_URL"])
        )
        transaction = connection.transaction()
        try:
            await transaction.start()
            try:
                await connection.execute(statement)
            except Exception as exc:
                return getattr(exc, "sqlstate", type(exc).__name__)
            return None
        finally:
            await transaction.rollback()
            await connection.close()

    probes = (
        'SELECT 1 FROM public."user" LIMIT 0',
        "SELECT 1 FROM public.tenant LIMIT 0",
        "SELECT 1 FROM public.institution_application LIMIT 0",
        "UPDATE public.institution_service_readiness SET version=version WHERE false",
        "DELETE FROM public.institution_service_readiness WHERE false",
        "TRUNCATE public.institution_service_readiness",
        "CREATE TABLE public.slice2_reader_forbidden_probe(id integer)",
        "CREATE FUNCTION public.slice2_reader_forbidden_probe() RETURNS integer "
        "LANGUAGE sql AS 'SELECT 1'",
        "SELECT nextval('public.therapist_workflow_audit_audit_id_seq')",
    )
    for statement in probes:
        assert asyncio.run(rejected(statement)) == "42501"


def test_机构当前性权威在同一reader事务内保持User与Tenant行锁(
    pg_database,
) -> None:
    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.core.security import CurrentUser
    from app.modules.therapist_qualification.api import (
        _institution_reader_read_currentness,
    )

    institution = _seed_readiness(pg_database, with_approved_therapist=True)
    tenant_id, org_admin_id, _, _, county_id = institution

    async def probe(statement: str) -> str | None:
        try:
            factory = get_slice2_session_factory("reader")
            async with factory() as session:
                actor = CurrentUser(
                    id=org_admin_id,
                    role="org_admin",
                    tenant_id=tenant_id,
                    org_id=county_id,
                )
                await _institution_reader_read_currentness(session, actor)
                connection = await asyncpg.connect(
                    _to_asyncpg_dsn(
                        os.environ["KG_TEST_MIGRATION_DATABASE_URL"]
                    )
                )
                transaction = connection.transaction()
                try:
                    await transaction.start()
                    await connection.execute("SET LOCAL lock_timeout='250ms'")
                    try:
                        await connection.execute(statement)
                    except Exception as exc:
                        return getattr(exc, "sqlstate", type(exc).__name__)
                    return None
                finally:
                    await transaction.rollback()
                    await connection.close()
        finally:
            await dispose_database_runtimes()

    try:
        assert asyncio.run(
            probe(
                f'UPDATE public."user" SET status=status WHERE id={org_admin_id}'
            )
        ) == "55P03"
        assert asyncio.run(
            probe(f"UPDATE public.tenant SET status=status WHERE id={tenant_id}")
        ) == "55P03"
    finally:
        from tests.integration.test_一期切片2健管师资质与ServiceReady数据库闭环 import (
            _cleanup_slice2_seed,
        )

        asyncio.run(
            _cleanup_slice2_seed(
                pg_database,
                tenant_id=tenant_id,
                org_admin_id=org_admin_id,
                reviewer_id=institution[2],
                county_id=county_id,
            )
        )


@pytest.mark.parametrize("revocation", ("user", "tenant", "approval"))
def test_机构服务就绪查询与撤权按冻结锁和快照语义收敛(
    real_db_client, pg_database, monkeypatch, revocation: str
) -> None:
    from app.modules.therapist_qualification import api

    _disable_refresh_dispatch(monkeypatch)
    institution = _seed_readiness(pg_database, with_approved_therapist=True)
    tenant_id, org_admin_id, _, _, county_id = institution
    headers = _headers(
        user_id=org_admin_id,
        role="org_admin",
        tenant_id=tenant_id,
        org_id=county_id,
    )
    entered_readiness = threading.Event()
    release_readiness = threading.Event()
    held_lock_sqlstates: list[str | None] = []
    original_read = api.read_readiness_fail_closed

    async def held_read(*args, **kwargs):
        connection = await asyncpg.connect(
            _to_asyncpg_dsn(os.environ["KG_TEST_MIGRATION_DATABASE_URL"])
        )
        transaction = connection.transaction()
        try:
            await transaction.start()
            await connection.execute("SET LOCAL lock_timeout='250ms'")
            try:
                await connection.execute(update)
            except Exception as exc:
                held_lock_sqlstates.append(
                    getattr(exc, "sqlstate", type(exc).__name__)
                )
            else:
                held_lock_sqlstates.append(None)
        finally:
            await transaction.rollback()
            await connection.close()
        entered_readiness.set()
        await asyncio.to_thread(release_readiness.wait)
        return await original_read(*args, **kwargs)

    monkeypatch.setattr(api, "read_readiness_fail_closed", held_read)

    if revocation == "user":
        update = f'UPDATE public."user" SET status=\'disabled\' WHERE id={org_admin_id}'
        restore = f'UPDATE public."user" SET status=\'active\' WHERE id={org_admin_id}'
    elif revocation == "tenant":
        update = f"UPDATE public.tenant SET status='closed' WHERE id={tenant_id}"
        restore = f"UPDATE public.tenant SET status='active' WHERE id={tenant_id}"
    else:
        update = (
            "UPDATE public.institution_application SET status='NEEDS_CORRECTION' "
            f"WHERE tenant_internal_id={tenant_id}"
        )
        restore = (
            "UPDATE public.institution_application SET status='APPROVED' "
            f"WHERE tenant_internal_id={tenant_id}"
        )

    def request():
        return real_db_client.get(
            "/api/v1/institution/service-readiness", headers=headers
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            request_future = executor.submit(request)
            try:
                assert entered_readiness.wait(timeout=10)
                expected_lock_state = None if revocation == "approval" else "55P03"
                assert held_lock_sqlstates == [expected_lock_state]
                if revocation == "approval":
                    pg_database.execute(update)
            finally:
                release_readiness.set()
            first = request_future.result(timeout=10)

        if revocation != "approval":
            pg_database.execute(update)

        assert first.status_code == 200
        denied = real_db_client.get(
            "/api/v1/institution/service-readiness", headers=headers
        )
        expected_status = 401 if revocation == "user" else 403
        expected_code = (
            "AUTHENTICATION_REQUIRED"
            if revocation == "user"
            else "ACTOR_CURRENTNESS_FORBIDDEN"
        )
        assert denied.status_code == expected_status
        assert denied.json()["code"] == expected_code
    finally:
        release_readiness.set()
        pg_database.execute(restore)
        _cleanup(real_db_client, pg_database, institution)
