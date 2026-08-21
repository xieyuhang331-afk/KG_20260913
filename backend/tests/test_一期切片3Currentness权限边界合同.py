import asyncio
import inspect
from uuid import UUID

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def one_or_none(self):
        return self._rows[0] if len(self._rows) == 1 else None

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _ApplicationAuthority:
    async def execute(self, statement, parameters=None):
        del parameters
        sql = str(statement)
        if "institution_application" in sql:
            raise RuntimeError("APPLICATION_ROLE_BASE_TABLE_DENIED")
        return _Result(({
            "id": 71,
            "role": "org_admin",
            "status": "active",
            "tenant_id": 9,
            "tenant_status": "active",
        },))


class _InstitutionAuthority:
    def __init__(self, tenant_internal_id=9):
        self.tenant_internal_id = tenant_internal_id

    async def execute(self, statement, parameters=None):
        del statement, parameters
        return _Result(({
            "tenant_internal_id": self.tenant_internal_id,
            "tenant_public_id": UUID("0198b963-38f0-7d7d-8000-000000000071"),
            "status": "APPROVED",
        },))


class _MemberReader:
    def __init__(self, rows=()):
        self.rows = rows

    async def execute(self, statement, parameters=None):
        del statement, parameters
        return _Result(self.rows)


def test_通用ApplicationAuthority仅验证User与Tenant() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api._current_institution)
    assert 'public."user"' in source
    assert "public.tenant" in source
    assert "institution_application" not in source
    assert "FOR SHARE OF u,t" in source


def test_批准机构事实仅由正式InstitutionOnboardingReader读取() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api)
    assert "get_institution_onboarding_reader_session" in source
    assert "async def _approved_institution" in source
    assert "institution_application" in inspect.getsource(api._approved_institution)
    assert "institution_application" not in inspect.getsource(api._tenant_public_id)


def test_全部TenantPublicId调用路径显式使用InstitutionAuthority() -> None:
    from app.modules.member_enrollment import api

    call_sites = (
        api.create_invitation,
        api.list_invitations,
        api.resend_invitation,
        api.revoke_invitation,
        api.institution_enrollments,
        api.institution_enrollment,
        api.institution_identity_check,
        api.create_assignment,
        api.cancel_assignment,
        api.institution_case,
        api.accept_enrollment,
        api.identity_submission,
        api.identity_resubmit,
        api.consent_record,
        api.withdraw_consent,
        api.revoke_proxy,
        api.claim_review,
        api.pii_access,
        api.platform_decision,
        api.accept_assignment,
        api.decline_assignment,
    )
    for endpoint in call_sites:
        source = inspect.getsource(endpoint)
        assert "institution_authority=Depends(get_institution_onboarding_reader_session)" in source
        assert "_tenant_public_id(authority" not in source


def test_平台实名审核不得用ApplicationAuthority读取Slice3或机构基表() -> None:
    from app.modules.member_enrollment import api

    for endpoint in (api.claim_review, api.pii_access, api.platform_decision):
        source = inspect.getsource(endpoint)
        assert "member_reader=Depends(get_member_enrollment_reader_session)" in source
        assert "institution_authority=Depends(get_institution_onboarding_reader_session)" in source
        assert "institution_application" not in source
        assert "service_enrollment" not in source
        assert "await authority.execute" not in source


def test_Application与OnboardingReader租户映射不一致时FailClosed() -> None:
    from app.core.security import CurrentUser
    from app.modules.member_enrollment import api

    actor = CurrentUser(id=71, role="org_admin", tenant_id=9, org_id=100)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(
            api._current_institution(
                _ApplicationAuthority(),
                _InstitutionAuthority(tenant_internal_id=10),
                actor,
            )
    )
    assert failure.value.status_code == 403
    assert failure.value.detail == {
        "code": "TENANT_SCOPE_FORBIDDEN",
        "message": "TENANT_SCOPE_FORBIDDEN",
    }


def test_平台审核租户由MemberReader与OnboardingReader共同确认() -> None:
    from app.modules.member_enrollment import api

    public_id = UUID("0198b963-38f0-7d7d-8000-000000000071")
    result = asyncio.run(
        api._platform_tenant(
            _MemberReader(({"tenant_public_id": public_id},)),
            _InstitutionAuthority(),
            UUID("0198b963-38f0-7d7d-8000-000000000072"),
        )
    )
    assert result == (9, public_id)


def test_机构邀请列表HTTP不再因Application角色拒绝机构表而503() -> None:
    from app.core.database import (
        get_db_session,
        get_institution_onboarding_reader_session,
        get_member_enrollment_reader_session,
    )
    from app.core.security import CurrentUser, get_current_user_from_jwt
    from app.modules.member_enrollment.api import institution_router

    application_authority = _ApplicationAuthority()
    institution_authority = _InstitutionAuthority()
    member_reader = _MemberReader()

    async def current_user():
        return CurrentUser(id=71, role="org_admin", tenant_id=9, org_id=100)

    async def application_session():
        yield application_authority

    async def institution_session():
        yield institution_authority

    async def member_session():
        yield member_reader

    app = FastAPI()
    app.include_router(institution_router)
    app.dependency_overrides[get_current_user_from_jwt] = current_user
    app.dependency_overrides[get_db_session] = application_session
    app.dependency_overrides[get_institution_onboarding_reader_session] = institution_session
    app.dependency_overrides[get_member_enrollment_reader_session] = member_session

    response = TestClient(app).get("/api/v1/institution/member-invitations")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
