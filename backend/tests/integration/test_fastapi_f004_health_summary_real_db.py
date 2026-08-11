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


def _get_health_summary(real_db_client, user_id: int, *, requester_id: int | None = None):
    return real_db_client.get(
        f"/api/v1/users/{user_id}/health-summary",
        headers=_member_headers(requester_id or user_id),
    )


def _assert_database_ready(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260812_0016"
    assert pg_database.fetch_value(
        """
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'health_indicator'
        """
    ) == "health_indicator"


def test_f004_real_db_health_summary_happy_path(real_db_client, pg_database):
    _assert_database_ready(pg_database)
    user = _register(real_db_client, "13800139801")
    _create_health_profile(real_db_client, user["id"])
    written = _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="diastolic_bp", value="80.00", recorded_at="2026-07-29T08:01:00Z"),
            _indicator(indicator_type="heart_rate", value="72.00", unit="bpm", recorded_at="2026-07-29T08:02:00Z"),
        ],
    )

    response = _get_health_summary(real_db_client, user["id"])

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user"]["user_id"] == user["id"]
    assert data["user"]["status"] == "active"
    assert data["profile"]["exists"] is True
    assert data["profile"]["gender"] == "F"
    assert data["profile"]["birth_date"] == "1990-01-01"
    assert data["profile"]["height"] == "165.5"
    assert data["profile"]["has_medical_history"] is True
    assert data["profile"]["has_allergy_history"] is False
    assert data["profile"]["has_family_history"] is True
    assert data["profile"]["has_symptoms"] is True

    latest_by_type = {item["indicator_type"]: item for item in data["latest_indicators"]}
    assert set(latest_by_type) == {"systolic_bp", "diastolic_bp", "heart_rate"}
    assert latest_by_type["systolic_bp"]["display_name"] == "收缩压"
    assert latest_by_type["systolic_bp"]["category"] == "blood_pressure"
    assert latest_by_type["systolic_bp"]["value"] == "120.00"
    assert latest_by_type["heart_rate"]["unit"] == "bpm"
    assert data["indicator_updated_at"].startswith("2026-07-29T08:02:00")
    assert data["data_completeness"]["profile_completed"] is True
    assert data["data_completeness"]["indicator_count"] == 3
    assert data["data_completeness"]["standard_indicator_count"] == 15
    assert "systolic_bp" not in data["data_completeness"]["missing_indicator_types"]

    rows = pg_database.fetch_rows(
        """
        SELECT id, indicator_type
        FROM health_indicator
        WHERE user_id = $1
        ORDER BY id
        """,
        user["id"],
    )
    assert [row["id"] for row in rows] == [item["id"] for item in written]


def test_f004_real_db_health_summary_profile_missing_returns_exists_false(real_db_client):
    user = _register(real_db_client, "13800139802")

    response = _get_health_summary(real_db_client, user["id"])

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["profile"]["exists"] is False
    assert data["profile"]["gender"] is None
    assert data["data_completeness"]["profile_completed"] is False


def test_f004_real_db_health_summary_without_indicators_returns_empty_list(real_db_client):
    user = _register(real_db_client, "13800139803")
    _create_health_profile(real_db_client, user["id"])

    response = _get_health_summary(real_db_client, user["id"])

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["latest_indicators"] == []
    assert data["indicator_updated_at"] is None
    assert data["data_completeness"]["indicator_count"] == 0
    assert "systolic_bp" in data["data_completeness"]["missing_indicator_types"]


def test_f004_real_db_health_summary_returns_latest_indicator_per_type(real_db_client):
    user = _register(real_db_client, "13800139804")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="118.00", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="121.00", recorded_at="2026-07-29T09:00:00Z"),
            _indicator(indicator_type="heart_rate", value="70.00", unit="bpm", recorded_at="2026-07-29T08:00:00Z"),
        ],
    )

    response = _get_health_summary(real_db_client, user["id"])

    assert response.status_code == 200
    latest_by_type = {item["indicator_type"]: item for item in response.json()["data"]["latest_indicators"]}
    assert latest_by_type["systolic_bp"]["value"] == "121.00"
    assert latest_by_type["heart_rate"]["value"] == "70.00"


def test_f004_real_db_health_summary_ignores_unknown_indicator_in_core_summary(real_db_client, pg_database):
    user = _register(real_db_client, "13800139805")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="unknown_metric", value="9.99", unit="u", recorded_at="2026-07-29T08:00:00Z"),
            _indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:01:00Z"),
        ],
    )

    response = _get_health_summary(real_db_client, user["id"])

    assert response.status_code == 200
    indicator_types = [item["indicator_type"] for item in response.json()["data"]["latest_indicators"]]
    assert indicator_types == ["systolic_bp"]
    assert "unknown_metric" not in indicator_types
    rows = pg_database.fetch_rows(
        """
        SELECT COUNT(*) AS indicator_count
        FROM health_indicator
        WHERE user_id = $1
          AND indicator_type = 'unknown_metric'
        """,
        user["id"],
    )
    assert rows[0]["indicator_count"] == 1


def test_f004_real_db_health_summary_idor_is_blocked(real_db_client):
    user_a = _register(real_db_client, "13800139806")
    user_b = _register(real_db_client, "13800139807")
    _create_health_profile(real_db_client, user_b["id"])

    response = _get_health_summary(real_db_client, user_b["id"], requester_id=user_a["id"])

    assert response.status_code == 403


def test_f004_real_db_health_summary_response_is_safe(real_db_client):
    user = _register(real_db_client, "13800139808")
    _create_health_profile(real_db_client, user["id"])
    _write_indicators(
        real_db_client,
        user["id"],
        [_indicator(indicator_type="systolic_bp", value="120.00", recorded_at="2026-07-29T08:00:00Z")],
    )

    response = _get_health_summary(real_db_client, user["id"])

    assert response.status_code == 200
    serialized = response.text
    assert "password_hash" not in serialized
    assert "id_card" not in serialized
    assert "real_name" not in serialized
    assert "risk_level" not in serialized
    assert "health_score" not in serialized
