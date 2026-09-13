from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.integration.conftest import _get_application_database_url

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("path", "field", "auth_code"), [
    pytest.param("/api/v1/platform/organizations/tree", "detail", "AUTHENTICATION_REQUIRED", id="organization"),
    pytest.param("/api/v1/institution/therapists", "code", "AUTHENTICATION_REQUIRED", id="slice2"),
    pytest.param("/api/v1/family/health-profile", "code", "AUTHENTICATION_REQUIRED", id="slice4"),
    pytest.param("/api/v1/family/assessments", "code", "AUTHENTICATION_REQUIRED", id="slice5"),
    pytest.param("/api/v1/platform/health-plan-templates", "code", "UNAUTHENTICATED", id="slice6"),
    pytest.param("/api/v1/family/data-exports/{export_id}", "code", "UNAUTHENTICATED", id="slice7"),
])
def test_C1A_D02_真实Authority到六族认证外壳且业务零进入(
    pg_database, real_db_client, currentness_authority, monkeypatch, path, field, auth_code,
):
    from fastapi.routing import iter_route_contexts

    from app.core.security import create_access_token

    subject = 9786103
    if pg_database.fetch_value(f'SELECT count(*) FROM public."user" WHERE id={subject}'):
        pytest.fail("C1A_D02_SYNTHETIC_FIXTURE_COLLISION", pytrace=False)
    pg_database.execute(
        'INSERT INTO public."user" (id,phone,password_hash,role,status) '
        f"VALUES ({subject},'19900006103','synthetic-not-a-password','member','active')"
    )
    try:
        token = create_access_token({"sub": str(subject), "role": "member"})
        headers = {"Authorization": f"Bearer {token}"}
        positive = real_db_client.get("/api/v1/auth/me", headers=headers)
        if positive.status_code != 200:
            pytest.fail("C1A_D02_AUTHORITY_POSITIVE_FAILED", pytrace=False)
        before_users = pg_database.fetch_value('SELECT count(*) FROM public."user"')
        before_audit = pg_database.fetch_value("SELECT count(*) FROM public.operation_log")
        route = next(r for r in iter_route_contexts(real_db_client.app.routes) if r.path == path and "GET" in (r.methods or ()))
        request_path = path.format(export_id="018f0f47-e4a8-7cc8-98f2-88d31f8b0001")
        calls = []

        async def forbidden_business():
            calls.append("business-session")
            raise RuntimeError("C1A_D02_BUSINESS_ENTRY_UNEXPECTED")

        original_endpoint = route.dependant.call

        async def observed_endpoint(**kwargs):
            calls.append("endpoint")
            return await original_endpoint(**kwargs)

        def unavailable_factory():
            raise ConnectionError("synthetic-authority-unavailable")

        def observe_business(dependant, scope):
            for child in dependant.dependencies:
                if getattr(child.call, "__module__", None) == "app.core.database":
                    scope.setitem(real_db_client.app.dependency_overrides, child.call, forbidden_business)
                observe_business(child, scope)

        with monkeypatch.context() as scope:
            scope.setattr(route.dependant, "call", observed_endpoint)
            observe_business(route.dependant, scope)
            pg_database.execute(f'UPDATE public."user" SET status=\'disabled\' WHERE id={subject}')
            query_start = len(currentness_authority)
            stale = real_db_client.get(request_path, headers=headers)
            if not any("select" in s and "user" in s for s in currentness_authority[query_start:]):
                pytest.fail("C1A_D02_REAL_AUTHORITY_QUERY_NOT_EXECUTED", pytrace=False)
            # Explicit fault injection, not an ACL revoke or a claim of database outage.
            scope.setattr("app.core.认证当前性.get_session_factory", unavailable_factory)
            unavailable = real_db_client.get(request_path, headers=headers)
        for response, status, code in (
            (stale, 401, auth_code), (unavailable, 503, "DEPENDENCY_UNAVAILABLE"),
        ):
            body = response.json()
            if (
                response.status_code != status
                or set(body) != {"code", "message", "request_id", "retryable", "field_errors"}
                or body["code"] != code
                or body["message"] != "request rejected"
                or body["request_id"] != response.headers.get("x-request-id")
                or body["retryable"] is not (status == 503)
                or body["field_errors"] != []
            ):
                pytest.fail("C1A_D02_FRESH_AUTH_ENVELOPE_MISMATCH", pytrace=False)
            if response.headers.get("Cache-Control") != "no-store, private":
                pytest.fail("C1A_D02_FRESH_AUTH_NO_STORE_MISSING", pytrace=False)
            if response.headers.get("Pragma") != "no-cache":
                pytest.fail("C1A_D02_FRESH_AUTH_PRAGMA_MISSING", pytrace=False)
            if status == 401 and response.headers.get("WWW-Authenticate") != "Bearer":
                pytest.fail("C1A_D02_FRESH_AUTH_BEARER_MISSING", pytrace=False)
            if any(value in response.text for value in (str(subject), token, "19900006103", "synthetic-authority-unavailable")):
                pytest.fail("C1A_D02_FRESH_PUBLIC_OUTPUT_UNSAFE", pytrace=False)
        if calls:
            pytest.fail("C1A_D02_FRESH_ENTERED_BUSINESS", pytrace=False)
        assert pg_database.fetch_value('SELECT count(*) FROM public."user"') == before_users
        assert pg_database.fetch_value("SELECT count(*) FROM public.operation_log") == before_audit
    finally:
        pg_database.execute(f'DELETE FROM public."user" WHERE id={subject}')


