from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException


@pytest.mark.parametrize(("job", "next_job", "profile"), [
    ("backend-unit", "backend-integration", "test"),
    ("backend-integration", "frontend-build", "ci_ephemeral"),
])
def test_C1A_R14_CI双Job生成独立限流材料并闭合Profile(job, next_job, profile):
    source = (Path(__file__).resolve().parents[2] / ".github/workflows/p2-foundation-ci.yml").read_text("utf-8")
    block = source.split(f"  {job}:", 1)[1].split(f"  {next_job}:", 1)[0]
    assert '"KG_AUTH_RATE_LIMIT_HMAC_KEY": secrets.token_urlsafe(64),' in block, "C1A_CI_RATE_LIMIT_KEY_MISSING"
    assert f'"KG_ENV": "{profile}",' in block, "C1A_CI_PROFILE_MISSING"
    mask_loop = 'for value in values.values():' if job == "backend-unit" else 'for value in (\n              *values.values(),'
    assert mask_loop in block and 'print(f"::add-mask::{value}")' in block
    assert 'for name, value in values.items():' in block and 'GITHUB_ENV' in block


@pytest.fixture
def access_settings(monkeypatch, tmp_path):
    from app.core.config import get_settings

    monkeypatch.setenv("KG_DATABASE_PASSWORD", secrets.token_urlsafe(32))
    monkeypatch.setenv("KG_JWT_SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("KG_JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("KG_AUTH_RATE_LIMIT_HMAC_KEY", secrets.token_urlsafe(32))
    for name in ("KG_SLICE5_CURSOR_SIGNING_KEY", "KG_SLICE7_CURSOR_SIGNING_KEY",
                 "KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY", "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY"):
        monkeypatch.setenv(name, secrets.token_urlsafe(48))
    monkeypatch.setenv("KG_ENV", "test")
    monkeypatch.setenv("KG_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "120")
    monkeypatch.setenv("KG_FILE_STORAGE_BACKEND", "local_filesystem")
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _claims():
    now = int(datetime.now(UTC).timestamp())
    return {
        "iss": "kanglin", "aud": "kanglin-phase1-api", "typ": "access",
        "sub": "101", "role": "member", "iat": now, "nbf": now,
        "exp": now + 7200, "jti": str(uuid4()),
    }


@pytest.mark.parametrize("case", [
    "missing", "bad-scheme", "bad-signature", "stale",
    "authority-unavailable", "unknown-401",
])
def test_C1A_D01_Slice3真实ASGI认证外壳与固定安全头(
    access_settings, monkeypatch, caplog, case
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.database import get_db_session, get_member_enrollment_reader_session
    from app.core.security import create_access_token, get_current_user_from_jwt
    from app.modules.member_enrollment import api

    events = []
    calls = []
    sentinel = "synthetic-private-material"
    failure = RuntimeError(sentinel) if case == "authority-unavailable" else None
    _authority_factory(monkeypatch, None, events, failure=failure)
    app = FastAPI()
    app.include_router(api.family_router)

    async def business_session():
        calls.append("session")
        yield object()

    def business_service(*args, **kwargs):
        calls.append("service")
        raise RuntimeError("unexpected-business-entry")

    app.dependency_overrides[get_db_session] = business_session
    app.dependency_overrides[get_member_enrollment_reader_session] = business_session
    monkeypatch.setattr(api, "MemberEnrollmentService", business_service)
    token = create_access_token({"sub": "101", "role": "member"})
    headers = {"Authorization": f"Bearer {token}"}
    if case == "missing":
        headers = {}
    elif case == "bad-scheme":
        headers = {"Authorization": "Basic invalid"}
    elif case == "bad-signature":
        head, payload, signature = token.split(".")
        changed_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        headers = {"Authorization": f"Bearer {head}.{payload}.{changed_signature}"}
    elif case == "unknown-401":
        async def unknown_auth_error():
            raise HTTPException(401, detail=sentinel, headers={
                "Location": sentinel, "Set-Cookie": sentinel, "X-Vendor": sentinel,
            })

        # Unit fault injection at the auth boundary; no Integration authority override.
        app.dependency_overrides[get_current_user_from_jwt] = unknown_auth_error
    with TestClient(app) as client:
        response = client.get("/api/v1/family/member-enrollments", headers=headers)
    expected_status = 503 if case == "authority-unavailable" else 401
    expected_code = (
        "DEPENDENCY_UNAVAILABLE" if expected_status == 503 else
        "ACCESS_TOKEN_STALE" if case == "stale" else "AUTHENTICATION_REQUIRED"
    )
    if (response.status_code != expected_status or
            response.json() != {"code": expected_code, "message": "request rejected"}):
        pytest.fail("C1_SLICE3_AUTH_ENVELOPE_MISMATCH", pytrace=False)
    if response.headers.get("Cache-Control") != "no-store":
        pytest.fail("C1_SLICE3_AUTH_NO_STORE_MISSING", pytrace=False)
    if expected_status == 401 and response.headers.get("WWW-Authenticate") != "Bearer":
        pytest.fail("C1_SLICE3_AUTH_BEARER_MISSING", pytrace=False)
    assert calls == []
    assert all(name not in response.headers for name in ("Location", "Set-Cookie", "X-Vendor"))
    if sentinel in response.text + str(response.headers) + caplog.text:
        pytest.fail("C1_SLICE3_AUTH_DETAIL_EXPOSED", pytrace=False)


def test_C1A_D01_Slice3认证码声明独立闭合(access_settings):
    from app.main import create_app

    operation = create_app().openapi()["paths"]["/api/v1/family/member-enrollments"]["get"]
    assert set(operation["x-symbolic-error-codes"]["401"]) == {
        "AUTHENTICATION_REQUIRED", "ACCESS_TOKEN_STALE",
    }
    response = operation["responses"]["401"]
    assert set(response["content"]["application/json"]["schema"]["properties"]["code"]["enum"]) == {
        "AUTHENTICATION_REQUIRED", "ACCESS_TOKEN_STALE",
    }
    assert response["headers"]["WWW-Authenticate"]["schema"]["enum"] == ["Bearer"]
    assert response["headers"]["Cache-Control"]["schema"]["enum"] == ["no-store"]


def _signed(settings, payload, *, raw_payload=None, header=None):
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    head = encode(json.dumps(header or {"alg": "HS256", "typ": "JWT"}).encode())
    body = encode(raw_payload if raw_payload is not None else json.dumps(payload).encode())
    unsigned = f"{head}.{body}"
    signature = hmac.new(settings.jwt_secret_key.encode(), unsigned.encode(), hashlib.sha256).digest()
    return f"{unsigned}.{encode(signature)}"


def _assert_rejected(token):
    from app.core.security import decode_access_token

    try:
        decode_access_token(token)
    except HTTPException as error:
        if error.status_code != 401:
            pytest.fail("C1A_WRONG_AUTH_STATUS", pytrace=False)
    else:
        pytest.fail("C1A_INVALID_ACCESS_PROFILE_ACCEPTED", pytrace=False)


def test_C1A_R01_签发完整Access用途与随机标识(access_settings):
    from app.core.security import create_access_token, decode_access_token

    first = decode_access_token(create_access_token({"sub": "101", "role": "member"}))
    second = decode_access_token(create_access_token({"sub": "101", "role": "member"}))
    if not {"aud", "nbf", "jti"}.issubset(first):
        pytest.fail("C1A_ISSUED_PROFILE_INCOMPLETE", pytrace=False)
    assert first["aud"] == "kanglin-phase1-api"
    assert first["nbf"] == first["iat"]
    assert first["exp"] - first["iat"] == 7200
    assert UUID(first["jti"]).version == 4
    assert first["jti"] != second["jti"]


@pytest.mark.parametrize("field", ["iss", "aud", "typ", "sub", "role", "iat", "nbf", "jti"])
def test_C1A_R02_必要Claim不可缺失(access_settings, field):
    payload = _claims()
    del payload[field]
    _assert_rejected(_signed(access_settings, payload))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("iss", "untrusted"), ("aud", "other-purpose"), ("typ", "setup"),
        ("sub", "0"), ("sub", "01"), ("sub", 101), ("sub", True),
        ("role", "unrecognized"), ("iat", True), ("nbf", False),
        ("jti", "not-a-uuid"), ("jti", "00000000-0000-0000-0000-000000000000"),
    ],
    ids=["issuer", "audience", "purpose", "zero-sub", "noncanonical-sub", "integer-sub",
         "bool-sub", "unknown-role", "bool-iat", "bool-nbf", "invalid-jti", "nil-jti"],
)
def test_C1A_R02_合法签名不替代用途结构校验(access_settings, field, value):
    payload = _claims()
    payload[field] = value
    _assert_rejected(_signed(access_settings, payload))


@pytest.mark.parametrize("case", ["future-iat", "future-nbf", "float-exp", "string-exp", "excess-lifetime"])
def test_C1A_R03_时钟类型和最大寿命闭合(access_settings, case):
    payload = _claims()
    if case == "future-iat":
        payload["iat"] += 60
    elif case == "future-nbf":
        payload["nbf"] += 60
    elif case == "float-exp":
        payload["exp"] = float(payload["exp"])
    elif case == "string-exp":
        payload["exp"] = str(payload["exp"])
    else:
        payload["exp"] += 1
    _assert_rejected(_signed(access_settings, payload))


def test_C1A_R03_重复JSON键不得取最后值(access_settings):
    payload = json.dumps(_claims())
    duplicate = ('{"iss":"other",' + payload[1:]).encode()
    _assert_rejected(_signed(access_settings, {}, raw_payload=duplicate))


def test_C1A_R03_非字典JSON安全401(access_settings):
    _assert_rejected(_signed(access_settings, []))


def test_C1A_R03_正常完整profile保持通过(access_settings):
    from app.core.security import decode_access_token

    result = decode_access_token(_signed(access_settings, _claims()))
    assert result["role"] == "member"


@pytest.mark.parametrize("segment", ["header", "payload"], ids=["nested-header", "nested-payload"])
@pytest.mark.asyncio
async def test_C1A_R03_深层JSON在长度限内真实ASGI安全401(access_settings, monkeypatch, caplog, segment):
    from fastapi import Depends, FastAPI

    from app.core import 认证当前性
    from app.core.security import get_current_user_from_jwt

    nested = b"[" * 1500 + b"0" + b"]" * 1500
    try:
        json.loads(nested)
    except RecursionError:
        pass
    else:
        pytest.fail("C1A_JSON_DEPTH_NOT_REPRODUCIBLE", pytrace=False)

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    header = nested if segment == "header" else b'{"alg":"HS256","typ":"JWT"}'
    payload = nested if segment == "payload" else json.dumps(_claims()).encode("utf-8")
    # Deliberately not signed: the real decoder parses these inputs before HMAC.
    token = ".".join((encode(header), encode(payload), encode(bytes(32))))
    if len(token) > 8192:
        pytest.fail("C1A_DEPTH_PROBE_EXCEEDS_TOKEN_LIMIT", pytrace=False)
    calls = {"authority": 0, "business": 0}

    async def authority(_user_id):
        calls["authority"] += 1
        raise RuntimeError("C1A_DEPTH_AUTHORITY_MUST_NOT_ENTER")

    monkeypatch.setattr(认证当前性, "_read_authority", authority)
    app = FastAPI()
    dependency = Depends(get_current_user_from_jwt)

    @app.get("/depth-probe")
    async def protected(current_user=dependency):
        calls["business"] += 1
        return {"ok": True}

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/depth-probe",
        "raw_path": b"/depth-probe", "query_string": b"", "root_path": "",
        "headers": [(b"authorization", ("Bearer " + token).encode("ascii"))],
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 50000),
    }
    escaped_recursion = False
    try:
        await app(scope, receive, send)
    except RecursionError:
        escaped_recursion = True
    if calls != {"authority": 0, "business": 0}:
        pytest.fail("C1A_DEPTH_INPUT_ENTERED_AUTHORITY_OR_BUSINESS", pytrace=False)
    if escaped_recursion:
        pytest.fail("C1A_NESTED_JWT_JSON_ESCAPED_401", pytrace=False)
    starts = [message for message in messages if message["type"] == "http.response.start"]
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    if len(starts) != 1 or starts[0]["status"] != 401:
        pytest.fail("C1A_DEPTH_RESPONSE_NOT_401", pytrace=False)
    headers = dict(starts[0]["headers"])
    if (headers.get(b"www-authenticate") != b"Bearer" or headers.get(b"cache-control") != b"no-store"
            or headers.get(b"content-type") != b"application/json"
            or json.loads(body) != {"detail": "Invalid or expired token"}):
        pytest.fail("C1A_DEPTH_RESPONSE_UNSAFE_CONTRACT", pytrace=False)
    public = body.decode("utf-8") + caplog.text
    if any(value in public for value in (token, nested.decode("ascii"), "RecursionError", "Traceback")):
        pytest.fail("C1A_DEPTH_RESPONSE_OR_LOG_INPUT_DISCLOSED", pytrace=False)


