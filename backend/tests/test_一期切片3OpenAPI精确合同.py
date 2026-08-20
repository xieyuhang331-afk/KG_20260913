from uuid import UUID
import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError
from fastapi import FastAPI

from app.modules.member_enrollment.schemas import (
    AcceptEnrollmentRequest,
    CreateMemberInvitationRequest,
    IdentityResubmitRequest,
    RecordConsentRequest,
)

ROOT = Path(__file__).resolve().parents[1]


ROUTES = {
    ("POST", "/api/v1/institution/member-invitations"),
    ("GET", "/api/v1/institution/member-invitations"),
    ("POST", "/api/v1/institution/member-invitations/{invitation_id}/resend"),
    ("POST", "/api/v1/institution/member-invitations/{invitation_id}/revoke"),
    ("GET", "/api/v1/institution/member-enrollments"),
    ("GET", "/api/v1/institution/member-enrollments/{enrollment_id}"),
    ("POST", "/api/v1/institution/member-enrollments/{enrollment_id}/identity-check"),
    ("POST", "/api/v1/institution/member-enrollments/{enrollment_id}/primary-assignments"),
    ("POST", "/api/v1/institution/primary-assignments/{assignment_id}/cancel"),
    ("GET", "/api/v1/institution/service-cases/{case_id}"),
    ("POST", "/api/v1/family/member-enrollments/accept"),
    ("GET", "/api/v1/family/member-enrollments"),
    ("GET", "/api/v1/family/member-enrollments/{enrollment_id}"),
    ("PUT", "/api/v1/family/member-enrollments/{enrollment_id}/identity-submission"),
    ("POST", "/api/v1/family/member-enrollments/{enrollment_id}/identity-resubmit"),
    ("GET", "/api/v1/family/member-enrollments/{enrollment_id}/consent-presentations"),
    ("POST", "/api/v1/family/member-enrollments/{enrollment_id}/consent-records"),
    ("POST", "/api/v1/family/consent-records/{consent_record_id}/withdraw"),
    ("POST", "/api/v1/family/proxy-grants/{grant_id}/revoke"),
    ("GET", "/api/v1/platform/member-identity-reviews"),
    ("GET", "/api/v1/platform/member-identity-reviews/{review_id}"),
    ("POST", "/api/v1/platform/member-identity-reviews/{review_id}/claim"),
    ("POST", "/api/v1/platform/member-identity-reviews/{review_id}/pii-access"),
    ("POST", "/api/v1/platform/member-identity-reviews/{review_id}/decision"),
    ("POST", "/api/v1/platform/consent-documents"),
    ("POST", "/api/v1/platform/consent-documents/{document_version_id}/publish"),
    ("POST", "/api/v1/platform/consent-documents/{document_version_id}/retire"),
    ("GET", "/api/v1/therapist/primary-assignments"),
    ("GET", "/api/v1/therapist/primary-assignments/{assignment_id}"),
    ("POST", "/api/v1/therapist/primary-assignments/{assignment_id}/accept"),
    ("POST", "/api/v1/therapist/primary-assignments/{assignment_id}/decline"),
    ("GET", "/api/v1/therapist/service-cases/{case_id}"),
}


def test_邀请手机号严格为大陆ASCII移动号() -> None:
    assert CreateMemberInvitationRequest(mode="SELF", phone="13800000000").phone == "13800000000"
    for phone in ("１２８００００００００", "12800000000", "138 0000 0000"):
        with pytest.raises(ValidationError):
            CreateMemberInvitationRequest(mode="SELF", phone=phone)


def test_接受短码严格六位ASCII() -> None:
    invitation_id = UUID("0198b963-38f0-7d7d-8000-000000000021")
    AcceptEnrollmentRequest(
        invitation_id=invitation_id,
        phone="13800000000",
        short_code="123456",
    )
    with pytest.raises(ValidationError):
        AcceptEnrollmentRequest(
            invitation_id=invitation_id,
            phone="13800000000",
            short_code="１２３４５６",
        )


def test_身份补正显式null和空patch均拒绝() -> None:
    common = {
        "document_type": "PRC_RESIDENT_ID",
        "expected_version": 2,
    }
    for patch in ({}, {"real_name": None}):
        with pytest.raises(ValidationError):
            IdentityResubmitRequest(**common, **patch)


