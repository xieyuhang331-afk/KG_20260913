from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _uuid_v7(value: UUID) -> UUID:
    if not isinstance(value, UUID):
        raise ValueError("UUID_V7_REQUIRED")
    normalized = UUID(int=value.int)
    if normalized.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return normalized


UuidV7 = Annotated[UUID, AfterValidator(_uuid_v7)]
ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]
StructuredCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
MilestoneCodeValue = Literal["D0", "D7", "D14", "D21", "D28"]
MilestoneStatusValue = Literal["PENDING", "DUE", "COMPLETED", "MISSED", "INVALIDATED"]
CaseStatusValue = Literal[
    "ACTIVE",
    "PAUSED",
    "CLOSING",
    "COMPLETED",
    "WITHDRAWN_BY_USER",
    "TERMINATED_BY_INSTITUTION",
    "TRANSFERRED",
    "UNABLE_TO_CONTACT",
    "SAFETY_TERMINATED",
]
TransferStatusValue = Literal[
    "REQUESTED_BY_USER",
    "NEW_INSTITUTION_REVIEWING",
    "ACCEPTED",
    "OLD_INSTITUTION_CLOSING",
    "USER_SCOPE_CONFIRMED",
    "TRANSFERRED",
    "REJECTED_BY_NEW_INSTITUTION",
    "CANCELLED_BY_USER",
]
ExportStatusValue = Literal[
    "REQUESTED", "GENERATING", "READY", "DOWNLOADED", "EXPIRED", "FAILED", "CANCELLED"
]
DataScope = Literal[
    "PROFILE",
    "REPORT",
    "CANONICAL_FACT",
    "ASSESSMENT",
    "APPROVED_PLAN",
    "MILESTONE",
    "SERVICE_SUMMARY",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MilestoneDTO(StrictModel):
    milestone_id: UuidV7
    service_case_id: UuidV7
    code: MilestoneCodeValue
    window_start: date
    window_end: date
    status: MilestoneStatusValue
    completed_at: AwareDatetime | None = None
    record_summary: dict[str, object] | None = None
    version: ExpectedVersion


class MilestonePageDTO(StrictModel):
    items: tuple[MilestoneDTO, ...]
    next_cursor: str | None = None


class ServiceFulfillmentDTO(StrictModel):
    service_case_id: UuidV7
    lifecycle_status: CaseStatusValue
    risk_flag: Literal["AT_RISK"] | None = None
    active_plan_id: UuidV7
    cycle_anchor_at: AwareDatetime
    current_schedule_version: ExpectedVersion
    milestones: tuple[MilestoneDTO, ...] = Field(min_length=5, max_length=5)
    open_high_risk_count: int = Field(ge=0)
    closing_readiness: Literal[
        "NOT_READY",
        "READY_TO_CLOSE",
        "BLOCKED_BY_HIGH_RISK",
        "BLOCKED_BY_MISSING_MILESTONE",
        "BLOCKED_BY_USER_ACK",
    ]
    version: ExpectedVersion


class ServiceFulfillmentPageDTO(StrictModel):
    items: tuple[ServiceFulfillmentDTO, ...]
    next_cursor: str | None = None


class MilestoneCompleteRequest(StrictModel):
    expected_version: ExpectedVersion
    record_summary: dict[StructuredCode, str]
    evidence_refs: tuple[UuidV7, ...] = Field(default=(), max_length=32)


class CaseTransitionRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: StructuredCode
    note: str | None = Field(default=None, max_length=500)


class ContactEvidence(StrictModel):
    channel: Literal["PHONE", "SMS", "APP", "PROXY"]
    attempted_at: AwareDatetime
    result_code: StructuredCode


class UnableToContactRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: StructuredCode
    contact_evidence: tuple[ContactEvidence, ...] = Field(min_length=2, max_length=8)
    institution_approved: Literal[True]

    @model_validator(mode="after")
    def distinct_channels(self):
        if len({item.channel for item in self.contact_evidence}) < 2:
            raise ValueError("TWO_CONTACT_CHANNELS_REQUIRED")
        return self


class SafetyTerminateRequest(StrictModel):
    expected_version: ExpectedVersion
    safety_reason: StructuredCode
    evidence_refs: tuple[UuidV7, ...] = Field(min_length=1, max_length=16)
    institution_approved: Literal[True]


class ClosingAssessmentCreateRequest(StrictModel):
    expected_version: ExpectedVersion
    assessment_id: UuidV7
    final_retest_evidence: tuple[StructuredCode, ...] = Field(min_length=1, max_length=32)


class ClosingAssessmentDTO(StrictModel):
    closing_assessment_id: UuidV7
    service_case_id: UuidV7
    assessment_id: UuidV7
    final_retest_evidence: tuple[str, ...]
    created_at: AwareDatetime
    version: ExpectedVersion


class ServiceSummaryCreateRequest(StrictModel):
    expected_version: ExpectedVersion
    assessment_id: UuidV7
    final_retest_evidence: tuple[StructuredCode, ...] = Field(min_length=1, max_length=32)
    milestone_outcomes: dict[MilestoneCodeValue, StructuredCode]
    safety_follow_up: tuple[StructuredCode, ...] = Field(min_length=1, max_length=16)
    next_step: tuple[StructuredCode, ...] = Field(min_length=1, max_length=16)


class ServiceSummaryDTO(StrictModel):
    summary_id: UuidV7
    service_case_id: UuidV7
    assessment_id: UuidV7
    final_retest_evidence: tuple[str, ...]
    milestone_outcomes: dict[str, str]
    safety_follow_up: tuple[str, ...]
    next_step: tuple[str, ...]
    created_at: AwareDatetime
    viewed_at: AwareDatetime | None = None
    version: ExpectedVersion


class SummaryAcknowledgeRequest(StrictModel):
    expected_version: ExpectedVersion


class SummaryAcknowledgementDTO(StrictModel):
    acknowledgement_id: UuidV7
    summary_id: UuidV7
    viewed_at: AwareDatetime
    version: ExpectedVersion


class TransferCreateRequest(StrictModel):
    target_tenant_id: UuidV7
    requested_scope: tuple[DataScope, ...] = Field(min_length=1, max_length=7)
    expected_version: ExpectedVersion

    @model_validator(mode="after")
    def unique_scope(self):
        if len(set(self.requested_scope)) != len(self.requested_scope):
            raise ValueError("DUPLICATE_SCOPE")
        return self


class TransferDecisionRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: StructuredCode
    service_label: StructuredCode | None = None


class TransferSourceCloseRequest(StrictModel):
    expected_version: ExpectedVersion
    summary_id: UuidV7
    risk_disposition: StructuredCode


class TransferScopeConfirmRequest(StrictModel):
    expected_version: ExpectedVersion
    exact_scope: tuple[DataScope, ...] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def unique_scope(self):
        if len(set(self.exact_scope)) != len(self.exact_scope):
            raise ValueError("DUPLICATE_SCOPE")
        return self


class TransferDTO(StrictModel):
    transfer_id: UuidV7
    source_service_case_id: UuidV7
    source_tenant_id: UuidV7
    target_tenant_id: UuidV7
    status: TransferStatusValue
    requested_scope: tuple[DataScope, ...]
    target_decision: str | None = None
    source_closure_status: str | None = None
    scope_confirmed_at: AwareDatetime | None = None
    transferred_at: AwareDatetime | None = None
    version: ExpectedVersion


class TransferPageDTO(StrictModel):
    items: tuple[TransferDTO, ...]
    next_cursor: str | None = None


class DataExportCreateRequest(StrictModel):
    requested_scope: tuple[DataScope, ...] = Field(min_length=1, max_length=7)
    reason: StructuredCode

    @model_validator(mode="after")
    def unique_scope(self):
        if len(set(self.requested_scope)) != len(self.requested_scope):
            raise ValueError("DUPLICATE_SCOPE")
        return self


class DataExportDTO(StrictModel):
    export_id: UuidV7
    subject_member_id: UuidV7
    status: ExportStatusValue
    requested_scope: tuple[DataScope, ...]
    requested_at: AwareDatetime
    ready_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    downloaded_at: AwareDatetime | None = None
    version: ExpectedVersion


class DataExportCancelRequest(StrictModel):
    expected_version: ExpectedVersion


class DownloadAccessRequest(StrictModel):
    reason: StructuredCode


class OneTimeDownloadDTO(StrictModel):
    access_token: str = Field(min_length=32, max_length=512)
    expires_at: AwareDatetime
    filename: str = Field(min_length=1, max_length=128)
    content_type: Literal["application/zip"]
