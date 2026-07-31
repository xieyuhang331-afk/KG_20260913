from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from app.core.permissions import can_access_region, ensure_can_review_tenant_application
from app.core.security import CurrentUser
from app.modules.review.schemas import (
    TenantReviewApproveRequest,
    TenantReviewDecisionResponse,
    TenantReviewDetailAttachment,
    TenantReviewDetailContact,
    TenantReviewDetailResponse,
    TenantReviewDetailStatus,
    TenantReviewDetailTenant,
    TenantReviewQueueItem,
    TenantReviewQueueQuery,
    TenantReviewQueueResponse,
    TenantReviewRejectRequest,
)
from app.modules.system.repository import create_operation_log
from app.modules.system.notification_service import build_tenant_review_notification_intent
from app.modules.tenant.repository import (
    apply_tenant_review_decision,
    create_tenant_review_log,
    get_tenant_application_detail,
    get_tenant_for_review_update,
    list_pending_tenant_applications,
)


def _resolve_review_scope(
    current_user: CurrentUser,
    query: TenantReviewQueueQuery,
) -> tuple[str | None, str | None]:
    if current_user.role == "super_admin":
        return query.province, query.city

    if current_user.role == "province_admin":
        if not current_user.province:
            raise HTTPException(status_code=403, detail="Forbidden")
        if query.province is not None and query.province != current_user.province:
            raise HTTPException(status_code=403, detail="Forbidden")
        return current_user.province, query.city

    if current_user.role == "city_admin":
        if not current_user.province or not current_user.city:
            raise HTTPException(status_code=403, detail="Forbidden")
        if query.province is not None and query.province != current_user.province:
            raise HTTPException(status_code=403, detail="Forbidden")
        if query.city is not None and query.city != current_user.city:
            raise HTTPException(status_code=403, detail="Forbidden")
        return current_user.province, current_user.city

    raise HTTPException(status_code=403, detail="Forbidden")


async def list_tenant_review_queue(
    session,
    current_user: CurrentUser,
    query: TenantReviewQueueQuery,
) -> TenantReviewQueueResponse:
    ensure_can_review_tenant_application(current_user)
    province, city = _resolve_review_scope(current_user, query)

    rows, total = await list_pending_tenant_applications(
        session,
        province=province,
        city=city,
        keyword=query.keyword,
        page=query.page,
        page_size=query.page_size,
    )

    return TenantReviewQueueResponse(
        items=[TenantReviewQueueItem(**row) for row in rows],
        page=query.page,
        page_size=query.page_size,
        total=total,
    )


def _tenant_value(tenant, key: str):
    if isinstance(tenant, dict):
        return tenant[key]
    return getattr(tenant, key)


def _ensure_can_access_tenant_review_object(current_user: CurrentUser, tenant) -> None:
    if not can_access_region(current_user, province=_tenant_value(tenant, "province"), city=_tenant_value(tenant, "city")):
        raise HTTPException(status_code=403, detail="Forbidden")


def _ensure_tenant_is_pending(tenant) -> None:
    if _tenant_value(tenant, "status") != "pending":
        raise HTTPException(status_code=409, detail="Tenant application is not pending")


async def get_tenant_review_detail(
    session,
    current_user: CurrentUser,
    tenant_id: int,
) -> TenantReviewDetailResponse:
    ensure_can_review_tenant_application(current_user)

    detail = await get_tenant_application_detail(session, tenant_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant = detail["tenant"]
    _ensure_can_access_tenant_review_object(current_user, tenant)

    return TenantReviewDetailResponse(
        tenant=TenantReviewDetailTenant(
            id=tenant["id"],
            tenant_code=tenant["tenant_code"],
            name=tenant["name"],
            short_name=tenant["short_name"],
            type=tenant["type"],
            credit_code=tenant["credit_code"],
            license_no=tenant["license_no"],
            license_image=tenant["license_image"],
            legal_person_name=tenant["legal_person_name"],
            province=tenant["province"],
            city=tenant["city"],
            district=tenant["district"],
            address=tenant["address"],
            grade=tenant["grade"],
        ),
        contact=TenantReviewDetailContact(
            contact_name=tenant["contact_name"],
            contact_phone=tenant["contact_phone"],
            contact_email=tenant["contact_email"],
        ),
        attachments=[TenantReviewDetailAttachment(**attachment) for attachment in detail["attachments"]],
        status=TenantReviewDetailStatus(
            current=tenant["status"],
            reviewed_by=tenant["reviewed_by"],
            reviewed_at=tenant["reviewed_at"],
            reject_reason=tenant["reject_reason"],
            approved_at=tenant["approved_at"],
        ),
        submitted_at=tenant["created_at"],
    )


async def _review_tenant_application(
    session,
    current_user: CurrentUser,
    tenant_id: int,
    *,
    target_status: str,
    review_action: str,
    operation_action: str,
    comment: str | None,
    reject_reason: str | None,
    grade: str | None,
) -> TenantReviewDecisionResponse:
    ensure_can_review_tenant_application(current_user)

    tenant = await get_tenant_for_review_update(session, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    _ensure_can_access_tenant_review_object(current_user, tenant)
    _ensure_tenant_is_pending(tenant)

    reviewed_at = datetime.now(timezone.utc)
    approved_at = reviewed_at if target_status == "active" else None

    try:
        tenant = await apply_tenant_review_decision(
            session,
            tenant,
            status=target_status,
            reviewed_by=current_user.id,
            reviewed_at=reviewed_at,
            approved_at=approved_at,
            reject_reason=reject_reason,
            grade=grade,
        )
        await create_tenant_review_log(
            session,
            tenant_id=tenant.id,
            reviewer_id=current_user.id,
            action=review_action,
            grade=grade if review_action == "approved" else None,
            comment=comment,
        )
        await create_operation_log(
            session,
            operator_id=current_user.id,
            module="tenant",
            object_type="tenant",
            object_id=tenant.id,
            action=operation_action,
            payload={
                "tenant_id": tenant.id,
                "status": target_status,
                "action": review_action,
            },
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    build_tenant_review_notification_intent(tenant=tenant, action=review_action)

    return TenantReviewDecisionResponse(
        tenant_id=tenant.id,
        status=tenant.status,
        reviewed_by=current_user.id,
        reviewed_at=reviewed_at,
    )


async def approve_tenant_application(
    session,
    current_user: CurrentUser,
    tenant_id: int,
    payload: TenantReviewApproveRequest,
) -> TenantReviewDecisionResponse:
    return await _review_tenant_application(
        session,
        current_user,
        tenant_id,
        target_status="active",
        review_action="approved",
        operation_action="tenant_onboarding_approved",
        comment=payload.comment,
        reject_reason=None,
        grade=payload.grade,
    )


async def reject_tenant_application(
    session,
    current_user: CurrentUser,
    tenant_id: int,
    payload: TenantReviewRejectRequest,
) -> TenantReviewDecisionResponse:
    return await _review_tenant_application(
        session,
        current_user,
        tenant_id,
        target_status="rejected",
        review_action="rejected",
        operation_action="tenant_onboarding_rejected",
        comment=payload.reason,
        reject_reason=payload.reason,
        grade=None,
    )