def _authority_row(**changes):
    fields = dict(id=101, role="member", tenant_id=None, status="active",
                  exited_at=None, deletion_requested_at=None, tenant_org_id=None)
    fields.update(changes)
    return SimpleNamespace(**fields)


def _authority_factory(monkeypatch, row, events, *, failure=None):
    from app.core import database, 认证当前性

    class Session:
        async def execute(self, statement):
            events.append("authority-execute")
            if failure:
                raise failure
            return SimpleNamespace(one_or_none=lambda: row)

        async def rollback(self):
            events.append("authority-rollback")

        async def close(self):
            events.append("authority-close")

    def factory():
        events.append("authority-open")
        return Session()

    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    monkeypatch.setattr(认证当前性, "get_session_factory", lambda: factory)


_SCOPE_AUTHORITY_FACTS = {
    "super_admin": ("super_admin", None, None),
    "sys_admin": ("sys_admin", None, None),
    "expert": ("expert", None, None),
    "org_operator": ("org_operator", None, None),
    "therapist": ("therapist", None, None),
    "host": ("host", None, None),
    "member": ("member", None, None),
    "org_admin": ("org_admin", 201, 301),
    "province_admin": ("province_admin", None, None),
    "city_admin": ("city_admin", None, None),
}
_SCOPE_ALLOWED = {
    "org_admin": {"org_id"}, "province_admin": {"province"},
    "city_admin": {"province", "city"},
}


