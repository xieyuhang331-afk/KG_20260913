from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.database import get_db_session
from app.core.responses import ok_response
from app.core.security import CurrentUser, get_current_user_from_jwt
from app.modules.auth.manual_identity_review_application import (
    PlatformAdminManualIdentityReviewConflict,
    PlatformAdminManualIdentityReviewForbidden,
    PlatformAdminManualIdentityReviewNotFound,
    PlatformAdminManualIdentityReviewRequest as ApplicationReviewRequest,
    PlatformAdminManualIdentityReviewUnavailable,
)
from app.modules.review.schemas import (
    PlatformAdminManualIdentityReviewRequest,
    PlatformAdminManualIdentityReviewResponse,
    TenantReviewApproveRequest,
    TenantReviewQueueQuery,
    TenantReviewRejectRequest,
)
from app.modules.review.service import (
    approve_tenant_application,
    get_tenant_review_detail,
    list_tenant_review_queue,
    reject_tenant_application,
)


router = APIRouter(prefix="/api/v1/reviews", tags=["review"])


async def get_platform_identity_reviewer(
    current_user: CurrentUser = Depends(get_current_user_from_jwt),
) -> CurrentUser:
    _require_platform_identity_reviewer(current_user)
    return current_user


def get_platform_admin_manual_identity_review_service(
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
):
    from app.composition.p1_verified_transition import (
        create_platform_admin_manual_identity_review_service,
    )
    from app.core.database import get_session_factory
    from app.core.uuid_generator import Uuid7Generator

    return create_platform_admin_manual_identity_review_service(
        session_factory=get_session_factory(),
        uuid_generator=Uuid7Generator(),
    )


def _require_platform_identity_reviewer(current_user: CurrentUser) -> None:
    if (
        current_user.role != "super_admin"
        or current_user.tenant_id is not None
        or current_user.org_id is not None
    ):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/users/{user_id}/identity/approve")
async def approve_platform_user_identity_review_api(
    user_id: int,
    payload: PlatformAdminManualIdentityReviewRequest,
    current_user: CurrentUser = Depends(get_platform_identity_reviewer),
    service=Depends(get_platform_admin_manual_identity_review_service),
) -> dict:
    try:
        result = await service.execute(
            current_user=current_user,
            user_ref=user_id,
            request=ApplicationReviewRequest(
                idempotency_key=payload.idempotency_key,
                decided_at=payload.decided_at,
                evidence_digest=payload.evidence_digest,
            ),
        )
    except PlatformAdminManualIdentityReviewForbidden:
        raise HTTPException(status_code=403, detail="Forbidden") from None
    except PlatformAdminManualIdentityReviewNotFound:
        raise HTTPException(
            status_code=404,
            detail="Identity review subject not found",
        ) from None
    except PlatformAdminManualIdentityReviewConflict:
        raise HTTPException(
            status_code=409,
            detail="Identity review decision conflict",
        ) from None
    except PlatformAdminManualIdentityReviewUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Identity review service unavailable",
        ) from None

    response = PlatformAdminManualIdentityReviewResponse(
        user_id=result.user_ref,
        status=result.status,
        verification_decision_ref=str(result.verification_decision_ref),
        registration_event_id=str(result.registration_event_id),
        authority_decision_key=result.authority_decision_key,
        replayed=result.replayed,
    )
    return ok_response(response.model_dump())


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
