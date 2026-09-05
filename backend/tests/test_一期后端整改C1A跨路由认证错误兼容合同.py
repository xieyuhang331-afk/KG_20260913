from __future__ import annotations

import importlib
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from tests.test_一期后端整改C1A认证当前性限流与配置合同 import (
    access_settings as access_settings,
)

_CASE_ID = "018f0f47-e4a8-7cc8-98f2-88d31f8b0001"
# Explicit external expectations, independent of the production error catalogues.
_ROUTES = [
    pytest.param("organization", "router", "GET", "/api/v1/platform/organizations/tree", "detail", "AUTHENTICATION_REQUIRED", id="organization-read"),
    pytest.param("organization", "router", "POST", "/api/v1/platform/organizations", "detail", "AUTHENTICATION_REQUIRED", id="organization-write"),
    pytest.param("therapist_qualification", "institution_router", "GET", "/api/v1/institution/therapists", "code", "AUTHENTICATION_REQUIRED", id="slice2-read"),
    pytest.param("therapist_qualification", "institution_router", "POST", "/api/v1/institution/therapist-invitations", "code", "AUTHENTICATION_REQUIRED", id="slice2-write"),
    pytest.param("user_health", "family_health_router", "GET", "/api/v1/family/health-profile", "code", "AUTHENTICATION_REQUIRED", id="slice4-family-read"),
    pytest.param("user_health", "family_health_router", "PUT", "/api/v1/family/health-profile", "code", "AUTHENTICATION_REQUIRED", id="slice4-family-write"),
    pytest.param("user_health", "institution_health_router", "GET", "/api/v1/institution/service-cases/{case_id}/health-record", "code", "AUTHENTICATION_REQUIRED", id="slice4-institution-read"),
    pytest.param("health_assessment", "family_router", "GET", "/api/v1/family/assessments", "code", "AUTHENTICATION_REQUIRED", id="slice5-read"),
    pytest.param("health_assessment", "therapist_router", "POST", "/api/v1/therapist/service-cases/{case_id}/assessments", "code", "AUTHENTICATION_REQUIRED", id="slice5-write"),
    pytest.param("health_plan", "platform_router", "GET", "/api/v1/platform/health-plan-templates", "code", "UNAUTHENTICATED", id="slice6-read"),
    pytest.param("health_plan", "platform_router", "POST", "/api/v1/platform/health-plan-templates", "code", "UNAUTHENTICATED", id="slice6-write"),
    pytest.param("service_fulfillment", "family_router", "GET", "/api/v1/family/data-exports/{export_id}", "code", "UNAUTHENTICATED", id="slice7-read"),
    pytest.param("service_fulfillment", "family_router", "POST", "/api/v1/family/data-exports", "code", "UNAUTHENTICATED", id="slice7-write"),
]
_ROUTE_FIELDS = ("module_name", "router_name", "method", "path", "field", "auth_code")


