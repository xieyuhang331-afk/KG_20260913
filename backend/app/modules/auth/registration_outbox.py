from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from uuid import RFC_4122, UUID

from app.modules.auth.eligibility_evidence import (
    build_p1_projection_digest,
)
from app.modules.member.application.registration_eligibility import (
    AccountClass,
    EligibilityDecision,
    EligibilityReason,
    RegistrationAccountStatus,
    RegistrationEligibilityPolicy,
    RegistrationUserRole,
    TrustedRegistrationEligibilityFacts,
    VerificationOutcome,
    VerificationStatus,
)


EVENT_TYPE = "identity.registration.verification_verified"
EVENT_SCHEMA_VERSION = 1
SOURCE_SYSTEM = "P1_USER"
POLICY_VERSION = "registration-eligibility-v1"
OUTBOX_LIFECYCLE_STATUSES = frozenset(
    {
        "pending",
        "processing",
        "retry",
        "delivered",
        "review_required",
        "dead_letter",
    }
)


class P1VerificationTransitionError(RuntimeError):
    pass


class InvalidP1VerificationTransition(P1VerificationTransitionError):
    pass


class P1VerificationTransitionNotReady(P1VerificationTransitionError):
    pass


class P1VerificationTransitionInconsistent(P1VerificationTransitionError):
    pass


class P1VerificationTransitionUnavailable(P1VerificationTransitionError):
    pass


class P1VerificationTransitionOutcomeUnknown(
    P1VerificationTransitionError
):
    pass


class P1VerificationTransitionCommitOutcomeUnknown(RuntimeError):
    """Persistence adapter signal: commit may have reached the database."""


def _require_token(value: object, field_name: str, maximum: int) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise InvalidP1VerificationTransition(
            f"{field_name} is invalid"
        )
    return value


def _require_positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise InvalidP1VerificationTransition(
            f"{field_name} must be a positive built-in int"
        )
    return value


def _require_uuid7(value: object, field_name: str) -> UUID:
    if not _is_uuid7(value):
        raise InvalidP1VerificationTransition(
            f"{field_name} must be a standard RFC UUID version 7"
        )
    return value


def _is_uuid7(value: object) -> bool:
    return (
        type(value) is UUID
        and value.version == 7
        and value.variant == RFC_4122
    )


def _require_utc(value: object, field_name: str) -> datetime:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() != timezone.utc.utcoffset(value)
    ):
        raise InvalidP1VerificationTransition(
            f"{field_name} must be an aware UTC datetime"
        )
    return value


