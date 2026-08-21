import secrets
from uuid import uuid4

import pytest


pytestmark = pytest.mark.integration


def test_机构登录与邀请列表使用分离CurrentnessAuthority(
    pg_database,
    application_database,
    real_db_client,
) -> None:
    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    tenant_id = 97101
    user_id = 97102
    org_id = 97103
    phone = "13" + "8" + ("0" * 8)
    password = secrets.token_urlsafe(24)
    password_hash = hash_password(password).replace("'", "''")
    tenant_public_id = Uuid7Generator().generate()
    invitation_id = uuid4()
    application_id = uuid4()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'Hotfix county','HOTFIX-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'HOTFIX-TENANT','Hotfix institution','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({user_id},'13' || '8' || repeat('0',8),'{password_hash}','org_admin','active',{tenant_id});"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{invitation_id}','Hotfix institution','HEALTH_STORE',decode('00','hex'),repeat('a',64),"
        f"'HOTFIX',{org_id},repeat('b',64),'ACTIVATED',0,now()+interval '1 day',{user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{application_id}','{invitation_id}',{user_id},'HEALTH_STORE','APPROVED','{{}}'::jsonb,'[]'::jsonb,"
        f"1,{tenant_id},'{tenant_public_id}',false,now(),now(),now(),now(),3)"
    )

    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.institution_application','SELECT')"
    ) is False

    login = real_db_client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert login.status_code == 200
    token = login.json()["data"]["access_token"]

    response = real_db_client.get(
        "/api/v1/institution/member-invitations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
