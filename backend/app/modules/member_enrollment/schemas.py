from __future__ import annotations

from datetime import date, datetime
import re
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.modules.member_enrollment.identity_authority import (
    parse_prc_resident_identity_birth_date,
)


T = TypeVar("T")
PHONE_RE = re.compile(r"^1[3-9][0-9]{9}$", re.ASCII)
CODE_RE = re.compile(r"^[0-9]{6}$", re.ASCII)
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-[A-Z]{2}|-[0-9]{3})?$")
SERVICE_TAGS = {
    "HYPERTENSION", "GLUCOSE_METABOLISM", "DYSLIPIDEMIA", "OBESITY"
}
DOCUMENT_TYPES = {
    "USER_AGREEMENT", "PRIVACY_POLICY", "HEALTH_DATA_PROCESSING",
    "INSTITUTION_SERVICE", "NON_MEDICAL_RISK", "PROXY_AUTHORIZATION",
}
PURPOSE_CODES = {
    "ACCOUNT_AND_SERVICE_ONBOARDING", "IDENTITY_VERIFICATION",
    "HEALTH_DATA_PROCESSING", "CARE_SERVICE_DELIVERY",
    "FAMILY_PROXY_AUTHORIZATION",
}


def _uuid_v7(value: UUID) -> UUID:
    if type(value) is not UUID or value.version != 7:
        raise ValueError("UUID_V7_REQUIRED")
    return value


