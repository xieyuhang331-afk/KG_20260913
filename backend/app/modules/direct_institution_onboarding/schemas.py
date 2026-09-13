from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator

PHONE_RE = re.compile(r"^1[3-9][0-9]{9}$", re.ASCII)


def _uuid_v7(value: UUID) -> UUID:
    normalized = UUID(int=value.int)
    if normalized.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return normalized


UuidV7 = Annotated[UUID, AfterValidator(_uuid_v7)]
ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _phone(value: object) -> str:
    if type(value) is not str or PHONE_RE.fullmatch(value) is None:
        raise ValueError("PHONE_INVALID")
    return value


class DirectCreateRequest(StrictModel):
    institution_name: str = Field(min_length=1, max_length=100)
    institution_type: Literal["HEALTH_STORE", "LICENSED_CLINIC"]
    admin_phone: str
    administrative_region_id: int = Field(ge=1)
    duplicate_acknowledged: bool = False
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    totp_code: str = Field(pattern=r"^[0-9]{6}$")

    _validate_phone = field_validator("admin_phone", mode="before")(_phone)


class DirectActivationRequest(StrictModel):
    onboarding_id: UuidV7
    credential_id: UuidV7
    activation_code: str = Field(min_length=16, max_length=256)
    phone: str
    password: str = Field(min_length=8, max_length=128)
    totp_secret: str = Field(min_length=16, max_length=128)
    totp_code: str = Field(pattern=r"^[0-9]{6}$")
    expected_version: ExpectedVersion

    _validate_phone = field_validator("phone", mode="before")(_phone)


class VersionedStepUpRequest(StrictModel):
    expected_version: ExpectedVersion
    totp_code: str = Field(pattern=r"^[0-9]{6}$")
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")


class ComplianceLicense(StrictModel):
    license_id: UuidV7
    license_type: Literal["BUSINESS_LICENSE", "MEDICAL_INSTITUTION_LICENSE"]
    license_no: str | None = Field(default=None, max_length=100)
    private_file_id: UuidV7
    valid_from: date
    valid_until: date


class DirectComplianceRequest(StrictModel):
    expected_version: ExpectedVersion
    institution_name: str = Field(min_length=1, max_length=100)
    institution_type: Literal["HEALTH_STORE", "LICENSED_CLINIC"]
    administrative_region_id: int = Field(ge=1)
    institution_code: str = Field(min_length=1, max_length=32)
    legal_representative_name: str = Field(min_length=1, max_length=50)
    unified_social_credit_code: str = Field(min_length=1, max_length=32)
    contact_name: str = Field(min_length=1, max_length=50)
    contact_phone: str
    address: str = Field(min_length=1, max_length=300)
    service_tags: tuple[
        Literal["HYPERTENSION", "GLUCOSE_METABOLISM", "DYSLIPIDEMIA", "OBESITY"],
        ...,
    ] = Field(min_length=1, max_length=4)
    licenses: tuple[ComplianceLicense, ...] = Field(min_length=1, max_length=10)

    _validate_phone = field_validator("contact_phone", mode="before")(_phone)


class CompliancePayloadV1(StrictModel):
    schema_version: Literal[1] = 1
    legal_representative_name: str = Field(min_length=1, max_length=50)
    unified_social_credit_code: str = Field(min_length=1, max_length=32)
    contact_name: str = Field(min_length=1, max_length=50)
    contact_phone: str
    address: str = Field(min_length=1, max_length=300)

    _validate_phone = field_validator("contact_phone", mode="before")(_phone)

    @classmethod
    def from_request(cls, request: DirectComplianceRequest) -> CompliancePayloadV1:
        return cls(
            legal_representative_name=request.legal_representative_name,
            unified_social_credit_code=request.unified_social_credit_code,
            contact_name=request.contact_name,
            contact_phone=request.contact_phone,
            address=request.address,
        )


class ComplianceDecisionRequest(StrictModel):
    expected_version: ExpectedVersion
    revision_id: UuidV7
    decision: Literal["APPROVE", "NEEDS_CORRECTION"]
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    correction_fields: tuple[str, ...] = Field(default=(), max_length=16)
    totp_code: str = Field(pattern=r"^[0-9]{6}$")


class AdminHandoffCreateRequest(StrictModel):
    new_phone: str
    expected_version: ExpectedVersion
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    totp_code: str = Field(pattern=r"^[0-9]{6}$")

    _validate_phone = field_validator("new_phone", mode="before")(_phone)


class AdminHandoffActivationRequest(StrictModel):
    onboarding_id: UuidV7
    handoff_id: UuidV7
    credential_id: UuidV7
    activation_code: str = Field(min_length=16, max_length=256)
    phone: str
    password: str = Field(min_length=8, max_length=128)
    totp_secret: str = Field(min_length=16, max_length=128)
    totp_code: str = Field(pattern=r"^[0-9]{6}$")
    expected_version: ExpectedVersion

    _validate_phone = field_validator("phone", mode="before")(_phone)


class DirectOnboardingDTO(StrictModel):
    onboarding_id: UuidV7
    tenant_id: UuidV7
    institution_code: str
    institution_name: str
    institution_type: str
    administrative_region_id: int
    status: str
    compliance_due_at: datetime | None
    current_revision_id: UuidV7 | None
    version: int


class DirectCredentialDTO(DirectOnboardingDTO):
    credential_id: UuidV7
    activation_code: str
    credential_expires_at: datetime


class DirectOnboardingPageDTO(StrictModel):
    items: tuple[DirectOnboardingDTO, ...]
    next_cursor: str | None = None


class DirectComplianceDTO(StrictModel):
    onboarding_id: UuidV7
    revision_id: UuidV7 | None
    revision_no: int | None
    status: str
    institution_name: str
    institution_type: str
    administrative_region_id: int
    institution_code: str
    service_tags: tuple[str, ...] = ()
    licenses: tuple[ComplianceLicense, ...] = ()
    correction_fields: tuple[str, ...] = ()
    version: int


class AdminHandoffCredentialDTO(StrictModel):
    onboarding_id: UuidV7
    handoff_id: UuidV7
    credential_id: UuidV7
    activation_code: str
    credential_expires_at: datetime
    status: str
    version: int


class AdminHandoffActivationDTO(StrictModel):
    onboarding_id: UuidV7
    handoff_id: UuidV7
    tenant_id: UuidV7
    status: str
    version: int