def test_同意purpose必须唯一排序且不允许未知值() -> None:
    common = {
        "document_version_id": UUID("0198b963-38f0-7d7d-8000-000000000022"),
        "rendition_id": UUID("0198b963-38f0-7d7d-8000-000000000023"),
        "choice": "ACCEPTED",
        "expected_version": 4,
    }
    value = RecordConsentRequest(
        **common,
        purpose_codes=("ACCOUNT_AND_SERVICE_ONBOARDING", "IDENTITY_VERIFICATION"),
    )
    assert value.purpose_codes == tuple(sorted(value.purpose_codes))
    with pytest.raises(ValidationError):
        RecordConsentRequest(**common, purpose_codes=("UNKNOWN",))


def test_32条路由独立冻结() -> None:
    from app.modules.member_enrollment.api import routers

    actual = {
        (method, route.path)
        for router in routers
        for route in router.routes
        for method in getattr(route, "methods", ())
        if route.path.startswith("/api/v1/")
    }
    assert actual == ROUTES


def test_32条路由不得保留依赖不可用占位实现() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api)
    assert "async def _unavailable" not in source
    assert "return await _unavailable()" not in source
    assert "response_model=dict" not in source


def test_32条路由OpenAPI使用独立错误目录且不暴露422() -> None:
    from app.modules.member_enrollment.api import (
        routers,
        strip_member_enrollment_validation_responses,
    )

    app = FastAPI()
    for router in routers:
        app.include_router(router)
    schema = strip_member_enrollment_validation_responses(app.openapi())
    for method, path in ROUTES:
        operation = schema["paths"][path][method.lower()]
        assert "422" not in operation["responses"]
        errors = operation.get("x-symbolic-error-codes")
        assert isinstance(errors, dict) and errors
        assert all(
            isinstance(values, list)
            and values
            and all(type(value) is str and value == value.upper() for value in values)
            for values in errors.values()
        )


def test_旧JWT在四类actor_currentness漂移后拒绝() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api)
    assert "async def _current_institution" in source
    assert "FOR SHARE OF u,t,a" in source
    assert "JOIN public.\"user\" u" in inspect.getsource(api._therapist_current)
    assert "await _current_reviewer(authority,actor)" in inspect.getsource(api.identity_reviews)
    assert "await _current_reviewer(authority,actor)" in inspect.getsource(api.identity_review)


def test_三类分页与detail真实投影() -> None:
    from app.modules.member_enrollment import api, repository

    api_source = inspect.getsource(api)
    repository_source = inspect.getsource(repository.MemberEnrollmentRepository)
    assert "cursor=cursor" in api_source
    assert "cursor_id" in repository_source
    for field in ('item["identity"]', 'item["proxy"]', 'item["consents"]', 'item["assignment"]'):
        assert field in api_source


def test_未提交实名的identity_check固定错误且零写() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api.institution_identity_check)
    assert "if verification is None" in source
    assert source.index("if verification is None") < source.index("_begin_mutation")


def test_A2平台同意文档三mutation固定platform_scope() -> None:
    from app.modules.member_enrollment import api

    for endpoint in (api.create_document, api.publish_document, api.retire_document):
        source = inspect.getsource(endpoint)
        assert "platform_scope=True" in source
        assert source.index("platform_scope=True") < source.index("_begin_mutation")


@pytest.mark.parametrize("parameter", ("cursor", "direct-detail", "safe-fields"))
def test_B3读取合同(parameter: str) -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api)
    if parameter == "cursor":
        assert 'raise _error("INVALID_CURSOR")' in inspect.getsource(api._cursor_id)
    elif parameter == "direct-detail":
        for endpoint in (
            api.institution_enrollment,
            api.family_enrollment,
            api.identity_review,
            api.therapist_assignment,
            api.institution_case,
            api.therapist_case,
        ):
            endpoint_source = inspect.getsource(endpoint)
            assert "limit=2" in endpoint_source
            assert "list_" not in endpoint_source
    else:
        assert "tenant_internal_id" not in inspect.getsource(api._public_row)
        migration = (
            ROOT
            / "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
        ).read_text(encoding="utf-8")
        assert "AS tenant_internal_id" not in migration
        for field in (
            "subject_masked_label",
            "therapist_display_name",
            "correction_fields",
            "proxy_witness_status",
        ):
            assert field in migration
