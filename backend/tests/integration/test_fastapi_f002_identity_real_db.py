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


def _identity_payload(real_name: str = "Zhang San", id_card: str = "110101199001011234") -> dict:
    return {
        "real_name": real_name,
        "id_card": id_card,
    }


def _fetch_identity(pg_database, user_id: int):
    rows = pg_database.fetch_rows(
        """
        SELECT id, real_name, id_card, verify_status
        FROM "user"
        WHERE id = $1
        """,
        user_id,
    )
    return rows[0] if rows else None


def test_f002_real_db_member_submits_identity(real_db_client, pg_database):
    user = _register(real_db_client, "13800139101")

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/identity",
        json=_identity_payload(),
        headers=_member_headers(user["id"]),
    )

    assert response.status_code == 410
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "code": "LEGACY_IDENTITY_ENDPOINT_RETIRED",
        "message": "request rejected",
    }

    db_user = _fetch_identity(pg_database, user["id"])
    assert db_user["real_name"] is None
    assert db_user["id_card"] is None
    assert db_user["verify_status"] is None


def test_f002_real_db_identity_idor_is_blocked(real_db_client, pg_database):
    user_a = _register(real_db_client, "13800139102")
    user_b = _register(real_db_client, "13800139103")

    response = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/identity",
        json=_identity_payload(real_name="Mallory", id_card="110101199001011235"),
        headers=_member_headers(user_a["id"]),
    )

    assert response.status_code == 410
    db_user_b = _fetch_identity(pg_database, user_b["id"])
    assert db_user_b["real_name"] is None
    assert db_user_b["id_card"] is None
    assert db_user_b["verify_status"] is None


def test_f002_real_db_identity_user_not_found_returns_404(real_db_client, pg_database):
    response = real_db_client.post(
        "/api/v1/users/99999999/identity",
        json=_identity_payload(),
        headers=_member_headers(99999999),
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "ACCESS_TOKEN_STALE"}
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "99999999" not in response.text

    caller = _register(real_db_client, "13800139104")
    retired = real_db_client.post(
        "/api/v1/users/99999999/identity",
        json=_identity_payload(),
        headers=_member_headers(caller["id"]),
    )
    assert retired.status_code == 410
    assert retired.json() == {
        "code": "LEGACY_IDENTITY_ENDPOINT_RETIRED", "message": "request rejected",
    }
    assert retired.headers["Cache-Control"] == "no-store"
    assert "99999999" not in retired.text
    unchanged = _fetch_identity(pg_database, caller["id"])
    assert unchanged["real_name"] is None
    assert unchanged["id_card"] is None
    assert unchanged["verify_status"] is None
    assert _fetch_identity(pg_database, 99999999) is None
