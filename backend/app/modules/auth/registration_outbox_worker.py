from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import UUID


MAX_ATTEMPTS = 8
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 30
MAX_BATCH_SIZE = 50


class RegistrationOutboxWorkerError(RuntimeError):
    pass


class RegistrationOutboxUnavailable(RegistrationOutboxWorkerError):
    pass


class RegistrationOutboxLeaseLost(RegistrationOutboxWorkerError):
    pass


class RegistrationOutboxCommitOutcomeUnknown(RuntimeError):
    """The worker transaction may have reached PostgreSQL."""


class DeliveryStatus(str, Enum):
    COMPLETED = "COMPLETED"
    REPLAYED = "REPLAYED"
    RETRYABLE = "RETRYABLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PERMANENT = "PERMANENT"
    INTERNAL_UNKNOWN = "INTERNAL_UNKNOWN"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class ConfirmationStatus(str, Enum):
    COMPLETE = "COMPLETE"
    ABSENT = "ABSENT"
    PARTIAL_OR_UNKNOWN = "PARTIAL_OR_UNKNOWN"


@dataclass(frozen=True, slots=True)
class RegistrationOutboxDeliveryResult:
    status: DeliveryStatus
    error_code: str | None = None
    error_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RegistrationOutboxWorkItem:
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
    payload_digest: str
    trace_ref: UUID | None
    attempt_count: int
    lease_owner: str
    lease_generation: int
    locked_until: datetime
    confirmation_only: bool = False


@dataclass(frozen=True, slots=True)
class RegistrationOutboxDispatchSummary:
    claimed: int
    delivered: int
    retried: int
    review_required: int
    dead_lettered: int
    lease_lost: int


@dataclass(frozen=True, slots=True)
class RegistrationOutboxState:
    event_id: UUID
    status: str
    lease_owner: str | None
    lease_generation: int
    locked_until: datetime | None
    last_error_category: str | None
    last_error_code: str | None
    last_error_digest: str | None

    def matches_target(self, status, category, code, digest) -> bool:
        return (
            self.status == status
            and self.last_error_category == category
            and self.last_error_code == code
            and self.last_error_digest == digest
        )

    def matches_lease(self, item: RegistrationOutboxWorkItem) -> bool:
        return (
            self.status == "processing"
            and self.lease_owner == item.lease_owner
            and self.lease_generation == item.lease_generation
        )


def retry_delay_seconds(event_id: UUID, attempt_count: int) -> float:
    if type(event_id) is not UUID or not 1 <= attempt_count <= MAX_ATTEMPTS:
        raise ValueError("registration outbox retry input is invalid")
    raw = min(900, 5 * (2 ** (attempt_count - 1)))
    digest = hashlib.sha256(
        f"{event_id}:{attempt_count}".encode("ascii")
    ).digest()
    fraction = int.from_bytes(digest[:2], "big") / 65535
    return raw * (0.8 + 0.4 * fraction)


