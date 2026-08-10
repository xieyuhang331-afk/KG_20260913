from __future__ import annotations

from typing import NoReturn, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.sqlalchemy_mapping import map_core_model_classes
from app.modules.system.models import OperationLog

from .domain import (
    CanonicalHealthFact,
    HealthFactCorrectionConflict,
    HealthFactIdempotencyConflict,
    HealthFactUnavailable,
)
from .models import CanonicalHealthFactOrmModel


_UNIQUE_VIOLATION = "23505"
_SOURCE_CONSTRAINT = "uq_canonical_health_fact_source_event"
_SUCCESSOR_CONSTRAINT = "uq_canonical_health_fact_single_successor"


class SqlAlchemyHealthFactRepository:
    def __init__(self, session) -> None:
        map_core_model_classes()
        self._session = session

    async def acquire_semantic_lock(self, lock_key: int) -> None:
        await _await_safely(
            self._session.execute(select(func.pg_advisory_xact_lock(lock_key)))
        )

    async def find_by_semantic_identity(
        self,
        *,
        source_type: str,
        producer_event_key: str,
        source_identity_digests: Sequence[str],
    ) -> CanonicalHealthFact | None:
        result = await _await_safely(
            self._session.execute(
                select(CanonicalHealthFactOrmModel).where(
                    CanonicalHealthFactOrmModel.source_type == source_type,
                    CanonicalHealthFactOrmModel.producer_event_key
                    == producer_event_key,
                    CanonicalHealthFactOrmModel.source_identity_digest.in_(
                        tuple(source_identity_digests)
                    ),
                )
            )
        )
        model = _scalar_one_or_none_safely(result)
        return None if model is None else _restore(model)

    async def get_by_id(self, fact_id: int) -> CanonicalHealthFact | None:
        model = await _await_safely(
            self._session.get(CanonicalHealthFactOrmModel, fact_id)
        )
        return None if model is None else _restore(model)

    async def get_successor(self, fact_id: int) -> CanonicalHealthFact | None:
        result = await _await_safely(
            self._session.execute(
                select(CanonicalHealthFactOrmModel).where(
                    CanonicalHealthFactOrmModel.supersedes_fact_id == fact_id
                )
            )
        )
        model = _scalar_one_or_none_safely(result)
        return None if model is None else _restore(model)

    async def add(self, fact: CanonicalHealthFact) -> CanonicalHealthFact:
        model = CanonicalHealthFactOrmModel(
            subject_user_id=fact.subject_user_id,
            indicator_code=fact.indicator_code,
            catalog_version=fact.catalog_version,
            value_kind=fact.value_kind,
            numeric_value=fact.numeric_value,
            unit=fact.unit,
            measured_at=fact.measured_at,
            source_type=fact.source_type,
            source_identity_digest=fact.source_identity_digest,
            producer_event_key=fact.producer_event_key,
            payload_digest=fact.payload_digest,
            digest_key_id=fact.digest_key_id,
            supersedes_fact_id=fact.supersedes_fact_id,
            correction_reason_code=fact.correction_reason_code,
            created_by=fact.created_by,
        )
        integrity: tuple[str | None, str | None] | None = None
        failed = False
        try:
            self._session.add(model)
            await self._session.flush()
            await self._session.refresh(model)
        except IntegrityError as exc:
            integrity = _constraint(exc)
        except Exception:
            failed = True
        if integrity is not None:
            _raise_integrity(*integrity)
        if failed:
            _raise_unavailable()
        return _restore(model)

    async def add_audit(self, *, fact: CanonicalHealthFact, action: str) -> None:
        if fact.id is None or action not in {
            "fact_appended",
            "fact_corrected",
        }:
            raise HealthFactUnavailable("Health fact audit operation failed")
        audit = OperationLog()
        audit.operator_id = fact.created_by
        audit.module = "health_fact"
        audit.object_type = "canonical_health_fact"
        audit.object_id = fact.id
        audit.action = action
        audit.payload = {
            "fact_id": fact.id,
            "source_type": fact.source_type,
            "digest_key_id": fact.digest_key_id,
            "payload_digest": fact.payload_digest,
            "source_identity_digest": fact.source_identity_digest,
            "outcome": "CREATED",
        }
        failed = False
        try:
            self._session.add(audit)
            await self._session.flush()
        except Exception:
            failed = True
        if failed:
            _raise_unavailable()

    async def has_audit(self, *, fact_id: int, action: str) -> bool:
        result = await _await_safely(
            self._session.execute(
                select(OperationLog.id).where(
                    OperationLog.module == "health_fact",
                    OperationLog.object_type == "canonical_health_fact",
                    OperationLog.object_id == fact_id,
                    OperationLog.action == action,
                )
            )
        )
        return _scalar_one_or_none_safely(result) is not None


async def _await_safely(awaitable):
    failed = False
    result = None
    try:
        result = await awaitable
    except Exception:
        failed = True
    if failed:
        _raise_unavailable()
    return result


def _scalar_one_or_none_safely(result):
    failed = False
    value = None
    try:
        value = result.scalar_one_or_none()
    except Exception:
        failed = True
    if failed:
        _raise_unavailable()
    return value


def _raise_unavailable() -> NoReturn:
    raise HealthFactUnavailable("Health fact persistence operation failed")


def _restore(model) -> CanonicalHealthFact:
    return CanonicalHealthFact(
        id=model.id,
        subject_user_id=model.subject_user_id,
        indicator_code=model.indicator_code,
        catalog_version=model.catalog_version,
        value_kind=model.value_kind,
        numeric_value=model.numeric_value,
        unit=model.unit,
        measured_at=model.measured_at,
        received_at=model.received_at,
        created_at=model.created_at,
        source_type=model.source_type,
        source_identity_digest=model.source_identity_digest,
        producer_event_key=model.producer_event_key,
        payload_digest=model.payload_digest,
        digest_key_id=model.digest_key_id,
        supersedes_fact_id=model.supersedes_fact_id,
        correction_reason_code=model.correction_reason_code,
        created_by=model.created_by,
    )


def _constraint(exc: IntegrityError) -> tuple[str | None, str | None]:
    original = exc.orig
    driver = getattr(original, "__cause__", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(driver, "sqlstate", None)
    name = getattr(getattr(original, "diag", None), "constraint_name", None)
    name = name or getattr(getattr(driver, "diag", None), "constraint_name", None)
    return sqlstate, name


def _raise_integrity(sqlstate: str | None, name: str | None) -> NoReturn:
    if sqlstate == _UNIQUE_VIOLATION and name == _SOURCE_CONSTRAINT:
        raise HealthFactIdempotencyConflict("Health fact idempotency conflict")
    if sqlstate == _UNIQUE_VIOLATION and name == _SUCCESSOR_CONSTRAINT:
        raise HealthFactCorrectionConflict("Health fact correction conflict")
    _raise_unavailable()
