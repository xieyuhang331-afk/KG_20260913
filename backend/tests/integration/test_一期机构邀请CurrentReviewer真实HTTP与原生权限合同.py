from __future__ import annotations

import asyncio
import os
import queue
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from app.modules.auth.service import hash_password

pytestmark = pytest.mark.integration

_CURRENTNESS_SIGNATURE = (
    "public.institution_onboarding_reviewer_currentness_v1(bigint)"
)
_REVIEWER_ID = 9955101
_REGION_ID = 9955102
_REVIEWER_PHONE = "13655555555"
_MEMBER_ID = 9955103
_MEMBER_PHONE = "13755555555"
_TENANT_ID = 9955104


def _login(client, phone: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["data"]["access_token"]}


@pytest.fixture(scope="module")
def reviewer_and_region(pg_database) -> tuple[str, str]:
    reviewer_password = secrets.token_urlsafe(24)
    member_password = secrets.token_urlsafe(24)
    reviewer_hash = hash_password(reviewer_password).replace("'", "''")
    member_hash = hash_password(member_password).replace("'", "''")
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({_REVIEWER_ID},'{_REVIEWER_PHONE}','{reviewer_hash}','super_admin','active',NULL),"
        f"({_MEMBER_ID},'{_MEMBER_PHONE}','{member_hash}','member','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES "
        "(9955199,NULL,'Synthetic headquarter','CURRENT-REVIEWER-HQ','headquarter','active',1),"
        "(9955200,9955199,'Synthetic province','CURRENT-REVIEWER-PROVINCE','province','active',1),"
        "(9955201,9955200,'Synthetic city','CURRENT-REVIEWER-CITY','city','active',1),"
        f"({_REGION_ID},9955201,'Synthetic county','CURRENT-REVIEWER-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status) "
        f"VALUES ({_TENANT_ID},{_REGION_ID},'CURRENT-REVIEWER-TENANT','Synthetic tenant','store','test','test','active')"
    )
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    pg_database.execute(
        'REVOKE ALL ON TABLE public."user" FROM "' + application_role + '"'
    )
    return reviewer_password, member_password


def _invitation_count(pg_database) -> int:
    return int(pg_database.fetch_value("SELECT count(*) FROM public.institution_invitation"))


def _post_invitation(client, authorization: dict[str, str], suffix: str):
    return client.post(
        "/api/v1/platform/institution-invitations",
        headers={**authorization, "Idempotency-Key": f"current-reviewer-{suffix}"},
        json={
            "institution_name": "Synthetic current reviewer institution",
            "institution_type": "HEALTH_STORE",
            "applicant_phone": "13855555555",
            "pilot_batch_code": "CURRENT-REVIEWER",
            "administrative_region_id": _REGION_ID,
            "expires_in_minutes": 60,
        },
    )


def _mutate_reviewer_with_observable_pid(
    database_url: str,
    mutation_sql: str,
    started: threading.Event,
    finished: threading.Event,
    backend_pid: queue.Queue[int],
) -> None:
    async def run() -> None:
        connection = await asyncpg.connect(database_url)
        try:
            transaction = connection.transaction()
            await transaction.start()
            await connection.execute("SET LOCAL lock_timeout = '15s'")
            backend_pid.put(await connection.fetchval("SELECT pg_backend_pid()"))
            started.set()
            await connection.execute(mutation_sql)
            await transaction.commit()
        finally:
            await connection.close()
            finished.set()

    asyncio.run(run())


def _wait_for_currentness_blocker(
    pg_database, waiter_pid: int, expected_holder_pid: int
) -> None:
    deadline = monotonic() + 10
    while monotonic() < deadline:
        blockers = pg_database.fetch_rows(
            "SELECT holder.pid "
            "FROM pg_catalog.pg_stat_activity waiter "
            "CROSS JOIN LATERAL "
            "unnest(pg_catalog.pg_blocking_pids(waiter.pid)) blocker(pid) "
            "JOIN pg_catalog.pg_stat_activity holder ON holder.pid=blocker.pid "
            f"WHERE waiter.pid={waiter_pid}"
        )
        if blockers:
            assert any(
                row["pid"] == expected_holder_pid for row in blockers
            ), "CURRENT_REVIEWER_WRONG_LOCK_HOLDER"
            return
        threading.Event().wait(0.025)
    pytest.fail("CURRENT_REVIEWER_DATABASE_BLOCK_NOT_OBSERVED")