async def _scope_http_response(settings, monkeypatch, role, extra, *, onboarding=False):
    from fastapi import FastAPI

    from app.modules.auth import api

    # Authority is independently predefined, never reconstructed from the JWT.
    authority_role, tenant, org = _SCOPE_AUTHORITY_FACTS[role]
    if onboarding:
        tenant, org = None, None
    events = []
    _authority_factory(monkeypatch, _authority_row(
        role=authority_role, tenant_id=tenant, tenant_org_id=org,
    ), events)
    settings.auth_context_map = {"101": {"province": "GD", "city": "GZ"}}
    payload = _claims()
    payload["role"] = role
    if role == "org_admin" and not onboarding:
        payload.update(tenant_id=201, org_id=301)
    if role in ("province_admin", "city_admin"):
        payload["province"] = "GD"
    if role == "city_admin":
        payload["city"] = "GZ"
    payload.update(extra)
    token = _signed(settings, payload)
    app = FastAPI()
    app.include_router(api.auth_router)
    endpoint_calls = []

    def profile(frame, event, _argument):
        if event == "call" and frame.f_code is api.get_auth_me_api.__code__:
            endpoint_calls.append(True)

    previous = sys.getprofile()
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "GET", "scheme": "http", "path": "/api/v1/auth/me",
        "raw_path": b"/api/v1/auth/me", "query_string": b"", "root_path": "",
        "headers": [(b"authorization", ("Bearer " + token).encode("ascii"))],
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 50000),
    }
    try:
        sys.setprofile(profile)
        await app(scope, receive, send)
    finally:
        sys.setprofile(previous)
    starts = [message for message in messages if message["type"] == "http.response.start"]
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    if len(starts) != 1:
        pytest.fail("C1A_SCOPE_ASGI_RESPONSE_START_INVALID", pytrace=False)
    response = SimpleNamespace(
        status_code=starts[0]["status"], text=body.decode("utf-8"), json=lambda: json.loads(body),
        headers={key.decode().title(): value.decode() for key, value in starts[0]["headers"]},
    )
    if not events or events[-1] != "authority-close":
        pytest.fail("C1A_SCOPE_AUTHORITY_NOT_RELEASED", pytrace=False)
    return response, bool(endpoint_calls), token


@pytest.mark.asyncio
@pytest.mark.parametrize(("role", "field"), [
    (role, field) for role in _SCOPE_AUTHORITY_FACTS
    for field in ("org_id", "province", "city") if field not in _SCOPE_ALLOWED.get(role, set())
])
async def test_C1A_I1_非适用Scope真实HTTP拒绝且业务零进入(access_settings, monkeypatch, caplog, role, field):
    response, entered, token = await _scope_http_response(
        access_settings, monkeypatch, role, {field: 301 if field == "org_id" else "GD"},
    )
    if response.status_code != 401 or entered:
        pytest.fail("C1A_ROLE_INAPPLICABLE_SCOPE_ACCEPTED", pytrace=False)
    if (response.json() != {"detail": "ACCESS_TOKEN_STALE"}
            or response.headers.get("Www-Authenticate") != "Bearer"
            or response.headers.get("Cache-Control") != "no-store"):
        pytest.fail("C1A_SCOPE_REJECTION_UNSAFE", pytrace=False)
    if token in response.text + caplog.text:
        pytest.fail("C1A_SCOPE_TOKEN_EXPOSED", pytrace=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", list(_SCOPE_AUTHORITY_FACTS))
@pytest.mark.parametrize("empty", [None, ""], ids=["null", "empty-string"])
async def test_C1A_I1_规范各角色与空Scope继续真实HTTP成功(access_settings, monkeypatch, role, empty):
    extra = {field: empty for field in ("org_id", "province", "city")
             if field not in _SCOPE_ALLOWED.get(role, set())}
    response, entered, _ = await _scope_http_response(access_settings, monkeypatch, role, extra)
    if response.status_code != 200 or not entered:
        pytest.fail("C1A_CANONICAL_ROLE_WRONGLY_REJECTED", pytrace=False)


@pytest.mark.asyncio
async def test_C1A_I1_待入驻机构管理员保留无Org兼容(access_settings, monkeypatch):
    response, entered, _ = await _scope_http_response(
        access_settings, monkeypatch, "org_admin", {}, onboarding=True,
    )
    if response.status_code != 200 or not entered:
        pytest.fail("C1A_ONBOARDING_SCOPE_WRONGLY_REJECTED", pytrace=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, [], {}], ids=["zero", "list", "object"])
async def test_C1A_I1_非适用Scope不把非字符串空容器视为空(access_settings, monkeypatch, value):
    response, entered, _ = await _scope_http_response(access_settings, monkeypatch, "member", {"province": value})
    if response.status_code != 401 or entered:
        pytest.fail("C1A_NONEMPTY_SCOPE_WRONGLY_TREATED_EMPTY", pytrace=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "disabled", "exited", "deletion", "role", "tenant"])
