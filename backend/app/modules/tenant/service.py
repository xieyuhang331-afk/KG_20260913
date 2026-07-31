from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException

from app.core.permissions import ensure_can_submit_tenant_application, ensure_is_member
from app.core.security import CurrentUser
from app.modules.tenant.repository import (
    create_tenant_application_record,
    get_tenant_application_status,
    list_active_tenant_options,
    list_tenant_applications_by_org,
    tenant_exists_by_credit_code,
)
from app.modules.tenant.schemas import (
    ActiveTenantListItem,
    ActiveTenantListResponse,
    ActiveTenantQuery,
    MyTenantApplicationListItem,
    MyTenantApplicationListResponse,
    MyTenantApplicationQuery,
    TenantApplicationCreate,
    TenantApplicationResponse,
    TenantApplicationStatusResponse,
)


def generate_tenant_code() -> str:
    return f"T{uuid4().hex[:12].upper()}"


async def submit_application(session, current_user: CurrentUser, payload: TenantApplicationCreate) -> TenantApplicationResponse:
    ensure_can_submit_tenant_application(current_user)
    if current_user.org_id is None:
        raise HTTPException(status_code=403, detail="Forbidden")

    if await tenant_exists_by_credit_code(session, payload.credit_code):
        raise HTTPException(status_code=409, detail="Tenant application already exists")

    tenant_data = payload.model_dump(exclude={"attachments"})
    tenant_data["org_id"] = current_user.org_id
    tenant_data["tenant_code"] = generate_tenant_code()
    tenant_data["status"] = "pending"

    tenant, attachments = await create_tenant_application_record(
        session,
        tenant_data=tenant_data,
        attachments=payload.attachments,
    )
    await session.commit()

    return TenantApplicationResponse(
        id=tenant.id,
        tenant_code=tenant.tenant_code,
        name=tenant.name,
        status=tenant.status,
        attachment_count=len(attachments),
    )


async def get_application_status(
    session,
    current_user: CurrentUser,
    tenant_id: int,
) -> TenantApplicationStatusResponse:
    if current_user.role != "org_admin" or current_user.org_id is None:
        raise HTTPException(status_code=403, detail="Forbidden")

    status = await get_tenant_application_status(session, tenant_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Tenant application not found")
    if status["org_id"] != current_user.org_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    return TenantApplicationStatusResponse(
        tenant_id=status["tenant_id"],
        tenant_code=status["tenant_code"],
        name=status["name"],
        status=status["status"],
        submitted_at=status["submitted_at"],
        reviewed_at=status["reviewed_at"],
        approved_at=status["approved_at"],
        reject_reason=status["reject_reason"],
    )


async def list_my_applications(
    session,
    current_user: CurrentUser,
    query: MyTenantApplicationQuery,
) -> MyTenantApplicationListResponse:
    if current_user.role != "org_admin" or current_user.org_id is None:
        raise HTTPException(status_code=403, detail="Forbidden")

    items, total = await list_tenant_applications_by_org(
        session,
        org_id=current_user.org_id,
        status=query.status,
        page=query.page,
        page_size=query.page_size,
    )
    return MyTenantApplicationListResponse(
        items=[MyTenantApplicationListItem(**item) for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def list_active_tenants(
    session,
    current_user: CurrentUser,
    query: ActiveTenantQuery,
) -> ActiveTenantListResponse:
    ensure_is_member(current_user)
    items, total = await list_active_tenant_options(
        session,
        province=query.province,
        city=query.city,
        keyword=query.keyword,
        page=query.page,
        page_size=query.page_size,
    )
    return ActiveTenantListResponse(
        items=[ActiveTenantListItem(**item) for item in items],
        total=total,
        page=query.page,
        page_size=query.page_size,
    )
