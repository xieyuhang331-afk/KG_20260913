from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.models import User

from .eligibility_evidence_models import (
    IdentityVerificationEvidenceOrmModel,
    RegistrationEligibilityDecisionEvidenceOrmModel,
    UserAccountClassificationEvidenceOrmModel,
)
from .registration_outbox import (
    P1RegistrationEligibilityDecision,
    P1RegistrationOutboxRecord,
    P1VerificationDecision,
    P1VerificationTransitionCommitOutcomeUnknown,
    P1VerificationTransitionResult,
    P1VerificationTransitionSnapshot,
    P1VerificationTransitionUnavailable,
)
from .registration_outbox_models import (
    RegistrationVerifiedOutboxOrmModel,
)


_UNIQUE_VIOLATION_SQLSTATE = "23505"
_CONFIRMABLE_CONSTRAINTS = frozenset(
    {
        "uq_identity_verification_event_identity",
        "uq_identity_verification_authority_key_present",
        "uq_identity_verification_registration_event_present",
        "uq_registration_eligibility_canonical_key",
        "uq_registration_verified_outbox_event",
        "uq_registration_verified_outbox_semantic_key",
        "uq_registration_verified_outbox_verification",
        "uq_registration_verified_outbox_authority",
        "uq_registration_verified_outbox_source_decision",
    }
)
_COMMIT_UNAVAILABLE_ERRORS = (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SqlAlchemyTimeoutError,
)


