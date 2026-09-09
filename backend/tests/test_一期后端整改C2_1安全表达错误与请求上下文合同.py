from __future__ import annotations

import asyncio
from uuid import RFC_4122, UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tests.test_一期后端整改C1A认证当前性限流与配置合同 import (
    access_settings as access_settings,
)

_PUBLIC = {
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/users/register"),
    ("post", "/api/v1/institution-onboarding/activate"),
    ("post", "/api/v1/therapist-onboarding/activate"),
    ("get", "/health"),
    ("get", "/health/live"),
    ("get", "/health/ready"),
}
_REQUEST_ID = "01990000-0000-7000-8000-000000000abc"


def assert_core_error_response(response, status, code, *, retryable=False):
    """Independent expected contract for only the approved core-consumer nodes."""
    body = response.json()
    assert response.status_code == status
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert body["code"] == code and body["message"] == "request rejected"
    assert body["retryable"] is retryable and body["field_errors"] == []
    assert _valid_id(body["request_id"])
    assert body["request_id"] == response.headers["x-request-id"]
    assert "no-store" in response.headers["cache-control"]
    assert "private" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"


def _valid_id(value):
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return (
        value == str(parsed) and len(value) == 36
        and parsed.version == 7 and parsed.variant == RFC_4122
    )


def test_C21_R01_四类安全方案必须已表达(access_settings):
    from app.main import create_app

    schemes = create_app().openapi().get("components", {}).get("securitySchemes", {})
    assert set(schemes) == {
        "AccessBearer", "IdentityReviewStepUp", "RecentAccessStepUp", "PrivateFileAccess",
    }, "C21_SECURITY_SCHEMES_MISSING"
    assert schemes["AccessBearer"] == {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    for name, header in (
        ("IdentityReviewStepUp", "X-Identity-Review-Step-Up"),
        ("RecentAccessStepUp", "X-Step-Up-Token"),
        ("PrivateFileAccess", "X-Private-File-Access"),
    ):
        assert schemes[name] == {"type": "apiKey", "in": "header", "name": header}


def test_C21_R05_OpenAPI如实声明核心错误且不覆盖局部422410(access_settings):
    from app.main import create_app

    schema = create_app().openapi()
    models = schema["components"]["schemas"]
    assert "ErrorResponseDTO" in models, "C21_CORE_ERROR_SCHEMA_MISSING"
    error = models["ErrorResponseDTO"]
    assert error["additionalProperties"] is False
    assert set(error["required"]) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert set(error["properties"]) == set(error["required"])
    assert error["properties"]["field_errors"]["maxItems"] == 8
    assert models["FieldErrorDTO"]["additionalProperties"] is False
    me = schema["paths"]["/api/v1/auth/me"]["get"]["responses"]
    for status in ("401", "503", "500"):
        assert me[status]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponseDTO"}
        assert me[status]["headers"]["Cache-Control"]["schema"]["enum"] == [
            "no-store, private", "no-store, private, max-age=0",
        ]
    for path, method in (
        ("/api/v1/auth/login", "post"),
        ("/api/v1/users/register", "post"),
        ("/api/v1/users/me/identity-verification", "put"),
    ):
        local = schema["paths"][path][method]["responses"]["422"]
        assert "request_id" not in str(local)
        assert local["headers"]["Cache-Control"]["schema"]["example"] == "no-store"
    for suffix in ("identity", "tenant-binding"):
        local = schema["paths"]["/api/v1/users/{user_id}/" + suffix]["post"]["responses"]["410"]
        assert "ErrorResponseDTO" not in str(local)
    private = schema["paths"]["/api/v1/private-files/{file_id}/content"]["get"]["responses"]
    assert "application/octet-stream" in private["200"]["content"]
    private_validation = private["422"]
    assert private_validation["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponseDTO"
    }
    assert private_validation["headers"]["Cache-Control"]["schema"]["enum"] == [
        "no-store, private, max-age=0"
    ]
    therapist = schema["paths"]["/api/v1/institution/therapist-invitations"]["post"]["responses"]
    assert "422" not in therapist


def test_C21_R02_未分类公开操作必须拒绝而非默许(access_settings):
    from app.main import create_app

    app = create_app()

    @app.get("/unclassified-probe")
    async def probe():
        return {"ok": True}

    with pytest.raises(RuntimeError, match="^API_SECURITY_CLASSIFICATION_INVALID$"):
        app.openapi()


def test_C21_R02_全部操作显式分类且只七项公开(access_settings):
    from app.main import create_app

    schema = create_app().openapi()
    count = 0
    public = set()
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "delete", "patch", "head", "options"}:
                continue
            count += 1
            assert "security" in operation, "C21_OPERATION_SECURITY_UNCLASSIFIED"
            if operation["security"] == []:
                public.add((method, path))
            else:
                assert len(operation["security"]) == 1
                assert operation["security"][0]["AccessBearer"] == []
    assert public == _PUBLIC
    assert count == 255


