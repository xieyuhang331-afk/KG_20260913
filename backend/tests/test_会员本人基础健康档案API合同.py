from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


def _client() -> tuple[TestClient, object]:
    from app.core.database import get_db_session
    from app.main import create_app

    app = create_app()
    session = object()

    async def fake_session():
        yield session

    app.dependency_overrides[get_db_session] = fake_session
    return TestClient(app), session


def _jwt_headers(*, user_id: int = 1001, role: str = "member") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _result(*, outcome: str | None = None):
    from app.modules.user_health.schemas import (
        MemberSelfHealthProfileData,
        MemberSelfHealthProfileResult,
    )

    return MemberSelfHealthProfileResult(
        state="COMPLETE",
        version=datetime(2026, 8, 8, 1, 2, 3, tzinfo=timezone.utc),
        profile=MemberSelfHealthProfileData(
            gender="female",
            birth_date=date(1990, 1, 1),
            height=Decimal("165.5"),
            weight=Decimal("55.0"),
            blood_type="A",
        ),
        bmi=Decimal("20.1"),
        outcome=outcome,
    )


def _write_payload() -> dict[str, object]:
    return {
        "gender": "female",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": "55.0",
        "blood_type": "A",
        "expected_version": None,
    }


def test_会员本人基础健康档案API尚未实现() -> None:
    from app.main import create_app

    paths = create_app().openapi()["paths"]
    self_profile = paths.get("/api/v1/users/me/health-profile", {})
    if "get" not in self_profile or "put" not in self_profile:
        pytest.fail("Member self basic health profile API is not implemented")


def test_GET只使用JWT主体且不接受目标用户ID() -> None:
    client, session = _client()

    async def get_profile(session_arg, *, user_id):
        assert session_arg is session
        assert user_id == 1001
        return _result()

    with patch(
        "app.modules.user_health.api.get_member_self_health_profile",
        new=AsyncMock(side_effect=get_profile),
    ):
        response = client.get(
            "/api/v1/users/me/health-profile",
            headers=_jwt_headers(user_id=1001),
        )

    assert response.status_code == 200
    assert response.json()["data"]["state"] == "COMPLETE"
    assert "user_id" not in response.json()["data"]


def test_PUT只使用JWT主体并传递乐观锁版本() -> None:
    client, session = _client()

    async def put_profile(
        session_arg,
        *,
        confirmation_session_factory_provider,
        user_id,
        payload,
    ):
        assert session_arg is session
        assert callable(confirmation_session_factory_provider)
        assert user_id == 1001
        assert payload.gender == "female"
        assert payload.expected_version is None
        return _result(outcome="CREATED")

    with patch(
        "app.modules.user_health.api.put_member_self_health_profile",
        new=AsyncMock(side_effect=put_profile),
    ):
        response = client.put(
            "/api/v1/users/me/health-profile",
            json=_write_payload(),
            headers=_jwt_headers(user_id=1001),
        )

    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == "CREATED"
    assert "user_id" not in response.json()["data"]["profile"]


@pytest.mark.parametrize(
    "role",
    ["super_admin", "province_admin", "city_admin", "org_admin", "therapist"],
)
def test_非member角色不能访问本人档案API(role: str) -> None:
    client, _ = _client()
    get_mock = AsyncMock()
    put_mock = AsyncMock()

    with (
        patch("app.modules.user_health.api.get_member_self_health_profile", new=get_mock),
        patch("app.modules.user_health.api.put_member_self_health_profile", new=put_mock),
    ):
        get_response = client.get(
            "/api/v1/users/me/health-profile",
            headers=_jwt_headers(role=role),
        )
        put_response = client.put(
            "/api/v1/users/me/health-profile",
            json=_write_payload(),
            headers=_jwt_headers(role=role),
        )

    assert get_response.status_code == 403
    assert put_response.status_code == 403
    get_mock.assert_not_awaited()
    put_mock.assert_not_awaited()


def test_响应不暴露身份租户机构或实名字段() -> None:
    client, _ = _client()

    with patch(
        "app.modules.user_health.api.get_member_self_health_profile",
        new=AsyncMock(return_value=_result()),
    ):
        response = client.get(
            "/api/v1/users/me/health-profile",
            headers=_jwt_headers(),
        )

    serialized = response.text.lower()
    for forbidden in (
        "user_id",
        "id_card",
        "phone",
        "verification",
        "tenant_id",
        "org_id",
    ):
        assert forbidden not in serialized


def test_请求体拒绝客户端指定user_id() -> None:
    client, _ = _client()
    payload = _write_payload()
    payload["user_id"] = 2002

    with patch(
        "app.modules.user_health.api.put_member_self_health_profile",
        new=AsyncMock(return_value=_result(outcome="CREATED")),
    ) as service_mock:
        response = client.put(
            "/api/v1/users/me/health-profile",
            json=payload,
            headers=_jwt_headers(user_id=1001),
        )

    assert response.status_code == 422
    service_mock.assert_not_awaited()


def test_未认证调用被拒绝() -> None:
    client, _ = _client()

    assert client.get("/api/v1/users/me/health-profile").status_code == 401
    assert client.put(
        "/api/v1/users/me/health-profile",
        json=_write_payload(),
    ).status_code == 401
