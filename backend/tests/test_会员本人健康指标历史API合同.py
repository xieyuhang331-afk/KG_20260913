from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def _configure(monkeypatch) -> None:
    monkeypatch.setenv("KG_DATABASE_PASSWORD", "process-only-test-secret")
    monkeypatch.setenv("KG_JWT_SECRET_KEY", "process-only-jwt-test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()


def _client(monkeypatch):
    _configure(monkeypatch)
    from app.core.database import get_db_session
    from app.main import create_app

    app = create_app()
    session = object()

    async def fake_session():
        yield session

    app.dependency_overrides[get_db_session] = fake_session
    return TestClient(app), session


def _headers(*, user_id=1001, role="member"):
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token({'sub': str(user_id), 'role': role})}"}


def test_会员本人健康指标历史API尚未实现(monkeypatch) -> None:
    _configure(monkeypatch)
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    history = paths.get("/api/v1/users/me/health-indicators", {})
    latest = paths.get("/api/v1/users/me/health-indicators/latest", {})
    if "get" not in history or "get" not in latest:
        pytest.fail("Member self health indicator history API is not implemented")


def test_历史API只使用JWT主体并传递稳定分页参数(monkeypatch) -> None:
    from app.modules.user_health.schemas import MemberSelfHealthIndicatorPage

    client, session = _client(monkeypatch)
    service = AsyncMock(
        return_value=MemberSelfHealthIndicatorPage(state="EMPTY", items=[], next_cursor=None)
    )
    with patch("app.modules.user_health.api.list_member_self_health_indicators", new=service):
        response = client.get(
            "/api/v1/users/me/health-indicators",
            params={"indicator_type": "systolic_bp", "limit": 25, "cursor": "opaque"},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["data"] == {"state": "EMPTY", "items": [], "next_cursor": None}
    kwargs = service.await_args.kwargs
    assert service.await_args.args == (session,)
    assert kwargs["user_id"] == 1001
    assert kwargs["indicator_type"] == "systolic_bp"
    assert kwargs["limit"] == 25
    assert kwargs["cursor"] == "opaque"


def test_latest只使用JWT主体且响应不暴露主体或临床解释(monkeypatch) -> None:
    from app.modules.user_health.schemas import (
        MemberSelfHealthIndicatorItem,
        MemberSelfHealthIndicatorLatest,
    )

    client, session = _client(monkeypatch)
    result = MemberSelfHealthIndicatorLatest(
        state="AVAILABLE",
        items=[
            MemberSelfHealthIndicatorItem(
                id=7,
                batch_id=None,
                indicator_type="systolic_bp",
                value=Decimal("120.00"),
                unit="mmHg",
                source="APP",
                recorded_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
            )
        ],
    )
    service = AsyncMock(return_value=result)
    with patch("app.modules.user_health.api.get_member_self_latest_health_indicators", new=service):
        response = client.get(
            "/api/v1/users/me/health-indicators/latest",
            headers=_headers(),
        )

    assert response.status_code == 200
    service.assert_awaited_once_with(session, user_id=1001)
    serialized = response.text.lower()
    for forbidden in (
        "user_id",
        "plan_id",
        "phone",
        "id_card",
        "tenant_id",
        "org_id",
        "risk_level",
        "diagnosis",
        "normal_range",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("role", ["super_admin", "province_admin", "city_admin", "org_admin", "therapist"])
def test_非member角色在服务调用前拒绝(monkeypatch, role) -> None:
    client, _ = _client(monkeypatch)
    history = AsyncMock()
    latest = AsyncMock()
    with (
        patch("app.modules.user_health.api.list_member_self_health_indicators", new=history),
        patch("app.modules.user_health.api.get_member_self_latest_health_indicators", new=latest),
    ):
        assert client.get("/api/v1/users/me/health-indicators", headers=_headers(role=role)).status_code == 403
        assert client.get("/api/v1/users/me/health-indicators/latest", headers=_headers(role=role)).status_code == 403
    history.assert_not_awaited()
    latest.assert_not_awaited()


def test_未认证调用拒绝且不触发服务(monkeypatch) -> None:
    client, _ = _client(monkeypatch)
    service = AsyncMock()
    with patch("app.modules.user_health.api.list_member_self_health_indicators", new=service):
        response = client.get("/api/v1/users/me/health-indicators")
    assert response.status_code == 401
    service.assert_not_awaited()
