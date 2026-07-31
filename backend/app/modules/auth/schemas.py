from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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


class TenantBindingRequest(BaseModel):
    tenant_id: int = Field(..., ge=1)


class TenantBindingResponse(BaseModel):
    user_id: int
    tenant_id: int
    tenant_code: str
    tenant_name: str
    bound_at: datetime | None