@pytest.mark.parametrize(("method", "path", "extra"), [
    ("get", "/api/v1/reviews/users/{user_id}/identity", "IdentityReviewStepUp"),
    ("post", "/api/v1/family/data-exports", "RecentAccessStepUp"),
    ("post", "/api/v1/family/data-exports/{export_id}/download-access", "RecentAccessStepUp"),
    ("get", "/api/v1/private-files/{file_id}/content", "PrivateFileAccess"),
], ids=["identity-review", "export-create", "export-download", "private-content"])
def test_C21_R03_附加凭证与Access必须AND(access_settings, method, path, extra):
    from app.main import create_app

    operation = create_app().openapi()["paths"][path][method]
    assert operation.get("security") == [{"AccessBearer": [], extra: []}], "C21_CREDENTIAL_AND_MISSING"


@pytest.mark.parametrize("headers", [
    [],
    [("X-Request-ID", "not-a-request-id")],
    [("X-Request-ID", "01990000-0000-4000-8000-000000000abc")],
    [("X-Request-ID", "01990000-0000-7000-c000-000000000abc")],
    [("X-Request-ID", "{" + _REQUEST_ID + "}")],
    [("X-Request-ID", "urn:uuid:" + _REQUEST_ID)],
    [("X-Request-ID", _REQUEST_ID.replace("-", ""))],
    [("X-Request-ID", " " + _REQUEST_ID)],
    [("X-Request-ID", _REQUEST_ID + "," + _REQUEST_ID)],
    [("X-Request-ID", "q" * 4096)],
    [("X-Request-ID", _REQUEST_ID), ("x-request-id", _REQUEST_ID)],
], ids=["absent", "text", "v4", "variant", "braces", "urn", "compact", "space", "comma", "oversized", "duplicate"])
def test_C21_R07_非法关联输入重生且不拒绝业务(headers):
    from app.core.middleware import add_request_middleware

    app = FastAPI()
    add_request_middleware(app)

    @app.get("/probe")
    async def probe(request: Request):
        return {"request_id": request.state.request_id}

    with TestClient(app) as client:
        response = client.get("/probe", headers=headers)
    assert response.status_code == 200
    actual = response.headers.get("x-request-id")
    assert _valid_id(actual), "C21_REQUEST_ID_NOT_CANONICAL_V7"
    assert response.json() == {"request_id": actual}
    if headers:
        assert actual != headers[0][1], "C21_INVALID_REQUEST_ID_REUSED"


def test_C21_R07_合法大写关联号只作小写归一化():
    from app.core.middleware import add_request_middleware

    app = FastAPI()
    add_request_middleware(app)

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/probe", headers={"X-Request-ID": _REQUEST_ID.upper()})
    assert response.headers["x-request-id"] == _REQUEST_ID, "C21_REQUEST_ID_CASE_NOT_NORMALIZED"


def test_C21_R05_核心认证错误为闭合安全DTO(access_settings):
    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.get("/api/v1/auth/me")
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}, "C21_ERROR_DTO_MISSING"
    assert response.status_code == 401
    assert body["code"] == "AUTHENTICATION_REQUIRED"
    assert body["message"] == "request rejected"
    assert body["retryable"] is False and body["field_errors"] == []
    assert body["request_id"] == response.headers["x-request-id"]
    assert _valid_id(body["request_id"])
    assert response.headers["www-authenticate"] == "Bearer"
    assert "no-store" in response.headers["cache-control"]


