from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

from app.core.接口合同 import install_error_contract
from app.modules.health_assessment.api import (
    _STATUS as SLICE5_STATUS,
)
from app.modules.health_assessment.api import (
    SLICE5_ROUTE_ERROR_CODES,
    SLICE5_VALIDATION_STATUS,
    Slice5Route,
    strip_slice5_validation_responses,
)
from app.modules.health_plan.api import (
    _STATUS as SLICE6_STATUS,
)
from app.modules.health_plan.api import (
    SLICE6_ROUTE_ERROR_CODES,
    Slice6Route,
    strip_slice6_validation_responses,
)
from app.modules.institution_onboarding.api import OnboardingRoute
from app.modules.member_enrollment.api import (
    SLICE3_ROUTE_ERROR_CODES,
    MemberEnrollmentRoute,
    strip_member_enrollment_validation_responses,
)
from app.modules.organization.api import OrganizationRoute
from app.modules.private_file.api import _PrivateFileRoute
from app.modules.service_fulfillment.api import (
    SLICE7_ROUTE_ERROR_CODES,
    Slice7Route,
    strip_slice7_validation_responses,
)
from app.modules.therapist_qualification.api import (
    SLICE2_ROUTE_ERROR_CODES,
    TherapistQualificationRoute,
    strip_therapist_validation_responses,
)
from app.modules.user_health.api import (
    _CODE_STATUS as SLICE4_STATUS,
)
from app.modules.user_health.api import (
    _SLICE4_HTTP_ERROR_CONTRACT_ROUTES,
    SLICE4_ROUTE_ERROR_CODES,
    Slice4Route,
    strip_slice4_validation_responses,
)

REQUEST_ID = "01990000-0000-7000-8000-000000000222"
EXPECTED_KEYS = {"code", "message", "request_id", "retryable", "field_errors"}

_S7_BASE = (
    "UNAUTHENTICATED",
    "ROLE_FORBIDDEN",
    "INVALID_REQUEST",
    "DEPENDENCY_UNAVAILABLE",
)
_S7_MUTATION = (
    *_S7_BASE,
    "RESOURCE_NOT_FOUND",
    "VERSION_CONFLICT",
    "STALE_VERSION",
    "IDEMPOTENCY_CONFLICT",
    "COMMIT_NOT_COMMITTED",
    "COMMIT_OUTCOME_UNKNOWN",
    "CURRENTNESS_FORBIDDEN",
)


def _expected_s7_codes(*specific: str, mutation: bool = False) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*( _S7_MUTATION if mutation else _S7_BASE), *specific)))


