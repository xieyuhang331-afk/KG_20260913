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


def _headers(*, role="member"):
    from app.core.security import create_access_token

    token = create_access_token({"sub": "1001", "role": role})
    return {"Authorization": f"Bearer {token}"}


def test_会员本人健康指标趋势API尚未实现(monkeypatch) -> None:
    _configure(monkeypatch)
    from app.main import create_app

    trend = create_app().openapi()["paths"].get("/api/v1/users/me/health-trends", {})
    if "get" not in trend:
        pytest.fail("Member self health indicator trend API is not implemented")


def test_趋势API只使用JWT主体并返回原始事实(monkeypatch) -> None:
    from app.modules.health_analysis.schemas import MemberSelfHealthTrend, TrendPoint

    client, session = _client(monkeypatch)
    result = MemberSelfHealthTrend(
        state="AVAILABLE",
        indicator_type="systolic_bp",
        unit="mmHg",
        points=[
            TrendPoint(
                value=Decimal("120.00"),
                recorded_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
                source="APP",
            )
        ],
    )
    service = AsyncMock(return_value=result)
    with patch("app.modules.health_analysis.api.get_member_self_health_trend", new=service):
        response = client.get(
            "/api/v1/users/me/health-trends",
            params={"indicator_type": "systolic_bp", "limit": 20},
            headers=_headers(),
        )
    assert response.status_code == 200
    kwargs = service.await_args.kwargs
    assert service.await_args.args == (session,)
    assert kwargs["user_id"] == 1001
    assert kwargs["indicator_type"] == "systolic_bp"
    assert kwargs["limit"] == 20
    serialized = response.text.lower()
    for forbidden in ("user_id", "display_name", "category", "risk", "diagnosis", "threshold", "normal_range"):
        assert forbidden not in serialized


@pytest.mark.parametrize("role", ["super_admin", "province_admin", "city_admin", "org_admin", "therapist"])
def test_非member角色不能调用本人趋势(monkeypatch, role) -> None:
    client, _ = _client(monkeypatch)
    service = AsyncMock()
    with patch("app.modules.health_analysis.api.get_member_self_health_trend", new=service):
        response = client.get(
            "/api/v1/users/me/health-trends",
            params={"indicator_type": "systolic_bp"},
            headers=_headers(role=role),
        )
    assert response.status_code == 403
    service.assert_not_awaited()


def test_趋势查询要求indicator_type且拒绝非法时间范围(monkeypatch) -> None:
    client, _ = _client(monkeypatch)
    service = AsyncMock()
    with patch("app.modules.health_analysis.api.get_member_self_health_trend", new=service):
        missing = client.get("/api/v1/users/me/health-trends", headers=_headers())
    assert missing.status_code == 422
    service.assert_not_awaited()
