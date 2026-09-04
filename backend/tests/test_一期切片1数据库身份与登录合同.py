from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Response

from app.core.config import Settings
from app.core.database import _slice1_url
from app.modules.auth.schemas import AuthLoginRequest
from app.modules.auth.service import _build_login_claims, hash_password, login_user
from app.modules.institution_onboarding.service import require_current_reviewer
from app.modules.private_file.api import post_file_access
from app.modules.private_file.schemas import FileAccessRequest


def _database_url(username: str) -> str:
    return f"postgresql+asyncpg://{username}:pw@db:5432/kg"


def _synthetic_mobile() -> str:
    return "138" + "0" * 8


def settings(**overrides):
    values = dict(
        database_password="x",
        jwt_secret_key="x",
        database_host="db",
        database_port=5432,
        database_name="kg",
        database_user="app",
        institution_onboarding_writer_database_url=_database_url("ow"),
        institution_review_writer_database_url=_database_url("rw"),
        private_file_writer_database_url=_database_url("fw"),
        private_file_access_writer_database_url=_database_url("aw"),
        institution_onboarding_reader_database_url=_database_url("rr"),
        institution_onboarding_writer_role="ow",
        institution_review_writer_role="rw",
        private_file_writer_role="fw",
        private_file_access_writer_role="aw",
        institution_onboarding_reader_role="rr",
    )
    values.update(overrides)
    return Settings(**values)


def test_slice1_database_urls_and_usernames_are_strictly_isolated():
    assert _slice1_url(settings(), "onboarding_writer").startswith(
        "postgresql+asyncpg://ow:"
    )
    assert _slice1_url(settings(), "access_writer").startswith(
        "postgresql+asyncpg://aw:"
    )
    with pytest.raises(RuntimeError):
        _slice1_url(settings(private_file_writer_role="rw"), "file_writer")
    with pytest.raises(RuntimeError):
        _slice1_url(
            settings(private_file_access_writer_role="fw"), "access_writer"
        )
    with pytest.raises(RuntimeError):
        _slice1_url(
            settings(
                institution_onboarding_reader_database_url=_database_url("rw")
            ),
            "reader",
        )


@pytest.mark.asyncio
async def test_controlled_institution_admin_login_requires_totp():
    secret = "JBSWY3DPEHPK3PXP"
    user = SimpleNamespace(
        id=9,
        phone=_synthetic_mobile(),
        password_hash=hash_password("Secret12345"),
        role="org_admin",
        status="active",
        tenant_id=None,
    )
    account = SimpleNamespace(totp_enabled=True, totp_secret_ciphertext=b"cipher")
    cipher = SimpleNamespace(decrypt=lambda _: secret)
    with (
        patch(
            "app.modules.auth.service.get_user_by_phone",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "app.modules.auth.service.get_onboarding_account_for_login",
            new=AsyncMock(return_value=account),
        ),
        patch(
            "app.modules.institution_onboarding.service.OnboardingSecrets",
            return_value=cipher,
        ),
    ):
        with pytest.raises(Exception, match="TOTP_REQUIRED_OR_INVALID"):
            await login_user(
                object(),
                AuthLoginRequest(phone=user.phone, password="Secret12345"),
            )


def test_dynamic_institution_admin_claims_use_approved_tenant_org(monkeypatch):
    user = SimpleNamespace(id=9, role="org_admin", tenant_id=51)
    monkeypatch.setattr(
        "app.modules.auth.service.get_controlled_auth_context",
        lambda _: {"org_id": 999},
    )
    claims = _build_login_claims(user, dynamic_org_id=41)
    assert claims == {
        "sub": "9",
        "role": "org_admin",
        "tenant_id": 51,
        "org_id": 41,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant_id", "org_id"),
    ((51, None), (None, 41), (51, 41)),
)
async def test_reviewer_old_jwt_with_any_institution_scope_is_rejected_before_database(
    monkeypatch,
    tenant_id,
    org_id,
):
    called = False

    async def currentness(self, user_id):
        nonlocal called
        called = True
        return {"id": user_id, "role": "super_admin", "status": "active", "tenant_id": None}

    monkeypatch.setattr(
        "app.modules.institution_onboarding.repository.InstitutionOnboardingRepository.reviewer_currentness",
        currentness,
    )
    actor = SimpleNamespace(
        id=7,
        role="super_admin",
        tenant_id=tenant_id,
        org_id=org_id,
    )
    with pytest.raises(HTTPException) as rejected:
        await require_current_reviewer(object(), actor)
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "ONBOARDING_REVIEW_ROLE_REQUIRED"
    assert called is False


@pytest.mark.asyncio
async def test_reviewer_current_database_row_must_remain_unscoped_and_active(monkeypatch):
    async def currentness(self, user_id):
        return {"id": user_id, "role": "super_admin", "status": "disabled", "tenant_id": None}

    monkeypatch.setattr(
        "app.modules.institution_onboarding.repository.InstitutionOnboardingRepository.reviewer_currentness",
        currentness,
    )
    actor = SimpleNamespace(
        id=7,
        role="super_admin",
        tenant_id=None,
        org_id=None,
    )
    with pytest.raises(HTTPException) as rejected:
        await require_current_reviewer(object(), actor)
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "ONBOARDING_REVIEW_CURRENTNESS_REQUIRED"


@pytest.mark.asyncio
async def test_reviewer_private_file_access_requires_current_password_before_repository(monkeypatch):
    current_password = "StrongPassword123!"
    reviewer = SimpleNamespace(
        id=7,
        role="super_admin",
        tenant_id=None,
        org_id=None,
    )
    current = {
        "id": reviewer.id,
        "role": reviewer.role,
        "status": "active",
        "tenant_id": None,
        "password_hash": hash_password(current_password),
    }
    currentness = AsyncMock(return_value=current)
    authorize = AsyncMock(return_value="opaque-token")
    monkeypatch.setattr(
        "app.modules.institution_onboarding.service.require_current_reviewer",
        currentness,
    )
    monkeypatch.setattr(
        "app.modules.private_file.api.authorize_file_access",
        authorize,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(private_object_store=object())
        )
    )

    with pytest.raises(HTTPException) as rejected:
        await post_file_access(
            file_id="file-1",
            payload=FileAccessRequest(
                reason_code="INSTITUTION_REVIEW",
                reauth_password="WrongPassword123!",
            ),
            request=request,
            response=Response(),
            current_user=reviewer,
            session=object(),
            access_writer_session=object(),
            identity_session=object(),
            report_authority_session=object(),
            report_institution_session=object(),
        )
    assert rejected.value.status_code == 403
    assert rejected.value.detail == "PRIVATE_FILE_REAUTH_REQUIRED"
    authorize.assert_not_awaited()

    response = await post_file_access(
        file_id="file-1",
        payload=FileAccessRequest(
            reason_code="INSTITUTION_REVIEW",
            reauth_password=current_password,
        ),
        request=request,
        response=Response(),
        current_user=reviewer,
        session=object(),
        access_writer_session=object(),
        identity_session=object(),
        report_authority_session=object(),
        report_institution_session=object(),
    )
    assert response["data"]["content_path"] == "/api/v1/private-files/file-1/content"
    assert response["data"]["access_credential"] == "opaque-token"
    authorize.assert_awaited_once()
