from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


MANUAL_REVIEW_AUTHORITY_SOURCE = "manual_review"
PLATFORM_REVIEWER_ROLE = "super_admin"
PLATFORM_ORG_SCOPE = "platform"
VERIFIED_OUTCOME = "verified"


class IdentityVerificationAuthorityError(RuntimeError):
    pass


class InvalidIdentityVerificationAuthorityDecision(
    IdentityVerificationAuthorityError
):
    pass


class IdentityVerificationAuthorityRejected(
    IdentityVerificationAuthorityError
):
    pass


class IdentityVerificationAuthorityUnavailable(
    IdentityVerificationAuthorityError
):
    pass


def _invalid(field_name: str) -> InvalidIdentityVerificationAuthorityDecision:
    return InvalidIdentityVerificationAuthorityDecision(
        f"{field_name} is invalid"
    )


def _require_token(value: object, field_name: str, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise _invalid(field_name)
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise _invalid(field_name)
    return value


def _require_utc(value: object, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise _invalid(field_name)
    return value


def _require_digest(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _invalid("evidence_digest")
    return value


@dataclass(frozen=True, slots=True)
class ManualIdentityReviewAuthorityDecision:
    authority_source: str
    authority_decision_id: str
    reviewer_subject_id: int
    reviewer_role: str
    reviewer_is_active: bool
    reviewer_tenant_scope: int | None
    reviewer_org_scope: str
    user_ref: int
    subject_tenant_id: int | None
    subject_org_id: int | None
    subject_binding_started: bool
    outcome: str
    facts_version: int
    currentness_version: int
    verification_epoch: int
    predecessor_currentness_version: int | None
    predecessor_verification_epoch: int | None
    decided_at: datetime
    evidence_digest: str
    correlation_id: str
    is_current: bool
    revocation_reference: str | None

    def __post_init__(self) -> None:
        if self.authority_source != MANUAL_REVIEW_AUTHORITY_SOURCE:
            raise _invalid("authority_source")
        _require_token(
            self.authority_decision_id,
            "authority_decision_id",
            128,
        )
        _require_positive_int(
            self.reviewer_subject_id, "reviewer_subject_id"
        )
        if self.reviewer_role != PLATFORM_REVIEWER_ROLE:
            raise _invalid("reviewer_role")
        if self.reviewer_is_active is not True:
            raise _invalid("reviewer_is_active")
        if self.reviewer_tenant_scope is not None:
            raise _invalid("reviewer_tenant_scope")
        if self.reviewer_org_scope != PLATFORM_ORG_SCOPE:
            raise _invalid("reviewer_org_scope")
        _require_positive_int(self.user_ref, "user_ref")
        if self.subject_tenant_id is not None:
            raise _invalid("subject_tenant_id")
        if self.subject_org_id is not None:
            raise _invalid("subject_org_id")
        if self.subject_binding_started is not False:
            raise _invalid("subject_binding_started")
        if self.reviewer_subject_id == self.user_ref:
            raise _invalid("reviewer_subject_id")
        if self.outcome != VERIFIED_OUTCOME:
            raise _invalid("outcome")
        _require_positive_int(self.facts_version, "facts_version")
        _require_positive_int(
            self.currentness_version, "currentness_version"
        )
        _require_positive_int(
            self.verification_epoch, "verification_epoch"
        )
        predecessor_versions = (
            self.predecessor_currentness_version,
            self.predecessor_verification_epoch,
        )
        if predecessor_versions == (None, None):
            if self.currentness_version != 1 or self.verification_epoch != 1:
                raise _invalid("predecessor_versions")
        elif None in predecessor_versions:
            raise _invalid("predecessor_versions")
        else:
            predecessor_currentness_version = _require_positive_int(
                self.predecessor_currentness_version,
                "predecessor_currentness_version",
            )
            predecessor_verification_epoch = _require_positive_int(
                self.predecessor_verification_epoch,
                "predecessor_verification_epoch",
            )
            if predecessor_currentness_version >= self.currentness_version:
                raise _invalid("currentness_version")
            if predecessor_verification_epoch >= self.verification_epoch:
                raise _invalid("verification_epoch")
        _require_utc(self.decided_at, "decided_at")
        _require_digest(self.evidence_digest)
        _require_token(self.correlation_id, "correlation_id", 128)
        if self.is_current is not True:
            raise _invalid("is_current")
        if self.revocation_reference is not None:
            raise _invalid("revocation_reference")


@runtime_checkable
class IdentityVerificationAuthorityPort(Protocol):
    async def load_current_decision(
        self, authority_decision_id: str
    ) -> ManualIdentityReviewAuthorityDecision: ...
