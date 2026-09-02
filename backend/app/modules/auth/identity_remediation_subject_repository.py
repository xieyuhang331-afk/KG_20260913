from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_WORKSET_SQL = text(
    "SELECT * FROM identity.a2_identity_remediation_subject_workset_v1("
    ":batch_ref,:version,:state_digest,:classification_hash,:snapshot_hash)"
)
_MATERIAL_SQL = text(
    "SELECT * FROM identity.a2_identity_remediation_h3_material_v1("
    ":batch_ref,:item_ref,:version,:state_digest)"
)
_MUTATION_SQL = text(
    "SELECT * FROM identity.a2_identity_remediation_h3_clear_legacy_v1("
    ":batch_ref,:item_ref,:version,:state_digest,:chain_digest,:gate_digest,:reason_code)"
)


@dataclass(frozen=True, slots=True)
class WorksetSubject:
    subject_user_ref: int = field(repr=False)
    primary_class: str
    secondary_flags: int
    preimage_digest: str


@dataclass(frozen=True, slots=True)
class H3Material:
    subject_user_ref: int = field(repr=False)
    legacy_real_name: str = field(repr=False)
    legacy_id_card: str = field(repr=False)
    submission_id: UUID
    submission_version: int
    real_name_ciphertext: bytes = field(repr=False)
    real_name_nonce: bytes = field(repr=False)
    id_card_ciphertext: bytes = field(repr=False)
    id_card_nonce: bytes = field(repr=False)
    encryption_key_id: str = field(repr=False)
    formal_content_digest: str = field(repr=False)
    formal_id_card_digest: str = field(repr=False)
    decision_ref: UUID
    decision_facts_version: int
    claim_id: UUID
    claim_version: int
    consent_version: str = field(repr=False)
    chain_digest: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class H3MutationResult:
    mutation_digest: str
    postimage_digest: str


class IdentityRemediationSubjectRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def fetch_workset(
        self,
        *,
        batch_ref: UUID,
        expected_batch_version: int,
        expected_batch_state_digest: str,
        expected_classification_rule_hash: str,
        expected_snapshot_ref_hash: str,
    ) -> tuple[WorksetSubject, ...]:
        result = await self._connection.execute(
            _WORKSET_SQL,
            {
                "batch_ref": batch_ref,
                "version": expected_batch_version,
                "state_digest": expected_batch_state_digest,
                "classification_hash": expected_classification_rule_hash,
                "snapshot_hash": expected_snapshot_ref_hash,
            },
        )
        return tuple(
            WorksetSubject(
                subject_user_ref=int(row["subject_user_ref"]),
                primary_class=str(row["primary_class"]),
                secondary_flags=int(row["secondary_flags"]),
                preimage_digest=str(row["preimage_digest"]),
            )
            for row in result.mappings()
        )

    async def fetch_h3_material(
        self,
        *,
        batch_ref: UUID,
        item_ref: UUID,
        expected_item_version: int,
        expected_item_state_digest: str,
    ) -> H3Material:
        result = await self._connection.execute(
            _MATERIAL_SQL,
            {
                "batch_ref": batch_ref,
                "item_ref": item_ref,
                "version": expected_item_version,
                "state_digest": expected_item_state_digest,
            },
        )
        row = result.mappings().one()
        return H3Material(
            subject_user_ref=int(row["subject_user_ref"]),
            legacy_real_name=str(row["legacy_real_name"]),
            legacy_id_card=str(row["legacy_id_card"]),
            submission_id=row["submission_id"],
            submission_version=int(row["submission_version"]),
            real_name_ciphertext=bytes(row["real_name_ciphertext"]),
            real_name_nonce=bytes(row["real_name_nonce"]),
            id_card_ciphertext=bytes(row["id_card_ciphertext"]),
            id_card_nonce=bytes(row["id_card_nonce"]),
            encryption_key_id=str(row["encryption_key_id"]),
            formal_content_digest=str(row["formal_content_digest"]),
            formal_id_card_digest=str(row["formal_id_card_digest"]),
            decision_ref=row["decision_ref"],
            decision_facts_version=int(row["decision_facts_version"]),
            claim_id=row["claim_id"],
            claim_version=int(row["claim_version"]),
            consent_version=str(row["consent_version"]),
            chain_digest=str(row["chain_digest"]),
        )

    async def clear_h3_legacy(
        self,
        *,
        batch_ref: UUID,
        item_ref: UUID,
        expected_item_version: int,
        expected_item_state_digest: str,
        expected_chain_digest: str,
        exact_match_gate_digest: str,
    ) -> H3MutationResult:
        result = await self._connection.execute(
            _MUTATION_SQL,
            {
                "batch_ref": batch_ref,
                "item_ref": item_ref,
                "version": expected_item_version,
                "state_digest": expected_item_state_digest,
                "chain_digest": expected_chain_digest,
                "gate_digest": exact_match_gate_digest,
                "reason_code": "A2_CANONICAL_MATCH_APPROVED",
            },
        )
        row = result.mappings().one()
        return H3MutationResult(
            mutation_digest=str(row["mutation_digest"]),
            postimage_digest=str(row["postimage_digest"]),
        )