def test_C21_R09_ContextVar并发嵌套复原():
    from app.core import middleware

    context = getattr(middleware, "request_id_context", None)
    assert context is not None, "C21_REQUEST_CONTEXT_MISSING"

    async def run():
        async def worker(value):
            token = context.set(value)
            try:
                await asyncio.sleep(0)
                assert context.get() == value
                nested = context.set("nested")
                try:
                    await asyncio.sleep(0)
                finally:
                    context.reset(nested)
                assert context.get() == value
            finally:
                context.reset(token)
        await asyncio.gather(worker("left"), worker("right"))
    previous = context.get()
    asyncio.run(run())
    assert context.get() == previous


@pytest.mark.parametrize(("status", "detail", "code", "retryable"), [
    (401, "Authentication required", "AUTHENTICATION_REQUIRED", False),
    (401, "Invalid credentials", "INVALID_CREDENTIALS", False),
    (401, "TOTP_REQUIRED_OR_INVALID", "TOTP_REQUIRED_OR_INVALID", False),
    (401, "ACCESS_TOKEN_STALE", "ACCESS_TOKEN_STALE", False),
    (403, "User is not active", "USER_INACTIVE", False),
    (409, "User is not active", "USER_INACTIVE", False),
    (403, "Login context is not configured", "LOGIN_CONTEXT_NOT_CONFIGURED", False),
    (409, "User already exists", "USER_EXISTS", False),
    (503, "Authentication service unavailable", "AUTHENTICATION_UNAVAILABLE", True),
    (422, "Only APP source is allowed for member write API", "HEALTH_INDICATOR_SOURCE_INVALID", False),
    (409, "HEALTH_PROFILE_VERSION_CONFLICT", "HEALTH_PROFILE_VERSION_CONFLICT", False),
    (503, "HEALTH_PROFILE_OUTCOME_UNKNOWN", "HEALTH_PROFILE_OUTCOME_UNKNOWN", False),
    (429, "AUTH_RATE_LIMITED", "AUTH_RATE_LIMITED", False),
    (503, "AUTH_RATE_LIMIT_CAPACITY", "AUTH_RATE_LIMIT_CAPACITY", False),
    (503, "AUTH_RATE_LIMIT_UNAVAILABLE", "AUTH_RATE_LIMIT_UNAVAILABLE", False),
    (410, "LEGACY_DISABLED_FOR_PILOT", "LEGACY_DISABLED_FOR_PILOT", False),
], ids=["auth", "credentials", "totp", "stale", "login-inactive", "write-inactive",
        "login-context", "duplicate", "dependency", "source", "version", "unknown-commit",
        "rate-limited", "rate-capacity", "rate-unavailable", "pilot-retired"])
def test_C21_R05_旧安全语义精确登记不合并状态(access_settings, status, detail, code, retryable):
    from fastapi import HTTPException

    from app.main import create_app

    app = create_app()

    @app.get("/contract-probe")
    async def probe():
        raise HTTPException(status, detail, headers={"Retry-After": "30", "X-Vendor": "must-not-reflect"})

    with TestClient(app) as client:
        response = client.get("/contract-probe")
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}, "C21_REGISTERED_ERROR_DTO_MISSING"
    assert (response.status_code, body["code"], body["retryable"]) == (status, code, retryable)
    assert body["message"] == "request rejected" and body["field_errors"] == []
    assert body["request_id"] == response.headers["x-request-id"]
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["retry-after"] == "30"
    assert "x-vendor" not in response.headers


