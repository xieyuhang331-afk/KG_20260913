from __future__ import annotations

from sqlalchemy import func, or_, select

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.tenant.models import Tenant, TenantAttachment, TenantReviewLog
from app.modules.tenant.schemas import TenantAttachmentCreate


def _ensure_mapped() -> None:
    map_core_model_classes()


async def tenant_exists_by_credit_code(session, credit_code: str) -> bool:
    _ensure_mapped()
    result = await session.execute(select(Tenant.id).where(Tenant.credit_code == credit_code).limit(1))
    return result.scalar_one_or_none() is not None


async def list_pending_tenant_applications(
    session,
    *,
    province: str | None,
    city: str | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    _ensure_mapped()
    filters = [Tenant.status == "pending"]
    if province is not None:
        filters.append(Tenant.province == province)
    if city is not None:
        filters.append(Tenant.city == city)
    if keyword:
        pattern = f"%{keyword}%"
        filters.append(
            or_(
                Tenant.name.ilike(pattern),
                Tenant.tenant_code.ilike(pattern),
                Tenant.credit_code.ilike(pattern),
            )
        )

    total_result = await session.execute(select(func.count()).select_from(Tenant).where(*filters))
    total = total_result.scalar_one()

    attachment_counts = (
        select(
            TenantAttachment.tenant_id.label("tenant_id"),
            func.count(TenantAttachment.id).label("attachment_count"),
        )
        .group_by(TenantAttachment.tenant_id)
        .subquery()
    )
    statement = (
        select(
            Tenant.id.label("tenant_id"),
            Tenant.tenant_code,
            Tenant.name,
            Tenant.type,
            Tenant.credit_code,
            Tenant.province,
            Tenant.city,
            Tenant.district,
            Tenant.contact_name,
            Tenant.contact_phone,
            Tenant.status,
            Tenant.created_at.label("submitted_at"),
            func.coalesce(attachment_counts.c.attachment_count, 0).label("attachment_count"),
        )
        .outerjoin(attachment_counts, attachment_counts.c.tenant_id == Tenant.id)
        .where(*filters)
        .order_by(Tenant.created_at.asc(), Tenant.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(statement)
    return [dict(row) for row in result.mappings().all()], total


async def list_active_tenant_options(
    session,
    *,
    province: str | None,
    city: str | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    _ensure_mapped()
    filters = [Tenant.status == "active"]
    if province is not None:
        filters.append(Tenant.province == province)
    if city is not None:
        filters.append(Tenant.city == city)
    if keyword:
        pattern = f"%{keyword}%"
        filters.append(
            or_(
                Tenant.name.ilike(pattern),
                Tenant.tenant_code.ilike(pattern),
                Tenant.address.ilike(pattern),
            )
        )

    total_result = await session.execute(select(func.count()).select_from(Tenant).where(*filters))
    total = total_result.scalar_one()

    statement = (
        select(
            Tenant.id.label("tenant_id"),
            Tenant.tenant_code,
            Tenant.name,
            Tenant.type,
            Tenant.grade,
            Tenant.province,
            Tenant.city,
            Tenant.district,
            Tenant.address,
            Tenant.logo_url,
            Tenant.contact_phone,
            Tenant.approved_at,
        )
        .where(*filters)
        .order_by(Tenant.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(statement)
    return [dict(row) for row in result.mappings().all()], total


async def list_tenant_applications_by_org(
    session,
    *,
    org_id: int,
    status: str | None,
    page: int,
    page_size: int,
) -> tuple[list[dict], int]:
    _ensure_mapped()
    filters = [Tenant.org_id == org_id]
    if status is not None:
        filters.append(Tenant.status == status)

    total_result = await session.execute(select(func.count()).select_from(Tenant).where(*filters))
    total = total_result.scalar_one()

    statement = (
        select(
            Tenant.id.label("tenant_id"),
            Tenant.tenant_code,
            Tenant.name,
            Tenant.status,
            Tenant.province,
            Tenant.city,
            Tenant.contact_name,
            Tenant.contact_phone,
            Tenant.created_at.label("submitted_at"),
            Tenant.reviewed_at,
            Tenant.approved_at,
            Tenant.reject_reason,
        )
        .where(*filters)
        .order_by(Tenant.created_at.desc(), Tenant.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await session.execute(statement)
    return [dict(row) for row in result.mappings().all()], total


async def get_tenant_application_detail(session, tenant_id: int) -> dict | None:
    _ensure_mapped()
    tenant_statement = select(
        Tenant.id,
        Tenant.tenant_code,
        Tenant.name,
        Tenant.short_name,
        Tenant.type,
        Tenant.credit_code,
        Tenant.license_no,
        Tenant.license_image,
        Tenant.legal_person_name,
        Tenant.province,
        Tenant.city,
        Tenant.district,
        Tenant.address,
        Tenant.grade,
        Tenant.contact_name,
        Tenant.contact_phone,
        Tenant.contact_email,
        Tenant.status,
        Tenant.reviewed_by,
        Tenant.reviewed_at,
        Tenant.reject_reason,
        Tenant.approved_at,
        Tenant.created_at,
        Tenant.updated_at,
    ).where(Tenant.id == tenant_id)
    tenant_result = await session.execute(tenant_statement)
    tenant = tenant_result.mappings().one_or_none()
    if tenant is None:
        return None

    attachment_statement = (
        select(
            TenantAttachment.id,
            TenantAttachment.file_type,
            TenantAttachment.file_url,
            TenantAttachment.created_at,
        )
        .where(TenantAttachment.tenant_id == tenant_id)
        .order_by(TenantAttachment.created_at.asc(), TenantAttachment.id.asc())
    )
    attachment_result = await session.execute(attachment_statement)

    return {
        "tenant": dict(tenant),
        "attachments": [dict(row) for row in attachment_result.mappings().all()],
    }


async def get_tenant_by_id_for_binding(session, tenant_id: int) -> dict | None:
    _ensure_mapped()
    statement = select(
        Tenant.id.label("tenant_id"),
        Tenant.tenant_code,
        Tenant.name,
        Tenant.status,
    ).where(Tenant.id == tenant_id)
    result = await session.execute(statement)
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def get_tenant_application_status(session, tenant_id: int) -> dict | None:
    _ensure_mapped()
    statement = select(
        Tenant.id.label("tenant_id"),
        Tenant.tenant_code,
        Tenant.name,
        Tenant.org_id,
        Tenant.status,
        Tenant.created_at.label("submitted_at"),
        Tenant.reviewed_at,
        Tenant.approved_at,
        Tenant.reject_reason,
    ).where(Tenant.id == tenant_id)
    result = await session.execute(statement)
    row = result.mappings().one_or_none()
    return dict(row) if row is not None else None


async def get_tenant_for_review_update(session, tenant_id: int):
    _ensure_mapped()
    statement = select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    result = await session.execute(statement)
    return result.scalar_one_or_none()


async def apply_tenant_review_decision(
    session,
    tenant,
    *,
    status: str,
    reviewed_by: int,
    reviewed_at,
    approved_at,
    reject_reason: str | None,
    grade: str | None,
):
    tenant.status = status
    tenant.reviewed_by = reviewed_by
    tenant.reviewed_at = reviewed_at
    tenant.approved_at = approved_at
    tenant.reject_reason = reject_reason
    if grade is not None:
        tenant.grade = grade
    await session.flush()
    return tenant


async def create_tenant_review_log(
    session,
    *,
    tenant_id: int,
    reviewer_id: int,
    action: str,
    grade: str | None,
    comment: str | None,
):
    _ensure_mapped()
    log = TenantReviewLog()
    log.tenant_id = tenant_id
    log.reviewer_id = reviewer_id
    log.action = action
    log.grade = grade
    log.comment = comment
    session.add(log)
    await session.flush()
    return log


async def create_tenant_application_record(
    session,
    *,
    tenant_data: dict,
    attachments: list[TenantAttachmentCreate],
) -> tuple[Tenant, list[TenantAttachment]]:
    _ensure_mapped()
    tenant = Tenant()
    for key, value in tenant_data.items():
        setattr(tenant, key, value)

    session.add(tenant)
    await session.flush()

    attachment_records: list[TenantAttachment] = []
    for attachment in attachments:
        record = TenantAttachment()
        record.tenant_id = tenant.id
        record.file_type = attachment.file_type
        record.file_url = attachment.file_url
        session.add(record)
        attachment_records.append(record)

    if attachment_records:
        await session.flush()

    return tenant, attachment_records
