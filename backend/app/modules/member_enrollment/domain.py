from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class MemberEnrollmentConflict(ValueError):
    """Stable, non-sensitive Slice 3 domain failure."""


class InvitationAttemptRejected(MemberEnrollmentConflict):
    """A safe rejection whose failed-attempt mutation must be committed."""


class InvitationStatus(StrEnum):
    INVITED = "INVITED"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class EnrollmentStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    IDENTITY_SUBMITTED = "IDENTITY_SUBMITTED"
    INSTITUTION_CHECKED = "INSTITUTION_CHECKED"
    PLATFORM_REVIEWING = "PLATFORM_REVIEWING"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    RESUBMITTED = "RESUBMITTED"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    CONSENT_PENDING = "CONSENT_PENDING"
    THERAPIST_PENDING = "THERAPIST_PENDING"
    CASE_CREATED = "CASE_CREATED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ProxyGrantStatus(StrEnum):
    CONSENT_PENDING = "CONSENT_PENDING"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ConsentStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class AssignmentStatus(StrEnum):
    PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"


class IdentityStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    INSTITUTION_CHECKED = "INSTITUTION_CHECKED"
    PLATFORM_REVIEWING = "PLATFORM_REVIEWING"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    RESUBMITTED = "RESUBMITTED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ConsentDocumentStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


def _require_version(actual: int, expected: int) -> None:
    if type(expected) is not int or expected < 1 or actual != expected:
        raise MemberEnrollmentConflict("VERSION_CONFLICT")


