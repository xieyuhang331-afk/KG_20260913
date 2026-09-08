from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os

import asyncpg
import pytest


pytestmark = pytest.mark.integration


def _register(real_db_client, phone: str) -> dict:
    response = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": phone, "password": "Secret12345"},
    )
    assert response.status_code == 200
    return response.json()["data"]


async def _set_currentness_async(user_id: int, *, status="active", verify_status="verified") -> None:
    database_url = os.environ["KG_TEST_DATABASE_URL"].replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(database_url)
    try:
        await connection.execute(
            'UPDATE public."user" SET status=$2, verify_status=$3 WHERE id=$1',
            user_id,
            status,
            verify_status,
        )
    finally:
        await connection.close()


def _set_currentness(user_id: int, **changes) -> None:
    asyncio.run(_set_currentness_async(user_id, **changes))


def _headers(user_id: int, *, role="member") -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _create_profile(real_db_client, user_id: int) -> None:
    response = real_db_client.put(
        "/api/v1/users/me/health-profile",
        headers=_headers(user_id),
        json={
            "gender": "female",
            "birth_date": "1990-01-01",
            "height": "165.0",
            "weight": "55.0",
            "blood_type": "A",
            "expected_version": None,
        },
    )
    assert response.status_code == 200


def _write_indicator(real_db_client, user_id: int, *, kind: str, value: str, unit: str, at: datetime) -> None:
    response = real_db_client.post(
        f"/api/v1/users/{user_id}/health-indicators",
        headers=_headers(user_id),
        json={
            "indicators": [
                {
                    "indicator_type": kind,
                    "value": value,
                    "unit": unit,
                    "source": "APP",
                    "recorded_at": at.isoformat(),
                }
            ]
        },
    )
    assert response.status_code == 201


def test_本人指标历史latest与并发新增分页稳定(real_db_client, pg_database) -> None:
    user = _register(real_db_client, "13800139501")
    _set_currentness(user["id"])
    _create_profile(real_db_client, user["id"])
    now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
    for offset, value in ((0, "120.00"), (1, "121.00"), (2, "122.00")):
        _write_indicator(
            real_db_client,
            user["id"],
            kind="systolic_bp",
            value=value,
            unit="mmHg",
            at=now + timedelta(minutes=offset),
        )

    first = real_db_client.get(
        "/api/v1/users/me/health-indicators",
        params={"indicator_type": "systolic_bp", "limit": 2},
        headers=_headers(user["id"]),
    )
    assert first.status_code == 200
    first_data = first.json()["data"]
    assert first_data["state"] == "AVAILABLE"
    assert [item["value"] for item in first_data["items"]] == ["122.00", "121.00"]
    assert first_data["next_cursor"]

    _write_indicator(
        real_db_client,
        user["id"],
        kind="systolic_bp",
        value="123.00",
        unit="mmHg",
        at=now + timedelta(minutes=3),
    )
    second = real_db_client.get(
        "/api/v1/users/me/health-indicators",
        params={
            "indicator_type": "systolic_bp",
            "limit": 2,
            "cursor": first_data["next_cursor"],
        },
        headers=_headers(user["id"]),
    )
    assert second.status_code == 200
    assert [item["value"] for item in second.json()["data"]["items"]] == ["120.00"]

    latest = real_db_client.get(
        "/api/v1/users/me/health-indicators/latest",
        headers=_headers(user["id"]),
    )
    assert latest.status_code == 200
    assert latest.json()["data"]["items"][0]["value"] == "123.00"
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM health_indicator WHERE user_id=" + str(user["id"])
    ) == 4


def test_本人趋势稳定升序且混合单位fail_closed(real_db_client) -> None:
    user = _register(real_db_client, "13800139502")
    _set_currentness(user["id"])
    _create_profile(real_db_client, user["id"])
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    _write_indicator(real_db_client, user["id"], kind="custom_metric", value="1.00", unit="u1", at=now)
    _write_indicator(
        real_db_client,
        user["id"],
        kind="custom_metric",
        value="2.00",
        unit="u1",
        at=now + timedelta(minutes=1),
    )
    trend = real_db_client.get(
        "/api/v1/users/me/health-trends",
        params={"indicator_type": "custom_metric"},
        headers=_headers(user["id"]),
    )
    assert trend.status_code == 200
    assert trend.json()["data"] == {
        "state": "AVAILABLE",
        "indicator_type": "custom_metric",
        "unit": "u1",
        "points": [
            {"value": "1.00", "recorded_at": now.isoformat().replace("+00:00", "Z"), "source": "APP"},
            {
                "value": "2.00",
                "recorded_at": (now + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                "source": "APP",
            },
        ],
    }

    _write_indicator(
        real_db_client,
        user["id"],
        kind="custom_metric",
        value="3.00",
        unit="u2",
        at=now + timedelta(minutes=2),
    )
    inconsistent = real_db_client.get(
        "/api/v1/users/me/health-trends",
        params={"indicator_type": "custom_metric"},
        headers=_headers(user["id"]),
    )
    assert inconsistent.status_code == 409
    body = inconsistent.json()
    assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
    assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
        "HEALTH_INDICATOR_UNIT_INCONSISTENT", "request rejected", [], False,
    )
    assert inconsistent.headers["X-Request-ID"] == body["request_id"]
    assert inconsistent.headers["Cache-Control"] == "no-store, private"
    assert inconsistent.headers["Pragma"] == "no-cache"


def test_空态权限与currentness保持fail_closed(real_db_client, pg_database) -> None:
    user = _register(real_db_client, "13800139503")
    _set_currentness(user["id"])
    headers = _headers(user["id"])
    empty = real_db_client.get("/api/v1/users/me/health-indicators", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["data"] == {"state": "EMPTY", "items": [], "next_cursor": None}

    pg_database.execute(
        'INSERT INTO public."user" (id,phone,password_hash,role,status) VALUES '
        "(83951,'13900083951','synthetic','org_admin','active')"
    )

    forbidden = real_db_client.get(
        "/api/v1/users/me/health-indicators/latest",
        headers=_headers(83951, role="org_admin"),
    )
    assert forbidden.status_code == 403
    original_status = pg_database.fetch_value(
        'SELECT status FROM public."user" WHERE id=' + str(user["id"])
    )
    try:
        _set_currentness(user["id"], status="disabled")
        unavailable = real_db_client.get("/api/v1/users/me/health-indicators", headers=headers)
        assert unavailable.status_code == 401
        body = unavailable.json()
        assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
        assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
            "ACCESS_TOKEN_STALE", "request rejected", [], False,
        )
        assert unavailable.headers["X-Request-ID"] == body["request_id"]
        assert unavailable.headers["WWW-Authenticate"] == "Bearer"
        assert unavailable.headers["Cache-Control"] == "no-store, private"
        assert unavailable.headers["Pragma"] == "no-cache"
        assert pg_database.fetch_value(
            "SELECT COUNT(*) FROM health_indicator WHERE user_id=" + str(user["id"])
        ) == 0
    finally:
        _set_currentness(user["id"], status=original_status)
