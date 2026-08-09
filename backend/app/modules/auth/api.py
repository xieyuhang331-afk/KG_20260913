from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.database import get_db_session
from app.core.permissions import ensure_can_access_own_user_resource
from app.core.responses import ok_response
from app.core.security import (
    CurrentUser,
    build_current_user_from_authorization_header,
    get_current_user_from_jwt,
)
from app.modules.auth.schemas import (
    AuthLoginRequest,
    AuthMeResponse,
    TenantBindingRequest,
    UserIdentityRequest,
    IdentityVerificationSubmissionRequest,
    IdentityVerificationSubmissionResponse,
    IdentityVerificationStatusResponse,
    UserRegisterRequest,
)
from app.modules.auth.service import bind_user_tenant, login_user, register_user, submit_user_identity


router = APIRouter(prefix="/api/v1/users", tags=["auth"])
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _identity_submission_service(session):
    from app.composition.identity_submission import create_identity_submission_service
    from app.modules.auth.identity_submission_crypto import IdentitySubmissionCryptoUnavailable

    try:
        return create_identity_submission_service(session)
    except IdentitySubmissionCryptoUnavailable:
        raise HTTPException(
            status_code=503, detail="Identity verification service unavailable"
        ) from None


def _identity_submission_http_error(error):
    from app.modules.auth.identity_submission import (
        IdentitySubmissionConflict,
        IdentitySubmissionForbidden,
        IdentitySubmissionRateLimited,
    )

    if isinstance(error, IdentitySubmissionForbidden):
        return HTTPException(status_code=403, detail="Forbidden")
    if isinstance(error, IdentitySubmissionConflict):
        return HTTPException(status_code=409, detail="Identity verification submission conflict")
    if isinstance(error, IdentitySubmissionRateLimited):
        return HTTPException(status_code=429, detail="Identity verification submission rate limited")
    return HTTPException(status_code=503, detail="Identity verification service unavailable")


@router.put("/me/identity-verification")
async def put_identity_verification_submission_api(
    payload: IdentityVerificationSubmissionRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    try:
        result = await _identity_submission_service(session).submit(
            current_user=current_user, request=payload
        )
    except Exception as error:
        from app.modules.auth.identity_submission import IdentitySubmissionError
        if isinstance(error, IdentitySubmissionError):
            raise _identity_submission_http_error(error) from None
        raise
    response = IdentityVerificationSubmissionResponse(
        status=result.status, submission_version=result.submission_version,
        id_card_masked=result.id_card_masked, submitted_at=result.submitted_at,
        outcome=result.outcome,
    )
    return ok_response(response.model_dump())


@router.get("/me/identity-verification")
async def get_identity_verification_status_api(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    try:
        result = await _identity_submission_service(session).status(
            current_user=current_user
        )
    except Exception as error:
        from app.modules.auth.identity_submission import IdentitySubmissionError
        if isinstance(error, IdentitySubmissionError):
            raise _identity_submission_http_error(error) from None
        raise
    response = IdentityVerificationStatusResponse(
        status=result.status, submission_version=result.submission_version,
        id_card_masked=result.id_card_masked, submitted_at=result.submitted_at,
        decided_at=result.decided_at,
        rejection_reason_code=result.rejection_reason_code,
        resubmit_available_at=result.resubmit_available_at,
    )
    return ok_response(response.model_dump())


@auth_router.post("/login")
async def login_user_api(
    payload: AuthLoginRequest,
    session=Depends(get_db_session),
) -> dict:
    result = await login_user(session, payload)
    return ok_response(result.model_dump())


@auth_router.get("/me")
async def get_auth_me_api(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    current_user = build_current_user_from_authorization_header(authorization)
    result = AuthMeResponse(
        id=current_user.id,
        role=current_user.role,
        tenant_id=current_user.tenant_id,
        org_id=current_user.org_id,
        province=current_user.province,
        city=current_user.city,
    )
    return ok_response(result.model_dump())


@router.post("/register")
async def register_user_api(
    payload: UserRegisterRequest,
    session=Depends(get_db_session),
) -> dict:
    result = await register_user(session, payload)
    return ok_response(result.model_dump())


@router.post("/{user_id}/identity")
async def submit_user_identity_api(
    user_id: int,
    payload: UserIdentityRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await submit_user_identity(session, current_user, user_id, payload)
    return ok_response(result.model_dump())


@router.post("/{user_id}/tenant-binding")
async def bind_user_tenant_api(
    user_id: int,
    payload: TenantBindingRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_can_access_own_user_resource(current_user, user_id)
    result = await bind_user_tenant(session, current_user, user_id, payload)
    return ok_response(result.model_dump())
