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
        "allergy_history": {"drug": "penicillin"},
        "family_history": None,
        "smoking": "never",
        "drinking": "none",
        "symptoms": [{"name": "fatigue"}],
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


def _indicator_payload(*, source: str = "APP", batch_id: str | None = None) -> dict:
    first = {
        "indicator_type": "systolic_bp",
        "value": "120.00",
        "unit": "mmHg",
        "source": source,
        "recorded_at": "2026-07-29T08:00:00Z",
    }
    second = {
        "indicator_type": "diastolic_bp",
        "value": "80.00",
        "unit": "mmHg",
        "source": source,
        "recorded_at": "2026-07-29T08:00:00Z",
    }
    if batch_id is not None:
        first["batch_id"] = batch_id
        second["batch_id"] = batch_id
    return {"indicators": [first, second]}


def _create_health_profile(real_db_client, user_id: int):
    response = real_db_client.post(
        f"/api/v1/users/{user_id}/health-profile",
        json=_health_profile_payload(),
        headers=_member_headers(user_id),
    )
    assert response.status_code == 201
    return response.json()["data"]


def _fetch_health_indicators(pg_database, user_id: int):
    return pg_database.fetch_rows(
        """
        SELECT
            id,
            user_id,
            batch_id,
            indicator_type,
            value,
            unit,
            source,
            recorded_at,
            created_at
        FROM health_indicator
        WHERE user_id = $1
        ORDER BY id
        """,
        user_id,
    )


def _health_indicator_count(pg_database, user_id: int) -> int:
    rows = pg_database.fetch_rows(
        "SELECT COUNT(*) AS indicator_count FROM health_indicator WHERE user_id = $1",
        user_id,
    )
    return rows[0]["indicator_count"]


def test_f003_real_db_health_indicator_write_happy_path(real_db_client, pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260910_0041"
    assert pg_database.fetch_value(
        """
        SELECT hypertable_name
        FROM timescaledb_information.hypertables
        WHERE hypertable_schema = 'public'
          AND hypertable_name = 'health_indicator'
        """
    ) == "health_indicator"

    user = _register(real_db_client, "13800139601")
    _create_health_profile(real_db_client, user["id"])

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-indicators",
        json=_indicator_payload(),
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert len(data) == 2
    assert data[0]["id"] > 0
    assert data[1]["id"] > data[0]["id"]
    assert len(data[0]["batch_id"]) == 36
    assert data[0]["batch_id"] == data[1]["batch_id"]
    assert data[0]["indicator_type"] == "systolic_bp"
    assert data[0]["value"] == "120.00"
    assert data[0]["unit"] == "mmHg"
    assert data[0]["source"] == "APP"
    assert data[0]["recorded_at"].startswith("2026-07-29T08:00:00")
    assert "created_at" in data[0]
    assert "password_hash" not in data[0]
    assert "id_card" not in data[0]
    assert "real_name" not in data[0]

    rows = _fetch_health_indicators(pg_database, user["id"])
    assert len(rows) == 2
    assert rows[0]["id"] == data[0]["id"]
    assert rows[1]["id"] == data[1]["id"]
    assert rows[0]["batch_id"] == data[0]["batch_id"]
    assert rows[1]["batch_id"] == data[0]["batch_id"]
    assert rows[0]["indicator_type"] == "systolic_bp"
    assert str(rows[0]["value"]) == "120.00"
    assert rows[0]["source"] == "APP"


def test_f003_real_db_health_indicator_write_keeps_provided_batch_id(real_db_client, pg_database):
    user = _register(real_db_client, "13800139602")
    _create_health_profile(real_db_client, user["id"])

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-indicators",
        json=_indicator_payload(batch_id="batch-from-client-0001"),
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert {item["batch_id"] for item in data} == {"batch-from-client-0001"}
    assert {row["batch_id"] for row in _fetch_health_indicators(pg_database, user["id"])} == {"batch-from-client-0001"}


def test_f003_real_db_health_indicator_idor_is_blocked(real_db_client, pg_database):
    user_a = _register(real_db_client, "13800139603")
    user_b = _register(real_db_client, "13800139604")
    _create_health_profile(real_db_client, user_b["id"])

    response = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/health-indicators",
        json=_indicator_payload(),
        headers=_member_headers(user_a["id"]),
    )

    assert response.status_code == 403
    assert _health_indicator_count(pg_database, user_b["id"]) == 0


