from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TenantReviewQueueQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    keyword: str | None = None
    province: str | None = None
    city: str | None = None


class TenantReviewQueueItem(BaseModel):
    tenant_id: int
    tenant_code: str
    name: str
    type: str
    credit_code: str | None
    province: str
    city: str
    district: str | None
    contact_name: str | None
    contact_phone: str | None
    status: str
    submitted_at: datetime
    attachment_count: int


class TenantReviewQueueResponse(BaseModel):
    items: list[TenantReviewQueueItem]
    page: int
    page_size: int
    total: int


class TenantReviewDetailTenant(BaseModel):
    id: int
    tenant_code: str
    name: str
    short_name: str | None
    type: str
    credit_code: str | None
    license_no: str | None
    license_image: str | None
    legal_person_name: str | None
    province: str
    city: str
    district: str | None
    address: str | None
    grade: str | None


class TenantReviewDetailContact(BaseModel):
    contact_name: str | None
    contact_phone: str | None
    contact_email: str | None


class TenantReviewDetailAttachment(BaseModel):
    id: int
    file_type: str
    file_url: str
    created_at: datetime


class TenantReviewDetailStatus(BaseModel):
    current: str
    reviewed_by: int | None
    reviewed_at: datetime | None
    reject_reason: str | None
    approved_at: datetime | None


class TenantReviewDetailResponse(BaseModel):
    tenant: TenantReviewDetailTenant
    contact: TenantReviewDetailContact
    attachments: list[TenantReviewDetailAttachment]
    status: TenantReviewDetailStatus
    submitted_at: datetime


class TenantReviewApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=500)
    grade: str | None = Field(default=None, max_length=20)


class TenantReviewRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class TenantReviewDecisionResponse(BaseModel):
    tenant_id: int
    status: str
    reviewed_by: int
    reviewed_at: datetime
