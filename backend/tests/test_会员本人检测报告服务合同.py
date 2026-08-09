from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException


def _user(**overrides):
    values = {"id": 7, "role": "member", "status": "active", "verify_status": "verified"}
    values.update(overrides)
    return SimpleNamespace(**values)


def _row(**overrides):
    values = {
        "id": 11,
        "report_type": "store_retest",
        "detection_time": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "view_status": "unread",
        "summary": None,
        "report_schema_version": 1,
        "report_data": {
            "metrics": [
                {"indicator_code": "systolic_bp", "value": "120", "unit": "mmHg"}
            ]
        },
        "is_initial_baseline": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_列表复核currentness并稳定分页(monkeypatch) -> None:
    from app.modules.user_health import service

    monkeypatch.setattr(service, "_detection_report_cursor_key", lambda: b"cursor-test-key")
    rows = [_row(id=12, is_initial_baseline=False), _row(id=11)]
    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    query = AsyncMock(return_value=rows)
    monkeypatch.setattr(service, "list_member_detection_reports", query)

    result = asyncio.run(
        service.list_member_self_detection_reports_service(
            object(), user_id=7, report_type=None, start_at=None, end_at=None, limit=1, cursor=None
        )
    )

    assert result.state == "AVAILABLE"
    assert [item.report_id for item in result.items] == [12]
    assert result.next_cursor
    query.assert_awaited_once()


def test_检测报告cursor防篡改且查询时间必须带时区(monkeypatch) -> None:
    from app.modules.user_health import service

    monkeypatch.setattr(service, "_detection_report_cursor_key", lambda: b"cursor-test-key")
    valid = service._encode_detection_report_cursor(_row())
    tampered = valid[:-1] + ("A" if valid[-1] != "A" else "B")

    with pytest.raises(HTTPException) as cursor_failure:
        asyncio.run(
            service.list_member_self_detection_reports_service(
                object(),
                user_id=7,
                report_type=None,
                start_at=None,
                end_at=None,
                limit=20,
                cursor=tampered,
            )
        )
    assert (cursor_failure.value.status_code, cursor_failure.value.detail) == (
        422,
        "DETECTION_REPORT_QUERY_INVALID",
    )

    with pytest.raises(HTTPException) as time_failure:
        asyncio.run(
            service.list_member_self_detection_reports_service(
                object(),
                user_id=7,
                report_type=None,
                start_at=datetime(2026, 8, 9),
                end_at=None,
                limit=20,
                cursor=None,
            )
        )
    assert (time_failure.value.status_code, time_failure.value.detail) == (
        422,
        "DETECTION_REPORT_QUERY_INVALID",
    )


def test_详情只输出冻结metrics且他人报告统一不存在(monkeypatch) -> None:
    from app.modules.user_health import service

    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    lookup = AsyncMock(return_value=_row())
    monkeypatch.setattr(service, "get_member_detection_report", lookup)
    session = object()
    result = asyncio.run(
        service.get_member_self_detection_report_service(session, user_id=7, report_id=11)
    )
    assert result.metrics[0].model_dump() == {
        "indicator_code": "systolic_bp",
        "value": result.metrics[0].value,
        "unit": "mmHg",
        "measured_at": None,
    }
    lookup.assert_awaited_once_with(session, user_id=7, report_id=11)

    lookup.return_value = None
    with pytest.raises(HTTPException) as failure:
        asyncio.run(
            service.get_member_self_detection_report_service(session, user_id=7, report_id=99)
        )
    assert (failure.value.status_code, failure.value.detail) == (404, "DETECTION_REPORT_NOT_FOUND")


def test_未知report_data字段fail_closed(monkeypatch) -> None:
    from app.modules.user_health import service

    monkeypatch.setattr(service, "get_member_profile_user_state", AsyncMock(return_value=_user()))
    unsafe = _row(report_data={"metrics": [], "raw_payload": {"unexpected": True}})
    monkeypatch.setattr(service, "get_member_detection_report", AsyncMock(return_value=unsafe))
    with pytest.raises(HTTPException) as failure:
        asyncio.run(
            service.get_member_self_detection_report_service(object(), user_id=7, report_id=11)
        )
    assert (failure.value.status_code, failure.value.detail) == (
        409,
        "DETECTION_REPORT_CONTENT_INCONSISTENT",
    )


def test_非当前会员拒绝且Cancellation原样传播(monkeypatch) -> None:
    from app.modules.user_health import service

    monkeypatch.setattr(
        service,
        "get_member_profile_user_state",
        AsyncMock(return_value=_user(status="disabled")),
    )
    with pytest.raises(HTTPException) as failure:
        asyncio.run(
            service.list_member_self_detection_reports_service(
                object(), user_id=7, report_type=None, start_at=None, end_at=None, limit=20, cursor=None
            )
        )
    assert (failure.value.status_code, failure.value.detail) == (
        403,
        "MEMBER_DETECTION_REPORT_ACCESS_DENIED",
    )

    monkeypatch.setattr(
        service,
        "get_member_profile_user_state",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.list_member_self_detection_reports_service(
                object(), user_id=7, report_type=None, start_at=None, end_at=None, limit=20, cursor=None
            )
        )
