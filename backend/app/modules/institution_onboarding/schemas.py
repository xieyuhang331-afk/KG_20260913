from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")


class OnboardingSuccessEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    code: Literal[0]
    message: Literal["ok"]
    data: T


class InvitationSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_id: UUID
    institution_name: str
    institution_type: Literal["HEALTH_STORE", "LICENSED_CLINIC"]
    status: str
    expires_at: datetime
    version: int


class InvitationIssuedDTO(InvitationSummaryDTO):
    short_code: str = Field(pattern=r"^[0-9]{6}$")


class InvitationRevokedDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_id: UUID
    institution_name: str
    institution_type: Literal["HEALTH_STORE", "LICENSED_CLINIC"]
    status: str
    version: int


class ActivationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: UUID
    user_id: int
    status: Literal["DRAFT"]
    version: int


class ApplicationPublicDraftDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_representative_name: str | None = None
    registered_address: str | None = None
    service_address: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    service_tags: tuple[str, ...] | None = None


class ApplicationReviewDraftDTO(ApplicationPublicDraftDTO):
    credit_code: str | None = None
    contact_phone: str | None = None


class LicenseDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_type: Literal["BUSINESS_LICENSE", "MEDICAL_INSTITUTION_LICENSE"]
    private_file_id: UUID
    valid_from: date | None
    valid_until: date | None


class ApplicationDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: UUID
    status: str
    institution_type: Literal["HEALTH_STORE", "LICENSED_CLINIC"]
    draft: ApplicationPublicDraftDTO
    correction_fields: tuple[str, ...]
    correction_reason_code: str | None
    current_revision_no: int
    version: int
    tenant_id: UUID | None
    tenant_active: bool
    service_ready: bool
    licenses: tuple[LicenseDTO, ...]


class ApplicationCorrectionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str
    fields: tuple[str, ...]
    reason_code: str | None
    version: int


class ReviewQueueItemDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: UUID
    status: str
    submitted_at: datetime | None
    version: int


class ReviewRevisionDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_no: int
    snapshot: ApplicationReviewDraftDTO
    created_at: datetime


class ReviewMaterialDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_type: Literal["BUSINESS_LICENSE", "MEDICAL_INSTITUTION_LICENSE"]
    private_file_id: UUID
    valid_from: date | None
    valid_until: date | None
    status: str
    scanned_at: datetime | None


class ReviewDetailDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: UUID
    status: str
    draft: ApplicationReviewDraftDTO
    version: int
    revisions: tuple[ReviewRevisionDTO, ...]
    materials: tuple[ReviewMaterialDTO, ...]


class InvitationCreate(BaseModel):
    institution_name: str = Field(min_length=2, max_length=100)
    institution_type: Literal["HEALTH_STORE", "LICENSED_CLINIC"]
    applicant_phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    pilot_batch_code: str = Field(min_length=1, max_length=32)
    administrative_region_id: int = Field(gt=0)
    expires_in_minutes: int = Field(default=60, ge=5, le=1440)


class InvitationResendRequest(BaseModel):
    expected_version: int = Field(gt=0)
    expires_in_minutes: int = Field(default=60, ge=5, le=1440)


class InvitationRevokeRequest(BaseModel):
    expected_version: int = Field(gt=0)


class ActivationRequest(BaseModel):
    invitation_id: UUID
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    short_code: str = Field(pattern=r"^[0-9]{6}$")
    password: str = Field(min_length=12, max_length=128)
    totp_secret: str = Field(min_length=16, max_length=128)
    totp_code: str = Field(pattern=r"^[0-9]{6}$")

    @field_validator("totp_secret")
    @classmethod
    def validate_totp_secret(cls, value: str) -> str:
        normalized = value.strip().upper()
        try:
            decoded = base64.b32decode(normalized, casefold=True)
        except (binascii.Error, ValueError):
            raise ValueError("TOTP_SECRET_INVALID") from None
        if len(decoded) < 20:
            raise ValueError("TOTP_SECRET_TOO_WEAK")
        return normalized


class ApplicationDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credit_code: str = Field(min_length=8, max_length=32)
    legal_representative_name: str = Field(min_length=2, max_length=50)
    registered_address: str = Field(min_length=4, max_length=255)
    service_address: str = Field(min_length=4, max_length=255)
    contact_name: str = Field(min_length=2, max_length=50)
    contact_phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    contact_email: str = Field(min_length=3, max_length=254)
    service_tags: tuple[str, ...] = Field(min_length=1, max_length=20)
    expected_version: int = Field(gt=0)


class LicenseBinding(BaseModel):
    license_type: Literal["BUSINESS_LICENSE", "MEDICAL_INSTITUTION_LICENSE"]
    private_file_id: UUID
    license_no: str | None = Field(default=None, max_length=64)
    valid_from: date
    valid_until: date

    @model_validator(mode="after")
    def validate_validity(self):
        if self.valid_from > self.valid_until:
            raise ValueError("license validity range is invalid")
        return self


class ApplicationSubmitRequest(BaseModel):
    expected_version: int = Field(gt=0)
    licenses: tuple[LicenseBinding, ...] = Field(min_length=1, max_length=2)


class ApplicationResubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credit_code: str | None = Field(default=None, min_length=8, max_length=32)
    legal_representative_name: str | None = Field(default=None, min_length=2, max_length=50)
    registered_address: str | None = Field(default=None, min_length=4, max_length=255)
    service_address: str | None = Field(default=None, min_length=4, max_length=255)
    contact_name: str | None = Field(default=None, min_length=2, max_length=50)
    contact_phone: str | None = Field(default=None, pattern=r"^1[3-9][0-9]{9}$")
    contact_email: str | None = Field(default=None, min_length=3, max_length=254)
    service_tags: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=20)
    expected_version: int = Field(gt=0)
    licenses: tuple[LicenseBinding, ...] = Field(min_length=1, max_length=2)


class ReviewDecisionRequest(BaseModel):
    decision: Literal["APPROVED", "NEEDS_CORRECTION", "REJECTED"]
    expected_version: int = Field(gt=0)
    correction_fields: tuple[str, ...] = ()
    reason_code: str | None = Field(default=None, max_length=64)

    @field_validator("reason_code")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        return value.strip() if value else None