def _hold_currentness_then_fail(
    *,
    outcome: str,
    locked: threading.Event,
    release: threading.Event,
    holder_pid: queue.Queue[int],
) -> None:
    async def run() -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.security import CurrentUser
        from app.modules.institution_onboarding.service import require_current_reviewer

        engine = create_async_engine(
            os.environ["KG_TEST_DATABASE_URL"],
            poolclass=NullPool,
        )
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                holder_pid.put(
                    (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                )
                await require_current_reviewer(
                    session,
                    CurrentUser(id=_REVIEWER_ID, role="super_admin"),
                )
                locked.set()
                if not await asyncio.to_thread(release.wait, 10):
                    raise AssertionError("CURRENT_REVIEWER_RELEASE_TIMEOUT")
                if outcome == "cancel":
                    raise asyncio.CancelledError
                raise RuntimeError("SYNTHETIC_BUSINESS_FAILURE")
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_真实登录与机构邀请通过闭合CurrentReviewer持久化(
    pg_database, application_database, real_db_client, reviewer_and_region
):
    reviewer_password, _ = reviewer_and_region
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]

    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.user','SELECT')"
    ) is False
    assert pg_database.fetch_value(
        "SELECT has_function_privilege('"
        + application_role
        + "','"
        + _CURRENTNESS_SIGNATURE
        + "','EXECUTE')"
    ) is True
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        application_database.fetch_value('SELECT id FROM public."user" LIMIT 1')

    authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    before = _invitation_count(pg_database)
    response = _post_invitation(real_db_client, authorization, "success")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ISSUED"
    assert _invitation_count(pg_database) == before + 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.institution_onboarding_audit "
        "WHERE actor_user_id=" + str(_REVIEWER_ID) + " AND action='INVITATION_CREATE'"
    ) == 1
    rendered = response.text
    assert _REVIEWER_PHONE not in rendered
    assert reviewer_password not in rendered


