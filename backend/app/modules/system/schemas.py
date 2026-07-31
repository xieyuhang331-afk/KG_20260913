from __future__ import annotations

from pydantic import BaseModel


class NotificationIntent(BaseModel):
    event: str
    tenant_id: int
    tenant_name: str | None
    recipient_scope: str
    title: str
    content: str
    payload: dict

