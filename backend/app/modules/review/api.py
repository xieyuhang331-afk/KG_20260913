from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.database import get_db_session
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.review.schemas import TenantReviewApproveRequest, TenantReviewQueueQuery, TenantReviewRejectRequest
from app.modules.review.service import (
    approve_tenant_application,
    get_tenant_review_detail,
    list_tenant_review_queue,
    reject_tenant_application,
)


router = APIRouter(prefix="/api/v1/reviews", tags=["review"])


@router.get("/queue/tenant")
async def get_tenant_review_queue(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: str | None = None,
    province: str | None = None,
    city: str | None = None,
) -> dict:
    query = TenantReviewQueueQuery(
        page=page,
        page_size=page_size,
        keyword=keyword,
        province=province,
        city=city,
    )
    result = await list_tenant_review_queue(session, current_user, query)
    return ok_response(result.model_dump())


@router.get("/tenants/{tenant_id}")
async def get_tenant_review_detail_api(
    tenant_id: int,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await get_tenant_review_detail(session, current_user, tenant_id)
    return ok_response(result.model_dump())


@router.post("/tenants/{tenant_id}/approve")
async def approve_tenant_review_api(
    tenant_id: int,
    payload: TenantReviewApproveRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await approve_tenant_application(session, current_user, tenant_id, payload)
    return ok_response(result.model_dump())


@router.post("/tenants/{tenant_id}/reject")
async def reject_tenant_review_api(
    tenant_id: int,
    payload: TenantReviewRejectRequest,
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
    session=Depends(get_db_session),
) -> dict:
    result = await reject_tenant_application(session, current_user, tenant_id, payload)
    return ok_response(result.model_dump())
