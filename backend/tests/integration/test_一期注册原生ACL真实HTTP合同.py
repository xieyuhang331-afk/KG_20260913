from __future__ import annotations

import asyncio
import os
import re
import secrets

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.conftest import _build_alembic_config, _to_asyncpg_dsn

pytestmark = pytest.mark.integration

_SAFE_DATABASE = re.compile(r"^kg_it_reg_[a-z0-9_]{8,48}$")
_SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _database_url_for_name(database_url: str, database_name: str) -> str:
    return make_url(database_url).set(database=database_name).render_as_string(
        hide_password=False
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_Fresh原生ACL下真实注册成功且不扩大User基础权限(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_BACKEND", "local_filesystem")
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path / "private"))
    from app.core import database
    from app.core.config import get_settings
    from app.core.database import get_db_session
    from app.main import create_app

    run_id = os.environ["KG_TEST_RUN_ID"]
    suffix = re.sub(r"[^a-z0-9_]", "", run_id.lower())[-32:]
    task_database = f"kg_it_reg_{suffix}"
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    if (
        not _SAFE_DATABASE.fullmatch(task_database)
        or not _SAFE_ROLE.fullmatch(migration_role)
        or not _SAFE_ROLE.fullmatch(application_role)
    ):
        pytest.fail("REGISTRATION_NATIVE_DATABASE_SCOPE_INVALID", pytrace=False)

    admin_url = _database_url_for_name(
        os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], "postgres"
    )
    migration_url = _database_url_for_name(
        os.environ["KG_TEST_MIGRATION_DATABASE_URL"], task_database
    )
    application_url = _database_url_for_name(
        os.environ["KG_TEST_DATABASE_URL"], task_database
    )
    original_urls = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("KG_") and name.endswith("_DATABASE_URL") and value
    }
    for name, value in original_urls.items():
        monkeypatch.setenv(name, _database_url_for_name(value, task_database))
    monkeypatch.setenv("KG_DATABASE_NAME", task_database)
    get_settings.cache_clear()

    created = False

    async def prepare() -> None:
        nonlocal created
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_url))
        try:
            await admin.execute(
                f'CREATE DATABASE "{task_database}" OWNER "{migration_role}"'
            )
            created = True
            await admin.execute(
                f'COMMENT ON DATABASE "{task_database}" '
                f"IS 'kg-test-disposable:{run_id}'"
            )
        finally:
            await admin.close()

        task_admin = await asyncpg.connect(
            _to_asyncpg_dsn(
                _database_url_for_name(
                    os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
                )
            )
        )
        try:
            await task_admin.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            await task_admin.execute(
                f'ALTER SCHEMA public OWNER TO "{migration_role}"'
            )
            await task_admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        finally:
            await task_admin.close()

    async def drop() -> None:
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_url))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                task_database,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{task_database}"')
        finally:
            await admin.close()

    async def assert_native_acl() -> None:
        task_admin_url = _database_url_for_name(
            os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
        )
        admin = await asyncpg.connect(_to_asyncpg_dsn(task_admin_url))
        application = await asyncpg.connect(_to_asyncpg_dsn(application_url))
        try:
            assert not await admin.fetchval(
                "SELECT has_table_privilege($1,'public.\"user\"','SELECT')",
                application_role,
            )
            assert not await admin.fetchval(
                "SELECT has_table_privilege($1,'public.\"user\"','INSERT')",
                application_role,
            )
            assert not await admin.fetchval(
                "SELECT has_sequence_privilege($1,'public.user_id_seq','USAGE')",
                application_role,
            )
            legacy_signature = (
                "public.auth_register_member_v1(character varying,character varying)"
            )
            signature = (
                "public.auth_register_member_v2(uuid,character varying,"
                "character varying,character,character varying,jsonb)"
            )
            assert await admin.fetchval(
                "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
                application_role,
                signature,
            )
            assert not await admin.fetchval(
                "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
                application_role,
                legacy_signature,
            )
            assert not await admin.fetchval(
                "SELECT has_function_privilege('public',to_regprocedure($1),'EXECUTE')",
                signature,
            )
            assert not await admin.fetchval(
                "SELECT has_function_privilege('public',to_regprocedure($1),'EXECUTE')",
                legacy_signature,
            )
            unrelated_roles = {
                value
                for name, value in os.environ.items()
                if name.startswith("KG_TEST_")
                and name.endswith("_ROLE")
                and _SAFE_ROLE.fullmatch(value)
                and value not in {application_role, migration_role}
            }
            for role in unrelated_roles:
                assert not await admin.fetchval(
                    "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
                    role,
                    signature,
                )
                assert not await admin.fetchval(
                    "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
                    role,
                    legacy_signature,
                )
            assert await admin.fetchval(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid='public.\"user\"'::regclass AND contype='u' "
                "AND pg_get_constraintdef(oid)='UNIQUE (phone)'"
            ) == "uq_user_phone"
            probes = (
                'SELECT id FROM public."user" WHERE phone=$1 LIMIT 1',
                "SELECT nextval('public.user_id_seq')",
            )
            for statement in probes:
                with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
                    if "$1" in statement:
                        await application.fetchval(statement, "1" + "9" * 10)
                    else:
                        await application.fetchval(statement)
                assert denied.value.sqlstate == "42501"
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as insert_denied:
                await application.execute(
                    'INSERT INTO public."user" '
                    '(id,phone,password_hash,role,status) VALUES ($1,$2,$3,$4,$5)',
                    900000001,
                    "1" + "9" * 10,
                    "pbkdf2_sha256$200000$" + "0" * 32 + "$" + "1" * 64,
                    "member",
                    "active",
                )
            assert insert_denied.value.sqlstate == "42501"
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as legacy_denied:
                await application.fetchrow(
                    "SELECT * FROM public.auth_register_member_v1($1,$2)",
                    "1" + "9" * 10,
                    "invalid-digest",
                )
            assert legacy_denied.value.sqlstate == "42501"
        finally:
            await application.close()
            await admin.close()

    async def assert_legacy_direct_read_remains_denied() -> None:
        from app.modules.auth.repository import user_exists_by_phone

        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                with pytest.raises(DBAPIError) as denied:
                    await user_exists_by_phone(session, "1" + "9" * 10)
                assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                await session.rollback()
        finally:
            await engine.dispose()

    async def user_count() -> int:
        task_admin_url = _database_url_for_name(
            os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
        )
        admin = await asyncpg.connect(_to_asyncpg_dsn(task_admin_url))
        try:
            return int(await admin.fetchval('SELECT count(*) FROM public."user"'))
        finally:
            await admin.close()

    async def phone_claim_count() -> int:
        task_admin_url = _database_url_for_name(
            os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
        )
        admin = await asyncpg.connect(_to_asyncpg_dsn(task_admin_url))
        try:
            return int(
                await admin.fetchval("SELECT count(*) FROM public.identity_phone_claim")
            )
        finally:
            await admin.close()

    async def registered_row(phone: str):
        task_admin_url = _database_url_for_name(
            os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
        )
        admin = await asyncpg.connect(_to_asyncpg_dsn(task_admin_url))
        try:
            return await admin.fetchrow(
                'SELECT role,status,verify_status,tenant_id,password_hash '
                'FROM public."user" WHERE phone=$1',
                phone,
            )
        finally:
            await admin.close()

    async def exercise_real_http() -> None:
        phone = "199" + "".join(str(secrets.randbelow(10)) for _ in range(8))
        password = "Synthetic-" + secrets.token_urlsafe(18)
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        app = create_app()

        async def override_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_session
        before = await user_count()
        try:
            with TestClient(
                app, raise_server_exceptions=False, client=("127.0.0.1", 50000)
            ) as client:
                response = client.post(
                    "/api/v1/users/register",
                    json={
                        "phone": phone,
                        "password": password,
                        "role": "super_admin",
                        "status": "disabled",
                        "tenant_id": 999999,
                        "verify_status": "approved",
                    },
                )
                duplicate = client.post(
                    "/api/v1/users/register",
                    json={"phone": phone, "password": password},
                )
                login = client.post(
                    "/api/v1/auth/login",
                    json={"phone": phone, "password": password},
                )
                assert login.status_code == 200
                token = login.json()["data"]["access_token"]
                me = client.get(
                    "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
                )
            after = await user_count()
            if response.status_code != 200:
                assert after == before
                body = response.json()
                assert body["code"] == "INTERNAL_ERROR"
                assert body["message"] == "request rejected"
                assert phone not in response.text
                assert password not in response.text

            assert response.status_code == 200
            assert after == before + 1
            assert duplicate.status_code == 409
            assert duplicate.json()["code"] == "USER_EXISTS"
            assert me.status_code == 200
            assert me.json()["data"]["id"] == response.json()["data"]["id"]
            data = response.json()["data"]
            assert data["phone"] == phone
            assert data["role"] == "member"
            assert data["status"] == "active"
            assert data["verify_status"] is None
            assert data["tenant_id"] is None
            assert data["created_at"] is not None
            row = await registered_row(phone)
            assert row is not None
            assert row["role"] == "member"
            assert row["status"] == "active"
            assert row["verify_status"] is None
            assert row["tenant_id"] is None
            assert row["password_hash"] != password
            assert row["password_hash"].startswith("pbkdf2_sha256$")
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    async def exercise_concurrent_registration() -> None:
        phone = "198" + "".join(str(secrets.randbelow(10)) for _ in range(8))
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        app = create_app()

        async def override_session():
            async with factory() as session:
                yield session

        def register(client: TestClient, password: str):
            return client.post(
                    "/api/v1/users/register",
                    json={"phone": phone, "password": password},
                )

        app.dependency_overrides[get_db_session] = override_session
        before = await user_count()
        try:
            with TestClient(
                app,
                raise_server_exceptions=False,
                client=("127.0.0.2", 50000),
            ) as client:
                first, second = await asyncio.gather(
                    asyncio.to_thread(
                        register,
                        client,
                        "Synthetic-A-" + secrets.token_urlsafe(16),
                    ),
                    asyncio.to_thread(
                        register,
                        client,
                        "Synthetic-B-" + secrets.token_urlsafe(16),
                    ),
                )
            assert sorted((first.status_code, second.status_code)) == [200, 409]
            rejected = first if first.status_code == 409 else second
            assert rejected.json()["code"] == "USER_EXISTS"
            assert await user_count() == before + 1
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    async def exercise_commit_outcomes() -> None:
        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        class UnknownAfterCommit:
            def __init__(self, session):
                self.session = session

            async def execute(self, *args, **kwargs):
                return await self.session.execute(*args, **kwargs)

            async def commit(self):
                await self.session.commit()
                raise ConnectionError("synthetic commit outcome unknown")

            async def rollback(self):
                await self.session.rollback()

            async def close(self):
                await self.session.close()

        class UnknownBeforeCommit(UnknownAfterCommit):
            async def commit(self):
                raise ConnectionError("synthetic commit not attempted")

        async def request_with(wrapper_type, phone: str):
            app = create_app()

            async def override_session():
                async with factory() as session:
                    yield wrapper_type(session)

            app.dependency_overrides[get_db_session] = override_session
            try:
                def invoke():
                    with TestClient(
                        app,
                        raise_server_exceptions=False,
                        client=("127.0.0.4", 50000),
                    ) as client:
                        return client.post(
                        "/api/v1/users/register",
                        json={
                            "phone": phone,
                            "password": "Synthetic-" + secrets.token_urlsafe(18),
                        },
                    )

                return await asyncio.to_thread(invoke)
            finally:
                app.dependency_overrides.clear()

        before = await user_count()
        committed_phone = "197" + "".join(
            str(secrets.randbelow(10)) for _ in range(8)
        )
        committed = await request_with(UnknownAfterCommit, committed_phone)
        assert committed.status_code == 200
        assert await user_count() == before + 1

        absent_phone = "196" + "".join(
            str(secrets.randbelow(10)) for _ in range(8)
        )
        not_committed = await request_with(UnknownBeforeCommit, absent_phone)
        assert not_committed.status_code == 503
        assert not_committed.json()["code"] == "AUTHENTICATION_UNAVAILABLE"
        assert await user_count() == before + 1
        assert await registered_row(absent_phone) is None
        await engine.dispose()

    async def assert_migration_state(
        *, registration_exists: bool, revision: str
    ) -> None:
        task_admin_url = _database_url_for_name(
            os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
        )
        admin = await asyncpg.connect(_to_asyncpg_dsn(task_admin_url))
        try:
            assert await admin.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == revision
            assert bool(
                await admin.fetchval(
                    "SELECT to_regprocedure("
                    "'public.auth_register_member_v1(character varying,character varying)'"
                    ") IS NOT NULL"
                )
            ) is registration_exists
            assert await admin.fetchval(
                "SELECT to_regprocedure('public.auth_login_subject_v1(character varying)') "
                "IS NOT NULL"
            )
            assert await admin.fetchval(
                "SELECT to_regprocedure('public.auth_user_currentness_v1(bigint)') "
                "IS NOT NULL"
            )
        finally:
            await admin.close()

    primary: BaseException | None = None
    try:
        _run(prepare())
        config = _build_alembic_config(migration_url)
        command.upgrade(config, "head")
        command.downgrade(config, "20260909_0040")
        _run(
            assert_migration_state(
                registration_exists=False, revision="20260909_0040"
            )
        )
        command.upgrade(config, "head")
        _run(
            assert_migration_state(
                registration_exists=True, revision="20260913_0044"
            )
        )
        _run(assert_native_acl())
        _run(assert_legacy_direct_read_remains_denied())
        _run(exercise_real_http())
        _run(exercise_concurrent_registration())
        _run(exercise_commit_outcomes())
        users_before_rejected_downgrade = _run(user_count())
        claims_before_rejected_downgrade = _run(phone_claim_count())
        with pytest.raises(RuntimeError, match="DIRECT_ONBOARDING_DOWNGRADE_NONEMPTY"):
            command.downgrade(config, "20260909_0040")
        _run(
            assert_migration_state(
                registration_exists=True, revision="20260913_0044"
            )
        )
        assert _run(user_count()) == users_before_rejected_downgrade
        assert _run(phone_claim_count()) == claims_before_rejected_downgrade
    except BaseException as error:
        primary = error
        raise
    finally:
        get_settings.cache_clear()
        _run(database.dispose_database_runtimes())
        if created:
            try:
                _run(drop())
            except BaseException:
                if primary is None:
                    raise
