from __future__ import annotations

from fastapi import HTTPException

from app.core.security import CurrentUser


PLATFORM_REVIEW_ROLES = {"super_admin", "province_admin", "city_admin"}
ORG_APPLICATION_ROLES = {"org_admin"}
MEMBER_ROLES = {"member"}


def is_platform_reviewer(user: CurrentUser) -> bool:
    return user.role in PLATFORM_REVIEW_ROLES


def is_org_admin(user: CurrentUser) -> bool:
    return user.role == "org_admin"


def is_member(user: CurrentUser) -> bool:
    return user.role in MEMBER_ROLES


def can_submit_tenant_application(user: CurrentUser) -> bool:
    return user.role in ORG_APPLICATION_ROLES


def can_review_tenant_application(user: CurrentUser) -> bool:
    return is_platform_reviewer(user)


def can_access_tenant(user: CurrentUser, tenant_id: int | None) -> bool:
    if user.role == "super_admin":
        return True
    if user.role == "org_admin":
        return tenant_id is not None and user.tenant_id == tenant_id
    return False


def can_access_region(user: CurrentUser, *, province: str | None, city: str | None) -> bool:
    if user.role == "super_admin":
        return True
    if user.role == "province_admin":
        return province is not None and user.province == province
    if user.role == "city_admin":
        return province is not None and city is not None and user.province == province and user.city == city
    return False


def ensure_can_submit_tenant_application(user: CurrentUser) -> None:
    if not can_submit_tenant_application(user):
        raise HTTPException(status_code=403, detail="Forbidden")


def ensure_can_review_tenant_application(user: CurrentUser) -> None:
    if not can_review_tenant_application(user):
        raise HTTPException(status_code=403, detail="Forbidden")


def ensure_is_member(user: CurrentUser) -> None:
    if not is_member(user):
        raise HTTPException(status_code=403, detail="Forbidden")


def ensure_can_access_own_user_resource(user: CurrentUser, user_id: int) -> None:
    ensure_is_member(user)
    if user.id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
