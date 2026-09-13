from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo


class DirectOnboardingConflict(ValueError):
    pass


class DirectOnboardingForbidden(ValueError):
    pass


class DirectOnboardingStatus(StrEnum):
    PENDING_ACTIVATION = "PENDING_ACTIVATION"
    ACTIVE_COMPLIANCE_PENDING = "ACTIVE_COMPLIANCE_PENDING"
    COMPLIANCE_UNDER_REVIEW = "COMPLIANCE_UNDER_REVIEW"
    COMPLIANCE_NEEDS_CORRECTION = "COMPLIANCE_NEEDS_CORRECTION"
    COMPLIANCE_APPROVED = "COMPLIANCE_APPROVED"
    REVOKED_BEFORE_ACTIVATION = "REVOKED_BEFORE_ACTIVATION"


class PhoneClaimState(StrEnum):
    PENDING = "PENDING"
    BOUND = "BOUND"
    RELEASED = "RELEASED"


@dataclass(slots=True)
class DirectInstitutionOnboarding:
    onboarding_id: UUID
    tenant_public_id: UUID
    institution_name: str
    institution_type: str
    administrative_region_id: int
    institution_code: str
    created_by: int
    created_at: datetime
    updated_at: datetime
    status: DirectOnboardingStatus = DirectOnboardingStatus.PENDING_ACTIVATION
    compliance_due_at: datetime | None = None
    current_revision_id: UUID | None = None
    version: int = 1

    @classmethod
    def create(cls, *, now: datetime, **values: object) -> DirectInstitutionOnboarding:
        if values.get("institution_type") not in {"HEALTH_STORE", "LICENSED_CLINIC"}:
            raise DirectOnboardingConflict("DIRECT_INSTITUTION_TYPE_INVALID")
        return cls(created_at=now, updated_at=now, **values)  # type: ignore[arg-type]

    def _expect(self, expected_version: int, *states: DirectOnboardingStatus) -> None:
        if expected_version != self.version:
            raise DirectOnboardingConflict("DIRECT_VERSION_CONFLICT")
        if self.status not in states:
            raise DirectOnboardingConflict("DIRECT_STATE_CONFLICT")

    def activate(self, *, now: datetime, expected_version: int) -> None:
        self._expect(expected_version, DirectOnboardingStatus.PENDING_ACTIVATION)
        self.status = DirectOnboardingStatus.ACTIVE_COMPLIANCE_PENDING
        shanghai = ZoneInfo("Asia/Shanghai")
        due_date = now.astimezone(shanghai).date() + timedelta(days=30)
        self.compliance_due_at = datetime.combine(due_date, time.min, shanghai)
        self.updated_at = now
        self.version += 1

    def observe(self, *, now: datetime) -> None:
        # The 30-day overdue Tenant disposition is deliberately product-pending.
        del now


@dataclass(slots=True)
class PhoneClaim:
    claim_id: UUID
    claim_kind: str
    claim_ref: UUID | None
    digest_key_id: str
    digest: str
    state: PhoneClaimState
    user_id: int | None
    created_at: datetime
    updated_at: datetime
    version: int = 1

    @classmethod
    def reserve(cls, *, now: datetime, **values: object) -> PhoneClaim:
        if values.get("claim_kind") not in {
            "EXISTING_USER",
            "MEMBER_ACCOUNT",
            "CONTROLLED_ORG_ADMIN",
            "DIRECT_ORG_ADMIN",
            "THERAPIST_ACCOUNT",
            "ADMIN_HANDOFF",
        }:
            raise DirectOnboardingConflict("PHONE_CLAIM_KIND_INVALID")
        if len(str(values.get("digest", ""))) != 64:
            raise DirectOnboardingConflict("PHONE_CLAIM_DIGEST_INVALID")
        return cls(
            state=PhoneClaimState.PENDING,
            user_id=None,
            created_at=now,
            updated_at=now,
            **values,  # type: ignore[arg-type]
        )

    @property
    def blocks_active_digest(self) -> bool:
        return self.state in {PhoneClaimState.PENDING, PhoneClaimState.BOUND}

    def _expect_pending(self, expected_version: int) -> None:
        if self.state is not PhoneClaimState.PENDING or self.version != expected_version:
            raise DirectOnboardingConflict("PHONE_CLAIM_STATE_CONFLICT")

    def bind(self, *, user_id: int, expected_version: int, now: datetime) -> None:
        self._expect_pending(expected_version)
        self.state = PhoneClaimState.BOUND
        self.user_id = user_id
        self.updated_at = now
        self.version += 1

    def release(self, *, expected_version: int, now: datetime) -> None:
        self._expect_pending(expected_version)
        self.state = PhoneClaimState.RELEASED
        self.updated_at = now
        self.version += 1
