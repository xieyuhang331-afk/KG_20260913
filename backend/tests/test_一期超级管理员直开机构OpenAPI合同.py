from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.database import get_institution_onboarding_reader_session
from app.core.middleware import RequestContextMiddleware
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.main import create_app
from app.modules.direct_institution_onboarding.api import (
    routers,
    strip_direct_onboarding_validation_responses,
)

EXPECTED = {
    ("post", "/api/v1/platform/direct-institution-onboardings"),
    ("get", "/api/v1/platform/direct-institution-onboardings"),
    ("get", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}"),
    (
        "post",
        "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/activation-credential:regenerate",
    ),
    ("post", "/api/v1/platform/direct-institution-onboardings/{onboarding_id}:revoke"),
    ("post", "/api/v1/institution-onboarding/direct-activate"),
    ("get", "/api/v1/institution-onboarding/direct-compliance"),
    ("put", "/api/v1/institution-onboarding/direct-compliance"),
    ("post", "/api/v1/institution-onboarding/direct-compliance:submit"),
    (
        "post",
        "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/compliance-decision",
    ),
    (
        "post",
        "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs",
    ),
    (
        "post",
        "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs/{handoff_id}:regenerate",
    ),
    ("post", "/api/v1/institution-onboarding/admin-handoffs/activate"),
}

WRITES = {item for item in EXPECTED if item[0] in {"post", "put"}}
ANONYMOUS_WRITES = {
    ("post", "/api/v1/institution-onboarding/direct-activate"),
    ("post", "/api/v1/institution-onboarding/admin-handoffs/activate"),
}


def _schema() -> dict:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return strip_direct_onboarding_validation_responses(app.openapi())


def test_冻结公开路由完整且没有额外路由() -> None:
    schema = _schema()
    actual = {
        (method, path)
        for path, item in schema["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual == EXPECTED


def test_主应用挂载全部直开路由且仅凭证激活为匿名() -> None:
    schema = create_app().openapi()
    actual = {
        (method, path)
        for path, item in schema["paths"].items()
        for method in item
        if (method, path) in EXPECTED
    }
    assert actual == EXPECTED
    for method, path in EXPECTED:
        operation = schema["paths"][path][method]
        assert operation["x-symbolic-error-codes"]
        if (method, path) in ANONYMOUS_WRITES:
            assert operation["security"] == []
        else:
            assert operation["security"] == [{"AccessBearer": []}]


def test_所有写操作要求Idempotency_Key且匿名激活不声明Bearer() -> None:
    schema = _schema()
    for method, path in WRITES:
        operation = schema["paths"][path][method]
        headers = {
            item["name"]: item
            for item in operation.get("parameters", [])
            if item["in"] == "header"
        }
        assert headers["Idempotency-Key"]["required"] is True
        if (method, path) in ANONYMOUS_WRITES:
            assert "security" not in operation
        else:
            assert operation["security"] == [{"AccessBearer": []}]


def test_一次性明文凭据响应声明no_store且普通读取不回显凭据() -> None:
    schema = _schema()
    sensitive = {
        ("post", "/api/v1/platform/direct-institution-onboardings"),
        (
            "post",
            "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/activation-credential:regenerate",
        ),
        (
            "post",
            "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs",
        ),
        (
            "post",
            "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs/{handoff_id}:regenerate",
        ),
    }
    for method, path in sensitive:
        response = schema["paths"][path][method]["responses"]["201"]
        assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"

    public_fields = set(
        schema["components"]["schemas"]["DirectOnboardingDTO"]["properties"]
    )
    forbidden = {
        "activation_code",
        "admin_phone",
        "phone",
        "ciphertext",
        "credential_digest",
        "key_id",
    }
    assert public_fields.isdisjoint(forbidden)


def test_公开错误目录固定且移除FastAPI默认422() -> None:
    schema = _schema()
    for method, path in EXPECTED:
        operation = schema["paths"][path][method]
        assert set(operation["x-symbolic-error-codes"]) >= {
            "UNAUTHENTICATED",
            "INVALID_REQUEST",
            "DEPENDENCY_UNAVAILABLE",
        }
        assert operation["responses"]["422"]["content"]["application/json"][
            "schema"
        ]["$ref"].endswith("/ErrorResponseDTO")


def test_真实依赖异常只返回安全503信封且不泄露内部异常() -> None:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    app.add_middleware(RequestContextMiddleware, application=app)
    app.dependency_overrides[get_current_user_from_jwt] = lambda: CurrentUser(
        id=17, role="super_admin"
    )

    async def unavailable():
        raise RuntimeError("synthetic-internal-database-detail")
        yield

    app.dependency_overrides[get_institution_onboarding_reader_session] = unavailable
    response = TestClient(app).get("/api/v1/platform/direct-institution-onboardings")
    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "synthetic-internal-database-detail" not in response.text


def test_一次性凭据类路由公开声明不可恢复明文的重放冲突() -> None:
    schema = _schema()
    for method, path in {
        ("post", "/api/v1/platform/direct-institution-onboardings"),
        (
            "post",
            "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/activation-credential:regenerate",
        ),
        (
            "post",
            "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs",
        ),
        (
            "post",
            "/api/v1/platform/direct-institution-onboardings/{onboarding_id}/admin-handoffs/{handoff_id}:regenerate",
        ),
    }:
        assert "ONE_TIME_CREDENTIAL_ALREADY_ISSUED" in schema["paths"][path][method][
            "x-symbolic-error-codes"
        ]


def test_CREATE首次响应显式返回后续激活必需的credential_id() -> None:
    schema = _schema()
    properties = schema["components"]["schemas"]["DirectCredentialDTO"]["properties"]
    required = schema["components"]["schemas"]["DirectCredentialDTO"]["required"]
    assert properties["credential_id"]["format"] == "uuid"
    assert "credential_id" in required