@pytest.mark.parametrize("status", [401, 403, 404, 409, 410, 413, 422, 429, 503])
def test_C21_R10_未登记detail与厂商header不得反射(access_settings, status):
    from fastapi import HTTPException

    from app.main import create_app

    app = create_app()
    sentinel = "synthetic-private-detail"

    @app.get("/unregistered-error")
    async def probe():
        raise HTTPException(status, {"input": sentinel}, headers={"WWW-Authenticate": sentinel, "Retry-After": sentinel})

    with TestClient(app) as client:
        response = client.get("/unregistered-error")
    assert sentinel not in response.text + str(response.headers), "C21_UNREGISTERED_ERROR_REFLECTED"
    assert response.status_code == status
    assert response.json()["request_id"] == response.headers["x-request-id"]
    assert response.json()["retryable"] is False


def test_C21_R05_框架405保留受控Allow且拒绝恶意方法值(access_settings):
    from fastapi import FastAPI, HTTPException

    from app.core.middleware import add_request_middleware
    from app.core.接口合同 import install_error_contract

    app = FastAPI()
    add_request_middleware(app)
    install_error_contract(app)

    @app.get("/probe")
    async def probe():
        return {"ok": True}

    @app.get("/malicious-allow")
    async def malicious_allow():
        raise HTTPException(405, "unregistered", headers={"Allow": "GET, X-INJECTED"})

    with TestClient(app) as client:
        framework = client.post("/probe")
        malicious = client.get("/malicious-allow")

    assert framework.status_code == 405
    assert framework.headers["allow"] == "GET"
    assert framework.json()["code"] == "METHOD_NOT_ALLOWED"
    assert framework.json()["request_id"] == framework.headers["x-request-id"]
    assert framework.headers["cache-control"] == "no-store, private"
    assert "allow" not in malicious.headers
    assert "X-INJECTED" not in malicious.text + str(malicious.headers)


def test_C21_R14_标准校验错误只输出已知字段且不泄漏任意键(access_settings):
    from pydantic import BaseModel

    from app.main import create_app

    class Payload(BaseModel):
        quantity: int

    app = create_app()

    @app.post("/validation-probe")
    async def probe(payload: Payload):
        return {"ok": True}

    # Resolve a locally declared test model without changing shared fixtures.
    probe.__annotations__["payload"] = Payload
    app.router.routes.pop()
    app.post("/validation-probe")(probe)
    with TestClient(app) as client:
        response = client.post("/validation-probe", json={"quantity": "synthetic-private-input", "synthetic-private-key": "not-reflected"})
    body = response.json()
    assert response.status_code == 422
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}, "C21_VALIDATION_DTO_MISSING"
    assert "synthetic-private" not in response.text
    assert body["field_errors"] == [{"field": "body.quantity", "code": "INVALID_VALUE"}]


def test_C21_R10_未知异常受控500且日志无原文(access_settings, capfd):
    from app.main import create_app

    app = create_app()

    @app.get("/unknown-probe")
    async def probe():
        raise RuntimeError("synthetic-vendor-private-value")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/unknown-probe")
    assert response.status_code == 500
    assert response.headers.get("content-type") == "application/json", "C21_UNKNOWN_ERROR_NOT_SAFE_JSON"
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert response.json()["request_id"] == response.headers["x-request-id"]
    captured = capfd.readouterr()
    assert "synthetic-vendor-private-value" not in response.text + captured.out + captured.err


def test_C21_R11_闭合日志忽略实际路径查询与身份(access_settings, capfd):
    import json

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/synthetic-private-path?token=synthetic-private-value",
                              headers={"X-Request-ID": _REQUEST_ID})
    output = capfd.readouterr()
    events = [json.loads(line) for line in output.err.splitlines() if line.startswith("{")]
    assert events, "C21_REQUEST_EVENT_MISSING"
    event = events[-1]
    assert set(event) == {
        "timestamp_utc", "level", "event", "request_id", "method", "route_template",
        "status_code", "duration_ms", "error_code", "retryable",
    }
    assert event["request_id"] == response.headers["x-request-id"] == _REQUEST_ID
    assert event["route_template"] == "unmatched" and event["method"] == "GET"
    assert event["status_code"] == 404
    assert "synthetic-private" not in output.out + output.err


