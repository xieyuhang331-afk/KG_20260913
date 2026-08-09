from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


def _user(**changes):
    values = {"id": 1001, "role": "member", "status": "active", "verify_status": "verified"}
    values.update(changes)
    return SimpleNamespace(**values)


def _indicator(*, row_id, at, indicator_type="systolic_bp", unit="mmHg"):
    return SimpleNamespace(
        id=row_id,
        batch_id=None,
        indicator_type=indicator_type,
        value="120.00",
        unit=unit,
        source="APP",
        recorded_at=at,
    )


def test_历史查询重新核验currentness并生成稳定cursor(monkeypatch) -> None:
    from app.modules.user_health import service

    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    rows = [_indicator(row_id=3, at=now), _indicator(row_id=2, at=now - timedelta(minutes=1))]
    user_reader = AsyncMock(return_value=_user())
    history_reader = AsyncMock(return_value=rows)
    monkeypatch.setattr(service, "get_member_profile_user_state", user_reader)
    monkeypatch.setattr(service, "list_member_health_indicator_history", history_reader)

    result = asyncio.run(
        service.list_member_self_health_indicators(
            object(), user_id=1001, indicator_type=None, start_at=None, end_at=None, limit=1, cursor=None
        )
    )

    assert result.state == "AVAILABLE"
    assert [item.id for item in result.items] == [3]
    assert result.next_cursor is not None
    user_reader.assert_awaited_once()
    assert history_reader.await_args.kwargs["limit"] == 2


@pytest.mark.parametrize(
    "current",
    [None, _user(role="org_admin"), _user(status="disabled"), _user(verify_status="submitted")],
)
def test_非当前有效实名member必须fail_closed且零指标查询(monkeypatch, current) -> None:
    from app.modules.user_health import service

    history_reader = AsyncMock()
    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=current))
    monkeypatch.setattr(service, "list_member_health_indicator_history", history_reader)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            service.list_member_self_health_indicators(
                object(), user_id=1001, indicator_type=None, start_at=None, end_at=None, limit=50, cursor=None
            )
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == "HEALTH_DATA_UNAVAILABLE"
    history_reader.assert_not_awaited()


def test_未知数据库异常固定503并切断供应商异常链(monkeypatch) -> None:
    from app.modules.user_health import service

    secret = "postgresql+asyncpg://unsafe:unsafe@hidden/hidden"
    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    monkeypatch.setattr(
        service,
        "list_member_health_indicator_history",
        AsyncMock(side_effect=RuntimeError(secret)),
    )
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            service.list_member_self_health_indicators(
                object(), user_id=1001, indicator_type=None, start_at=None, end_at=None, limit=50, cursor=None
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail == "HEALTH_DATA_UNAVAILABLE"
    assert caught.value.__cause__ is None
    assert secret not in repr(caught.value)


def test_Cancellation原样传播(monkeypatch) -> None:
    from app.modules.user_health import service

    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    monkeypatch.setattr(
        service,
        "list_member_health_indicator_history",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.list_member_self_health_indicators(
                object(), user_id=1001, indicator_type=None, start_at=None, end_at=None, limit=50, cursor=None
            )
        )


def test_趋势混合单位fail_closed且不做换算(monkeypatch) -> None:
    from app.modules.health_analysis import service

    now = datetime(2026, 8, 9, tzinfo=timezone.utc)
    rows = [
        _indicator(row_id=1, at=now, unit="mmHg"),
        _indicator(row_id=2, at=now + timedelta(minutes=1), unit="kPa"),
    ]
    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    monkeypatch.setattr(service, "list_member_indicator_trend_points", AsyncMock(return_value=rows))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            service.get_member_self_health_trend(
                object(),
                user_id=1001,
                indicator_type="systolic_bp",
                start_at=now,
                end_at=now + timedelta(hours=1),
                limit=200,
            )
        )
    assert caught.value.status_code == 409
    assert caught.value.detail == "HEALTH_INDICATOR_UNIT_INCONSISTENT"


def test_趋势空态与稳定原始事实(monkeypatch) -> None:
    from app.modules.health_analysis import service

    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    reader = AsyncMock(return_value=[])
    monkeypatch.setattr(service, "list_member_indicator_trend_points", reader)
    result = asyncio.run(
        service.get_member_self_health_trend(
            object(),
            user_id=1001,
            indicator_type="custom_fact",
            start_at=None,
            end_at=None,
            limit=200,
        )
    )
    assert result.state == "EMPTY"
    assert result.indicator_type == "custom_fact"
    assert result.unit is None
    assert result.points == []
    assert reader.await_args.kwargs["start_at"] is not None
    assert reader.await_args.kwargs["end_at"] is not None
