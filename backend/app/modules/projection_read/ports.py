from datetime import date, datetime
from typing import Protocol

from .domain import (
    HealthFactCursor, HealthProjectionFactDTO, HealthProjectionSelectionDTO,
    HealthReadGrant, HealthSelectionCursor, OrganizationChildCursor,
    OrganizationProjectionNodeDTO, OrganizationReadGrant, ProjectionPageDTO,
    ProjectionReadPrincipal,
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