class RegistrationOutboxDispatcher:
    def __init__(
        self,
        *,
        unit_of_work_factory,
        orchestrator,
        outcome_confirmer,
        sleep=asyncio.sleep,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._orchestrator = orchestrator
        self._outcome_confirmer = outcome_confirmer
        self._sleep = sleep

    async def run_once(
        self, *, lease_owner: str, limit: int = MAX_BATCH_SIZE
    ) -> RegistrationOutboxDispatchSummary:
        if (
            type(lease_owner) is not str
            or not lease_owner.strip()
            or len(lease_owner) > 128
            or type(limit) is not int
            or not 1 <= limit <= MAX_BATCH_SIZE
        ):
            raise ValueError("registration outbox claim input is invalid")

        items = await self._claim(lease_owner, limit)
        counts = {
            "delivered": 0,
            "retried": 0,
            "review_required": 0,
            "dead_lettered": 0,
            "lease_lost": 0,
        }
        for item in items:
            result = await self._deliver(item)
            counts[result] += 1
        return RegistrationOutboxDispatchSummary(
            claimed=len(items), **counts
        )

    async def _claim(self, lease_owner: str, limit: int):
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                items = await unit_of_work.claim(
                    lease_owner=lease_owner,
                    limit=limit,
                    lease_seconds=LEASE_SECONDS,
                )
                await unit_of_work.commit()
                return tuple(items)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RegistrationOutboxUnavailable(
                "registration outbox claim is unavailable"
            ) from None

    async def _deliver(self, item: RegistrationOutboxWorkItem) -> str:
        if not _valid_envelope(item):
            return await self._persist_result(
                item,
                RegistrationOutboxDeliveryResult(
                    DeliveryStatus.PERMANENT,
                    "INVALID_ENVELOPE",
                    _error_digest("INVALID_ENVELOPE"),
                ),
            )
        if item.confirmation_only:
            confirmation = await self._confirm_identity(item)
            result = self._confirmation_result(item, confirmation)
            return await self._persist_result(item, result)

        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(item, stop))
        cancelled = None
        try:
            result = await self._invoke_orchestrator(item)
        except asyncio.CancelledError as caught:
            cancelled = caught
            result = None
        except Exception:
            result = RegistrationOutboxDeliveryResult(
                DeliveryStatus.INTERNAL_UNKNOWN,
                "UNEXPECTED_INTERNAL",
                _error_digest("UNEXPECTED_INTERNAL"),
            )
        finally:
            stop.set()
            heartbeat.cancel()
            try:
                lease_valid = await heartbeat
            except asyncio.CancelledError:
                lease_valid = True

        if cancelled is not None:
            raise cancelled
        if not lease_valid:
            return "lease_lost"
        if result.status is DeliveryStatus.OUTCOME_UNKNOWN:
            confirmation = await self._confirm_identity(item)
            result = self._confirmation_result(item, confirmation)
        return await self._persist_result(item, result)

    async def _invoke_orchestrator(self, item):
        call = getattr(self._orchestrator, "deliver", self._orchestrator)
        result = await call(item)
        if type(result) is not RegistrationOutboxDeliveryResult:
            return RegistrationOutboxDeliveryResult(
                DeliveryStatus.PERMANENT,
                "INVALID_ENVELOPE",
                _error_digest("INVALID_ENVELOPE"),
            )
        return result

    async def _confirm_identity(self, item) -> ConfirmationStatus:
        try:
            call = getattr(
                self._outcome_confirmer,
                "confirm",
                self._outcome_confirmer,
            )
            result = await call(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            return ConfirmationStatus.PARTIAL_OR_UNKNOWN
        if type(result) is not ConfirmationStatus:
            return ConfirmationStatus.PARTIAL_OR_UNKNOWN
        return result

    @staticmethod
    def _confirmation_result(item, confirmation):
        if confirmation is ConfirmationStatus.COMPLETE:
            return RegistrationOutboxDeliveryResult(
                DeliveryStatus.COMPLETED
            )
        if confirmation is ConfirmationStatus.ABSENT:
            return RegistrationOutboxDeliveryResult(
                DeliveryStatus.RETRYABLE,
                "ORCHESTRATOR_RETRYABLE",
                _error_digest("ORCHESTRATOR_RETRYABLE"),
            )
        return RegistrationOutboxDeliveryResult(
            DeliveryStatus.REVIEW_REQUIRED,
            "OUTCOME_UNKNOWN_UNCONFIRMED",
            _error_digest("OUTCOME_UNKNOWN_UNCONFIRMED"),
        )

    async def _heartbeat(self, item, stop: asyncio.Event) -> bool:
        while not stop.is_set():
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=HEARTBEAT_SECONDS
                )
                return True
            except TimeoutError:
                pass
            try:
                if not await self._renew_lease(item):
                    return False
            except asyncio.CancelledError:
                raise
            except Exception:
                return False
        return True

    async def _renew_lease(self, item) -> bool:
        renewed_until = None
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                renewed_until = await unit_of_work.renew(
                    event_id=item.event_id,
                    lease_owner=item.lease_owner,
                    lease_generation=item.lease_generation,
                    lease_seconds=LEASE_SECONDS,
                )
                if renewed_until is None:
                    return False
                await unit_of_work.commit()
                return True
        except RegistrationOutboxCommitOutcomeUnknown:
            pass
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.get_state(item.event_id)
            return (
                state is not None
                and state.matches_lease(item)
                and state.locked_until is not None
                and renewed_until is not None
                and state.locked_until == renewed_until
            )

    async def _persist_result(self, item, result) -> str:
        target, category, code, digest, delay = self._target(item, result)
        changed = await self._transition_once(
            item, target, category, code, digest, delay
        )
        if not changed:
            return "lease_lost"
        return {
            "delivered": "delivered",
            "retry": "retried",
            "review_required": "review_required",
            "dead_letter": "dead_lettered",
        }[target]

    @staticmethod
    def _target(item, result):
        if result.status in {
            DeliveryStatus.COMPLETED,
            DeliveryStatus.REPLAYED,
        }:
            return "delivered", None, None, None, None
        if result.status in {
            DeliveryStatus.RETRYABLE,
            DeliveryStatus.INTERNAL_UNKNOWN,
        }:
            internal = result.status is DeliveryStatus.INTERNAL_UNKNOWN
            if internal:
                code = "UNEXPECTED_INTERNAL"
                return (
                    "review_required",
                    "INTERNAL_UNKNOWN",
                    code,
                    _error_digest(code),
                    None,
                )
            if item.attempt_count >= MAX_ATTEMPTS:
                code = "RETRY_EXHAUSTED"
                return (
                    "dead_letter",
                    "PERMANENT",
                    code,
                    _error_digest(code),
                    None,
                )
            allowed = {
                "DEPENDENCY_UNAVAILABLE",
                "RATE_LIMITED",
                "TRANSIENT_TRANSPORT",
                "ORCHESTRATOR_RETRYABLE",
            }
            code = (
                result.error_code
                if result.error_code in allowed
                else "ORCHESTRATOR_RETRYABLE"
            )
            return (
                "retry",
                "RETRYABLE",
                code,
                result.error_digest or _error_digest(code),
                retry_delay_seconds(item.event_id, item.attempt_count),
            )
        if result.status is DeliveryStatus.REVIEW_REQUIRED:
            allowed = {
                "ELIGIBILITY_PROOF_MISSING",
                "ELIGIBILITY_STALE",
                "ELIGIBILITY_INCONSISTENT",
                "BOOTSTRAP_CONFLICT",
                "OUTCOME_UNKNOWN_UNCONFIRMED",
                "P1_VERIFICATION_OUTBOX_INCONSISTENT",
            }
            code = (
                result.error_code
                if result.error_code in allowed
                else "OUTCOME_UNKNOWN_UNCONFIRMED"
            )
            return (
                "review_required",
                "REVIEW_REQUIRED",
                code,
                result.error_digest or _error_digest(code),
                None,
            )
        allowed = {
            "UNSUPPORTED_EVENT_TYPE",
            "UNSUPPORTED_SCHEMA_VERSION",
            "INVALID_ENVELOPE",
        }
        code = (
            result.error_code
            if result.error_code in allowed
            else "INVALID_ENVELOPE"
        )
        return (
            "dead_letter",
            "PERMANENT",
            code,
            result.error_digest or _error_digest(code),
            None,
        )

    async def _transition_once(
        self, item, target, category, code, digest, delay
    ) -> bool:
        commit_unknown = False
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                changed = await unit_of_work.transition(
                    event_id=item.event_id,
                    lease_owner=item.lease_owner,
                    lease_generation=item.lease_generation,
                    target_status=target,
                    error_category=category,
                    error_code=code,
                    error_digest=digest,
                    retry_delay_seconds=delay,
                )
                if not changed:
                    return False
                await unit_of_work.commit()
                return True
        except RegistrationOutboxCommitOutcomeUnknown:
            commit_unknown = True
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RegistrationOutboxUnavailable(
                "registration outbox transition is unavailable"
            ) from None

        if commit_unknown:
            return await self._confirm_transition_once(
                item, target, category, code, digest, delay
            )
        raise AssertionError("unreachable")

    async def _confirm_transition_once(
        self, item, target, category, code, digest, delay
    ) -> bool:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                state = await unit_of_work.get_state(item.event_id)
                if state is not None and state.matches_target(
                    target, category, code, digest
                ):
                    return True
                if state is None or not state.matches_lease(item):
                    return False
                changed = await unit_of_work.transition(
                    event_id=item.event_id,
                    lease_owner=item.lease_owner,
                    lease_generation=item.lease_generation,
                    target_status=target,
                    error_category=category,
                    error_code=code,
                    error_digest=digest,
                    retry_delay_seconds=delay,
                )
                if not changed:
                    return False
                await unit_of_work.commit()
                return True
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RegistrationOutboxUnavailable(
                "registration outbox confirmation is unavailable"
            ) from None


