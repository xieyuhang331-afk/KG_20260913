from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Generic, Literal, TypeVar
import re
import unicodedata
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.therapist_qualification.domain import CORRECTION_FIELDS, SERVICE_TAGS


ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]
ServiceTag = Literal["HYPERTENSION", "GLUCOSE_METABOLISM", "DYSLIPIDEMIA", "OBESITY"]
ProfileStatusValue = Literal[
    "ACTIVATED", "DRAFT", "SUBMITTED", "UNDER_REVIEW", "NEEDS_CORRECTION",
    "RESUBMITTED", "APPROVED_ACTIVE", "SUSPENDED", "EXITED", "REJECTED",
]
InvitationStatusValue = Literal["INVITED", "ACTIVATED", "EXPIRED", "REVOKED"]
ReviewStatusValue = Literal["QUEUED", "UNDER_REVIEW", "DECIDED"]
QualificationOutcomeValue = Literal["SUBMITTED", "APPROVED", "REJECTED", "SUPERSEDED"]
CorrectionField = Literal["real_name", "display_name", "practice_summary", "service_tags"]
ReadinessReason = Literal[
    "COMPLIANCE_SUSPENDED", "INSTITUTION_APPROVAL_SOURCE_INVALID",
    "INSTITUTION_LICENSE_INVALID", "METABOLIC_SCOPE_MISSING",
    "NO_APPROVED_ACTIVE_THERAPIST", "TENANT_NOT_ACTIVE",
]
T = TypeVar("T")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TherapistInvitationCreate(StrictModel):
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    expires_in_minutes: int = Field(ge=5, le=1440)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return re.sub(r"[ \-()]", "", unicodedata.normalize("NFKC", value))


class TherapistInvitationRevoke(StrictModel):
    expected_version: ExpectedVersion
    reason_code: Literal["INVITE_WITHDRAWN", "WRONG_RECIPIENT"]


class TherapistActivate(StrictModel):
    invitation_id: UUID
    phone: str = Field(pattern=r"^1[3-9][0-9]{9}$")
    short_code: str = Field(pattern=r"^[0-9]{6}$")
    password: str = Field(min_length=12, max_length=128)
    totp_secret: str = Field(min_length=16, max_length=128, pattern=r"^[A-Z2-7]+=*$")
    totp_code: str = Field(pattern=r"^[0-9]{6}$")

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: object) -> object:
        return TherapistInvitationCreate.normalize_phone(value)


class TherapistProfileDraft(StrictModel):
    real_name: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=50)
    practice_summary: str = Field(max_length=500)
    service_tags: tuple[ServiceTag, ...] = Field(min_length=1, max_length=4)
    expected_version: ExpectedVersion

    @field_validator("service_tags")
    @classmethod
    def unique_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or not set(value).issubset(SERVICE_TAGS):
            raise ValueError("service_tags must be unique")
        return tuple(sorted(value))

    @field_validator("real_name", mode="before")
    @classmethod
    def normalize_real_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return " ".join(unicodedata.normalize("NFKC", value).split())


