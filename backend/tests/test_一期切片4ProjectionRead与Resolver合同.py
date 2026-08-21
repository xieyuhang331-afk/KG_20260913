from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest


MEMBER = UUID("00000000-0000-7000-8000-000000000401")


def _fact(n, source, state="VERIFIED", superseded=False):
    from app.modules.health_projection.domain import HealthCurrentFactV2
    return HealthCurrentFactV2(n, UUID(f"00000000-0000-7000-8000-{n:012d}"), MEMBER, None, "weight", Decimal("70"), "kg", datetime(2026, 8, 20, 1, tzinfo=timezone.utc), datetime(2026, 8, 20, 2, tzinfo=timezone.utc), source, state, 7, superseded)


def _item(status=7):
    from app.modules.projection_read.domain import HealthProjectionCoverageItem
    return HealthProjectionCoverageItem("weight", 3, status, 3, 3, "b" * 64, "c" * 64)


def _token(status=7):
    from app.modules.projection_read.domain import HealthProjectionCoverageToken
    return HealthProjectionCoverageToken(MEMBER, "10:20:", 2, "a" * 64, (_item(status),))


def _evidence(generation_id, generation_no, status=7):
    from app.modules.projection_read.domain import ReadyHealthProjectionEvidence
    return ReadyHealthProjectionEvidence(generation_id, generation_no, 2, "health-daily-selection-v2", MEMBER, "10:20:", "a" * 64, (_item(status),))


def test_D16_D17_ModuleE只见projection_read端口且resolver无generation输入():
    import inspect
    from app.modules.projection_read.ports import HealthProjectionCoverageAuthorityPort, LatestReadyHealthProjectionResolverPort, MemberHealthProjectionReadPort
    assert hasattr(HealthProjectionCoverageAuthorityPort, "capture") and hasattr(MemberHealthProjectionReadPort, "list_current_facts")
    assert "generation_id" not in inspect.signature(LatestReadyHealthProjectionResolverPort.resolve).parameters


def test_D18_DEVICE争议与被修正事实不进入v2且STORE优先():
    from app.modules.health_projection.domain import select_window_winner_v2
    from app.modules.organization_projection.domain import ProjectionSourceInvalid
    assert select_window_winner_v2((_fact(1, "APP"), _fact(2, "REPORT"), _fact(3, "STORE"))).source_type == "STORE"
    with pytest.raises(ProjectionSourceInvalid):
        select_window_winner_v2((_fact(1, "APP"), _fact(4, "DEVICE")))
    assert select_window_winner_v2((_fact(1, "APP"), _fact(3, "STORE", "DISPUTED"))).source_type == "APP"
    assert select_window_winner_v2((_fact(1, "APP"), _fact(3, "STORE", superseded=True))).source_type == "APP"


def test_D18_D19_D33_只选双水位完整一致的最大唯一READY():
    from app.modules.projection_read.domain import ProjectionReadUnavailable
    from app.modules.projection_read.service import resolve_latest_ready_generation
    assert resolve_latest_ready_generation(coverage_token=_token(), candidates=(_evidence(10, 1), _evidence(11, 2))).generation_id == 11
    assert resolve_latest_ready_generation(coverage_token=_token(8), candidates=(_evidence(11, 2),)) is None
    with pytest.raises(ProjectionReadUnavailable):
        resolve_latest_ready_generation(coverage_token=_token(), candidates=(_evidence(10, 2), _evidence(11, 2)))
