from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


def _uuid_v7(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID_V7_REQUIRED")
    normalized = UUID(int=value.int)
    if normalized.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return normalized


UuidV7 = Annotated[UUID, AfterValidator(_uuid_v7)]
ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]
RiskLevel = Literal["NOT_ASSESSED", "WITHIN_RANGE", "ATTENTION", "HIGH_RISK"]
AssessmentStatus = Literal[
    "DRAFT_SNAPSHOT", "RUNNING", "COMPLETED", "FAILED", "UNDER_REVIEW", "SUPERSEDED"
]
HighRiskTaskStatus = Literal["OPEN", "CLAIMED", "ESCALATED", "REFERRED", "RESOLVED"]
ModuleCode = Literal[
    "BLOOD_PRESSURE_CARDIOVASCULAR",
    "GLUCOSE_METABOLISM",
    "LIPID_METABOLISM",
    "WEIGHT_ABDOMINAL_OBESITY",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssessmentStartRequest(StrictModel):
    expected_case_version: ExpectedVersion


class AssessmentDisputeRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: Literal["DATA_INACCURATE", "CONTEXT_INCOMPLETE", "RESULT_NOT_UNDERSTOOD"]


class HighRiskTaskActionRequest(StrictModel):
    expected_version: ExpectedVersion
    action_code: Literal["CLAIM", "ESCALATE", "REFER", "RESOLVE"]
    contact_outcome_code: Literal["CONTACTED", "UNABLE_TO_CONTACT", "NOT_REQUIRED"] | None = None
    advice_code: Literal[
        "PROMPT_ARTIFICIAL_REVIEW",
        "PROMPT_MEDICAL_CONTACT",
        "PROMPT_EMERGENCY_IF_ACUTE",
        "PROMPT_LOW_GLUCOSE_SAFE_INTAKE",
    ] | None = None
    reason_code: Literal[
        "CLAIMED_FOR_REVIEW",
        "SAFETY_STATE_UNCONFIRMED",
        "REFERRED_TO_MEDICAL_RESPONSIBLE_PERSON",
        "CURRENT_REASSESSMENT_NON_HIGH_RISK",
    ] | None = None
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def evidence_required_for_closure(self):
        if self.action_code in {"REFER", "RESOLVE"} and (
            self.contact_outcome_code is None
            or self.advice_code is None
            or self.reason_code is None
        ):
            raise ValueError("HIGH_RISK_ACTION_INVALID")
        return self


class RuleSetCreateRequest(StrictModel):
    rule_set_code: Literal["CN_ADULT_BASELINE_V1"]
    version_no: int = Field(ge=1, le=2**63 - 1)
    typed_rule_payload: dict
    medical_content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approval_evidence_ref: str = Field(min_length=1, max_length=256)


class VersionRequest(StrictModel):
    expected_version: ExpectedVersion


class RuleReviewRequest(VersionRequest):
    decision: Literal["APPROVE", "NEEDS_CORRECTION"]
    reason_code: str = Field(min_length=1, max_length=64)


class RulePublishRequest(VersionRequest):
    effective_from: AwareDatetime


class RuleGovernanceRequest(VersionRequest):
    reason_code: str = Field(min_length=1, max_length=64)


class ModuleResultDTO(StrictModel):
    module_code: ModuleCode
    risk_level: RiskLevel
    reason_codes: tuple[str, ...]
    evidence_items: tuple[str, ...]
    message_codes: tuple[str, ...]


class InputEvidenceDTO(StrictModel):
    assembly_ref: UuidV7
    profile_revision_ref: UuidV7
    data_as_of: AwareDatetime
    source_types: tuple[Literal["STORE", "REPORT", "APP"], ...]
    watermark_status: Literal["CURRENT", "STALE"]


class AssessmentSummaryDTO(StrictModel):
    assessment_id: UuidV7
    service_case_id: UuidV7
    sequence_no: int = Field(ge=1)
    status: AssessmentStatus
    overall_risk: RiskLevel | None = None
    rule_version: str
    input_snapshot_ref: UuidV7
    supersedes_assessment_id: UuidV7 | None = None
    initiated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    version: ExpectedVersion


class AssessmentDetailDTO(AssessmentSummaryDTO):
    module_results: tuple[ModuleResultDTO, ...]
    input_evidence: InputEvidenceDTO
    dispute_status: Literal["OPEN", "RESOLVED"] | None = None
    high_risk_task_ref: UuidV7 | None = None


class AssessmentPageDTO(StrictModel):
    items: tuple[AssessmentSummaryDTO, ...]
    next_cursor: str | None = None


class BlockingDTO(StrictModel):
    ordinary_plan: bool
    case_completion: bool


class HighRiskTaskDTO(StrictModel):
    task_id: UuidV7
    assessment_id: UuidV7
    service_case_id: UuidV7
    status: HighRiskTaskStatus
    reason_module_codes: tuple[ModuleCode, ...]
    assignee: int | None = None
    due_at: AwareDatetime
    last_action_at: AwareDatetime | None = None
    blocking: BlockingDTO
    version: ExpectedVersion
    created_at: AwareDatetime
    closed_at: AwareDatetime | None = None


class HighRiskTaskPageDTO(StrictModel):
    items: tuple[HighRiskTaskDTO, ...]
    next_cursor: str | None = None


class DisputeDTO(StrictModel):
    dispute_id: UuidV7
    assessment_id: UuidV7
    status: Literal["OPEN", "RESOLVED"]
    reason_code: str
    created_at: AwareDatetime
    version: ExpectedVersion


class RuleSetVersionDTO(StrictModel):
    rule_set_version_id: UuidV7
    version_no: int = Field(ge=1)
    status: Literal["DRAFT", "IN_REVIEW", "NEEDS_CORRECTION", "PUBLISHED", "SUSPENDED", "RETIRED"]
    module_metadata: tuple[ModuleCode, ...]
    author: int
    reviewer: int | None = None
    approval_state: Literal["PENDING", "APPROVED", "NEEDS_CORRECTION"]
    effective_from: AwareDatetime | None = None
    suspended_at: AwareDatetime | None = None
    retired_at: AwareDatetime | None = None
    version: ExpectedVersion


class RuleSetVersionDetailDTO(RuleSetVersionDTO):
    rule_set_code: str
    typed_rule_payload: dict
    approval_evidence_ref: str | None = None


class RuleSetPageDTO(StrictModel):
    items: tuple[RuleSetVersionDTO, ...]
    next_cursor: str | None = None
