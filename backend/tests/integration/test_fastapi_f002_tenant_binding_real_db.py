import pytest


pytestmark = pytest.mark.integration


def _register(real_db_client, phone: str):
    response = real_db_client.post(
        "/api/v1/users/register",
        json={"phone": phone, "password": "Secret12345"},
    )
    assert response.status_code == 200
    return response.json()["data"]


def _headers(role: str = "member", *, user_id: int) -> dict:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _seed_tenants(pg_database):
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES (8101, 'Task8 Org', 'TASK8ORG', 'tenant_org')
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
            (8101, 'TASK8ACTIVE', 'Kanglin Active Store', 'health_store', '91330100TASK800001', 'ZJ', 'HZ', 'active', NOW()),
            (8101, 'TASK8PENDING', 'Kanglin Pending Store', 'health_store', '91330100TASK800002', 'ZJ', 'HZ', 'pending', NULL),
            (8101, 'TASK8REJECTED', 'Kanglin Rejected Store', 'health_store', '91330100TASK800003', 'ZJ', 'HZ', 'rejected', NULL),
            (8101, 'TASK8ACTIVE2', 'Kanglin Active Store 2', 'health_store', '91330100TASK800004', 'ZJ', 'HZ', 'active', NOW())
        ON CONFLICT (tenant_code) DO NOTHING
        """
    )


def _tenant_id(pg_database, tenant_code: str) -> int:
    rows = pg_database.fetch_rows(
        "SELECT id FROM tenant WHERE tenant_code = $1",
        tenant_code,
    )
    return rows[0]["id"]


def _user_tenant_id(pg_database, user_id: int):
    rows = pg_database.fetch_rows(
        'SELECT tenant_id FROM "user" WHERE id = $1',
        user_id,
    )
    return rows[0]["tenant_id"] if rows else None


def test_f002_real_db_member_binds_active_tenant(real_db_client, pg_database):
    _seed_tenants(pg_database)
    user = _register(real_db_client, "13800139301")
    active_tenant_id = _tenant_id(pg_database, "TASK8ACTIVE")

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": active_tenant_id},
        headers=_headers(user_id=user["id"]),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_id"] == user["id"]
    assert data["tenant_id"] == active_tenant_id
    assert data["tenant_code"] == "TASK8ACTIVE"
    assert data["tenant_name"] == "Kanglin Active Store"
    assert data["bound_at"] is not None
    assert _user_tenant_id(pg_database, user["id"]) == active_tenant_id


def test_f002_real_db_pending_and_rejected_tenants_cannot_be_bound(real_db_client, pg_database):
    _seed_tenants(pg_database)
    pending_user = _register(real_db_client, "13800139302")
    rejected_user = _register(real_db_client, "13800139303")

    pending_response = real_db_client.post(
        f"/api/v1/users/{pending_user['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK8PENDING")},
        headers=_headers(user_id=pending_user["id"]),
    )
    rejected_response = real_db_client.post(
        f"/api/v1/users/{rejected_user['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK8REJECTED")},
        headers=_headers(user_id=rejected_user["id"]),
    )

    assert pending_response.status_code == 409
    assert rejected_response.status_code == 409
    assert _user_tenant_id(pg_database, pending_user["id"]) is None
    assert _user_tenant_id(pg_database, rejected_user["id"]) is None


def test_f002_real_db_duplicate_binding_does_not_overwrite(real_db_client, pg_database):
    _seed_tenants(pg_database)
    user = _register(real_db_client, "13800139304")
    first_tenant_id = _tenant_id(pg_database, "TASK8ACTIVE")
    second_tenant_id = _tenant_id(pg_database, "TASK8ACTIVE2")

    first_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": first_tenant_id},
        headers=_headers(user_id=user["id"]),
    )
    second_response = real_db_client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": second_tenant_id},
        headers=_headers(user_id=user["id"]),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "User already bound to tenant"
    assert _user_tenant_id(pg_database, user["id"]) == first_tenant_id


def test_f002_real_db_tenant_binding_idor_is_blocked(real_db_client, pg_database):
    _seed_tenants(pg_database)
    user_a = _register(real_db_client, "13800139305")
    user_b = _register(real_db_client, "13800139306")

    response = real_db_client.post(
        f"/api/v1/users/{user_b['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK8ACTIVE")},
        headers=_headers(user_id=user_a["id"]),
    )

    assert response.status_code == 403
    assert _user_tenant_id(pg_database, user_b["id"]) is None


def test_f002_real_db_non_member_cannot_bind_tenant(real_db_client, pg_database):
    _seed_tenants(pg_database)
    user = _register(real_db_client, "13800139307")

    response = real_db_client.post(
        f"/api/v1/users/{user['id']}/tenant-binding",
        json={"tenant_id": _tenant_id(pg_database, "TASK8ACTIVE")},
        headers=_headers(role="org_admin", user_id=user["id"]),
    )

    assert response.status_code == 403
    assert _user_tenant_id(pg_database, user["id"]) is None
