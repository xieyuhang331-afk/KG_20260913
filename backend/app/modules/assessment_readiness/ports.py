from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.projection_read.ports import (
    HealthProjectionCoverageAuthorityPort,
    LatestReadyHealthProjectionResolverPort,
    MemberHealthProjectionReadPort,
)

__all__ = (
    "AssessmentReadinessRepositoryPort",
    "HealthProjectionCoverageAuthorityPort",
    "LatestReadyHealthProjectionResolverPort",
    "MemberHealthProjectionReadPort",
)


class AssessmentReadinessRepositoryPort(Protocol):
    async def current_readiness(self, service_case_id: UUID) -> dict | None: ...
