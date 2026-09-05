from concurrent.futures import ThreadPoolExecutor
import json

import pytest


pytestmark = pytest.mark.integration


def _headers(user_id: int, role: str, **claims) -> dict[str, str]:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user_id), "role": role, **claims})
    return {"Authorization": f"Bearer {token}"}


def _seed(pg_database) -> None:
    pg_database.execute(
        'INSERT INTO public."user" '
        '(id,phone,password_hash,real_name,role,status,verify_status) VALUES '
        "(301001,'TESTPHONE01','x','测试总部管理员','super_admin','active','verified'),"
        "(301002,'TESTPHONE02','x','测试省管理员','province_admin','active','verified'),"
        "(301003,'TESTPHONE03','x','测试市管理员','city_admin','active','verified') "
        'ON CONFLICT (id) DO NOTHING'
    )
    pg_database.execute(
        "INSERT INTO public.platform_org "
        "(id,parent_id,org_name,org_code,org_type,sort_order,status,version,created_by,updated_by) VALUES "
        "(301100,NULL,'测试总部','TEST_HQ','headquarter',0,'active',1,301001,301001) "
        "ON CONFLICT (id) DO NOTHING"
    )


def test_P3机构权限矩阵精确收紧(pg_database, application_database, readonly_database):
    del pg_database
    application = application_database.fetch_rows(
        "SELECT "
        "has_table_privilege(current_user,'public.platform_org','SELECT') AS can_select,"
        "has_table_privilege(current_user,'public.platform_org','INSERT') AS can_insert,"
        "has_table_privilege(current_user,'public.platform_org','UPDATE') AS can_update,"
        "has_column_privilege(current_user,'public.platform_org','org_name','UPDATE') AS can_update_org_name,"
        "has_column_privilege(current_user,'public.platform_org','sort_order','UPDATE') AS can_update_sort_order,"
        "has_column_privilege(current_user,'public.platform_org','status','UPDATE') AS can_update_status,"
        "has_column_privilege(current_user,'public.platform_org','admin_id','UPDATE') AS can_update_admin_id,"
        "has_column_privilege(current_user,'public.platform_org','version','UPDATE') AS can_update_version,"
        "has_column_privilege(current_user,'public.platform_org','updated_at','UPDATE') AS can_update_updated_at,"
        "has_column_privilege(current_user,'public.platform_org','updated_by','UPDATE') AS can_update_updated_by,"
        "has_column_privilege(current_user,'public.platform_org','parent_id','UPDATE') AS can_update_parent_id,"
        "has_column_privilege(current_user,'public.platform_org','org_code','UPDATE') AS can_update_org_code,"
        "has_column_privilege(current_user,'public.platform_org','org_type','UPDATE') AS can_update_org_type,"
        "has_column_privilege(current_user,'public.platform_org','created_by','UPDATE') AS can_update_created_by,"
        "has_table_privilege(current_user,'public.platform_org','DELETE') AS can_delete,"
        "has_table_privilege(current_user,'public.platform_org','TRUNCATE') AS can_truncate,"
        "has_table_privilege(current_user,'public.platform_org','REFERENCES') AS can_references,"
        "has_table_privilege(current_user,'public.platform_org','TRIGGER') AS can_trigger,"
        "has_sequence_privilege(current_user,'public.platform_org_id_seq','USAGE') AS can_sequence,"
        "has_sequence_privilege(current_user,'public.platform_org_id_seq','SELECT') AS can_sequence_select,"
        "has_table_privilege(current_user,'public.operation_log','SELECT') AS audit_select,"
        "has_table_privilege(current_user,'public.operation_log','INSERT') AS audit_insert,"
        "has_table_privilege(current_user,'public.operation_log','UPDATE') AS audit_update,"
        "has_table_privilege(current_user,'public.operation_log','DELETE') AS audit_delete,"
        "has_table_privilege(current_user,'public.operation_log','TRUNCATE') AS audit_truncate,"
        "has_table_privilege(current_user,'public.operation_log','REFERENCES') AS audit_references,"
        "has_table_privilege(current_user,'public.operation_log','TRIGGER') AS audit_trigger,"
        "has_sequence_privilege(current_user,'public.operation_log_id_seq','USAGE') AS audit_sequence,"
        "has_sequence_privilege(current_user,'public.operation_log_id_seq','SELECT') AS audit_sequence_select"
    )[0]
    assert application == {
        "can_select": True,
        "can_insert": True,
        "can_update": False,
        "can_update_org_name": True, "can_update_sort_order": True,
        "can_update_status": True, "can_update_admin_id": True,
        "can_update_version": True, "can_update_updated_at": True,
        "can_update_updated_by": True, "can_update_parent_id": False,
        "can_update_org_code": False, "can_update_org_type": False,
        "can_update_created_by": False,
        "can_delete": False, "can_truncate": False,
        "can_references": False, "can_trigger": False,
        "can_sequence": True, "can_sequence_select": True,
        "audit_select": True, "audit_insert": True, "audit_update": False,
        "audit_delete": False, "audit_truncate": False, "audit_references": False,
        "audit_trigger": False, "audit_sequence": True, "audit_sequence_select": True,
    }
    readonly = readonly_database.fetch_rows(
        "SELECT "
        "has_table_privilege(current_user,'public.platform_org','SELECT') AS can_select,"
        "has_table_privilege(current_user,'public.platform_org','INSERT') AS can_insert,"
        "has_table_privilege(current_user,'public.platform_org','UPDATE') AS can_update,"
        "has_table_privilege(current_user,'public.platform_org','DELETE') AS can_delete,"
        "has_table_privilege(current_user,'public.platform_org','TRUNCATE') AS can_truncate,"
        "has_table_privilege(current_user,'public.platform_org','REFERENCES') AS can_references,"
        "has_table_privilege(current_user,'public.platform_org','TRIGGER') AS can_trigger,"
        "has_sequence_privilege(current_user,'public.platform_org_id_seq','USAGE') AS can_sequence,"
        "has_table_privilege(current_user,'public.operation_log','SELECT') AS audit_select,"
        "has_table_privilege(current_user,'public.operation_log','INSERT') AS audit_insert,"
        "has_sequence_privilege(current_user,'public.operation_log_id_seq','USAGE') AS audit_sequence"
    )[0]
    assert readonly == {
        "can_select": True,
        "can_insert": False,
        "can_update": False,
        "can_delete": False, "can_truncate": False, "can_references": False,
        "can_trigger": False, "can_sequence": False, "audit_select": True,
        "audit_insert": False, "audit_sequence": False,
    }