def test_认证与Currentness拒绝边界均不产生邀请侧效(
    pg_database, real_db_client, reviewer_and_region
):
    reviewer_password, member_password = reviewer_and_region
    before = _invitation_count(pg_database)

    missing = _post_invitation(real_db_client, {}, "missing")
    invalid = _post_invitation(
        real_db_client, {"Authorization": "Bearer synthetic-invalid"}, "invalid"
    )
    member = _post_invitation(
        real_db_client,
        _login(real_db_client, _MEMBER_PHONE, member_password),
        "member",
    )
    reviewer_authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    pg_database.execute(
        f"UPDATE public.\"user\" SET status='disabled' WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_status = _post_invitation(
            real_db_client, reviewer_authorization, "stale-status"
        )
    finally:
        pg_database.execute(
            f"UPDATE public.\"user\" SET status='active' WHERE id={_REVIEWER_ID}"
        )
    pg_database.execute(
        f"UPDATE public.\"user\" SET role='member' WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_role = _post_invitation(
            real_db_client, reviewer_authorization, "stale-role"
        )
    finally:
        pg_database.execute(
            f"UPDATE public.\"user\" SET role='super_admin' WHERE id={_REVIEWER_ID}"
        )
    pg_database.execute(
        f"UPDATE public.\"user\" SET tenant_id={_TENANT_ID} WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_tenant = _post_invitation(
            real_db_client, reviewer_authorization, "stale-tenant"
        )
    finally:
        pg_database.execute(
            f"UPDATE public.\"user\" SET tenant_id=NULL WHERE id={_REVIEWER_ID}"
        )
    changed_hash = hash_password(secrets.token_urlsafe(24)).replace("'", "''")
    pg_database.execute(
        f"UPDATE public.\"user\" SET password_hash='{changed_hash}' WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_credential = real_db_client.post(
            "/api/v1/auth/login",
            json={"phone": _REVIEWER_PHONE, "password": reviewer_password},
        )
    finally:
        original_hash = hash_password(reviewer_password).replace("'", "''")
        pg_database.execute(
            f"UPDATE public.\"user\" SET password_hash='{original_hash}' WHERE id={_REVIEWER_ID}"
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert member.status_code == 403
    for stale in (stale_status, stale_role, stale_tenant):
        assert stale.status_code == 401
        assert stale.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert stale_credential.status_code == 401
    assert stale_credential.json()["code"] == "INVALID_CREDENTIALS"
    assert _invitation_count(pg_database) == before


def test_CurrentReviewer闭合函数权限不可用时稳定503且零侧效(
    pg_database, real_db_client, reviewer_and_region
):
    reviewer_password, _ = reviewer_and_region
    authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    before = _invitation_count(pg_database)
    pg_database.execute(
        'REVOKE EXECUTE ON FUNCTION '
        'public.institution_onboarding_reviewer_currentness_v1(BIGINT) FROM "'
        + application_role
        + '"'
    )
    try:
        response = _post_invitation(real_db_client, authorization, "unavailable")
    finally:
        pg_database.execute(
            'GRANT EXECUTE ON FUNCTION '
            'public.institution_onboarding_reviewer_currentness_v1(BIGINT) TO "'
            + application_role
            + '"'
        )

    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert _invitation_count(pg_database) == before
    for forbidden in (
        _REVIEWER_PHONE,
        reviewer_password,
        "institution_onboarding_reviewer_currentness_v1",
        "SELECT",
        "postgresql",
    ):
        assert forbidden not in response.text


@pytest.mark.parametrize(
    ("mutation_sql", "restore_sql", "suffix"),
    (
        (
            f'UPDATE public."user" SET status=\'disabled\' WHERE id={_REVIEWER_ID}',
            f'UPDATE public."user" SET status=\'active\' WHERE id={_REVIEWER_ID}',
            "status",
        ),
        (
            f'UPDATE public."user" SET role=\'member\' WHERE id={_REVIEWER_ID}',
            f'UPDATE public."user" SET role=\'super_admin\' WHERE id={_REVIEWER_ID}',
            "role",
        ),
        (
            f'UPDATE public."user" SET tenant_id={_TENANT_ID} WHERE id={_REVIEWER_ID}',
            f'UPDATE public."user" SET tenant_id=NULL WHERE id={_REVIEWER_ID}',
            "tenant",
        ),
    ),
    ids=("status", "role", "tenant"),
)
def test_审核先取得CurrentReviewer锁则撤权等待邀请事务结束(
    pg_database,
    real_db_client,
    reviewer_and_region,
    monkeypatch,
    mutation_sql: str,
    restore_sql: str,
    suffix: str,
):
    from app.modules.institution_onboarding import api as onboarding_api

    reviewer_password, _ = reviewer_and_region
    authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    before = _invitation_count(pg_database)
    currentness_passed = threading.Event()
    allow_business_write = threading.Event()
    mutation_started = threading.Event()
    mutation_finished = threading.Event()
    mutation_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    identity_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    original_create = onboarding_api.create_invitation

    async def gated_create(*args, **kwargs):
        from sqlalchemy import text

        identity_pid.put(
            (
                await kwargs["region_session"].execute(
                    text("SELECT pg_backend_pid()")
                )
            ).scalar_one()
        )
        currentness_passed.set()
        if not await asyncio.to_thread(allow_business_write.wait, 10):
            pytest.fail("CURRENT_REVIEWER_BUSINESS_GATE_TIMEOUT")
        return await original_create(*args, **kwargs)

    monkeypatch.setattr(onboarding_api, "create_invitation", gated_create)

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        request_future = executor.submit(
            _post_invitation,
            real_db_client,
            authorization,
            f"locked-{suffix}",
        )
        assert currentness_passed.wait(10), "CURRENT_REVIEWER_GATE_NOT_REACHED"
        mutation_future = executor.submit(
            _mutate_reviewer_with_observable_pid,
            pg_database.database_url,
            mutation_sql,
            mutation_started,
            mutation_finished,
            mutation_pid,
        )
        try:
            assert mutation_started.wait(10), "CURRENT_REVIEWER_MUTATION_NOT_SENT"
            _wait_for_currentness_blocker(
                pg_database,
                mutation_pid.get(timeout=2),
                identity_pid.get(timeout=2),
            )
            assert not mutation_finished.is_set(), "CURRENT_REVIEWER_LOCK_NOT_HELD"
        finally:
            allow_business_write.set()
        response = request_future.result(timeout=20)
        mutation_future.result(timeout=20)
    finally:
        allow_business_write.set()
        executor.shutdown(wait=False, cancel_futures=True)
        pg_database.execute(restore_sql)

    assert response.status_code == 200
    assert _invitation_count(pg_database) == before + 1
    assert mutation_finished.is_set()


@pytest.mark.parametrize("outcome", ("error", "cancel"), ids=("error", "cancel"))
def test_CurrentReviewer业务异常或取消后释放锁且等待更新有界完成(
    pg_database,
    reviewer_and_region,
    outcome: str,
):
    del reviewer_and_region
    locked = threading.Event()
    release = threading.Event()
    mutation_started = threading.Event()
    mutation_finished = threading.Event()
    mutation_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    holder_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    mutation_sql = (
        f'UPDATE public."user" SET status=\'disabled\' WHERE id={_REVIEWER_ID}'
    )
    restore_sql = f'UPDATE public."user" SET status=\'active\' WHERE id={_REVIEWER_ID}'
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        holder_future = executor.submit(
            _hold_currentness_then_fail,
            outcome=outcome,
            locked=locked,
            release=release,
            holder_pid=holder_pid,
        )
        assert locked.wait(10), "CURRENT_REVIEWER_GATE_NOT_REACHED"
        mutation_future = executor.submit(
            _mutate_reviewer_with_observable_pid,
            pg_database.database_url,
            mutation_sql,
            mutation_started,
            mutation_finished,
            mutation_pid,
        )
        assert mutation_started.wait(10), "CURRENT_REVIEWER_MUTATION_NOT_SENT"
        _wait_for_currentness_blocker(
            pg_database,
            mutation_pid.get(timeout=2),
            holder_pid.get(timeout=2),
        )
        release.set()
        expected_error = asyncio.CancelledError if outcome == "cancel" else RuntimeError
        with pytest.raises(expected_error):
            holder_future.result(timeout=20)
        mutation_future.result(timeout=20)
    finally:
        release.set()
        executor.shutdown(wait=False, cancel_futures=True)
        pg_database.execute(restore_sql)

    assert mutation_finished.is_set(), "CURRENT_REVIEWER_LOCK_NOT_RELEASED"


def test_0044仅Application角色可执行且没有User底表权限(pg_database):
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    denied_roles = (
        os.environ["KG_TEST_READONLY_ROLE"],
        os.environ["KG_TEST_VERIFICATION_WRITER_ROLE"],
        os.environ["KG_TEST_DELIVERY_WORKER_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_CASE_WRITER_ROLE"],
        os.environ["KG_TEST_SLICE7_EXPORT_WORKER_ROLE"],
    )
    assert pg_database.fetch_value(
        "SELECT has_function_privilege('"
        + application_role
        + "','"
        + _CURRENTNESS_SIGNATURE
        + "','EXECUTE')"
    ) is True
    for role in denied_roles:
        assert pg_database.fetch_value(
            "SELECT has_function_privilege('"
            + role
            + "','"
            + _CURRENTNESS_SIGNATURE
            + "','EXECUTE')"
        ) is False
    assert pg_database.fetch_value(
        "SELECT has_table_privilege('"
        + application_role
        + "','public.user','SELECT,INSERT,UPDATE,DELETE')"
    ) is False


def test_0044输入拒绝稳定且不泄漏主体(pg_database, application_database):
    for value in ("NULL", "0", "-1"):
        with pytest.raises(asyncpg.DataError) as caught:
            application_database.fetch_value(
                "SELECT id FROM public.institution_onboarding_reviewer_currentness_v1("
                + value
                + ")"
            )
        rendered = str(caught.value)
        assert "INSTITUTION_REVIEWER_CURRENTNESS_INPUT_INVALID" in rendered
        assert _REVIEWER_PHONE not in rendered


def test_0044到0043对称往返且仅删除本Revision函数(pg_database):
    backend_root = Path(__file__).resolve().parents[2]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(backend_root / "app" / "migrations"),
    )
    config.set_main_option(
        "sqlalchemy.url", os.environ["KG_TEST_MIGRATION_DATABASE_URL"]
    )
    before = _invitation_count(pg_database)

    command.downgrade(config, "20260912_0043")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260912_0043"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('" + _CURRENTNESS_SIGNATURE + "') IS NULL"
    ) is True
    assert _invitation_count(pg_database) == before

    command.upgrade(config, "20260913_0044")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260913_0044"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('" + _CURRENTNESS_SIGNATURE + "') IS NOT NULL"
    ) is True
    assert _invitation_count(pg_database) == before

    command.upgrade(config, "20260914_0047")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260914_0047"
