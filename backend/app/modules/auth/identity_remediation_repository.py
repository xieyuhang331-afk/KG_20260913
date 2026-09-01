from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.auth.identity_remediation import IdentitySnapshotFacts

_SNAPSHOT_SQL = text(
    """
    SELECT * FROM identity.a2_identity_inventory_snapshot_v1()
    """
)


class IdentityRemediationInventoryRepository:
    async def fetch_snapshot(
        self,
        connection: AsyncConnection,
    ) -> Sequence[IdentitySnapshotFacts]:
        result = await connection.execute(_SNAPSHOT_SQL)
        return tuple(
            IdentitySnapshotFacts(
                role=row.role,
                legacy_pii_present=row.legacy_pii_present,
                identity_authority_signal=row.identity_authority_signal,
                formal_chain_complete=row.formal_chain_complete,
                tenant_present=row.tenant_present,
                tenant_relation_known=row.tenant_relation_known,
                self_link_count=row.self_link_count,
                enrollment_count=row.enrollment_count,
                current_enrollment_count=row.current_enrollment_count,
                tenant_matches_unique_current=row.tenant_matches_unique_current,
                enrollment_scope_complete=row.enrollment_scope_complete,
            )
            for row in result
        )
