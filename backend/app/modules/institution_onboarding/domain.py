from __future__ import annotations

import base64
import hashlib
import hmac
import struct
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID


class OnboardingConflict(ValueError):
    pass


class OnboardingForbidden(ValueError):
    pass


class InvitationStatus(StrEnum):
    ISSUED = "ISSUED"
    ACTIVATED = "ACTIVATED"
    REVOKED = "REVOKED"


class ApplicationStatus(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"


_FROZEN_FIELDS = {
    "institution_name",
    "institution_type",
    "applicant_phone",
    "pilot_batch_code",
    "administrative_region_id",
}
_DRAFT_REQUIRED = {
    "credit_code_digest",
    "legal_representative_name",
    "registered_address",
    "service_address",
    "contact_name",
    "contact_phone_digest",
    "contact_email",
    "service_tags",
}
CORRECTION_FIELD_TO_DRAFT = {
    "credit_code": "credit_code_digest",
    "legal_representative_name": "legal_representative_name",
    "registered_address": "registered_address",
    "service_address": "service_address",
    "contact_name": "contact_name",
    "contact_phone": "contact_phone_digest",
    "contact_email": "contact_email",
    "service_tags": "service_tags",
}
LICENSE_CORRECTION_FIELDS = {
    "business_license",
    "medical_institution_license",
}


@dataclass(slots=True)
class InstitutionInvitation:
    invitation_id: UUID
    institution_name: str
    institution_type: str
    applicant_phone_digest: str
    pilot_batch_code: str
    administrative_region_id: int
    code_digest: str
    expires_at: datetime
    issued_by: int
    issued_at: datetime
    status: InvitationStatus = InvitationStatus.ISSUED
    failed_attempts: int = 0
    version: int = 1

    @classmethod
    def issue(cls, **values: object) -> InstitutionInvitation:
        now = values.pop("now")
        return cls(issued_at=now, **values)  # type: ignore[arg-type]

    def activate(self, *, phone_digest: str, code_digest: str, now: datetime) -> None:
        if self.status is not InvitationStatus.ISSUED:
            raise OnboardingConflict("ONBOARDING_INVITATION_STATE_CONFLICT")
        if now >= self.expires_at:
            raise OnboardingConflict("ONBOARDING_INVITATION_EXPIRED")
        if self.failed_attempts >= 5:
            raise OnboardingConflict("ONBOARDING_INVITATION_ATTEMPT_LIMIT")
        if not hmac.compare_digest(phone_digest, self.applicant_phone_digest):
            self.failed_attempts += 1
            raise OnboardingForbidden("ONBOARDING_INVITATION_PHONE_MISMATCH")
        if not hmac.compare_digest(code_digest, self.code_digest):
            self.failed_attempts += 1
            raise OnboardingForbidden("ONBOARDING_INVITATION_CODE_MISMATCH")
        self.status = InvitationStatus.ACTIVATED
        self.version += 1

    def resend(self, *, code_digest: str, expires_at: datetime, now: datetime) -> None:
        if self.status is not InvitationStatus.ISSUED or now >= self.expires_at:
            raise OnboardingConflict("ONBOARDING_INVITATION_STATE_CONFLICT")
        self.code_digest = code_digest
        self.expires_at = expires_at
        self.failed_attempts = 0
        self.version += 1

    def revoke(self, *, now: datetime) -> None:
        del now
        if self.status is not InvitationStatus.ISSUED:
            raise OnboardingConflict("ONBOARDING_INVITATION_STATE_CONFLICT")
        self.status = InvitationStatus.REVOKED
        self.version += 1


@dataclass(frozen=True, slots=True)
class ApplicationRevision:
    revision_no: int
    snapshot: Mapping[str, object]
    created_at: datetime


@dataclass(slots=True)
class InstitutionApplication:
    application_id: UUID
    invitation_id: UUID
    applicant_user_id: int
    institution_type: str
    created_at: datetime
    status: ApplicationStatus = ApplicationStatus.DRAFT
    version: int = 1
    draft: dict[str, object] = field(default_factory=dict)
    revisions: list[ApplicationRevision] = field(default_factory=list)
    correction_fields: frozenset[str] = frozenset()
    correction_reason_code: str | None = None

    @classmethod
    def create(
        cls,
        *,
        application_id: UUID,
        invitation_id: UUID,
        applicant_user_id: int,
        now: datetime,
        institution_type: str = "HEALTH_STORE",
    ) -> InstitutionApplication:
        if institution_type not in {"HEALTH_STORE", "LICENSED_CLINIC"}:
            raise OnboardingConflict("ONBOARDING_INSTITUTION_TYPE_INVALID")
        return cls(application_id, invitation_id, applicant_user_id, institution_type, now)

    def _expect(self, expected_version: int, *states: ApplicationStatus) -> None:
        if expected_version != self.version:
            raise OnboardingConflict("ONBOARDING_VERSION_CONFLICT")
        if self.status not in states:
            raise OnboardingConflict("ONBOARDING_APPLICATION_STATE_CONFLICT")

    def save_draft(self, values: Mapping[str, object], *, expected_version: int, now: datetime) -> None:
        del now
        self._expect(expected_version, ApplicationStatus.DRAFT)
        if _FROZEN_FIELDS.intersection(values):
            raise OnboardingForbidden("ONBOARDING_FROZEN_FIELD_MUTATION")
        self.draft.update(values)
        self.version += 1

    def _validate_submit(self, clean_file_purposes: tuple[str, ...]) -> None:
        if not _DRAFT_REQUIRED.issubset(self.draft):
            raise OnboardingConflict("ONBOARDING_DRAFT_INCOMPLETE")
        required = {"BUSINESS_LICENSE"}
        if self.institution_type == "LICENSED_CLINIC":
            required.add("MEDICAL_INSTITUTION_LICENSE")
        if not required.issubset(clean_file_purposes):
            raise OnboardingConflict("ONBOARDING_REQUIRED_LICENSE_MISSING")

    def _revision(self, now: datetime) -> ApplicationRevision:
        snapshot = MappingProxyType(dict(self.draft))
        revision = ApplicationRevision(len(self.revisions) + 1, snapshot, now)
        self.revisions.append(revision)
        return revision

    def submit(
        self, *, clean_file_purposes: tuple[str, ...], expected_version: int, now: datetime
    ) -> ApplicationRevision:
        self._expect(expected_version, ApplicationStatus.DRAFT)
        self._validate_submit(clean_file_purposes)
        revision = self._revision(now)
        self.status = ApplicationStatus.SUBMITTED
        self.version += 1
        return revision

    def start_review(self, *, expected_version: int, now: datetime) -> None:
        del now
        self._expect(expected_version, ApplicationStatus.SUBMITTED)
        self.status = ApplicationStatus.UNDER_REVIEW
        self.version += 1

    def request_correction(
        self,
        *,
        fields: tuple[str, ...],
        reason_code: str,
        expected_version: int,
        now: datetime,
    ) -> None:
        del now
        self._expect(expected_version, ApplicationStatus.UNDER_REVIEW)
        if (
            not fields
            or len(set(fields)) != len(fields)
            or not set(fields).issubset(
                set(CORRECTION_FIELD_TO_DRAFT) | LICENSE_CORRECTION_FIELDS
            )
        ):
            raise OnboardingForbidden("ONBOARDING_CORRECTION_FIELD_FORBIDDEN")
        self.correction_fields = frozenset(fields)
        self.correction_reason_code = reason_code
        self.status = ApplicationStatus.NEEDS_CORRECTION
        self.version += 1

    def save_correction(
        self, values: Mapping[str, object], *, expected_version: int, now: datetime
    ) -> None:
        del now
        self._expect(expected_version, ApplicationStatus.NEEDS_CORRECTION)
        permitted = {
            CORRECTION_FIELD_TO_DRAFT[field]
            for field in self.correction_fields
            if field in CORRECTION_FIELD_TO_DRAFT
        }
        if "credit_code" in self.correction_fields:
            permitted.add("credit_code_ciphertext")
        if "contact_phone" in self.correction_fields:
            permitted.add("contact_phone_ciphertext")
        only_license_correction = bool(self.correction_fields) and set(
            self.correction_fields
        ).issubset(LICENSE_CORRECTION_FIELDS)
        if (not values and not only_license_correction) or not set(values).issubset(
            permitted
        ):
            raise OnboardingForbidden("ONBOARDING_CORRECTION_FIELD_FORBIDDEN")
        self.draft.update(values)
        self.version += 1

    def resubmit(
        self, *, clean_file_purposes: tuple[str, ...], expected_version: int, now: datetime
    ) -> ApplicationRevision:
        self._expect(expected_version, ApplicationStatus.NEEDS_CORRECTION)
        self._validate_submit(clean_file_purposes)
        revision = self._revision(now)
        self.status = ApplicationStatus.SUBMITTED
        self.correction_fields = frozenset()
        self.correction_reason_code = None
        self.version += 1
        return revision

    def reject(self, *, reason_code: str, expected_version: int, now: datetime) -> None:
        del reason_code, now
        self._expect(expected_version, ApplicationStatus.UNDER_REVIEW)
        self.status = ApplicationStatus.REJECTED
        self.version += 1

    def approve(self, *, expected_version: int, now: datetime) -> None:
        del now
        self._expect(expected_version, ApplicationStatus.UNDER_REVIEW)
        self.status = ApplicationStatus.APPROVED
        self.version += 1


def generate_totp(secret: str, *, at: datetime, digits: int = 6, period: int = 30) -> str:
    key = base64.b32decode(secret.upper(), casefold=True)
    counter = int(at.timestamp()) // period
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return f"{number:0{digits}d}"


def verify_totp(secret: str, code: str, *, at: datetime, window: int = 1) -> bool:
    if len(code) != 6 or not code.isdigit():
        return False
    for offset in range(-window, window + 1):
        candidate_at = datetime.fromtimestamp(at.timestamp() + offset * 30, tz=at.tzinfo)
        if hmac.compare_digest(generate_totp(secret, at=candidate_at), code):
            return True
    return False
