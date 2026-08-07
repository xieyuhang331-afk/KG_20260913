from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import RFC_4122, UUID

from app.core.uuid_generator import UuidGenerator
from app.modules.member.application.registration_eligibility import (
    AccountClass,
    EligibilityDecision,
    EligibilityReason,
    RegistrationEligibilityPolicy,
    TrustedRegistrationEligibilityFacts,
    VerificationOutcome,
)


class RegistrationEligibilityEvidenceError(RuntimeError):
    pass


class InvalidRegistrationEligibilityEvidence(
    RegistrationEligibilityEvidenceError
):
    pass


class RegistrationEligibilityEvidenceConflict(
    RegistrationEligibilityEvidenceError
):
    pass


class RegistrationEligibilityEvidenceNotFound(
    RegistrationEligibilityEvidenceError
):
    pass


class RegistrationEligibilityEvidenceUnavailable(
    RegistrationEligibilityEvidenceError
):
    pass


class RegistrationEligibilityEvidenceInconsistent(
    RegistrationEligibilityEvidenceError
):
    pass


def _require_uuid7(value: object, field_name: str) -> UUID:
    if (
        type(value) is not UUID
        or value.version != 7
        or value.variant != RFC_4122
    ):
        raise InvalidRegistrationEligibilityEvidence(
            f"{field_name} must be a standard RFC UUID version 7"
        )
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise InvalidRegistrationEligibilityEvidence(
            f"{field_name} must be a positive built-in int"
        )
    return value


