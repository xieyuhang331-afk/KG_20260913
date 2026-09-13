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
    def __init__(self, row=None):
        self.row = row or {
            "actor_current": True,
            "institution_current": True,
            "tenant_public_id": UUID("0198b963-38f0-7d7d-8000-000000000071"),
        }
        self.calls = []

    async def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, parameters))
        if (
            'FROM public."user"' in sql
            or "FROM public.tenant" in sql
            or "FROM public.institution_application" in sql
        ):
            raise RuntimeError("APPLICATION_ROLE_BASE_TABLE_DENIED")
        return _Result((self.row,))


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


def test_通用ApplicationAuthority只调用受限机构Currentness函数() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api._current_institution)
    assert "slice3_institution_currentness_authority_v1" in source
    assert 'public."user"' not in source
    assert "FROM public.tenant" not in source
    assert "FROM public.institution_application" not in source


def test_受限机构Currentness调用只传Actor和声明Tenant() -> None:
    from app.core.security import CurrentUser
    from app.modules.member_enrollment import api

    authority = _ApplicationAuthority()
    actor = CurrentUser(id=71, role="org_admin", tenant_id=9, org_id=100)
    assert asyncio.run(api._current_institution(authority, actor)) == (
        9,
        UUID("0198b963-38f0-7d7d-8000-000000000071"),
    )
    assert len(authority.calls) == 1
    sql, parameters = authority.calls[0]
    assert sql == (
        "SELECT actor_current,institution_current,tenant_public_id "
        "FROM public.slice3_institution_currentness_authority_v1"
        "(:actor_user_id,:claimed_tenant_id)"
    )
    assert parameters == {"actor_user_id": 71, "claimed_tenant_id": 9}


def test_批准机构事实由受限函数读取且其他路径保留正式Reader() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api)
    assert "get_institution_onboarding_reader_session" in source
    assert "async def _approved_institution" in source
    assert "institution_application" in inspect.getsource(api._approved_institution)
    assert "slice3_institution_currentness_authority_v1" in inspect.getsource(
        api._current_institution
    )


def test_十条机构路由只使用受限Currentness且其他路径保留InstitutionReader() -> None:
    from typing import get_args, get_type_hints

    from fastapi.params import Depends as DependsParam

    from app.modules.member_enrollment import api

    def dependency(endpoint, parameter_name):
        parameter = inspect.signature(endpoint).parameters.get(parameter_name)
        if parameter is None:
            return None
        candidates = []
        if isinstance(parameter.default, DependsParam):
            candidates.append(parameter.default)
        annotation = get_type_hints(endpoint, include_extras=True).get(parameter_name)
        candidates.extend(
            item for item in get_args(annotation) if isinstance(item, DependsParam)
        )
        assert len(candidates) <= 1
        return candidates[0].dependency if candidates else None

    institution_sites = (
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
    )
    for endpoint in institution_sites:
        source = inspect.getsource(endpoint)
        assert dependency(endpoint, "authority") is api.get_db_session
        assert dependency(endpoint, "institution_authority") is None
        assert "_current_institution(authority,actor)" in source.replace(" ", "")

    remaining_reader_sites = (
        api.accept_enrollment,
        api.identity_submission,
        api.identity_resubmit,
        api.consent_record,
        api.withdraw_consent,
        api.revoke_proxy,
        api.claim_review,
        api.pii_access,
        api.platform_decision,
    )
    for endpoint in remaining_reader_sites:
        source = inspect.getsource(endpoint)
        assert dependency(endpoint, "institution_authority") is api.get_institution_onboarding_reader_session
        assert "_tenant_public_id(authority" not in source

    for endpoint in (api.accept_assignment, api.decline_assignment):
        source = inspect.getsource(endpoint)
        assert dependency(endpoint, "institution_authority") is None
        assert dependency(endpoint, "session") is api.get_member_case_writer_session
        assert "_therapist_current(session,actor)" in source.replace(" ", "")


def test_平台实名审核不得用ApplicationAuthority读取Slice3或机构基表() -> None:
    from typing import get_args, get_type_hints

    from fastapi.params import Depends as DependsParam

    from app.modules.member_enrollment import api

    def dependency(endpoint, parameter_name):
        parameter = inspect.signature(endpoint).parameters[parameter_name]
        candidates = []
        if isinstance(parameter.default, DependsParam):
            candidates.append(parameter.default)
        annotation = get_type_hints(endpoint, include_extras=True)[parameter_name]
        candidates.extend(
            item for item in get_args(annotation) if isinstance(item, DependsParam)
        )
        assert len(candidates) == 1
        return candidates[0].dependency

    for endpoint in (api.claim_review, api.pii_access, api.platform_decision):
        source = inspect.getsource(endpoint)
        assert dependency(endpoint, "member_reader") is api.get_member_enrollment_reader_session
        assert dependency(endpoint, "institution_authority") is api.get_institution_onboarding_reader_session
        assert "institution_application" not in source
        assert "service_enrollment" not in source
        assert "await authority.execute" not in source


@pytest.mark.parametrize(
    ("row", "code"),
    (
        (
            {
                "actor_current": False,
                "institution_current": False,
                "tenant_public_id": None,
            },
            "ACTOR_CURRENTNESS_FORBIDDEN",
        ),
        (
            {
                "actor_current": True,
                "institution_current": False,
                "tenant_public_id": None,
            },
            "TENANT_SCOPE_FORBIDDEN",
        ),
    ),
)
def test_受限机构Currentness失败精确FailClosed(row, code) -> None:
    from app.core.security import CurrentUser
    from app.modules.member_enrollment import api

    actor = CurrentUser(id=71, role="org_admin", tenant_id=9, org_id=100)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(api._current_institution(_ApplicationAuthority(row), actor))
    assert failure.value.status_code == 403
    assert failure.value.detail == {
        "code": code,
        "message": code,
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
        get_member_enrollment_reader_session,
    )
    from app.core.security import CurrentUser, get_current_user_from_jwt
    from app.modules.member_enrollment.api import institution_router

    application_authority = _ApplicationAuthority()
    member_reader = _MemberReader()

    async def current_user():
        return CurrentUser(id=71, role="org_admin", tenant_id=9, org_id=100)

    async def application_session():
        yield application_authority

    async def member_session():
        yield member_reader

    app = FastAPI()
    app.include_router(institution_router)
    app.dependency_overrides[get_current_user_from_jwt] = current_user
    app.dependency_overrides[get_db_session] = application_session
    app.dependency_overrides[get_member_enrollment_reader_session] = member_session

    response = TestClient(app).get("/api/v1/institution/member-invitations")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
