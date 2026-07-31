from __future__ import annotations

from fastapi import APIRouter, Depends, Header

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
    UserRegisterRequest,
)
from app.modules.auth.service import bind_user_tenant, login_user, register_user, submit_user_identity


router = APIRouter(prefix="/api/v1/users", tags=["auth"])
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


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
