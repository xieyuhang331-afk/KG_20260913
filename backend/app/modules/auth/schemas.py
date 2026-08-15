from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserRegisterRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")
    password: str = Field(..., min_length=8, max_length=128)


class UserRegisterResponse(BaseModel):
    id: int
    phone: str
    role: str
    status: str | None
    verify_status: str | None
    tenant_id: int | None
    created_at: datetime | None


class AuthLoginRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1\d{10}$")
    password: str = Field(..., min_length=8, max_length=128)
    totp_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class AuthLoginUser(BaseModel):
    id: int
    phone: str
    role: str
    status: str | None
    tenant_id: int | None
    org_id: int | None = None
    province: str | None = None
    city: str | None = None


class AuthLoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    user: AuthLoginUser


class AuthMeResponse(BaseModel):
    id: int
    role: str
    tenant_id: int | None
    org_id: int | None
    province: str | None
    city: str | None


class UserIdentityRequest(BaseModel):
    real_name: str = Field(..., min_length=1, max_length=50)
    id_card: str = Field(..., pattern=r"^\d{18}$")


class UserIdentityResponse(BaseModel):
    user_id: int
    real_name: str
    id_card_masked: str
    verify_status: str


class IdentityVerificationSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    real_name: str = Field(min_length=2, max_length=50)
    id_card: str = Field(pattern=r"^\d{17}[0-9X]$")
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^\S(?:.*\S)?$")
    consent_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")

    @field_validator("real_name")
    @classmethod
    def validate_real_name(cls, value: str) -> str:
        normalized = value.strip()
        if normalized != value or any(ord(character) < 32 for character in value):
            raise ValueError("real_name is invalid")
        return value


class IdentityVerificationSubmissionResponse(BaseModel):
    status: str
    submission_version: int
    id_card_masked: str
    submitted_at: datetime
    outcome: str


class IdentityVerificationStatusResponse(BaseModel):
    status: str
    submission_version: int | None = None
    id_card_masked: str | None = None
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    rejection_reason_code: str | None = None
    resubmit_available_at: datetime | None = None


class TenantBindingRequest(BaseModel):
    tenant_id: int = Field(..., ge=1)


class TenantBindingResponse(BaseModel):
    user_id: int
    tenant_id: int
    tenant_code: str
    tenant_name: str
    bound_at: datetime | None
