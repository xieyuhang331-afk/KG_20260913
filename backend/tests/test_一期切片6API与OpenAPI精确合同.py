from __future__ import annotations

from fastapi import FastAPI

from app.modules.health_plan.api import SLICE6_ROUTE_ERROR_CODES, routers


EXPECTED_ROUTES = {
    ("GET", "/api/v1/institutions/service-cases/{service_case_id}/plan-generation-eligibility"),
    ("POST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations"),
    ("GET", "/api/v1/institutions/plan-generations/{request_id}"),
    ("GET", "/api/v1/institutions/service-cases/{service_case_id}/plans"),
    ("GET", "/api/v1/institutions/plans/{plan_id}"),
    ("POST", "/api/v1/platform/health-plan-templates"),
    ("GET", "/api/v1/platform/health-plan-templates"),
    ("GET", "/api/v1/platform/health-plan-templates/{template_version_id}"),
    ("POST", "/api/v1/platform/health-plan-templates/{template_version_id}/publish"),
    ("POST", "/api/v1/platform/health-plan-templates/{template_version_id}/retire"),
    ("GET", "/api/v1/platform/health-plan-reviews"),
    ("GET", "/api/v1/platform/health-plan-reviews/{review_id}"),
    ("POST", "/api/v1/platform/health-plan-reviews/{review_id}/claim"),
    ("POST", "/api/v1/platform/health-plan-reviews/{review_id}/decision"),
    ("GET", "/api/v1/therapist/service-cases/{service_case_id}/plans"),
    ("GET", "/api/v1/therapist/plans/{plan_id}"),
    ("POST", "/api/v1/therapist/plans/{plan_id}/explanations"),
    ("GET", "/api/v1/family/service-cases/{service_case_id}/plans"),
    ("GET", "/api/v1/family/plans/{plan_id}"),
    ("POST", "/api/v1/family/plans/{plan_id}/decision"),
}


def _app() -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return app


def test_Slice6公开路由集合和错误目录精确冻结() -> None:
    paths = _app().openapi()["paths"]
    actual = {
        (method.upper(), path)
        for path, path_item in paths.items()
        for method in path_item
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert actual == EXPECTED_ROUTES
    assert set(SLICE6_ROUTE_ERROR_CODES) == EXPECTED_ROUTES
    assert all("UNAUTHENTICATED" in codes for codes in SLICE6_ROUTE_ERROR_CODES.values())
    assert all("DEPENDENCY_UNAVAILABLE" in codes for codes in SLICE6_ROUTE_ERROR_CODES.values())


def test_生成请求不能提交服务器内部资格或引用() -> None:
    schema = _app().openapi()
    request = schema["components"]["schemas"]["CreatePlanGenerationRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {"expected_service_case_version"}
    text = str(request)
    for forbidden in ("eligible", "assessment_id", "template_version_id", "generation_id"):
        assert forbidden not in text


def test_审核和用户决定不能提交方案医学正文() -> None:
    schemas = _app().openapi()["components"]["schemas"]
    assert set(schemas["ReviewDecisionRequest"]["properties"]) == {
        "decision",
        "reason_codes",
        "expected_version",
    }
    assert set(schemas["UserDecisionRequest"]["properties"]) == {
        "decision",
        "expected_version",
    }
    for name in ("ReviewDecisionRequest", "UserDecisionRequest", "PlanExplanationRequest"):
        assert schemas[name]["additionalProperties"] is False
        assert "content" not in str(schemas[name]).lower()


def test_详情有no_store且列表与详情响应模型严格分离() -> None:
    schema = _app().openapi()
    details = [
        ("/api/v1/institutions/plans/{plan_id}", "get"),
        ("/api/v1/therapist/plans/{plan_id}", "get"),
        ("/api/v1/therapist/plans/{plan_id}/explanations", "post"),
        ("/api/v1/family/plans/{plan_id}", "get"),
        ("/api/v1/family/plans/{plan_id}/decision", "post"),
    ]
    for path, method in details:
        operation = schema["paths"][path][method]
        assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/PlanDetailDTO")
    list_operation = schema["paths"]["/api/v1/family/service-cases/{service_case_id}/plans"]["get"]
    assert list_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/PlanPageDTO")
