import json

import pytest


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fixed_authority_context(monkeypatch):
    from app.core.config import get_settings

    context = {
        "100": {"org_id": 20}, "101": {"org_id": 30},
        "2": {"province": "ZJ"}, "3": {"province": "ZJ", "city": "HZ"},
    }
    with monkeypatch.context() as scoped:
        scoped.setenv("KG_AUTH_CONTEXT_MAP", json.dumps(context))
        get_settings.cache_clear()
        try:
            yield
        finally:
            scoped.undo()
            get_settings.cache_clear()


def _headers(role, *, user_id, org_id=None, province=None, city=None):
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


def _payload(credit_code, *, province="ZJ", city="HZ"):
    return {
        "name": f"Kanglin {credit_code[-4:]} Store",
        "type": "health_store",
        "credit_code": credit_code,
        "license_no": "LIC-20260728",
        "license_image": "mock://license.png",
        "legal_person_name": "Alice",
        "province": province,
        "city": city,
        "district": "XH",
        "address": "No.100 Wensan Road",
        "contact_name": "Bob",
        "contact_phone": "13800138000",
        "contact_email": "ops@example.com",
        "attachments": [
            {
                "file_type": "business_license",
                "file_url": "mock://license.png",
            }
        ],
    }


def _seed_org_and_users(pg_database):
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES
          (20, 'Org 20', 'ORG20', 'tenant_org'),
          (30, 'Org 30', 'ORG30', 'tenant_org')
        ON CONFLICT (org_code) DO NOTHING
        """
    )
    pg_database.execute(
        """
        INSERT INTO "user" (id, phone, password_hash, role)
        VALUES
          (1, '13900000001', 'hash', 'super_admin'),
          (2, '13900000002', 'hash', 'province_admin'),
          (3, '13900000003', 'hash', 'city_admin'),
          (100, '13900000100', 'hash', 'org_admin'),
          (101, '13900000101', 'hash', 'org_admin')
        ON CONFLICT (phone) DO NOTHING
        """
    )


def _submit(real_db_client, credit_code, *, org_id=20, province="ZJ", city="HZ"):
    response = real_db_client.post(
        "/api/v1/tenants",
        json=_payload(credit_code, province=province, city=city),
        headers=_headers("org_admin", user_id={20: 100, 30: 101}[org_id], org_id=org_id),
    )
    if response.status_code != 200:
        pytest.fail(f"C1_SUBMIT_STATUS_{response.status_code}", pytrace=False)
    return response


def _fetch_tenant(pg_database, tenant_id):
    return pg_database.fetch_rows("SELECT * FROM tenant WHERE id = $1", tenant_id)[0]


def _fetch_attachments(pg_database, tenant_id):
    return pg_database.fetch_rows(
        """
        SELECT tenant_id, file_type, file_url
        FROM tenant_attachment
        WHERE tenant_id = $1
        ORDER BY id
        """,
        tenant_id,
    )


def _fetch_review_logs(pg_database, tenant_id):
    return pg_database.fetch_rows(
        """
        SELECT tenant_id, reviewer_id, action, grade, comment
        FROM tenant_review_log
        WHERE tenant_id = $1
        ORDER BY id
        """,
        tenant_id,
    )


def _fetch_operation_logs(pg_database, tenant_id):
    return pg_database.fetch_rows(
        """
        SELECT operator_id, module, object_type, object_id, action,
               payload->>'tenant_id' AS payload_tenant_id,
               payload->>'status' AS payload_status,
               payload->>'action' AS payload_action
        FROM operation_log
        WHERE object_id = $1
        ORDER BY id
        """,
        tenant_id,
    )


def test_f001_real_db_approve_happy_path(real_db_client, pg_database):
    _seed_org_and_users(pg_database)

    submit_response = _submit(real_db_client, "91330100MA10000123")
    assert submit_response.status_code == 200
    tenant_id = submit_response.json()["data"]["id"]

    queue_response = real_db_client.get(
        "/api/v1/reviews/queue/tenant",
        headers=_headers("super_admin", user_id=1),
    )
    detail_response = real_db_client.get(
        f"/api/v1/reviews/tenants/{tenant_id}",
        headers=_headers("super_admin", user_id=1),
    )
    approve_response = real_db_client.post(
        f"/api/v1/reviews/tenants/{tenant_id}/approve",
        json={"comment": "approved", "grade": "flagship"},
        headers=_headers("super_admin", user_id=1),
    )
    status_response = real_db_client.get(
        f"/api/v1/tenants/{tenant_id}/application-status",
        headers=_headers("org_admin", user_id=100, org_id=20),
    )

    assert queue_response.status_code == 200
    assert detail_response.status_code == 200
    assert approve_response.status_code == 200
    assert status_response.status_code == 200

    submit_data = submit_response.json()["data"]
    assert submit_data["status"] == "pending"
    assert submit_data["attachment_count"] == 1
    assert submit_data["tenant_code"]

    queue_items = queue_response.json()["data"]["items"]
    assert any(item["tenant_id"] == tenant_id and item["attachment_count"] == 1 for item in queue_items)
    assert detail_response.json()["data"]["status"]["current"] == "pending"
    assert detail_response.json()["data"]["attachments"][0]["file_type"] == "business_license"
    assert approve_response.json()["data"]["status"] == "active"
    assert status_response.json()["data"]["status"] == "active"
    assert "org_id" not in status_response.json()["data"]

    tenant = _fetch_tenant(pg_database, tenant_id)
    assert tenant["org_id"] == 20
    assert tenant["status"] == "active"
    assert tenant["reviewed_by"] == 1
    assert tenant["reviewed_at"] is not None
    assert tenant["approved_at"] is not None
    assert tenant["reject_reason"] is None
    assert tenant["grade"] == "flagship"

    attachments = _fetch_attachments(pg_database, tenant_id)
    assert attachments == [
        {
            "tenant_id": tenant_id,
            "file_type": "business_license",
            "file_url": "mock://license.png",
        }
    ]

    review_logs = _fetch_review_logs(pg_database, tenant_id)
    assert review_logs == [
        {
            "tenant_id": tenant_id,
            "reviewer_id": 1,
            "action": "approved",
            "grade": "flagship",
            "comment": "approved",
        }
    ]

    operation_logs = _fetch_operation_logs(pg_database, tenant_id)
    assert operation_logs == [
        {
            "operator_id": 1,
            "module": "tenant",
            "object_type": "tenant",
            "object_id": tenant_id,
            "action": "tenant_onboarding_approved",
            "payload_tenant_id": str(tenant_id),
            "payload_status": "active",
            "payload_action": "approved",
        }
    ]


def test_f001_real_db_reject_path(real_db_client, pg_database):
    _seed_org_and_users(pg_database)

    submit_response = _submit(real_db_client, "91330100MA10000999")
    tenant_id = submit_response.json()["data"]["id"]

    reject_response = real_db_client.post(
        f"/api/v1/reviews/tenants/{tenant_id}/reject",
        json={"reason": "license image is unclear"},
        headers=_headers("super_admin", user_id=1),
    )
    status_response = real_db_client.get(
        f"/api/v1/tenants/{tenant_id}/application-status",
        headers=_headers("org_admin", user_id=100, org_id=20),
    )

    assert reject_response.status_code == 200
    assert status_response.status_code == 200
    assert status_response.json()["data"]["status"] == "rejected"
    assert status_response.json()["data"]["reject_reason"] == "license image is unclear"

    tenant = _fetch_tenant(pg_database, tenant_id)
    assert tenant["status"] == "rejected"
    assert tenant["reviewed_by"] == 1
    assert tenant["reviewed_at"] is not None
    assert tenant["approved_at"] is None
    assert tenant["reject_reason"] == "license image is unclear"

    review_logs = _fetch_review_logs(pg_database, tenant_id)
    assert review_logs[0]["action"] == "rejected"
    assert review_logs[0]["grade"] is None
    assert review_logs[0]["comment"] == "license image is unclear"

    operation_logs = _fetch_operation_logs(pg_database, tenant_id)
    assert operation_logs[0]["action"] == "tenant_onboarding_rejected"
    assert operation_logs[0]["payload_status"] == "rejected"
    assert operation_logs[0]["payload_action"] == "rejected"


def test_f001_real_db_non_pending_cannot_be_reviewed_again(real_db_client, pg_database):
    _seed_org_and_users(pg_database)

    submit_response = _submit(real_db_client, "91330100MA10000888")
    tenant_id = submit_response.json()["data"]["id"]
    first_approve = real_db_client.post(
        f"/api/v1/reviews/tenants/{tenant_id}/approve",
        json={},
        headers=_headers("super_admin", user_id=1),
    )
    second_approve = real_db_client.post(
        f"/api/v1/reviews/tenants/{tenant_id}/approve",
        json={},
        headers=_headers("super_admin", user_id=1),
    )
    reject_after_active = real_db_client.post(
        f"/api/v1/reviews/tenants/{tenant_id}/reject",
        json={"reason": "late reject"},
        headers=_headers("super_admin", user_id=1),
    )

    assert first_approve.status_code == 200
    assert second_approve.status_code == 409
    assert reject_after_active.status_code == 409
    assert _fetch_tenant(pg_database, tenant_id)["status"] == "active"
    assert len(_fetch_review_logs(pg_database, tenant_id)) == 1
    assert len(_fetch_operation_logs(pg_database, tenant_id)) == 1


def test_f001_real_db_org_id_idor_is_blocked(real_db_client, pg_database):
    _seed_org_and_users(pg_database)

    own_response = _submit(real_db_client, "91330100MA10000111", org_id=20)
    other_response = _submit(real_db_client, "91330100MA10000222", org_id=30)
    own_tenant_id = own_response.json()["data"]["id"]
    other_tenant_id = other_response.json()["data"]["id"]

    own_status = real_db_client.get(
        f"/api/v1/tenants/{own_tenant_id}/application-status",
        headers=_headers("org_admin", user_id=100, org_id=20),
    )
    cross_org_status = real_db_client.get(
        f"/api/v1/tenants/{other_tenant_id}/application-status",
        headers=_headers("org_admin", user_id=100, org_id=20),
    )

    assert own_status.status_code == 200
    assert own_status.json()["data"]["status"] == "pending"
    assert "org_id" not in own_status.json()["data"]
    assert cross_org_status.status_code == 403


def test_f001_real_db_region_scope_is_enforced(real_db_client, pg_database):
    _seed_org_and_users(pg_database)

    zj_response = _submit(real_db_client, "91330100MA10000333", province="ZJ", city="HZ")
    js_response = _submit(real_db_client, "91330100MA10000444", province="JS", city="NJ")
    nb_response = _submit(real_db_client, "91330100MA10000555", province="ZJ", city="NB")
    zj_tenant_id = zj_response.json()["data"]["id"]
    js_tenant_id = js_response.json()["data"]["id"]
    nb_tenant_id = nb_response.json()["data"]["id"]

    province_queue = real_db_client.get(
        "/api/v1/reviews/queue/tenant",
        headers=_headers("province_admin", user_id=2, province="ZJ"),
    )
    city_queue = real_db_client.get(
        "/api/v1/reviews/queue/tenant",
        headers=_headers("city_admin", user_id=3, province="ZJ", city="HZ"),
    )
    province_cross_detail = real_db_client.get(
        f"/api/v1/reviews/tenants/{js_tenant_id}",
        headers=_headers("province_admin", user_id=2, province="ZJ"),
    )
    city_cross_detail = real_db_client.get(
        f"/api/v1/reviews/tenants/{nb_tenant_id}",
        headers=_headers("city_admin", user_id=3, province="ZJ", city="HZ"),
    )
    city_own_approve = real_db_client.post(
        f"/api/v1/reviews/tenants/{zj_tenant_id}/approve",
        json={},
        headers=_headers("city_admin", user_id=3, province="ZJ", city="HZ"),
    )

    assert province_queue.status_code == 200
    assert {item["tenant_id"] for item in province_queue.json()["data"]["items"]}.issuperset(
        {zj_tenant_id, nb_tenant_id}
    )
    assert city_queue.status_code == 200
    assert [item["tenant_id"] for item in city_queue.json()["data"]["items"] if item["tenant_id"] in {zj_tenant_id, nb_tenant_id}] == [zj_tenant_id]
    assert province_cross_detail.status_code == 403
    assert city_cross_detail.status_code == 403
    assert city_own_approve.status_code == 200
    assert _fetch_tenant(pg_database, js_tenant_id)["status"] == "pending"
    assert _fetch_tenant(pg_database, nb_tenant_id)["status"] == "pending"
    assert _fetch_tenant(pg_database, zj_tenant_id)["status"] == "active"
