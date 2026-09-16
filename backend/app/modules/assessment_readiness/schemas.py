from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class AssessmentReadinessDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_case_id: UUID
    status: Literal["DATA_INSUFFICIENT", "DATA_SYNC_PENDING", "DISPUTED", "ASSESSMENT_READY"]
    reason_codes: tuple[str, ...]
    missing_indicator_codes: tuple[str, ...] = ()
    expired_indicator_codes: tuple[str, ...] = ()
    disputed_indicator_codes: tuple[str, ...] = ()
    profile_revision_id: UUID | None = None
    policy_version: str | None = None
    projection_status: Literal["CURRENT", "SYNC_PENDING", "UNAVAILABLE"] | None = None
    data_as_of: AwareDatetime | None = None
    generated_at: AwareDatetime | None = None
    assembly_id: UUID | None = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadinessIndicatorRequirementDTO(_StrictModel):
    indicator_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    max_age_days: int | None = Field(default=None, ge=0, le=3650)


class ReadinessPolicyContentDTO(_StrictModel):
    required_profile_sections: tuple[str, ...] = Field(min_length=1, max_length=64)
    required_indicators: tuple[ReadinessIndicatorRequirementDTO, ...] = Field(
        min_length=1, max_length=128
    )
    allowed_states: tuple[Literal["SELF_REPORTED", "VERIFIED", "REVIEWED"], ...] = Field(
        min_length=1, max_length=3
    )
    projection_version: int = Field(ge=2, le=32767)
    rule_version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")

    @field_validator("required_profile_sections")
    @classmethod
    def unique_sections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(
            not item or len(item) > 64 or not item.replace("_", "").replace("-", "").isalnum()
            for item in value
        ):
            raise ValueError("READINESS_POLICY_INVALID")
        return value

    @field_validator("required_indicators")
    @classmethod
    def unique_indicators(
        cls, value: tuple[ReadinessIndicatorRequirementDTO, ...]
    ) -> tuple[ReadinessIndicatorRequirementDTO, ...]:
        if len({item.indicator_code for item in value}) != len(value):
            raise ValueError("READINESS_POLICY_INVALID")
        return value

    @field_validator("allowed_states")
    @classmethod
    def unique_states(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("READINESS_POLICY_INVALID")
        return value


class ReadinessPolicyCreateRequest(_StrictModel):
    version_no: int = Field(ge=1, le=2**63 - 1)
    content: ReadinessPolicyContentDTO
    approval_evidence_ref: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"
    )
    approval_package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReadinessPolicyDraftUpdateRequest(_StrictModel):
    expected_version: int = Field(ge=1, le=2**63 - 1)
    content: ReadinessPolicyContentDTO
    approval_evidence_ref: str = Field(
        min_length=8, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$"
    )
    approval_package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReadinessPolicyVersionRequest(_StrictModel):
    expected_version: int = Field(ge=1, le=2**63 - 1)


class ReadinessPolicyReviewRequest(ReadinessPolicyVersionRequest):
    decision: Literal["APPROVE", "CORRECTION"]
    reason_code: Literal[
        "PROFESSIONAL_POLICY_APPROVED",
        "POLICY_CONTENT_CORRECTION_REQUIRED",
        "APPROVAL_EVIDENCE_CORRECTION_REQUIRED",
    ]


class ReadinessPolicyGovernanceRequest(ReadinessPolicyVersionRequest):
    reason_code: Literal[
        "APPROVED_POLICY_RELEASE",
        "POLICY_SAFETY_REVIEW_REQUIRED",
        "POLICY_SAFETY_REVIEW_CLEARED",
        "SUPERSEDED_BY_APPROVED_POLICY",
        "POLICY_WITHDRAWN",
    ]


class ReadinessPolicyVersionDTO(_StrictModel):
    policy_version_id: UUID
    version_no: int
    status: Literal[
        "DRAFT",
        "IN_REVIEW",
        "NEEDS_CORRECTION",
        "APPROVED",
        "PUBLISHED",
        "SUSPENDED",
        "RETIRED",
    ]
    content: ReadinessPolicyContentDTO
    approval_evidence_ref: str | None
    approval_package_digest: str | None
    medical_approval_verified: bool
    policy_digest: str
    effective_from: AwareDatetime | None
    suspended_at: AwareDatetime | None
    retired_at: AwareDatetime | None
    row_version: int
