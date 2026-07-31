from __future__ import annotations

from app.modules.system.schemas import NotificationIntent


def _tenant_value(tenant, key: str):
    if isinstance(tenant, dict):
        return tenant[key]
    return getattr(tenant, key)


def build_tenant_review_notification_intent(*, tenant, action: str) -> NotificationIntent:
    tenant_id = _tenant_value(tenant, "id")
    tenant_name = _tenant_value(tenant, "name")

    if action == "approved":
        return NotificationIntent(
            event="tenant_onboarding_approved",
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            recipient_scope="tenant_org_admin",
            title="门店入驻审核已通过",
            content="您的门店入驻申请已通过审核，门店状态已启用。",
            payload={
                "tenant_id": tenant_id,
                "status": "active",
                "action": "approved",
            },
        )

    if action == "rejected":
        reject_reason = _tenant_value(tenant, "reject_reason")
        return NotificationIntent(
            event="tenant_onboarding_rejected",
            tenant_id=tenant_id,
            tenant_name=tenant_name,
            recipient_scope="tenant_org_admin",
            title="门店入驻审核未通过",
            content=f"您的门店入驻申请未通过审核，原因：{reject_reason}",
            payload={
                "tenant_id": tenant_id,
                "status": "rejected",
                "action": "rejected",
                "reject_reason": reject_reason,
            },
        )

    raise ValueError(f"Unsupported tenant review notification action: {action}")