@dataclass(frozen=True, slots=True)
class MemberServiceInvitation:
    invitation_id: UUID
    tenant_id: int
    mode: str
    status: InvitationStatus
    expires_at: datetime
    version: int
    failed_attempts: int = 0
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    def record_failed_attempt(self, *, now: datetime) -> MemberServiceInvitation:
        if self.status is not InvitationStatus.INVITED:
            raise MemberEnrollmentConflict("INVITATION_STATE_CONFLICT")
        attempts = min(5, self.failed_attempts + 1)
        expired = attempts == 5 or now >= self.expires_at
        return replace(
            self,
            failed_attempts=attempts,
            status=InvitationStatus.EXPIRED if expired else self.status,
            version=self.version + 1,
        )

    def accept(self, *, expected_version: int, now: datetime) -> MemberServiceInvitation:
        _require_version(self.version, expected_version)
        if self.failed_attempts >= 5 or self.status is InvitationStatus.EXPIRED:
            raise MemberEnrollmentConflict("INVITATION_EXHAUSTED")
        if self.status is not InvitationStatus.INVITED:
            raise MemberEnrollmentConflict("INVITATION_STATE_CONFLICT")
        if now >= self.expires_at:
            raise MemberEnrollmentConflict("INVITATION_EXPIRED")
        return replace(
            self,
            status=InvitationStatus.ACCEPTED,
            accepted_at=now,
            version=self.version + 1,
        )

    def revoke(self, *, expected_version: int, now: datetime) -> MemberServiceInvitation:
        _require_version(self.version, expected_version)
        if self.status is not InvitationStatus.INVITED:
            raise MemberEnrollmentConflict("INVITATION_STATE_CONFLICT")
        return replace(
            self,
            status=InvitationStatus.REVOKED,
            revoked_at=now,
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class ProxyGrant:
    grant_id: UUID
    enrollment_id: UUID
    principal_member_id: UUID
    proxy_member_id: UUID
    slot_no: int
    status: ProxyGrantStatus
    version: int
    witness_decision_id: UUID | None = None
    authorization_document_version_id: UUID | None = None
    valid_from: datetime | None = None
    revoked_at: datetime | None = None

    @classmethod
    def create_pending(
        cls,
        *,
        grant_id: UUID,
        enrollment_id: UUID,
        principal_member_id: UUID,
        proxy_member_id: UUID,
        slot_no: int,
    ) -> ProxyGrant:
        if type(slot_no) is not int or slot_no not in {1, 2}:
            raise MemberEnrollmentConflict("PROXY_SLOT_INVALID")
        if principal_member_id == proxy_member_id:
            raise MemberEnrollmentConflict("PROXY_PRINCIPAL_CONFLICT")
        return cls(
            grant_id=grant_id,
            enrollment_id=enrollment_id,
            principal_member_id=principal_member_id,
            proxy_member_id=proxy_member_id,
            slot_no=slot_no,
            status=ProxyGrantStatus.CONSENT_PENDING,
            version=1,
        )

    def bind_witness(self, *, witness_decision_id: UUID, expected_version: int) -> ProxyGrant:
        _require_version(self.version, expected_version)
        if self.status is not ProxyGrantStatus.CONSENT_PENDING:
            raise MemberEnrollmentConflict("PROXY_STATE_CONFLICT")
        return replace(
            self,
            witness_decision_id=witness_decision_id,
            version=self.version + 1,
        )

    def activate(
        self,
        *,
        witness_decision_id: UUID | None,
        authorization_document_version_id: UUID | None,
        expected_version: int,
        now: datetime,
    ) -> ProxyGrant:
        _require_version(self.version, expected_version)
        if self.status is not ProxyGrantStatus.CONSENT_PENDING:
            raise MemberEnrollmentConflict("PROXY_STATE_CONFLICT")
        witness = self.witness_decision_id or witness_decision_id
        if witness is None:
            raise MemberEnrollmentConflict("PROXY_WITNESS_REQUIRED")
        if authorization_document_version_id is None:
            raise MemberEnrollmentConflict("PROXY_AUTHORIZATION_REQUIRED")
        return replace(
            self,
            status=ProxyGrantStatus.ACTIVE,
            witness_decision_id=witness,
            authorization_document_version_id=authorization_document_version_id,
            valid_from=now,
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    consent_record_id: UUID
    status: ConsentStatus
    version: int
    withdrawn_at: datetime | None = None

    def withdraw(self, *, expected_version: int, now: datetime) -> ConsentRecord:
        _require_version(self.version, expected_version)
        if self.status is not ConsentStatus.ACCEPTED:
            raise MemberEnrollmentConflict("CONSENT_STATE_CONFLICT")
        return replace(
            self,
            status=ConsentStatus.WITHDRAWN,
            withdrawn_at=now,
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class PrimaryTherapistAssignment:
    assignment_id: UUID
    status: AssignmentStatus
    version: int
    decided_at: datetime | None = None

    def accept(
        self,
        *,
        expected_version: int,
        active_case_count: int,
        capacity_limit: int,
        now: datetime,
    ) -> PrimaryTherapistAssignment:
        _require_version(self.version, expected_version)
        if self.status is not AssignmentStatus.PENDING_ACCEPTANCE:
            raise MemberEnrollmentConflict("ASSIGNMENT_STATE_CONFLICT")
        if active_case_count >= capacity_limit:
            raise MemberEnrollmentConflict("THERAPIST_CAPACITY_REACHED")
        return replace(
            self,
            status=AssignmentStatus.ACCEPTED,
            decided_at=now,
            version=self.version + 1,
        )


@dataclass(frozen=True, slots=True)
class IdentityVerification:
    verification_id: UUID
    current_revision_id: UUID
    status: IdentityStatus
    version: int

    def institution_decide(
        self, *, revision_id: UUID, decision: str, expected_version: int
    ) -> IdentityVerification:
        _require_version(self.version, expected_version)
        if revision_id != self.current_revision_id or self.status not in {
            IdentityStatus.SUBMITTED,
            IdentityStatus.RESUBMITTED,
        }:
            raise MemberEnrollmentConflict("IDENTITY_REVISION_STALE")
        target = {
            "CHECKED": IdentityStatus.INSTITUTION_CHECKED,
            "NEEDS_CORRECTION": IdentityStatus.NEEDS_CORRECTION,
            "REJECTED": IdentityStatus.REJECTED,
        }.get(decision)
        if target is None:
            raise MemberEnrollmentConflict("IDENTITY_DECISION_INVALID")
        return replace(self, status=target, version=self.version + 1)

    def claim(self, *, expected_version: int) -> IdentityVerification:
        _require_version(self.version, expected_version)
        if self.status is not IdentityStatus.INSTITUTION_CHECKED:
            raise MemberEnrollmentConflict("IDENTITY_STATE_CONFLICT")
        return replace(
            self,
            status=IdentityStatus.PLATFORM_REVIEWING,
            version=self.version + 1,
        )

    def platform_decide(
        self, *, revision_id: UUID, decision: str, expected_version: int
    ) -> IdentityVerification:
        _require_version(self.version, expected_version)
        if self.status is not IdentityStatus.PLATFORM_REVIEWING:
            raise MemberEnrollmentConflict("IDENTITY_STATE_CONFLICT")
        if revision_id != self.current_revision_id:
            raise MemberEnrollmentConflict("IDENTITY_REVISION_STALE")
        target = {
            "APPROVED": IdentityStatus.VERIFIED,
            "NEEDS_CORRECTION": IdentityStatus.NEEDS_CORRECTION,
            "REJECTED": IdentityStatus.REJECTED,
        }.get(decision)
        if target is None:
            raise MemberEnrollmentConflict("IDENTITY_DECISION_INVALID")
        return replace(self, status=target, version=self.version + 1)


@dataclass(frozen=True, slots=True)
class ConsentDocument:
    document_version_id: UUID
    status: ConsentDocumentStatus
    version: int
    effective_at: datetime | None = None
    retired_at: datetime | None = None

    def publish(self, *, expected_version: int, now: datetime, effective_at: datetime) -> ConsentDocument:
        _require_version(self.version, expected_version)
        if self.status is not ConsentDocumentStatus.DRAFT or effective_at > now:
            raise MemberEnrollmentConflict("CONSENT_VERSION_CONFLICT")
        return replace(
            self,
            status=ConsentDocumentStatus.PUBLISHED,
            effective_at=effective_at,
            version=self.version + 1,
        )

    def retire(self, *, expected_version: int, now: datetime) -> ConsentDocument:
        _require_version(self.version, expected_version)
        if self.status is not ConsentDocumentStatus.PUBLISHED:
            raise MemberEnrollmentConflict("CONSENT_STATE_CONFLICT")
        return replace(
            self,
            status=ConsentDocumentStatus.RETIRED,
            retired_at=now,
            version=self.version + 1,
        )
