from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.health_analysis.aggregation import STANDARD_INDICATORS


pytestmark = pytest.mark.integration


def _register(real_db_client, phone: str):
    response = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": phone, "password": "Secret12345"},
    )
    assert response.status_code == 200
    return response.json()["data"]


def _member_headers(user_id: int) -> dict:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": "member"})
    return {"Authorization": f"Bearer {token}"}


def _health_profile_payload() -> dict:
    return {
        "gender": "F",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": "55.0",
        "blood_type": "A",
        "medical_history": ["hypertension"],
        "allergy_history": None,
        "family_history": {"items": ["diabetes"]},
        "smoking": "never",
        "drinking": "none",
        "symptoms": [{"name": "fatigue"}],
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


def _create_health_profile(real_db_client, user_id: int):
    response = real_db_client.post(
        f"/api/v1/users/{user_id}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user_id),
    )
    assert response.status_code == 201
    return response.json()["data"]


def _indicator(
    *,
    indicator_type: str,
    value: str,
    recorded_at: str,
    unit: str = "mmHg",
    batch_id: str | None = None,
) -> dict:
    payload = {
        "indicator_type": indicator_type,
        "value": value,
        "unit": unit,
        "source": "APP",
        "recorded_at": recorded_at,
    }
    if batch_id is not None:
        payload["batch_id"] = batch_id
    return payload


def _write_indicators(real_db_client, user_id: int, indicators: list[dict]):
    response = real_db_client.post(
        f"/api/v1/users/{user_id}/health-indicators",
        json={"indicators": indicators},
        headers=_member_headers(user_id),
    )
    assert response.status_code == 201
    return response.json()["data"]


def _get_health_trend(
    real_db_client,
    user_id: int,
    *,
    indicator_type: str,
    requester_id: int | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    limit: int | None = None,
):
    params: dict[str, str | int] = {"indicator_type": indicator_type}
    if start_at is not None:
        params["start_at"] = start_at
    if end_at is not None:
        params["end_at"] = end_at
    if limit is not None:
        params["limit"] = limit

    return real_db_client.get(
        f"/api/v1/users/{user_id}/health-trends",
        params=params,
        headers=_member_headers(requester_id or user_id),
    )


def _assert_database_ready(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260915_0048"
    assert pg_database.fetch_value(
        """
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'health_indicator'
        """
    ) == "health_indicator"


def test_f004_real_db_health_trend_happy_path(real_db_client, pg_database):
    _assert_database_ready(pg_database)
    user = _register(real_db_client, "13800139901")
    _create_health_profile(real_db_client, user["id"])
    anchor = datetime.now(timezone.utc).replace(microsecond=0)
    first_systolic_at = anchor - timedelta(days=2)
    diastolic_at = first_systolic_at + timedelta(minutes=1)
    second_systolic_at = anchor - timedelta(days=1)
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(
                indicator_type="systolic_bp",
                value="118.00",
                recorded_at=first_systolic_at.isoformat(),
            ),
            _indicator(
                indicator_type="diastolic_bp",
                value="78.00",
                recorded_at=diastolic_at.isoformat(),
            ),
            _indicator(
                indicator_type="systolic_bp",
                value="121.00",
                recorded_at=second_systolic_at.isoformat(),
            ),
        ],
    )

    response = _get_health_trend(real_db_client, user["id"], indicator_type="systolic_bp")

    assert response.status_code == 200
    data = response.json()["data"]
    indicator_spec = STANDARD_INDICATORS["systolic_bp"]
    assert data["indicator_type"] == "systolic_bp"
    assert data["display_name"] == indicator_spec["display_name"]
    assert data["category"] == indicator_spec["category"]
    assert data["unit"] == indicator_spec["default_unit"]
    assert [point["value"] for point in data["points"]] == ["118.00", "121.00"]
    assert [point["source"] for point in data["points"]] == ["APP", "APP"]
    assert [point["recorded_at"] for point in data["points"]] == sorted(
        point["recorded_at"] for point in data["points"]
    )

    rows = pg_database.fetch_rows(
        """
        SELECT COUNT(*) AS indicator_count
        FROM health_indicator
        WHERE user_id = $1
          AND indicator_type = 'systolic_bp'
        """,
        user["id"],
    )
    assert rows[0]["indicator_count"] == 2


def test_f004_real_db_health_trend_filters_time_range(real_db_client):
    user = _register(real_db_client, "13800139902")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="116.00", recorded_at="2026-07-28T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="124.00", recorded_at="2026-07-30T08:00:00Z"),
        ],
    )

    response = _get_health_trend(
        real_db_client,
        user["id"],
        indicator_type="systolic_bp",
        start_at="2026-07-29T00:00:00Z",
        end_at="2026-07-29T23:59:59Z",
    )

    assert response.status_code == 200
    points = response.json()["data"]["points"]
    assert [point["value"] for point in points] == ["120.00"]


def test_f004_real_db_health_trend_applies_limit(real_db_client):
    user = _register(real_db_client, "13800139903")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="heart_rate", value="70.00", unit="bpm", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="heart_rate", value="71.00", unit="bpm", recorded_at="2026-07-29T09:00:00Z"),
            _indicator(indicator_type="heart_rate", value="72.00", unit="bpm", recorded_at="2026-07-29T10:00:00Z"),
        ],
    )

    response = _get_health_trend(
        real_db_client,
        user["id"],
        indicator_type="heart_rate",
        start_at="2026-07-29T00:00:00Z",
        end_at="2026-07-29T23:59:59Z",
        limit=2,
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["unit"] == "bpm"
    assert [point["value"] for point in data["points"]] == ["70.00", "71.00"]


def test_f004_real_db_health_trend_unknown_indicator_returns_422(real_db_client):
    user = _register(real_db_client, "13800139904")

    response = _get_health_trend(real_db_client, user["id"], indicator_type="unknown_metric")

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
    assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
        "HEALTH_INDICATOR_TYPE_INVALID", "request rejected", [], False,
    )
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Pragma"] == "no-cache"


def test_f004_real_db_health_trend_idor_is_blocked(real_db_client, pg_database):
    user_a = _register(real_db_client, "13800139905")
    user_b = _register(real_db_client, "13800139906")
    _create_health_profile(real_db_client, user_b["id"])
    _write_indicators(
        real_db_client,
        user_b["id"],
        [_indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:00:00Z")],
    )

    response = _get_health_trend(
        real_db_client,
        user_b["id"],
        indicator_type="systolic_bp",
        requester_id=user_a["id"],
    )

    assert response.status_code == 403
    rows = pg_database.fetch_rows(
        """
        SELECT COUNT(*) AS indicator_count
        FROM health_indicator
        WHERE user_id = $1
        """,
        user_b["id"],
    )
    assert rows[0]["indicator_count"] == 1