def test_P3机构创建候选排序与并发闭环(real_db_client, pg_database):
    _seed(pg_database)
    headers = _headers(301001, "super_admin")
    candidate = real_db_client.get(
        "/api/v1/platform/organizations/301100/admin-candidates", headers=headers
    )
    assert candidate.status_code == 200
    assert candidate.json() == {"items": [], "next_cursor": None}

    payloads = [
        {
            "org_name": "测试甲省",
            "org_code": "TEST_PROVINCE_A",
            "org_type": "province",
            "parent_id": 301100,
            "parent_expected_version": 1,
        },
        {
            "org_name": "测试乙省",
            "org_code": "TEST_PROVINCE_B",
            "org_type": "province",
            "parent_id": 301100,
            "parent_expected_version": 1,
        },
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda body: real_db_client.post(
            "/api/v1/platform/organizations", json=body, headers=headers
        ), payloads))
    assert sorted(response.status_code for response in responses) == [201, 409]
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.json() == {"detail": "ORGANIZATION_VERSION_CONFLICT"}
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.platform_org WHERE parent_id=301100"
    ) == 1
    assert pg_database.fetch_value(
        "SELECT version FROM public.platform_org WHERE id=301100"
    ) == 2

    winner = next(response.json() for response in responses if response.status_code == 201)
    second = real_db_client.post(
        "/api/v1/platform/organizations",
        json={
            "org_name": "测试丙省",
            "org_code": "TEST_PROVINCE_C",
            "org_type": "province",
            "parent_id": 301100,
            "parent_expected_version": 2,
        },
        headers=headers,
    )
    assert second.status_code == 201
    second_data = second.json()
    order = real_db_client.put(
        "/api/v1/platform/organizations/301100/children/order",
        json={
            "parent_expected_version": 3,
            "items": [
                {"organization_id": winner["id"], "expected_version": 1, "sort_order": 1},
                {"organization_id": second_data["id"], "expected_version": 1, "sort_order": 0},
            ],
        },
        headers=headers,
    )
    assert order.status_code == 200
    body = order.json()
    assert body["parent_version"] == 4
    assert [item["version"] for item in body["items"]] == [2, 2]


