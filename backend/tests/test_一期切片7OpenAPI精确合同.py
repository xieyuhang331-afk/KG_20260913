from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.modules.service_fulfillment.api import (
    Slice7Route,
    routers,
    strip_slice7_validation_responses,
)
from app.modules.service_fulfillment.service import ServiceFulfillmentError


EXPECTED = {
    ("get", "/api/v1/therapist/service-cases/{case_id}/fulfillment"),
    ("get", "/api/v1/therapist/service-cases/{case_id}/milestones"),
    ("get", "/api/v1/therapist/milestones/{milestone_id}"),
    ("post", "/api/v1/therapist/milestones/{milestone_id}/complete"),
    ("post", "/api/v1/therapist/service-cases/{case_id}/pause"),
    ("post", "/api/v1/therapist/service-cases/{case_id}/resume"),
    ("post", "/api/v1/therapist/service-cases/{case_id}/closing-assessments"),
    ("post", "/api/v1/therapist/service-cases/{case_id}/summaries"),
    ("post", "/api/v1/therapist/service-cases/{case_id}/unable-to-contact"),
    ("post", "/api/v1/therapist/service-cases/{case_id}/safety-terminate"),
    ("get", "/api/v1/institutions/service-cases/{case_id}/fulfillment"),
    ("get", "/api/v1/institutions/service-cases"),
    ("post", "/api/v1/institutions/service-cases/{case_id}/pause"),
    ("post", "/api/v1/institutions/service-cases/{case_id}/resume"),
    ("post", "/api/v1/institutions/service-cases/{case_id}/terminate"),
    ("get", "/api/v1/institutions/service-transfers"),
    ("get", "/api/v1/institutions/service-transfers/{transfer_id}"),
    ("post", "/api/v1/institutions/service-transfers/{transfer_id}/accept"),
    ("post", "/api/v1/institutions/service-transfers/{transfer_id}/start-review"),
    ("post", "/api/v1/institutions/service-transfers/{transfer_id}/reject"),
    ("post", "/api/v1/institutions/service-transfers/{transfer_id}/source-close"),
    ("get", "/api/v1/institutions/service-transfers/{transfer_id}/continuation-handoff"),
    ("post", "/api/v1/institutions/service-transfers/{transfer_id}/continuation-case"),
    ("get", "/api/v1/family/service-cases/{case_id}/fulfillment"),
    ("get", "/api/v1/family/service-cases/{case_id}/milestones"),
    ("get", "/api/v1/family/service-cases/{case_id}/summaries/current"),
    ("post", "/api/v1/family/service-summaries/{summary_id}/acknowledge"),
    ("post", "/api/v1/family/service-cases/{case_id}/withdraw"),
    ("post", "/api/v1/family/service-cases/{case_id}/transfers"),
    ("get", "/api/v1/family/service-transfers/{transfer_id}"),
    ("post", "/api/v1/family/service-transfers/{transfer_id}/cancel"),
    ("post", "/api/v1/family/service-transfers/{transfer_id}/confirm-scope"),
    ("post", "/api/v1/family/data-exports"),
    ("get", "/api/v1/family/data-exports/{export_id}"),
    ("post", "/api/v1/family/data-exports/{export_id}/download-access"),
    ("post", "/api/v1/family/data-exports/{export_id}/cancel"),
    ("get", "/api/v1/platform/service-fulfillment/cases"),
    ("get", "/api/v1/platform/service-fulfillment/cases/{case_id}"),
    ("get", "/api/v1/platform/service-transfers"),
    ("get", "/api/v1/platform/service-transfers/{transfer_id}"),
    ("post", "/api/v1/platform/service-transfers/{transfer_id}/coordinate-close"),
    ("get", "/api/v1/platform/data-exports"),
    ("get", "/api/v1/platform/data-exports/{export_id}"),
    ("post", "/api/v1/platform/service-cases/{case_id}/safety-terminate"),
    ("post", "/api/v1/platform/proxy-major-authorizations"),
    ("post", "/api/v1/platform/proxy-major-authorizations/{authorization_id}/revoke"),
}


def _schema() -> dict:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return strip_slice7_validation_responses(app.openapi())


def test_Slice7冻结路由全部暴露且没有额外公开路由() -> None:
    schema = _schema()
    actual = {
        (method, path)
        for path, item in schema["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual == EXPECTED


def test_敏感下载响应只含一次性字段且声明no_store() -> None:
    schema = _schema()
    operation = schema["paths"]["/api/v1/family/data-exports/{export_id}/download-access"]["post"]
    assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    fields = set(schema["components"]["schemas"]["OneTimeDownloadDTO"]["properties"])
    assert fields == {"access_token", "expires_at", "filename", "content_type"}
    assert {"bucket", "storage_key", "private_file_id", "ciphertext"}.isdisjoint(fields)


def test_错误目录保留401_403_404_409_422_503() -> None:
    schema = _schema()
    operation = schema["paths"]["/api/v1/family/data-exports/{export_id}"]["get"]
    assert {"UNAUTHENTICATED", "ROLE_FORBIDDEN", "EXPORT_NOT_FOUND", "VERSION_CONFLICT", "INVALID_REQUEST", "DEPENDENCY_UNAVAILABLE"}.issubset(set(operation["x-symbolic-error-codes"]))


def test_Slice7错误翻译返回冻结安全JSON信封() -> None:
    app = FastAPI()
    router = APIRouter()
    router.route_class = Slice7Route

    @router.get("/http/{status_code}")
    async def http_error(status_code: int):
        raise HTTPException(status_code=status_code, detail="rejected")

    @router.get("/domain/{code}")
    async def domain_error(code: str):
        raise ServiceFulfillmentError(code)

    @router.get("/validated")
    async def validated(value: int):
        return {"value": value}

    @router.get("/unknown")
    async def unknown():
        raise RuntimeError("credential=secret database=https://private.invalid")

    app.include_router(router)
    with TestClient(app) as client:
        cases = (
            ("/http/401", 401, "UNAUTHENTICATED"),
            ("/domain/ROLE_FORBIDDEN", 403, "ROLE_FORBIDDEN"),
            ("/domain/RESOURCE_NOT_FOUND", 404, "RESOURCE_NOT_FOUND"),
            ("/domain/VERSION_CONFLICT", 409, "VERSION_CONFLICT"),
            ("/validated?value=invalid", 422, "INVALID_REQUEST"),
            ("/unknown", 503, "DEPENDENCY_UNAVAILABLE"),
        )
        for path, status_code, code in cases:
            response = client.get(path)
            assert response.status_code == status_code
            assert response.json() == {
                "code": code,
                "message": "request rejected",
            }
            assert "secret" not in response.text
            assert "private.invalid" not in response.text
