from __future__ import annotations

from email.message import Message
from json import JSONDecodeError
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.core.database import get_db_session
from app.core.responses import ok_response
from app.core.security import (
    CurrentUser,
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


_AUTH_INPUT_INVALID_RESPONSE = {
    **_NO_STORE_RESPONSE,
    "content": {
        "application/json": {
            "schema": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "message"],
                "properties": {
                    "code": {"type": "string", "enum": ["AUTH_INPUT_INVALID"]},
                    "message": {"type": "string", "enum": ["request rejected"]},
                },
            },
        },
    },
}


def _auth_input_invalid():
    return JSONResponse(
        status_code=422, content={"code": "AUTH_INPUT_INVALID", "message": "request rejected"},
        headers={"Cache-Control": "no-store"},
    )


class _AuthInputRoute(APIRoute):
    request_model: type[AuthLoginRequest] | type[UserRegisterRequest]

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            # Match the locked FastAPI JSON content-type contract. Starlette
            # caches body/json; the original route retains normal dependencies
            # and validates the same model, without a second admission/session.
            content_type = Message()
            content_type["content-type"] = request.headers.get("content-type", "")
            subtype = content_type.get_content_subtype()
            try:
                if content_type.get_content_maintype() == "application" and (
                    subtype == "json" or subtype.endswith("+json")
                ):
                    try:
                        body = await request.json()
                    except (ValueError, RecursionError):
                        return _auth_input_invalid()
                else:
                    body = await request.body()
                self.request_model.model_validate(body)
            except (JSONDecodeError, UnicodeDecodeError, ValidationError):
                return _auth_input_invalid()
            try:
                return await original(request)
            except RequestValidationError:
                return _auth_input_invalid()

        return handler


class _LoginInputRoute(_AuthInputRoute):
    request_model = AuthLoginRequest


class _RegistrationInputRoute(_AuthInputRoute):
    request_model = UserRegisterRequest


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


def _rate_limiter(request: Request):
    limiter = getattr(request.app.state, "auth_rate_limiter", None)
    if limiter is None:
        raise HTTPException(503, "AUTH_RATE_LIMIT_UNAVAILABLE", headers={"Cache-Control": "no-store"})
    return limiter


async def _login_admission(request: Request, payload: AuthLoginRequest):
    limiter = _rate_limiter(request)
    reservation = limiter.reserve_login(request.client.host if request.client else None, payload.phone)
    failed = False
    try:
        yield
    except HTTPException as error:
        failed = error.status_code in (401, 403)
        error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
        raise
    finally:
        limiter.settle_login(reservation, failed=failed)


async def _registration_admission(request: Request, payload: UserRegisterRequest):
    _rate_limiter(request).reserve_registration(request.client.host if request.client else None, payload.phone)
    try:
        yield
    except HTTPException as error:
        error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
        raise


async def login_user_api(
    payload: AuthLoginRequest,
    response: Response,
    admission: Annotated[None, Depends(_login_admission)],
    session=Depends(get_db_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    result = await login_user(session, payload)
    return ok_response(result.model_dump())


auth_router.add_api_route(
    "/login", login_user_api, methods=["POST"], route_class_override=_LoginInputRoute,
    responses={422: _AUTH_INPUT_INVALID_RESPONSE, 429: _NO_STORE_RESPONSE, 503: _NO_STORE_RESPONSE},
)


@auth_router.get("/me")
async def get_auth_me_api(
    response: Response,
    current_user: Annotated[CurrentUser, Depends(get_current_user_from_jwt)],
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    result = AuthMeResponse(
        id=current_user.id,
        role=current_user.role,
        tenant_id=current_user.tenant_id,
        org_id=current_user.org_id,
        province=current_user.province,
        city=current_user.city,
    )
    return ok_response(result.model_dump())


async def register_user_api(
    payload: UserRegisterRequest,
    response: Response,
    admission: Annotated[None, Depends(_registration_admission)],
    session=Depends(get_db_session),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    result = await register_user(session, payload)
    return ok_response(result.model_dump())


router.add_api_route(
    "/register", register_user_api, methods=["POST"], route_class_override=_RegistrationInputRoute,
    responses={422: _AUTH_INPUT_INVALID_RESPONSE, 429: _NO_STORE_RESPONSE, 503: _NO_STORE_RESPONSE},
)


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
