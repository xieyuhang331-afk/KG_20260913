from __future__ import annotations

import asyncio
import hashlib
import hmac
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from app.modules.auth.identity_document import canonicalize_prc_resident_identity
from app.modules.auth.identity_remediation import InventoryReport
from app.modules.auth.identity_remediation_ledger_repository import (
    CommitOutcome,
    LedgerExpectation,
    LedgerWriteRequest,
    LedgerWriteResult,
)
from app.modules.auth.identity_remediation_subject_repository import H3Material
from app.modules.auth.identity_submission_crypto import (
    EncryptedIdentityValue,
    IdentitySubmissionCrypto,
    IdentitySubmissionCryptoUnavailable,
)


class IdentityRemediationContractError(RuntimeError):
    pass


class IdentityRemediationCommitOutcomeUnknown(IdentityRemediationContractError):
    pass


@dataclass(frozen=True, slots=True)
class H3ExactMatchResult:
    approved: bool
    gate_digest: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RemediationRunSummary:
    status: str
    result_code: str
    class_status_counts: dict[str, int]
    processed_count: int
    mutation_count: int
    digest_present: bool

    def to_public_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "result_code": self.result_code,
            "class_status_counts": dict(sorted(self.class_status_counts.items())),
            "processed_count": self.processed_count,
            "mutation_count": self.mutation_count,
            "digest_present": self.digest_present,
        }


class RemediationUnitOfWork(Protocol):
    ledger: object
    subjects: object

    async def commit(self) -> None: ...


class ConfirmationUnitOfWork(Protocol):
    ledger: object


UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[RemediationUnitOfWork]]
ConfirmationFactory = Callable[
    [], AbstractAsyncContextManager[ConfirmationUnitOfWork]
]


def action_for_primary_class(primary_class: str) -> tuple[str, bool]:
    actions = {
        "H0": ("EXCLUDE_ITEM", False),
        "H1": ("REQUIRE_ITEM", False),
        "H2": ("REQUIRE_ITEM", False),
        "H3": ("H3_EXACT_MATCH", False),
        "H4": ("REQUIRE_ITEM", True),
        "H5": ("REQUIRE_ITEM", True),
        "H6": ("REQUIRE_ITEM", False),
        "H7": ("EXCLUDE_ITEM", False),
    }
    try:
        return actions[primary_class]
    except KeyError:
        raise IdentityRemediationContractError(
            "A2_REMEDIATION_CLASS_INVALID"
        ) from None


def verify_h3_exact_match(
    material: H3Material,
    *,
    crypto: IdentitySubmissionCrypto,
) -> H3ExactMatchResult:
    try:
        if not hmac.compare_digest(material.encryption_key_id, crypto.key_id):
            raise IdentitySubmissionCryptoUnavailable("key mismatch")
        name_aad = crypto.aad(
            submission_id=str(material.submission_id),
            user_ref=material.subject_user_ref,
            version=material.submission_version,
            field="real_name",
            key_id=material.encryption_key_id,
        )
        card_aad = crypto.aad(
            submission_id=str(material.submission_id),
            user_ref=material.subject_user_ref,
            version=material.submission_version,
            field="id_card",
            key_id=material.encryption_key_id,
        )
        formal_name = crypto.decrypt(
            EncryptedIdentityValue(
                ciphertext=material.real_name_ciphertext,
                nonce=material.real_name_nonce,
            ),
            aad=name_aad,
        )
        formal_card = canonicalize_prc_resident_identity(
            crypto.decrypt(
                EncryptedIdentityValue(
                    ciphertext=material.id_card_ciphertext,
                    nonce=material.id_card_nonce,
                ),
                aad=card_aad,
            )
        )
        legacy_card = canonicalize_prc_resident_identity(material.legacy_id_card)
        expected_content = crypto.digest(
            "content",
            f"{formal_name}\x1f{formal_card}\x1f{material.consent_version}",
        )
        expected_card = crypto.digest("id-card", formal_card)
        comparisons = (
            hmac.compare_digest(
                formal_name.encode("utf-8"), material.legacy_real_name.encode("utf-8")
            ),
            hmac.compare_digest(formal_card, legacy_card),
            hmac.compare_digest(expected_content, material.formal_content_digest),
            hmac.compare_digest(expected_card, material.formal_id_card_digest),
        )
        if not all(comparisons):
            raise ValueError("mismatch")
        gate_digest = hashlib.sha256(
            (
                "A2_R_H3_EXACT_MATCH_GATE_V1;"
                f"submission={material.submission_id};"
                f"submission_version={material.submission_version};"
                f"decision={material.decision_ref};"
                f"decision_version={material.decision_facts_version};"
                f"claim={material.claim_id};claim_version={material.claim_version};"
                f"content={expected_content};card={expected_card};"
                f"consent={material.consent_version};"
            ).encode()
        ).hexdigest()
        return H3ExactMatchResult(True, gate_digest, None)
    except asyncio.CancelledError:
        raise
    except (IdentitySubmissionCryptoUnavailable, ValueError, TypeError, UnicodeError):
        return H3ExactMatchResult(
            False,
            "",
            "A2_REMEDIATION_H3_MISMATCH",
        )