@dataclass(frozen=True, slots=True)
class P1VerificationTransitionCommand:
    authority: str
    case_ref: str
    decision_version: int
    target_facts_version: int
    user_ref: int
    verification_epoch: int
    outcome: str
    evidence_digest: str
    actor_type: str
    actor_ref: str
    decided_at: datetime

    def __post_init__(self) -> None:
        _require_token(self.authority, "authority", 32)
        _require_token(self.case_ref, "case_ref", 128)
        _require_positive_int(self.decision_version, "decision_version")
        _require_positive_int(
            self.target_facts_version, "target_facts_version"
        )
        _require_positive_int(self.user_ref, "user_ref")
        _require_positive_int(
            self.verification_epoch, "verification_epoch"
        )
        if self.outcome != VerificationOutcome.VERIFIED.value:
            raise InvalidP1VerificationTransition(
                "outcome must be verified"
            )
        _require_token(self.evidence_digest, "evidence_digest", 128)
        _require_token(self.actor_type, "actor_type", 32)
        _require_token(self.actor_ref, "actor_ref", 64)
        _require_utc(self.decided_at, "decided_at")

    @property
    def authority_decision_key(self) -> str:
        canonical = json.dumps(
            {
                "authority": self.authority,
                "case_ref": self.case_ref,
                "decision_version": self.decision_version,
                "target_facts_version": self.target_facts_version,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def transition_digest(self) -> str:
        canonical = json.dumps(
            {
                "authority_decision_key": self.authority_decision_key,
                "user_ref": self.user_ref,
                "verification_epoch": self.verification_epoch,
                "outcome": self.outcome,
                "evidence_digest": self.evidence_digest,
                "actor_type": self.actor_type,
                "actor_ref": self.actor_ref,
                "decided_at": self.decided_at.isoformat(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class P1VerificationTransitionResult:
    user_ref: int
    verification_decision_ref: UUID
    registration_event_id: UUID
    authority_decision_key: str
    transition_digest: str
    facts_version: int
    status: str
    replayed: bool

    def __post_init__(self) -> None:
        _require_positive_int(self.user_ref, "user_ref")
        _require_uuid7(
            self.verification_decision_ref,
            "verification_decision_ref",
        )
        _require_uuid7(
            self.registration_event_id, "registration_event_id"
        )
        if (
            type(self.authority_decision_key) is not str
            or len(self.authority_decision_key) != 64
        ):
            raise InvalidP1VerificationTransition(
                "authority_decision_key is invalid"
            )
        if (
            type(self.transition_digest) is not str
            or len(self.transition_digest) != 64
        ):
            raise InvalidP1VerificationTransition(
                "transition_digest is invalid"
            )
        _require_positive_int(self.facts_version, "facts_version")
        if self.status != VerificationStatus.VERIFIED.value:
            raise InvalidP1VerificationTransition("status is invalid")
        if type(self.replayed) is not bool:
            raise InvalidP1VerificationTransition("replayed is invalid")


@dataclass(frozen=True, slots=True)
class P1VerificationDecision:
    decision_ref: UUID
    user_ref: int
    facts_version: int
    verification_epoch: int
    outcome: str
    evidence_digest: str
    actor_type: str
    actor_ref: str
    decided_at: datetime
    authority_decision_key: str
    registration_event_id: UUID
    supersedes_ref: UUID | None


@dataclass(frozen=True, slots=True)
class P1RegistrationEligibilityDecision:
    decision_ref: UUID
    user_ref: int
    facts_version: int
    verification_decision_ref: UUID
    classification_decision_ref: UUID
    policy_version: str
    facts_digest: str
    p1_projection_digest: str
    decision: str
    reason: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class P1RegistrationOutboxRecord:
    outbox_record_id: UUID
    event_id: UUID
    semantic_idempotency_key: str
    event_type: str
    event_schema_version: int
    source_system: str
    source_ref: int
    verification_decision_ref: UUID
    authority_decision_key: str
    facts_version: int
    occurred_at: datetime
    status: str = "pending"


@dataclass(frozen=True, slots=True)
class P1VerificationTransitionSnapshot:
    result: P1VerificationTransitionResult
    user_role: str
    user_status: str
    user_verify_status: str
    user_updated_at: datetime
    verification: P1VerificationDecision
    classification: object
    eligibility: P1RegistrationEligibilityDecision
    outbox: P1RegistrationOutboxRecord


@dataclass(frozen=True, slots=True)
class _TransitionIds:
    verification_ref: UUID
    event_id: UUID
    eligibility_ref: UUID
    outbox_record_id: UUID


class P1VerificationTransitionWriter:
    def __init__(self, *, unit_of_work_factory, uuid_generator) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._uuid_generator = uuid_generator

    async def execute(
        self, command: P1VerificationTransitionCommand
    ) -> P1VerificationTransitionResult:
        if type(command) is not P1VerificationTransitionCommand:
            raise InvalidP1VerificationTransition("command is invalid")

        unavailable = False
        try:
            return await self._execute(command)
        except P1VerificationTransitionError:
            raise
        except Exception:
            unavailable = True

        if unavailable:
            raise P1VerificationTransitionUnavailable(
                "P1 verification transition is unavailable"
            )
        raise AssertionError("unreachable")

    async def _execute(
        self, command: P1VerificationTransitionCommand
    ) -> P1VerificationTransitionResult:
        authority_key = command.authority_decision_key
        ids: _TransitionIds | None = None
        commit_unknown = False

        try:
            async with self._unit_of_work_factory() as unit_of_work:
                existing = (
                    await unit_of_work.find_by_authority_decision_key(
                        authority_key
                    )
                )
                if existing is not None:
                    return self._replay(
                        existing, command, authority_key
                    )

                prepared = await self._load_ready(
                    unit_of_work, command
                )
                ids = self._generate_ids()
                return await self._write_new(
                    unit_of_work,
                    command,
                    authority_key,
                    ids,
                    prepared,
                )
        except P1VerificationTransitionCommitOutcomeUnknown:
            commit_unknown = True

        if commit_unknown and ids is not None:
            return await self._confirm_or_retry(
                command, authority_key, ids
            )
        raise AssertionError("unreachable")

    async def _confirm_or_retry(
        self,
        command: P1VerificationTransitionCommand,
        authority_key: str,
        ids: _TransitionIds,
    ) -> P1VerificationTransitionResult:
        second_commit_unknown = False
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                existing = (
                    await unit_of_work.find_by_authority_decision_key(
                        authority_key
                    )
                )
                if existing is not None:
                    return self._replay(
                        existing, command, authority_key
                    )
                prepared = await self._load_ready(
                    unit_of_work, command
                )
                return await self._write_new(
                    unit_of_work,
                    command,
                    authority_key,
                    ids,
                    prepared,
                )
        except P1VerificationTransitionCommitOutcomeUnknown:
            second_commit_unknown = True

        if second_commit_unknown:
            raise P1VerificationTransitionOutcomeUnknown(
                "P1 verification transition outcome is unknown"
            )
        raise AssertionError("unreachable")

    async def _write_new(
        self,
        unit_of_work,
        command: P1VerificationTransitionCommand,
        authority_key: str,
        ids: _TransitionIds,
        prepared,
    ) -> P1VerificationTransitionResult:
        user, classification, predecessor = prepared

        user.verify_status = VerificationStatus.VERIFIED.value
        user.updated_at = command.decided_at
        facts = self._build_facts(
            user=user,
            classification=classification,
            command=command,
            verification_ref=ids.verification_ref,
        )
        eligibility_result = RegistrationEligibilityPolicy.evaluate(facts)
        if (
            eligibility_result.decision is not EligibilityDecision.ELIGIBLE
            or eligibility_result.reason is not EligibilityReason.ELIGIBLE
        ):
            raise P1VerificationTransitionNotReady(
                "P1 verification transition prerequisites are not ready"
            )

        verification = P1VerificationDecision(
            decision_ref=ids.verification_ref,
            user_ref=command.user_ref,
            facts_version=command.target_facts_version,
            verification_epoch=command.verification_epoch,
            outcome=command.outcome,
            evidence_digest=command.evidence_digest,
            actor_type=command.actor_type,
            actor_ref=command.actor_ref,
            decided_at=command.decided_at,
            authority_decision_key=authority_key,
            registration_event_id=ids.event_id,
            supersedes_ref=(
                None
                if predecessor is None
                else predecessor.decision_ref
            ),
        )
        eligibility = P1RegistrationEligibilityDecision(
            decision_ref=ids.eligibility_ref,
            user_ref=command.user_ref,
            facts_version=command.target_facts_version,
            verification_decision_ref=ids.verification_ref,
            classification_decision_ref=classification.decision_ref,
            policy_version=POLICY_VERSION,
            facts_digest=hashlib.sha256(
                repr(facts).encode("utf-8")
            ).hexdigest(),
            p1_projection_digest=build_p1_projection_digest(
                user_ref=command.user_ref,
                role=user.role,
                status=user.status,
                verify_status=user.verify_status,
                updated_at=user.updated_at,
            ),
            decision=eligibility_result.decision.value,
            reason=eligibility_result.reason.value,
            decided_at=command.decided_at,
        )
        outbox = P1RegistrationOutboxRecord(
            outbox_record_id=ids.outbox_record_id,
            event_id=ids.event_id,
            semantic_idempotency_key=self._semantic_key(
                command.user_ref, authority_key
            ),
            event_type=EVENT_TYPE,
            event_schema_version=EVENT_SCHEMA_VERSION,
            source_system=SOURCE_SYSTEM,
            source_ref=command.user_ref,
            verification_decision_ref=ids.verification_ref,
            authority_decision_key=authority_key,
            facts_version=command.target_facts_version,
            occurred_at=command.decided_at,
        )

        await unit_of_work.add_verification_decision(verification)
        await unit_of_work.add_eligibility_decision(eligibility)
        await unit_of_work.add_outbox(outbox)
        await unit_of_work.commit()

        return P1VerificationTransitionResult(
            user_ref=command.user_ref,
            verification_decision_ref=ids.verification_ref,
            registration_event_id=ids.event_id,
            authority_decision_key=authority_key,
            transition_digest=command.transition_digest,
            facts_version=command.target_facts_version,
            status=VerificationStatus.VERIFIED.value,
            replayed=False,
        )

    async def _load_ready(self, unit_of_work, command):
        user = await unit_of_work.get_user_for_update(command.user_ref)
        classification = await unit_of_work.get_current_classification(
            command.user_ref
        )
        predecessor = await unit_of_work.get_current_verification(
            command.user_ref
        )
        self._require_ready(
            user, classification, predecessor, command
        )
        return user, classification, predecessor

    def _generate_ids(self) -> _TransitionIds:
        return _TransitionIds(
            verification_ref=self._generate_uuid7(
                "verification_decision_ref"
            ),
            event_id=self._generate_uuid7("registration_event_id"),
            eligibility_ref=self._generate_uuid7(
                "eligibility_decision_ref"
            ),
            outbox_record_id=self._generate_uuid7(
                "outbox_record_id"
            ),
        )

    def _generate_uuid7(self, field_name: str) -> UUID:
        return _require_uuid7(
            self._uuid_generator.generate(), field_name
        )

    @staticmethod
    def _semantic_key(user_ref: int, authority_key: str) -> str:
        return (
            f"{EVENT_TYPE}:v{EVENT_SCHEMA_VERSION}:"
            f"p1_user:{user_ref}:authority:{authority_key}"
        )

    @staticmethod
    def _build_facts(
        *, user, classification, command, verification_ref
    ) -> TrustedRegistrationEligibilityFacts:
        try:
            return TrustedRegistrationEligibilityFacts(
                user_ref=command.user_ref,
                exists=True,
                facts_version=command.target_facts_version,
                role=RegistrationUserRole(user.role),
                status=RegistrationAccountStatus(user.status),
                verify_status=VerificationStatus.VERIFIED,
                verification_decision_ref=verification_ref,
                verification_subject_user_ref=command.user_ref,
                verification_outcome=VerificationOutcome.VERIFIED,
                verification_epoch=command.verification_epoch,
                verification_is_current=True,
                account_class=AccountClass(
                    classification.account_class
                ),
                classification_decision_ref=(
                    classification.decision_ref
                ),
                classification_subject_user_ref=(
                    classification.user_ref
                ),
                classification_version=(
                    classification.classification_version
                ),
                classification_is_current=classification.is_current,
            )
        except (AttributeError, ValueError):
            raise P1VerificationTransitionNotReady(
                "P1 verification transition prerequisites are not ready"
            ) from None

    @classmethod
    def _replay(
        cls,
        existing,
        command: P1VerificationTransitionCommand,
        authority_key: str,
    ) -> P1VerificationTransitionResult:
        if type(existing) is not P1VerificationTransitionSnapshot:
            cls._raise_inconsistent()

        result = existing.result
        verification = existing.verification
        classification = existing.classification
        eligibility = existing.eligibility
        outbox = existing.outbox

        expected_facts = TrustedRegistrationEligibilityFacts(
            user_ref=command.user_ref,
            exists=True,
            facts_version=command.target_facts_version,
            role=RegistrationUserRole.MEMBER,
            status=RegistrationAccountStatus.ACTIVE,
            verify_status=VerificationStatus.VERIFIED,
            verification_decision_ref=(
                result.verification_decision_ref
            ),
            verification_subject_user_ref=command.user_ref,
            verification_outcome=VerificationOutcome.VERIFIED,
            verification_epoch=command.verification_epoch,
            verification_is_current=True,
            account_class=AccountClass.NATURAL_PERSON,
            classification_decision_ref=(
                getattr(classification, "decision_ref", None)
            ),
            classification_subject_user_ref=(
                getattr(classification, "user_ref", None)
            ),
            classification_version=(
                getattr(classification, "classification_version", None)
            ),
            classification_is_current=(
                getattr(classification, "is_current", None)
            ),
        )
        expected_facts_digest = hashlib.sha256(
            repr(expected_facts).encode("utf-8")
        ).hexdigest()
        expected_projection_digest = build_p1_projection_digest(
            user_ref=command.user_ref,
            role=existing.user_role,
            status=existing.user_status,
            verify_status=existing.user_verify_status,
            updated_at=existing.user_updated_at,
        )

        valid = (
            type(result) is P1VerificationTransitionResult
            and result.authority_decision_key == authority_key
            and result.transition_digest == command.transition_digest
            and result.user_ref == command.user_ref
            and result.facts_version == command.target_facts_version
            and result.status == VerificationStatus.VERIFIED.value
            and existing.user_role == RegistrationUserRole.MEMBER.value
            and existing.user_status
            == RegistrationAccountStatus.ACTIVE.value
            and existing.user_verify_status
            == VerificationStatus.VERIFIED.value
            and type(verification) is P1VerificationDecision
            and verification.decision_ref
            == result.verification_decision_ref
            and verification.user_ref == command.user_ref
            and verification.facts_version
            == command.target_facts_version
            and verification.verification_epoch
            == command.verification_epoch
            and verification.outcome == command.outcome
            and verification.evidence_digest
            == command.evidence_digest
            and verification.actor_type == command.actor_type
            and verification.actor_ref == command.actor_ref
            and verification.decided_at == command.decided_at
            and verification.authority_decision_key == authority_key
            and verification.registration_event_id
            == result.registration_event_id
            and (
                verification.supersedes_ref is None
                or _is_uuid7(verification.supersedes_ref)
            )
            and getattr(classification, "user_ref", None)
            == command.user_ref
            and getattr(classification, "facts_version", None)
            == command.target_facts_version
            and getattr(classification, "account_class", None)
            == AccountClass.NATURAL_PERSON.value
            and getattr(classification, "is_current", None) is True
            and _is_uuid7(
                getattr(classification, "decision_ref", None)
            )
            and type(
                getattr(classification, "classification_version", None)
            )
            is int
            and classification.classification_version > 0
            and type(eligibility)
            is P1RegistrationEligibilityDecision
            and _is_uuid7(eligibility.decision_ref)
            and eligibility.user_ref == command.user_ref
            and eligibility.facts_version
            == command.target_facts_version
            and eligibility.verification_decision_ref
            == result.verification_decision_ref
            and eligibility.classification_decision_ref
            == classification.decision_ref
            and eligibility.policy_version == POLICY_VERSION
            and eligibility.facts_digest == expected_facts_digest
            and eligibility.p1_projection_digest
            == expected_projection_digest
            and eligibility.decision
            == EligibilityDecision.ELIGIBLE.value
            and eligibility.reason == EligibilityReason.ELIGIBLE.value
            and eligibility.decided_at == command.decided_at
            and type(outbox) is P1RegistrationOutboxRecord
            and _is_uuid7(outbox.outbox_record_id)
            and outbox.event_id == result.registration_event_id
            and outbox.semantic_idempotency_key
            == cls._semantic_key(command.user_ref, authority_key)
            and outbox.event_type == EVENT_TYPE
            and outbox.event_schema_version == EVENT_SCHEMA_VERSION
            and outbox.source_system == SOURCE_SYSTEM
            and outbox.source_ref == command.user_ref
            and outbox.verification_decision_ref
            == result.verification_decision_ref
            and outbox.authority_decision_key == authority_key
            and outbox.facts_version == command.target_facts_version
            and outbox.occurred_at == command.decided_at
            and outbox.status in OUTBOX_LIFECYCLE_STATUSES
        )
        if not valid:
            cls._raise_inconsistent()
        return replace(result, replayed=True)

    @staticmethod
    def _raise_inconsistent() -> None:
        raise P1VerificationTransitionInconsistent(
            "P1 verification transition is inconsistent"
        )

    @staticmethod
    def _require_ready(
        user, classification, predecessor, command
    ) -> None:
        predecessor_ready = predecessor is None or (
            _is_uuid7(getattr(predecessor, "decision_ref", None))
            and getattr(predecessor, "user_ref", None)
            == command.user_ref
            and type(getattr(predecessor, "facts_version", None)) is int
            and predecessor.facts_version < command.target_facts_version
            and type(
                getattr(predecessor, "verification_epoch", None)
            )
            is int
            and predecessor.verification_epoch
            < command.verification_epoch
        )
        if (
            user is None
            or type(getattr(user, "id", None)) is not int
            or user.id != command.user_ref
            or getattr(user, "role", None)
            != RegistrationUserRole.MEMBER.value
            or getattr(user, "status", None)
            != RegistrationAccountStatus.ACTIVE.value
            or getattr(user, "verify_status", None)
            not in {"submitted", "pending", "unverified"}
            or classification is None
            or getattr(classification, "user_ref", None)
            != command.user_ref
            or getattr(classification, "facts_version", None)
            != command.target_facts_version
            or getattr(classification, "is_current", None) is not True
            or not _is_uuid7(
                getattr(classification, "decision_ref", None)
            )
            or type(
                getattr(classification, "classification_version", None)
            )
            is not int
            or classification.classification_version <= 0
            or getattr(classification, "account_class", None)
            != AccountClass.NATURAL_PERSON.value
            or not predecessor_ready
        ):
            raise P1VerificationTransitionNotReady(
                "P1 verification transition prerequisites are not ready"
            )
