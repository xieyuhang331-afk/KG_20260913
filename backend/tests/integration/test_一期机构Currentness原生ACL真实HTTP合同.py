from __future__ import annotations

import asyncio
import os
import secrets
from itertools import count

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import (
    _build_alembic_config,
    _get_test_database_url,
    _to_asyncpg_dsn,
)
from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)

pytestmark = pytest.mark.integration

FUNCTION_SIGNATURE = (
    "public.slice3_institution_currentness_authority_v1(bigint,bigint)"
)
_VARIANTS = count()


def _value(database, sql: str, *parameters):
    return next(iter(database.fetch_rows(sql, *parameters)[0].values()))


def _seed_current_institution(
    pg_database, real_db_client, *, variant: int = 0
) -> dict[str, object]:
    from app.core.uuid_generator import Uuid7Generator

    tenant_id = 994201 + (variant * 10)
    org_id = 994202 + (variant * 10)
    phone = ("136", "135", "134")[variant] + (str(4 + variant) * 8)
    password = secrets.token_urlsafe(24)
    tenant_public_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.platform_org("
        "id,parent_id,org_name,org_code,org_type,status,version) VALUES ("
        f"{org_id},NULL,'R2 synthetic county','R2-COUNTY-{variant}','county','active',1);"
        "INSERT INTO public.tenant("
        "id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'R2-TENANT-{variant}','R2 synthetic institution',"
        "'store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database,
        real_db_client,
        phone=phone,
        password=password,
        org_id=org_id,
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        institution_name="R2 synthetic institution",
        pilot_batch_code=f"R2-CURRENTNESS-{variant}",
        service_tags=(),
    )
    return {
        "tenant_id": tenant_id,
        "tenant_public_id": tenant_public_id,
        "user_id": activated["user_id"],
        "phone": phone,
        "password": password,
        "totp_secret": activated["totp_secret"],
    }


@pytest.fixture
def current_institution(pg_database, real_db_client) -> dict[str, object]:
    role = os.environ["KG_TEST_APPLICATION_ROLE"]
    pg_database.execute(
        'GRANT SELECT ON TABLE public."user", public.tenant, '
        f'public.institution_application TO "{role}"'
    )
    seeded = _seed_current_institution(
        pg_database, real_db_client, variant=next(_VARIANTS)
    )
    pg_database.execute(
        'REVOKE ALL ON TABLE public."user", public.tenant, '
        f'public.institution_application FROM "{role}"'
    )
    return seeded


