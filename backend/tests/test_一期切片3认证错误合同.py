from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import (
    get_db_session,
    get_institution_onboarding_reader_session,
    get_member_enrollment_reader_session,
)
from app.core.security import create_access_token
from app.modules.member_enrollment.api import institution_router


def _app_with_sessions(session_calls: list[str], *, unavailable: bool = False) -> FastAPI:
    app = FastAPI()
    app.include_router(institution_router)

    async def application_session():
        session_calls.append("application")
        if unavailable:
            raise RuntimeError("safe dependency unavailable")
        yield object()

    async def member_reader_session():
        session_calls.append("member_reader")
        if unavailable:
            raise RuntimeError("safe dependency unavailable")
        yield object()

    async def institution_reader_session():
        session_calls.append("institution_reader")
        if unavailable:
            raise RuntimeError("safe dependency unavailable")
        yield object()

    app.dependency_overrides[get_db_session] = application_session
    app.dependency_overrides[get_member_enrollment_reader_session] = member_reader_session
    app.dependency_overrides[
        get_institution_onboarding_reader_session
    ] = institution_reader_session
    return app


def test_缺失或无效授权固定401且不进入业务数据库() -> None:
    cases = (
        {},
        {"Authorization": "Basic invalid"},
        {"Authorization": "Bearer invalid"},
        {
            "Authorization": "Bearer "
            + create_access_token({"sub": "71", "role": "org_admin", "exp": 0})
        },
    )

    for headers in cases:
        session_calls: list[str] = []
        response = TestClient(_app_with_sessions(session_calls)).get(
            "/api/v1/institution/member-invitations", headers=headers
        )

        assert response.status_code == 401
        assert response.json() == {
            "code": "AUTHENTICATION_REQUIRED",
            "message": "request rejected",
        }
        assert session_calls == []


def test_有效令牌但角色不足固定403() -> None:
    session_calls: list[str] = []
    token = create_access_token({"sub": "71", "role": "member"})

    response = TestClient(_app_with_sessions(session_calls)).get(
        "/api/v1/institution/member-invitations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"


def test_有效令牌后真实依赖不可用保持503() -> None:
    session_calls: list[str] = []
    token = create_access_token(
        {"sub": "71", "role": "org_admin", "tenant_id": 9, "org_id": 100}
    )

    response = TestClient(_app_with_sessions(session_calls, unavailable=True)).get(
        "/api/v1/institution/member-invitations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "request rejected",
    }
    assert session_calls