EXPECTED_SLICE7_ERROR_CODES = {
    ("GET", "/api/v1/therapist/service-cases/{case_id}/fulfillment"): _expected_s7_codes(),
    ("GET", "/api/v1/therapist/service-cases/{case_id}/milestones"): _expected_s7_codes(),
    ("GET", "/api/v1/therapist/milestones/{milestone_id}"): _expected_s7_codes("MILESTONE_NOT_FOUND"),
    ("POST", "/api/v1/therapist/milestones/{milestone_id}/complete"): _expected_s7_codes("CASE_STATE_CONFLICT", "MILESTONE_WINDOW_CLOSED", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/pause"): _expected_s7_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/resume"): _expected_s7_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/closing-assessments"): _expected_s7_codes("CASE_STATE_CONFLICT", "CLOSURE_PREREQUISITE_MISSING", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/summaries"): _expected_s7_codes("CASE_STATE_CONFLICT", "CLOSURE_PREREQUISITE_MISSING", mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/unable-to-contact"): _expected_s7_codes(mutation=True),
    ("POST", "/api/v1/therapist/service-cases/{case_id}/safety-terminate"): _expected_s7_codes(mutation=True),
    ("POST", "/api/v1/institutions/service-cases/{case_id}/pause"): _expected_s7_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-cases/{case_id}/resume"): _expected_s7_codes("CASE_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-cases/{case_id}/terminate"): _expected_s7_codes(mutation=True),
    ("GET", "/api/v1/institutions/service-cases/{case_id}/fulfillment"): _expected_s7_codes(),
    ("GET", "/api/v1/institutions/service-cases"): _expected_s7_codes(),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/start-review"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/accept"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", "CURRENTNESS_FORBIDDEN", mutation=True),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/reject"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/source-close"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("GET", "/api/v1/institutions/service-transfers/{transfer_id}/continuation-handoff"): _expected_s7_codes("HANDOFF_NOT_FOUND"),
    ("POST", "/api/v1/institutions/service-transfers/{transfer_id}/continuation-case"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", "CURRENTNESS_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/institutions/service-transfers"): _expected_s7_codes(),
    ("GET", "/api/v1/institutions/service-transfers/{transfer_id}"): _expected_s7_codes("TRANSFER_NOT_FOUND"),
    ("POST", "/api/v1/family/service-cases/{case_id}/withdraw"): _expected_s7_codes(mutation=True),
    ("GET", "/api/v1/family/service-cases/{case_id}/fulfillment"): _expected_s7_codes(),
    ("GET", "/api/v1/family/service-cases/{case_id}/milestones"): _expected_s7_codes(),
    ("GET", "/api/v1/family/service-cases/{case_id}/summaries/current"): _expected_s7_codes(),
    ("POST", "/api/v1/family/service-summaries/{summary_id}/acknowledge"): _expected_s7_codes("CLOSURE_PREREQUISITE_MISSING", mutation=True),
    ("POST", "/api/v1/family/service-cases/{case_id}/transfers"): _expected_s7_codes("CURRENTNESS_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/family/service-transfers/{transfer_id}"): _expected_s7_codes("TRANSFER_NOT_FOUND"),
    ("POST", "/api/v1/family/service-transfers/{transfer_id}/cancel"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/family/service-transfers/{transfer_id}/confirm-scope"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", mutation=True),
    ("POST", "/api/v1/family/data-exports"): _expected_s7_codes("STEP_UP_FORBIDDEN", "PROXY_PERMISSION_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/family/data-exports/{export_id}"): _expected_s7_codes("EXPORT_NOT_FOUND", "PROXY_PERMISSION_FORBIDDEN"),
    ("POST", "/api/v1/family/data-exports/{export_id}/download-access"): _expected_s7_codes("STEP_UP_FORBIDDEN", "PROXY_PERMISSION_FORBIDDEN", "EXPORT_NOT_READY", mutation=True),
    ("POST", "/api/v1/family/data-exports/{export_id}/cancel"): _expected_s7_codes("PROXY_PERMISSION_FORBIDDEN", "EXPORT_NOT_READY", mutation=True),
    ("POST", "/api/v1/platform/service-cases/{case_id}/safety-terminate"): _expected_s7_codes(mutation=True),
    ("POST", "/api/v1/platform/service-transfers/{transfer_id}/coordinate-close"): _expected_s7_codes("TRANSFER_STATE_CONFLICT", "CURRENTNESS_FORBIDDEN", mutation=True),
    ("POST", "/api/v1/platform/proxy-major-authorizations"): _expected_s7_codes("CURRENTNESS_FORBIDDEN", mutation=True),
    ("POST", "/api/v1/platform/proxy-major-authorizations/{authorization_id}/revoke"): _expected_s7_codes("CURRENTNESS_FORBIDDEN", mutation=True),
    ("GET", "/api/v1/platform/service-transfers"): _expected_s7_codes(),
    ("GET", "/api/v1/platform/service-transfers/{transfer_id}"): _expected_s7_codes("TRANSFER_NOT_FOUND"),
    ("GET", "/api/v1/platform/service-fulfillment/cases"): _expected_s7_codes(),
    ("GET", "/api/v1/platform/service-fulfillment/cases/{case_id}"): _expected_s7_codes(),
    ("GET", "/api/v1/platform/data-exports"): _expected_s7_codes(),
    ("GET", "/api/v1/platform/data-exports/{export_id}"): _expected_s7_codes("EXPORT_NOT_FOUND"),
}

EXPECTED_SLICE7_CODE_STATUS = {
    "UNAUTHENTICATED": 401,
    "ROLE_FORBIDDEN": 403,
    "CURRENTNESS_FORBIDDEN": 403,
    "PROXY_PERMISSION_FORBIDDEN": 403,
    "STEP_UP_FORBIDDEN": 403,
    "RESOURCE_NOT_FOUND": 404,
    "MILESTONE_NOT_FOUND": 404,
    "TRANSFER_NOT_FOUND": 404,
    "HANDOFF_NOT_FOUND": 404,
    "EXPORT_NOT_FOUND": 404,
    "VERSION_CONFLICT": 409,
    "STALE_VERSION": 409,
    "IDEMPOTENCY_CONFLICT": 409,
    "MILESTONE_WINDOW_CLOSED": 409,
    "CASE_STATE_CONFLICT": 409,
    "CLOSURE_PREREQUISITE_MISSING": 409,
    "TRANSFER_STATE_CONFLICT": 409,
    "EXPORT_NOT_READY": 409,
    "INVALID_REQUEST": 422,
    "DEPENDENCY_UNAVAILABLE": 503,
    "COMMIT_NOT_COMMITTED": 503,
    "COMMIT_OUTCOME_UNKNOWN": 503,
}


def _first_route(catalog: dict[tuple[str, str], object]) -> tuple[str, str]:
    return next(iter(catalog))


@pytest.mark.parametrize(
    ("route_class", "method", "path"),
    [
        (OrganizationRoute, "GET", "/api/v1/platform/organizations/tree"),
        (TherapistQualificationRoute, *_first_route(SLICE2_ROUTE_ERROR_CODES)),
        (MemberEnrollmentRoute, *_first_route(SLICE3_ROUTE_ERROR_CODES)),
        (Slice4Route, *_first_route(SLICE4_ROUTE_ERROR_CODES)),
        (Slice5Route, *_first_route(SLICE5_ROUTE_ERROR_CODES)),
        (Slice6Route, *_first_route(SLICE6_ROUTE_ERROR_CODES)),
        (Slice7Route, "GET", "/api/v1/family/service-cases/{case_id}"),
        (_PrivateFileRoute, "GET", "/api/v1/private-files/{file_id}/metadata"),
    ],
)
def test_C2_2_R01_R03_自定义Route未知异常统一为安全500(
    route_class: type, method: str, path: str,
) -> None:
    app = FastAPI()
    install_error_contract(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = REQUEST_ID
        response = await call_next(request)
        response.headers["X-Request-ID"] = REQUEST_ID
        return response

    async def failed_endpoint() -> None:
        raise RuntimeError("synthetic vendor SQL and credential detail")

    app.router.add_api_route(path, failed_endpoint, methods=[method], route_class_override=route_class)
    concrete_path = path.replace("{case_id}", "case-safe").replace("{file_id}", "file-safe")
    response = TestClient(app, raise_server_exceptions=False).request(method, concrete_path)
    body = response.json()
    assert response.status_code == 500, "C22_UNKNOWN_EXCEPTION_NOT_500"
    assert set(body) == EXPECTED_KEYS, "C22_ERROR_DTO_NOT_UNIFIED"
    assert body == {
        "code": "INTERNAL_ERROR",
        "message": "request rejected",
        "request_id": REQUEST_ID,
        "retryable": False,
        "field_errors": [],
    }
    assert response.headers["x-request-id"] == REQUEST_ID
    assert response.headers["cache-control"] in {
        "no-store, private",
        "no-store, private, max-age=0",
    }
    assert response.headers["pragma"] == "no-cache"
    assert "vendor" not in response.text and "credential" not in response.text


def _schema(path: str, method: str, statuses: tuple[str, ...]) -> dict[str, object]:
    return {
        "paths": {
            path: {
                method.lower(): {
                    "responses": {status: {"description": "Request rejected"} for status in statuses}
                }
            }
        }
    }


@pytest.mark.parametrize(
    ("transform", "catalog"),
    [
        (strip_therapist_validation_responses, SLICE2_ROUTE_ERROR_CODES),
        (strip_member_enrollment_validation_responses, SLICE3_ROUTE_ERROR_CODES),
        (strip_slice4_validation_responses, SLICE4_ROUTE_ERROR_CODES),
        (strip_slice5_validation_responses, SLICE5_ROUTE_ERROR_CODES),
        (strip_slice6_validation_responses, SLICE6_ROUTE_ERROR_CODES),
    ],
)
def test_C2_2_R04_自定义OpenAPI错误响应引用核心DTO(
    transform: Callable[[dict[str, object]], dict[str, object]],
    catalog: dict[tuple[str, str], object],
) -> None:
    method, path = _first_route(catalog)
    schema = _schema(path, method, ("401", "500", "503"))
    transformed = transform(schema)
    responses = transformed["paths"][path][method.lower()]["responses"]  # type: ignore[index]
    for status in ("401", "500", "503"):
        response = responses[status]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponseDTO"
        }
        headers = response["headers"]
        assert {"X-Request-ID", "Cache-Control", "Pragma"} <= set(headers)


def test_C2_2_R04_Slice7_OpenAPI错误响应引用核心DTO() -> None:
    path = "/api/v1/family/service-cases/{case_id}/fulfillment"
    schema = _schema(path, "GET", ("401", "500", "503"))
    schema["paths"][path]["get"]["tags"] = ["service-fulfillment-family"]  # type: ignore[index]
    transformed = strip_slice7_validation_responses(schema)
    responses = transformed["paths"][path]["get"]["responses"]  # type: ignore[index]
    for status in ("401", "500", "503"):
        assert responses[status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponseDTO"
        }


def _assert_openapi_error_response(operation: dict[str, object], status: str) -> None:
    responses = operation["responses"]
    assert isinstance(responses, dict)
    assert status in responses, f"C22_OPENAPI_ERROR_STATUS_MISSING:{status}"
    response = responses[status]
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponseDTO"
    }, f"C22_OPENAPI_ERROR_SCHEMA_NOT_CORE:{status}"
    headers = response["headers"]
    assert {"X-Request-ID", "Cache-Control", "Pragma"} <= set(headers), (
        f"C22_OPENAPI_ERROR_HEADERS_INCOMPLETE:{status}"
    )
    if status == "401":
        assert "WWW-Authenticate" in headers
    if status in {"429", "503"}:
        assert "Retry-After" in headers


def _assert_status_consistent_example(
    operation: dict[str, object],
    status: str,
    allowed_codes: tuple[str, ...],
) -> None:
    value = operation["responses"][status]["content"]["application/json"][
        "examples"
    ]["rejected"]["value"]
    expected_codes = {"INTERNAL_ERROR"} if status == "500" else set(allowed_codes)
    assert value["code"] in expected_codes, f"C22_OPENAPI_EXAMPLE_CODE_MISMATCH:{status}"


