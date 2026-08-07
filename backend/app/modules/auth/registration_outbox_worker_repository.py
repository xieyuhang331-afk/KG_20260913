from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)
from sqlalchemy.orm import aliased

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.auth.eligibility_evidence import (
    build_p1_projection_digest,
)
from app.modules.auth.models import User

from .eligibility_evidence_models import (
    IdentityVerificationEvidenceOrmModel,
    RegistrationEligibilityDecisionEvidenceOrmModel,
    UserAccountClassificationEvidenceOrmModel,
)
from .registration_outbox import POLICY_VERSION, P1RegistrationOutboxRecord
from .registration_outbox_models import RegistrationVerifiedOutboxOrmModel
from .registration_outbox_worker import (
    MAX_ATTEMPTS,
    RegistrationOutboxCommitOutcomeUnknown,
    RegistrationOutboxState,
    RegistrationOutboxUnavailable,
    RegistrationOutboxWorkItem,
)


_COMMIT_UNKNOWN_ERRORS = (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SqlAlchemyTimeoutError,
)


def _safe_repository_call(operation):
    async def safe(*args, **kwargs):
        failed = False
        result = None
        try:
            result = await operation(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        if failed:
            _raise_unavailable()
        return result

    return safe


class SqlAlchemyRegistrationOutboxWorkerRepository:
    def __init__(self, session) -> None:
        self._session = session

    @_safe_repository_call
    async def claim(self, *, lease_owner, limit, lease_seconds):
        try:
            now = await self._database_now()
            statement = (
                select(RegistrationVerifiedOutboxOrmModel)
                .where(
                    or_(
                        and_(
                            RegistrationVerifiedOutboxOrmModel.status.in_(
                                ("pending", "retry")
                            ),
                            RegistrationVerifiedOutboxOrmModel.available_at
                            <= now,
                            RegistrationVerifiedOutboxOrmModel.attempt_count
                            < MAX_ATTEMPTS,
                        ),
                        and_(
                            RegistrationVerifiedOutboxOrmModel.status
                            == "processing",
                            RegistrationVerifiedOutboxOrmModel.locked_until
                            <= now,
                        ),
                    )
                )
                .order_by(
                    RegistrationVerifiedOutboxOrmModel.available_at,
                    RegistrationVerifiedOutboxOrmModel.created_at,
                    RegistrationVerifiedOutboxOrmModel.event_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            rows = (await self._session.scalars(statement)).all()
            items = []
            for row in rows:
                expired_processing = row.status == "processing"
                confirmation_only = (
                    expired_processing
                    and row.attempt_count >= MAX_ATTEMPTS
                )
                if (
                    not expired_processing
                    or row.attempt_count < MAX_ATTEMPTS
                ):
                    row.attempt_count += 1
                row.status = "processing"
                row.lease_owner = lease_owner
                row.locked_until = now + timedelta(seconds=lease_seconds)
                row.lease_generation += 1
                row.updated_at = now
                items.append(
                    _restore_work_item(
                        row,
                        confirmation_only=confirmation_only,
                    )
                )
            await self._session.flush()
            return tuple(items)
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    @_safe_repository_call
    async def renew(
        self,
        *,
        event_id,
        lease_owner,
        lease_generation,
        lease_seconds,
    ) -> bool:
        try:
            now = await self._database_now()
            statement = (
                update(RegistrationVerifiedOutboxOrmModel)
                .where(
                    RegistrationVerifiedOutboxOrmModel.event_id
                    == event_id,
                    RegistrationVerifiedOutboxOrmModel.status
                    == "processing",
                    RegistrationVerifiedOutboxOrmModel.lease_owner
                    == lease_owner,
                    RegistrationVerifiedOutboxOrmModel.lease_generation
                    == lease_generation,
                )
                .values(
                    locked_until=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            result = await self._session.execute(statement)
            return (
                now + timedelta(seconds=lease_seconds)
                if result.rowcount == 1
                else None
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    @_safe_repository_call
    async def transition(
        self,
        *,
        event_id,
        lease_owner,
        lease_generation,
        target_status,
        error_category,
        error_code,
        error_digest,
        retry_delay_seconds,
    ) -> bool:
        try:
            now = await self._database_now()
            values = {
                "status": target_status,
                "lease_owner": None,
                "locked_until": None,
                "last_error_category": error_category,
                "last_error_code": error_code,
                "last_error_digest": error_digest,
                "delivered_at": (
                    now if target_status == "delivered" else None
                ),
                "updated_at": now,
            }
            if target_status == "retry":
                values["available_at"] = now + timedelta(
                    seconds=retry_delay_seconds
                )
            statement = (
                update(RegistrationVerifiedOutboxOrmModel)
                .where(
                    RegistrationVerifiedOutboxOrmModel.event_id
                    == event_id,
                    RegistrationVerifiedOutboxOrmModel.status
                    == "processing",
                    RegistrationVerifiedOutboxOrmModel.lease_owner
                    == lease_owner,
                    RegistrationVerifiedOutboxOrmModel.lease_generation
                    == lease_generation,
                    RegistrationVerifiedOutboxOrmModel.locked_until > now,
                )
                .values(**values)
            )
            result = await self._session.execute(statement)
            return result.rowcount == 1
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    @_safe_repository_call
    async def get_state(self, event_id):
        try:
            statement = select(
                RegistrationVerifiedOutboxOrmModel.event_id,
                RegistrationVerifiedOutboxOrmModel.status,
                RegistrationVerifiedOutboxOrmModel.lease_owner,
                RegistrationVerifiedOutboxOrmModel.lease_generation,
                RegistrationVerifiedOutboxOrmModel.locked_until,
                RegistrationVerifiedOutboxOrmModel.last_error_category,
                RegistrationVerifiedOutboxOrmModel.last_error_code,
                RegistrationVerifiedOutboxOrmModel.last_error_digest,
            ).where(
                RegistrationVerifiedOutboxOrmModel.event_id == event_id
            )
            row = (await self._session.execute(statement)).one_or_none()
            if row is None:
                return None
            return RegistrationOutboxState(
                event_id=_uuid(row.event_id),
                status=row.status,
                lease_owner=row.lease_owner,
                lease_generation=row.lease_generation,
                locked_until=row.locked_until,
                last_error_category=row.last_error_category,
                last_error_code=row.last_error_code,
                last_error_digest=row.last_error_digest,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    @_safe_repository_call
    async def find_reconciliation_candidates(self, limit):
        map_core_model_classes()
        verification = IdentityVerificationEvidenceOrmModel
        classification = UserAccountClassificationEvidenceOrmModel
        eligibility = RegistrationEligibilityDecisionEvidenceOrmModel
        verification_successor = aliased(verification)
        classification_successor = aliased(classification)
        eligibility_other_policy = aliased(eligibility)
        try:
            statement = (
                select(
                    User.id,
                    User.role,
                    User.status,
                    User.verify_status,
                    User.updated_at,
                    verification.decision_ref.label("verification_ref"),
                    verification.registration_event_id.label("event_id"),
                    verification.authority_decision_key,
                    verification.facts_version,
                    verification.decided_at,
                    classification.decision_ref.label(
                        "classification_ref"
                    ),
                    eligibility.p1_projection_digest,
                )
                .join(verification, verification.user_ref == User.id)
                .join(
                    classification,
                    classification.user_ref == User.id,
                )
                .join(
                    eligibility,
                    and_(
                        eligibility.user_ref == User.id,
                        eligibility.facts_version
                        == verification.facts_version,
                        eligibility.verification_decision_ref
                        == verification.decision_ref,
                    eligibility.classification_decision_ref
                        == classification.decision_ref,
                    ),
                )
                .where(
                    User.role == "member",
                    User.status == "active",
                    User.verify_status == "verified",
                    verification.outcome == "verified",
                    verification.authority_decision_key.is_not(None),
                    verification.registration_event_id.is_not(None),
                    classification.account_class == "natural_person",
                    classification.facts_version
                    == verification.facts_version,
                    eligibility.policy_version == POLICY_VERSION,
                    eligibility.decision == "eligible",
                    eligibility.reason == "eligible",
                    ~exists().where(
                        verification_successor.supersedes_ref
                        == verification.decision_ref
                    ),
                    ~exists().where(
                        classification_successor.supersedes_ref
                        == classification.decision_ref
                    ),
                    ~exists().where(
                        and_(
                            eligibility_other_policy.user_ref
                            == eligibility.user_ref,
                            eligibility_other_policy.facts_version
                            == eligibility.facts_version,
                            eligibility_other_policy.policy_version
                            != POLICY_VERSION,
                        )
                    ),
                    ~exists().where(
                        RegistrationVerifiedOutboxOrmModel.event_id
                        == verification.registration_event_id
                    ),
                )
                .order_by(verification.decided_at, verification.decision_ref)
                .limit(limit)
            )
            rows = (await self._session.execute(statement)).all()
            candidates = []
            for row in rows:
                digest = build_p1_projection_digest(
                    user_ref=row.id,
                    role=row.role,
                    status=row.status,
                    verify_status=row.verify_status,
                    updated_at=row.updated_at,
                )
                if digest != row.p1_projection_digest:
                    continue
                event_id = _uuid(row.event_id)
                authority_key = row.authority_decision_key
                candidates.append(
                    P1RegistrationOutboxRecord(
                        outbox_record_id=event_id,
                        event_id=event_id,
                        semantic_idempotency_key=(
                            "identity.registration.verification_verified:"
                            f"v1:p1_user:{row.id}:authority:{authority_key}"
                        ),
                        event_type=(
                            "identity.registration.verification_verified"
                        ),
                        event_schema_version=1,
                        source_system="P1_USER",
                        source_ref=row.id,
                        verification_decision_ref=_uuid(
                            row.verification_ref
                        ),
                        authority_decision_key=authority_key,
                        facts_version=row.facts_version,
                        occurred_at=row.decided_at,
                    )
                )
            return tuple(candidates)
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    @_safe_repository_call
    async def add_reconciled(self, candidate) -> bool:
        if type(candidate) is not P1RegistrationOutboxRecord:
            _raise_unavailable()
        values = _candidate_values(candidate)
        statement = (
            postgresql_insert(RegistrationVerifiedOutboxOrmModel)
            .values(**values)
            .on_conflict_do_nothing()
        )
        try:
            result = await self._session.execute(statement)
            return result.rowcount == 1
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    @_safe_repository_call
    async def find_by_semantic_key(self, semantic_key):
        try:
            statement = select(
                RegistrationVerifiedOutboxOrmModel
            ).where(
                RegistrationVerifiedOutboxOrmModel.semantic_idempotency_key
                == semantic_key
            )
            row = (await self._session.scalars(statement)).one_or_none()
            return None if row is None else _restore_record(row)
        except asyncio.CancelledError:
            raise
        except Exception:
            _raise_unavailable()

    async def _database_now(self):
        return await self._session.scalar(select(func.current_timestamp()))


class SqlAlchemyRegistrationOutboxWorkerUnitOfWork:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory
        self._session = None
        self._repository = None
        self._entered = False
        self._finalized = False

    async def __aenter__(self):
        if self._entered:
            _raise_unavailable()
        self._entered = True
        try:
            self._session = self._session_factory()
            await self._session.begin()
            self._repository = SqlAlchemyRegistrationOutboxWorkerRepository(
                self._session
            )
            return self
        except asyncio.CancelledError:
            await self._cleanup()
            raise
        except Exception:
            await self._cleanup()
            _raise_unavailable()

    async def __aexit__(self, exc_type, exc, traceback):
        cleanup_error = None
        if self._session is not None and not self._finalized:
            try:
                await self._session.rollback()
            except BaseException as caught:
                cleanup_error = caught
        try:
            if self._session is not None:
                await self._session.close()
        except BaseException as caught:
            if cleanup_error is None:
                cleanup_error = caught
        self._session = None
        self._repository = None
        if exc is not None:
            return False
        if cleanup_error is not None:
            if not isinstance(cleanup_error, Exception):
                raise cleanup_error
            _raise_unavailable()
        return False

    async def commit(self):
        self._require_active()
        commit_unknown = False
        unavailable = False
        try:
            await self._session.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if _commit_outcome_unknown(exc):
                commit_unknown = True
            else:
                unavailable = True
        if commit_unknown:
            raise RegistrationOutboxCommitOutcomeUnknown(
                "registration outbox commit outcome is unknown"
            )
        if unavailable:
            _raise_unavailable()
        self._finalized = True

    async def claim(self, **kwargs):
        return await self._active_repository().claim(**kwargs)

    async def renew(self, **kwargs):
        return await self._active_repository().renew(**kwargs)

    async def transition(self, **kwargs):
        return await self._active_repository().transition(**kwargs)

    async def get_state(self, event_id):
        return await self._active_repository().get_state(event_id)

    async def find_reconciliation_candidates(self, limit):
        return await self._active_repository().find_reconciliation_candidates(
            limit
        )

    async def add_reconciled(self, candidate):
        return await self._active_repository().add_reconciled(candidate)

    async def find_by_semantic_key(self, semantic_key):
        return await self._active_repository().find_by_semantic_key(
            semantic_key
        )

    def _active_repository(self):
        self._require_active()
        return self._repository

    def _require_active(self):
        if self._session is None or self._repository is None:
            _raise_unavailable()

    async def _cleanup(self):
        if self._session is None:
            return
        try:
            await self._session.rollback()
        except BaseException:
            pass
        try:
            await self._session.close()
        except BaseException:
            pass
        self._session = None
        self._repository = None


def _restore_work_item(row, *, confirmation_only):
    return RegistrationOutboxWorkItem(
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
        payload_digest=row.payload_digest,
        trace_ref=_optional_uuid(row.trace_ref),
        attempt_count=row.attempt_count,
        lease_owner=row.lease_owner,
        lease_generation=row.lease_generation,
        locked_until=row.locked_until,
        confirmation_only=confirmation_only,
    )


def _candidate_values(candidate):
    canonical = json.dumps(
        {
            "authority_decision_key": candidate.authority_decision_key,
            "event_id": str(candidate.event_id),
            "event_schema_version": candidate.event_schema_version,
            "event_type": candidate.event_type,
            "facts_version": candidate.facts_version,
            "occurred_at": candidate.occurred_at.isoformat(),
            "source_ref": candidate.source_ref,
            "source_system": candidate.source_system,
            "trace_ref": None,
            "verification_decision_ref": str(
                candidate.verification_decision_ref
            ),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "outbox_record_id": candidate.outbox_record_id,
        "event_id": candidate.event_id,
        "semantic_idempotency_key": candidate.semantic_idempotency_key,
        "event_type": candidate.event_type,
        "event_schema_version": candidate.event_schema_version,
        "source_system": candidate.source_system,
        "source_ref": candidate.source_ref,
        "verification_decision_ref": candidate.verification_decision_ref,
        "authority_decision_key": candidate.authority_decision_key,
        "facts_version": candidate.facts_version,
        "occurred_at": candidate.occurred_at,
        "trace_ref": None,
        "payload_digest": hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
    }


def _restore_record(row):
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


def _uuid(value):
    if type(value) is UUID:
        return value
    if isinstance(value, UUID):
        return UUID(int=value.int)
    _raise_unavailable()


def _optional_uuid(value):
    return None if value is None else _uuid(value)


def _commit_outcome_unknown(exc):
    return isinstance(exc, _COMMIT_UNKNOWN_ERRORS) or (
        isinstance(exc, DBAPIError) and exc.connection_invalidated
    )


def _raise_unavailable():
    raise RegistrationOutboxUnavailable(
        "registration outbox persistence is unavailable"
    ) from None