class TherapistProfilePatch(StrictModel):
    real_name: str | None = Field(default=None, min_length=1, max_length=50)
    display_name: str | None = Field(default=None, min_length=1, max_length=50)
    practice_summary: str | None = Field(default=None, max_length=500)
    service_tags: tuple[ServiceTag, ...] | None = Field(default=None, min_length=1, max_length=4)

    @field_validator("service_tags")
    @classmethod
    def unique_tags(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        return value if value is None else TherapistProfileDraft.unique_tags(value)

    @field_validator("real_name", mode="before")
    @classmethod
    def normalize_real_name(cls, value: object) -> object:
        return value if value is None else TherapistProfileDraft.normalize_real_name(value)

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("explicit null is not allowed")
        return self


class QualificationInput(StrictModel):
    qualification_type: Literal["METABOLIC_HEALTH_PRACTICE"]
    certificate_no: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    issuer_name: str = Field(min_length=1, max_length=100)
    valid_from: date
    valid_until: date
    attachment_file_ids: tuple[UUID, ...] = Field(min_length=1, max_length=3)

    @field_validator("certificate_no", mode="before")
    @classmethod
    def normalize_certificate(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return unicodedata.normalize("NFKC", value).strip().replace(" ", "").replace("-", "").upper()

    @model_validator(mode="after")
    def validate_contract(self):
        if self.valid_from > self.valid_until:
            raise ValueError("validity range is invalid")
        if len(set(self.attachment_file_ids)) != len(self.attachment_file_ids):
            raise ValueError("attachment ids must be unique")
        self.certificate_no = self.certificate_no.upper()
        return self


class TherapistSubmit(StrictModel):
    expected_version: ExpectedVersion
    qualification: QualificationInput


class TherapistResubmit(StrictModel):
    expected_version: ExpectedVersion
    profile_changes: TherapistProfilePatch
    qualification: QualificationInput
    decision_id: UUID

    @field_validator("profile_changes")
    @classmethod
    def allowed_profile_changes(cls, value: TherapistProfilePatch) -> TherapistProfilePatch:
        if not set(value.model_fields_set).issubset(CORRECTION_FIELDS):
            raise ValueError("profile_changes contains forbidden fields")
        return value


class TherapistRenew(StrictModel):
    expected_version: ExpectedVersion
    predecessor_version_id: UUID
    qualification: QualificationInput


class TherapistRenewalResubmit(StrictModel):
    expected_review_version: ExpectedVersion
    decision_id: UUID
    qualification: QualificationInput


class TherapistReviewDecisionRequest(StrictModel):
    decision: Literal["START_REVIEW", "NEEDS_CORRECTION", "REJECTED", "APPROVED"]
    reason_code: str | None = Field(default=None, max_length=64)
    profile_fields: tuple[Literal["real_name", "display_name", "practice_summary", "service_tags"], ...] = ()
    qualification_targets: tuple[UUID, ...] = ()
    qualification_outcomes: dict[UUID, Literal["APPROVED", "REJECTED"]] = Field(default_factory=dict)
    expected_version: ExpectedVersion

    @model_validator(mode="after")
    def decision_truth_table(self):
        if len(set(self.profile_fields)) != len(self.profile_fields) or len(set(self.qualification_targets)) != len(self.qualification_targets):
            raise ValueError("duplicate correction target")
        corrections = bool(self.profile_fields or self.qualification_targets)
        if self.decision == "START_REVIEW":
            if self.reason_code is not None or corrections or self.qualification_outcomes:
                raise ValueError("invalid start review request")
        elif self.decision == "NEEDS_CORRECTION":
            if not self.reason_code or not corrections or self.qualification_outcomes:
                raise ValueError("invalid correction decision")
        elif self.decision == "REJECTED":
            if not self.reason_code or corrections or not self.qualification_outcomes:
                raise ValueError("invalid rejection decision")
        elif self.reason_code is not None or corrections or not self.qualification_outcomes:
            raise ValueError("invalid approval decision")
        return self


class TherapistStatusRequest(StrictModel):
    expected_version: ExpectedVersion
    reason_code: str = Field(min_length=1, max_length=64)


class TherapistResumeRequest(StrictModel):
    expected_version: ExpectedVersion


class InvitationDTO(StrictModel):
    invitation_id: UUID
    masked_phone: str
    status: InvitationStatusValue
    expires_at: AwareDatetime
    issued_at: AwareDatetime
    activated_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class InvitationCreatedDTO(InvitationDTO):
    short_code: str = Field(pattern=r"^[0-9]{6}$")


class ProfileDTO(StrictModel):
    therapist_id: UUID
    tenant_id: UUID
    display_name: str | None = None
    practice_summary: str | None = None
    status: ProfileStatusValue
    service_tags: tuple[ServiceTag, ...] | None = None
    capacity_limit: Literal[30]
    active_case_count: int = Field(ge=0, le=30)
    qualification_valid_until: date | None = None
    current_revision_no: int = Field(ge=0)
    version: int = Field(ge=1)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def require_materialized_profile_after_activation(self):
        if self.status != "ACTIVATED" and any(
            value is None
            for value in (self.display_name, self.practice_summary, self.service_tags)
        ):
            raise ValueError("materialized profile fields are required")
        return self


class SelfProfileDTO(ProfileDTO):
    real_name: str | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def require_real_name_after_activation(self):
        if self.status != "ACTIVATED" and self.real_name is None:
            raise ValueError("real_name is required")
        return self


class QualificationDTO(StrictModel):
    qualification_version_id: UUID
    qualification_type: Literal["METABOLIC_HEALTH_PRACTICE"]
    masked_certificate_no: str
    issuer_name: str
    valid_from: date
    valid_until: date
    derived_review_status: QualificationOutcomeValue
    attachment_count: int = Field(ge=1, le=3)
    version_no: int = Field(ge=1)


class ReviewItemDTO(StrictModel):
    review_item_id: UUID
    therapist_id: UUID
    revision_id: UUID
    qualification_version_id: UUID | None = None
    review_kind: Literal["INITIAL", "RENEWAL"]
    status: ReviewStatusValue
    created_at: AwareDatetime
    claimed_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class CorrectionDTO(StrictModel):
    decision_id: UUID
    review_item_id: UUID
    profile_fields: tuple[CorrectionField, ...]
    qualification_targets: tuple[UUID, ...]
    reason_code: str = Field(min_length=1, max_length=64)
    review_version: int = Field(ge=1)


class RevisionDTO(StrictModel):
    revision_id: UUID
    revision_no: int = Field(ge=1)
    created_at: AwareDatetime


class MutationProfileDTO(StrictModel):
    therapist_id: UUID
    status: ProfileStatusValue
    revision_id: UUID | None = None
    revision_no: int | None = Field(default=None, ge=1)
    review_item_id: UUID | None = None
    version: int = Field(ge=1)


class ReadinessDTO(StrictModel):
    tenant_id: UUID
    readiness_status: Literal["NOT_READY", "SERVICE_READY"]
    reason_codes: tuple[ReadinessReason, ...]
    qualified_therapist_count: int = Field(ge=0)
    computed_at: AwareDatetime
    evidence_version: int = Field(ge=1)


class EvidenceDTO(ReadinessDTO):
    input_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    result_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class TherapistDetailDTO(StrictModel):
    profile: ProfileDTO
    qualifications: tuple[QualificationDTO, ...]


class SelfTherapistDetailDTO(StrictModel):
    profile: SelfProfileDTO
    qualifications: tuple[QualificationDTO, ...]
    correction: CorrectionDTO | None = None


class ReviewDetailDTO(StrictModel):
    profile: ProfileDTO
    revisions: tuple[RevisionDTO, ...]
    qualifications: tuple[QualificationDTO, ...]
    current_qualification_ids: tuple[UUID, ...]
    review_item: ReviewItemDTO


class ReviewDecisionDTO(StrictModel):
    review_item: ReviewItemDTO
    profile: MutationProfileDTO
    decision_id: UUID | None = None


class PageDTO(StrictModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = Field(default=None, max_length=512)


class SuccessEnvelope(StrictModel, Generic[T]):
    code: Literal[0] = 0
    message: Literal["ok"] = "ok"
    data: T


class ErrorEnvelope(StrictModel):
    code: str
    message: str