@pytest.fixture
def currentness_authority(pg_database, monkeypatch):
    from app.core import 认证当前性

    engine = create_async_engine(_get_application_database_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before_execute(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.lower())

    monkeypatch.setattr(认证当前性, "get_session_factory", lambda: factory)
    try:
        yield statements
    finally:
        asyncio.run(engine.dispose())


@pytest.mark.parametrize("change", ["status", "exit", "deletion", "role", "missing"])
def test_C1A_R12_正式应用身份最小查询与旧令牌即时失效(pg_database, real_db_client, currentness_authority, change):
    from app.core.security import create_access_token

    subject = 9786101
    if pg_database.fetch_value(f'SELECT count(*) FROM public."user" WHERE id={subject}'):
        pytest.fail("C1A_SYNTHETIC_FIXTURE_COLLISION", pytrace=False)
    pg_database.execute(
        f'INSERT INTO public."user" (id,phone,password_hash,role,status) '
        f"VALUES ({subject},'19900006101','synthetic-not-a-password','member','active')"
    )
    try:
        token = create_access_token({"sub": str(subject), "role": "member"})
        headers = {"Authorization": f"Bearer {token}"}
        response = real_db_client.get("/api/v1/auth/me", headers=headers)
        if response.status_code != 200:
            pytest.fail("C1A_REAL_AUTHORITY_POSITIVE_PATH_FAILED", pytrace=False)
        if change == "missing":
            pg_database.execute(f'DELETE FROM public."user" WHERE id={subject}')
        else:
            assignments = {"status": "status='disabled'", "exit": "exited_at=NOW()",
                           "deletion": "deletion_requested_at=NOW()", "role": "role='therapist'"}
            pg_database.execute(f'UPDATE public."user" SET {assignments[change]} WHERE id={subject}')
        response = real_db_client.get("/api/v1/auth/me", headers=headers)
        if response.status_code != 401:
            pytest.fail("C1A_REAL_CURRENTNESS_NOT_ENFORCED", pytrace=False)
        assert response.headers["Cache-Control"] == "no-store, private"
        forbidden = ("password_hash", "real_name", "id_card", "phone", "select *")
        if any(part in statement for part in forbidden for statement in currentness_authority):
            pytest.fail("C1A_AUTHORITY_PROJECTION_NOT_MINIMAL", pytrace=False)
        assert any("set transaction read only" in statement for statement in currentness_authority)
    finally:
        pg_database.execute(f'DELETE FROM public."user" WHERE id={subject}')


def test_C1A_R12_正式HTTP注册登录与429闭环(pg_database, real_db_client, currentness_authority):
    phone = "19900006102"
    password = "Synthetic-C1A-Password"
    if pg_database.fetch_value(f"SELECT count(*) FROM public.\"user\" WHERE phone='{phone}'"):
        pytest.fail("C1A_SYNTHETIC_FIXTURE_COLLISION", pytrace=False)
    subject = None
    try:
        response = real_db_client.post("/api/v1/users/register", json={"phone": phone, "password": password})
        if response.status_code != 200:
            pytest.fail("C1A_FRESH_REGISTER_REJECTED", pytrace=False)
        subject = response.json()["data"]["id"]
        response = real_db_client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
        if response.status_code != 200:
            pytest.fail("C1A_FRESH_LOGIN_REJECTED", pytrace=False)
        token = response.json()["data"]["access_token"]
        me = real_db_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        for _ in range(5):
            response = real_db_client.post("/api/v1/auth/login", json={"phone": phone, "password": "Wrong-Synthetic-Password"})
            if response.status_code != 401:
                pytest.fail("C1A_FRESH_BAD_PASSWORD_BOUNDARY", pytrace=False)
        limited = real_db_client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
        assert limited.status_code == 429
        assert limited.headers["Cache-Control"] == "no-store, private"
        assert int(limited.headers["Retry-After"]) > 0
        if phone in limited.text or password in limited.text or token in limited.text:
            pytest.fail("C1A_FRESH_LIMIT_RESPONSE_UNSAFE", pytrace=False)
    finally:
        if subject is not None:
            pg_database.execute(
                "DELETE FROM public.identity_phone_claim "
                f"WHERE user_id={int(subject)}"
            )
            pg_database.execute(f'DELETE FROM public."user" WHERE id={int(subject)}')
        else:
            pg_database.execute(f"DELETE FROM public.\"user\" WHERE phone='{phone}'")