def test_P3机构管理员候选最小PII与本人未分配(real_db_client, pg_database):
    _seed(pg_database)
    headers = _headers(301001, "super_admin")
    pg_database.execute(
        'INSERT INTO public."user" '
        '(id,phone,password_hash,real_name,role,status,verify_status) VALUES '
        "(300998,'TESTPHONE98','x','   ','province_admin','active','verified'),"
        "(300999,'TESTPHONE99','x',E'bad\\nname','province_admin','active','verified') "
        'ON CONFLICT (id) DO NOTHING'
    )
    created = real_db_client.post(
        "/api/v1/platform/organizations",
        json={
            "org_name": "测试候选省",
            "org_code": "TEST_PROVINCE_D",
            "org_type": "province",
            "parent_id": 301100,
            "parent_expected_version": pg_database.fetch_value(
                "SELECT version FROM public.platform_org WHERE id=301100"
            ),
        },
        headers=headers,
    )
    assert created.status_code == 201
    organization_id = created.json()["id"]
    candidates = real_db_client.get(
        f"/api/v1/platform/organizations/{organization_id}/admin-candidates?page_size=1",
        headers=headers,
    )
    assert candidates.status_code == 200
    assert candidates.json()["items"] == [
        {
            "user_id": 301002,
            "display_name": "测试省管理员",
            "role": "province_admin",
            "assignment_status": "unassigned",
        }
    ]
    assert set(candidates.json()["items"][0]) == {
        "user_id", "display_name", "role", "assignment_status"
    }

    pg_database.execute(
        "INSERT INTO public.tenant (id,tenant_code,name,type,province,city,status) "
        "VALUES (301200,'TEST_TENANT','测试机构','clinic','测试省','测试市','active') "
        "ON CONFLICT (id) DO NOTHING"
    )
    pg_database.execute(
        "INSERT INTO public.\"user\" "
        "(id,phone,password_hash,real_name,role,status,verify_status,tenant_id) VALUES "
        "(301004,'TESTPHONE04','x','测试机构管理员','org_admin','active','verified',301200) "
        "ON CONFLICT (id) DO NOTHING"
    )
    me = real_db_client.get(
        "/api/v1/organizations/me", headers=_headers(301004, "org_admin", tenant_id=301200)
    )
    assert me.status_code == 200
    assert me.json()["assignment_status"] == "unassigned"
    assert me.json()["organization_id"] is None
    assert me.json()["organization_path"] == []


@pytest.fixture
def _descendant_scope_actor_context(monkeypatch):
    from app.core.config import get_settings

    with monkeypatch.context() as scoped:
        scoped.setenv(
            "KG_AUTH_CONTEXT_MAP",
            json.dumps({"301002": {"province": "TEST_SCOPE_PROVINCE"}}),
        )
        get_settings.cache_clear()
        try:
            yield
        finally:
            scoped.undo()
            get_settings.cache_clear()


