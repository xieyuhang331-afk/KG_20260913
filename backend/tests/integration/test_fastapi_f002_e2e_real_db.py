from __future__ import annotations

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
    return _headers("member", user_id=user_id)


def _headers(role: str, *, user_id: int, org_id: int | None = None, province: str | None = None, city: str | None = None):
    from app.core.security import create_access_token

    claims = {"sub": str(user_id), "role": role}
    if org_id is not None:
        claims["org_id"] = org_id
    if province is not None:
        claims["province"] = province
    if city is not None:
        claims["city"] = city
    token = create_access_token(claims)
    return {"Authorization": f"Bearer {token}"}


def _profile_payload() -> dict:
    return {
        "gender": "F",
        "birth_date": "1990-01-01",
        "height": "165.5",
        "weight": "55.0",
        "blood_type": "A",
        "medical_history": ["hypertension"],
        "allergy_history": {"drug": ["penicillin"]},
        "family_history": [],
        "smoking": "never",
        "drinking": "none",
        "symptoms": [{"name": "fatigue"}],
        "sleep_quality": "normal",
        "bowel_urination": "normal",
    }


def _tenant_payload(credit_code: str) -> dict:
    return {
        "name": f"Kanglin Task9 {credit_code[-4:]} Store",
        "type": "health_store",
        "credit_code": credit_code,
        "license_no": "LIC-TASK9",
        "license_image": "mock://license.png",
        "legal_person_name": "Alice",
        "province": "ZJ",
        "city": "HZ",
        "district": "XH",
        "address": "No.100 Wensan Road",
        "contact_name": "Bob",
        "contact_phone": "13800138000",
        "contact_email": "ops@example.com",
        "attachments": [{"file_type": "business_license", "file_url": "mock://license.png"}],
    }


def _seed_tenants(pg_database):
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES (8301, 'Task9 Org', 'TASK9ORG', 'tenant_org')
        ON CONFLICT (org_code) DO NOTHING
        """
    )
    pg_database.execute(
        """
        INSERT INTO tenant (
            org_id, tenant_code, name, type, credit_code,
            province, city, status, approved_at
        )
        VALUES
            (8301, 'TASK9ACTIVE', 'Kanglin Task9 Active Store', 'health_store', '91330100TASK900001', 'ZJ', 'HZ', 'active', NOW()),
            (8301, 'TASK9PENDING', 'Kanglin Task9 Pending Store', 'health_store', '91330100TASK900002', 'ZJ', 'HZ', 'pending', NULL),
            (8301, 'TASK9REJECTED', 'Kanglin Task9 Rejected Store', 'health_store', '91330100TASK900003', 'ZJ', 'HZ', 'rejected', NULL),
            (8301, 'TASK9ACTIVE2', 'Kanglin Task9 Active Store 2', 'health_store', '91330100TASK900004', 'ZJ', 'HZ', 'active', NOW())
        ON CONFLICT (tenant_code) DO NOTHING
        """
    )


def _seed_f001_org_and_users(pg_database):
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES (8401, 'Task9 F001 Org', 'TASK9F001ORG', 'tenant_org')
        ON CONFLICT (org_code) DO NOTHING
        """
    )
    pg_database.execute(
        """
        INSERT INTO "user" (id, phone, password_hash, role, status)
        VALUES
          (9101, '13900009101', 'hash', 'super_admin', 'active'),
          (9102, '13900009102', 'hash', 'org_admin', 'active')
        ON CONFLICT (phone) DO NOTHING
        """
    )


def _tenant_id(pg_database, tenant_code: str) -> int:
    rows = pg_database.fetch_rows("SELECT id FROM tenant WHERE tenant_code = $1", tenant_code)
    return rows[0]["id"]


def _fetch_user(pg_database, user_id: int):
    rows = pg_database.fetch_rows(
        """
        SELECT id, phone, password_hash, role, status, real_name, id_card, verify_status, tenant_id
        FROM "user"
        WHERE id = $1
        """,
        user_id,
    )
    return rows[0] if rows else None


def _user_count_by_phone(pg_database, phone: str) -> int:
    rows = pg_database.fetch_rows('SELECT COUNT(*) AS user_count FROM "user" WHERE phone = $1', phone)
    return rows[0]["user_count"]


