from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


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


class PlatformAdminManualIdentityReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^\S(?:.*\S)?$",
    )
    submission_version: int = Field(ge=1)
    decision_basis_code: Literal["APPROVED_OFFLINE_IDENTITY_CHECK"]


class PlatformAdminManualIdentityReviewResponse(BaseModel):
    user_id: int
    submission_version: int
    status: str
    decision_ref: str
    replayed: bool


class PlatformIdentityReviewStepUpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(min_length=1, max_length=256)


class PlatformIdentityReviewStepUpResponse(BaseModel):
    step_up_token: str
    token_type: Literal["identity_review_step_up"] = "identity_review_step_up"
    expires_in: Literal[120] = 120


class IdentityReviewQueueItem(BaseModel):
    user_id: int
    submission_version: int
    id_card_masked: str
    submitted_at: datetime

    @classmethod
    def from_submission(cls, model):
        return cls(
            user_id=model.user_ref,
            submission_version=model.version,
            id_card_masked=model.id_card_masked,
            submitted_at=model.submitted_at,
        )


class IdentityReviewQueueResponse(BaseModel):
    items: list[IdentityReviewQueueItem]
    page: int
    page_size: int
    total: int


class IdentityReviewDetailResponse(BaseModel):
    user_id: int
    submission_version: int
    status: str
    real_name: str
    id_card: str
    id_card_masked: str
    consent_version: str
    submitted_at: datetime

    @classmethod
    def from_review(cls, model, name: str, card: str):
        return cls(
            user_id=model.user_ref,
            submission_version=model.version,
            status=model.status,
            real_name=name,
            id_card=card,
            id_card_masked=model.id_card_masked,
            consent_version=model.consent_version,
            submitted_at=model.submitted_at,
        )


class IdentityReviewRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason_code: Literal["OFFLINE_CHECK_FAILED"]
    decided_at: datetime | None = None


class PlatformIdentityReviewRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=128)
    reason_code: Literal["OFFLINE_CHECK_FAILED"]


class IdentityReviewRejectResponse(BaseModel):
    user_id: int
    submission_version: int
    status: str
    replayed: bool


class ReviewErrorResponse(BaseModel):
    detail: str


class PlatformIdentityReviewStepUpEnvelope(BaseModel):
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: PlatformIdentityReviewStepUpResponse


class IdentityReviewQueueEnvelope(BaseModel):
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: IdentityReviewQueueResponse


class IdentityReviewDetailEnvelope(BaseModel):
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: IdentityReviewDetailResponse


class PlatformAdminManualIdentityReviewEnvelope(BaseModel):
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: PlatformAdminManualIdentityReviewResponse


class IdentityReviewRejectEnvelope(BaseModel):
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: IdentityReviewRejectResponse
