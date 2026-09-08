from __future__ import annotations

from fastapi import FastAPI


SELF = "/api/v1/family"
PROXY = "/api/v1/family/proxy-enrollments/{enrollment_id}"
THERAPIST = "/api/v1/therapist/service-cases/{case_id}"
INSTITUTION = "/api/v1/institution/service-cases/{case_id}"


def _routes(prefix: str, *, therapist: bool = False) -> dict[tuple[str, str], tuple[str, bool]]:
    facts = "health-facts/{fact_ref}"
    result = {
        ("GET", f"{prefix}/health-profile"): ("HealthProfileDTO", False),
        ("GET", f"{prefix}/detection-reports"): ("DetectionReportPageDTO", False),
        ("GET", f"{prefix}/detection-reports/{{report_id}}"): ("DetectionReportDetailDTO", False),
        ("POST", f"{prefix}/health-indicators"): ("HealthFactBatchDTO", True),
        ("POST", f"{prefix}/{facts}/corrections"): ("HealthFactDTO", True),
        ("GET", f"{prefix}/health-indicators/history"): ("HealthIndicatorPageDTO", False),
        ("GET", f"{prefix}/health-indicators/latest"): ("HealthIndicatorLatestDTO", False),
        ("GET", f"{prefix}/health-indicators/trends"): ("HealthIndicatorTrendDTO", False),
    }
    if therapist:
        result[("POST", f"{prefix}/{facts}/verify")] = ("HealthFactDTO", True)
        result[("POST", f"{prefix}/{facts}/dispute")] = ("HealthFactDTO", True)
    else:
        result[("PUT", f"{prefix}/health-profile")] = ("HealthProfileDTO", True)
        result[("POST", f"{prefix}/detection-reports")] = ("DetectionReportDTO", True)
    return result


ROUTES = {
    **_routes(SELF),
    **_routes(PROXY),
    **_routes(THERAPIST, therapist=True),
    ("GET", f"{INSTITUTION}/health-record"): ("InstitutionHealthRecordDTO", False),
    ("GET", f"{INSTITUTION}/detection-reports"): ("DetectionReportPageDTO", False),
    ("GET", f"{INSTITUTION}/health-indicators/latest"): ("HealthIndicatorLatestDTO", False),
    ("GET", "/api/v1/service-cases/{case_id}/assessment-readiness"): (
        "AssessmentReadinessDTO",
        False,
    ),
}


PROFILE_READ = (
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "HEALTH_PROFILE_NOT_FOUND",
    "DEPENDENCY_UNAVAILABLE",
)
PROFILE_WRITE = (
    "INVALID_REQUEST",
    "PROFILE_SNAPSHOT_INVALID",
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "HEALTH_PROFILE_NOT_FOUND",
    "VERSION_CONFLICT",
    "COMMIT_OUTCOME_UNKNOWN",
    "DEPENDENCY_UNAVAILABLE",
)
REPORT_CREATE = (
    "INVALID_REQUEST",
    "MEASURED_AT_INVALID",
    "REPORT_ATTACHMENT_COUNT_INVALID",
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "PRIVATE_FILE_NOT_FOUND",
    "PRIVATE_FILE_NOT_CLEAN",
    "PRIVATE_FILE_BIND_CONFLICT",
    "COMMIT_OUTCOME_UNKNOWN",
    "DEPENDENCY_UNAVAILABLE",
)
REPORT_LIST = ("INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN", "DEPENDENCY_UNAVAILABLE")
REPORT_DETAIL = (
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "DETECTION_REPORT_NOT_FOUND",
    "DEPENDENCY_UNAVAILABLE",
)
FACT_CREATE = (
    "INVALID_REQUEST",
    "INDICATOR_CATALOG_UNKNOWN",
    "INDICATOR_UNIT_INVALID",
    "DEVICE_SOURCE_FORBIDDEN",
    "MEASURED_AT_INVALID",
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "COMMIT_OUTCOME_UNKNOWN",
    "DEPENDENCY_UNAVAILABLE",
)
CORRECTION = (
    "INVALID_REQUEST",
    "INDICATOR_UNIT_INVALID",
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "HEALTH_FACT_NOT_FOUND",
    "HEALTH_FACT_CORRECTION_CONFLICT",
    "STATE_CONFLICT",
    "COMMIT_OUTCOME_UNKNOWN",
    "DEPENDENCY_UNAVAILABLE",
)
PROJECTION_READ = (
    "INVALID_REQUEST",
    "ACTOR_CURRENTNESS_FORBIDDEN",
    "PROJECTION_SYNC_PENDING",
    "PROJECTION_READ_UNAVAILABLE",
    "DEPENDENCY_UNAVAILABLE",
)