def test_0042真实ACL与机构邀请列表使用原生CurrentnessAuthority(
    pg_database,
    application_database,
    real_db_client,
    current_institution,
) -> None:
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260913_0044"
    )
    seeded = current_institution

    for table_name in ('public."user"', "public.tenant", "public.institution_application"):
        assert _value(
            application_database,
            "SELECT has_table_privilege(current_user,$1,'SELECT')", table_name
        ) is False
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
            application_database.fetch_value(f"SELECT 1 FROM {table_name} LIMIT 1")
        assert denied.value.sqlstate == "42501"

    row = application_database.fetch_rows(
        "SELECT actor_current,institution_current,tenant_public_id "
        "FROM public.slice3_institution_currentness_authority_v1($1,$2)",
        seeded["user_id"],
        seeded["tenant_id"],
    )[0]
    assert row == {
        "actor_current": True,
        "institution_current": True,
        "tenant_public_id": seeded["tenant_public_id"],
    }

    authorization = _login(
        real_db_client,
        str(seeded["phone"]),
        str(seeded["password"]),
        totp_secret=str(seeded["totp_secret"]),
    )
    response = real_db_client.get(
        "/api/v1/institution/member-invitations", headers=authorization
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}

    pg_database.execute(
        "UPDATE public.institution_application SET status='REJECTED' "
        f"WHERE tenant_internal_id={seeded['tenant_id']}"
    )
    rejected = real_db_client.get(
        "/api/v1/institution/member-invitations", headers=authorization
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "TENANT_SCOPE_FORBIDDEN"
    pg_database.execute(
        "UPDATE public.institution_application SET status='APPROVED' "
        f"WHERE tenant_internal_id={seeded['tenant_id']}"
    )


def test_0042函数只向Application开放且其他Runtime和PUBLIC拒绝(
    pg_database,
    member_enrollment_writer_database,
) -> None:
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    denied_roles = (
        os.environ["KG_TEST_READONLY_ROLE"],
        os.environ["KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_READER_ROLE"],
    )
    assert _value(
        pg_database,
        "SELECT has_function_privilege($1,$2,'EXECUTE')",
        application_role,
        FUNCTION_SIGNATURE,
    )
    assert not _value(
        pg_database,
        "SELECT has_function_privilege('public',$1,'EXECUTE')",
        FUNCTION_SIGNATURE,
    )
    for role in denied_roles:
        assert not _value(
            pg_database,
            "SELECT has_function_privilege($1,$2,'EXECUTE')",
            role,
            FUNCTION_SIGNATURE,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
        member_enrollment_writer_database.fetch_value(
            "SELECT public.slice3_institution_currentness_authority_v1(1,1)"
        )
    assert denied.value.sqlstate == "42501"


async def _wait_until_tenant_lock_waits(
    admin, application_pid: int, authority: asyncio.Task
) -> None:
    for _ in range(100):
        if authority.done():
            await authority
            raise AssertionError("authority completed before the controlled Tenant release")
        waiting = await admin.fetchval(
            "SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=$1",
            application_pid,
        )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("authority did not reach the controlled Tenant lock")


async def _verify_lock_order(
    admin_dsn: str,
    application_dsn: str,
    *,
    user_id: int,
    tenant_id: int,
) -> None:
    tenant_blocker = await asyncpg.connect(admin_dsn)
    observer = await asyncpg.connect(admin_dsn)
    application = await asyncpg.connect(application_dsn)
    tenant_tx = tenant_blocker.transaction()
    application_tx = application.transaction()
    await tenant_tx.start()
    await application_tx.start()
    authority = None
    try:
        await tenant_blocker.fetchval(
            "SELECT id FROM public.tenant WHERE id=$1 FOR UPDATE", tenant_id
        )
        application_pid = await application.fetchval("SELECT pg_backend_pid()")
        authority = asyncio.create_task(
            application.fetchrow(
                "SELECT * FROM public.slice3_institution_currentness_authority_v1($1,$2)",
                user_id,
                tenant_id,
            )
        )
        await _wait_until_tenant_lock_waits(observer, application_pid, authority)

        await observer.execute("SET lock_timeout='250ms'")
        with pytest.raises(asyncpg.LockNotAvailableError) as blocked:
            await observer.execute(
                'UPDATE public."user" SET status=status WHERE id=$1', user_id
            )
        assert blocked.value.sqlstate == "55P03"

        await tenant_tx.rollback()
        row = await asyncio.wait_for(authority, timeout=2)
        assert row["actor_current"] is True
        assert row["institution_current"] is True
    finally:
        if authority is not None and not authority.done():
            authority.cancel()
            await asyncio.gather(authority, return_exceptions=True)
        if tenant_blocker.is_in_transaction():
            await tenant_tx.rollback()
        if application.is_in_transaction():
            await application_tx.rollback()
        await application.close()
        await observer.close()
        await tenant_blocker.close()


def test_0042真实锁序固定为User后Tenant(
    pg_database,
    application_database,
    current_institution,
) -> None:
    seeded = current_institution
    asyncio.run(
        _verify_lock_order(
            _to_asyncpg_dsn(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"]),
            application_database.database_url,
            user_id=int(seeded["user_id"]),
            tenant_id=int(seeded["tenant_id"]),
        )
    )


async def _verify_institution_read_does_not_wait(
    admin_dsn: str,
    application_dsn: str,
    *,
    user_id: int,
    tenant_id: int,
) -> None:
    blocker = await asyncpg.connect(admin_dsn)
    application = await asyncpg.connect(application_dsn)
    blocker_tx = blocker.transaction()
    await blocker_tx.start()
    try:
        await blocker.fetchval(
            "SELECT application_id FROM public.institution_application "
            "WHERE tenant_internal_id=$1 FOR UPDATE",
            tenant_id,
        )
        row = await asyncio.wait_for(
            application.fetchrow(
                "SELECT * FROM public.slice3_institution_currentness_authority_v1($1,$2)",
                user_id,
                tenant_id,
            ),
            timeout=2,
        )
        assert row["actor_current"] is True
        assert row["institution_current"] is True
    finally:
        if blocker.is_in_transaction():
            await blocker_tx.rollback()
        await application.close()
        await blocker.close()


def test_0042机构批准事实保持MVCC读取且不等待机构行锁(
    pg_database,
    application_database,
    current_institution,
) -> None:
    seeded = current_institution
    asyncio.run(
        _verify_institution_read_does_not_wait(
            _to_asyncpg_dsn(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"]),
            application_database.database_url,
            user_id=int(seeded["user_id"]),
            tenant_id=int(seeded["tenant_id"]),
        )
    )


def test_0041到0042降级再升级保持单一Head和精确ACL(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260910_0041")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260910_0041"
    )
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('" + FUNCTION_SIGNATURE + "') IS NULL"
    )

    command.upgrade(config, "20260911_0042")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260911_0042"
    )
    assert pg_database.fetch_value(
        "SELECT count(*)=1 FROM alembic_version"
    )
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('" + FUNCTION_SIGNATURE + "') IS NOT NULL"
    )
    assert _value(
        pg_database,
        "SELECT has_function_privilege($1,$2,'EXECUTE')",
        os.environ["KG_TEST_APPLICATION_ROLE"],
        FUNCTION_SIGNATURE,
    )
    assert not pg_database.fetch_value(
        "SELECT has_function_privilege('public','"
        + FUNCTION_SIGNATURE
        + "','EXECUTE')"
    )