async def propagate_cancellation(awaitable: Awaitable[object]) -> object:
    return await awaitable


@dataclass(frozen=True, slots=True)
class _OperationAttempt:
    requests: tuple[LedgerWriteRequest, ...]
    expectation: LedgerExpectation
    result: LedgerWriteResult

    @property
    def request(self) -> LedgerWriteRequest:
        return self.requests[-1]


class IdentityRemediationApplicationService:
    def __init__(
        self,
        *,
        inventory_provider: Callable[[], Awaitable[InventoryReport]],
        unit_of_work_factory: UnitOfWorkFactory,
        confirmation_factory: ConfirmationFactory,
        crypto: IdentitySubmissionCrypto,
        actor_scope: str,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._inventory_provider = inventory_provider
        self._unit_of_work_factory = unit_of_work_factory
        self._confirmation_factory = confirmation_factory
        self._crypto = crypto
        self._actor_scope = actor_scope
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4
        self._running_batch: LedgerWriteResult | None = None
        self._used = False

    def _key(self, operation: str, batch_ref: UUID, item_ref: UUID | None) -> str:
        value = f"A2_R_IDEMPOTENCY_V1:{self._actor_scope}:{operation}:{batch_ref}:{item_ref or '-'}"
        return hashlib.sha256(value.encode()).hexdigest()

    def _base_request(
        self,
        *,
        operation: str,
        batch_ref: UUID,
        item_ref: UUID | None,
        prior: LedgerWriteResult | None,
        reason_code: str,
        eligibility_digest: str | None = None,
        mutation_digest: str | None = None,
        postimage_digest: str | None = None,
    ) -> LedgerWriteRequest:
        return LedgerWriteRequest(
            operation=operation,
            batch_ref=batch_ref,
            item_ref=item_ref,
            subject_user_ref=None,
            primary_class=None,
            secondary_flags=None,
            expected_version=prior.version if prior else None,
            expected_target_state_digest=prior.state_digest if prior else None,
            classification_rule_hash=None,
            snapshot_ref_hash=None,
            input_count=None,
            h0_count=None,
            h1_count=None,
            h2_count=None,
            h3_count=None,
            h4_count=None,
            h5_count=None,
            h6_count=None,
            h7_count=None,
            preimage_digest=None,
            eligibility_action_gate_digest=eligibility_digest,
            mutation_digest=mutation_digest,
            business_postimage_digest=postimage_digest,
            actor_scope=self._actor_scope,
            reason_code=reason_code,
            idempotency_key_digest=self._key(operation, batch_ref, item_ref),
            receipt_id=self._uuid_factory(),
            audit_id=self._uuid_factory(),
            occurred_at=self._clock(),
        )

    async def _confirm(
        self,
        request: LedgerWriteRequest,
        expectation: LedgerExpectation,
    ) -> CommitOutcome:
        try:
            async with self._confirmation_factory() as confirmation:
                return await confirmation.ledger.confirm(request, expectation)
        except asyncio.CancelledError:
            raise
        except Exception:
            return CommitOutcome.UNKNOWN

    async def _replay_committed(
        self,
        request: LedgerWriteRequest,
    ) -> LedgerWriteResult:
        async with self._unit_of_work_factory() as unit:
            result = await unit.ledger.write(request)
            await unit.commit()
            return result

    async def _best_effort_pause(self) -> None:
        prior = self._running_batch
        if prior is None or prior.state != "RUNNING":
            return
        request = self._base_request(
            operation="PAUSE_BATCH",
            batch_ref=prior.target_ref,
            item_ref=None,
            prior=prior,
            reason_code="A2_BATCH_CONTROLLED",
        )
        try:
            async with self._unit_of_work_factory() as unit:
                paused = await unit.ledger.write(request)
                await unit.commit()
            self._running_batch = paused
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _pause_after_started_failure(self) -> None:
        prior = self._running_batch
        if prior is None or prior.state != "RUNNING":
            return
        try:
            await self._best_effort_pause()
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _execute(
        self,
        operation: Callable[[RemediationUnitOfWork], Awaitable[_OperationAttempt]],
    ) -> LedgerWriteResult:
        frozen_parameters: tuple[tuple[object, ...], ...] | None = None
        for attempt_index in range(2):
            commit_failed = False
            deferred_unknown: IdentityRemediationCommitOutcomeUnknown | None = None
            try:
                async with self._unit_of_work_factory() as unit:
                    try:
                        attempt = await operation(unit)
                        actual_parameters = tuple(
                            request.as_parameters() for request in attempt.requests
                        )
                        if frozen_parameters is None:
                            frozen_parameters = actual_parameters
                        elif actual_parameters != frozen_parameters:
                            raise IdentityRemediationCommitOutcomeUnknown(
                                "A2_REMEDIATION_RETRY_REQUEST_INCONSISTENT"
                            )
                        try:
                            await unit.commit()
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            commit_failed = True
                    except IdentityRemediationCommitOutcomeUnknown as error:
                        deferred_unknown = error
                        raise
            except asyncio.CancelledError:
                raise
            except Exception:
                if deferred_unknown is not None:
                    await self._best_effort_pause()
                    raise deferred_unknown from None
                if not commit_failed:
                    raise
            if deferred_unknown is not None:
                await self._best_effort_pause()
                raise deferred_unknown
            if not commit_failed:
                return attempt.result
            outcome = await self._confirm(attempt.request, attempt.expectation)
            if outcome is CommitOutcome.COMMITTED:
                try:
                    return await self._replay_committed(attempt.request)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._best_effort_pause()
                    raise IdentityRemediationContractError(
                        "A2_REMEDIATION_COMMITTED_READBACK_FAILED"
                    ) from None
            if outcome is CommitOutcome.NOT_COMMITTED and attempt_index == 0:
                continue
            await self._best_effort_pause()
            raise IdentityRemediationCommitOutcomeUnknown(
                "A2_REMEDIATION_COMMIT_OUTCOME_UNKNOWN"
            )
        raise AssertionError("unreachable")

    async def _execute_request(
        self,
        request: LedgerWriteRequest,
        expectation: LedgerExpectation,
    ) -> LedgerWriteResult:
        async def operation(unit: RemediationUnitOfWork) -> _OperationAttempt:
            result = await unit.ledger.write(request)
            return _OperationAttempt((request,), expectation, result)

        return await self._execute(operation)

    async def run(self) -> RemediationRunSummary:
        if self._used:
            raise IdentityRemediationContractError("A2_REMEDIATION_SERVICE_ONE_SHOT")
        self._used = True
        self._running_batch = None
        try:
            return await self._run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._pause_after_started_failure()
            raise
        finally:
            self._running_batch = None

    async def _run_once(self) -> RemediationRunSummary:
        report = await self._inventory_provider()
        batch_ref = UUID(report.batch_ref)
        classification_hash = report.classification_rule_hash.lower()
        snapshot_hash = report.evidence_hash.lower()
        plan_request = LedgerWriteRequest.plan_batch(
            batch_ref=batch_ref,
            classification_rule_hash=classification_hash,
            snapshot_ref_hash=snapshot_hash,
            class_counts=report.class_counts,
            actor_scope=self._actor_scope,
            idempotency_key_digest=self._key("PLAN_BATCH", batch_ref, None),
            receipt_id=self._uuid_factory(),
            audit_id=self._uuid_factory(),
            occurred_at=self._clock(),
        )
        planned = await self._execute_request(
            plan_request, LedgerExpectation("PLANNED", 1, "PLANNED")
        )

        registrations = await self._register_workset(
            batch_ref=batch_ref,
            planned=planned,
            classification_hash=classification_hash,
            snapshot_hash=snapshot_hash,
            expected_count=report.input_count,
        )
        start_request = self._base_request(
            operation="START_BATCH",
            batch_ref=batch_ref,
            item_ref=None,
            prior=planned,
            reason_code="A2_BATCH_CONTROLLED",
        )
        running = await self._execute_request(
            start_request,
            LedgerExpectation("RUNNING", planned.version + 1, "RUNNING"),
        )
        self._running_batch = running

        status_counts: Counter[str] = Counter()
        mutation_count = 0
        pause_required = False
        for subject, discovered in registrations:
            action, must_pause = action_for_primary_class(subject.primary_class)
            pause_required = pause_required or must_pause
            if action == "H3_EXACT_MATCH":
                final = await self._process_h3(batch_ref, discovered)
                mutation_count += final.state == "REMEDIATED"
            else:
                reason = "A2_EXCLUDED" if action == "EXCLUDE_ITEM" else "A2_HUMAN_REVIEW_REQUIRED"
                request = self._base_request(
                    operation=action,
                    batch_ref=batch_ref,
                    item_ref=discovered.target_ref,
                    prior=discovered,
                    reason_code=reason,
                )
                expected_state = "EXCLUDED" if action == "EXCLUDE_ITEM" else "REMEDIATION_REQUIRED"
                final = await self._execute_request(
                    request,
                    LedgerExpectation(expected_state, discovered.version + 1, expected_state),
                )
            status_counts[f"{subject.primary_class}:{final.state}"] += 1

        if pause_required:
            pause_request = self._base_request(
                operation="PAUSE_BATCH",
                batch_ref=batch_ref,
                item_ref=None,
                prior=running,
                reason_code="A2_BATCH_CONTROLLED",
            )
            terminal = await self._execute_request(
                pause_request,
                LedgerExpectation("PAUSED", running.version + 1, "PAUSED"),
            )
            result_code = "A2_REMEDIATION_TOKEN_WINDOW_UNPROVEN"
        else:
            complete_request = self._base_request(
                operation="COMPLETE_BATCH",
                batch_ref=batch_ref,
                item_ref=None,
                prior=running,
                reason_code="A2_BATCH_CONTROLLED",
            )
            terminal = await self._execute_request(
                complete_request,
                LedgerExpectation("COMPLETED", running.version + 1, "COMPLETED"),
            )
            result_code = "A2_REMEDIATION_COMPLETED"
        self._running_batch = terminal
        return RemediationRunSummary(
            status=terminal.state,
            result_code=result_code,
            class_status_counts=dict(status_counts),
            processed_count=len(registrations),
            mutation_count=mutation_count,
            digest_present=bool(terminal.state_digest),
        )

    async def _register_workset(
        self,
        *,
        batch_ref: UUID,
        planned: LedgerWriteResult,
        classification_hash: str,
        snapshot_hash: str,
        expected_count: int,
    ) -> tuple[tuple[object, LedgerWriteResult], ...]:
        frozen_subjects: tuple[object, ...] | None = None
        frozen_subject_facts: tuple[tuple[object, ...], ...] | None = None
        frozen_requests: tuple[LedgerWriteRequest, ...] | None = None
        registration_results: dict[UUID, LedgerWriteResult] = {}

        def subject_facts(subject) -> tuple[object, ...]:
            return (
                subject.subject_user_ref,
                subject.primary_class,
                subject.secondary_flags,
                subject.preimage_digest,
            )

        def request_for(subject) -> LedgerWriteRequest:
            item_ref = self._uuid_factory()
            return LedgerWriteRequest(
                operation="REGISTER_ITEM",
                batch_ref=batch_ref,
                item_ref=item_ref,
                subject_user_ref=subject.subject_user_ref,
                primary_class=subject.primary_class,
                secondary_flags=subject.secondary_flags,
                expected_version=None,
                expected_target_state_digest=None,
                classification_rule_hash=None,
                snapshot_ref_hash=None,
                input_count=None,
                h0_count=None,
                h1_count=None,
                h2_count=None,
                h3_count=None,
                h4_count=None,
                h5_count=None,
                h6_count=None,
                h7_count=None,
                preimage_digest=subject.preimage_digest,
                eligibility_action_gate_digest=None,
                mutation_digest=None,
                business_postimage_digest=None,
                actor_scope=self._actor_scope,
                reason_code="A2_ITEM_DISCOVERED",
                idempotency_key_digest=self._key(
                    "REGISTER_ITEM", batch_ref, item_ref
                ),
                receipt_id=self._uuid_factory(),
                audit_id=self._uuid_factory(),
                occurred_at=self._clock(),
            )

        async def register_all(unit: RemediationUnitOfWork) -> _OperationAttempt:
            nonlocal frozen_subjects, frozen_subject_facts, frozen_requests
            current_subjects = tuple(
                sorted(
                    await unit.subjects.fetch_workset(
                        batch_ref=batch_ref,
                        expected_batch_version=planned.version,
                        expected_batch_state_digest=planned.state_digest,
                        expected_classification_rule_hash=classification_hash,
                        expected_snapshot_ref_hash=snapshot_hash,
                    ),
                    key=lambda subject: subject.subject_user_ref,
                )
            )
            current_facts = tuple(subject_facts(subject) for subject in current_subjects)
            if len(current_subjects) != expected_count:
                raise IdentityRemediationContractError(
                    "A2_REMEDIATION_MANIFEST_MISMATCH"
                )
            if frozen_subjects is None:
                frozen_subjects = current_subjects
                frozen_subject_facts = current_facts
                frozen_requests = tuple(request_for(subject) for subject in current_subjects)
            elif current_facts != frozen_subject_facts:
                raise IdentityRemediationCommitOutcomeUnknown(
                    "A2_REMEDIATION_WORKSET_RETRY_INCONSISTENT"
                )
            assert frozen_requests is not None
            results: list[LedgerWriteResult] = []
            for request in frozen_requests:
                result = await unit.ledger.write(request)
                registration_results[request.item_ref] = result
                results.append(result)
            if not results:
                raise IdentityRemediationContractError("A2_REMEDIATION_EMPTY_WORKSET")
            return _OperationAttempt(
                frozen_requests,
                LedgerExpectation("DISCOVERED", 1, "DISCOVERED"),
                results[-1],
            )

        if expected_count == 0:
            async with self._unit_of_work_factory() as unit:
                workset = await unit.subjects.fetch_workset(
                    batch_ref=batch_ref,
                    expected_batch_version=planned.version,
                    expected_batch_state_digest=planned.state_digest,
                    expected_classification_rule_hash=classification_hash,
                    expected_snapshot_ref_hash=snapshot_hash,
                )
                if tuple(workset):
                    raise IdentityRemediationContractError(
                        "A2_REMEDIATION_MANIFEST_MISMATCH"
                    )
                await unit.commit()
            return ()
        await self._execute(register_all)
        assert frozen_subjects is not None and frozen_requests is not None
        return tuple(
            (subject, registration_results[request.item_ref])
            for subject, request in zip(
                frozen_subjects, frozen_requests, strict=True
            )
        )

    async def _process_h3(
        self,
        batch_ref: UUID,
        discovered: LedgerWriteResult,
    ) -> LedgerWriteResult:
        async with self._unit_of_work_factory() as unit:
            material = await unit.subjects.fetch_h3_material(
                batch_ref=batch_ref,
                item_ref=discovered.target_ref,
                expected_item_version=discovered.version,
                expected_item_state_digest=discovered.state_digest,
            )
            match = verify_h3_exact_match(material, crypto=self._crypto)
        if not match.approved:
            request = self._base_request(
                operation="REQUIRE_ITEM",
                batch_ref=batch_ref,
                item_ref=discovered.target_ref,
                prior=discovered,
                reason_code="A2_HUMAN_REVIEW_REQUIRED",
            )
            return await self._execute_request(
                request,
                LedgerExpectation("REMEDIATION_REQUIRED", discovered.version + 1, "REMEDIATION_REQUIRED"),
            )

        ready_request = self._base_request(
            operation="MARK_ITEM_READY",
            batch_ref=batch_ref,
            item_ref=discovered.target_ref,
            prior=discovered,
            reason_code="A2_CANONICAL_MATCH_APPROVED",
            eligibility_digest=match.gate_digest,
        )
        ready = await self._execute_request(
            ready_request,
            LedgerExpectation("READY", discovered.version + 1, "READY"),
        )
        start_request = self._base_request(
            operation="START_ITEM",
            batch_ref=batch_ref,
            item_ref=discovered.target_ref,
            prior=ready,
            reason_code="A2_APPROVED_REMEDIATION",
        )
        processing = await self._execute_request(
            start_request,
            LedgerExpectation("PROCESSING", ready.version + 1, "PROCESSING"),
        )

        return await self._complete_h3(batch_ref, processing, match.gate_digest)

    async def _complete_h3(
        self,
        batch_ref: UUID,
        processing: LedgerWriteResult,
        exact_match_gate_digest: str,
    ) -> LedgerWriteResult:

        frozen_request: LedgerWriteRequest | None = None
        frozen_invariants: tuple[str, ...] | None = None

        async def mutate_and_complete(unit: RemediationUnitOfWork) -> _OperationAttempt:
            nonlocal frozen_invariants, frozen_request
            current = await unit.subjects.fetch_h3_material(
                batch_ref=batch_ref,
                item_ref=processing.target_ref,
                expected_item_version=processing.version,
                expected_item_state_digest=processing.state_digest,
            )
            current_match = verify_h3_exact_match(current, crypto=self._crypto)
            if not current_match.approved or not hmac.compare_digest(
                current_match.gate_digest, exact_match_gate_digest
            ):
                raise IdentityRemediationContractError("A2_REMEDIATION_H3_MISMATCH")
            mutation = await unit.subjects.clear_h3_legacy(
                batch_ref=batch_ref,
                item_ref=processing.target_ref,
                expected_item_version=processing.version,
                expected_item_state_digest=processing.state_digest,
                expected_chain_digest=current.chain_digest,
                exact_match_gate_digest=exact_match_gate_digest,
            )
            current_invariants = (
                current_match.gate_digest,
                current.chain_digest,
                mutation.mutation_digest,
                mutation.postimage_digest,
                str(processing.version),
                processing.state_digest,
            )
            if frozen_request is None:
                frozen_invariants = current_invariants
                frozen_request = self._base_request(
                    operation="COMPLETE_ITEM",
                    batch_ref=batch_ref,
                    item_ref=processing.target_ref,
                    prior=processing,
                    reason_code="A2_APPROVED_REMEDIATION",
                    mutation_digest=mutation.mutation_digest,
                    postimage_digest=mutation.postimage_digest,
                )
            elif current_invariants != frozen_invariants:
                raise IdentityRemediationCommitOutcomeUnknown(
                    "A2_REMEDIATION_RETRY_REQUEST_INCONSISTENT"
                )
            result = await unit.ledger.write(frozen_request)
            return _OperationAttempt(
                (frozen_request,),
                LedgerExpectation("REMEDIATED", processing.version + 1, "REMEDIATED"),
                result,
            )

        return await self._execute(mutate_and_complete)