def test_C21_R12_观测失败不改业务结果或重复执行(access_settings, monkeypatch, capfd):
    from app.core import logging as request_logging
    from app.main import create_app

    emit = getattr(request_logging, "emit_request_event", None)
    assert emit is not None, "C21_SAFE_LOGGING_BOUNDARY_MISSING"
    calls = []
    app = create_app()

    @app.post("/success-probe")
    async def probe():
        calls.append("mutation")
        return {"ok": True}

    def fail(*args, **kwargs):
        raise OSError("synthetic-private-logger-value")

    monkeypatch.setattr(request_logging, "emit_request_event", fail)
    with TestClient(app) as client:
        response = client.post("/success-probe")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert calls == ["mutation"]
    output = capfd.readouterr()
    assert "OBSERVABILITY_EVENT_FAILED" in output.err
    assert "synthetic-private-logger-value" not in output.out + output.err


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True], ids=["before-start", "after-start"])
async def test_C21_R09_ASGI取消原样传播并复原上下文(started):
    from app.core.middleware import RequestContextMiddleware, request_id_context

    cancelled = asyncio.CancelledError()
    messages = []
    before = request_id_context.get()

    async def endpoint(scope, receive, send):
        assert request_id_context.get() == scope["state"]["request_id"]
        if started:
            await send({"type": "http.response.start", "status": 200, "headers": []})
        raise cancelled

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    with pytest.raises(asyncio.CancelledError) as caught:
        await RequestContextMiddleware(endpoint)(
            {"type": "http", "headers": [], "method": "GET", "path": "/probe"}, receive, send,
        )
    assert caught.value is cancelled
    assert request_id_context.get() == before
    assert len(messages) == int(started)


@pytest.mark.asyncio
async def test_C21_R09_流式已开始不得发第二响应且不暴露异常原文():
    from app.core.middleware import RequestContextMiddleware, request_id_context

    messages = []

    async def endpoint(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"unchanged", "more_body": True})
        raise RuntimeError("synthetic-private-stream-value")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    with pytest.raises(RuntimeError) as caught:
        await RequestContextMiddleware(endpoint)(
            {"type": "http", "headers": [], "method": "GET", "path": "/probe"}, receive, send,
        )
    assert str(caught.value) == "REQUEST_STREAM_FAILED", "C21_STREAM_ERROR_NOT_SANITIZED"
    assert [m["type"] for m in messages] == ["http.response.start", "http.response.body"]
    assert messages[1]["body"] == b"unchanged"
    assert request_id_context.get() is None


@pytest.mark.parametrize("status", [401, 403, 500, 503])
@pytest.mark.parametrize(("cache", "expected"), [
    (None, "no-store, private"),
    ("no-store", "no-store, private"),
    ("no-store, private, max-age=0", "no-store, private, max-age=0"),
    ("public, max-age=3600", "no-store, private"),
], ids=["absent", "no-store", "strong", "conflicting-public"])
def test_C21_R05_核心错误安全头闭合合并(access_settings, status, cache, expected):
    from fastapi import HTTPException

    from app.main import create_app

    app = create_app()

    @app.get("/header-probe")
    async def probe():
        headers = {"WWW-Authenticate": "Bearer", "Retry-After": "30", "Vary": "Authorization, Origin"}
        if cache is not None:
            headers["Cache-Control"] = cache
        raise HTTPException(status, "unregistered-safe-probe", headers=headers)

    with TestClient(app) as client:
        response = client.get("/header-probe")
    assert response.status_code == status
    assert response.headers["cache-control"] == expected, "C21_CORE_PRIVATE_CACHE_MISSING"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["retry-after"] == "30"
    assert response.headers["vary"] == "Authorization, Origin"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["success", "stream-failure", "cancel"])
