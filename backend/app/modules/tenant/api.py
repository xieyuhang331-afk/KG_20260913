from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.database import get_db_session
from app.core.config import get_settings
from fastapi import HTTPException
from app.core.permissions import ensure_is_member
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.tenant.schemas import ActiveTenantQuery, MyTenantApplicationQuery, TenantApplicationCreate
from app.modules.tenant.service import get_application_status, list_active_tenants, list_my_applications, submit_application


router = APIRouter(prefix="/api/v1/tenants", tags=["tenant"])


@router.post("")
async def create_tenant_application(
    payload: TenantApplicationCreate,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    if get_settings().phase1_pilot_mode:
        raise HTTPException(status_code=410, detail="LEGACY_DISABLED_FOR_PILOT")
    result = await submit_application(session, current_user, payload)
    return ok_response(result.model_dump())


@router.get("/my-applications")
async def get_my_tenant_applications(
    status: Annotated[Literal["pending", "active", "rejected"] | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    query = MyTenantApplicationQuery(status=status, page=page, page_size=page_size)
    result = await list_my_applications(session, current_user, query)
    return ok_response(result.model_dump())


@router.get("/{tenant_id}/application-status")
async def get_tenant_application_status(
    tenant_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await get_application_status(session, current_user, tenant_id)
    return ok_response(result.model_dump())


@router.get("/active")
async def get_active_tenants(
    province: Annotated[str | None, Query(max_length=30)] = None,
    city: Annotated[str | None, Query(max_length=30)] = None,
    keyword: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    ensure_is_member(current_user)
    query = ActiveTenantQuery(
        province=province,
        city=city,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    result = await list_active_tenants(session, current_user, query)
    return ok_response(result.model_dump())