def test_f003_real_db_health_indicator_source_must_be_app(real_db_client, pg_database):
    user = _register(real_db_client, "13800139605")
    _create_health_profile(real_db_client, user["id"])

    for source in ("STORE", "DEVICE", "REPORT"):
        response = real_db_client.post(
            f"/api/v1/users/{user['id']}/health-indicators",
            json=_indicator_payload(source=source),
            headers=_member_headers(user["id"]),
        )

        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
        assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
            "HEALTH_INDICATOR_SOURCE_INVALID", "request rejected", [], False,
        )
        assert response.headers["X-Request-ID"] == body["request_id"]
        assert response.headers["Cache-Control"] == "no-store, private"
        assert response.headers["Pragma"] == "no-cache"

    assert _health_indicator_count(pg_database, user["id"]) == 0


def test_f003_real_db_health_indicator_requires_health_profile(real_db_client, pg_database):
    user = _register(real_db_client, "13800139606")

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-indicators",
        json=_indicator_payload(),
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 409
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
    assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
        "HEALTH_PROFILE_REQUIRED", "request rejected", [], False,
    )
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Pragma"] == "no-cache"
    assert _health_indicator_count(pg_database, user["id"]) == 0


def test_f003_real_db_health_indicator_inactive_user_is_rejected(real_db_client, pg_database):
    user = _register(real_db_client, "13800139607")
    _create_health_profile(real_db_client, user["id"])
    original_status = pg_database.fetch_value(
        f'SELECT status FROM public."user" WHERE id={user["id"]}'
    )
    try:
        pg_database.execute('UPDATE "user" SET status = \'disabled\' WHERE id = %s' % user["id"])
        response = real_db_client.post(
            f"/api/v1/users/{user['id']}/health-indicators",
            json=_indicator_payload(),
            headers=_member_headers(user["id"]),
        )
        assert response.status_code == 401
        body = response.json()
        assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
        assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
            "ACCESS_TOKEN_STALE", "request rejected", [], False,
        )
        assert response.headers["X-Request-ID"] == body["request_id"]
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["Cache-Control"] == "no-store, private"
        assert response.headers["Pragma"] == "no-cache"
        assert _health_indicator_count(pg_database, user["id"]) == 0
    finally:
        pg_database.execute(
            'UPDATE public."user" SET status=\'' + original_status.replace("'", "''")
            + f'\' WHERE id={user["id"]}'
        )


def test_f003_real_db_health_indicator_invalid_payload_is_rejected(real_db_client, pg_database):
    user = _register(real_db_client, "13800139608")
    _create_health_profile(real_db_client, user["id"])

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-indicators",
        json={"indicators": [{"source": "APP"}]},
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 422
    assert _health_indicator_count(pg_database, user["id"]) == 0


def test_f003_real_db_health_indicator_duplicate_facts_are_appended(real_db_client, pg_database):
    user = _register(real_db_client, "13800139609")
    _create_health_profile(real_db_client, user["id"])
    payload = _indicator_payload(batch_id="duplicate-batch-0001")

    first = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-indicators",
        json=payload,
        headers=_member_headers(user["id"]),
    )
    second = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-indicators",
        json=payload,
        headers=_member_headers(user["id"]),
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert _health_indicator_count(pg_database, user["id"]) == 4
    assert [row["id"] for row in _fetch_health_indicators(pg_database, user["id"])] == sorted(
        row["id"] for row in _fetch_health_indicators(pg_database, user["id"])
    )
