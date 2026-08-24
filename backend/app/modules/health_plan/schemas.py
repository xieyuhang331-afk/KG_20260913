from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.modules.health_plan.domain import MODULE_CODES


def _uuid_v7(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID_V7_REQUIRED")
    normalized = UUID(int=value.int)
    if normalized.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return normalized


UuidV7 = Annotated[UUID, AfterValidator(_uuid_v7)]
ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]
ModuleCode = Literal[
    "BLOOD_PRESSURE_CARDIOVASCULAR",
    "GLUCOSE_METABOLISM",
    "LIPID_METABOLISM",
    "WEIGHT_ABDOMINAL_OBESITY",
]
StructuredCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
PlanStatus = Literal[
    "REQUESTED",
    "GENERATING",
    "GENERATION_FAILED",
    "IN_REVIEW",
    "NEEDS_CORRECTION",
    "USER_DECISION_PENDING",
    "NEEDS_EXPLANATION",
    "DECLINED",
    "REJECTED",
    "ACTIVE",
    "SUPERSEDED",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthPlanTemplateCreateRequest(StrictModel):
    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    applicable_modules: tuple[ModuleCode, ...] = Field(min_length=1, max_length=4)
    goals_by_module: dict[ModuleCode, tuple[StructuredCode, ...]]
    stage_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=16)
    milestone_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=32)
    sop_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=64)
    contraindication_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=64)
    user_message_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=64)
    therapist_action_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=64)
    medical_approval_ref: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_module_goals(self):
        if set(self.goals_by_module) != set(self.applicable_modules):
            raise ValueError("TEMPLATE_MODULE_GOALS_MISMATCH")
        if any(not values or any(not value or len(value) > 64 for value in values) for values in self.goals_by_module.values()):
            raise ValueError("TEMPLATE_CODE_INVALID")
        return self


class TemplateVersionRequest(StrictModel):
    expected_version: ExpectedVersion


class HealthPlanTemplateDTO(StrictModel):
    template_version_id: UuidV7
    template_code: str
    version_no: int = Field(ge=1)
    status: Literal["DRAFT", "PUBLISHED", "RETIRED"]
    applicable_modules: tuple[ModuleCode, ...]
    goals_by_module: dict[ModuleCode, tuple[str, ...]]
    stage_codes: tuple[str, ...]
    milestone_codes: tuple[str, ...]
    sop_codes: tuple[str, ...]
    contraindication_codes: tuple[str, ...]
    user_message_codes: tuple[str, ...]
    therapist_action_codes: tuple[str, ...]
    medical_approval_ref: str
    created_at: AwareDatetime
    published_at: AwareDatetime | None = None
    retired_at: AwareDatetime | None = None
    version: ExpectedVersion


class HealthPlanTemplatePageDTO(StrictModel):
    items: tuple[HealthPlanTemplateDTO, ...]
    next_cursor: str | None = None


class CreatePlanGenerationRequest(StrictModel):
    expected_service_case_version: ExpectedVersion


class PlanGenerationEligibilityDTO(StrictModel):
    service_case_id: UuidV7
    eligible: bool
    blocking_codes: tuple[str, ...]
    current_assessment_id: UuidV7 | None = None
    assessment_version: int | None = Field(default=None, ge=1)
    published_template_version_id: UuidV7 | None = None
    active_generation_request_id: UuidV7 | None = None
    active_plan_id: UuidV7 | None = None
    expected_service_case_version: ExpectedVersion
    evaluated_at: AwareDatetime


class PlanGenerationRequestDTO(StrictModel):
    request_id: UuidV7
    service_case_id: UuidV7
    status: PlanStatus
    current_plan_id: UuidV7 | None = None
    current_plan_version: int | None = Field(default=None, ge=1)
    failure_code: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    version: ExpectedVersion


class ReviewDecisionRequest(StrictModel):
    decision: Literal["APPROVED", "NEEDS_CORRECTION", "REJECTED"]
    reason_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=16)
    expected_version: ExpectedVersion


class ClaimRequest(StrictModel):
    expected_version: ExpectedVersion


class PlanExplanationRequest(StrictModel):
    explanation_codes: tuple[StructuredCode, ...] = Field(min_length=1, max_length=16)
    expected_version: ExpectedVersion


class UserDecisionRequest(StrictModel):
    decision: Literal["ACCEPT", "NEEDS_EXPLANATION", "DECLINE"]
    expected_version: ExpectedVersion


class ModuleSummaryDTO(StrictModel):
    module_code: ModuleCode
    risk_level: Literal["NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION", "HIGH_RISK"]


class PlanSummaryDTO(StrictModel):
    plan_id: UuidV7
    service_case_id: UuidV7
    version_no: int = Field(ge=1)
    status: PlanStatus
    template_code: str
    template_version: int = Field(ge=1)
    overall_risk_level: Literal["NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION", "HIGH_RISK"]
    created_at: AwareDatetime
    updated_at: AwareDatetime
    version: ExpectedVersion


class ReviewSummaryDTO(StrictModel):
    status: Literal["PENDING", "CLAIMED", "APPROVED", "NEEDS_CORRECTION", "REJECTED"]
    decision_codes: tuple[str, ...] = ()
    decided_at: AwareDatetime | None = None


class UserDecisionSummaryDTO(StrictModel):
    decision: Literal["ACCEPT", "NEEDS_EXPLANATION", "DECLINE"] | None = None
    decided_at: AwareDatetime | None = None


class ExplanationDTO(StrictModel):
    explanation_id: UuidV7
    explanation_codes: tuple[str, ...]
    created_at: AwareDatetime


class PlanDetailDTO(PlanSummaryDTO):
    module_summaries: tuple[ModuleSummaryDTO, ...]
    goals: tuple[str, ...]
    stages: tuple[str, ...]
    milestones: tuple[str, ...]
    sop_items: tuple[str, ...]
    contraindication_codes: tuple[str, ...]
    user_message_codes: tuple[str, ...]
    therapist_action_codes: tuple[str, ...]
    review_summary: ReviewSummaryDTO
    user_decision_summary: UserDecisionSummaryDTO
    explanations: tuple[ExplanationDTO, ...]


class PlanPageDTO(StrictModel):
    items: tuple[PlanSummaryDTO, ...]
    next_cursor: str | None = None


class PlanReviewDTO(StrictModel):
    review_id: UuidV7
    request_id: UuidV7
    plan_id: UuidV7
    service_case_id: UuidV7
    status: Literal["PENDING", "CLAIMED", "APPROVED", "NEEDS_CORRECTION", "REJECTED"]
    plan_version_no: int = Field(ge=1)
    claimed_at: AwareDatetime | None = None
    decided_at: AwareDatetime | None = None
    version: ExpectedVersion


class PlanReviewPageDTO(StrictModel):
    items: tuple[PlanReviewDTO, ...]
    next_cursor: str | None = None


def utc_now() -> datetime:
    """Single importable clock seam for tests; production stores database timestamps."""

    from datetime import timezone

    return datetime.now(timezone.utc)
