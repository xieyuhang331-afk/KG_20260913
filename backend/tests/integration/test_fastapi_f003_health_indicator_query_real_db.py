from __future__ import annotations

import pytest


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
        "family_history": None,
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


def _write_indicators(real_db_client, user_id: int, indicators: list[dict]):
    response = real_db_client.post(
        f"/api/v1/users/{user_id}/health-indicators",
        json={"indicators": indicators},
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


def _fetch_health_indicators(pg_database, user_id: int):
    return pg_database.fetch_rows(
        """
        SELECT id, user_id, indicator_type, value, unit, source, recorded_at, batch_id
        FROM health_indicator
        WHERE user_id = $1
        ORDER BY recorded_at DESC, id DESC
        """,
        user_id,
    )


def _health_indicator_count(pg_database, user_id: int) -> int:
    rows = pg_database.fetch_rows(
        "SELECT COUNT(*) AS indicator_count FROM health_indicator WHERE user_id = $1",
        user_id,
    )
    return rows[0]["indicator_count"]


def _assert_database_ready(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260823_0028"
    assert pg_database.fetch_value(
        """
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'health_indicator'
        """
    ) == "health_indicator"


def test_f003_real_db_health_indicator_history_query_returns_timescale_data(real_db_client, pg_database):
    _assert_database_ready(pg_database)
    user = _register(real_db_client, "13800139701")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="118.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="121.00", recorded_at="2026-07-29T09:00:00Z"),
            _indicator(indicator_type="diastolic_bp", value="79.00", recorded_at="2026-07-29T08:30:00Z"),
        ],
    )

    response = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators",
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert [item["indicator_type"] for item in data] == ["systolic_bp", "diastolic_bp", "systolic_bp"]
    assert [item["value"] for item in data] == ["121.00", "79.00", "118.00"]
    assert "password_hash" not in data[0]
    assert "id_card" not in data[0]
    assert "real_name" not in data[0]

    db_rows = _fetch_health_indicators(pg_database, user["id"])
    assert len(db_rows) == 3
    assert [row["id"] for row in db_rows] == [item["id"] for item in data]


def test_f003_real_db_health_indicator_history_query_filters_type_and_time(real_db_client, pg_database):
    user = _register(real_db_client, "13800139702")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="116.00", recorded_at="2026-07-28T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="122.00", recorded_at="2026-07-30T08:00:00Z"),
            _indicator(indicator_type="diastolic_bp", value="80.00", recorded_at="2026-07-29T08:00:00Z"),
        ],
    )

    response = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators",
        params={
            "indicator_type": "systolic_bp",
            "start_at": "2026-07-29T00:00:00Z",
            "end_at": "2026-07-29T23:59:59Z",
        },
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["indicator_type"] == "systolic_bp"
    assert data[0]["value"] == "120.00"


def test_f003_real_db_health_indicator_history_query_limit(real_db_client):
    user = _register(real_db_client, "13800139703")
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

    response = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators",
        params={"limit": 2},
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 2
    assert [item["value"] for item in data] == ["72.00", "71.00"]


def test_f003_real_db_health_indicator_latest_query_returns_latest_per_type(real_db_client):
    user = _register(real_db_client, "13800139704")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="118.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="121.00", recorded_at="2026-07-29T09:00:00Z"),
            _indicator(indicator_type="diastolic_bp", value="78.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="diastolic_bp", value="81.00", recorded_at="2026-07-29T09:00:00Z"),
        ],
    )

    response = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators/latest",
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 200
    data_by_type = {item["indicator_type"]: item for item in response.json()["data"]}
    assert set(data_by_type) == {"systolic_bp", "diastolic_bp"}
    assert data_by_type["systolic_bp"]["value"] == "121.00"
    assert data_by_type["diastolic_bp"]["value"] == "81.00"


def test_f003_real_db_health_indicator_latest_query_uses_id_desc_tie_breaker(real_db_client):
    user = _register(real_db_client, "13800139705")
    _create_health_profile(real_db_client, user["id"])
    written = _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="heart_rate", value="70.00", unit="bpm", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="heart_rate", value="72.00", unit="bpm", recorded_at="2026-07-29T08:00:00Z"),
        ],
    )

    response = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators/latest",
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["indicator_type"] == "heart_rate"
    assert data[0]["id"] == max(item["id"] for item in written)
    assert data[0]["value"] == "72.00"


def test_f003_real_db_health_indicator_query_empty_result(real_db_client, pg_database):
    user = _register(real_db_client, "13800139706")
    _create_health_profile(real_db_client, user["id"])

    history = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators",
        headers=_member_headers(user["id"]),
    )
    latest = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-indicators/latest",
        headers=_member_headers(user["id"]),
    )

    assert history.status_code == 200
    assert history.json()["data"] == []
    assert latest.status_code == 200
    assert latest.json()["data"] == []
    assert _health_indicator_count(pg_database, user["id"]) == 0


def test_f003_real_db_health_indicator_query_idor_is_blocked(real_db_client, pg_database):
    user_a = _register(real_db_client, "13800139707")
    user_b = _register(real_db_client, "13800139708")
    _create_health_profile(real_db_client, user_b["id"])
    _write_indicators(
        real_db_client,
        user_b["id"],
        [_indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:00:00Z")],
    )

    history = real_db_client.get(
        f"/api/v1/users/{user_b['id']}/health-indicators",
        headers=_member_headers(user_a["id"]),
    )
    latest = real_db_client.get(
        f"/api/v1/users/{user_b['id']}/health-indicators/latest",
        headers=_member_headers(user_a["id"]),
    )

    assert history.status_code == 403
    assert latest.status_code == 403
    assert _health_indicator_count(pg_database, user_b["id"]) == 1