def _family_errors(prefix: str, *, proxy: bool) -> dict[tuple[str, str], tuple[str, ...]]:
    extra = ("PROXY_PERMISSION_FORBIDDEN", "PROXY_GRANT_INVALID") if proxy else ()
    values = {
        ("GET", f"{prefix}/health-profile"): PROFILE_READ,
        ("PUT", f"{prefix}/health-profile"): PROFILE_WRITE,
        ("POST", f"{prefix}/detection-reports"): REPORT_CREATE,
        ("GET", f"{prefix}/detection-reports"): REPORT_LIST,
        ("GET", f"{prefix}/detection-reports/{{report_id}}"): REPORT_DETAIL,
        ("POST", f"{prefix}/health-indicators"): FACT_CREATE,
        ("POST", f"{prefix}/health-facts/{{fact_ref}}/corrections"): CORRECTION,
        ("GET", f"{prefix}/health-indicators/history"): PROJECTION_READ,
        ("GET", f"{prefix}/health-indicators/latest"): PROJECTION_READ,
        ("GET", f"{prefix}/health-indicators/trends"): PROJECTION_READ,
    }
    return {key: tuple(dict.fromkeys(("AUTHENTICATION_REQUIRED", *codes, *extra))) for key, codes in values.items()}


ERRORS = {
    **_family_errors(SELF, proxy=False),
    **_family_errors(PROXY, proxy=True),
}


