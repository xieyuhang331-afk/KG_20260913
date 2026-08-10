from __future__ import annotations

from typing import Any, Protocol, Sequence

from .domain import CanonicalHealthFact, CanonicalHealthFactDraft


class HealthFactProducerAuthorityPort(Protocol):
    async def authorize(
        self, draft: CanonicalHealthFactDraft
    ) -> CanonicalHealthFactDraft: ...


class HealthFactIndicatorCatalogPort(Protocol):
    def canonical_unit(self, indicator_code: str) -> str | None: ...


class HealthFactWriterPort(Protocol):
    async def write(self, draft: CanonicalHealthFactDraft) -> Any: ...


class HealthFactReaderPort(Protocol):
    async def get_by_id(self, fact_id: int) -> CanonicalHealthFact | None: ...


class HealthFactRepositoryPort(Protocol):
    async def acquire_semantic_lock(self, lock_key: int) -> None: ...

    async def find_by_semantic_identity(
        self,
        *,
        source_type: str,
        producer_event_key: str,
        source_identity_digests: Sequence[str],
    ) -> CanonicalHealthFact | None: ...

    async def get_by_id(self, fact_id: int) -> CanonicalHealthFact | None: ...

    async def get_successor(
        self, fact_id: int
    ) -> CanonicalHealthFact | None: ...

    async def add(self, fact: CanonicalHealthFact) -> CanonicalHealthFact: ...

    async def add_audit(self, *, fact: CanonicalHealthFact, action: str) -> None: ...

    async def has_audit(self, *, fact_id: int, action: str) -> bool: ...


class HealthFactUnitOfWorkPort(Protocol):
    repository: HealthFactRepositoryPort

    async def __aenter__(self) -> "HealthFactUnitOfWorkPort": ...

    async def __aexit__(self, exc_type, exc, traceback) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
