from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets

import asyncpg
import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.conftest import _build_alembic_config, _to_asyncpg_dsn

pytestmark = pytest.mark.integration

_SAFE_DATABASE = re.compile(r"^kg_it_auth_[a-z0-9_]{8,48}$")
_SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _database_url_for_name(database_url: str, database_name: str) -> str:
    return make_url(database_url).set(database=database_name).render_as_string(
        hide_password=False
    )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_Fresh原生ACL下登录当前身份与迁移生命周期闭环(monkeypatch) -> None:
    from app.core import database, 认证当前性
    from app.core.config import get_settings
    from app.core.database import get_db_session
    from app.main import create_app
    from app.modules.auth.service import hash_password

    run_id = os.environ["KG_TEST_RUN_ID"]
    suffix = re.sub(r"[^a-z0-9_]", "", run_id.lower())[-32:]
    task_database = f"kg_it_auth_{suffix}"
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    if (
        not _SAFE_DATABASE.fullmatch(task_database)
        or not _SAFE_ROLE.fullmatch(migration_role)
        or not _SAFE_ROLE.fullmatch(application_role)
    ):
        pytest.fail("AUTH_NATIVE_DATABASE_SCOPE_INVALID", pytrace=False)

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

    async def assert_preimage_denied() -> None:
        connection = await asyncpg.connect(_to_asyncpg_dsn(application_url))
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
                await connection.fetchval('SELECT id FROM public."user" LIMIT 1')
            assert denied.value.sqlstate == "42501"
            assert not await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n "
                "ON n.oid=p.pronamespace WHERE n.nspname='public' "
                "AND p.proname='auth_login_subject_v1')"
            )
        finally:
            await connection.close()

    async def seed_and_assert_acl() -> tuple[int, str, str]:
        admin_task_url = _database_url_for_name(
            os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
        )
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_task_url))
        phone = "199" + "".join(str(secrets.randbelow(10)) for _ in range(8))
        password = "Synthetic-" + secrets.token_urlsafe(18)
        try:
            user_id = await admin.fetchval(
                'INSERT INTO public."user"(phone,password_hash,role,status) '
                "VALUES($1,$2,'member','active') RETURNING id",
                phone,
                hash_password(password),
            )
            assert not await admin.fetchval(
                "SELECT has_table_privilege($1,'public.\"user\"','SELECT')",
                application_role,
            )
            assert not await admin.fetchval(
                "SELECT has_table_privilege($1,'public.tenant','SELECT')",
                application_role,
            )
            for signature in (
                "public.auth_login_subject_v1(character varying)",
                "public.auth_user_currentness_v1(bigint)",
            ):
                assert await admin.fetchval(
                    "SELECT has_function_privilege($1,$2,'EXECUTE')",
                    application_role,
                    signature,
                )
                assert not await admin.fetchval(
                    "SELECT has_function_privilege('public',$1,'EXECUTE')",
                    signature,
                )
                for name, role in os.environ.items():
                    if (
                        name.startswith("KG_TEST_")
                        and name.endswith("_ROLE")
                        and role not in {application_role, migration_role}
                    ):
                        assert not await admin.fetchval(
                            "SELECT has_function_privilege($1,$2,'EXECUTE')",
                            role,
                            signature,
                        )
            return int(user_id), phone, password
        finally:
            await admin.close()

    async def assert_invalid_inputs_fail_closed() -> None:
        application = await asyncpg.connect(_to_asyncpg_dsn(application_url))
        admin = await asyncpg.connect(
            _to_asyncpg_dsn(
                _database_url_for_name(
                    os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
                )
            )
        )
        try:
            before = await admin.fetchval('SELECT count(*) FROM public."user"')
            for statement, value in (
                ("SELECT * FROM public.auth_login_subject_v1($1)", "invalid"),
                ("SELECT * FROM public.auth_user_currentness_v1($1)", 0),
            ):
                with pytest.raises(asyncpg.DataError) as invalid:
                    await application.fetch(statement, value)
                assert invalid.value.sqlstate == "22023"
            after = await admin.fetchval('SELECT count(*) FROM public."user"')
            assert after == before
        finally:
            await application.close()
            await admin.close()

    async def assert_unrelated_roles_cannot_execute(
        user_id: int, phone: str
    ) -> None:
        for name in (
            "KG_TEST_READONLY_DATABASE_URL",
            "KG_TEST_DELIVERY_WORKER_DATABASE_URL",
        ):
            connection = await asyncpg.connect(
                _to_asyncpg_dsn(
                    _database_url_for_name(os.environ[name], task_database)
                )
            )
            try:
                for statement, value in (
                    ("SELECT * FROM public.auth_login_subject_v1($1)", phone),
                    (
                        "SELECT * FROM public.auth_user_currentness_v1($1)",
                        user_id,
                    ),
                ):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
                        await connection.fetch(statement, value)
                    assert denied.value.sqlstate == "42501"
            finally:
                await connection.close()

    async def seed_totp_subjects() -> dict[str, object]:
        from app.core.uuid_generator import Uuid7Generator
        from app.modules.institution_onboarding.service import OnboardingSecrets
        from app.modules.therapist_qualification.service import TherapistSecrets

        admin = await asyncpg.connect(
            _to_asyncpg_dsn(
                _database_url_for_name(
                    os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database
                )
            )
        )
        generator = Uuid7Generator()
        org_id = 990_040
        tenant_id = 990_040
        tenant_public_id = generator.generate()
        org_user_id = 990_041
        therapist_user_id = 990_042
        org_phone = "198" + "4" * 8
        therapist_phone = "197" + "4" * 8
        org_password = "Synthetic-" + secrets.token_urlsafe(18)
        therapist_password = "Synthetic-" + secrets.token_urlsafe(18)
        org_totp = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
        therapist_totp = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
        institution_invitation_id = generator.generate()
        application_id = generator.generate()
        therapist_invitation_id = generator.generate()
        therapist_id = generator.generate()
        onboarding = OnboardingSecrets()
        therapist = TherapistSecrets()
        try:
            await admin.execute(
                "INSERT INTO public.platform_org(id,org_name,org_code,org_type,status) "
                "VALUES($1,'Synthetic Auth Org','AUTH0040','county','active')",
                org_id,
            )
            await admin.execute(
                "INSERT INTO public.tenant("
                "id,org_id,tenant_code,name,type,province,city,status) "
                "VALUES($1,$2,'AUTH0040','Synthetic Auth Tenant','health_store',"
                "'Synthetic Province','Synthetic City','active')",
                tenant_id,
                org_id,
            )
            await admin.execute(
                'INSERT INTO public."user"('
                "id,phone,password_hash,role,status,tenant_id) VALUES "
                "($1,$2,$3,'org_admin','active',$4),"
                "($5,$6,$7,'therapist','active',$4)",
                org_user_id,
                org_phone,
                hash_password(org_password),
                tenant_id,
                therapist_user_id,
                therapist_phone,
                hash_password(therapist_password),
            )
            await admin.execute(
                "INSERT INTO public.institution_invitation("
                "invitation_id,institution_name,institution_type,"
                "applicant_phone_ciphertext,applicant_phone_digest,"
                "pilot_batch_code,administrative_region_id,code_digest,status,"
                "failed_attempts,expires_at,issued_by,issued_at,activated_at,version) "
                "VALUES($1,'Synthetic Auth Institution','HEALTH_STORE',$2,$3,"
                "'AUTH0040',$4,$5,'ACTIVATED',0,now()+interval '1 day',0,"
                "now(),now(),1)",
                institution_invitation_id,
                onboarding.encrypt(org_phone),
                onboarding.digest(org_phone),
                org_id,
                onboarding.digest("Synthetic-Code"),
            )
            await admin.execute(
                "INSERT INTO public.institution_onboarding_account("
                "user_id,invitation_id,totp_secret_ciphertext,totp_enabled,"
                "activated_at) VALUES($1,$2,$3,true,now())",
                org_user_id,
                institution_invitation_id,
                onboarding.encrypt(org_totp),
            )
            await admin.execute(
                "INSERT INTO public.institution_application("
                "application_id,invitation_id,applicant_user_id,institution_type,"
                "status,draft_payload,correction_fields,current_revision_no,"
                "tenant_internal_id,tenant_public_id,service_ready,created_at,"
                "updated_at,submitted_at,reviewed_at,version) "
                "VALUES($1,$2,$3,'HEALTH_STORE','APPROVED','{}'::jsonb,"
                "'[]'::jsonb,1,$4,$5,false,now(),now(),now(),now(),3)",
                application_id,
                institution_invitation_id,
                org_user_id,
                tenant_id,
                tenant_public_id,
            )
            therapist_phone_ciphertext = therapist.encrypt_pii(
                therapist_phone,
                field="invitation-phone",
                tenant_public_id=tenant_public_id,
                object_id=therapist_invitation_id,
            )
            await admin.execute(
                "INSERT INTO public.therapist_invitation("
                "invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,"
                "phone_digest,phone_digest_key_id,phone_masked,code_digest,"
                "code_digest_key_id,expires_at,status,failed_attempts,issued_by,"
                "issued_at,activated_at,version) VALUES("
                "$1,$2,$3,$4,$5,$6,'197****4444',$7,$8,now()+interval '1 day',"
                "'ACTIVATED',0,$9,now(),now(),1)",
                therapist_invitation_id,
                tenant_id,
                therapist_phone_ciphertext,
                therapist.pii_key_id,
                therapist.digest_pii(therapist_phone, field="invitation-phone"),
                therapist.digest_key_id,
                therapist.code_digest("Synthetic-Code"),
                therapist.code_key_id,
                org_user_id,
            )
            await admin.execute(
                "INSERT INTO public.therapist_profile("
                "therapist_id,user_id,tenant_id,invitation_id,status,capacity_limit,"
                "active_case_count,current_revision_no,totp_secret_ciphertext,"
                "totp_encryption_key_id,totp_enabled,activated_at,created_at,"
                "updated_at,version) VALUES($1,$2,$3,$4,'DRAFT',30,0,0,$5,$6,"
                "true,now(),now(),now(),1)",
                therapist_id,
                therapist_user_id,
                tenant_id,
                therapist_invitation_id,
                therapist.encrypt_totp(
                    therapist_totp, tenant_public_id, therapist_id
                ),
                therapist.totp_key_id,
            )
        finally:
            await admin.close()
        return {
            "org_id": org_id,
            "tenant_id": tenant_id,
            "org_user_id": org_user_id,
            "org_phone": org_phone,
            "org_password": org_password,
            "org_totp": org_totp,
            "therapist_user_id": therapist_user_id,
            "therapist_phone": therapist_phone,
            "therapist_password": therapist_password,
            "therapist_totp": therapist_totp,
        }

    async def assert_login_and_currentness(
        user_id: int,
        phone: str,
        password: str,
        totp_subjects: dict[str, object],
    ) -> None:
        from app.modules.auth import api as auth_api
        from app.modules.institution_onboarding.domain import generate_totp
        from app.modules.institution_onboarding.service import utcnow

        engine = create_async_engine(application_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        app = create_app()

        async def override_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db_session] = override_session
        monkeypatch.setattr(认证当前性, "get_session_factory", lambda: factory)
        try:
            with TestClient(
                app, raise_server_exceptions=False, client=("127.0.0.1", 50000)
            ) as client:
                original_login_user = auth_api.login_user
                original_register_user = auth_api.register_user

                async def business_service_must_not_run(*_args, **_kwargs):
                    raise AssertionError("AUTH_BUSINESS_SERVICE_ENTERED")

                monkeypatch.setattr(auth_api, "login_user", business_service_must_not_run)
                monkeypatch.setattr(
                    auth_api, "register_user", business_service_must_not_run
                )
                invalid_phones = (
                    "1٢٣٤٥٦٧٨٩٠١",
                    "1２３４５６７８９０１",
                    "1२३४५६७८९०१",
                    "12345٦٧٨٩٠١",
                    "1234567890",
                    "22345678901",
                    " 12345678901",
                    "12345678901 ",
                    "12345678901\n",
                )
                for path in ("/api/v1/auth/login", "/api/v1/users/register"):
                    for invalid_phone in invalid_phones:
                        invalid = client.post(
                            path,
                            json={
                                "phone": invalid_phone,
                                "password": "Synthetic-Password",
                            },
                        )
                        assert invalid.status_code == 422
                        assert invalid.json() == {
                            "code": "AUTH_INPUT_INVALID",
                            "message": "request rejected",
                        }
                        assert invalid.headers["cache-control"] == "no-store"
                        assert invalid_phone not in invalid.text
                        assert "Synthetic-Password" not in invalid.text
                monkeypatch.setattr(auth_api, "login_user", original_login_user)
                monkeypatch.setattr(auth_api, "register_user", original_register_user)

                before = client.post(
                    "/api/v1/auth/login",
                    json={"phone": "19900000000", "password": "Synthetic-Wrong"},
                )
                assert before.status_code == 401
                wrong = client.post(
                    "/api/v1/auth/login",
                    json={"phone": phone, "password": "Synthetic-Wrong"},
                )
                assert wrong.status_code == 401
                success = client.post(
                    "/api/v1/auth/login", json={"phone": phone, "password": password}
                )
                assert success.status_code == 200
                token = success.json()["data"]["access_token"]
                assert success.json()["data"]["user"]["id"] == user_id
                me = client.get(
                    "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
                )
                assert me.status_code == 200
                protected = client.get(
                    "/api/v1/family/member-enrollments",
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert protected.status_code == 403

                async def execute_admin(statement: str, *values) -> None:
                    admin = await asyncpg.connect(
                        _to_asyncpg_dsn(
                            _database_url_for_name(
                                os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"],
                                task_database,
                            )
                        )
                    )
                    try:
                        await admin.execute(statement, *values)
                    finally:
                        await admin.close()

                for mutate, restore in (
                    (
                        'UPDATE public."user" SET status=\'disabled\' WHERE id=$1',
                        'UPDATE public."user" SET status=\'active\' WHERE id=$1',
                    ),
                    (
                        'UPDATE public."user" SET exited_at=now() WHERE id=$1',
                        'UPDATE public."user" SET exited_at=NULL WHERE id=$1',
                    ),
                    (
                        'UPDATE public."user" SET deletion_requested_at=now() WHERE id=$1',
                        'UPDATE public."user" SET deletion_requested_at=NULL WHERE id=$1',
                    ),
                    (
                        'UPDATE public."user" SET role=\'host\' WHERE id=$1',
                        'UPDATE public."user" SET role=\'member\' WHERE id=$1',
                    ),
                    (
                        'UPDATE public."user" SET tenant_id=$2 WHERE id=$1',
                        'UPDATE public."user" SET tenant_id=NULL WHERE id=$1',
                    ),
                ):
                    values = (
                        (user_id, int(totp_subjects["tenant_id"]))
                        if "$2" in mutate
                        else (user_id,)
                    )
                    await execute_admin(mutate, *values)
                    stale = client.get(
                        "/api/v1/auth/me",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert stale.status_code == 401
                    assert phone not in stale.text and password not in stale.text
                    await execute_admin(restore, user_id)

                def totp_login(prefix: str) -> tuple[str, dict[str, str]]:
                    valid_code = generate_totp(
                        str(totp_subjects[f"{prefix}_totp"]), at=utcnow()
                    )
                    wrong_code = f"{(int(valid_code) + 1) % 1_000_000:06d}"
                    payload = {
                        "phone": str(totp_subjects[f"{prefix}_phone"]),
                        "password": str(totp_subjects[f"{prefix}_password"]),
                    }
                    missing = client.post("/api/v1/auth/login", json=payload)
                    assert missing.status_code == 401
                    invalid = client.post(
                        "/api/v1/auth/login",
                        json={**payload, "totp_code": wrong_code},
                    )
                    assert invalid.status_code == 401
                    success = client.post(
                        "/api/v1/auth/login",
                        json={**payload, "totp_code": valid_code},
                    )
                    assert success.status_code == 200
                    access_token = success.json()["data"]["access_token"]
                    headers = {"Authorization": f"Bearer {access_token}"}
                    current = client.get("/api/v1/auth/me", headers=headers)
                    assert current.status_code == 200
                    return access_token, headers

                org_token, org_headers = totp_login("org")
                org_read = client.get(
                    "/api/v1/institution/member-invitations", headers=org_headers
                )
                # Independent Slice 3 business-currentness boundary: authentication
                # succeeds, then the route's Application session is denied direct
                # access to the protected user/tenant preimage.
                assert org_read.status_code == 503
                assert org_read.json() == {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "request rejected",
                }
                await execute_admin(
                    "UPDATE public.tenant SET org_id=NULL WHERE id=$1",
                    int(totp_subjects["tenant_id"]),
                )
                org_stale = client.get(
                    "/api/v1/auth/me",
                    headers={"Authorization": f"Bearer {org_token}"},
                )
                assert org_stale.status_code == 401
                await execute_admin(
                    "UPDATE public.tenant SET org_id=$2 WHERE id=$1",
                    int(totp_subjects["tenant_id"]),
                    int(totp_subjects["org_id"]),
                )

                _, therapist_headers = totp_login("therapist")
                therapist_read = client.get(
                    "/api/v1/therapist-onboarding/profile",
                    headers=therapist_headers,
                )
                # Independent Slice 2 business-currentness boundary: login and
                # /auth/me succeed before the route performs a protected
                # user/tenant preimage read through the Application session.
                assert therapist_read.status_code == 503
                assert therapist_read.json() == {
                    "code": "DEPENDENCY_UNAVAILABLE",
                    "message": "request rejected",
                }
        finally:
            app.dependency_overrides.clear()
            await engine.dispose()

    primary: BaseException | None = None
    try:
        _run(prepare())
        command.upgrade(_build_alembic_config(migration_url), "20260906_0039")
        _run(assert_preimage_denied())
        command.upgrade(_build_alembic_config(migration_url), "head")
        user_id, phone, password = _run(seed_and_assert_acl())
        totp_subjects = _run(seed_totp_subjects())
        _run(assert_invalid_inputs_fail_closed())
        _run(assert_unrelated_roles_cannot_execute(user_id, phone))
        _run(
            assert_login_and_currentness(
                user_id, phone, password, totp_subjects
            )
        )
        _run(database.dispose_database_runtimes())
        command.downgrade(_build_alembic_config(migration_url), "20260906_0039")
        command.upgrade(_build_alembic_config(migration_url), "head")
    except BaseException as error:
        primary = error
        raise
    finally:
        get_settings.cache_clear()
        if created:
            try:
                _run(drop())
            except BaseException:
                if primary is None:
                    raise