def test_tenant_descendant_scope_is_fail_closed_for_legacy_archived_and_corrupted_nodes(
    real_db_client, pg_database, _descendant_scope_actor_context
):
    _seed(pg_database)
    pg_database.execute(
        "INSERT INTO public.platform_org "
        "(id,parent_id,org_name,org_code,org_type,sort_order,status,admin_id,version,created_by,updated_by) VALUES "
        "(302100,301100,'Test Scope Province','TEST_SCOPE_PROVINCE','province',0,'active',301002,1,301001,301001),"
        "(302101,302100,'Test Canonical City','TEST_SCOPE_CITY','city',0,'inactive',NULL,1,301001,301001),"
        "(302102,302100,'Test Legacy Node','TEST_SCOPE_LEGACY','tenant_org',1,'active',NULL,1,301001,301001),"
        "(302103,302100,'Test Archived City','TEST_SCOPE_ARCHIVED','city',2,'archived',NULL,1,301001,301001) "
        "ON CONFLICT (id) DO NOTHING"
    )
    pg_database.execute(
        "INSERT INTO public.tenant (id,tenant_code,name,type,province,city,status,org_id) VALUES "
        "(302200,'TEST_SCOPE_TENANT_ROOT','Test Root Tenant','clinic','Test','Test','active',302100),"
        "(302201,'TEST_SCOPE_TENANT_CANONICAL','Test Canonical Tenant','clinic','Test','Test','active',302101),"
        "(302202,'TEST_SCOPE_TENANT_LEGACY','Test Legacy Tenant','clinic','Test','Test','active',302102),"
        "(302203,'TEST_SCOPE_TENANT_ARCHIVED','Test Archived Tenant','clinic','Test','Test','active',302103) "
        "ON CONFLICT (id) DO NOTHING"
    )
    headers = _headers(301002, "province_admin", province="TEST_SCOPE_PROVINCE")
    response = real_db_client.get(
        "/api/v1/platform/organizations/302100/tenants?include_descendants=true",
        headers=headers,
    )
    assert response.status_code == 200
    assert {item["tenant_code"] for item in response.json()["items"]} == {
        "TEST_SCOPE_TENANT_ROOT", "TEST_SCOPE_TENANT_CANONICAL"
    }

    pg_database.execute(
        "INSERT INTO public.platform_org "
        "(id,parent_id,org_name,org_code,org_type,sort_order,status,version,created_by,updated_by) VALUES "
        "(302104,302100,'Test Corrupted Node','TEST_SCOPE_CORRUPTED','corrupted',3,'active',1,301001,301001) "
        "ON CONFLICT (id) DO NOTHING"
    )
    corrupted = real_db_client.get(
        "/api/v1/platform/organizations/302100/tenants?include_descendants=true",
        headers=headers,
    )
    assert corrupted.status_code == 503
    assert corrupted.json() == {"detail": "ORGANIZATION_DATA_CORRUPTED"}


def test_mutation_commit_outcome_unknown_confirms_fresh_database_state(
    real_db_client, pg_database, monkeypatch
):
    from app.modules.organization.repository import OrganizationUnitOfWork

    _seed(pg_database)
    original_commit = OrganizationUnitOfWork.commit

    async def committed_then_unknown(self):
        await original_commit(self)
        raise RuntimeError("fixed test commit outcome unknown")

    monkeypatch.setattr(OrganizationUnitOfWork, "commit", committed_then_unknown)
    parent_version = pg_database.fetch_value(
        "SELECT version FROM public.platform_org WHERE id=301100"
    )
    response = real_db_client.post(
        "/api/v1/platform/organizations",
        json={
            "org_name": "Test Outcome Province",
            "org_code": "TEST_OUTCOME_PROVINCE",
            "org_type": "province",
            "parent_id": 301100,
            "parent_expected_version": parent_version,
        },
        headers=_headers(301001, "super_admin"),
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "ORGANIZATION_COMMIT_OUTCOME_UNKNOWN"}
    created = pg_database.fetch_rows(
        "SELECT id,parent_id,org_code,version FROM public.platform_org "
        "WHERE org_code='TEST_OUTCOME_PROVINCE'"
    )
    assert len(created) == 1
    assert created[0]["parent_id"] == 301100
    assert created[0]["version"] == 1
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.operation_log "
        "WHERE module='organization' AND object_type='platform_org' "
        f"AND object_id={created[0]['id']} AND action='organization_created'"
    ) == 1