@pytest.mark.parametrize(_ROUTE_FIELDS, _ROUTES)
@pytest.mark.parametrize("case", [
    "missing", "bad-scheme", "bad-signature", "expired", "stale",
    "authority-unavailable", "unknown-401", "unknown-503",
])
def test_D02_真实路由认证失败在业务前安全收敛(
    access_settings, monkeypatch, caplog,
    module_name, router_name, method, path, field, auth_code, case,
):
    from app.core.security import create_access_token, get_current_user_from_jwt

    module = importlib.import_module(f"app.modules.{module_name}.api")
    app = FastAPI()
    app.include_router(getattr(module, router_name))
    matches = [r for r in iter_route_contexts(app.routes) if r.path == path and method in (r.methods or ())]
    if len(matches) != 1:
        pytest.fail("C1_D02_ROUTE_SELECTION_INVALID", pytrace=False)
    route = matches[0]
    calls = []
    original = route.dependant.call

    async def observed_endpoint(**kwargs):
        calls.append("endpoint")
        return await original(**kwargs)

    async def business_session():
        calls.append("business-session")
        raise RuntimeError("C1_D02_BUSINESS_ENTRY_UNEXPECTED")

    def instrument_dependencies(dependant):
        for child in dependant.dependencies:
            if getattr(child.call, "__module__", None) == "app.core.database":
                app.dependency_overrides[child.call] = business_session
            instrument_dependencies(child)

    route.dependant.call = observed_endpoint
    instrument_dependencies(route.dependant)
    # Independent unit authority failure; real JWT parsing/currentness still runs.
    sentinel = "synthetic-vendor-private-value"
    authority = AsyncMock(
        return_value=None,
        side_effect=RuntimeError(sentinel) if case == "authority-unavailable" else None,
    )
    monkeypatch.setattr("app.core.认证当前性._read_authority", authority)
    claims = {"sub": "101", "role": "member"}
    if case == "expired":
        claims["exp"] = 0
    token = create_access_token(claims)
    headers = {"Authorization": f"Bearer {token}"}
    if case == "missing":
        headers = {}
    elif case == "bad-scheme":
        headers = {"Authorization": "Basic invalid"}
    elif case == "bad-signature":
        head, body, signature = token.split(".")
        signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        headers = {"Authorization": f"Bearer {head}.{body}.{signature}"}
    elif case in ("unknown-401", "unknown-503"):
        async def rejected_dependency():
            raise HTTPException(
                401 if case == "unknown-401" else 503,
                detail=sentinel,
                headers={"Location": sentinel, "Set-Cookie": sentinel, "X-Vendor": sentinel},
            )

        # Fault injection rejects only; never bypass currentness with an authenticated actor.
        app.dependency_overrides[get_current_user_from_jwt] = rejected_dependency
    with TestClient(app) as client:
        response = client.request(method, path.format(case_id=_CASE_ID, export_id=_CASE_ID), headers=headers, json={})
    expected_status = 503 if case in ("authority-unavailable", "unknown-503") else 401
    expected_code = "DEPENDENCY_UNAVAILABLE" if expected_status == 503 else auth_code
    expected = {field: expected_code}
    if field == "code":
        expected["message"] = "request rejected"
    if response.status_code != expected_status or response.json() != expected:
        pytest.fail("C1_D02_AUTH_STATUS_OR_ENVELOPE_MISMATCH", pytrace=False)
    if response.headers.get("Cache-Control") != "no-store":
        pytest.fail("C1_D02_AUTH_NO_STORE_MISSING", pytrace=False)
    if expected_status == 401 and response.headers.get("WWW-Authenticate") != "Bearer":
        pytest.fail("C1_D02_AUTH_BEARER_MISSING", pytrace=False)
    if calls:
        pytest.fail("C1_D02_AUTH_ENTERED_BUSINESS", pytrace=False)
    assert authority.await_count == (1 if case in ("stale", "authority-unavailable") else 0)
    if sentinel in response.text + str(response.headers) + caplog.text:
        pytest.fail("C1_D02_VENDOR_DETAIL_EXPOSED", pytrace=False)
    assert all(name not in response.headers for name in ("Location", "Set-Cookie", "X-Vendor"))


@pytest.mark.parametrize(_ROUTE_FIELDS, _ROUTES)
def test_D02_局部声明与固定认证状态及安全头一致(
    access_settings, module_name, router_name, method, path, field, auth_code,
):
    from app.main import create_app

    operation = create_app().openapi()["paths"][path][method.lower()]
    for status, code in (("401", auth_code), ("503", "DEPENDENCY_UNAVAILABLE")):
        response = operation["responses"].get(status)
        if not response:
            pytest.fail("C1_D02_AUTH_RESPONSE_DECLARATION_MISSING", pytrace=False)
        media = response.get("content", {}).get("application/json", {})
        expected = {field: code}
        if field == "code":
            expected["message"] = "request rejected"
        # 503 can also carry legitimate business codes such as COMMIT_OUTCOME_UNKNOWN.
        # Authentication examples are exact without shrinking those existing schemas.
        if media.get("examples", {}).get("authentication", {}).get("value") != expected:
            pytest.fail("C1_D02_AUTH_EXAMPLE_DECLARATION_MISSING", pytrace=False)
        if response.get("headers", {}).get("Cache-Control", {}).get("schema", {}).get("enum") != ["no-store"]:
            pytest.fail("C1_D02_CACHE_DECLARATION_MISSING", pytrace=False)
        if status == "401" and response.get("headers", {}).get("WWW-Authenticate", {}).get("schema", {}).get("enum") != ["Bearer"]:
            pytest.fail("C1_D02_BEARER_DECLARATION_MISSING", pytrace=False)


@pytest.mark.parametrize("authorization", [None, "Basic invalid"], ids=["missing", "bad-scheme"])
def test_D02_core普通认证失败也带固定安全头(access_settings, authorization):
    from app.core.security import get_current_user_from_jwt

    app = FastAPI()
    auth_dependency = Depends(get_current_user_from_jwt)

    @app.get("/probe")
    async def probe(actor=auth_dependency):
        pytest.fail("C1_D02_AUTH_ENTERED_ENDPOINT", pytrace=False)

    headers = {} if authorization is None else {"Authorization": authorization}
    with TestClient(app) as client:
        response = client.get("/probe", headers=headers)
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}
    if response.headers.get("WWW-Authenticate") != "Bearer" or response.headers.get("Cache-Control") != "no-store":
        pytest.fail("C1_D02_CORE_AUTH_HEADERS_MISSING", pytrace=False)