def test_C2_2_R04_真实OpenAPI逐operation覆盖全部可发生错误状态() -> None:
    from app.main import create_app

    schema = create_app().openapi()
    slice7_tags = {
        "service-fulfillment-therapist",
        "service-fulfillment-institution",
        "service-fulfillment-family",
        "service-fulfillment-platform",
    }
    actual_slice7_operations = {
        (method.upper(), path)
        for path, path_item in schema["paths"].items()
        for method, operation in path_item.items()
        if isinstance(operation, dict)
        and slice7_tags.intersection(operation.get("tags", ()))
    }
    assert actual_slice7_operations == set(EXPECTED_SLICE7_ERROR_CODES)
    for catalog in (SLICE2_ROUTE_ERROR_CODES, SLICE3_ROUTE_ERROR_CODES):
        for (method, path), errors in catalog.items():
            operation = schema["paths"][path][method.lower()]
            for status in {*map(str, errors), "500"}:
                _assert_openapi_error_response(operation, status)
                _assert_status_consistent_example(
                    operation,
                    status,
                    errors.get(int(status), ()),
                )
            assert operation["x-symbolic-error-codes"] == {
                str(status): list(codes) for status, codes in errors.items()
            }
    catalog_contracts = (
        (SLICE4_ROUTE_ERROR_CODES, SLICE4_STATUS, None),
        (SLICE5_ROUTE_ERROR_CODES, SLICE5_STATUS, SLICE5_VALIDATION_STATUS),
        (SLICE6_ROUTE_ERROR_CODES, SLICE6_STATUS, 422),
    )
    for catalog, status_map, fixed_validation_status in catalog_contracts:
        for (method, path), codes in catalog.items():
            operation = schema["paths"][path][method.lower()]
            validation_status = fixed_validation_status
            if catalog is SLICE4_ROUTE_ERROR_CODES:
                validation_status = (
                    422
                    if (method, path) in _SLICE4_HTTP_ERROR_CONTRACT_ROUTES
                    else 400
                )
            expected_statuses = {
                *(str(status_map[code]) for code in codes),
                str(validation_status),
                "500",
            }
            for status in expected_statuses:
                _assert_openapi_error_response(operation, status)
            assert set(operation["x-symbolic-error-codes"]) == set(codes)

    assert SLICE7_ROUTE_ERROR_CODES == EXPECTED_SLICE7_ERROR_CODES
    for (method, path), codes in EXPECTED_SLICE7_ERROR_CODES.items():
        operation = schema["paths"][path][method.lower()]
        expected_statuses = {
            *(str(EXPECTED_SLICE7_CODE_STATUS[code]) for code in codes),
            "422",
            "500",
        }
        for status in expected_statuses:
            _assert_openapi_error_response(operation, status)
        assert tuple(operation["x-symbolic-error-codes"]) == codes


def test_C2_2_R04_Organization_OpenAPI示例不得把业务错误标成内部错误() -> None:
    from app.main import create_app

    operation = create_app().openapi()["paths"][
        "/api/v1/platform/organizations/tree"
    ]["get"]
    expected = {
        "400": "ORGANIZATION_REQUEST_INVALID",
        "403": "ORGANIZATION_SCOPE_FORBIDDEN",
        "404": "ORGANIZATION_NOT_FOUND",
        "409": "ORGANIZATION_STATE_CONFLICT",
        "422": "ORGANIZATION_REQUEST_INVALID",
        "500": "INTERNAL_ERROR",
    }
    for status, code in expected.items():
        examples = operation["responses"][status]["content"]["application/json"][
            "examples"
        ]
        assert examples["rejected"]["value"]["code"] == code