async def test_C1A_R04_权威失效拒绝旧Token(access_settings, monkeypatch, case):
    from starlette.requests import Request

    from app.core.security import get_current_user_from_jwt

    changes = {"disabled": {"status": "disabled"}, "exited": {"exited_at": object()},
               "deletion": {"deletion_requested_at": object()}, "role": {"role": "therapist"},
               "tenant": {"tenant_id": 201}}
    row = None if case == "missing" else _authority_row(**changes[case])
    events = []
    _authority_factory(monkeypatch, row, events)
    token = _signed(access_settings, _claims())
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    try:
        await get_current_user_from_jwt(request)
    except HTTPException as error:
        assert error.status_code == 401
    else:
        pytest.fail("C1A_STALE_AUTHORITY_ACCEPTED", pytrace=False)
    assert events[-1] == "authority-close"


@pytest.mark.asyncio
async def test_C1A_R05_依赖异常安全503并先释放会话(access_settings, monkeypatch):
    from starlette.requests import Request

    from app.core.security import get_current_user_from_jwt

    events = []
    _authority_factory(monkeypatch, None, events, failure=OSError("synthetic-vendor-detail"))
    token = _signed(access_settings, _claims())
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    try:
        await get_current_user_from_jwt(request)
    except HTTPException as error:
        assert error.status_code == 503
        assert "synthetic-vendor-detail" not in str(error.detail)
    else:
        pytest.fail("C1A_AUTHORITY_NOT_CHECKED", pytrace=False)
    assert events[-2:] == ["authority-rollback", "authority-close"]


def test_C1A_R06_me真实HTTP共用权威入口(access_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    events = []
    _authority_factory(monkeypatch, _authority_row(status="disabled"), events)
    token = _signed(access_settings, _claims())
    response = TestClient(create_app()).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    if response.status_code != 401:
        pytest.fail("C1A_ME_CURRENTNESS_BYPASS", pytrace=False)
    assert events[-1] == "authority-close"


def _limiter(*, capacity=4096):
    from app.core.认证限流 import AuthRateLimiter

    clock = [0.0]
    limiter = AuthRateLimiter(secrets.token_bytes(32), clock=lambda: clock[0], capacity=capacity)
    return limiter, clock


def test_C1A_R07_登录失败冷却与并发预留():
    limiter, clock = _limiter()
    reservations = [limiter.reserve_login("127.0.0.1", "synthetic-subject") for _ in range(5)]
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_login("127.0.0.1", "synthetic-subject")
    assert exc.value.status_code == 429
    for reservation in reservations:
        limiter.settle_login(reservation, failed=True)
    clock[0] = 899
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_login("127.0.0.1", "synthetic-subject")
    assert int(exc.value.headers["Retry-After"]) >= 1
    clock[0] = 901
    limiter.settle_login(limiter.reserve_login("127.0.0.1", "synthetic-subject"), failed=False)


def test_C1A_R07_成功不清IP或其他失败():
    limiter, _ = _limiter()
    failed = limiter.reserve_login("127.0.0.1", "synthetic-subject")
    success = limiter.reserve_login("127.0.0.1", "synthetic-subject")
    limiter.settle_login(failed, failed=True)
    limiter.settle_login(success, failed=False)
    for _ in range(4):
        limiter.settle_login(limiter.reserve_login("127.0.0.1", "synthetic-subject"), failed=True)
    with pytest.raises(HTTPException):
        limiter.reserve_login("127.0.0.1", "synthetic-subject")
    for n in range(14):
        limiter.settle_login(limiter.reserve_login("127.0.0.1", f"distinct-{n}"), failed=False)
    with pytest.raises(HTTPException):
        limiter.reserve_login("127.0.0.1", "distinct-last")


def test_C1A_E1_同主体真实线程混合结算只释放本人且保留失败与IP():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    limiter, clock = _limiter()
    reservations = [limiter.reserve_login("127.0.0.1", "mixed-subject") for _ in range(5)]
    barrier = Barrier(5, timeout=10)

    def settle(index):
        barrier.wait()
        limiter.settle_login(reservations[index], failed=index < 3)

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(settle, range(5)))
    buckets = list(limiter._buckets.values())
    if (limiter._reservations or any(bucket.pending for bucket in buckets)
            or sorted(bucket.count for bucket in buckets) != [3, 3, 5]):
        pytest.fail("C1A_MIXED_SETTLEMENT_LOST_OTHER_RESERVATION_OR_COUNT", pytrace=False)
    for _ in range(2):
        limiter.settle_login(limiter.reserve_login("127.0.0.1", "mixed-subject"), failed=True)
    for index in range(13):
        limiter.settle_login(limiter.reserve_login("127.0.0.1", f"other-{index}"), failed=False)
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_login("127.0.0.1", "ip-over-budget")
    assert exc.value.status_code == 429
    clock[0] = 899
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_login("127.0.0.2", "mixed-subject")
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) == 1
    if limiter._reservations or any(bucket.pending for bucket in limiter._buckets.values()):
        pytest.fail("C1A_MIXED_SETTLEMENT_RESERVATION_RESIDUE", pytrace=False)


def test_C1A_R07_注册主体每日3次与IP5次():
    limiter, clock = _limiter()
    for _ in range(3):
        limiter.reserve_registration("127.0.0.1", "synthetic-subject")
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_registration("127.0.0.2", "synthetic-subject")
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) == 86400
    for n in range(2):
        limiter.reserve_registration("127.0.0.1", f"distinct-{n}")
    with pytest.raises(HTTPException):
        limiter.reserve_registration("127.0.0.1", "distinct-last")
    clock[0] = 86401
    limiter.reserve_registration("127.0.0.1", "synthetic-subject")


def test_C1A_R08_容量不得驱逐活跃计数或保存明文():
    limiter, _ = _limiter(capacity=3)
    first = limiter.reserve_login("127.0.0.1", "synthetic-subject")
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_login("127.0.0.2", "second-subject")
    assert exc.value.status_code == 503
    assert "synthetic-subject" not in repr(vars(limiter))
    assert "127.0.0.1" not in repr(vars(limiter))
    limiter.settle_login(first, failed=True)


