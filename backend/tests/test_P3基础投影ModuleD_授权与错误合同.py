import asyncio
from datetime import date, datetime, timezone

import pytest

from app.modules.projection_read.domain import (
    HealthFactCursor, HealthReadGrant, HealthSelectionCursor, OrganizationChildCursor,
    OrganizationReadGrant, ProjectionAccessDenied, ProjectionGenerationUnavailable,
    ProjectionIndicatorDenied, ProjectionInvalidRequest, ProjectionReadPrincipal,
)
from app.modules.projection_read.service import (
    HealthProjectionReadService, OrganizationProjectionReadService,
    validate_health_request, validate_organization_grant,
)


def test_Organization授权固定active和scope且无inactive开关():
    grant = OrganizationReadGrant(tenant_id=7, allowed_root_organization_ids=(11,))
    assert not hasattr(grant, "allow_inactive")
    principal = ProjectionReadPrincipal(actor_id=1, tenant_id=7, actor_type="platform", request_id="r")
    validate_organization_grant(principal, grant)
    with pytest.raises(ProjectionAccessDenied):
        validate_organization_grant(principal, OrganizationReadGrant(8, (11,)))


def test_Health请求必须单subject和非空完整授权指标():
    with pytest.raises(ProjectionInvalidRequest):
        validate_health_request(1, (), ("heart_rate",))
    with pytest.raises(ProjectionIndicatorDenied):
        validate_health_request(1, ("heart_rate", "spo2"), ("heart_rate",))


@pytest.mark.parametrize(
    "grant",
    (
        HealthReadGrant(8, 2, ("heart_rate",), "SELF"),
        HealthReadGrant(7, 3, ("heart_rate",), "SELF"),
    ),
)
def test_Health租户或subject授权不一致在Runtime前拒绝(grant):
    class _Policy:
        async def authorize_health_read(self, **kwargs):
            return grant

    service = HealthProjectionReadService(_Policy())

    async def verify():
        with pytest.raises(ProjectionAccessDenied):
            await service._grant(1, 2, ("heart_rate",), _principal())

    asyncio.run(verify())


def test_错误码固定且CancelledError不是领域错误():
    assert ProjectionGenerationUnavailable.code == "PROJECTION_GENERATION_UNAVAILABLE"
    assert ProjectionAccessDenied.code == "PROJECTION_ACCESS_DENIED"
    assert not issubclass(asyncio.CancelledError, Exception) or not issubclass(asyncio.CancelledError, ProjectionAccessDenied)


class _NeverPolicy:
    def __init__(self):
        self.calls = 0

    async def authorize_organization_read(self, **kwargs):
        self.calls += 1
        raise AssertionError("invalid cursor reached policy")

    async def authorize_health_read(self, **kwargs):
        self.calls += 1
        raise AssertionError("invalid cursor reached policy")


def _principal():
    return ProjectionReadPrincipal(actor_id=1, tenant_id=7, actor_type="platform", request_id="r")


@pytest.mark.parametrize(
    "cursor",
    (
        OrganizationChildCursor("1", 2),
        OrganizationChildCursor(True, 2),
        OrganizationChildCursor(1, "2"),
        OrganizationChildCursor(1, True),
        OrganizationChildCursor(-1, 2),
        OrganizationChildCursor(1, 0),
    ),
)
def test_Organization非法cursor在Policy和Runtime前固定拒绝(cursor):
    policy = _NeverPolicy()
    service = OrganizationProjectionReadService(policy)

    async def verify():
        with pytest.raises(ProjectionInvalidRequest) as rejected:
            await service.list_children(
                generation_id=1, parent_id=1, cursor=cursor, limit=10,
                principal=_principal(),
            )
        assert rejected.value.code == "PROJECTION_INVALID_REQUEST"
        assert rejected.value.__cause__ is None

    asyncio.run(verify())
    assert policy.calls == 0


@pytest.mark.parametrize(
    "cursor",
    (
        HealthFactCursor("2026-08-14T00:00:00Z", 1),
        HealthFactCursor(datetime(2026, 8, 14), 1),
        HealthFactCursor(datetime(2026, 8, 14, tzinfo=timezone.utc), "1"),
        HealthFactCursor(datetime(2026, 8, 14, tzinfo=timezone.utc), True),
        HealthFactCursor(datetime(2026, 8, 14, tzinfo=timezone.utc), 0),
    ),
)
def test_HealthFact非法cursor在Policy和Runtime前固定拒绝(cursor):
    policy = _NeverPolicy()
    service = HealthProjectionReadService(policy)

    async def verify():
        with pytest.raises(ProjectionInvalidRequest) as rejected:
            await service.list_current_facts(
                generation_id=1, subject_user_id=2,
                indicator_codes=("heart_rate",),
                measured_from=datetime(2026, 8, 1, tzinfo=timezone.utc),
                measured_to=datetime(2026, 8, 15, tzinfo=timezone.utc),
                cursor=cursor, limit=10, principal=_principal(),
            )
        assert rejected.value.code == "PROJECTION_INVALID_REQUEST"
        assert rejected.value.__cause__ is None

    asyncio.run(verify())
    assert policy.calls == 0


@pytest.mark.parametrize(
    "cursor",
    (
        HealthSelectionCursor(datetime(2026, 8, 14, tzinfo=timezone.utc), "heart_rate", 1),
        HealthSelectionCursor(date(2026, 8, 14), 1, 1),
        HealthSelectionCursor(date(2026, 8, 14), "unknown", 1),
        HealthSelectionCursor(date(2026, 8, 14), "spo2", 1),
        HealthSelectionCursor(date(2026, 8, 14), "heart_rate", "1"),
        HealthSelectionCursor(date(2026, 8, 14), "heart_rate", True),
        HealthSelectionCursor(date(2026, 8, 14), "heart_rate", 0),
    ),
)
def test_HealthSelection非法cursor在Policy和Runtime前固定拒绝(cursor):
    policy = _NeverPolicy()
    service = HealthProjectionReadService(policy)

    async def verify():
        with pytest.raises(ProjectionInvalidRequest) as rejected:
            await service.list_daily_selections(
                generation_id=1, subject_user_id=2,
                indicator_codes=("heart_rate",),
                business_day_from=date(2026, 8, 1),
                business_day_to=date(2026, 8, 15), cursor=cursor,
                limit=10, principal=_principal(),
            )
        assert rejected.value.code == "PROJECTION_INVALID_REQUEST"
        assert rejected.value.__cause__ is None

    asyncio.run(verify())
    assert policy.calls == 0