class RegistrationOutboxReconciler:
    def __init__(self, *, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def run_once(self, *, limit: int = MAX_BATCH_SIZE) -> int:
        if type(limit) is not int or not 1 <= limit <= MAX_BATCH_SIZE:
            raise ValueError("registration outbox reconciliation limit is invalid")
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                candidates = tuple(
                    await unit_of_work.find_reconciliation_candidates(limit)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RegistrationOutboxUnavailable(
                "registration outbox reconciliation is unavailable"
            ) from None

        completed = 0
        for candidate in candidates:
            if await self._insert_or_confirm(candidate):
                completed += 1
        return completed

    async def _insert_or_confirm(self, candidate) -> bool:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                inserted = await unit_of_work.add_reconciled(candidate)
                await unit_of_work.commit()
                if inserted:
                    return True
        except RegistrationOutboxCommitOutcomeUnknown:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RegistrationOutboxUnavailable(
                "registration outbox reconciliation is unavailable"
            ) from None

        try:
            async with self._unit_of_work_factory() as unit_of_work:
                winner = await unit_of_work.find_by_semantic_key(
                    candidate.semantic_idempotency_key
                )
                return _same_canonical_envelope(winner, candidate)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise RegistrationOutboxUnavailable(
                "registration outbox reconciliation outcome is unknown"
            ) from None


def _error_digest(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()


def _valid_envelope(item: RegistrationOutboxWorkItem) -> bool:
    expected_semantic_key = (
        "identity.registration.verification_verified:v1:"
        f"p1_user:{item.source_ref}:authority:{item.authority_decision_key}"
    )
    if (
        item.event_type
        != "identity.registration.verification_verified"
        or item.event_schema_version != 1
        or item.source_system != "P1_USER"
        or type(item.source_ref) is not int
        or item.source_ref <= 0
        or item.trace_ref is not None
        or type(item.authority_decision_key) is not str
        or len(item.authority_decision_key) != 64
        or any(c not in "0123456789abcdef" for c in item.authority_decision_key)
        or item.semantic_idempotency_key != expected_semantic_key
    ):
        return False
    canonical = json.dumps(
        {
            "authority_decision_key": item.authority_decision_key,
            "event_id": str(item.event_id),
            "event_schema_version": item.event_schema_version,
            "event_type": item.event_type,
            "facts_version": item.facts_version,
            "occurred_at": item.occurred_at.isoformat(),
            "source_ref": item.source_ref,
            "source_system": item.source_system,
            "trace_ref": None,
            "verification_decision_ref": str(
                item.verification_decision_ref
            ),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    expected_digest = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    return hmac.compare_digest(item.payload_digest, expected_digest)


def _same_canonical_envelope(winner, candidate) -> bool:
    if winner is None or candidate is None:
        return False
    fields = (
        "outbox_record_id",
        "event_id",
        "semantic_idempotency_key",
        "event_type",
        "event_schema_version",
        "source_system",
        "source_ref",
        "verification_decision_ref",
        "authority_decision_key",
        "facts_version",
        "occurred_at",
    )
    return all(
        getattr(winner, field, object())
        == getattr(candidate, field, object())
        for field in fields
    )