def test_C1A_R09_HTTP限流先于业务Session且忽略伪代理头(access_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.database import get_db_session
    from app.main import create_app
    from app.modules.auth import api

    calls = []

    async def session():
        calls.append("session")
        yield object()

    async def login(_session, _payload):
        calls.append("login")
        raise HTTPException(401, "Invalid credentials")

    monkeypatch.setattr(api, "login_user", login)
    app = create_app()
    app.dependency_overrides[get_db_session] = session
    client = TestClient(app, client=("127.0.0.1", 12345))
    for index in range(6):
        response = client.post("/api/v1/auth/login", json={"phone": "19900000000", "password": "SyntheticPassword"},
                               headers={"X-Forwarded-For": f"192.0.2.{index + 1}",
                                        "Forwarded": f"for=192.0.2.{index + 1}"})
    if response.status_code != 429:
        pytest.fail("C1A_LOGIN_HTTP_LIMIT_MISSING", pytrace=False)
    assert calls.count("session") == 5
    assert calls.count("login") == 5
    assert response.headers["Cache-Control"] == "no-store"
    assert int(response.headers["Retry-After"]) > 0


@pytest.mark.parametrize(
    ("name", "value"),
    [("KG_JWT_SECRET_KEY", "changeme"), ("KG_AUTH_RATE_LIMIT_HMAC_KEY", ""),
     ("KG_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "121"), ("KG_JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "14"),
     ("KG_JWT_ALGORITHM", "HS512"), ("KG_ENV", "production"), ("KG_ENV", "unknown")],
    ids=["weak-key", "missing-limiter-key", "ttl-high", "ttl-low", "algorithm", "production", "unknown-profile"],
)
def test_C1A_R10_错误配置在文件初始化前拒绝(access_settings, monkeypatch, name, value):
    from app import main
    from app.core.config import get_settings

    calls = []
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    monkeypatch.setattr(main, "build_private_object_store", lambda **_kwargs: calls.append("file-init"))
    try:
        main.create_app()
    except RuntimeError:
        pass
    else:
        pytest.fail("C1A_UNSAFE_STARTUP_ACCEPTED", pytrace=False)
    assert calls == []


@pytest.mark.asyncio
async def test_C1A_R13_未知账号同样执行一次密码成本(access_settings, monkeypatch):
    from app.modules.auth import service
    from app.modules.auth.schemas import AuthLoginRequest

    calls = []

    async def absent(_session, _phone):
        return None

    monkeypatch.setattr(service, "get_user_by_phone", absent)
    monkeypatch.setattr(service, "verify_password", lambda *_args: calls.append("password-cost") or False)
    with pytest.raises(HTTPException) as exc:
        await service.login_user(object(), AuthLoginRequest(phone="19900000000", password="SyntheticPassword"))
    assert exc.value.status_code == 401
    if calls != ["password-cost"]:
        pytest.fail("C1A_UNKNOWN_SUBJECT_TIMING_GAP", pytrace=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["exited_at", "deletion_requested_at"])
async def test_C1A_R13_已退出或删除请求账号不签发Token(access_settings, monkeypatch, field):
    from app.modules.auth import service
    from app.modules.auth.schemas import AuthLoginRequest

    user = _authority_row(**{field: object()})
    user.phone = "19900000000"
    user.password_hash = "synthetic-hash"

    async def load(_session, _phone):
        return user

    monkeypatch.setattr(service, "get_user_by_phone", load)
    monkeypatch.setattr(service, "verify_password", lambda *_args: True)
    try:
        await service.login_user(object(), AuthLoginRequest(phone="19900000000", password="SyntheticPassword"))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        pytest.fail("C1A_INACTIVE_AUTHORITY_TOKEN_ISSUED", pytrace=False)


def test_C1A_R09_注册冲突也应no_store并消耗额度(access_settings, monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.database import get_db_session
    from app.main import create_app
    from app.modules.auth import api

    async def session():
        yield object()

    async def conflict(_session, _payload):
        raise HTTPException(409, "User already exists")

    monkeypatch.setattr(api, "register_user", conflict)
    app = create_app()
    app.dependency_overrides[get_db_session] = session
    client = TestClient(app, client=("127.0.0.1", 12345))
    response = client.post("/api/v1/users/register", json={"phone": "19900000000", "password": "SyntheticPassword"})
    if response.headers.get("Cache-Control") != "no-store":
        pytest.fail("C1A_REGISTRATION_FAILURE_CACHEABLE", pytrace=False)


def test_C1A_R07_真实线程竞争只允许五个在途():
    from concurrent.futures import ThreadPoolExecutor

    limiter, _ = _limiter()

    def attempt(_):
        try:
            limiter.reserve_login("127.0.0.1", "synthetic-subject")
            return True
        except HTTPException:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(attempt, range(36)))
    assert sum(results) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["none", "ordinary", "cancel"])
@pytest.mark.parametrize("cleanup", ["none", "ordinary", "cancel"])
async def test_C1A_R11_权威会话取消主异常与两步清理(access_settings, monkeypatch, body, cleanup):
    import asyncio

    from app.core import 认证当前性 as authority

    events = []
    primary = asyncio.CancelledError() if body == "cancel" else RuntimeError("primary")
    clean_error = asyncio.CancelledError() if cleanup == "cancel" else OSError("cleanup")

    class Session:
        async def execute(self, _statement):
            if body != "none":
                raise primary
            return SimpleNamespace(one_or_none=_authority_row)

        async def rollback(self):
            events.append("rollback")
            if cleanup != "none":
                raise clean_error

        async def close(self):
            events.append("close")
            if cleanup != "none":
                raise clean_error

    monkeypatch.setattr(authority, "get_session_factory", lambda: Session)
    if body == cleanup == "none":
        await authority._read_authority(101)
    else:
        expected = primary if body == "cancel" else clean_error if cleanup == "cancel" or body == "none" else primary
        with pytest.raises(type(expected)) as exc:
            await authority._read_authority(101)
        assert exc.value is expected
    assert events == ["rollback", "close"]


@pytest.mark.parametrize("peer", [None, "testclient", "not-an-ip", "127.0.0.1,192.0.2.1"],
                         ids=["missing", "test-hostname", "invalid", "proxy-chain"])
def test_C1A_R09_缺失与非法直接来源关闭(peer):
    limiter, _ = _limiter()
    with pytest.raises(HTTPException) as exc:
        limiter.reserve_login(peer, "synthetic-subject")
    assert exc.value.status_code == 503
    assert exc.value.detail == "AUTH_PEER_UNAVAILABLE"


def test_C1A_R09_合法loopback来源执行正常配额判断():
    limiter, _ = _limiter()
    limiter.settle_login(limiter.reserve_login("127.0.0.1", "synthetic-subject"), failed=False)