UuidV7 = Annotated[UUID, AfterValidator(_uuid_v7)]
ExpectedVersion = Annotated[int, Field(ge=1, le=2**63 - 1)]
ServiceTag = Literal["HYPERTENSION", "GLUCOSE_METABOLISM", "DYSLIPIDEMIA", "OBESITY"]
DocumentType = Literal[
    "USER_AGREEMENT", "PRIVACY_POLICY", "HEALTH_DATA_PROCESSING",
    "INSTITUTION_SERVICE", "NON_MEDICAL_RISK", "PROXY_AUTHORIZATION",
]
PurposeCode = Literal[
    "ACCOUNT_AND_SERVICE_ONBOARDING", "IDENTITY_VERIFICATION",
    "HEALTH_DATA_PROCESSING", "CARE_SERVICE_DELIVERY",
    "FAMILY_PROXY_AUTHORIZATION",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _phone(value: object) -> str:
    if type(value) is not str or PHONE_RE.fullmatch(value) is None:
        raise ValueError("PHONE_INVALID")
    return value


class CreateMemberInvitationRequest(StrictModel):
    mode: Literal["SELF", "PROXY_ELDER"]
    phone: str

    _validate_phone = field_validator("phone", mode="before")(_phone)


class AcceptEnrollmentRequest(StrictModel):
    invitation_id: UuidV7
    phone: str
    short_code: str

    _validate_phone = field_validator("phone", mode="before")(_phone)

    @field_validator("short_code", mode="before")
    @classmethod
    def validate_code(cls, value: object) -> str:
        if type(value) is not str or CODE_RE.fullmatch(value) is None:
            raise ValueError("SHORT_CODE_INVALID")
        return value


class VersionRequest(StrictModel):
    expected_version: ExpectedVersion


class ReasonedVersionRequest(VersionRequest):
    reason_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")


class InvitationRevokeRequest(VersionRequest):
    reason_code: Literal["DUPLICATE_INVITATION", "WRONG_RECIPIENT", "INSTITUTION_CANCELLED"]


class AssignmentCancelRequest(VersionRequest):
    reason_code: Literal["MEMBER_UNAVAILABLE", "THERAPIST_UNAVAILABLE", "INSTITUTION_CANCELLED"]


class ConsentWithdrawRequest(VersionRequest):
    reason_code: Literal["CONSENT_WITHDRAWN_BY_SUBJECT", "CONSENT_WITHDRAWN_BY_PROXY"]


class ProxyRevokeRequest(VersionRequest):
    reason_code: Literal["PROXY_REQUESTED", "PLATFORM_COMPLIANCE"]


class ConsentRetireRequest(VersionRequest):
    reason_code: Literal["SUPERSEDED_BY_NEW_VERSION", "LEGAL_WITHDRAWAL"]


class AssignmentDeclineRequest(VersionRequest):
    reason_code: Literal["CAPACITY_CONFLICT", "SCOPE_MISMATCH", "THERAPIST_DECLINED"]


class IdentitySubmissionRequest(StrictModel):
    document_type: Literal["PRC_RESIDENT_ID"]
    real_name: str = Field(min_length=2, max_length=50)
    id_number: str
    expected_version: ExpectedVersion

    @field_validator("real_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("REAL_NAME_INVALID")
        return normalized

    @field_validator("id_number")
    @classmethod
    def validate_identity(cls, value: str) -> str:
        parse_prc_resident_identity_birth_date(value)
        return value


class IdentityResubmitRequest(StrictModel):
    document_type: Literal["PRC_RESIDENT_ID"]
    real_name: str | None = Field(default=None, min_length=2, max_length=50)
    id_number: str | None = None
    expected_version: ExpectedVersion

    @model_validator(mode="after")
    def reject_empty_or_null_patch(self):
        fields = self.model_fields_set - {"document_type", "expected_version"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("IDENTITY_PATCH_INVALID")
        return self

    @field_validator("id_number")
    @classmethod
    def validate_identity(cls, value: str | None) -> str | None:
        if value is not None:
            parse_prc_resident_identity_birth_date(value)
        return value


class InstitutionIdentityCheckRequest(StrictModel):
    revision_id: UuidV7
    decision: Literal["CHECKED", "NEEDS_CORRECTION", "REJECTED"]
    reason_code: str | None = Field(default=None, max_length=64)
    correction_fields: tuple[Literal["real_name", "id_number"], ...] = ()
    attestation_code: Literal[
        "OFFLINE_IDENTITY_CHECKED", "PRINCIPAL_PRESENT_AND_AUTHORIZED_PROXY"
    ] | None = None
    expected_version: ExpectedVersion

    @model_validator(mode="after")
    def truth(self):
        unique = len(set(self.correction_fields)) == len(self.correction_fields)
        if not unique:
            raise ValueError("CORRECTION_FIELDS_INVALID")
        if self.decision == "CHECKED":
            if self.reason_code is not None or self.correction_fields or self.attestation_code is None:
                raise ValueError("IDENTITY_CHECK_INVALID")
        elif self.decision == "NEEDS_CORRECTION":
            if self.reason_code not in {
                "IDENTITY_INFORMATION_INCONSISTENT", "IDENTITY_DOCUMENT_INVALID",
                "PROXY_EVIDENCE_INCOMPLETE",
            } or not self.correction_fields or self.attestation_code is not None:
                raise ValueError("IDENTITY_CHECK_INVALID")
        elif self.reason_code not in {"OFFLINE_CHECK_FAILED", "IDENTITY_DOCUMENT_INVALID"} or self.correction_fields or self.attestation_code is not None:
            raise ValueError("IDENTITY_CHECK_INVALID")
        return self


class PlatformIdentityDecisionRequest(StrictModel):
    revision_id: UuidV7
    decision: Literal["APPROVED", "NEEDS_CORRECTION", "REJECTED"]
    reason_code: str | None = Field(default=None, max_length=64)
    correction_fields: tuple[Literal["real_name", "id_number"], ...] = ()
    represented_elder_eligible: bool | None = None
    expected_version: ExpectedVersion

    @model_validator(mode="after")
    def truth(self):
        if len(set(self.correction_fields)) != len(self.correction_fields):
            raise ValueError("CORRECTION_FIELDS_INVALID")
        if self.decision == "APPROVED":
            if self.reason_code is not None or self.correction_fields:
                raise ValueError("PLATFORM_DECISION_INVALID")
        elif self.decision == "NEEDS_CORRECTION":
            if self.reason_code not in {
                "IDENTITY_INFORMATION_INCONSISTENT", "IDENTITY_DOCUMENT_INVALID",
                "PROXY_EVIDENCE_INCOMPLETE",
            } or not self.correction_fields or self.represented_elder_eligible is not None:
                raise ValueError("PLATFORM_DECISION_INVALID")
        elif self.reason_code not in {
            "IDENTITY_DUPLICATE", "IDENTITY_DOCUMENT_INVALID", "PROXY_EVIDENCE_INCOMPLETE"
        } or self.correction_fields or self.represented_elder_eligible is not None:
            raise ValueError("PLATFORM_DECISION_INVALID")
        return self


class CreateAssignmentRequest(VersionRequest):
    therapist_id: UuidV7
    service_scope_tags: tuple[ServiceTag, ...] = Field(min_length=1, max_length=4)

    @field_validator("service_scope_tags")
    @classmethod
    def tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value or not set(value) <= SERVICE_TAGS:
            raise ValueError("SERVICE_SCOPE_INVALID")
        return value


class RecordConsentRequest(VersionRequest):
    document_version_id: UuidV7
    rendition_id: UuidV7
    choice: Literal["ACCEPTED", "DECLINED"]
    purpose_codes: tuple[PurposeCode, ...] = Field(min_length=1, max_length=5)

    @field_validator("purpose_codes")
    @classmethod
    def purposes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if tuple(sorted(set(value))) != value or not set(value) <= PURPOSE_CODES:
            raise ValueError("PURPOSE_CODES_INVALID")
        return value


class PiiAccessRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=256, repr=False)
    reason_code: Literal["PLATFORM_IDENTITY_REVIEW", "DUPLICATE_IDENTITY_INVESTIGATION"]


class ConsentRenditionInput(StrictModel):
    locale: str
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=100_000)

    @field_validator("locale")
    @classmethod
    def locale_contract(cls, value: str) -> str:
        if LOCALE_RE.fullmatch(value) is None:
            raise ValueError("LOCALE_INVALID")
        return value


class CreateConsentDocumentRequest(StrictModel):
    document_type: DocumentType
    semantic_version: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
    requires_reconsent: Literal[True]
    renditions: tuple[ConsentRenditionInput, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def rendition_contract(self):
        locales = tuple(item.locale for item in self.renditions)
        if len(set(locales)) != len(locales) or "zh-CN" not in locales:
            raise ValueError("CONSENT_RENDITION_INCOMPLETE")
        return self


class PublishConsentDocumentRequest(VersionRequest):
    effective_at: AwareDatetime


class CursorQuery(StrictModel):
    cursor: str | None = Field(default=None, max_length=2048)
    limit: int = Field(default=50, ge=1, le=100)


class InvitationListQuery(CursorQuery):
    status: Literal["INVITED", "ACCEPTED", "REVOKED", "EXPIRED"] | None = None


class InstitutionEnrollmentListQuery(CursorQuery):
    status: str | None = Field(default=None, max_length=32)


class FamilyEnrollmentListQuery(CursorQuery):
    pass


class IdentityReviewListQuery(CursorQuery):
    status: str | None = Field(default=None, max_length=24)


class TherapistAssignmentListQuery(CursorQuery):
    status: Literal["PENDING_ACCEPTANCE", "ACCEPTED", "DECLINED", "CANCELLED"] | None = None


class ConsentPresentationQuery(StrictModel):
    locale: str
    document_types: tuple[DocumentType, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def contract(self):
        ConsentRenditionInput.locale_contract(self.locale)
        if len(set(self.document_types)) != len(self.document_types):
            raise ValueError("DOCUMENT_TYPES_INVALID")
        return self


class InvitationDTO(StrictModel):
    invitation_id: UuidV7
    tenant_id: UuidV7
    mode: Literal["SELF", "PROXY_ELDER"]
    phone_masked: str
    expires_at: AwareDatetime
    status: Literal["INVITED", "ACCEPTED", "REVOKED", "EXPIRED"]
    failed_attempts: int = Field(ge=0, le=5)
    issued_at: AwareDatetime
    accepted_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class InvitationSecretDTO(InvitationDTO):
    short_code: str

    _code = field_validator("short_code", mode="before")(AcceptEnrollmentRequest.validate_code.__func__)


class EnrollmentDTO(StrictModel):
    enrollment_id: UuidV7
    tenant_id: UuidV7
    subject_member_id: UuidV7 | None = None
    proxy_member_id: UuidV7 | None = None
    mode: Literal["SELF", "PROXY_ELDER"]
    status: str
    service_scope_tags: tuple[ServiceTag, ...]
    current_identity_verification_id: UuidV7 | None = None
    current_assignment_id: UuidV7 | None = None
    service_case_id: UuidV7 | None = None
    accepted_at: AwareDatetime | None = None
    identity_verified_at: AwareDatetime | None = None
    case_created_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class IdentityStatusDTO(StrictModel):
    verification_id: UuidV7
    enrollment_id: UuidV7
    member_id: UuidV7
    current_revision_id: UuidV7
    status: str
    id_masked: str
    submitted_at: AwareDatetime
    institution_checked_at: AwareDatetime | None = None
    platform_decided_at: AwareDatetime | None = None
    reason_codes: tuple[str, ...] = ()
    version: int = Field(ge=1)


class EnrollmentSummaryDTO(StrictModel):
    enrollment_id: UuidV7
    mode: Literal["SELF", "PROXY_ELDER"]
    status: str
    accepted_at: AwareDatetime | None = None
    identity_verified_at: AwareDatetime | None = None
    case_created_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class EnrollmentDetailDTO(EnrollmentDTO):
    identity: IdentityStatusDTO | None = None
    proxy: "ProxyGrantDTO | None" = None
    consents: tuple["ConsentRecordDTO", ...] = ()
    assignment: "AssignmentDTO | None" = None


class IdentityReviewSummaryDTO(StrictModel):
    review_id: UuidV7
    enrollment_id: UuidV7
    member_id: UuidV7
    mode: Literal["SELF", "PROXY_ELDER"]
    status: str
    id_masked: str
    submitted_at: AwareDatetime
    version: int = Field(ge=1)


class IdentityReviewDetailDTO(IdentityReviewSummaryDTO):
    current_revision_id: UuidV7
    current_revision_no: int = Field(ge=1)
    institution_attestation: str | None = None
    correction_fields: tuple[Literal["real_name", "id_number"], ...] = ()
    proxy_witness_status: str | None = None


class IdentityPiiDTO(StrictModel):
    review_id: UuidV7
    revision_id: UuidV7
    real_name: str
    id_number: str
    birth_date: date
    access_id: UuidV7


class ProxyGrantDTO(StrictModel):
    grant_id: UuidV7
    principal_member_id: UuidV7
    proxy_member_id: UuidV7
    permission_codes: tuple[str, ...]
    authorization_document_version_id: UuidV7 | None = None
    status: str
    valid_from: AwareDatetime | None = None
    valid_until: AwareDatetime | None = None
    version: int = Field(ge=1)


class ConsentRecordDTO(StrictModel):
    consent_record_id: UuidV7
    enrollment_id: UuidV7
    document_type: DocumentType
    document_version_id: UuidV7
    rendition_id: UuidV7
    locale: str
    choice: Literal["ACCEPTED", "DECLINED"]
    status: str
    presented_at: AwareDatetime
    accepted_at: AwareDatetime | None = None
    withdrawn_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class ConsentRenditionDTO(StrictModel):
    rendition_id: UuidV7
    locale: str
    title: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ConsentDocumentDTO(StrictModel):
    document_version_id: UuidV7
    document_type: DocumentType
    semantic_version: str
    status: Literal["DRAFT", "PUBLISHED", "RETIRED"]
    requires_reconsent: Literal[True]
    effective_at: AwareDatetime | None = None
    retired_at: AwareDatetime | None = None
    renditions: tuple[ConsentRenditionDTO, ...]
    version: int = Field(ge=1)


class ConsentPresentationDTO(StrictModel):
    document_version_id: UuidV7
    document_type: DocumentType
    semantic_version: str
    rendition_id: UuidV7
    locale: str
    title: str
    body: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_at: AwareDatetime
    requires_reconsent: Literal[True]
    purpose_codes: tuple[PurposeCode, ...]


class ConsentPresentationListDTO(StrictModel):
    items: tuple[ConsentPresentationDTO, ...]


class AssignmentDTO(StrictModel):
    assignment_id: UuidV7
    enrollment_id: UuidV7
    tenant_id: UuidV7
    subject_member_id: UuidV7
    therapist_id: UuidV7
    status: str
    service_scope_tags: tuple[ServiceTag, ...]
    reason_code: str | None = None
    service_case_id: UuidV7 | None = None
    created_at: AwareDatetime
    decided_at: AwareDatetime | None = None
    version: int = Field(ge=1)


class AssignmentDetailDTO(AssignmentDTO):
    subject_masked_label: str
    therapist_display_name: str


class PreparingCaseDTO(StrictModel):
    case_id: UuidV7
    enrollment_id: UuidV7
    subject_member_id: UuidV7
    tenant_id: UuidV7
    primary_therapist_id: UuidV7
    assignment_id: UuidV7
    status: Literal["PREPARING"]
    service_scope_tags: tuple[ServiceTag, ...]
    created_at: AwareDatetime
    version: int = Field(ge=1)


class PageDTO(StrictModel, Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None = None


class ErrorEnvelopeDTO(StrictModel):
    code: str
    message: str


InvitationPageDTO = PageDTO[InvitationDTO]
EnrollmentSummaryPageDTO = PageDTO[EnrollmentSummaryDTO]
EnrollmentPageDTO = PageDTO[EnrollmentDTO]
IdentityReviewPageDTO = PageDTO[IdentityReviewSummaryDTO]
AssignmentPageDTO = PageDTO[AssignmentDTO]


EnrollmentDetailDTO.model_rebuild()