def test_C2_2_R07_不得保留字符串猜码和广义依赖翻译() -> None:
    from pathlib import Path

    root = Path(__file__).parents[1] / "app" / "modules"
    for relative in (
        "user_health/api.py",
        "health_assessment/api.py",
        "health_plan/api.py",
        "service_fulfillment/api.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "def _safe_code(" not in source
        assert "def _safe_error_code(" not in source
    for relative in ("institution_onboarding/api.py", "private_file/api.py"):
        source = (root / relative).read_text(encoding="utf-8")
        safe_call = source[source.index("async def _safe_call"):]
        safe_call = safe_call[: safe_call.index("\n\n@")]
        assert "except Exception:" not in safe_call, "C22_BROAD_DEPENDENCY_TRANSLATION_PRESENT"


def test_C2_2_R02_onboarding十三路由成功Schema非空且实际DTO不损失字段() -> None:
    from app.main import create_app
    from app.modules.institution_onboarding.schemas import (
        ActivationDTO,
        ApplicationCorrectionDTO,
        ApplicationDTO,
        InvitationIssuedDTO,
        InvitationRevokedDTO,
        InvitationSummaryDTO,
        OnboardingSuccessEnvelope,
        ReviewDetailDTO,
        ReviewQueueItemDTO,
    )

    operations = (
        ("post", "/api/v1/platform/institution-invitations"),
        ("get", "/api/v1/platform/institution-invitations"),
        ("post", "/api/v1/platform/institution-invitations/{invitation_id}/resend"),
        ("post", "/api/v1/platform/institution-invitations/{invitation_id}/revoke"),
        ("post", "/api/v1/institution-onboarding/activate"),
        ("get", "/api/v1/institution-onboarding/application"),
        ("put", "/api/v1/institution-onboarding/application"),
        ("post", "/api/v1/institution-onboarding/application/submit"),
        ("get", "/api/v1/institution-onboarding/application/corrections"),
        ("post", "/api/v1/institution-onboarding/application/resubmit"),
        ("get", "/api/v1/platform/institution-reviews"),
        ("get", "/api/v1/platform/institution-reviews/{application_id}"),
        ("post", "/api/v1/platform/institution-reviews/{application_id}/decision"),
    )
    schema = create_app().openapi()
    for method, path in operations:
        responses = schema["paths"][path][method]["responses"]
        success = next(value for status, value in responses.items() if status.startswith("2"))
        declared = success["content"]["application/json"]["schema"]
        assert declared and "OnboardingSuccessEnvelope" in declared.get("$ref", "")

    uid = "01990000-0000-7000-8000-000000000224"
    now = "2026-09-08T00:00:00Z"
    cases = (
        (InvitationIssuedDTO, {"invitation_id": uid, "institution_name": "synthetic institution", "institution_type": "HEALTH_STORE", "status": "ISSUED", "short_code": "123456", "expires_at": now, "version": 1}),
        (InvitationSummaryDTO, {"invitation_id": uid, "institution_name": "synthetic institution", "institution_type": "HEALTH_STORE", "status": "ISSUED", "expires_at": now, "version": 1}),
        (InvitationRevokedDTO, {"invitation_id": uid, "institution_name": "synthetic institution", "institution_type": "HEALTH_STORE", "status": "REVOKED", "version": 2}),
        (ActivationDTO, {"application_id": uid, "user_id": 1, "status": "DRAFT", "version": 1}),
        (ApplicationDTO, {"application_id": uid, "status": "DRAFT", "institution_type": "HEALTH_STORE", "draft": {}, "correction_fields": [], "correction_reason_code": None, "current_revision_no": 0, "version": 1, "tenant_id": None, "tenant_active": False, "service_ready": False, "licenses": []}),
        (ApplicationCorrectionDTO, {"status": "NEEDS_CORRECTION", "fields": ["contact_email"], "reason_code": "CONTACT_INVALID", "version": 2}),
        (ReviewQueueItemDTO, {"application_id": uid, "status": "SUBMITTED", "submitted_at": now, "version": 2}),
        (ReviewDetailDTO, {"application_id": uid, "status": "SUBMITTED", "draft": {}, "version": 2, "revisions": [], "materials": []}),
    )
    for model, data in cases:
        value = OnboardingSuccessEnvelope[model].model_validate({"code": 0, "message": "ok", "data": data})
        assert value.model_dump(mode="json", exclude_unset=True)["data"] == data

    assert UUID(uid).version == 7 and datetime.fromisoformat(now.replace("Z", "+00:00")).tzinfo == UTC


@pytest.mark.parametrize(
    ("route_class", "method", "path", "error_type", "code", "retryable"),
    [
        (Slice5Route, "GET", "/api/v1/family/assessments", "assessment", "DEPENDENCY_UNAVAILABLE", True),
        (Slice6Route, "POST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations", "plan", "COMMIT_OUTCOME_UNKNOWN", False),
        (Slice7Route, "POST", "/api/v1/family/data-exports", "fulfillment", "COMMIT_OUTCOME_UNKNOWN", False),
    ],
)
def test_C2_2_R05_R06_仅注册typed故障映射503且unknown_commit不可重试(
    route_class: type, method: str, path: str, error_type: str, code: str, retryable: bool,
) -> None:
    from app.modules.health_assessment.service import HealthAssessmentError
    from app.modules.health_plan.service import HealthPlanError
    from app.modules.service_fulfillment.service import ServiceFulfillmentError

    errors = {
        "assessment": HealthAssessmentError,
        "plan": HealthPlanError,
        "fulfillment": ServiceFulfillmentError,
    }
    app = FastAPI()
    install_error_contract(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = REQUEST_ID
        response = await call_next(request)
        response.headers["X-Request-ID"] = REQUEST_ID
        return response

    async def failed_endpoint() -> None:
        raise errors[error_type](code)

    app.router.add_api_route(path, failed_endpoint, methods=[method], route_class_override=route_class)
    concrete = path.replace("{service_case_id}", "case-safe")
    response = TestClient(app, raise_server_exceptions=False).request(method, concrete)
    assert response.status_code == 503
    assert response.json() == {
        "code": code,
        "message": "request rejected",
        "request_id": REQUEST_ID,
        "retryable": retryable,
        "field_errors": [],
    }


@pytest.mark.parametrize(
    ("kind", "route_class", "method", "path", "outcome", "expected_code", "retryable"),
    [
        ("assessment", Slice5Route, "POST", "/api/v1/platform/assessment-rule-sets", "NOT_COMMITTED", "DEPENDENCY_UNAVAILABLE", True),
        ("assessment", Slice5Route, "POST", "/api/v1/platform/assessment-rule-sets", "UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", False),
        ("plan", Slice6Route, "POST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations", "NOT_COMMITTED", "DEPENDENCY_UNAVAILABLE", True),
        ("plan", Slice6Route, "POST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations", "UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", False),
        ("fulfillment", Slice7Route, "POST", "/api/v1/family/data-exports", "NOT_COMMITTED", "COMMIT_NOT_COMMITTED", True),
        ("fulfillment", Slice7Route, "POST", "/api/v1/family/data-exports", "UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", False),
    ],
)
def test_C2_2_R05_真实commit_helper类型化出口保持原业务码(
    kind: str,
    route_class: type,
    method: str,
    path: str,
    outcome: str,
    expected_code: str,
    retryable: bool,
) -> None:
    from app.modules.health_assessment import service as assessment_service
    from app.modules.health_plan import service as plan_service
    from app.modules.service_fulfillment import service as fulfillment_service

    modules = {
        "assessment": assessment_service,
        "plan": plan_service,
        "fulfillment": fulfillment_service,
    }
    selected = modules[kind]

    class FailedSession:
        commit_count = 0
        rollback_count = 0

        async def commit(self):
            self.commit_count += 1
            raise RuntimeError("synthetic database failure")

        async def rollback(self):
            self.rollback_count += 1

    session = FailedSession()

    async def confirm():
        return selected.CommitOutcome[outcome]

    async def failed_endpoint() -> None:
        await selected.commit_with_confirmation(session, confirm=confirm)

    app = FastAPI()
    install_error_contract(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = REQUEST_ID
        response = await call_next(request)
        response.headers["X-Request-ID"] = REQUEST_ID
        return response

    app.router.add_api_route(
        path, failed_endpoint, methods=[method], route_class_override=route_class
    )
    concrete = path.replace("{service_case_id}", "case-safe")
    response = TestClient(app, raise_server_exceptions=False).request(method, concrete)
    assert response.status_code == 503
    assert response.json() == {
        "code": expected_code,
        "message": "request rejected",
        "request_id": REQUEST_ID,
        "retryable": retryable,
        "field_errors": [],
    }
    assert session.commit_count == 1
    assert session.rollback_count == 1


def test_C2_2_R05_真实Slice4服务类型化出口且通用同文本异常仍为500() -> None:
    from app.modules.user_health.service import create_formal_health_facts

    async def failed_endpoint() -> None:
        await create_formal_health_facts(
            None,
            actor_user_id=1,
            actor_context="UNREGISTERED_CONTEXT",
            subject_member_id=UUID("01990000-0000-7000-8000-000000000225"),
            subject_user_id=None,
            service_case_id=UUID("01990000-0000-7000-8000-000000000226"),
            enrollment_id=UUID("01990000-0000-7000-8000-000000000227"),
            payload=None,
            idempotency_key="c22-user-health-typed",
        )

    app = FastAPI()
    install_error_contract(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = REQUEST_ID
        response = await call_next(request)
        response.headers["X-Request-ID"] = REQUEST_ID
        return response

    path = "/api/v1/family/health-indicators"
    app.router.add_api_route(
        path, failed_endpoint, methods=["POST"], route_class_override=Slice4Route
    )
    response = TestClient(app).post(
        "/api/v1/family/health-indicators"
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"

    async def generic_endpoint() -> None:
        raise RuntimeError("ACTOR_CURRENTNESS_FORBIDDEN")

    generic_path = "/api/v1/family/health-profile"
    app.router.add_api_route(
        generic_path, generic_endpoint, methods=["PUT"], route_class_override=Slice4Route
    )
    generic = TestClient(app).put(
        "/api/v1/family/health-profile"
    )
    assert generic.status_code == 500
    assert generic.json()["code"] == "INTERNAL_ERROR"


def test_C2_2_R02_private_content成功OpenAPI仅声明二进制流() -> None:
    from app.main import create_app

    operation = create_app().openapi()["paths"]["/api/v1/private-files/{file_id}/content"]["get"]
    success = operation["responses"]["200"]["content"]
    assert set(success) == {"application/octet-stream"}
    assert success["application/octet-stream"]["schema"] == {"type": "string", "format": "binary"}


@pytest.mark.parametrize(
    ("detail", "expected_code"),
    [
        ("ONBOARDING_VERSION_CONFLICT", "ONBOARDING_VERSION_CONFLICT"),
        ("synthetic vendor SQL detail", "CONFLICT"),
    ],
)
def test_C2_2_R03_onboarding仅保留显式领域码(detail: str, expected_code: str) -> None:
    from fastapi import HTTPException

    app = FastAPI()
    install_error_contract(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = REQUEST_ID
        response = await call_next(request)
        response.headers["X-Request-ID"] = REQUEST_ID
        return response

    async def failed_endpoint() -> None:
        raise HTTPException(409, detail)

    app.router.add_api_route("/onboarding-probe", failed_endpoint, methods=["GET"], route_class_override=OnboardingRoute)
    response = TestClient(app).get("/onboarding-probe")
    assert response.status_code == 409
    assert response.json() == {
        "code": expected_code,
        "message": "request rejected",
        "request_id": REQUEST_ID,
        "retryable": False,
        "field_errors": [],
    }
    assert detail not in response.text if expected_code == "CONFLICT" else True


class _FailingSlice5Session:
    def __init__(self, driver_error: BaseException) -> None:
        self.driver_error = driver_error

    async def execute(self, _statement, _parameters):
        raise DBAPIError("SELECT", {}, self.driver_error, False)


class _MappingsResult:
    def mappings(self):
        return self

    def one(self) -> dict[str, object]:
        return {"value": "not-a-sha256-digest"}


class _InvalidSlice6PostimageSession:
    async def execute(self, _statement, _parameters):
        return _MappingsResult()


class _ValueMappingsResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def mappings(self):
        return self

    def one(self) -> dict[str, object]:
        return {"value": self.value}


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value

    def scalar_one_or_none(self) -> object:
        return self.value


class _FailingSlice6Session:
    def __init__(self, code: str) -> None:
        self.driver_error = asyncpg.exceptions.RaiseError(code)

    async def execute(self, statement, _parameters):
        if "slice6_mutation_expected_v1" in str(statement):
            return _ValueMappingsResult("0" * 64)
        raise DBAPIError("SELECT", {}, self.driver_error, False)


class _FailingScalarSession:
    def __init__(self, code: str) -> None:
        self.driver_error = asyncpg.exceptions.RaiseError(code)

    async def execute(self, _statement, _parameters):
        raise DBAPIError("SELECT", {}, self.driver_error, False)


class _DirectFailSession:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def execute(self, _statement, _parameters):
        raise self.error


def _repository_error_response(
    route_class,
    method: str,
    path: str,
    endpoint,
) -> object:
    app = FastAPI()
    install_error_contract(app)

    @app.middleware("http")
    async def request_context(request, call_next):
        request.state.request_id = REQUEST_ID
        response = await call_next(request)
        response.headers["X-Request-ID"] = REQUEST_ID
        return response

    app.router.add_api_route(
        path,
        endpoint,
        methods=[method],
        route_class_override=route_class,
    )
    concrete = path
    for parameter in (
        "version_id", "service_case_id", "case_id", "fact_ref", "assessment_id",
        "task_id", "review_id", "plan_id", "template_version_id", "milestone_id",
        "summary_id", "transfer_id", "export_id",
    ):
        concrete = concrete.replace(
            "{" + parameter + "}",
            "01990000-0000-7000-8000-000000000229",
        )
    return TestClient(app, raise_server_exceptions=False).request(method, concrete)


def test_C2_2_R03_Slice5精确数据库领域码经Repository进入409且未知诊断保持500() -> None:
    from app.modules.health_assessment.repository import HealthAssessmentRepository

    async def known_endpoint() -> None:
        repository = HealthAssessmentRepository(
            _FailingSlice5Session(asyncpg.exceptions.RaiseError("RULE_STATE_CONFLICT"))
        )
        await repository.govern_rule_set("UPDATE_DRAFT", {})

    path = "/api/v1/platform/assessment-rule-sets/{version_id}/draft"
    known = _repository_error_response(Slice5Route, "PATCH", path, known_endpoint)
    assert known.status_code == 409
    assert known.json() == {
        "code": "RULE_STATE_CONFLICT",
        "message": "request rejected",
        "request_id": REQUEST_ID,
        "retryable": False,
        "field_errors": [],
    }

    async def unknown_endpoint() -> None:
        repository = HealthAssessmentRepository(
            _FailingSlice5Session(asyncpg.exceptions.RaiseError("SYNTHETIC_VENDOR_DETAIL"))
        )
        await repository.govern_rule_set("UPDATE_DRAFT", {})

    unknown = _repository_error_response(Slice5Route, "PATCH", path, unknown_endpoint)
    assert unknown.status_code == 500
    assert unknown.json()["code"] == "INTERNAL_ERROR"
    assert "SYNTHETIC_VENDOR_DETAIL" not in unknown.text


@pytest.mark.parametrize(
    ("driver_error", "operation"),
    [
        (asyncpg.exceptions.UniqueViolationError("RULE_STATE_CONFLICT"), "UPDATE_DRAFT"),
        (asyncpg.exceptions.RaiseError("RULE_STATE_CONFLICT"), "CREATE"),
        (RuntimeError("RULE_STATE_CONFLICT"), "UPDATE_DRAFT"),
    ],
    ids=("other-sqlstate", "unauthorized-operation", "forged-attributes"),
)
def test_C2_2_R03_Slice5非精确驱动诊断不得翻译(driver_error, operation: str) -> None:
    from app.modules.health_assessment.repository import HealthAssessmentRepository

    if type(driver_error) is RuntimeError:
        driver_error.sqlstate = "P0001"

    async def endpoint() -> None:
        repository = HealthAssessmentRepository(_FailingSlice5Session(driver_error))
        await repository.govern_rule_set(operation, {})

    path = "/api/v1/platform/assessment-rule-sets/{version_id}/draft"
    response = _repository_error_response(Slice5Route, "PATCH", path, endpoint)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "RULE_STATE_CONFLICT" not in response.text


def test_C2_2_R03_Slice5非目标Repository调用点不得翻译() -> None:
    from app.modules.health_assessment.repository import HealthAssessmentRepository

    async def endpoint() -> None:
        repository = HealthAssessmentRepository(
            _FailingSlice5Session(asyncpg.exceptions.RaiseError("RULE_STATE_CONFLICT"))
        )
        await repository.actor_read_is_current(
            actor_user_id=1,
            actor_role="expert",
            service_case_id=UUID("01990000-0000-7000-8000-000000000230"),
            subject_member_id=UUID("01990000-0000-7000-8000-000000000231"),
            tenant_id=1,
        )

    path = "/api/v1/platform/assessment-rule-sets/{version_id}/draft"
    response = _repository_error_response(Slice5Route, "PATCH", path, endpoint)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"


def test_C2_2_R03_Slice6预期后像异常经Repository进入503且普通同文本异常保持500() -> None:
    from app.modules.health_plan.repository import HealthPlanRepository

    async def repository_endpoint() -> None:
        repository = HealthPlanRepository(_InvalidSlice6PostimageSession())
        await repository._prepare_mutation("PLAN_GENERATION_REQUEST", {})

    translated = _repository_error_response(
        Slice6Route,
        "POST",
        "/api/v1/institutions/service-cases/{service_case_id}/plan-generations",
        repository_endpoint,
    )
    assert translated.status_code == 503
    assert translated.json() == {
        "code": "COMMIT_OUTCOME_UNKNOWN",
        "message": "request rejected",
        "request_id": REQUEST_ID,
        "retryable": False,
        "field_errors": [],
    }

    async def generic_endpoint() -> None:
        raise RuntimeError("COMMIT_OUTCOME_UNKNOWN")

    generic = _repository_error_response(
        Slice6Route,
        "POST",
        "/api/v1/institutions/service-cases/{service_case_id}/plan-generations",
        generic_endpoint,
    )
    assert generic.status_code == 500
    assert generic.json()["code"] == "INTERNAL_ERROR"


@pytest.mark.parametrize(
    ("callpoint", "raw_code", "public_code", "method", "path"),
    [
        ("profile", "SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN", "ACTOR_CURRENTNESS_FORBIDDEN", "PUT", "/api/v1/family/health-profile"),
        ("profile", "SLICE4_VERSION_CONFLICT", "VERSION_CONFLICT", "PUT", "/api/v1/family/health-profile"),
        ("profile", "SLICE4_COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", "PUT", "/api/v1/family/health-profile"),
        ("report", "SLICE4_DETECTION_REPORT_FORBIDDEN", "ACTOR_CURRENTNESS_FORBIDDEN", "POST", "/api/v1/family/detection-reports"),
        ("report", "SLICE4_DETECTION_REPORT_INVALID", "INVALID_REQUEST", "POST", "/api/v1/family/detection-reports"),
        ("report", "SLICE4_PRIVATE_FILE_BIND_CONFLICT", "PRIVATE_FILE_BIND_CONFLICT", "POST", "/api/v1/family/detection-reports"),
        ("report", "SLICE4_COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", "POST", "/api/v1/family/detection-reports"),
        ("fact", "SLICE4_HEALTH_FACT_NOT_FOUND", "HEALTH_FACT_NOT_FOUND", "POST", "/api/v1/therapist/service-cases/{case_id}/health-facts/{fact_ref}/verify"),
        ("fact", "SLICE4_HEALTH_FACT_STATE_INVALID", "INVALID_REQUEST", "POST", "/api/v1/therapist/service-cases/{case_id}/health-facts/{fact_ref}/verify"),
        ("fact", "SLICE4_HEALTH_FACT_STATE_CONFLICT", "STATE_CONFLICT", "POST", "/api/v1/therapist/service-cases/{case_id}/health-facts/{fact_ref}/verify"),
        ("fact", "SLICE4_HEALTH_FACT_STATE_FORBIDDEN", "ACTOR_CURRENTNESS_FORBIDDEN", "POST", "/api/v1/therapist/service-cases/{case_id}/health-facts/{fact_ref}/verify"),
        ("fact", "SLICE4_COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", "POST", "/api/v1/therapist/service-cases/{case_id}/health-facts/{fact_ref}/verify"),
    ],
)
def test_C2_2_R03_Slice4公开写调用点精确数据库码翻译(
    callpoint: str, raw_code: str, public_code: str, method: str, path: str
) -> None:
    from app.modules.user_health.repository import Slice4HealthRecordRepository

    async def endpoint() -> None:
        repository = Slice4HealthRecordRepository(_FailingSlice5Session(asyncpg.exceptions.RaiseError(raw_code)))
        if callpoint == "profile":
            await repository.create_profile_root(changed_fields={})
        elif callpoint == "report":
            await repository.create_detection_report()
        else:
            await repository.health_fact_state_transition()

    response = _repository_error_response(Slice4Route, method, path, endpoint)
    assert response.status_code == SLICE4_STATUS[public_code]
    assert response.json()["code"] == public_code
    assert response.json()["retryable"] is False
    assert raw_code not in response.json()["message"]


@pytest.mark.parametrize(
    ("callpoint", "operation", "raw_code", "public_code", "path"),
    [
        ("replay", "", "IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_CONFLICT", "/api/v1/therapist/service-cases/{case_id}/assessments"),
        ("replay", "", "SLICE5_COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", "/api/v1/therapist/service-cases/{case_id}/assessments"),
        ("write", "", "SLICE5_INVALID_PAYLOAD", "INVALID_REQUEST", "/api/v1/therapist/service-cases/{case_id}/assessments"),
        ("write", "", "ASSEMBLY_STALE", "ASSEMBLY_STALE", "/api/v1/therapist/service-cases/{case_id}/assessments"),
        ("dispute", "", "VERSION_CONFLICT", "VERSION_CONFLICT", "/api/v1/family/assessments/{assessment_id}/disputes"),
        ("dispute", "", "SLICE5_COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", "/api/v1/family/assessments/{assessment_id}/disputes"),
        ("risk", "", "HIGH_RISK_TASK_NOT_FOUND", "HIGH_RISK_TASK_NOT_FOUND", "/api/v1/therapist/high-risk-tasks/{task_id}/actions"),
        ("risk", "", "INVALID_TASK_TRANSITION", "INVALID_TASK_TRANSITION", "/api/v1/therapist/high-risk-tasks/{task_id}/actions"),
        ("rule", "CREATE", "RULE_SET_INVALID", "INVALID_REQUEST", "/api/v1/platform/assessment-rule-sets"),
        ("rule", "UPDATE_DRAFT", "RULE_STATE_CONFLICT", "RULE_STATE_CONFLICT", "/api/v1/platform/assessment-rule-sets/{version_id}/draft"),
        ("rule", "SUBMIT", "RULE_GOVERNANCE_FORBIDDEN", "RULE_GOVERNANCE_FORBIDDEN", "/api/v1/platform/assessment-rule-sets/{version_id}/submit"),
        ("rule", "PUBLISH", "SLICE5_COMMIT_OUTCOME_UNKNOWN", "COMMIT_OUTCOME_UNKNOWN", "/api/v1/platform/assessment-rule-sets/{version_id}/publish"),
    ],
)
def test_C2_2_R03_Slice5公开写调用点与operation精确数据库码翻译(
    callpoint: str, operation: str, raw_code: str, public_code: str, path: str
) -> None:
    from app.modules.health_assessment.repository import HealthAssessmentRepository

    async def endpoint() -> None:
        repository = HealthAssessmentRepository(_FailingSlice5Session(asyncpg.exceptions.RaiseError(raw_code)))
        if callpoint == "replay":
            await repository.assessment_start_replay(1, "key", b"digest")
        elif callpoint == "write":
            await repository.write_assessment_start({})
        elif callpoint == "dispute":
            await repository.raise_dispute({})
        elif callpoint == "risk":
            await repository.transition_high_risk_task({})
        else:
            await repository.govern_rule_set(operation, {})

    method = "PATCH" if operation == "UPDATE_DRAFT" else "POST"
    response = _repository_error_response(Slice5Route, method, path, endpoint)
    assert response.status_code == SLICE5_STATUS[public_code]
    assert response.json()["code"] == public_code
    assert response.json()["retryable"] is False
    assert raw_code not in response.json()["message"]


@pytest.mark.parametrize(
    ("callpoint", "operation", "raw_code", "path"),
    [
        ("replay", "PLAN_TEMPLATE_CREATE", "IDEMPOTENCY_CONFLICT", "/api/v1/platform/health-plan-templates"),
        ("generation", "", "SERVICE_CASE_NOT_CURRENT", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations"),
        ("generation", "", "INVALID_REQUEST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations"),
        ("generation", "", "ACTIVE_PLAN_CONFLICT", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations"),
        ("review", "CLAIM", "STALE_VERSION", "/api/v1/platform/health-plan-reviews/{review_id}/claim"),
        ("review", "DECIDE", "REVIEW_DECISION_CONFLICT", "/api/v1/platform/health-plan-reviews/{review_id}/decision"),
        ("explain", "", "PLAN_NOT_FOUND", "/api/v1/therapist/plans/{plan_id}/explanations"),
        ("decide", "", "USER_DECISION_CONFLICT", "/api/v1/family/plans/{plan_id}/decision"),
        ("template", "CREATE", "INVALID_REQUEST", "/api/v1/platform/health-plan-templates"),
        ("template", "PUBLISH", "STALE_VERSION", "/api/v1/platform/health-plan-templates/{template_version_id}/publish"),
    ],
)
def test_C2_2_R03_Slice6公开写调用点与operation精确数据库码翻译(
    callpoint: str, operation: str, raw_code: str, path: str
) -> None:
    from app.modules.health_plan.repository import HealthPlanRepository

    async def endpoint() -> None:
        repository = HealthPlanRepository(_FailingSlice6Session(raw_code))
        if callpoint == "replay":
            await repository.mutation_replay(1, operation, "key", "0" * 64)
        elif callpoint == "generation":
            await repository.create_generation_request({})
        elif callpoint == "review":
            await repository.transition_review(operation, {})
        elif callpoint == "explain":
            await repository.explain_plan({})
        elif callpoint == "decide":
            await repository.decide_plan({})
        else:
            await repository.govern_template(operation, {})

    response = _repository_error_response(Slice6Route, "POST", path, endpoint)
    assert response.status_code == SLICE6_STATUS[raw_code]
    assert response.json()["code"] == raw_code
    assert raw_code not in response.json()["message"]


def _slice7_payload() -> dict[str, object]:
    return {
        key: "0" * 64 if "digest" in key else "value"
        for key in (
            "actor_scope", "actor_user_id", "actor_role", "target_id", "operation_id",
            "idempotency_key", "request_digest", "expected_response_digest", "response",
            "occurred_at", "audit_id", "event_id", "receipt_id", "lifecycle_event_id",
        )
    }


@pytest.mark.parametrize(
    ("operation", "raw_code", "public_code", "path"),
    [
        ("COMPLETE_MILESTONE", "STALE_VERSION", "STALE_VERSION", "/api/v1/therapist/milestones/{milestone_id}/complete"),
        ("PAUSE_CASE", "CURRENTNESS_FORBIDDEN", "CURRENTNESS_FORBIDDEN", "/api/v1/therapist/service-cases/{case_id}/pause"),
        ("ACK_SUMMARY", "CLOSURE_PREREQUISITE_MISSING", "CLOSURE_PREREQUISITE_MISSING", "/api/v1/family/service-summaries/{summary_id}/acknowledge"),
        ("ACCEPT_TRANSFER", "TRANSFER_STATE_CONFLICT", "TRANSFER_STATE_CONFLICT", "/api/v1/institutions/service-transfers/{transfer_id}/accept"),
        ("EXPORT_DOWNLOAD_ACCESS", "EXPORT_NOT_READY", "EXPORT_NOT_READY", "/api/v1/family/data-exports/{export_id}/download-access"),
        ("CREATE_EXPORT", "IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_CONFLICT", "/api/v1/family/data-exports"),
    ],
)
def test_C2_2_R03_Slice7公开mutation_operation精确数据库码翻译(
    operation: str, raw_code: str, public_code: str, path: str
) -> None:
    from app.modules.service_fulfillment.repository import ServiceFulfillmentRepository

    async def endpoint() -> None:
        repository = ServiceFulfillmentRepository(_FailingScalarSession(raw_code))
        await repository.mutate(operation, _slice7_payload())

    response = _repository_error_response(Slice7Route, "POST", path, endpoint)
    assert response.status_code == {
        "ROLE_FORBIDDEN": 403,
        "CURRENTNESS_FORBIDDEN": 403,
        "STALE_VERSION": 409,
        "IDEMPOTENCY_CONFLICT": 409,
        "CLOSURE_PREREQUISITE_MISSING": 409,
        "TRANSFER_STATE_CONFLICT": 409,
        "EXPORT_NOT_READY": 409,
    }[public_code]
    assert response.json()["code"] == public_code
    assert raw_code not in response.json()["message"]


@pytest.mark.parametrize(
    ("route_class", "path", "repository_call"),
    [
        (
            Slice4Route,
            "/api/v1/family/detection-reports",
            lambda session: __import__(
                "app.modules.user_health.repository", fromlist=["Slice4HealthRecordRepository"]
            ).Slice4HealthRecordRepository(session).create_detection_report(),
        ),
        (
            Slice5Route,
            "/api/v1/platform/assessment-rule-sets",
            lambda session: __import__(
                "app.modules.health_assessment.repository", fromlist=["HealthAssessmentRepository"]
            ).HealthAssessmentRepository(session).govern_rule_set("CREATE", {}),
        ),
        (
            Slice6Route,
            "/api/v1/platform/health-plan-templates",
            lambda session: __import__(
                "app.modules.health_plan.repository", fromlist=["HealthPlanRepository"]
            ).HealthPlanRepository(session).mutation_replay(1, "PLAN_TEMPLATE_CREATE", "key", "0" * 64),
        ),
        (
            Slice7Route,
            "/api/v1/family/data-exports",
            lambda session: __import__(
                "app.modules.service_fulfillment.repository", fromlist=["ServiceFulfillmentRepository"]
            ).ServiceFulfillmentRepository(session).replay(1, "CREATE_EXPORT", "key", "0" * 64),
        ),
    ],
    ids=("slice4", "slice5", "slice6", "slice7"),
)
@pytest.mark.parametrize(
    "driver_error",
    [
        asyncpg.exceptions.UniqueViolationError("IDEMPOTENCY_CONFLICT"),
        RuntimeError("IDEMPOTENCY_CONFLICT"),
        asyncpg.exceptions.RaiseError("IDEMPOTENCY_CONFLICT", "extra"),
        asyncpg.exceptions.RaiseError("UNREGISTERED_DATABASE_CODE"),
    ],
    ids=("other-sqlstate", "forged-runtime", "non-single-args", "unknown-code"),
)
def test_C2_2_R03_Repository数据库翻译拒绝非精确诊断(
    route_class, path: str, repository_call: Callable, driver_error: BaseException
) -> None:
    if type(driver_error) is RuntimeError:
        driver_error.sqlstate = "P0001"

    async def endpoint() -> None:
        await repository_call(_FailingSlice5Session(driver_error))

    response = _repository_error_response(route_class, "POST", path, endpoint)
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "IDEMPOTENCY_CONFLICT" not in response.json()["message"]
    assert "UNREGISTERED_DATABASE_CODE" not in response.text


def test_C2_2_R03_Repository只接受orig或一层cause中真实asyncpg异常() -> None:
    from app.modules.health_assessment.repository import HealthAssessmentRepository

    wrapper = RuntimeError("safe wrapper")
    wrapper.__cause__ = asyncpg.exceptions.RaiseError("RULE_SET_INVALID")

    async def endpoint() -> None:
        repository = HealthAssessmentRepository(_FailingSlice5Session(wrapper))
        await repository.govern_rule_set("CREATE", {})

    response = _repository_error_response(
        Slice5Route, "POST", "/api/v1/platform/assessment-rule-sets", endpoint
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "repository_call",
    [
        lambda session: __import__(
            "app.modules.user_health.repository", fromlist=["Slice4HealthRecordRepository"]
        ).Slice4HealthRecordRepository(session).create_detection_report(),
        lambda session: __import__(
            "app.modules.health_assessment.repository", fromlist=["HealthAssessmentRepository"]
        ).HealthAssessmentRepository(session).govern_rule_set("CREATE", {}),
        lambda session: __import__(
            "app.modules.health_plan.repository", fromlist=["HealthPlanRepository"]
        ).HealthPlanRepository(session).mutation_replay(1, "PLAN_TEMPLATE_CREATE", "key", "0" * 64),
        lambda session: __import__(
            "app.modules.service_fulfillment.repository", fromlist=["ServiceFulfillmentRepository"]
        ).ServiceFulfillmentRepository(session).replay(1, "CREATE_EXPORT", "key", "0" * 64),
    ],
    ids=("slice4", "slice5", "slice6", "slice7"),
)
def test_C2_2_R03_Repository取消必须原样传播(repository_call: Callable) -> None:
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(repository_call(_DirectFailSession(asyncio.CancelledError())))


def test_C2_2_R03_Slice7Worker调用点不得借用公开HTTP翻译() -> None:
    from app.modules.service_fulfillment.repository import ServiceFulfillmentRepository

    async def exercise() -> None:
        repository = ServiceFulfillmentRepository(_FailingScalarSession("STALE_VERSION"))
        await repository.mutate("MARK_MISSED", _slice7_payload())

    with pytest.raises(DBAPIError):
        asyncio.run(exercise())


def test_C2_2_R03_Slice4每个Repository登记码均可达对应写路由与OpenAPI() -> None:
    from app.modules.user_health.repository import (
        _DATABASE_ERRORS,
        Slice4HealthRecordRepository,
    )

    routes = {
        "CREATE_PROFILE_ROOT": ("PUT", "/api/v1/family/health-profile"),
        "CREATE_DETECTION_REPORT": ("POST", "/api/v1/family/detection-reports"),
        "HEALTH_FACT_STATE_TRANSITION": (
            "POST",
            "/api/v1/therapist/service-cases/{case_id}/health-facts/{fact_ref}/verify",
        ),
    }

    for callpoint, errors in _DATABASE_ERRORS.items():
        method, path = routes[callpoint]
        for raw_code, public_code in errors.items():
            case_path = (
                "/api/v1/therapist/service-cases/{case_id}/"
                "health-facts/{fact_ref}/corrections"
                if raw_code == "SLICE4_HEALTH_FACT_CORRECTION_CONFLICT"
                else path
            )

            async def endpoint(
                callpoint: str = callpoint, raw_code: str = raw_code
            ) -> None:
                repository = Slice4HealthRecordRepository(
                    _FailingSlice5Session(asyncpg.exceptions.RaiseError(raw_code))
                )
                if callpoint == "CREATE_PROFILE_ROOT":
                    await repository.create_profile_root(changed_fields={})
                elif callpoint == "CREATE_DETECTION_REPORT":
                    await repository.create_detection_report()
                else:
                    await repository.health_fact_state_transition()

            response = _repository_error_response(Slice4Route, method, case_path, endpoint)
            assert response.status_code != 500, (callpoint, raw_code)
            assert response.json()["code"] == public_code
            assert public_code in SLICE4_ROUTE_ERROR_CODES[(method, case_path)]


def test_C2_2_R03_Slice5每个Repository登记码均可达对应写路由与OpenAPI() -> None:
    from app.modules.health_assessment.repository import (
        _DATABASE_ERRORS,
        HealthAssessmentRepository,
    )

    routes = {
        "ASSESSMENT_START_REPLAY": (
            "POST", "/api/v1/therapist/service-cases/{case_id}/assessments"
        ),
        "ASSESSMENT_START_WRITE": (
            "POST", "/api/v1/therapist/service-cases/{case_id}/assessments"
        ),
        "RAISE_DISPUTE": ("POST", "/api/v1/family/assessments/{assessment_id}/disputes"),
        "HIGH_RISK_TRANSITION": (
            "POST", "/api/v1/therapist/high-risk-tasks/{task_id}/actions"
        ),
        "RULE_CREATE": ("POST", "/api/v1/platform/assessment-rule-sets"),
        "RULE_UPDATE_DRAFT": (
            "PATCH", "/api/v1/platform/assessment-rule-sets/{version_id}/draft"
        ),
        "RULE_SUBMIT": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/submit"
        ),
        "RULE_REVIEW_APPROVE": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/review"
        ),
        "RULE_REVIEW_CORRECTION": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/review"
        ),
        "RULE_PUBLISH": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/publish"
        ),
        "RULE_SUSPEND": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/suspend"
        ),
        "RULE_RESUME": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/resume"
        ),
        "RULE_RETIRE": (
            "POST", "/api/v1/platform/assessment-rule-sets/{version_id}/retire"
        ),
    }

    for callpoint, errors in _DATABASE_ERRORS.items():
        method, path = routes[callpoint]
        for raw_code, public_code in errors.items():
            async def endpoint(
                callpoint: str = callpoint, raw_code: str = raw_code
            ) -> None:
                repository = HealthAssessmentRepository(
                    _FailingSlice5Session(asyncpg.exceptions.RaiseError(raw_code))
                )
                if callpoint == "ASSESSMENT_START_REPLAY":
                    await repository.assessment_start_replay(1, "key", b"digest")
                elif callpoint == "ASSESSMENT_START_WRITE":
                    await repository.write_assessment_start({})
                elif callpoint == "RAISE_DISPUTE":
                    await repository.raise_dispute({})
                elif callpoint == "HIGH_RISK_TRANSITION":
                    await repository.transition_high_risk_task({})
                else:
                    await repository.govern_rule_set(callpoint.removeprefix("RULE_"), {})

            response = _repository_error_response(Slice5Route, method, path, endpoint)
            assert response.status_code != 500, (callpoint, raw_code)
            assert response.json()["code"] == public_code
            assert public_code in SLICE5_ROUTE_ERROR_CODES[(method, path)]


def test_C2_2_R03_Slice6每个Repository登记码均可达对应写路由与OpenAPI() -> None:
    from app.modules.health_plan.repository import (
        _DATABASE_ERRORS,
        HealthPlanRepository,
    )

    routes = {
        "MUTATION_REPLAY": ("POST", "/api/v1/platform/health-plan-templates"),
        "GENERATION_REQUEST": (
            "POST", "/api/v1/institutions/service-cases/{service_case_id}/plan-generations"
        ),
        "REVIEW_CLAIM": (
            "POST", "/api/v1/platform/health-plan-reviews/{review_id}/claim"
        ),
        "REVIEW_DECIDE": (
            "POST", "/api/v1/platform/health-plan-reviews/{review_id}/decision"
        ),
        "PLAN_EXPLANATION": (
            "POST", "/api/v1/therapist/plans/{plan_id}/explanations"
        ),
        "PLAN_USER_DECISION": (
            "POST", "/api/v1/family/plans/{plan_id}/decision"
        ),
        "TEMPLATE_CREATE": ("POST", "/api/v1/platform/health-plan-templates"),
        "TEMPLATE_PUBLISH": (
            "POST", "/api/v1/platform/health-plan-templates/{template_version_id}/publish"
        ),
        "TEMPLATE_RETIRE": (
            "POST", "/api/v1/platform/health-plan-templates/{template_version_id}/retire"
        ),
    }

    for callpoint, errors in _DATABASE_ERRORS.items():
        method, path = routes[callpoint]
        for raw_code in errors:
            async def endpoint(
                callpoint: str = callpoint, raw_code: str = raw_code
            ) -> None:
                repository = HealthPlanRepository(_FailingSlice6Session(raw_code))
                if callpoint == "MUTATION_REPLAY":
                    await repository.mutation_replay(
                        1, "PLAN_TEMPLATE_CREATE", "key", "0" * 64
                    )
                elif callpoint == "GENERATION_REQUEST":
                    await repository.create_generation_request({})
                elif callpoint.startswith("REVIEW_"):
                    await repository.transition_review(
                        callpoint.removeprefix("REVIEW_"), {}
                    )
                elif callpoint == "PLAN_EXPLANATION":
                    await repository.explain_plan({})
                elif callpoint == "PLAN_USER_DECISION":
                    await repository.decide_plan({})
                else:
                    await repository.govern_template(
                        callpoint.removeprefix("TEMPLATE_"), {}
                    )

            response = _repository_error_response(Slice6Route, method, path, endpoint)
            assert response.status_code != 500, (callpoint, raw_code)
            assert response.json()["code"] == raw_code
            assert raw_code in SLICE6_ROUTE_ERROR_CODES[(method, path)]


def test_C2_2_R03_Slice7每个公开operation登记码均可达具体写路由与OpenAPI() -> None:
    from app.modules.service_fulfillment.repository import (
        _CURRENTNESS_OPERATIONS,
        ServiceFulfillmentRepository,
        _mutation_errors,
    )

    routes = {
        "COMPLETE_MILESTONE": "/api/v1/therapist/milestones/{milestone_id}/complete",
        "PAUSE_CASE": "/api/v1/therapist/service-cases/{case_id}/pause",
        "RESUME_CASE": "/api/v1/therapist/service-cases/{case_id}/resume",
        "WITHDRAW_CASE": "/api/v1/family/service-cases/{case_id}/withdraw",
        "TERMINATE_CASE": "/api/v1/institutions/service-cases/{case_id}/terminate",
        "UNABLE_TO_CONTACT": "/api/v1/therapist/service-cases/{case_id}/unable-to-contact",
        "SAFETY_TERMINATE": "/api/v1/therapist/service-cases/{case_id}/safety-terminate",
        "CREATE_CLOSING_ASSESSMENT": "/api/v1/therapist/service-cases/{case_id}/closing-assessments",
        "CREATE_SUMMARY": "/api/v1/therapist/service-cases/{case_id}/summaries",
        "ACK_SUMMARY": "/api/v1/family/service-summaries/{summary_id}/acknowledge",
        "CREATE_TRANSFER": "/api/v1/family/service-cases/{case_id}/transfers",
        "CANCEL_TRANSFER": "/api/v1/family/service-transfers/{transfer_id}/cancel",
        "CONFIRM_TRANSFER_SCOPE": "/api/v1/family/service-transfers/{transfer_id}/confirm-scope",
        "START_REVIEW_TRANSFER": "/api/v1/institutions/service-transfers/{transfer_id}/start-review",
        "ACCEPT_TRANSFER": "/api/v1/institutions/service-transfers/{transfer_id}/accept",
        "REJECT_TRANSFER": "/api/v1/institutions/service-transfers/{transfer_id}/reject",
        "SOURCE_CLOSE_TRANSFER": "/api/v1/institutions/service-transfers/{transfer_id}/source-close",
        "COORDINATE_TRANSFER_CLOSE": "/api/v1/platform/service-transfers/{transfer_id}/coordinate-close",
        "LINK_CONTINUATION_CASE": "/api/v1/institutions/service-transfers/{transfer_id}/continuation-case",
        "AUTHORIZE_PROXY_MAJOR": "/api/v1/platform/proxy-major-authorizations",
        "REVOKE_PROXY_MAJOR": "/api/v1/platform/proxy-major-authorizations/{authorization_id}/revoke",
        "CREATE_EXPORT": "/api/v1/family/data-exports",
        "CANCEL_EXPORT": "/api/v1/family/data-exports/{export_id}/cancel",
        "EXPORT_DOWNLOAD_ACCESS": "/api/v1/family/data-exports/{export_id}/download-access",
    }
    assert set(routes) == set(_CURRENTNESS_OPERATIONS)

    for operation, path in routes.items():
        for raw_code, public_code in _mutation_errors(operation).items():
            async def endpoint(
                operation: str = operation, raw_code: str = raw_code
            ) -> None:
                await ServiceFulfillmentRepository(
                    _FailingScalarSession(raw_code)
                ).mutate(operation, _slice7_payload())

            response = _repository_error_response(Slice7Route, "POST", path, endpoint)
            assert response.status_code != 500, (operation, raw_code)
            assert response.json()["code"] == public_code
            assert public_code in SLICE7_ROUTE_ERROR_CODES[("POST", path)]