_STARTUP_PROBE = r'''
import os
import secrets
import socket
import sys
import tempfile

mode, profile, broker_state = sys.argv[1:]
os.environ.update({
    "KG_ENV": profile,
    "KG_DATABASE_PASSWORD": secrets.token_urlsafe(32),
    "KG_JWT_SECRET_KEY": secrets.token_urlsafe(32),
    "KG_AUTH_RATE_LIMIT_HMAC_KEY": secrets.token_urlsafe(32),
    "KG_SLICE5_CURSOR_SIGNING_KEY": secrets.token_urlsafe(48),
    "KG_SLICE7_CURSOR_SIGNING_KEY": secrets.token_urlsafe(48),
    "KG_SLICE5_PUBLIC_REFERENCE_HMAC_KEY": secrets.token_urlsafe(48),
    "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY": secrets.token_urlsafe(48),
    "KG_FILE_STORAGE_BACKEND": "local_filesystem",
})
secret = secrets.token_urlsafe(32)
valid_broker = "amqp://synthetic:" + secret + "@127.0.0.1:1//"
if broker_state == "valid":
    os.environ["KG_CELERY_BROKER_URL"] = valid_broker
elif broker_state == "invalid":
    os.environ["KG_CELERY_BROKER_URL"] = "unsupported://synthetic:" + secret + "@127.0.0.1/"
elif broker_state == "empty":
    os.environ["KG_CELERY_BROKER_URL"] = ""
elif broker_state.startswith("port-"):
    port = {"port-text": "notnumeric", "port-high": "65536", "port-zero": "0",
            "port-negative": "-1", "port-empty": "", "port-max": "65535"}[broker_state]
    os.environ["KG_CELERY_BROKER_URL"] = "amqp://synthetic:" + secret + "@127.0.0.1:" + port + "//"
elif broker_state in {"default-amqp", "default-amqps", "ipv6-default"}:
    scheme = "amqps" if broker_state == "default-amqps" else "amqp"
    host = "[::1]" if broker_state == "ipv6-default" else "127.0.0.1"
    os.environ["KG_CELERY_BROKER_URL"] = scheme + "://synthetic:" + secret + "@" + host + "//"

class WorkerBoundaryReached(Exception):
    pass

def network_forbidden(*args, **kwargs):
    raise AssertionError("C1A_UNEXPECTED_NETWORK_EFFECT")

socket.socket.connect = network_forbidden
socket.create_connection = network_forbidden

try:
    with tempfile.TemporaryDirectory(prefix="c1a-startup-") as root:
        os.environ["KG_PRIVATE_FILE_STORAGE_ROOT"] = root
        if mode.startswith("late-"):
            # Construct a sender under a permitted test profile, then change the
            # actual worker profile. Bootstrap must revalidate, not trust import.
            os.environ["KG_ENV"] = "test"
        if mode == "api-first":
            from app.main import create_app
            create_app()
        from app.tasks.celery_app import celery_app, create_celery_app
        if mode in {"api", "api-first"}:
            from app.main import create_app
            create_app()
            create_app()
            create_celery_app()
            print("API_SENDER_ONLY_READY")
        elif mode == "publish":
            try:
                celery_app.send_task("identity.registration.dispatch_outbox", retry=False)
            except Exception:
                print("PUBLISH_REJECTED")
            else:
                raise AssertionError("C1A_FALSE_PUBLISH_SUCCESS")
        else:
            from app.core.config import get_settings
            os.environ["KG_ENV"] = profile
            get_settings.cache_clear()
            from celery.worker.worker import WorkController
            def reached(*args, **kwargs):
                raise WorkerBoundaryReached()
            # This is the first Celery controller body, before loader/consumer
            # setup. A valid startup may reach it; invalid startup must not.
            WorkController.__init__ = reached
            if mode.endswith("cli"):
                from celery.__main__ import main
                sys.argv = ["celery", "-A", "app.tasks.celery_app:celery_app", "worker", "--pool=solo"]
                main()
            elif mode.endswith("worker-main"):
                celery_app.worker_main(["worker", "--pool=solo"])
            elif mode.endswith("controller"):
                celery_app.WorkController()
            else:
                celery_app.Worker()
except WorkerBoundaryReached:
    print("WORKER_BOUNDARY_REACHED")
except Exception as error:
    # Do not propagate generated configuration or vendor detail into JUnit.
    if str(error) == "AUTH_CONFIGURATION_INVALID":
        print("AUTH_CONFIGURATION_INVALID")
        sys.exit(42)
    print("C1A_STARTUP_PROBE_UNEXPECTED")
    sys.exit(79)
'''


def _startup_probe(mode, profile, broker_state):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("KG_", "CELERY_"))}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", _STARTUP_PROBE, mode, profile, broker_state],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    output = result.stdout + result.stderr
    if any(marker in output for marker in ("amqp://", "unsupported://", "synthetic:", "Traceback")):
        pytest.fail("C1A_STARTUP_PROBE_OUTPUT_UNSAFE", pytrace=False)
    return result.returncode, output


@pytest.mark.parametrize("profile", ["local", "ci_ephemeral"])
@pytest.mark.parametrize("mode", ["api", "api-first"])
def test_C1A_R14_API无Broker导入顺序与重复创建不启动Worker(profile, mode):
    code, output = _startup_probe(mode, profile, "missing")
    if code != 0 or output.strip() != "API_SENDER_ONLY_READY":
        pytest.fail("C1A_API_ONLY_WRONGLY_REQUIRES_WORKER_BROKER", pytrace=False)


@pytest.mark.parametrize("entry", ["cli", "worker-main", "worker", "controller"])
@pytest.mark.parametrize("broker_state", ["missing", "invalid", "empty"])
def test_C1A_R14_真实Worker入口缺失或非法Broker连接前非零退出(entry, broker_state):
    code, output = _startup_probe("late-" + entry, "ci_ephemeral", broker_state)
    if code == 0 or "AUTH_CONFIGURATION_INVALID" not in output or "WORKER_BOUNDARY_REACHED" in output:
        pytest.fail("C1A_WORKER_BOOTSTRAP_DID_NOT_REVALIDATE", pytrace=False)


@pytest.mark.parametrize("entry", ["cli", "worker-main", "worker", "controller"])
def test_C1A_R14_合法Worker配置可进入框架后续步骤(entry):
    code, output = _startup_probe(entry, "ci_ephemeral", "valid")
    if code != 0 or output.strip() != "WORKER_BOUNDARY_REACHED":
        pytest.fail("C1A_VALID_WORKER_BLOCKED_BEFORE_FRAMEWORK", pytrace=False)


@pytest.mark.parametrize("state", ["invalid", "empty"])
def test_C1A_R14_API显式非法Broker不得静默忽略(state):
    code, output = _startup_probe("api", "ci_ephemeral", state)
    if code == 0 or "AUTH_CONFIGURATION_INVALID" not in output:
        pytest.fail("C1A_CONFIGURED_INVALID_BROKER_IGNORED", pytrace=False)


