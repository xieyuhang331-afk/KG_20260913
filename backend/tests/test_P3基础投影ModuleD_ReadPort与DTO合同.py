from dataclasses import FrozenInstanceError, fields
import inspect

import pytest

from app.modules.projection_read.domain import (
    HealthFactCursor, HealthProjectionFactDTO, HealthProjectionSelectionDTO,
    HealthSelectionCursor,
    OrganizationChildCursor, OrganizationProjectionNodeDTO,
)
from app.modules.projection_read.ports import (
    HealthProjectionReadPort, OrganizationProjectionReadPort,
)


def test_Module_D_DTO不可变且字段最小化():
    assert [f.name for f in fields(OrganizationProjectionNodeDTO)] == [
        "generation_id", "organization_id", "parent_id", "org_code", "org_name",
        "org_type", "status", "sort_order", "path_ids", "path_codes", "scope_eligible",
    ]
    assert [f.name for f in fields(HealthProjectionFactDTO)] == [
        "generation_id", "fact_id", "subject_user_id", "indicator_code", "numeric_value",
        "unit", "measured_at", "received_at", "source_type", "business_day",
    ]
    assert [f.name for f in fields(HealthProjectionSelectionDTO)] == [
        "generation_id", "subject_user_id", "indicator_code", "business_day",
        "winner_fact_id", "rule_version",
    ]
    dto = OrganizationChildCursor(1, 2)
    with pytest.raises(FrozenInstanceError):
        dto.sort_order = 3
    assert fields(HealthFactCursor)


def test_Module_D_ReadPort每个方法显式要求generation_id且不存在latest解析器():
    for port in (OrganizationProjectionReadPort, HealthProjectionReadPort):
        for name, method in inspect.getmembers(port, inspect.isfunction):
            if name.startswith("_"):
                continue
            assert "generation_id" in inspect.signature(method).parameters
            assert "latest" not in name.lower()


def test_Module_D_cursor字段数量精确且不承载授权或PHI():
    assert [field.name for field in fields(OrganizationChildCursor)] == [
        "sort_order", "organization_id",
    ]
    assert [field.name for field in fields(HealthFactCursor)] == [
        "measured_at", "fact_id",
    ]
    assert [field.name for field in fields(HealthSelectionCursor)] == [
        "business_day", "indicator_code", "winner_fact_id",
    ]
