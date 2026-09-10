from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _indicator(
    *,
    indicator_type: str,
    value: str,
    recorded_at: datetime,
    unit: str = "mmHg",
) -> dict:
    return {
        "indicator_type": indicator_type,
        "value": value,
        "unit": unit,
        "source": "APP",
        "recorded_at": _iso(recorded_at),
    }


def _write_indicators(real_db_client, user_id: int, indicators: list[dict]):
    response = real_db_client.post(
        f"/api/v1/users/{user_id}/health-indicators",
        json={"indicators": indicators},
        headers=_member_headers(user_id),
    )
    assert response.status_code == 201
    return response.json()["data"]


def _get_ai_health_input(real_db_client, user_id: int):
    return real_db_client.get(
        f"/internal/v1/users/{user_id}/ai-health-input",
        headers=_member_headers(user_id),
    )


def _assert_database_ready(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260912_0043"
    assert pg_database.fetch_value(
        """
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'health_indicator'
        """
    ) == "health_indicator"


def _assert_contract_safety(serialized: str):
    assert "password_hash" not in serialized
    assert "phone" not in serialized
    assert "id_card" not in serialized
    assert "real_name" not in serialized
    assert '"health_score"' not in serialized
    assert '"risk_level"' not in serialized
    assert '"diagnosis"' not in serialized
    assert '"recommendation"' not in serialized


def test_f004_real_db_ai_contract_happy_path(real_db_client, pg_database):
    _assert_database_ready(pg_database)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    older = now - timedelta(days=2)
    newer = now - timedelta(days=1)
    user = _register(real_db_client, "13800139701")
    _create_health_profile(real_db_client, user["id"])
    written = _write_indicators(
        real_db_client,
        user["id"],
        [
            _indicator(indicator_type="systolic_bp", value="118.00", recorded_at=older),
            _indicator(indicator_type="systolic_bp", value="121.00", recorded_at=newer),
            _indicator(indicator_type="heart_rate", value="72.00", unit="bpm", recorded_at=newer),
        ],
    )

    response = _get_ai_health_input(real_db_client, user["id"])

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["contract_version"] == "f004.ai_input.v1"
    assert data["user_context"]["user_id"] == user["id"]
    assert data["user_context"]["status"] == "active"
    assert data["user_context"]["tenant_id"] is None
    assert data["profile_context"]["gender"] == "F"
    assert data["profile_context"]["birth_date"] == "1990-01-01"
    assert data["profile_context"]["height"] == "165.5"
    assert data["profile_context"]["has_medical_history"] is True

    latest_by_type = {item["indicator_type"]: item for item in data["latest_indicators"]}
    assert set(latest_by_type) == {"systolic_bp", "heart_rate"}
    assert latest_by_type["systolic_bp"]["value"] == "121.00"
    assert latest_by_type["systolic_bp"]["unit"] == "mmHg"
    assert latest_by_type["heart_rate"]["unit"] == "bpm"

    trends_by_type = {item["indicator_type"]: item for item in data["trend_context"]}
    assert set(trends_by_type) == {"systolic_bp", "heart_rate"}
    assert trends_by_type["systolic_bp"]["category"] == "blood_pressure"
    assert [point["value"] for point in trends_by_type["systolic_bp"]["points"]] == ["118.00", "121.00"]
    assert [point["recorded_at"] for point in trends_by_type["systolic_bp"]["points"]] == sorted(
        point["recorded_at"] for point in trends_by_type["systolic_bp"]["points"]
    )

    assert data["data_completeness"]["profile_completed"] is True
    assert data["data_completeness"]["indicator_count"] == 2
    assert data["data_completeness"]["standard_indicator_count"] == 15
    assert "systolic_bp" not in data["data_completeness"]["missing_indicator_types"]
    assert all(data["safety_policy"].values())
    assert data["source_refs"]["source_tables"] == ["user", "health_profile", "health_indicator"]
    _assert_contract_safety(response.text)

    rows = pg_database.fetch_rows(
        """
        SELECT id
        FROM health_indicator
        WHERE user_id = $1
        ORDER BY id
        """,
        user["id"],
    )
    assert [row["id"] for row in rows] == [item["id"] for item in written]


def test_f004_real_db_ai_contract_without_health_data_returns_valid_contract(real_db_client):
    user = _register(real_db_client, "13800139702")

    response = _get_ai_health_input(real_db_client, user["id"])

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["contract_version"] == "f004.ai_input.v1"
    assert data["user_context"]["user_id"] == user["id"]
    assert data["profile_context"]["gender"] is None
    assert data["latest_indicators"] == []
    assert data["trend_context"] == []
    assert data["data_completeness"]["profile_completed"] is False
    assert data["data_completeness"]["indicator_count"] == 0
    assert all(data["safety_policy"].values())
    _assert_contract_safety(response.text)
