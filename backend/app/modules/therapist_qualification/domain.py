from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable
from uuid import UUID


class TherapistConflict(ValueError):
    """A stable, non-sensitive Slice 2 business error."""


class InvitationStatus(StrEnum):
    INVITED = "INVITED"
    ACTIVATED = "ACTIVATED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ProfileStatus(StrEnum):
    ACTIVATED = "ACTIVATED"
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    RESUBMITTED = "RESUBMITTED"
    APPROVED_ACTIVE = "APPROVED_ACTIVE"
    SUSPENDED = "SUSPENDED"
    EXITED = "EXITED"
    REJECTED = "REJECTED"


class ReviewKind(StrEnum):
    INITIAL = "INITIAL"
    RENEWAL = "RENEWAL"


class ReviewStatus(StrEnum):
    QUEUED = "QUEUED"
    UNDER_REVIEW = "UNDER_REVIEW"
    DECIDED = "DECIDED"


class ReviewDecision(StrEnum):
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"


class QualificationOutcome(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ReadinessStatus(StrEnum):
    NOT_READY = "NOT_READY"
    SERVICE_READY = "SERVICE_READY"


SERVICE_TAGS = frozenset(
    {"HYPERTENSION", "GLUCOSE_METABOLISM", "DYSLIPIDEMIA", "OBESITY"}
)
CORRECTION_FIELDS = frozenset(
    {"real_name", "display_name", "practice_summary", "service_tags"}
)
READINESS_REASONS = (
    "COMPLIANCE_SUSPENDED",
    "INSTITUTION_APPROVAL_SOURCE_INVALID",
    "INSTITUTION_LICENSE_INVALID",
    "METABOLIC_SCOPE_MISSING",
    "NO_APPROVED_ACTIVE_THERAPIST",
    "TENANT_NOT_ACTIVE",
)


@dataclass(frozen=True, slots=True)
class TherapistInvitation:
    invitation_id: UUID
    tenant_id: int
    expires_at: datetime
    issued_at: datetime
    status: InvitationStatus = InvitationStatus.INVITED
    failed_attempts: int = 0
    activated_at: datetime | None = None
    revoked_at: datetime | None = None
    version: int = 1

    def activate(self, *, now: datetime) -> TherapistInvitation:
        if self.status is not InvitationStatus.INVITED:
            raise TherapistConflict("THERAPIST_INVITATION_STATE_CONFLICT")
        if self.failed_attempts >= 5:
            raise TherapistConflict("THERAPIST_ACTIVATION_ATTEMPTS_EXHAUSTED")
        if now >= self.expires_at:
            raise TherapistConflict("THERAPIST_INVITATION_EXPIRED")
        return replace(
            self,
            status=InvitationStatus.ACTIVATED,
            activated_at=now,
            version=self.version + 1,
        )

    def record_failed_attempt(self, *, now: datetime) -> TherapistInvitation:
        if self.status is not InvitationStatus.INVITED:
            raise TherapistConflict("THERAPIST_INVITATION_INVALID")
        attempts = min(5, self.failed_attempts + 1)
        status = InvitationStatus.EXPIRED if now >= self.expires_at else self.status
        return replace(self, failed_attempts=attempts, status=status, version=self.version + 1)

    def revoke(self, *, expected_version: int, now: datetime) -> TherapistInvitation:
        _require_version(self.version, expected_version)
        if self.status is not InvitationStatus.INVITED:
            raise TherapistConflict("THERAPIST_INVITATION_STATE_CONFLICT")
        return replace(
            self,
            status=InvitationStatus.REVOKED,
            revoked_at=now,
            version=self.version + 1,
        )

    def expire(self, *, expected_version: int, now: datetime) -> TherapistInvitation:
        _require_version(self.version, expected_version)
        if self.status is not InvitationStatus.INVITED or now < self.expires_at:
            raise TherapistConflict("THERAPIST_INVITATION_NOT_EXPIRABLE")
        return replace(
            self,
            status=InvitationStatus.EXPIRED,
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class QualificationInput:
    qualification_type: str
    valid_from: date
    valid_until: date
    attachment_file_ids: tuple[UUID, ...]

    def validate(self) -> None:
        if self.qualification_type != "METABOLIC_HEALTH_PRACTICE":
            raise TherapistConflict("THERAPIST_QUALIFICATION_TYPE_INVALID")
        if self.valid_from > self.valid_until:
            raise TherapistConflict("THERAPIST_QUALIFICATION_DATES_INVALID")
        if not 1 <= len(self.attachment_file_ids) <= 3:
            raise TherapistConflict("THERAPIST_QUALIFICATION_ATTACHMENTS_INVALID")
        if len(set(self.attachment_file_ids)) != len(self.attachment_file_ids):
            raise TherapistConflict("THERAPIST_QUALIFICATION_ATTACHMENTS_INVALID")


@dataclass(frozen=True, slots=True)
class TherapistProfile:
    therapist_id: UUID
    user_id: int
    tenant_id: int
    status: ProfileStatus
    capacity_limit: int = 30
    active_case_count: int = 0
    current_revision_no: int = 0
    version: int = 1
    qualification_valid_until: date | None = None

    def save_draft(self, *, expected_version: int) -> TherapistProfile:
        _require_version(self.version, expected_version)
        if self.status not in {ProfileStatus.ACTIVATED, ProfileStatus.DRAFT}:
            raise TherapistConflict("THERAPIST_STATE_CONFLICT")
        return replace(self, status=ProfileStatus.DRAFT, version=self.version + 1)

    def submit(self, *, expected_version: int, revision_no: int) -> TherapistProfile:
        _require_version(self.version, expected_version)
        if self.status not in {ProfileStatus.ACTIVATED, ProfileStatus.DRAFT}:
            raise TherapistConflict("THERAPIST_STATE_CONFLICT")
        return replace(
            self,
            status=ProfileStatus.SUBMITTED,
            current_revision_no=revision_no,
            version=self.version + 1,
        )

    def resubmit(self, *, expected_version: int, revision_no: int) -> TherapistProfile:
        _require_version(self.version, expected_version)
        if self.status is not ProfileStatus.NEEDS_CORRECTION:
            raise TherapistConflict("THERAPIST_STATE_CONFLICT")
        return replace(
            self,
            status=ProfileStatus.RESUBMITTED,
            current_revision_no=revision_no,
            version=self.version + 1,
        )

    def approve(self, *, expected_version: int, valid_until: date) -> TherapistProfile:
        _require_version(self.version, expected_version)
        if self.status not in {ProfileStatus.UNDER_REVIEW, ProfileStatus.RESUBMITTED, ProfileStatus.SUSPENDED}:
            raise TherapistConflict("THERAPIST_APPROVAL_PRECONDITION_FAILED")
        return replace(
            self,
            status=ProfileStatus.APPROVED_ACTIVE,
            qualification_valid_until=valid_until,
            version=self.version + 1,
        )

    def suspend(self, *, expected_version: int) -> TherapistProfile:
        _require_version(self.version, expected_version)
        if self.status is not ProfileStatus.APPROVED_ACTIVE:
            raise TherapistConflict("THERAPIST_SUSPEND_STATE_CONFLICT")
        return replace(self, status=ProfileStatus.SUSPENDED, version=self.version + 1)

    def exit(self, *, expected_version: int) -> TherapistProfile:
        _require_version(self.version, expected_version)
        if self.status not in {ProfileStatus.APPROVED_ACTIVE, ProfileStatus.SUSPENDED}:
            raise TherapistConflict("THERAPIST_STATE_CONFLICT")
        if self.active_case_count:
            raise TherapistConflict("THERAPIST_ACTIVE_CASES_REMAIN")
        return replace(self, status=ProfileStatus.EXITED, version=self.version + 1)


@dataclass(frozen=True, slots=True)
class ReadinessInputs:
    tenant_active: bool
    institution_type: str
    valid_license_types: tuple[str, ...]
    approved_therapists: tuple[tuple[date, tuple[str, ...]], ...]
    service_tags: tuple[str, ...]
    compliance_suspended: bool
    institution_approval_source_valid: bool
    institution_license_expiries: tuple[date, ...] = ()


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    status: ReadinessStatus
    reason_codes: tuple[str, ...]
    qualified_therapist_count: int
    next_expiry_at: date | None


def compute_service_readiness(inputs: ReadinessInputs) -> ReadinessResult:
    reasons: list[str] = []
    required_licenses = {"BUSINESS_LICENSE"}
    if inputs.institution_type == "LICENSED_CLINIC":
        required_licenses.add("MEDICAL_INSTITUTION_LICENSE")
    license_valid = (
        inputs.institution_type in {"HEALTH_STORE", "LICENSED_CLINIC"}
        and required_licenses.issubset(set(inputs.valid_license_types))
    )
    institution_tags = SERVICE_TAGS.intersection(inputs.service_tags)
    qualified = tuple(
        expiry
        for expiry, therapist_tags in inputs.approved_therapists
        if institution_tags.intersection(therapist_tags)
    )
    if not inputs.tenant_active:
        reasons.append("TENANT_NOT_ACTIVE")
    if not license_valid:
        reasons.append("INSTITUTION_LICENSE_INVALID")
    if not qualified:
        reasons.append("NO_APPROVED_ACTIVE_THERAPIST")
    if not institution_tags or (inputs.approved_therapists and not qualified):
        reasons.append("METABOLIC_SCOPE_MISSING")
    if inputs.compliance_suspended:
        reasons.append("COMPLIANCE_SUSPENDED")
    if not inputs.institution_approval_source_valid:
        reasons.append("INSTITUTION_APPROVAL_SOURCE_INVALID")
    ordered = tuple(sorted(reasons))
    expiry_boundaries = (
        qualified + inputs.institution_license_expiries
    )
    return ReadinessResult(
        status=ReadinessStatus.NOT_READY if ordered else ReadinessStatus.SERVICE_READY,
        reason_codes=ordered,
        qualified_therapist_count=len(qualified),
        next_expiry_at=min(expiry_boundaries) if expiry_boundaries else None,
    )


def validate_correction_scope(
    *, requested_profile_fields: Iterable[str], allowed_profile_fields: Iterable[str]
) -> tuple[str, ...]:
    requested = tuple(sorted(set(requested_profile_fields)))
    allowed = set(allowed_profile_fields)
    if not requested or not set(requested).issubset(CORRECTION_FIELDS & allowed):
        raise TherapistConflict("THERAPIST_CORRECTION_SCOPE_CONFLICT")
    return requested


def _require_version(actual: int, expected: int) -> None:
    if type(expected) is not int or expected < 1 or actual != expected:
        raise TherapistConflict("THERAPIST_VERSION_CONFLICT")