class SqlAlchemyP1VerificationTransitionRepository:
    def __init__(self, session) -> None:
        self._session = session

    async def find_by_authority_decision_key(
        self, authority_decision_key: str
    ) -> P1VerificationTransitionSnapshot | None:
        failed = False
        snapshot = None
        try:
            snapshot = await self._find_by_authority_decision_key(
                authority_decision_key
            )
        except P1VerificationTransitionUnavailable:
            failed = True
        except Exception:
            failed = True
        if failed:
            _raise_unavailable()
        return snapshot

    async def _find_by_authority_decision_key(
        self, authority_decision_key: str
    ) -> P1VerificationTransitionSnapshot | None:
        identity = await self._one_or_none(
            select(
                IdentityVerificationEvidenceOrmModel,
                RegistrationVerifiedOutboxOrmModel,
            )
            .select_from(IdentityVerificationEvidenceOrmModel)
            .join(
                RegistrationVerifiedOutboxOrmModel,
                IdentityVerificationEvidenceOrmModel.authority_decision_key
                == RegistrationVerifiedOutboxOrmModel.authority_decision_key,
                full=True,
            )
            .where(
                or_(
                    IdentityVerificationEvidenceOrmModel.authority_decision_key
                    == authority_decision_key,
                    RegistrationVerifiedOutboxOrmModel.authority_decision_key
                    == authority_decision_key,
                )
            )
        )
        if identity is None:
            return None
        verification, outbox = identity

        verification_ref = getattr(
            verification,
            "decision_ref",
            getattr(outbox, "verification_decision_ref", None),
        )
        eligibility = await self._scalar(
            select(RegistrationEligibilityDecisionEvidenceOrmModel).where(
                RegistrationEligibilityDecisionEvidenceOrmModel.verification_decision_ref
                == verification_ref,
                RegistrationEligibilityDecisionEvidenceOrmModel.facts_version
                == getattr(
                    verification,
                    "facts_version",
                    getattr(outbox, "facts_version", None),
                ),
            )
        )
        classification = await self._scalar(
            select(UserAccountClassificationEvidenceOrmModel).where(
                UserAccountClassificationEvidenceOrmModel.decision_ref
                == getattr(
                    eligibility, "classification_decision_ref", None
                )
            )
        )
        map_core_model_classes()
        source_ref = getattr(
            verification,
            "user_ref",
            getattr(outbox, "source_ref", None),
        )
        user = await self._scalar(
            select(User).where(User.id == source_ref).limit(1)
        )

        restored_verification = (
            None
            if verification is None
            else _restore_verification(verification)
        )
        restored_eligibility = (
            None
            if eligibility is None
            else _restore_eligibility(eligibility)
        )
        restored_classification = (
            None
            if classification is None
            else _restore_classification(classification)
        )
        restored_outbox = (
            None if outbox is None else _restore_verified_outbox(outbox)
        )
        transition_digest = (
            "0" * 64
            if restored_verification is None
            else _transition_digest(restored_verification)
        )
        result = _restore_result(
            verification=restored_verification,
            outbox=restored_outbox,
            authority_decision_key=authority_decision_key,
            transition_digest=transition_digest,
        )
        return P1VerificationTransitionSnapshot(
            result=result,
            user_role=getattr(user, "role", None),
            user_status=getattr(user, "status", None),
            user_verify_status=getattr(user, "verify_status", None),
            user_updated_at=getattr(user, "updated_at", None),
            verification=restored_verification,
            classification=restored_classification,
            eligibility=restored_eligibility,
            outbox=restored_outbox,
        )

    async def get_user_for_update(self, user_ref: int):
        map_core_model_classes()
        return await self._scalar(
            select(User)
            .where(User.id == user_ref)
            .limit(1)
            .with_for_update()
        )

    async def get_current_classification(self, user_ref: int):
        row = await self._scalar(
            select(UserAccountClassificationEvidenceOrmModel)
            .where(
                UserAccountClassificationEvidenceOrmModel.user_ref
                == user_ref
            )
            .order_by(
                UserAccountClassificationEvidenceOrmModel.classification_version.desc()
            )
            .limit(1)
        )
        return None if row is None else _restore_classification(row)

    async def get_current_verification(self, user_ref: int):
        row = await self._scalar(
            select(IdentityVerificationEvidenceOrmModel)
            .where(
                IdentityVerificationEvidenceOrmModel.user_ref == user_ref
            )
            .order_by(
                IdentityVerificationEvidenceOrmModel.verification_epoch.desc()
            )
            .limit(1)
        )
        return None if row is None else _restore_verification(row)

    async def add_verification_decision(
        self, evidence: P1VerificationDecision
    ) -> None:
        await self._add(
            IdentityVerificationEvidenceOrmModel(
                decision_ref=evidence.decision_ref,
                user_ref=evidence.user_ref,
                facts_version=evidence.facts_version,
                verification_epoch=evidence.verification_epoch,
                outcome=evidence.outcome,
                evidence_digest=evidence.evidence_digest,
                actor_type=evidence.actor_type,
                actor_ref=evidence.actor_ref,
                supersedes_ref=evidence.supersedes_ref,
                decided_at=evidence.decided_at,
                authority_decision_key=evidence.authority_decision_key,
                registration_event_id=evidence.registration_event_id,
            )
        )

    async def add_eligibility_decision(
        self, evidence: P1RegistrationEligibilityDecision
    ) -> None:
        await self._add(
            RegistrationEligibilityDecisionEvidenceOrmModel(
                decision_ref=evidence.decision_ref,
                user_ref=evidence.user_ref,
                facts_version=evidence.facts_version,
                verification_decision_ref=(
                    evidence.verification_decision_ref
                ),
                classification_decision_ref=(
                    evidence.classification_decision_ref
                ),
                policy_version=evidence.policy_version,
                facts_digest=evidence.facts_digest,
                p1_projection_digest=evidence.p1_projection_digest,
                decision=evidence.decision,
                reason=evidence.reason,
                decided_at=evidence.decided_at,
            )
        )

    async def add_outbox(
        self, outbox: P1RegistrationOutboxRecord
    ) -> None:
        await self._add(
            RegistrationVerifiedOutboxOrmModel(
                outbox_record_id=outbox.outbox_record_id,
                event_id=outbox.event_id,
                semantic_idempotency_key=(
                    outbox.semantic_idempotency_key
                ),
                event_type=outbox.event_type,
                event_schema_version=outbox.event_schema_version,
                source_system=outbox.source_system,
                source_ref=outbox.source_ref,
                verification_decision_ref=(
                    outbox.verification_decision_ref
                ),
                authority_decision_key=outbox.authority_decision_key,
                facts_version=outbox.facts_version,
                occurred_at=outbox.occurred_at,
                trace_ref=None,
                payload_digest=_payload_digest(outbox),
                status="pending",
                attempt_count=0,
                lease_generation=0,
            )
        )

    async def _scalar(self, statement):
        failed = False
        value = None
        try:
            result = await self._session.execute(statement)
            value = result.scalar_one_or_none()
        except Exception:
            failed = True
        if failed:
            _raise_unavailable()
        return value

    async def _one_or_none(self, statement):
        failed = False
        value = None
        try:
            result = await self._session.execute(statement)
            value = result.one_or_none()
        except Exception:
            failed = True
        if failed:
            _raise_unavailable()
        return value

    async def _add(self, model) -> None:
        failed = False
        try:
            self._session.add(model)
        except Exception:
            failed = True
        if failed:
            _raise_unavailable()


