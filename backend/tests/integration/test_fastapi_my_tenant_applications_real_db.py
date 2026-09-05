from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.integration


def _headers(*, user_id: int, role: str, org_id: int | None = None) -> dict:
    from app.core.security import create_access_token

    claims = {"sub": str(user_id), "role": role}
    if org_id is not None:
        claims["org_id"] = org_id
    token = create_access_token(claims)
    return {"Authorization": f"Bearer {token}"}


def _seed_applications(pg_database) -> None:
    pg_database.execute(
        'INSERT INTO public."user" (id, phone, password_hash, role, status) '
        "VALUES (86001, '13900086001', 'synthetic', 'org_admin', 'active') "
        "ON CONFLICT (id) DO NOTHING"
    )
    pg_database.execute(
        """
        INSERT INTO platform_org (id, org_name, org_code, org_type)
        VALUES
            (8601, 'Institution Query Org A', 'INSTQUERYA', 'tenant_org'),
            (8602, 'Institution Query Org B', 'INSTQUERYB', 'tenant_org')
        ON CONFLICT (org_code) DO NOTHING;

        INSERT INTO tenant (
            id, org_id, tenant_code, name, type, credit_code,
            province, city, contact_name, contact_phone, status,
            reviewed_at, approved_at, reject_reason
        )
        VALUES
            (
                86011, 8601, 'INSTQUERY-PENDING', 'Institution Query Pending', 'store',
                '913301008601100001', 'Zhejiang', 'Hangzhou', 'Contact A', '13800008601',
                'pending', NULL, NULL, NULL
            ),
            (
                86012, 8601, 'INSTQUERY-REJECTED', 'Institution Query Rejected', 'store',
                '913301008601200001', 'Zhejiang', 'Hangzhou', 'Contact A', '13800008601',
                'rejected', NOW(), NULL, 'missing license'
            ),
            (
                86021, 8602, 'INSTQUERY-OTHER', 'Institution Query Other Org', 'store',
                '913301008602100001', 'Jiangsu', 'Nanjing', 'Contact B', '13800008602',
                'active', NOW(), NOW(), NULL
            )
        ON CONFLICT (tenant_code) DO NOTHING
        """
    )


@pytest.fixture
def _application_actor_context(monkeypatch):
    from app.core.config import get_settings

    with monkeypatch.context() as scoped:
        scoped.setenv("KG_AUTH_CONTEXT_MAP", json.dumps({"86001": {"org_id": 8601}}))
        get_settings.cache_clear()
        try:
            yield
        finally:
            scoped.undo()
            get_settings.cache_clear()


def test_org_admin_lists_and_filters_only_own_applications(
    real_db_client, pg_database, _application_actor_context
):
    _seed_applications(pg_database)
    headers = _headers(user_id=86001, role="org_admin", org_id=8601)

    all_response = real_db_client.get(
        "/api/v1/tenants/my-applications?page=1&page_size=20",
        headers=headers,
    )
    rejected_response = real_db_client.get(
        "/api/v1/tenants/my-applications?status=rejected",
        headers=headers,
    )

    assert all_response.status_code == 200
    all_data = all_response.json()["data"]
    assert {item["tenant_code"] for item in all_data["items"]} == {
        "INSTQUERY-PENDING",
        "INSTQUERY-REJECTED",
    }
    assert all(item["tenant_code"] != "INSTQUERY-OTHER" for item in all_data["items"])
    assert all("org_id" not in item for item in all_data["items"])

    assert rejected_response.status_code == 200
    rejected_data = rejected_response.json()["data"]
    assert rejected_data["total"] == 1
    assert rejected_data["items"][0]["tenant_code"] == "INSTQUERY-REJECTED"
    assert rejected_data["items"][0]["reject_reason"] == "missing license"
