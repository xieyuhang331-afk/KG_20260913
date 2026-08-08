from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps

import pytest


def _async_test(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


@_async_test
async def test_用户状态查询只选择授权所需投影() -> None:
    from app.modules.user_health.repository import get_member_profile_user_state

    captured = []

    class Result:
        def one_or_none(self):
            return None

    class Session:
        async def execute(self, statement):
            captured.append(statement)
            return Result()

    assert await get_member_profile_user_state(Session(), 1001) is None
    selected = {column.key for column in captured[0].selected_columns}
    assert selected == {"id", "role", "status", "verify_status"}
    rendered = str(captured[0]).lower()
    assert "phone" not in rendered
    assert "id_card" not in rendered
    assert "password_hash" not in rendered


@_async_test
async def test_条件更新同时绑定user_id与expected_updated_at() -> None:
    from app.modules.user_health.repository import update_health_profile_record

    captured = []

    class Mappings:
        def one_or_none(self):
            return None

    class Result:
        def mappings(self):
            return Mappings()

    class Session:
        async def execute(self, statement):
            captured.append(statement)
            return Result()

    version = datetime(2026, 8, 8, tzinfo=timezone.utc)
    result = await update_health_profile_record(
        Session(),
        user_id=1001,
        expected_updated_at=version,
        profile_data={"weight": Decimal("56.0")},
        updated_at=version,
    )

    assert result is None
    rendered = str(captured[0]).lower()
    assert "health_profile.user_id" in rendered
    assert "health_profile.updated_at" in rendered
    assert "returning" in rendered
