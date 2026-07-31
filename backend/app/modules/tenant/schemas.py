from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TenantAttachmentCreate(BaseModel):
    file_type: str = Field(min_length=1, max_length=20)
    file_url: str = Field(min_length=1, max_length=500)


class TenantApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str = Field(min_length=1, max_length=20)
    credit_code: str = Field(min_length=1, max_length=18)
    license_no: str | None = Field(default=None, max_length=50)
    license_image: str | None = Field(default=None, max_length=500)
    legal_person_name: str | None = Field(default=None, max_length=50)
    province: str = Field(min_length=1, max_length=30)
    city: str = Field(min_length=1, max_length=30)
    district: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=50)
    contact_phone: str | None = Field(default=None, max_length=11)
    contact_email: str | None = Field(default=None, max_length=100)
    attachments: list[TenantAttachmentCreate] = Field(default_factory=list)


class TenantApplicationResponse(BaseModel):
    id: int
    tenant_code: str
    name: str
    status: str
    attachment_count: int


class TenantApplicationStatusResponse(BaseModel):
    tenant_id: int
    tenant_code: str
    name: str
    status: str
    submitted_at: datetime
    reviewed_at: datetime | None
    approved_at: datetime | None
    reject_reason: str | None


class MyTenantApplicationQuery(BaseModel):
    status: Literal["pending", "active", "rejected"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class MyTenantApplicationListItem(BaseModel):
    tenant_id: int
    tenant_code: str
    name: str
    status: str
    province: str
    city: str
    contact_name: str | None
    contact_phone: str | None
    submitted_at: datetime
    reviewed_at: datetime | None
    approved_at: datetime | None
    reject_reason: str | None


class MyTenantApplicationListResponse(BaseModel):
    items: list[MyTenantApplicationListItem]
    total: int
    page: int
    page_size: int


class ActiveTenantQuery(BaseModel):
    province: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=30)
    keyword: str | None = Field(default=None, max_length=100)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ActiveTenantListItem(BaseModel):
    tenant_id: int
    tenant_code: str
    name: str
    type: str
    grade: str | None
    province: str
    city: str
    district: str | None
    address: str | None
    logo_url: str | None
    contact_phone: str | None
    approved_at: datetime | None


class ActiveTenantListResponse(BaseModel):
    items: list[ActiveTenantListItem]
    total: int
    page: int
    page_size: int
