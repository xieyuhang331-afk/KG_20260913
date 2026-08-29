from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID


class BusinessClockPort(Protocol):
    def now(self) -> datetime: ...


class ExportArchiveAccessPort(Protocol):
    async def __call__(
        self,
        session,
        *,
        user_id: int,
        file_id: str,
        reason_code: str,
        expires_at: int,
        token_id: str,
    ) -> str: ...


class ServiceFulfillmentRepositoryPort(Protocol):
    async def authority(
        self,
        operation: str,
        target_id: UUID,
        actor_user_id: int,
        actor_role: str,
        actor_tenant_id: int | None,
    ) -> dict | None: ...

    async def replay(
        self,
        actor_user_id: int,
        operation: str,
        idempotency_key: str,
        request_digest: str,
    ) -> dict | None: ...

    async def mutate(self, operation: str, payload: Mapping[str, object]) -> dict: ...

    async def confirm_mutation_outcome(
        self, expected: Mapping[str, object]
    ) -> dict: ...

    async def read_one(
        self, resource: str, target_id: UUID, actor_user_id: int, actor_role: str
    ) -> dict | None: ...

    async def read_many(
        self,
        resource: str,
        scope_id: UUID | None,
        actor_user_id: int,
        actor_role: str,
        cursor_id: UUID | None,
        snapshot_ceiling: UUID | None,
        limit: int,
        status_filter: str | None,
        risk_filter: str | None,
    ) -> tuple[list[dict], UUID | None]: ...

    async def claim_work(self, kind: str, worker_id: str, limit: int) -> list[dict]: ...

    async def claim_export(self, export_id: UUID, worker_id: str) -> dict | None: ...

    async def export_snapshot(self, export_id: UUID) -> dict: ...

    async def export_source_file(self, export_id: UUID, file_id: UUID) -> dict: ...

    async def bind_export_artifact(self, payload: Mapping[str, object]) -> dict: ...

    async def confirm_export_ready(self, payload: Mapping[str, object]) -> bool: ...

    async def consume_export_download(self, payload: Mapping[str, object]) -> bool: ...
    async def confirm_export_download(self, payload: Mapping[str, object]) -> bool: ...

    async def fail_export(self, payload: Mapping[str, object]) -> dict: ...

    async def recover_export(self, payload: Mapping[str, object]) -> dict | None: ...

    async def claim_export_cleanup(self, cutoff: datetime, limit: int) -> list[dict]: ...

    async def complete_export_cleanup(self, payload: Mapping[str, object]) -> bool: ...