def _therapist(*codes: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(("AUTHENTICATION_REQUIRED", *codes, "ACTOR_CURRENTNESS_FORBIDDEN", "THERAPIST_SCOPE_FORBIDDEN", "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "DEPENDENCY_UNAVAILABLE")))


ERRORS.update(
    {
        ("GET", f"{THERAPIST}/health-profile"): _therapist("HEALTH_PROFILE_NOT_FOUND"),
        ("GET", f"{THERAPIST}/detection-reports"): _therapist("INVALID_REQUEST"),
        ("GET", f"{THERAPIST}/detection-reports/{{report_id}}"): _therapist("DETECTION_REPORT_NOT_FOUND"),
        ("POST", f"{THERAPIST}/health-indicators"): _therapist(
            "INVALID_REQUEST", "INDICATOR_CATALOG_UNKNOWN", "INDICATOR_UNIT_INVALID",
            "DEVICE_SOURCE_FORBIDDEN", "MEASURED_AT_INVALID", "DETECTION_REPORT_NOT_FOUND",
            "PRIVATE_FILE_NOT_CLEAN", "COMMIT_OUTCOME_UNKNOWN",
        ),
        ("POST", f"{THERAPIST}/health-facts/{{fact_ref}}/verify"): _therapist(
            "INVALID_REQUEST", "HEALTH_FACT_NOT_FOUND", "STATE_CONFLICT", "VERSION_CONFLICT",
            "COMMIT_OUTCOME_UNKNOWN",
        ),
        ("POST", f"{THERAPIST}/health-facts/{{fact_ref}}/dispute"): _therapist(
            "INVALID_REQUEST", "HEALTH_FACT_NOT_FOUND", "STATE_CONFLICT", "VERSION_CONFLICT",
            "COMMIT_OUTCOME_UNKNOWN",
        ),
        ("POST", f"{THERAPIST}/health-facts/{{fact_ref}}/corrections"): _therapist(
            "INVALID_REQUEST", "INDICATOR_UNIT_INVALID", "HEALTH_FACT_NOT_FOUND",
            "HEALTH_FACT_CORRECTION_CONFLICT", "STATE_CONFLICT", "COMMIT_OUTCOME_UNKNOWN",
        ),
        ("GET", f"{THERAPIST}/health-indicators/history"): _therapist("INVALID_REQUEST", "PROJECTION_SYNC_PENDING", "PROJECTION_READ_UNAVAILABLE"),
        ("GET", f"{THERAPIST}/health-indicators/latest"): _therapist("INVALID_REQUEST", "PROJECTION_SYNC_PENDING", "PROJECTION_READ_UNAVAILABLE"),
        ("GET", f"{THERAPIST}/health-indicators/trends"): _therapist("INVALID_REQUEST", "PROJECTION_SYNC_PENDING", "PROJECTION_READ_UNAVAILABLE"),
        ("GET", f"{INSTITUTION}/health-record"): (
            "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "INSTITUTION_SCOPE_FORBIDDEN",
            "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{INSTITUTION}/detection-reports"): (
            "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "INSTITUTION_SCOPE_FORBIDDEN",
            "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", f"{INSTITUTION}/health-indicators/latest"): (
            "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "INSTITUTION_SCOPE_FORBIDDEN",
            "SERVICE_CASE_NOT_FOUND", "CONSENT_REQUIRED", "PROJECTION_SYNC_PENDING",
            "PROJECTION_READ_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE",
        ),
        ("GET", "/api/v1/service-cases/{case_id}/assessment-readiness"): (
            "AUTHENTICATION_REQUIRED", "INVALID_REQUEST", "ACTOR_CURRENTNESS_FORBIDDEN",
            "PROXY_PERMISSION_FORBIDDEN", "THERAPIST_SCOPE_FORBIDDEN",
            "INSTITUTION_SCOPE_FORBIDDEN", "SERVICE_CASE_NOT_FOUND",
            "DEPENDENCY_UNAVAILABLE",
        ),
    }
)


def test_D29_三十四条正式路由及逐路由schema错误与幂等要求独立冻结() -> None:
    from app.modules.user_health.api import formal_routers, strip_slice4_validation_responses

    actual = {
        (method, route.path)
        for router in formal_routers
        for route in router.routes
        for method in getattr(route, "methods", ())
    }
    assert actual == set(ROUTES)
    assert set(ERRORS) == set(ROUTES)

    app = FastAPI()
    for router in formal_routers:
        app.include_router(router)
    schema = strip_slice4_validation_responses(app.openapi())
    validation_routes = {
        ("GET", f"{INSTITUTION}/health-record"),
        ("GET", f"{INSTITUTION}/detection-reports"),
        ("GET", f"{INSTITUTION}/health-indicators/latest"),
        ("GET", "/api/v1/service-cases/{case_id}/assessment-readiness"),
    }
    for key, (response_name, mutation) in ROUTES.items():
        method, path = key
        operation = schema["paths"][path][method.lower()]
        assert ("422" in operation["responses"]) is (key in validation_routes)
        success = operation["responses"]["201" if method == "POST" and mutation and not path.endswith(("/verify", "/dispute")) else "200"]
        assert success["content"]["application/json"]["schema"]["$ref"].endswith(f"/{response_name}")
        assert operation["x-symbolic-error-codes"] == list(ERRORS[key])
        headers = operation.get("parameters", [])
        has_key = any(item.get("in") == "header" and item.get("name") == "Idempotency-Key" for item in headers)
        assert has_key is mutation


def test_D17_D25_D38_正式OpenAPI不接受generation或内部整数标识() -> None:
    from app.modules.user_health.api import formal_routers

    app = FastAPI()
    for router in formal_routers:
        app.include_router(router)
    source = str(app.openapi())
    for forbidden in ("generation_id", "subject_user_id", "subject_member_id", "tenant_internal_id", "fact_id"):
        assert forbidden not in source
