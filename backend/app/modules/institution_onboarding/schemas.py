from __future__ import annotations

import base64
import binascii
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ApplicationSubmitRequest(BaseModel):
    expected_version: int = Field(gt=0)
    licenses: tuple[LicenseBinding, ...] = Field(min_length=1, max_length=2)


class ApplicationResubmitRequest(ApplicationDraftRequest):
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
