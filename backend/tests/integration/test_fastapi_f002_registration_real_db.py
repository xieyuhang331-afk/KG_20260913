import pytest


pytestmark = pytest.mark.integration


def _register_payload(phone: str) -> dict:
    return {
        "phone": phone,
        "password": "Secret12345",
    }


def _fetch_user_by_phone(pg_database, phone: str):
    rows = pg_database.fetch_rows(
        """
        SELECT id, phone, password_hash, role, status, tenant_id
        FROM "user"
        WHERE phone = $1
        """,
        phone,
    )
    return rows[0] if rows else None


def test_f002_real_db_user_registration_creates_member_user(real_db_client, pg_database):
    response = real_db_client.post("/api/v1/users/register", json=_register_payload("13800139001"))

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["phone"] == "13800139001"
    assert data["role"] == "member"
    assert data["status"] == "active"
    assert data["tenant_id"] is None
    assert data["created_at"] is not None

    user = _fetch_user_by_phone(pg_database, "13800139001")
    assert user is not None
    assert user["phone"] == "13800139001"
    assert user["password_hash"] != "Secret12345"
    assert user["password_hash"].startswith("pbkdf2_sha256$")
    assert user["role"] == "member"
    assert user["status"] == "active"
    assert user["tenant_id"] is None


def test_f002_real_db_duplicate_phone_returns_409(real_db_client, pg_database):
    first_response = real_db_client.post("/api/v1/users/register", json=_register_payload("13800139002"))
    second_response = real_db_client.post("/api/v1/users/register", json=_register_payload("13800139002"))

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "User already exists"

    users = pg_database.fetch_rows(
        """
        SELECT id
        FROM "user"
        WHERE phone = $1
        """,
        "13800139002",
    )
    assert len(users) == 1

