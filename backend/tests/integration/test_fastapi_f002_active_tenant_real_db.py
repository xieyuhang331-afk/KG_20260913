import pytest


pytestmark = pytest.mark.integration


def _headers(role: str = "member", *, user_id: int | None = None) -> dict:
    from app.core.security import create_access_token

    actor_id = {"member": 80011, "org_admin": 80012}[role] if user_id is None else user_id
    token = create_access_token({"sub": str(actor_id), "role": role})
    return {"Authorization": f"Bearer {token}"}


def _seed_active_tenant_test_data(pg_database):
    pg_database.execute(
        'INSERT INTO public."user" (id, phone, password_hash, role, status) '
        "VALUES (80011, '13900080011', 'synthetic', 'member', 'active'), "
        "(80012, '13900080012', 'synthetic', 'org_admin', 'active') "
        "ON CONFLICT (id) DO NOTHING"
    )
    assert pg_database.fetch_value(
        'SELECT count(*) FROM public."user" WHERE status=\'active\' '
        "AND tenant_id IS NULL AND ((id=80011 AND role='member') "
        "OR (id=80012 AND role='org_admin'))"
    ) == 2
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES (8001, 'Task7 Org', 'TASK7ORG', 'tenant_org')
        ON CONFLICT (org_code) DO NOTHING
        """
    )
    pg_database.execute(
        """
        INSERT INTO tenant (
            org_id, tenant_code, name, type, credit_code, license_no,
            license_image, legal_person_name, province, city, district,
            address, contact_name, contact_phone, status, approved_at
        )
        VALUES
            (8001, 'TASK7ACTIVEHZ', 'Kanglin West Lake Store', 'health_store', '91330100TASK700001',
             'LIC-ACTIVE', 'mock://license-active.png', 'Alice', 'ZJ', 'HZ', 'XH',
             'No.100 Wensan Road', 'Bob', '13800138000', 'active', NOW()),
            (8001, 'TASK7PENDING', 'Kanglin Pending Store', 'health_store', '91330100TASK700002',
             'LIC-PENDING', 'mock://license-pending.png', 'Carol', 'ZJ', 'HZ', 'XH',
             'No.200 Wensan Road', 'Dave', '13800138001', 'pending', NULL),
            (8001, 'TASK7REJECTED', 'Kanglin Rejected Store', 'health_store', '91330100TASK700003',
             'LIC-REJECTED', 'mock://license-rejected.png', 'Eve', 'JS', 'NJ', 'QH',
             'No.300 Zhongshan Road', 'Frank', '13800138002', 'rejected', NULL),
            (8001, 'TASK7ACTIVENB', 'Kanglin Ningbo Store', 'health_store', '91330100TASK700004',
             'LIC-NB', 'mock://license-nb.png', 'Grace', 'ZJ', 'NB', 'HS',
             'No.400 Harbor Road', 'Heidi', '13800138003', 'active', NOW())
        ON CONFLICT (tenant_code) DO NOTHING
        """
    )


def test_f002_real_db_member_lists_only_active_tenants(real_db_client, pg_database):
    _seed_active_tenant_test_data(pg_database)

    response = real_db_client.get("/api/v1/tenants/active?keyword=Kanglin", headers=_headers())

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    codes = {item["tenant_code"] for item in items}
    assert "TASK7ACTIVEHZ" in codes
    assert "TASK7ACTIVENB" in codes
    assert "TASK7PENDING" not in codes
    assert "TASK7REJECTED" not in codes

    missing_actor = real_db_client.get(
        "/api/v1/tenants/active", headers=_headers(user_id=80013)
    )
    assert missing_actor.status_code == 401
    body = missing_actor.json()
    assert set(body) == {"code", "message", "request_id", "field_errors", "retryable"}
    assert (body["code"], body["message"], body["field_errors"], body["retryable"]) == (
        "ACCESS_TOKEN_STALE", "request rejected", [], False,
    )
    assert missing_actor.headers["X-Request-ID"] == body["request_id"]
    assert missing_actor.headers["WWW-Authenticate"] == "Bearer"
    assert missing_actor.headers["Cache-Control"] == "no-store, private"
    assert missing_actor.headers["Pragma"] == "no-cache"


def test_f002_real_db_active_tenant_filters_and_pagination(real_db_client, pg_database):
    _seed_active_tenant_test_data(pg_database)

    response = real_db_client.get(
        "/api/v1/tenants/active?province=ZJ&city=HZ&keyword=West&page=1&page_size=1",
        headers=_headers(),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert [item["tenant_code"] for item in data["items"]] == ["TASK7ACTIVEHZ"]


def test_f002_real_db_active_tenant_response_does_not_expose_sensitive_fields(real_db_client, pg_database):
    _seed_active_tenant_test_data(pg_database)

    response = real_db_client.get("/api/v1/tenants/active?keyword=West", headers=_headers())

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert "credit_code" not in item
    assert "license_no" not in item
    assert "license_image" not in item
    assert "legal_person_name" not in item
    assert "contact_name" not in item
    assert "contact_email" not in item
    assert "org_id" not in item
    assert "reviewed_by" not in item


def test_f002_real_db_non_member_cannot_query_active_tenants(real_db_client, pg_database):
    _seed_active_tenant_test_data(pg_database)

    response = real_db_client.get("/api/v1/tenants/active", headers=_headers("org_admin"))

    assert response.status_code == 403