@pytest.mark.parametrize("observation", ["emit-ordinary", "emit-cancel", "diagnostic-ordinary", "diagnostic-cancel"])
async def test_C21_R11_观测双失败仍保持取消主异常与上下文复原(monkeypatch, body, observation):
    from app.core import logging as request_logging
    from app.core.middleware import RequestContextMiddleware, request_id_context

    primary = asyncio.CancelledError()
    observer_cancel = asyncio.CancelledError()
    sent = []
    events = []

    async def endpoint(scope, receive, send):
        events.append("endpoint")
        if body == "cancel":
            raise primary
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"unchanged", "more_body": False})
        if body == "stream-failure":
            raise RuntimeError("synthetic-private-stream-message")

    def emit(**kwargs):
        if observation == "emit-cancel":
            raise observer_cancel
        raise OSError("synthetic-private-log-message")

    def diagnostic():
        if observation == "diagnostic-cancel":
            raise observer_cancel
        if observation == "diagnostic-ordinary":
            raise RuntimeError("synthetic-private-diagnostic-message")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    monkeypatch.setattr(request_logging, "emit_request_event", emit)
    monkeypatch.setattr(request_logging, "observation_failed", diagnostic)
    caught = None
    outer = request_id_context.set(_REQUEST_ID)
    try:
        try:
            await RequestContextMiddleware(endpoint)(
                {"type": "http", "headers": [], "method": "GET", "path": "/probe"}, receive, send,
            )
        except (RuntimeError, asyncio.CancelledError) as error:
            caught = error
        assert request_id_context.get() == _REQUEST_ID
    finally:
        request_id_context.reset(outer)
    assert events == ["endpoint"]
    if body == "cancel":
        assert caught is primary, "C21_PRIMARY_CANCEL_REPLACED_BY_OBSERVATION"
        assert sent == []
    elif observation.endswith("cancel"):
        assert caught is observer_cancel
    elif body == "stream-failure":
        assert isinstance(caught, RuntimeError) and str(caught) == "REQUEST_STREAM_FAILED", "C21_STREAM_PRIMARY_REPLACED"
    else:
        assert caught is None, "C21_COMMITTED_RESPONSE_REPLACED_BY_DIAGNOSTIC"
        assert sent[-1]["body"] == b"unchanged"


@pytest.mark.asyncio
async def test_C21_R09_实际ASGI并发嵌套隔离且二进制逐块不变(monkeypatch):
    from app.core import logging as request_logging
    from app.core.middleware import RequestContextMiddleware, request_id_context

    ids = [_REQUEST_ID, "01990000-0000-7000-8000-000000000def"]
    barrier = asyncio.Barrier(2)
    logs = []
    outputs = [[], []]
    monkeypatch.setattr(request_logging, "emit_request_event", lambda **event: logs.append(event))

    async def receive():
        return {"type": "http.request", "body": b""}

    async def inner(scope, receive, send):
        assert request_id_context.get() == scope["state"]["request_id"]
        await send({"type": "http.response.start", "status": 206, "headers": [(b"content-type", b"application/octet-stream")]})
        await send({"type": "http.response.body", "body": b"\x00\xff", "more_body": True})
        await send({"type": "http.response.body", "body": b"\x01\xfe", "more_body": False})

    async def endpoint(scope, receive, send):
        expected = scope["state"]["request_id"]
        await barrier.wait()
        assert request_id_context.get() == expected
        await RequestContextMiddleware(inner)(
            {"type": "http", "headers": [], "method": "GET", "path": "/inner"}, receive, send,
        )
        assert request_id_context.get() == expected

    async def run(index):
        async def send(message):
            outputs[index].append(message)

        await RequestContextMiddleware(endpoint)(
            {"type": "http", "headers": [(b"x-request-id", ids[index].encode())], "method": "GET", "path": "/outer"}, receive, send,
        )
        assert request_id_context.get() is None

    await asyncio.gather(run(0), run(1))
    assert request_id_context.get() is None
    assert len(logs) == 4 and len({event["request_id"] for event in logs}) == 4
    for index, messages in enumerate(outputs):
        assert messages[1:] == [
            {"type": "http.response.body", "body": b"\x00\xff", "more_body": True},
            {"type": "http.response.body", "body": b"\x01\xfe", "more_body": False},
        ]
        assert dict(messages[0]["headers"])[b"x-request-id"] == ids[index].encode()
        assert messages[0]["status"] == 206