def _require_utc(value: object, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise InvalidRegistrationEligibilityEvidence(
            f"{field_name} must be an aware UTC datetime"
        )
    return value


def _require_token(value: object, field_name: str, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise InvalidRegistrationEligibilityEvidence(
            f"{field_name} is invalid"
        )
    return value


@dataclass(frozen=True, slots=True)
class IdentityVerificationEvidence:
    decision_ref: UUID
    user_ref: int
    facts_version: int
    verification_epoch: int
    outcome: VerificationOutcome
    evidence_digest: str
    actor_type: str
    actor_ref: str
    decided_at: datetime
    supersedes_ref: UUID | None = None

    def __post_init__(self) -> None:
        _require_uuid7(self.decision_ref, "decision_ref")
        _require_positive_int(self.user_ref, "user_ref")
        _require_positive_int(self.facts_version, "facts_version")
        _require_positive_int(self.verification_epoch, "verification_epoch")
        if type(self.outcome) is not VerificationOutcome:
            raise InvalidRegistrationEligibilityEvidence(
                "outcome must be a VerificationOutcome"
            )
        _require_token(self.evidence_digest, "evidence_digest", 128)
        _require_token(self.actor_type, "actor_type", 32)
        _require_token(self.actor_ref, "actor_ref", 64)
        _require_utc(self.decided_at, "decided_at")
        if self.supersedes_ref is not None:
            _require_uuid7(self.supersedes_ref, "supersedes_ref")
            if self.supersedes_ref == self.decision_ref:
                raise InvalidRegistrationEligibilityEvidence(
                    "verification evidence cannot supersede itself"
                )


@dataclass(frozen=True, slots=True)
class UserAccountClassificationEvidence:
    decision_ref: UUID
    user_ref: int
    facts_version: int
    classification_version: int
    account_class: AccountClass
    decision_basis_code: str
    decided_at: datetime
    supersedes_ref: UUID | None = None

    def __post_init__(self) -> None:
        _require_uuid7(self.decision_ref, "decision_ref")
        _require_positive_int(self.user_ref, "user_ref")
        _require_positive_int(self.facts_version, "facts_version")
        _require_positive_int(
            self.classification_version, "classification_version"
        )
        if type(self.account_class) is not AccountClass:
            raise InvalidRegistrationEligibilityEvidence(
                "account_class must be an AccountClass"
            )
        _require_token(
            self.decision_basis_code, "decision_basis_code", 64
        )
        _require_utc(self.decided_at, "decided_at")
        if self.supersedes_ref is not None:
            _require_uuid7(self.supersedes_ref, "supersedes_ref")
            if self.supersedes_ref == self.decision_ref:
                raise InvalidRegistrationEligibilityEvidence(
                    "classification evidence cannot supersede itself"
                )


@dataclass(frozen=True, slots=True)
class RegistrationEligibilityDecisionEvidence:
    decision_ref: UUID
    user_ref: int
    facts_version: int
    verification_decision_ref: UUID
    classification_decision_ref: UUID
    policy_version: str
    facts_digest: str
    p1_projection_digest: str
    decision: EligibilityDecision
    reason: EligibilityReason
    decided_at: datetime

    def __post_init__(self) -> None:
        _require_uuid7(self.decision_ref, "decision_ref")
        _require_positive_int(self.user_ref, "user_ref")
        _require_positive_int(self.facts_version, "facts_version")
        _require_uuid7(
            self.verification_decision_ref, "verification_decision_ref"
        )
        _require_uuid7(
            self.classification_decision_ref,
            "classification_decision_ref",
        )
        _require_token(self.policy_version, "policy_version", 32)
        _require_token(self.facts_digest, "facts_digest", 128)
        _require_sha256_digest(
            self.p1_projection_digest, "p1_projection_digest"
        )
        if type(self.decision) is not EligibilityDecision:
            raise InvalidRegistrationEligibilityEvidence(
                "decision must be an EligibilityDecision"
            )
        if type(self.reason) is not EligibilityReason:
            raise InvalidRegistrationEligibilityEvidence(
                "reason must be an EligibilityReason"
            )
        _require_utc(self.decided_at, "decided_at")


@dataclass(frozen=True, slots=True)
class CurrentEligibilityDecisionProof:
    decision_ref: UUID
    user_ref: int
    facts_version: int
    verification_decision_ref: UUID
    classification_decision_ref: UUID
    policy_version: str
    facts_digest: str
    p1_projection_digest: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class RegistrationEligibilityFactsSnapshot:
    facts: TrustedRegistrationEligibilityFacts
    p1_projection_digest: str

    def __post_init__(self) -> None:
        if type(self.facts) is not TrustedRegistrationEligibilityFacts:
            raise InvalidRegistrationEligibilityEvidence(
                "facts must be TrustedRegistrationEligibilityFacts"
            )
        _require_sha256_digest(
            self.p1_projection_digest, "p1_projection_digest"
        )


def build_p1_projection_digest(
    *,
    user_ref: int,
    role: str | None,
    status: str | None,
    verify_status: str | None,
    updated_at: datetime | None,
) -> str:
    _require_positive_int(user_ref, "user_ref")
    if updated_at is not None:
        _require_utc(updated_at, "updated_at")
    payload = {
        "role": role,
        "status": status,
        "updated_at": (
            None
            if updated_at is None
            else updated_at.astimezone(timezone.utc).isoformat(
                timespec="microseconds"
            )
        ),
        "user_ref": user_ref,
        "verify_status": verify_status,
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_sha256_digest(value: object, field_name: str) -> str:
    _require_token(value, field_name, 64)
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise InvalidRegistrationEligibilityEvidence(
            f"{field_name} must be a lowercase SHA-256 digest"
        )
    return value


class RegistrationEligibilityFactsReader(Protocol):
    async def get_current(
        self, user_ref: int
    ) -> RegistrationEligibilityFactsSnapshot: ...


class RegistrationEligibilityEvidenceStore(Protocol):
    async def get_decision_by_key(
        self, *, user_ref: int, facts_version: int, policy_version: str
    ) -> RegistrationEligibilityDecisionEvidence | None: ...

    async def add_decision(
        self, evidence: RegistrationEligibilityDecisionEvidence
    ) -> None: ...


class EligibilityDecisionEvidenceReader(Protocol):
    async def get_current_eligible(
        self, *, decision_ref: UUID, user_ref: int
    ) -> CurrentEligibilityDecisionProof: ...


class RegistrationEligibilityDecisionService:
    def __init__(
        self,
        *,
        facts_reader: RegistrationEligibilityFactsReader,
        evidence_store: RegistrationEligibilityEvidenceStore,
        uuid_generator: UuidGenerator,
        clock,
    ) -> None:
        self._facts_reader = facts_reader
        self._evidence_store = evidence_store
        self._uuid_generator = uuid_generator
        self._clock = clock

    async def decide(
        self, *, user_ref: int, policy_version: str
    ) -> RegistrationEligibilityDecisionEvidence:
        _require_positive_int(user_ref, "user_ref")
        _require_token(policy_version, "policy_version", 32)
        snapshot = await self._facts_reader.get_current(user_ref)
        facts = snapshot.facts
        if (
            facts.user_ref != user_ref
            or facts.verification_subject_user_ref != user_ref
            or facts.classification_subject_user_ref != user_ref
        ):
            raise RegistrationEligibilityEvidenceInconsistent(
                "registration eligibility facts do not match the requested subject"
            )
        result = RegistrationEligibilityPolicy.evaluate(facts)

        existing = await self._evidence_store.get_decision_by_key(
            user_ref=user_ref,
            facts_version=facts.facts_version,
            policy_version=policy_version,
        )
        if existing is not None:
            if (
                existing.p1_projection_digest
                != snapshot.p1_projection_digest
                or existing.verification_decision_ref
                != facts.verification_decision_ref
                or existing.classification_decision_ref
                != facts.classification_decision_ref
                or existing.decision is not result.decision
                or existing.reason is not result.reason
            ):
                raise RegistrationEligibilityEvidenceInconsistent(
                    "canonical eligibility evidence does not match current facts"
                )
            return existing

        verification_ref = facts.verification_decision_ref
        classification_ref = facts.classification_decision_ref
        if verification_ref is None or classification_ref is None:
            raise RegistrationEligibilityEvidenceInconsistent(
                "eligibility facts are missing decision references"
            )

        evidence = RegistrationEligibilityDecisionEvidence(
            decision_ref=self._uuid_generator.generate(),
            user_ref=user_ref,
            facts_version=facts.facts_version,
            verification_decision_ref=verification_ref,
            classification_decision_ref=classification_ref,
            policy_version=policy_version,
            facts_digest=hashlib.sha256(repr(facts).encode("utf-8")).hexdigest(),
            p1_projection_digest=snapshot.p1_projection_digest,
            decision=result.decision,
            reason=result.reason,
            decided_at=self._clock(),
        )
        await self._evidence_store.add_decision(evidence)
        return evidence
