from datetime import UTC, datetime, timedelta

from app.modules.organization_projection.service import classify_organization_post_hwm
from app.modules.health_projection.service import classify_health_post_hwm


def test_Organization高水位后新增与合法版本推进仅为INFORMATIONAL():
    completed = datetime(2026, 8, 13, tzinfo=UTC)
    assert classify_organization_post_hwm(source_id=102, max_id=101, source_version=1, projected_version=None, updated_at=completed) == "ORG_POST_HWM_NEW_SOURCE"
    assert classify_organization_post_hwm(source_id=100, max_id=101, source_version=2, projected_version=1, updated_at=completed + timedelta(seconds=1)) == "ORG_POST_BUILD_VALID_VERSION_ADVANCE"
    assert classify_organization_post_hwm(source_id=100, max_id=101, source_version=1, projected_version=1, updated_at=completed + timedelta(seconds=1)) == "ORG_UNPROVEN_SOURCE_DRIFT"


def test_Health高水位后事实不进入当前generation():
    assert classify_health_post_hwm(fact_id=502, max_fact_id=501) == "HEALTH_POST_HWM_NEW_FACT"
    assert classify_health_post_hwm(fact_id=501, max_fact_id=501) is None
