from __future__ import annotations

from app.modules.health_assessment import api


ROUTES = {
    ("POST", "/api/v1/therapist/service-cases/{case_id}/assessments"),
    ("GET", "/api/v1/therapist/service-cases/{case_id}/assessments"),
    ("GET", "/api/v1/therapist/service-cases/{case_id}/assessments/{assessment_id}"),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/assessments/{assessment_id}/disputes"),
    ("GET", "/api/v1/therapist/high-risk-tasks"),
    ("GET", "/api/v1/therapist/high-risk-tasks/{task_id}"),
    ("POST", "/api/v1/therapist/high-risk-tasks/{task_id}/actions"),
    ("GET", "/api/v1/institution/high-risk-tasks"),
    ("GET", "/api/v1/institution/high-risk-tasks/{task_id}"),
    ("POST", "/api/v1/institution/high-risk-tasks/{task_id}/actions"),
    ("GET", "/api/v1/institution/service-cases/{case_id}/assessments"),
    ("GET", "/api/v1/family/assessments"),
    ("GET", "/api/v1/family/assessments/{assessment_id}"),
    ("POST", "/api/v1/family/assessments/{assessment_id}/disputes"),
    ("GET", "/api/v1/family/proxy-enrollments/{enrollment_id}/assessments"),
    ("GET", "/api/v1/family/proxy-enrollments/{enrollment_id}/assessments/{assessment_id}"),
    ("POST", "/api/v1/family/proxy-enrollments/{enrollment_id}/assessments/{assessment_id}/disputes"),
    ("POST", "/api/v1/platform/assessment-rule-sets"),
    ("GET", "/api/v1/platform/assessment-rule-sets"),
    ("GET", "/api/v1/platform/assessment-rule-sets/{version_id}"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/submit"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/review"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/publish"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/suspend"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/resume"),
    ("POST", "/api/v1/platform/assessment-rule-sets/{version_id}/retire"),
    ("GET", "/api/v1/platform/high-risk-tasks"),
    ("GET", "/api/v1/platform/high-risk-tasks/{task_id}"),
}


def test_slice5路由目录精确且每条有独立错误目录() -> None:
    actual = {
        (method, route.path)
        for router in api.routers
        for route in router.routes
        for method in route.methods
    }
    assert actual == ROUTES
    assert set(api.SLICE5_ROUTE_ERROR_CODES) == ROUTES
    for codes in api.SLICE5_ROUTE_ERROR_CODES.values():
        assert codes[0] == "AUTHENTICATION_REQUIRED"
        assert "DEPENDENCY_UNAVAILABLE" in codes
        assert len(codes) == len(set(codes))


def test_slice5不改变公开成功Schema且校验错误固定422() -> None:
    by_key = {
        (method, route.path): route
        for router in api.routers
        for route in router.routes
        for method in route.methods
    }
    assert by_key[("POST", "/api/v1/therapist/service-cases/{case_id}/assessments")].status_code == 202
    assert by_key[("POST", "/api/v1/family/assessments/{assessment_id}/disputes")].status_code == 201
    assert by_key[("POST", "/api/v1/platform/assessment-rule-sets")].status_code == 201
    assert all(route.response_model is not None for route in by_key.values())
    assert api.SLICE5_VALIDATION_STATUS == 422