@pytest.mark.parametrize("mode", ["api", "late-worker"])
@pytest.mark.parametrize("state", ["port-text", "port-high", "port-zero", "port-negative", "port-empty"])
def test_C1A_I2_Broker非法端口在实际入口连接和Loader前安全拒绝(mode, state):
    code, output = _startup_probe(mode, "ci_ephemeral", state)
    if code != 42 or output.strip() != "AUTH_CONFIGURATION_INVALID":
        pytest.fail("C1A_INVALID_BROKER_PORT_NOT_REJECTED_BEFORE_LOADER", pytrace=False)


@pytest.mark.parametrize("mode", ["api", "worker"])
@pytest.mark.parametrize("state", ["valid", "port-max", "default-amqp", "default-amqps", "ipv6-default"])
def test_C1A_I2_Broker合法默认及显式端口保留启动合同(mode, state):
    code, output = _startup_probe(mode, "ci_ephemeral", state)
    expected = "API_SENDER_ONLY_READY" if mode == "api" else "WORKER_BOUNDARY_REACHED"
    if code != 0 or output.strip() != expected:
        pytest.fail("C1A_VALID_BROKER_PORT_REJECTED", pytrace=False)


def test_C1A_R14_无Broker发布不得虚假成功():
    code, output = _startup_probe("publish", "local", "missing")
    if code != 0 or output.strip() != "PUBLISH_REJECTED":
        pytest.fail("C1A_MISSING_BROKER_PUBLISH_BOUNDARY", pytrace=False)


@pytest.mark.parametrize(
    ("path", "field"),
    [("/api/v1/auth/login", "password"), ("/api/v1/auth/login", "totp_code"),
     ("/api/v1/users/register", "password")],
    ids=["login-invalid-password", "login-invalid-totp", "register-invalid-password"],
)
def test_C1A_R15_认证非法输入422不回显且不进入业务依赖(access_settings, monkeypatch, caplog, path, field):
    value = secrets.token_urlsafe(4) if field == "password" else "synthetic-invalid-code"
    payload = {"phone": "19900000000", "password": "SyntheticPassword", field: value}
    _assert_auth_validation_boundary(monkeypatch, caplog, path, {"json": payload}, value)


@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/users/register"], ids=["login", "register"])
@pytest.mark.parametrize("kind", ["depth", "integer-limit"])
@pytest.mark.asyncio
async def test_C1A_R15_JSON解析资源边界真实生产入口安全422(access_settings, monkeypatch, caplog, path, kind):
    from fastapi import FastAPI

    from app.core import database
    from app.modules.auth import api

    body = b"[" * 1500 + b"0" + b"]" * 1500 if kind == "depth" else b"1" * 5000
    expected_error = RecursionError if kind == "depth" else ValueError
    if sys.get_int_max_str_digits() != 4300:
        pytest.fail("C1A_JSON_DEFAULT_INTEGER_LIMIT_CHANGED", pytrace=False)
    try:
        json.loads(body)
    except expected_error:
        pass
    else:
        pytest.fail("C1A_JSON_RESOURCE_BOUNDARY_NOT_REPRODUCIBLE", pytrace=False)
    calls = dict(admission=0, session_dependency=0, session_factory=0, endpoint=0, service=0)
    observed_codes = {
        api._login_admission.__code__: "admission",
        api._registration_admission.__code__: "admission",
        database.get_db_session.__code__: "session_dependency",
        api.login_user_api.__code__: "endpoint",
        api.register_user_api.__code__: "endpoint",
    }

    def profile(frame, event, _argument):
        if event == "call" and frame.f_code in observed_codes:
            calls[observed_codes[frame.f_code]] += 1

    def forbidden_factory():
        calls["session_factory"] += 1
        raise RuntimeError("C1A_INVALID_JSON_ENTERED_SESSION_FACTORY")

    async def forbidden_service(*_args):
        calls["service"] += 1
        raise RuntimeError("C1A_INVALID_JSON_ENTERED_SERVICE")

    monkeypatch.setattr(database, "get_session_factory", forbidden_factory)
    monkeypatch.setattr(api, "login_user", forbidden_service)
    monkeypatch.setattr(api, "register_user", forbidden_service)
    app = FastAPI()
    app.include_router(api.auth_router)
    app.include_router(api.router)
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode("ascii"), "query_string": b"", "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "server": ("127.0.0.1", 80), "client": ("127.0.0.1", 50000),
    }
    escaped = False
    previous_profile = sys.getprofile()
    try:
        sys.setprofile(profile)
        try:
            await app(scope, receive, send)
        except expected_error:
            escaped = True
    finally:
        sys.setprofile(previous_profile)
    if any(calls.values()):
        pytest.fail("C1A_JSON_INPUT_ENTERED_ADMISSION_OR_BUSINESS", pytrace=False)
    if escaped:
        pytest.fail("C1A_AUTH_JSON_PARSER_ESCAPED_SAFE_422", pytrace=False)
    starts = [message for message in messages if message["type"] == "http.response.start"]
    public = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    if len(starts) != 1 or starts[0]["status"] != 422:
        pytest.fail("C1A_AUTH_JSON_RESPONSE_NOT_422", pytrace=False)
    headers = dict(starts[0]["headers"])
    if (headers.get(b"cache-control") != b"no-store" or headers.get(b"content-type") != b"application/json"
            or json.loads(public) != {"code": "AUTH_INPUT_INVALID", "message": "request rejected"}):
        pytest.fail("C1A_AUTH_JSON_RESPONSE_NOT_CLOSED", pytrace=False)
    public_text = public.decode("utf-8") + caplog.text
    if any(value in public_text for value in (body.decode("ascii"), '"input"', "Traceback", "RecursionError", "ValueError")):
        pytest.fail("C1A_AUTH_JSON_RESPONSE_OR_LOG_UNSAFE", pytrace=False)


@pytest.mark.asyncio
async def test_C1A_R15_JSON之外普通ValueError不得误译422(access_settings, monkeypatch):
    from starlette.requests import Request

    from app.modules.auth import api

    marker = ValueError("C1A_SYNTHETIC_MODEL_FAILURE")

    def model_failure(_cls, _value):
        raise marker

    monkeypatch.setattr(api.AuthLoginRequest, "model_validate", classmethod(model_failure))

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    route = next(route for route in api.auth_router.routes if route.path == "/api/v1/auth/login")
    request = Request({"type": "http", "headers": [(b"content-type", b"application/json")]}, receive)
    with pytest.raises(ValueError) as caught:
        await route.get_route_handler()(request)
    if caught.value is not marker:
        pytest.fail("C1A_NON_JSON_PRIMARY_ERROR_REPLACED", pytrace=False)


