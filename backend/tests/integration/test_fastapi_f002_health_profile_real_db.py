import json

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
        "allergy_history": {"drug": "penicillin"},
        "family_history": None,
        "smoking": "never",
        "drinking": "none",
        "symptoms": [{"name": "fatigue"}],
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


def _fetch_health_profile(pg_database, user_id: int):
    rows = pg_database.fetch_rows(
        """
        SELECT
            id,
            user_id,
            gender,
            birth_date,
            height,
            weight,
            medical_history::text AS medical_history,
            allergy_history::text AS allergy_history,
            symptoms::text AS symptoms
        FROM health_profile
        WHERE user_id = $1
        """,
        user_id,
    )
    return rows[0] if rows else None


def _health_profile_count(pg_database, user_id: int) -> int:
    rows = pg_database.fetch_rows(
        """
        SELECT COUNT(*) AS profile_count
        FROM health_profile
        WHERE user_id = $1
        """,
        user_id,
    )
    return rows[0]["profile_count"]


def test_f002_real_db_health_profile_create_happy_path(real_db_client, pg_database):
    user = _register(real_db_client, "13800139201")

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["user_id"] == user["id"]
    assert data["gender"] == "F"
    assert data["birth_date"] == "1990-01-01"
    assert data["height"] == "165.5"
    assert data["weight"] == "55.0"

    profile = _fetch_health_profile(pg_database, user["id"])
    assert profile is not None
    assert profile["user_id"] == user["id"]
    assert profile["gender"] == "F"
    assert profile["birth_date"].isoformat() == "1990-01-01"
    assert str(profile["height"]) == "165.5"
    assert str(profile["weight"]) == "55.0"


def test_f002_real_db_health_profile_persists_jsonb_fields(real_db_client, pg_database):
    user = _register(real_db_client, "13800139202")

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 201
    profile = _fetch_health_profile(pg_database, user["id"])
    assert json.loads(profile["medical_history"]) == ["hypertension"]
    assert json.loads(profile["allergy_history"]) == {"drug": "penicillin"}
    assert json.loads(profile["symptoms"]) == [{"name": "fatigue"}]


def test_f002_real_db_duplicate_health_profile_returns_409(real_db_client, pg_database):
    user = _register(real_db_client, "13800139203")

    first_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user["id"]),
    )
    second_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user["id"]),
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "Health profile already exists"
    assert _health_profile_count(pg_database, user["id"]) == 1
    assert pg_database.fetch_value(
        """
        SELECT COUNT(*)
        FROM pg_constraint
        WHERE conrelid = 'health_profile'::regclass
          AND contype = 'u'
          AND pg_get_constraintdef(oid) = 'UNIQUE (user_id)'
        """
    ) == 1


def test_f002_real_db_health_profile_idor_is_blocked(real_db_client, pg_database):
    user_a = _register(real_db_client, "13800139204")
    user_b = _register(real_db_client, "13800139205")

    response = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user_a["id"]),
    )

    assert response.status_code == 403
    assert _health_profile_count(pg_database, user_b["id"]) == 0


def test_f002_real_db_health_profile_user_not_found_returns_404(real_db_client):
    response = real_db_client.post(
        "/api/v1/users/99999999/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(99999999),
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"