class SqlAlchemyP1VerificationTransitionUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = None
        self._repository = None
        self._finalized = False
        self._entered = False
        self._exited = False

    @property
    def repository(self) -> SqlAlchemyP1VerificationTransitionRepository:
        self._require_active()
        return self._repository

    async def __aenter__(self):
        if self._entered or self._exited:
            _raise_unavailable()
        self._entered = True
        caught = None
        try:
            self._session = self._session_factory()
            await self._session.begin()
            self._repository = SqlAlchemyP1VerificationTransitionRepository(
                self._session
            )
        except BaseException as exc:
            caught = exc
        if caught is not None:
            cleanup_base_exception = await self._cleanup_enter_failure()
            self._exited = True
            if not isinstance(caught, Exception):
                raise caught
            if cleanup_base_exception is not None:
                raise cleanup_base_exception
            _raise_unavailable()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self._require_active()
        cleanup_base_exception = None
        cleanup_failed = False
        if not self._finalized:
            try:
                await self._session.rollback()
            except BaseException as caught:
                if not isinstance(caught, Exception):
                    cleanup_base_exception = caught
                else:
                    cleanup_failed = True
        try:
            await self._session.close()
        except BaseException as caught:
            if not isinstance(caught, Exception):
                cleanup_base_exception = caught
            else:
                cleanup_failed = True
        finally:
            self._clear()

        if exc is not None:
            return False
        if cleanup_base_exception is not None:
            raise cleanup_base_exception
        if cleanup_failed:
            _raise_unavailable()
        return False

    async def commit(self) -> None:
        self._require_active()
        if self._finalized:
            _raise_unavailable()
        commit_unknown = False
        unavailable = False
        try:
            await self._session.commit()
        except Exception as exc:
            if _commit_outcome_requires_confirmation(exc):
                commit_unknown = True
            else:
                unavailable = True
        if commit_unknown:
            raise P1VerificationTransitionCommitOutcomeUnknown(
                "P1 verification transition commit outcome is unknown"
            )
        if unavailable:
            _raise_unavailable()
        self._finalized = True

    async def find_by_authority_decision_key(self, authority_decision_key):
        return await self.repository.find_by_authority_decision_key(
            authority_decision_key
        )

    async def get_user_for_update(self, user_ref):
        return await self.repository.get_user_for_update(user_ref)

    async def get_current_classification(self, user_ref):
        return await self.repository.get_current_classification(user_ref)

    async def get_current_verification(self, user_ref):
        return await self.repository.get_current_verification(user_ref)

    async def add_verification_decision(self, evidence):
        await self.repository.add_verification_decision(evidence)

    async def add_eligibility_decision(self, evidence):
        await self.repository.add_eligibility_decision(evidence)

    async def add_outbox(self, outbox):
        await self.repository.add_outbox(outbox)

    async def _cleanup_enter_failure(self) -> BaseException | None:
        if self._session is None:
            return None
        cleanup_base_exception = None
        try:
            await self._session.rollback()
        except BaseException as caught:
            if not isinstance(caught, Exception):
                cleanup_base_exception = caught
        try:
            await self._session.close()
        except BaseException as caught:
            if (
                cleanup_base_exception is None
                and not isinstance(caught, Exception)
            ):
                cleanup_base_exception = caught
        self._session = None
        self._repository = None
        return cleanup_base_exception

    def _require_active(self) -> None:
        if (
            not self._entered
            or self._exited
            or self._session is None
            or self._repository is None
        ):
            _raise_unavailable()

    def _clear(self) -> None:
        self._session = None
        self._repository = None
        self._finalized = True
        self._exited = True


