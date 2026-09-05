from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.core.database import (
    get_db_session,
    get_member_enrollment_reader_session,
    get_slice4_clinical_reader_session,
    get_slice4_institution_reader_session,
)
from app.core.security import create_access_token
from app.modules.user_health.api import institution_health_router, readiness_router


CASE_ID = "00000000-0000-7000-8000-000000000041"
READ_ROUTES = (
    f"/api/v1/institution/service-cases/{CASE_ID}/health-record",
    f"/api/v1/institution/service-cases/{CASE_ID}/detection-reports",
    f"/api/v1/institution/service-cases/{CASE_ID}/health-indicators/latest",
    f"/api/v1/service-cases/{CASE_ID}/assessment-readiness",
)


@pytest.fixture(autouse=True)
def _synthetic_runtime_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KG_DATABASE_PASSWORD", "slice4-http-contract-db-password")
    monkeypatch.setenv(
        "KG_JWT_SECRET_KEY", "slice4-http-contract-jwt-key-at-least-32-bytes"
    )
    get_settings.cache_clear()
    rows = {
        user_id: SimpleNamespace(
            id=user_id, role=role, tenant_id=tenant_id, tenant_org_id=None,
            status="active", exited_at=None, deletion_requested_at=None,
        )
        for user_id, role, tenant_id in (
            (94001, "org_admin", 94002), (94011, "super_admin", None),
        )
    }
    monkeypatch.setattr("app.core.认证当前性._read_authority", AsyncMock(side_effect=rows.get))
    yield
    get_settings.cache_clear()


class _RowsResult:
    def __init__(self, rows: tuple[dict[str, object], ...]) -> None:
        self._rows = rows

    def mappings(self):
        return self._rows


class _Session:
    def __init__(
        self,
        calls: list[str],
        name: str,
        *,
        case_exists: bool,
        scope_forbidden: bool,
    ) -> None:
        self._calls = calls
        self._name = name
        self._case_exists = case_exists
        self._scope_forbidden = scope_forbidden

    async def execute(self, statement, parameters=None):
        del statement, parameters
        self._calls.append(f"execute:{self._name}")
        if self._name == "member_reader":
            rows = ({"case_id": CASE_ID},) if self._case_exists else ()
            return _RowsResult(rows)
        if self._name == "institution" and self._scope_forbidden:
            raise RuntimeError("SLICE4_INSTITUTION_SCOPE_FORBIDDEN")
        raise RuntimeError("safe dependency unavailable")


def _app(
    calls: list[str],
    *,
    case_exists: bool = False,
    scope_forbidden: bool = False,
    dependency_unavailable: bool = False,
) -> FastAPI:
    app = FastAPI()
    app.include_router(institution_health_router)
    app.include_router(readiness_router)

    def dependency(name: str):
        async def value():
            calls.append(f"dependency:{name}")
            if dependency_unavailable:
                raise RuntimeError("safe dependency unavailable")
            yield _Session(
                calls,
                name,
                case_exists=case_exists,
                scope_forbidden=scope_forbidden,
            )

        return value

    app.dependency_overrides[get_db_session] = dependency("application")
    app.dependency_overrides[get_member_enrollment_reader_session] = dependency(
        "member_reader"
    )
    app.dependency_overrides[get_slice4_clinical_reader_session] = dependency("clinical")
    app.dependency_overrides[get_slice4_institution_reader_session] = dependency(
        "institution"
    )
    return app


def _authorization(role: str, *, tenant_id: int | None = None) -> dict[str, str]:
    claims: dict[str, object] = {"sub": "94011" if role == "super_admin" else "94001", "role": role}
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    return {"Authorization": f"Bearer {create_access_token(claims)}"}


def test_四个只读接口缺失或无效认证固定401且不进入数据库依赖() -> None:
    invalid_headers = (
        {},
        {"Authorization": "Basic invalid"},
        {"Authorization": "Bearer invalid"},
        {
            "Authorization": "Bearer "
            + create_access_token({"sub": "94001", "role": "org_admin", "exp": 0})
        },
    )

    for route in READ_ROUTES:
        for headers in invalid_headers:
            calls: list[str] = []
            response = TestClient(_app(calls)).get(route, headers=headers)

            assert response.status_code == 401
            assert response.json() == {
                "code": "AUTHENTICATION_REQUIRED",
                "message": "request rejected",
            }
            assert calls == []


def test_四个只读接口角色和租户越权固定403() -> None:
    for route in READ_ROUTES:
        role_calls: list[str] = []
        role_response = TestClient(_app(role_calls, case_exists=True)).get(
            route, headers=_authorization("super_admin")
        )
        assert role_response.status_code == 403

        tenant_calls: list[str] = []
        tenant_response = TestClient(
            _app(tenant_calls, case_exists=True, scope_forbidden=True)
        ).get(route, headers=_authorization("org_admin", tenant_id=94002))
        assert tenant_response.status_code == 403
        assert tenant_response.json()["code"] == "INSTITUTION_SCOPE_FORBIDDEN"


def test_四个只读接口合法但不存在的UUIDv7固定404() -> None:
    for route in READ_ROUTES:
        response = TestClient(_app([], case_exists=False)).get(
            route, headers=_authorization("org_admin", tenant_id=94002)
        )
        assert response.status_code == 404
        assert response.json()["code"] == "SERVICE_CASE_NOT_FOUND"


def test_四个只读接口非法参数保持422() -> None:
    for route in READ_ROUTES:
        response = TestClient(_app([])).get(
            route.replace(CASE_ID, "not-a-uuid"),
            headers=_authorization("org_admin", tenant_id=94002),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_REQUEST"


def test_四个只读接口真实依赖不可用保持503() -> None:
    for route in READ_ROUTES:
        response = TestClient(_app([], dependency_unavailable=True)).get(
            route, headers=_authorization("org_admin", tenant_id=94002)
        )
        assert response.status_code == 503
        assert response.json() == {
            "code": "DEPENDENCY_UNAVAILABLE",
            "message": "request rejected",
        }
