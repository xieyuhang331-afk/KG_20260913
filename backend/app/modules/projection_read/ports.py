from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from .domain import (
    HealthFactCursor, HealthProjectionFactDTO, HealthProjectionSelectionDTO,
    HealthReadGrant, HealthSelectionCursor, OrganizationChildCursor,
    OrganizationProjectionNodeDTO, OrganizationReadGrant, ProjectionPageDTO,
    ProjectionReadPrincipal,
    HealthProjectionCoverageToken, ReadyHealthProjectionEvidence,
    MemberHealthProjectionFactDTO,
)


class ProjectionAccessPolicyPort(Protocol):
    async def authorize_organization_read(
        self, *, principal: ProjectionReadPrincipal, generation_id: int,
        organization_id: int | None,
    ) -> OrganizationReadGrant: ...

    async def authorize_health_read(
        self, *, principal: ProjectionReadPrincipal, generation_id: int,
        subject_user_id: int, requested_indicator_codes: tuple[str, ...],
    ) -> HealthReadGrant: ...


class OrganizationProjectionReadPort(Protocol):
    async def get_node(self, *, generation_id: int, organization_id: int, principal: ProjectionReadPrincipal) -> OrganizationProjectionNodeDTO: ...
    async def list_children(self, *, generation_id: int, parent_id: int, cursor: OrganizationChildCursor | None, limit: int, principal: ProjectionReadPrincipal) -> ProjectionPageDTO: ...
    async def get_path(self, *, generation_id: int, organization_id: int, principal: ProjectionReadPrincipal) -> tuple[OrganizationProjectionNodeDTO, ...]: ...


class HealthProjectionReadPort(Protocol):
    async def list_current_facts(self, *, generation_id: int, subject_user_id: int, indicator_codes: tuple[str, ...], measured_from: datetime, measured_to: datetime, cursor: HealthFactCursor | None, limit: int, principal: ProjectionReadPrincipal) -> ProjectionPageDTO[HealthProjectionFactDTO, HealthFactCursor]: ...
    async def list_daily_selections(self, *, generation_id: int, subject_user_id: int, indicator_codes: tuple[str, ...], business_day_from: date, business_day_to: date, cursor: HealthSelectionCursor | None, limit: int, principal: ProjectionReadPrincipal) -> ProjectionPageDTO[HealthProjectionSelectionDTO, HealthSelectionCursor]: ...


class HealthProjectionCoverageAuthorityPort(Protocol):
    async def capture(self, *, subject_member_id: UUID, required_indicator_codes: tuple[str, ...]) -> HealthProjectionCoverageToken: ...


class LatestReadyHealthProjectionResolverPort(Protocol):
    async def resolve(self, *, coverage_token: HealthProjectionCoverageToken, required_projection_version: int = 2) -> ReadyHealthProjectionEvidence | None: ...


class MemberHealthProjectionReadPort(Protocol):
    async def list_current_facts(self, *, resolved_generation: ReadyHealthProjectionEvidence, subject_member_id: UUID, indicator_codes: tuple[str, ...], measured_from: datetime, measured_to: datetime, limit: int) -> ProjectionPageDTO[MemberHealthProjectionFactDTO, None]: ...
