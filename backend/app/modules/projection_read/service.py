import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime

import sqlalchemy as sa

from app.core.database import get_projection_session_factory

from .domain import (
    HealthFactCursor, HealthSelectionCursor, OrganizationChildCursor,
    OrganizationReadGrant, ProjectionAccessDenied, ProjectionGenerationUnavailable,
    ProjectionIndicatorDenied, ProjectionInvalidRequest, ProjectionPageDTO,
    ProjectionReadPrincipal, ProjectionReadUnavailable, ProjectionScopeDenied,
)
from .repository import HealthProjectionReadRepository, OrganizationProjectionReadRepository


READER_SCHEMA_LOCK_KEY = 4341875042858344256
INDICATOR_CATALOG_V1 = frozenset(("systolic_bp", "diastolic_bp", "heart_rate", "fasting_glucose", "postprandial_glucose_2h", "hba1c", "total_cholesterol", "triglyceride", "hdl_c", "ldl_c", "weight", "bmi", "uric_acid", "spo2", "bone_density_t_score"))
AUTHORIZATION_BASES = frozenset(("SELF", "FAMILY_GRANT", "MANAGED_CUSTOMER", "PLATFORM_DUTY"))


def _identity(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProjectionInvalidRequest()


def _principal(value: ProjectionReadPrincipal) -> None:
    if not isinstance(value, ProjectionReadPrincipal): raise ProjectionInvalidRequest()
    _identity(value.actor_id); _identity(value.tenant_id)
    if not value.actor_type or not value.request_id: raise ProjectionInvalidRequest()


def _limit(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 200:
        raise ProjectionInvalidRequest()


def _organization_cursor(value: OrganizationChildCursor | None) -> None:
    if value is None:
        return
    if (
        type(value) is not OrganizationChildCursor
        or type(value.sort_order) is not int
        or value.sort_order < 0
        or type(value.organization_id) is not int
        or value.organization_id < 1
    ):
        raise ProjectionInvalidRequest() from None


def _health_fact_cursor(
    value: HealthFactCursor | None, start: datetime, end: datetime,
) -> None:
    if value is None:
        return
    if (
        type(value) is not HealthFactCursor
        or type(value.measured_at) is not datetime
        or value.measured_at.utcoffset() is None
        or not start <= value.measured_at < end
        or type(value.fact_id) is not int
        or value.fact_id < 1
    ):
        raise ProjectionInvalidRequest() from None


def _health_selection_cursor(
    value: HealthSelectionCursor | None, start: date, end: date,
    indicators: tuple[str, ...],
) -> None:
    if value is None:
        return
    if (
        type(value) is not HealthSelectionCursor
        or type(value.business_day) is not date
        or not start <= value.business_day < end
        or type(value.indicator_code) is not str
        or value.indicator_code not in INDICATOR_CATALOG_V1
        or value.indicator_code not in indicators
        or type(value.winner_fact_id) is not int
        or value.winner_fact_id < 1
    ):
        raise ProjectionInvalidRequest() from None


def validate_organization_grant(principal: ProjectionReadPrincipal, grant: OrganizationReadGrant) -> None:
    if grant.tenant_id != principal.tenant_id or not grant.allowed_root_organization_ids or len(set(grant.allowed_root_organization_ids)) != len(grant.allowed_root_organization_ids) or any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in grant.allowed_root_organization_ids):
        raise ProjectionAccessDenied()


def validate_health_request(subject_id: int, requested: tuple[str, ...], allowed: tuple[str, ...]) -> tuple[str, ...]:
    _identity(subject_id)
    if not requested or len(set(requested)) != len(requested) or not set(requested).issubset(INDICATOR_CATALOG_V1): raise ProjectionInvalidRequest()
    if not set(requested).issubset(allowed): raise ProjectionIndicatorDenied()
    return requested


async def _authorize(call):
    try:
        return await call
    except asyncio.CancelledError:
        raise
    except (ProjectionInvalidRequest, ProjectionAccessDenied, ProjectionIndicatorDenied, ProjectionScopeDenied):
        raise
    except Exception:
        raise ProjectionReadUnavailable() from None


@asynccontextmanager
async def _reader(kind: str, repository_class):
    try:
        factory = await get_projection_session_factory(kind)
        async with factory() as session:
            async with session.begin():
                await session.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                await session.execute(sa.text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": READER_SCHEMA_LOCK_KEY})
                yield repository_class(session)
    except asyncio.CancelledError:
        raise
    except (ProjectionInvalidRequest, ProjectionAccessDenied, ProjectionGenerationUnavailable, ProjectionIndicatorDenied, ProjectionScopeDenied):
        raise
    except Exception:
        raise ProjectionReadUnavailable() from None


class OrganizationProjectionReadService:
    def __init__(self, policy): self.policy = policy

    async def get_node(self, *, generation_id: int, organization_id: int, principal: ProjectionReadPrincipal):
        _identity(generation_id); _identity(organization_id); _principal(principal)
        grant = await _authorize(self.policy.authorize_organization_read(principal=principal, generation_id=generation_id, organization_id=organization_id))
        validate_organization_grant(principal, grant)
        async with _reader("organization_reader", OrganizationProjectionReadRepository) as repo:
            if await repo.generation(generation_id) is None: raise ProjectionGenerationUnavailable()
            node = await repo.node(generation_id, organization_id, grant.allowed_root_organization_ids, principal.tenant_id, grant.tenant_id)
            if node is None: raise ProjectionScopeDenied()
            return node

    async def list_children(self, *, generation_id: int, parent_id: int, cursor: OrganizationChildCursor | None, limit: int, principal: ProjectionReadPrincipal):
        _identity(generation_id); _identity(parent_id); _limit(limit); _principal(principal)
        _organization_cursor(cursor)
        grant = await _authorize(self.policy.authorize_organization_read(principal=principal, generation_id=generation_id, organization_id=parent_id)); validate_organization_grant(principal, grant)
        async with _reader("organization_reader", OrganizationProjectionReadRepository) as repo:
            generation = await repo.generation(generation_id)
            if generation is None: raise ProjectionGenerationUnavailable()
            rows = await repo.children(generation_id, parent_id, grant.allowed_root_organization_ids, principal.tenant_id, grant.tenant_id, cursor, limit)
            items = rows[:limit]; next_cursor = OrganizationChildCursor(items[-1].sort_order, items[-1].organization_id) if len(rows) > limit else None
            return ProjectionPageDTO(generation, items, next_cursor)

    async def get_path(self, *, generation_id: int, organization_id: int, principal: ProjectionReadPrincipal):
        _identity(generation_id); _identity(organization_id); _principal(principal)
        grant = await _authorize(self.policy.authorize_organization_read(principal=principal, generation_id=generation_id, organization_id=organization_id)); validate_organization_grant(principal, grant)
        async with _reader("organization_reader", OrganizationProjectionReadRepository) as repo:
            if await repo.generation(generation_id) is None: raise ProjectionGenerationUnavailable()
            node = await repo.node(generation_id, organization_id, grant.allowed_root_organization_ids, principal.tenant_id, grant.tenant_id)
            if node is None: raise ProjectionScopeDenied()
            positions = [node.path_ids.index(root) for root in grant.allowed_root_organization_ids if root in node.path_ids]
            if not positions: raise ProjectionScopeDenied()
            authorized_path_ids = node.path_ids[min(positions):]
            path = await repo.path(generation_id, authorized_path_ids, grant.allowed_root_organization_ids, principal.tenant_id, grant.tenant_id)
            if len(path) != len(authorized_path_ids): raise ProjectionScopeDenied()
            return path


class HealthProjectionReadService:
    def __init__(self, policy): self.policy = policy

    async def _grant(self, generation_id, subject_id, indicators, principal):
        _identity(generation_id); _identity(subject_id); _principal(principal)
        grant = await _authorize(self.policy.authorize_health_read(principal=principal, generation_id=generation_id, subject_user_id=subject_id, requested_indicator_codes=indicators))
        if grant.tenant_id != principal.tenant_id or grant.subject_user_id != subject_id or grant.authorization_basis not in AUTHORIZATION_BASES: raise ProjectionAccessDenied()
        validate_health_request(subject_id, indicators, grant.allowed_indicator_codes)
        return grant

    async def list_current_facts(self, *, generation_id: int, subject_user_id: int, indicator_codes: tuple[str, ...], measured_from: datetime, measured_to: datetime, cursor: HealthFactCursor | None, limit: int, principal: ProjectionReadPrincipal):
        _limit(limit)
        if type(measured_from) is not datetime or type(measured_to) is not datetime or measured_from.utcoffset() is None or measured_to.utcoffset() is None or measured_from >= measured_to or (measured_to-measured_from).total_seconds() > 366*86400: raise ProjectionInvalidRequest()
        _health_fact_cursor(cursor, measured_from, measured_to)
        grant = await self._grant(generation_id, subject_user_id, indicator_codes, principal)
        async with _reader("health_reader", HealthProjectionReadRepository) as repo:
            generation = await repo.generation(generation_id)
            if generation is None: raise ProjectionGenerationUnavailable()
            rows = await repo.facts(generation_id, subject_user_id, indicator_codes, measured_from, measured_to, cursor, limit, principal.tenant_id, grant.tenant_id)
            items = rows[:limit]; next_cursor = HealthFactCursor(items[-1].measured_at, items[-1].fact_id) if len(rows) > limit else None
            return ProjectionPageDTO(generation, items, next_cursor)

    async def list_daily_selections(self, *, generation_id: int, subject_user_id: int, indicator_codes: tuple[str, ...], business_day_from: date, business_day_to: date, cursor: HealthSelectionCursor | None, limit: int, principal: ProjectionReadPrincipal):
        _limit(limit)
        if type(business_day_from) is not date or type(business_day_to) is not date or business_day_from >= business_day_to or (business_day_to-business_day_from).days > 366: raise ProjectionInvalidRequest()
        _health_selection_cursor(cursor, business_day_from, business_day_to, indicator_codes)
        grant = await self._grant(generation_id, subject_user_id, indicator_codes, principal)
        async with _reader("health_reader", HealthProjectionReadRepository) as repo:
            generation = await repo.generation(generation_id)
            if generation is None: raise ProjectionGenerationUnavailable()
            rows = await repo.selections(generation_id, subject_user_id, indicator_codes, business_day_from, business_day_to, cursor, limit, principal.tenant_id, grant.tenant_id)
            items = rows[:limit]; next_cursor = HealthSelectionCursor(items[-1].business_day, items[-1].indicator_code, items[-1].winner_fact_id) if len(rows) > limit else None
            return ProjectionPageDTO(generation, items, next_cursor)
