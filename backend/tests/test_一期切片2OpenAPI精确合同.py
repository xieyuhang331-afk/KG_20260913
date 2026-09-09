import os

os.environ.setdefault("KG_DATABASE_PASSWORD", "test-only")
os.environ.setdefault("KG_JWT_SECRET_KEY", "test-only-secret-key-value")

from app.main import create_app
from app.core.database import (
    get_db_session,
    get_therapist_onboarding_writer_session,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.modules.therapist_qualification.api import (
    _decode_cursor,
    _encode_cursor,
    _http_error_code,
)


AUTH = "AUTHENTICATION_REQUIRED"
DEPENDENCY = "DEPENDENCY_UNAVAILABLE"
UNKNOWN_COMMIT = "COMMIT_OUTCOME_UNKNOWN"


def _errors(*, bad=None, actor=None, missing=(), conflict=(), mutation=False, auth_extra=()):
    bad_values = (("INVALID_REQUEST",) if mutation else ()) if bad is None else tuple(bad)
    value = {401: tuple((AUTH, *auth_extra)), 503: tuple((DEPENDENCY, UNKNOWN_COMMIT) if mutation else (DEPENDENCY,))}
    if bad_values:
        value[400] = bad_values
    if actor:
        value[403] = (actor,)
    if missing:
        value[404] = tuple(missing)
    if conflict:
        value[409] = tuple(conflict)
    return value


# 独立于api.py的批准合同快照：method/path/success/errors/idempotency/expected_version。
APPROVED_ROUTE_CONTRACTS = {
    ("POST", "/api/v1/institution/therapist-invitations"): (201, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", conflict=("TENANT_NOT_ACTIVE", "THERAPIST_INVITATION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, False),
    ("GET", "/api/v1/institution/therapist-invitations"): (200, _errors(bad=("INVALID_CURSOR",), actor="ACTOR_CURRENTNESS_FORBIDDEN"), False, False),
    ("POST", "/api/v1/institution/therapist-invitations/{invitation_id}/revoke"): (200, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_INVITATION_NOT_FOUND",), conflict=("THERAPIST_INVITATION_STATE_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("GET", "/api/v1/institution/therapists"): (200, _errors(bad=("INVALID_CURSOR",), actor="ACTOR_CURRENTNESS_FORBIDDEN"), False, False),
    ("GET", "/api/v1/institution/therapists/{therapist_id}"): (200, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND",)), False, False),
    ("GET", "/api/v1/institution/service-readiness"): (200, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", missing=("READINESS_NOT_FOUND",)), False, False),
    ("GET", "/api/v1/institution/service-readiness/evidence"): (200, _errors(bad=("INVALID_CURSOR",), actor="ACTOR_CURRENTNESS_FORBIDDEN"), False, False),
    ("POST", "/api/v1/therapist-onboarding/activate"): (201, _errors(bad=("INVALID_REQUEST",), conflict=("IDEMPOTENCY_CONFLICT",), mutation=True, auth_extra=("THERAPIST_INVITATION_INVALID", "THERAPIST_INVITATION_EXPIRED", "THERAPIST_ACTIVATION_ATTEMPTS_EXHAUSTED")), True, False),
    ("GET", "/api/v1/therapist-onboarding/profile"): (200, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND",)), False, False),
    ("PUT", "/api/v1/therapist-onboarding/profile"): (200, _errors(bad=("INVALID_REQUEST", "THERAPIST_FIELD_FORBIDDEN"), actor="ACTOR_CURRENTNESS_FORBIDDEN", conflict=("THERAPIST_VERSION_CONFLICT", "THERAPIST_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/therapist-onboarding/qualifications/submit"): (201, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", conflict=("THERAPIST_SUBMISSION_INCOMPLETE", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_BIND_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("GET", "/api/v1/therapist-onboarding/corrections"): (200, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_CORRECTION_NOT_FOUND",)), False, False),
    ("POST", "/api/v1/therapist-onboarding/resubmit"): (201, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", conflict=("THERAPIST_CORRECTION_SCOPE_CONFLICT", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_BIND_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/therapist-onboarding/qualifications/renew"): (201, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", conflict=("THERAPIST_RENEWAL_CONFLICT", "PRIVATE_FILE_NOT_CLEAN", "PRIVATE_FILE_BIND_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/therapist-onboarding/qualification-renewals/{review_item_id}/resubmit"): (201, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_REVIEW_ITEM_NOT_FOUND",), conflict=("THERAPIST_RENEWAL_CORRECTION_SCOPE_CONFLICT", "THERAPIST_RENEWAL_REVIEW_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/therapist-onboarding/exit"): (200, _errors(actor="ACTOR_CURRENTNESS_FORBIDDEN", conflict=("THERAPIST_ACTIVE_CASES_REMAIN", "THERAPIST_VERSION_CONFLICT", "THERAPIST_STATE_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("GET", "/api/v1/platform/therapist-reviews"): (200, _errors(bad=("INVALID_CURSOR",), actor="REVIEWER_CURRENTNESS_FORBIDDEN"), False, False),
    ("GET", "/api/v1/platform/therapist-reviews/{therapist_id}"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND", "THERAPIST_REVIEW_ITEM_NOT_FOUND")), False, False),
    ("POST", "/api/v1/platform/therapist-reviews/{therapist_id}/decision"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND",), conflict=("THERAPIST_REVIEW_STATE_CONFLICT", "THERAPIST_CORRECTION_FIELD_FORBIDDEN", "THERAPIST_APPROVAL_PRECONDITION_FAILED", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("GET", "/api/v1/platform/therapist-renewal-reviews"): (200, _errors(bad=("INVALID_CURSOR",), actor="REVIEWER_CURRENTNESS_FORBIDDEN"), False, False),
    ("POST", "/api/v1/platform/therapist-renewal-reviews/{review_item_id}/decision"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_REVIEW_ITEM_NOT_FOUND",), conflict=("THERAPIST_RENEWAL_REVIEW_CONFLICT", "THERAPIST_RESUME_PRECONDITION_FAILED", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/suspend"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND",), conflict=("THERAPIST_SUSPEND_STATE_CONFLICT", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/resume"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND",), conflict=("THERAPIST_RESUME_PRECONDITION_FAILED", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/exit"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("THERAPIST_NOT_FOUND",), conflict=("THERAPIST_ACTIVE_CASES_REMAIN", "THERAPIST_VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT"), mutation=True), True, True),
    ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness"): (200, _errors(actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("READINESS_NOT_FOUND",)), False, False),
    ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness/evidence"): (200, _errors(bad=("INVALID_CURSOR",), actor="REVIEWER_CURRENTNESS_FORBIDDEN", missing=("READINESS_NOT_FOUND",)), False, False),
}


APPROVED_ROUTE_SCHEMAS = {
    ("POST", "/api/v1/institution/therapist-invitations"): ("TherapistInvitationCreate", "SuccessEnvelope_InvitationCreatedDTO_", None),
    ("GET", "/api/v1/institution/therapist-invitations"): (None, "SuccessEnvelope_PageDTO_InvitationDTO__", None),
    ("POST", "/api/v1/institution/therapist-invitations/{invitation_id}/revoke"): ("TherapistInvitationRevoke", "SuccessEnvelope_InvitationDTO_", "expected_version"),
    ("GET", "/api/v1/institution/therapists"): (None, "SuccessEnvelope_PageDTO_ProfileDTO__", None),
    ("GET", "/api/v1/institution/therapists/{therapist_id}"): (None, "SuccessEnvelope_TherapistDetailDTO_", None),
    ("GET", "/api/v1/institution/service-readiness"): (None, "SuccessEnvelope_ReadinessDTO_", None),
    ("GET", "/api/v1/institution/service-readiness/evidence"): (None, "SuccessEnvelope_PageDTO_EvidenceDTO__", None),
    ("POST", "/api/v1/therapist-onboarding/activate"): ("TherapistActivate", "SuccessEnvelope_MutationProfileDTO_", None),
    ("GET", "/api/v1/therapist-onboarding/profile"): (None, "SuccessEnvelope_SelfTherapistDetailDTO_", None),
    ("PUT", "/api/v1/therapist-onboarding/profile"): ("TherapistProfileDraft", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("POST", "/api/v1/therapist-onboarding/qualifications/submit"): ("TherapistSubmit", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("GET", "/api/v1/therapist-onboarding/corrections"): (None, "SuccessEnvelope_CorrectionDTO_", None),
    ("POST", "/api/v1/therapist-onboarding/resubmit"): ("TherapistResubmit", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("POST", "/api/v1/therapist-onboarding/qualifications/renew"): ("TherapistRenew", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("POST", "/api/v1/therapist-onboarding/qualification-renewals/{review_item_id}/resubmit"): ("TherapistRenewalResubmit", "SuccessEnvelope_MutationProfileDTO_", "expected_review_version"),
    ("POST", "/api/v1/therapist-onboarding/exit"): ("TherapistStatusRequest", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("GET", "/api/v1/platform/therapist-reviews"): (None, "SuccessEnvelope_PageDTO_ReviewItemDTO__", None),
    ("GET", "/api/v1/platform/therapist-reviews/{therapist_id}"): (None, "SuccessEnvelope_ReviewDetailDTO_", None),
    ("POST", "/api/v1/platform/therapist-reviews/{therapist_id}/decision"): ("TherapistReviewDecisionRequest", "SuccessEnvelope_ReviewDecisionDTO_", "expected_version"),
    ("GET", "/api/v1/platform/therapist-renewal-reviews"): (None, "SuccessEnvelope_PageDTO_ReviewItemDTO__", None),
    ("POST", "/api/v1/platform/therapist-renewal-reviews/{review_item_id}/decision"): ("TherapistReviewDecisionRequest", "SuccessEnvelope_ReviewDecisionDTO_", "expected_version"),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/suspend"): ("TherapistStatusRequest", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/resume"): ("TherapistResumeRequest", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("POST", "/api/v1/platform/therapists/{therapist_id}/exit"): ("TherapistStatusRequest", "SuccessEnvelope_MutationProfileDTO_", "expected_version"),
    ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness"): (None, "SuccessEnvelope_ReadinessDTO_", None),
    ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness/evidence"): (None, "SuccessEnvelope_PageDTO_EvidenceDTO__", None),
}

assert set(APPROVED_ROUTE_SCHEMAS) == set(APPROVED_ROUTE_CONTRACTS)


def test_逐路由typed_schema_status_error与UUIDv7():
    paths = create_app().openapi()["paths"]
    approved_operations = {
        (path, method.lower())
        for method, path in APPROVED_ROUTE_CONTRACTS
    }
    actual_operations = {
        (path, method)
        for path, methods in paths.items()
        for method, value in methods.items()
        if method in {"get", "post", "put"}
        and "therapist_qualification" in value.get("tags", ())
    }
    assert actual_operations == approved_operations
    operations = {
        (path, method): paths[path][method]
        for path, method in approved_operations
    }
    assert len(operations) == 26
    for (_, _), operation in operations.items():
        success = operation["responses"].get("200") or operation["responses"].get("201")
        assert success["content"]["application/json"]["schema"]["$ref"].startswith("#/components/schemas/SuccessEnvelope")
        assert "503" in operation["responses"]
        assert "422" not in operation["responses"]
        for status, response in operation["responses"].items():
            if status in {"200", "201"}:
                continue
            assert response["content"]["application/json"]["schema"]["$ref"].endswith(
                "ErrorResponseDTO"
            )
    for operation in operations.values():
        for parameter in operation.get("parameters", []):
            if parameter["name"] == "cursor":
                string_schema = next(
                    value for value in parameter["schema"]["anyOf"]
                    if value.get("type") == "string"
                )
                assert string_schema["maxLength"] == 512
            if parameter["name"] == "limit":
                assert parameter["schema"]["minimum"] == 1
                assert parameter["schema"]["maximum"] == 100
                assert parameter["schema"]["default"] == 20


def test_不可信cursor逐类型校验且无字符串兜底():
    invitation = _encode_cursor({
        "issued_at": "2026-08-16T10:00:00+08:00",
        "invitation_id": "00000000-0000-7000-8000-000000000001",
    })
    decoded = _decode_cursor(invitation, "invitation")
    assert decoded[1] == "00000000-0000-7000-8000-000000000001"
    evidence = _encode_cursor({"evidence_version": 2})
    assert _decode_cursor(evidence, "evidence") == 2
    for invalid, kind in (
        ("e30", "evidence"),
        (_encode_cursor({"evidence_version": True}), "evidence"),
        (_encode_cursor({"therapist_id": "not-a-uuid"}), "profile"),
        (_encode_cursor({"issued_at": "2026-08-16T10:00:00", "invitation_id": "00000000-0000-7000-8000-000000000001"}), "invitation"),
    ):
        with pytest.raises(HTTPException) as exc:
            _decode_cursor(invalid, kind)
        assert exc.value.status_code == 400
        assert exc.value.detail == "INVALID_CURSOR"


def test_schema拒绝固定400安全错误信封且不进入业务依赖():
    app = create_app()

    async def unused_session():
        yield object()

    app.dependency_overrides[get_therapist_onboarding_writer_session] = unused_session
    app.dependency_overrides[get_db_session] = unused_session
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/therapist-onboarding/activate",
        json={},
    )
    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "request rejected"
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["retryable"] is False and body["field_errors"] == []


def test_未分类依赖异常固定500安全错误信封且不暴露异常文本():
    app = create_app()

    async def unavailable_session():
        raise RuntimeError("vendor detail must not escape")
        yield

    async def unused_session():
        yield object()

    app.dependency_overrides[get_therapist_onboarding_writer_session] = (
        unavailable_session
    )
    app.dependency_overrides[get_db_session] = unused_session
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/therapist-onboarding/activate",
        headers={"Idempotency-Key": "slice2-error-envelope"},
        json={
            "invitation_id": "00000000-0000-7000-8000-000000000001",
            "phone": "1" + "3" + "0" * 9,
            "short_code": "123456",
            "password": "test-only-password",
            "totp_secret": "A" * 16,
            "totp_code": "000000",
        },
    )
    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert body["code"] == "INTERNAL_ERROR"
    assert body["message"] == "request rejected"
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["retryable"] is False and body["field_errors"] == []


def test_每条路由冻结精确symbolic_error_code集合且运行时共用():
    schema = create_app().openapi()
    observed = {}
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            key = (method.upper(), path)
            if key not in APPROVED_ROUTE_CONTRACTS:
                continue
            success_status, expected_errors, needs_idempotency, _ = APPROVED_ROUTE_CONTRACTS[key]
            request_schema, response_schema, version_field = APPROVED_ROUTE_SCHEMAS[key]
            codes = operation.get("x-symbolic-error-codes")
            assert set(codes) == {str(status) for status in expected_errors}
            for status, values in expected_errors.items():
                assert set(codes[str(status)]) == set(values)
            assert str(success_status) in operation["responses"]
            assert set(operation["responses"]) == {str(success_status), *codes, "500"}
            success_ref = operation["responses"][str(success_status)]["content"]["application/json"]["schema"]["$ref"]
            assert success_ref == f"#/components/schemas/{response_schema}"
            headers = {parameter["name"] for parameter in operation.get("parameters", []) if parameter["in"] == "header"}
            assert ("Idempotency-Key" in headers) is needs_idempotency
            body = operation.get("requestBody", {})
            body_schema = body.get("content", {}).get("application/json", {}).get("schema", {})
            if request_schema is None:
                assert body_schema == {}
            else:
                assert body_schema.get("$ref") == f"#/components/schemas/{request_schema}"
                model = schema["components"]["schemas"][request_schema]
                if version_field is not None:
                    assert version_field in model.get("required", ())
            observed[key] = codes
    assert set(observed) == set(APPROVED_ROUTE_CONTRACTS)

    app = create_app()

    async def unknown_business_error():
        raise HTTPException(409, "UNDECLARED_VENDOR_DETAIL")
        yield

    async def unused_session():
        yield object()

    app.dependency_overrides[get_therapist_onboarding_writer_session] = unknown_business_error
    app.dependency_overrides[get_db_session] = unused_session
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/therapist-onboarding/activate",
        headers={"Idempotency-Key": "slice2-error-allowlist"},
        json={
            "invitation_id": "00000000-0000-7000-8000-000000000001",
            "phone": "1" + "3" + "0" * 9,
            "short_code": "123456",
            "password": "test-only-password",
            "totp_secret": "A" * 16,
            "totp_code": "000000",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert body["code"] == "INVALID_REQUEST"
    assert body["message"] == "request rejected"
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["retryable"] is False and body["field_errors"] == []


@pytest.mark.parametrize(
    ("method", "path", "status", "detail", "expected", "undeclared_expected"),
    (
        ("GET", "/api/v1/platform/therapist-reviews/00000000-0000-7000-8000-000000000001", 404, "THERAPIST_REVIEW_ITEM_NOT_FOUND", (404, "THERAPIST_REVIEW_ITEM_NOT_FOUND"), (503, "DEPENDENCY_UNAVAILABLE")),
        ("POST", "/api/v1/platform/therapist-reviews/00000000-0000-7000-8000-000000000001/decision", 409, "THERAPIST_VERSION_CONFLICT", (409, "THERAPIST_VERSION_CONFLICT"), (400, "INVALID_REQUEST")),
        ("PUT", "/api/v1/therapist-onboarding/profile", 400, "INVALID_REQUEST", (400, "INVALID_REQUEST"), (400, "INVALID_REQUEST")),
        ("GET", "/api/v1/platform/therapist-reviews", 403, "REVIEWER_CURRENTNESS_FORBIDDEN", (403, "REVIEWER_CURRENTNESS_FORBIDDEN"), (503, "DEPENDENCY_UNAVAILABLE")),
        ("POST", "/api/v1/platform/therapist-reviews/00000000-0000-7000-8000-000000000001/decision", 503, "COMMIT_OUTCOME_UNKNOWN", (503, "COMMIT_OUTCOME_UNKNOWN"), (503, "DEPENDENCY_UNAVAILABLE")),
    ),
)
def test_真实异常按独立目录映射且未声明code不泄漏(
    method, path, status, detail, expected, undeclared_expected
):
    from starlette.requests import Request

    route_path = path
    for candidate in APPROVED_ROUTE_CONTRACTS:
        if candidate[0] == method and candidate[1].replace("{therapist_id}", "00000000-0000-7000-8000-000000000001") == path:
            route_path = candidate[1]
            break
    allowed = APPROVED_ROUTE_CONTRACTS[(method, route_path)][1]
    request = Request({"type": "http", "method": method, "path": path, "headers": []})
    assert _http_error_code(request, HTTPException(status, detail), allowed) == expected
    actual = _http_error_code(request, HTTPException(status, "UNDECLARED_VENDOR_DETAIL"), allowed)
    assert actual == undeclared_expected
    assert actual[1] in allowed[actual[0]]


@pytest.mark.parametrize(
    ("method", "route_path", "status", "detail", "expected"),
    (
        ("GET", "/api/v1/institution/service-readiness", 404, "not found", (503, "DEPENDENCY_UNAVAILABLE")),
        ("GET", "/api/v1/platform/tenants/{tenant_id}/service-readiness", 404, {"vendor": "hidden"}, (503, "DEPENDENCY_UNAVAILABLE")),
        ("POST", "/api/v1/platform/therapists/{therapist_id}/suspend", 409, "state conflict", (400, "INVALID_REQUEST")),
        ("POST", "/api/v1/platform/therapists/{therapist_id}/resume", 409, None, (400, "INVALID_REQUEST")),
    ),
)
def test_非规范异常回退仍必须属于该路由批准目录(
    method, route_path, status, detail, expected
):
    from starlette.requests import Request

    allowed = APPROVED_ROUTE_CONTRACTS[(method, route_path)][1]
    request_path = (
        route_path.replace("{tenant_id}", "7")
        .replace("{therapist_id}", "00000000-0000-7000-8000-000000000001")
    )
    request = Request({"type": "http", "method": method, "path": request_path, "headers": []})
    actual = _http_error_code(request, HTTPException(status, detail), allowed)
    assert actual == expected
    assert actual[1] in allowed[actual[0]]