def _fetch_health_profile(pg_database, user_id: int):
    rows = pg_database.fetch_rows(
        """
        SELECT
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
        "SELECT COUNT(*) AS profile_count FROM health_profile WHERE user_id = $1",
        user_id,
    )
    return rows[0]["profile_count"]


def _operation_log_count(pg_database, tenant_id: int) -> int:
    rows = pg_database.fetch_rows(
        "SELECT COUNT(*) AS log_count FROM operation_log WHERE object_type = 'tenant' AND object_id = $1",
        tenant_id,
    )
    return rows[0]["log_count"]


def test_f002_real_db_full_user_onboarding_happy_path(real_db_client, pg_database):
    _seed_tenants(pg_database)
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260812_0016"

    user = _register(real_db_client, "13800139401")
    assert user["role"] == "member"
    assert user["status"] == "active"
    assert "password_hash" not in user

    identity_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/identity",
        json={"real_name": "Zhang San", "id_card": "110101199001011234"},
        headers=_member_headers(user["id"]),
    )
    assert identity_response.status_code == 200
    identity = identity_response.json()["data"]
    assert identity["verify_status"] == "submitted"
    assert identity["id_card_masked"] == "110101********1234"
    assert "id_card" not in identity

    create_profile_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=_member_headers(user["id"]),
    )
    assert create_profile_response.status_code == 201
    created_profile = create_profile_response.json()["data"]
    assert created_profile["user_id"] == user["id"]
    assert created_profile["medical_history"] == ["hypertension"]

    get_profile_response = real_db_client.get(
        f"/api/v1/users/{user['id']}/health-profile",
        headers=_member_headers(user["id"]),
    )
    assert get_profile_response.status_code == 200
    queried_profile = get_profile_response.json()["data"]
    assert queried_profile["birth_date"] == "1990-01-01"
    assert "password_hash" not in queried_profile
    assert "id_card" not in queried_profile
    assert "real_name" not in queried_profile

    active_response = real_db_client.get(
        "/api/v1/tenants/active?keyword=Task9&page=1&page_size=20",
        headers=_member_headers(user["id"]),
    )
    assert active_response.status_code == 200
    active_items = active_response.json()["data"]["items"]
    tenant_codes = {item["tenant_code"] for item in active_items}
    assert "TASK9ACTIVE" in tenant_codes
    assert "TASK9ACTIVE2" in tenant_codes
    assert "TASK9PENDING" not in tenant_codes
    assert "TASK9REJECTED" not in tenant_codes
    assert "credit_code" not in active_items[0]
    assert "license_no" not in active_items[0]
    assert "org_id" not in active_items[0]

    active_tenant_id = _tenant_id(pg_database, "TASK9ACTIVE")
    bind_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": active_tenant_id},
        headers=_member_headers(user["id"]),
    )
    assert bind_response.status_code == 200
    assert bind_response.json()["data"]["tenant_id"] == active_tenant_id

    db_user = _fetch_user(pg_database, user["id"])
    assert db_user["role"] == "member"
    assert db_user["status"] == "active"
    assert db_user["password_hash"] != "Secret12345"
    assert db_user["real_name"] == "Zhang San"
    assert db_user["id_card"] == "110101199001011234"
    assert db_user["verify_status"] == "submitted"
    assert db_user["tenant_id"] == active_tenant_id

    db_profile = _fetch_health_profile(pg_database, user["id"])
    assert db_profile["user_id"] == user["id"]
    assert db_profile["birth_date"].isoformat() == "1990-01-01"
    assert str(db_profile["height"]) == "165.5"
    assert str(db_profile["weight"]) == "55.0"
    assert json.loads(db_profile["medical_history"]) == ["hypertension"]
    assert json.loads(db_profile["allergy_history"]) == {"drug": ["penicillin"]}
    assert json.loads(db_profile["symptoms"]) == [{"name": "fatigue"}]


def test_f002_real_db_duplicate_registration_returns_409(real_db_client, pg_database):
    first = real_db_client.post("/api/v1/users/register", json={"phone": "13800139402", "password": "Secret12345"})
    second = real_db_client.post("/api/v1/users/register", json={"phone": "13800139402", "password": "Secret12345"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert _user_count_by_phone(pg_database, "13800139402") == 1


def test_f002_real_db_idor_guards_do_not_mutate_target_user(real_db_client, pg_database):
    _seed_tenants(pg_database)
    user_a = _register(real_db_client, "13800139403")
    user_b = _register(real_db_client, "13800139404")

    identity = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/identity",
        json={"real_name": "Li Si", "id_card": "110101199001011235"},
        headers=_member_headers(user_a["id"]),
    )
    create_profile = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/health-profile",
        json=_profile_payload(),
        headers=_member_headers(user_a["id"]),
    )
    query_profile = real_db_client.get(
        f"/api/v1/users/{user_b['id']}/health-profile",
        headers=_member_headers(user_a["id"]),
    )
    bind = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK9ACTIVE")},
        headers=_member_headers(user_a["id"]),
    )

    assert identity.status_code == 403
    assert create_profile.status_code == 403
    assert query_profile.status_code == 403
    assert bind.status_code == 403
    db_user_b = _fetch_user(pg_database, user_b["id"])
    assert db_user_b["real_name"] is None
    assert db_user_b["id_card"] is None
    assert db_user_b["verify_status"] is None
    assert db_user_b["tenant_id"] is None
    assert _health_profile_count(pg_database, user_b["id"]) == 0


def test_f002_real_db_duplicate_health_profile_returns_409(real_db_client, pg_database):
    user = _register(real_db_client, "13800139405")

    first = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=_member_headers(user["id"]),
    )
    second = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=_member_headers(user["id"]),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert _health_profile_count(pg_database, user["id"]) == 1


def test_f002_real_db_tenant_binding_guards(real_db_client, pg_database):
    _seed_tenants(pg_database)
    pending_user = _register(real_db_client, "13800139406")
    rejected_user = _register(real_db_client, "13800139407")
    missing_tenant_user = _register(real_db_client, "13800139408")
    duplicate_user = _register(real_db_client, "13800139409")
    active_tenant_id = _tenant_id(pg_database, "TASK9ACTIVE")
    second_active_tenant_id = _tenant_id(pg_database, "TASK9ACTIVE2")

    pending = real_db_client.post(
        f"/api/v1/users/{pending_user['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK9PENDING")},
        headers=_member_headers(pending_user["id"]),
    )
    rejected = real_db_client.post(
        f"/api/v1/users/{rejected_user['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK9REJECTED")},
        headers=_member_headers(rejected_user["id"]),
    )
    missing = real_db_client.post(
        f"/api/v1/users/{missing_tenant_user['id']}/tenant-binding",
        json={"tenant_id": 99999999},
        headers=_member_headers(missing_tenant_user["id"]),
    )
    first_binding = real_db_client.post(
        f"/api/v1/users/{duplicate_user['id']}/tenant-binding",
        json={"tenant_id": active_tenant_id},
        headers=_member_headers(duplicate_user["id"]),
    )
    second_binding = real_db_client.post(
        f"/api/v1/users/{duplicate_user['id']}/tenant-binding",
        json={"tenant_id": second_active_tenant_id},
        headers=_member_headers(duplicate_user["id"]),
    )

    assert pending.status_code == 409
    assert rejected.status_code == 409
    assert missing.status_code == 404
    assert first_binding.status_code == 200
    assert second_binding.status_code == 409
    assert _fetch_user(pg_database, pending_user["id"])["tenant_id"] is None
    assert _fetch_user(pg_database, rejected_user["id"])["tenant_id"] is None
    assert _fetch_user(pg_database, missing_tenant_user["id"])["tenant_id"] is None
    assert _fetch_user(pg_database, duplicate_user["id"])["tenant_id"] == active_tenant_id


@pytest.mark.parametrize("role", ["org_admin", "super_admin", "province_admin", "city_admin"])
def test_f002_real_db_non_member_roles_are_forbidden(real_db_client, pg_database, role):
    _seed_tenants(pg_database)
    user = _register(real_db_client, f"138001395{len(role):02d}")
    headers = _headers(role, user_id=user["id"])

    identity = real_db_client.post(
        f"/api/v1/users/{user['id']}/identity",
        json={"real_name": "Zhang San", "id_card": "110101199001011234"},
        headers=headers,
    )
    create_profile = real_db_client.post(
        f"/api/v1/users/{user['id']}/health-profile",
        json=_profile_payload(),
        headers=headers,
    )
    query_profile = real_db_client.get(f"/api/v1/users/{user['id']}/health-profile", headers=headers)
    active_tenants = real_db_client.get("/api/v1/tenants/active", headers=headers)
    bind_tenant = real_db_client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK9ACTIVE")},
        headers=headers,
    )

    assert identity.status_code == 403
    assert create_profile.status_code == 403
    assert query_profile.status_code == 403
    assert active_tenants.status_code == 403
    assert bind_tenant.status_code == 403
    assert _fetch_user(pg_database, user["id"])["tenant_id"] is None


def test_f001_approved_tenant_can_be_consumed_by_f002_binding(real_db_client, pg_database):
    _seed_f001_org_and_users(pg_database)
    member = _register(real_db_client, "13800139420")

    submit_response = real_db_client.post(
        "/api/v1/tenants",
        json=_tenant_payload("91330100MA20000999"),
        headers=_headers("org_admin", user_id=9102, org_id=8401),
    )
    assert submit_response.status_code == 200
    tenant_id = submit_response.json()["data"]["id"]

    approve_response = real_db_client.post(
        f"/api/v1/reviews/tenants/{tenant_id}/approve",
        json={"comment": "approved for F002 E2E", "grade": "standard"},
        headers=_headers("super_admin", user_id=9101),
    )
    assert approve_response.status_code == 200

    active_response = real_db_client.get(
        "/api/v1/tenants/active?keyword=Task9",
        headers=_member_headers(member["id"]),
    )
    assert active_response.status_code == 200
    assert any(item["tenant_id"] == tenant_id for item in active_response.json()["data"]["items"])

    before_log_count = _operation_log_count(pg_database, tenant_id)
    bind_response = real_db_client.post(
        f"/api/v1/users/{member['id']}/tenant-binding",
        json={"tenant_id": tenant_id},
        headers=_member_headers(member["id"]),
    )

    assert bind_response.status_code == 200
    assert _fetch_user(pg_database, member["id"])["tenant_id"] == tenant_id
    assert _operation_log_count(pg_database, tenant_id) == before_log_count
