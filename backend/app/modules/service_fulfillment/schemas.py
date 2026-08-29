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
    "PLAN_PENDING",
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
MajorProxyPermission = Literal[
    "PLAN_DECISION",
    "SERVICE_WITHDRAW",
    "SERVICE_TRANSFER",
    "PERSONAL_DATA_EXPORT",
]
ContinuationHandoffStatus = Literal[
    "PENDING_TARGET_ENROLLMENT",
    "ENROLLMENT_CREATED",
    "ASSIGNMENT_PENDING",
    "CONTINUATION_CASE_LINKED",
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
    active_plan_id: UuidV7 | None = None
    cycle_anchor_at: AwareDatetime | None = None
    current_schedule_version: ExpectedVersion | None = None
    milestones: tuple[MilestoneDTO, ...] = Field(max_length=5)
    open_high_risk_count: int = Field(ge=0)
    closing_readiness: Literal[
        "NOT_READY",
        "READY_TO_CLOSE",
        "BLOCKED_BY_HIGH_RISK",
        "BLOCKED_BY_MISSING_MILESTONE",
        "BLOCKED_BY_USER_ACK",
    ]
    version: ExpectedVersion

    @model_validator(mode="after")
    def cycle_shape_matches_lifecycle(self):
        if self.lifecycle_status == "PLAN_PENDING":
            if (
                self.active_plan_id is not None
                or self.cycle_anchor_at is not None
                or self.current_schedule_version is not None
                or self.milestones
            ):
                raise ValueError("PLAN_PENDING_CYCLE_FORBIDDEN")
        elif (
            self.active_plan_id is None
            or self.cycle_anchor_at is None
            or self.current_schedule_version is None
            or len(self.milestones) != 5
        ):
            raise ValueError("ACTIVE_CYCLE_REQUIRED")
        return self


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


class ContinuationCaseLinkRequest(StrictModel):
    new_service_case_id: UuidV7
    expected_version: ExpectedVersion


class ContinuationHandoffDTO(StrictModel):
    handoff_id: UuidV7
    transfer_id: UuidV7
    source_service_case_id: UuidV7
    source_tenant_id: UuidV7
    target_tenant_id: UuidV7
    subject_member_id: UuidV7
    authorized_scope: tuple[DataScope, ...]
    status: ContinuationHandoffStatus
    created_at: AwareDatetime
    linked_enrollment_id: UuidV7 | None = None
    linked_service_case_id: UuidV7 | None = None
    linked_at: AwareDatetime | None = None
    version: ExpectedVersion


class ProxyMajorAuthorizationCreateRequest(StrictModel):
    proxy_grant_id: UuidV7
    authorization_document_version_id: UuidV7
    witness_decision_id: UuidV7
    permission_codes: tuple[MajorProxyPermission, ...] = Field(min_length=1, max_length=4)
    valid_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def unique_permissions(self):
        if len(set(self.permission_codes)) != len(self.permission_codes):
            raise ValueError("DUPLICATE_PERMISSION_CODE")
        return self


class ProxyMajorAuthorizationRevokeRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: StructuredCode


class ProxyMajorAuthorizationDTO(StrictModel):
    authorization_id: UuidV7
    proxy_grant_id: UuidV7
    principal_member_id: UuidV7
    proxy_member_id: UuidV7
    authorization_document_version_id: UuidV7
    witness_decision_id: UuidV7
    permission_codes: tuple[MajorProxyPermission, ...]
    granted_by: int
    valid_from: AwareDatetime
    valid_until: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    version: ExpectedVersion


class DataExportCreateRequest(StrictModel):
    subject_member_id: UuidV7 | None = None
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


class DataExportPageDTO(StrictModel):
    items: tuple[DataExportDTO, ...]
    next_cursor: str | None = None


class DataExportCancelRequest(StrictModel):
    expected_version: ExpectedVersion


class DownloadAccessRequest(StrictModel):
    reason: StructuredCode
    expected_version: ExpectedVersion


class OneTimeDownloadDTO(StrictModel):
    access_token: str = Field(min_length=32, max_length=512)
    expires_at: AwareDatetime
    filename: str = Field(min_length=1, max_length=128)
    content_type: Literal["application/zip"]
