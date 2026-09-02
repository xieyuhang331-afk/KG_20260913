from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_WRITER_SQL = text(
    "SELECT * FROM identity.a2_identity_remediation_ledger_v1("
    + ",".join(f":p{index}" for index in range(29))
    + ")"
)
_CONFIRMATION_SQL = text(
    "SELECT identity.a2_identity_remediation_confirm_v1("
    + ",".join(f":p{index}" for index in range(32))
    + ") AS outcome"
)


class CommitOutcome(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class LedgerWriteResult:
    target_type: str
    target_ref: UUID
    state: str
    version: int
    result_code: str
    receipt_id: UUID
    state_digest: str


@dataclass(frozen=True, slots=True)
class LedgerExpectation:
    state: str
    version: int
    result_code: str

    def to_result(self, request: LedgerWriteRequest) -> LedgerWriteResult:
        target_ref = request.item_ref or request.batch_ref
        return LedgerWriteResult(
            target_type="ITEM" if request.item_ref else "BATCH",
            target_ref=target_ref,
            state=self.state,
            version=self.version,
            result_code=self.result_code,
            receipt_id=request.receipt_id,
            state_digest="",
        )


@dataclass(frozen=True, slots=True)
class LedgerWriteRequest:
    operation: str
    batch_ref: UUID
    item_ref: UUID | None
    subject_user_ref: int | None = field(repr=False)
    primary_class: str | None
    secondary_flags: int | None
    expected_version: int | None
    expected_target_state_digest: str | None
    classification_rule_hash: str | None
    snapshot_ref_hash: str | None
    input_count: int | None
    h0_count: int | None
    h1_count: int | None
    h2_count: int | None
    h3_count: int | None
    h4_count: int | None
    h5_count: int | None
    h6_count: int | None
    h7_count: int | None
    preimage_digest: str | None
    eligibility_action_gate_digest: str | None
    mutation_digest: str | None
    business_postimage_digest: str | None
    actor_scope: str
    reason_code: str
    idempotency_key_digest: str
    receipt_id: UUID
    audit_id: UUID
    occurred_at: datetime

    def as_parameters(self) -> tuple[object, ...]:
        return (
            self.operation,
            self.batch_ref,
            self.item_ref,
            self.subject_user_ref,
            self.primary_class,
            self.secondary_flags,
            self.expected_version,
            self.expected_target_state_digest,
            self.classification_rule_hash,
            self.snapshot_ref_hash,
            self.input_count,
            self.h0_count,
            self.h1_count,
            self.h2_count,
            self.h3_count,
            self.h4_count,
            self.h5_count,
            self.h6_count,
            self.h7_count,
            self.preimage_digest,
            self.eligibility_action_gate_digest,
            self.mutation_digest,
            self.business_postimage_digest,
            self.actor_scope,
            self.reason_code,
            self.idempotency_key_digest,
            self.receipt_id,
            self.audit_id,
            self.occurred_at,
        )

    @classmethod
    def plan_batch(
        cls,
        *,
        batch_ref: UUID,
        classification_rule_hash: str,
        snapshot_ref_hash: str,
        class_counts: Mapping[str, int],
        actor_scope: str,
        idempotency_key_digest: str,
        receipt_id: UUID,
        audit_id: UUID,
        occurred_at: datetime,
    ) -> LedgerWriteRequest:
        counts = tuple(int(class_counts[f"H{index}"]) for index in range(8))
        return cls(
            operation="PLAN_BATCH",
            batch_ref=batch_ref,
            item_ref=None,
            subject_user_ref=None,
            primary_class=None,
            secondary_flags=None,
            expected_version=None,
            expected_target_state_digest=None,
            classification_rule_hash=classification_rule_hash,
            snapshot_ref_hash=snapshot_ref_hash,
            input_count=sum(counts),
            h0_count=counts[0],
            h1_count=counts[1],
            h2_count=counts[2],
            h3_count=counts[3],
            h4_count=counts[4],
            h5_count=counts[5],
            h6_count=counts[6],
            h7_count=counts[7],
            preimage_digest=None,
            eligibility_action_gate_digest=None,
            mutation_digest=None,
            business_postimage_digest=None,
            actor_scope=actor_scope,
            reason_code="A2_BATCH_PLANNED",
            idempotency_key_digest=idempotency_key_digest,
            receipt_id=receipt_id,
            audit_id=audit_id,
            occurred_at=occurred_at,
        )


class IdentityRemediationLedgerRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def write(self, request: LedgerWriteRequest) -> LedgerWriteResult:
        parameters = {
            f"p{index}": value
            for index, value in enumerate(request.as_parameters())
        }
        result = await self._connection.execute(_WRITER_SQL, parameters)
        row = result.mappings().one()
        return LedgerWriteResult(
            target_type=str(row["target_type"]),
            target_ref=row["target_ref"],
            state=str(row["state"]),
            version=int(row["version"]),
            result_code=str(row["result_code"]),
            receipt_id=row["receipt_id"],
            state_digest=str(row["state_digest"]),
        )

    async def confirm(
        self,
        request: LedgerWriteRequest,
        expectation: LedgerExpectation,
    ) -> CommitOutcome:
        values = (*request.as_parameters(), expectation.state, expectation.version, expectation.result_code)
        parameters = {f"p{index}": value for index, value in enumerate(values)}
        result = await self._connection.execute(_CONFIRMATION_SQL, parameters)
        value = str(result.scalar_one())
        try:
            return CommitOutcome(value)
        except ValueError:
            return CommitOutcome.UNKNOWN
