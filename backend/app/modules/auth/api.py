from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.core.database import get_db_session
from app.core.responses import ok_response
from app.core.security import (
    CurrentUser,
    build_current_user_from_authorization_header,
    get_current_user_from_jwt,
)
from app.modules.auth.schemas import (
    AuthLoginRequest,
    AuthMeResponse,
    IdentityVerificationStatusResponse,
    IdentityVerificationSubmissionRequest,
    IdentityVerificationSubmissionResponse,
    LegacyIdentityEndpointRetiredResponse,
    LegacyMemberTenantBindingRetiredResponse,
    UserRegisterRequest,
)
from app.modules.auth.service import login_user, register_user

router = APIRouter(prefix="/api/v1/users", tags=["auth"])
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _identity_submission_service(session):
    from app.composition.identity_submission import create_identity_submission_service
    from app.modules.auth.identity_submission_crypto import (
        IdentitySubmissionCryptoUnavailable,
    )

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


_NO_STORE_RESPONSE = {
    "headers": {
        "Cache-Control": {
            "description": "Sensitive identity responses are never cacheable.",
            "schema": {"type": "string", "example": "no-store"},
        }
    }
}


_IDENTITY_INPUT_INVALID_RESPONSE = {
    **_NO_STORE_RESPONSE,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "message"],
                "properties": {
                    "code": {
                        "type": "string",
                        "enum": ["IDENTITY_VERIFICATION_INPUT_INVALID"],
                    },
                    "message": {
                        "type": "string",
                        "enum": ["request rejected"],
                    },
                },
            }
        }
    },
}


class IdentityVerificationSubmissionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={
                        "code": "IDENTITY_VERIFICATION_INPUT_INVALID",
                        "message": "request rejected",
                    },
                    headers={"Cache-Control": "no-store"},
                )

        return handler


async def put_identity_verification_submission_api(
    payload: IdentityVerificationSubmissionRequest,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
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


router.add_api_route(
    "/me/identity-verification",
    put_identity_verification_submission_api,
    methods=["PUT"],
    responses={
        200: _NO_STORE_RESPONSE,
        422: _IDENTITY_INPUT_INVALID_RESPONSE,
    },
    route_class_override=IdentityVerificationSubmissionRoute,
)


@router.get(
    "/me/identity-verification",
    responses={200: _NO_STORE_RESPONSE},
)
async def get_identity_verification_status_api(
    response: Response,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
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


@router.post(
    "/{user_id}/identity",
    deprecated=True,
    status_code=410,
    response_model=LegacyIdentityEndpointRetiredResponse,
    responses={410: _NO_STORE_RESPONSE},
)
async def submit_user_identity_api(
    user_id: int,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
) -> LegacyIdentityEndpointRetiredResponse:
    del user_id, current_user
    response.headers["Cache-Control"] = "no-store"
    return LegacyIdentityEndpointRetiredResponse(
        code="LEGACY_IDENTITY_ENDPOINT_RETIRED",
        message="request rejected",
    )


@router.post(
    "/{user_id}/tenant-binding",
    deprecated=True,
    status_code=410,
    response_model=LegacyMemberTenantBindingRetiredResponse,
    responses={410: _NO_STORE_RESPONSE},
)
async def bind_user_tenant_api(
    user_id: int,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
) -> LegacyMemberTenantBindingRetiredResponse:
    del user_id, current_user
    response.headers["Cache-Control"] = "no-store"
    return LegacyMemberTenantBindingRetiredResponse(
        code="LEGACY_MEMBER_TENANT_BINDING_RETIRED",
        message="request rejected",
    )