def _assert_auth_validation_boundary(monkeypatch, caplog, path, request_kwargs, sentinel):
    from fastapi.testclient import TestClient

    from app.core.database import get_db_session
    from app.main import create_app
    from app.modules.auth import api, service
    from app.modules.institution_onboarding import domain

    calls = []

    async def session():
        calls.extend(("session-created", "session-entered"))
        yield object()

    async def forbidden(*_args):
        calls.append("business-service")
        raise AssertionError("C1A_INVALID_INPUT_REACHED_SERVICE")

    monkeypatch.setattr(api, "login_user", forbidden)
    monkeypatch.setattr(api, "register_user", forbidden)
    monkeypatch.setattr(service, "verify_password", lambda *_args: calls.append("pbkdf2-verify"))
    monkeypatch.setattr(service, "hash_password", lambda *_args: calls.append("pbkdf2-hash"))
    monkeypatch.setattr(domain, "verify_totp", lambda *_args: calls.append("totp-verify"))
    app = create_app()
    app.dependency_overrides[get_db_session] = session
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(path, **request_kwargs)
    if sentinel in response.text or '"input"' in response.text or sentinel in caplog.text:
        pytest.fail("C1A_AUTH_VALIDATION_INPUT_DISCLOSED", pytrace=False)
    if response.status_code != 422 or response.headers.get("Cache-Control") != "no-store":
        pytest.fail("C1A_AUTH_VALIDATION_UNSAFE_CONTRACT", pytrace=False)
    if (response.json() != {"code": "AUTH_INPUT_INVALID", "message": "request rejected"}
            or not response.headers.get("Content-Type", "").startswith("application/json")):
        pytest.fail("C1A_AUTH_VALIDATION_DTO_NOT_CLOSED", pytrace=False)
    if calls:
        pytest.fail("C1A_INVALID_INPUT_ENTERED_BUSINESS_DEPENDENCY", pytrace=False)


@pytest.mark.parametrize("path", ["/api/v1/auth/login", "/api/v1/users/register"], ids=["login", "register"])
@pytest.mark.parametrize("case", [
    "wrong-type", "long-password", "missing-phone", "missing-password", "invalid-phone",
    "list-body", "null-body", "malformed-json", "empty-body", "invalid-utf8", "wrong-content-type",
])
def test_C1A_R15_完整请求验证分支先于业务与密码成本(access_settings, monkeypatch, caplog, path, case):
    sentinel = "SyntheticValidationSentinel"
    payload = {"phone": "19900000000", "password": sentinel}
    request_kwargs = {"json": payload}
    if case == "wrong-type":
        payload["password"] = {"value": sentinel}
    elif case == "long-password":
        payload["password"] = sentinel * 8
    elif case == "missing-phone":
        del payload["phone"]
    elif case == "missing-password":
        del payload["password"]
        payload["synthetic-extra-field"] = sentinel
    elif case == "invalid-phone":
        payload["phone"] = sentinel
    elif case == "list-body":
        request_kwargs = {"json": [payload]}
    elif case == "null-body":
        request_kwargs = {"content": b"null", "headers": {"Content-Type": "application/json"}}
    elif case == "malformed-json":
        request_kwargs = {"content": '{"password":"' + sentinel, "headers": {"Content-Type": "application/json"}}
    elif case == "empty-body":
        request_kwargs = {"content": b"", "headers": {"Content-Type": "application/json"}}
    elif case == "invalid-utf8":
        request_kwargs = {"content": b"\xff", "headers": {"Content-Type": "application/json"}}
    elif case == "wrong-content-type":
        request_kwargs = {"content": json.dumps(payload), "headers": {"Content-Type": "text/plain"}}
    _assert_auth_validation_boundary(monkeypatch, caplog, path, request_kwargs, sentinel)


def test_C1A_R15_两入口OpenAPI422与运行DTO一致且保留429503(access_settings):
    from app.main import create_app

    document = create_app().openapi()
    for path in ("/api/v1/auth/login", "/api/v1/users/register"):
        responses = document["paths"][path]["post"]["responses"]
        schema = responses["422"]["content"]["application/json"]["schema"]
        if (schema.get("additionalProperties") is not False
                or schema.get("properties", {}).get("code", {}).get("enum") != ["AUTH_INPUT_INVALID"]):
            pytest.fail("C1A_AUTH_VALIDATION_OPENAPI_NOT_CLOSED", pytrace=False)
        assert responses["422"]["headers"]["Cache-Control"]["schema"]["example"] == "no-store"
        assert "429" in responses and "503" in responses


@pytest.mark.parametrize("kind", ["login", "register"])
@pytest.mark.parametrize("status", [200, 401, 503])
def test_C1A_R15_合法请求只执行一次限流依赖且不改服务状态(access_settings, monkeypatch, kind, status):
    from fastapi.testclient import TestClient

    from app.core.database import get_db_session
    from app.main import create_app
    from app.modules.auth import api

    calls = []

    async def session():
        calls.append("session")
        try:
            yield object()
        finally:
            calls.append("session-exit")

    async def service(*_args):
        calls.append("service")
        if status != 200:
            raise HTTPException(status, "synthetic-controlled-error")
        return SimpleNamespace(model_dump=lambda: {"status": "synthetic-success"})

    monkeypatch.setattr(api, "login_user" if kind == "login" else "register_user", service)
    app = create_app()
    app.dependency_overrides[get_db_session] = session
    limiter = app.state.auth_rate_limiter
    reserve = getattr(limiter, "reserve_login" if kind == "login" else "reserve_registration")

    def reserved(*args):
        calls.append("reserve")
        return reserve(*args)

    monkeypatch.setattr(limiter, "reserve_login" if kind == "login" else "reserve_registration", reserved)
    if kind == "login":
        settle = limiter.settle_login

        def settled(*args, **kwargs):
            calls.append("settle")
            return settle(*args, **kwargs)

        monkeypatch.setattr(limiter, "settle_login", settled)
    path = "/api/v1/auth/login" if kind == "login" else "/api/v1/users/register"
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.post(path, json={"phone": "19900000000", "password": "SyntheticPassword"})
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "no-store"
    assert calls.count("reserve") == calls.count("session") == calls.count("service") == calls.count("session-exit") == 1
    assert calls.index("reserve") < calls.index("session") < calls.index("service")
    assert calls.count("settle") == (1 if kind == "login" else 0)