def _payload_digest(outbox: P1RegistrationOutboxRecord) -> str:
    canonical = json.dumps(
        {
            "authority_decision_key": outbox.authority_decision_key,
            "event_id": str(outbox.event_id),
            "event_schema_version": outbox.event_schema_version,
            "event_type": outbox.event_type,
            "facts_version": outbox.facts_version,
            "occurred_at": outbox.occurred_at.isoformat(),
            "source_ref": outbox.source_ref,
            "source_system": outbox.source_system,
            "trace_ref": None,
            "verification_decision_ref": str(
                outbox.verification_decision_ref
            ),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _transition_digest(verification: P1VerificationDecision) -> str:
    canonical = json.dumps(
        {
            "actor_ref": verification.actor_ref,
            "actor_type": verification.actor_type,
            "authority_decision_key": (
                verification.authority_decision_key
            ),
            "decided_at": verification.decided_at.isoformat(),
            "evidence_digest": verification.evidence_digest,
            "outcome": verification.outcome,
            "user_ref": verification.user_ref,
            "verification_epoch": verification.verification_epoch,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _restore_verification(row) -> P1VerificationDecision:
    return P1VerificationDecision(
        decision_ref=_uuid(row.decision_ref),
        user_ref=row.user_ref,
        facts_version=row.facts_version,
        verification_epoch=row.verification_epoch,
        outcome=row.outcome,
        evidence_digest=row.evidence_digest,
        actor_type=row.actor_type,
        actor_ref=row.actor_ref,
        decided_at=row.decided_at,
        authority_decision_key=row.authority_decision_key,
        registration_event_id=_optional_uuid(row.registration_event_id),
        supersedes_ref=_optional_uuid(row.supersedes_ref),
    )


def _restore_classification(row):
    return SimpleNamespace(
        decision_ref=_uuid(row.decision_ref),
        user_ref=row.user_ref,
        facts_version=row.facts_version,
        classification_version=row.classification_version,
        account_class=row.account_class,
        decision_basis_code=row.decision_basis_code,
        supersedes_ref=_optional_uuid(row.supersedes_ref),
        decided_at=row.decided_at,
        is_current=True,
    )


def _restore_eligibility(row) -> P1RegistrationEligibilityDecision:
    return P1RegistrationEligibilityDecision(
        decision_ref=_uuid(row.decision_ref),
        user_ref=row.user_ref,
        facts_version=row.facts_version,
        verification_decision_ref=_uuid(row.verification_decision_ref),
        classification_decision_ref=_uuid(
            row.classification_decision_ref
        ),
        policy_version=row.policy_version,
        facts_digest=row.facts_digest,
        p1_projection_digest=row.p1_projection_digest,
        decision=row.decision,
        reason=row.reason,
        decided_at=row.decided_at,
    )


def _restore_outbox(row) -> P1RegistrationOutboxRecord:
    return P1RegistrationOutboxRecord(
        outbox_record_id=_uuid(row.outbox_record_id),
        event_id=_uuid(row.event_id),
        semantic_idempotency_key=row.semantic_idempotency_key,
        event_type=row.event_type,
        event_schema_version=row.event_schema_version,
        source_system=row.source_system,
        source_ref=row.source_ref,
        verification_decision_ref=_uuid(row.verification_decision_ref),
        authority_decision_key=row.authority_decision_key,
        facts_version=row.facts_version,
        occurred_at=row.occurred_at,
        status=row.status,
    )


def _restore_verified_outbox(row):
    restored = _restore_outbox(row)
    expected_digest = _payload_digest(restored)
    actual_digest = getattr(row, "payload_digest", None)
    if (
        getattr(row, "trace_ref", None) is not None
        or type(actual_digest) is not str
        or not hmac.compare_digest(actual_digest, expected_digest)
    ):
        return SimpleNamespace(invalid_outbox_envelope=True)
    return restored


def _restore_result(
    *,
    verification,
    outbox,
    authority_decision_key: str,
    transition_digest: str,
):
    if type(verification) is P1VerificationDecision:
        event_id = verification.registration_event_id
        if type(event_id) is UUID:
            return P1VerificationTransitionResult(
                user_ref=verification.user_ref,
                verification_decision_ref=verification.decision_ref,
                registration_event_id=event_id,
                authority_decision_key=authority_decision_key,
                transition_digest=transition_digest,
                facts_version=verification.facts_version,
                status="verified",
                replayed=False,
            )
    if type(outbox) is P1RegistrationOutboxRecord:
        return P1VerificationTransitionResult(
            user_ref=outbox.source_ref,
            verification_decision_ref=outbox.verification_decision_ref,
            registration_event_id=outbox.event_id,
            authority_decision_key=authority_decision_key,
            transition_digest=transition_digest,
            facts_version=outbox.facts_version,
            status="verified",
            replayed=False,
        )
    return SimpleNamespace(invalid_transition_snapshot=True)


def _uuid(value) -> UUID:
    if type(value) is UUID:
        return value
    if isinstance(value, UUID):
        return UUID(int=value.int)
    _raise_unavailable()


def _optional_uuid(value) -> UUID | None:
    return None if value is None else _uuid(value)


def _commit_outcome_requires_confirmation(exc: Exception) -> bool:
    if isinstance(exc, _COMMIT_UNAVAILABLE_ERRORS):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    if not isinstance(exc, IntegrityError):
        return False
    original = exc.orig
    driver_error = getattr(original, "__cause__", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        driver_error, "sqlstate", None
    )
    constraint_name = getattr(
        original, "constraint_name", None
    ) or getattr(driver_error, "constraint_name", None)
    return (
        sqlstate == _UNIQUE_VIOLATION_SQLSTATE
        and constraint_name in _CONFIRMABLE_CONSTRAINTS
    )


def _raise_unavailable():
    raise P1VerificationTransitionUnavailable(
        "P1 verification transition persistence is unavailable"
    )
