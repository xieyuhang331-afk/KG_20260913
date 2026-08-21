import secrets
from uuid import UUID, uuid4

import pytest


pytestmark = pytest.mark.integration


def test_机构会员邀请创建与非空列表返回标准UUIDv7(
    pg_database,
    application_database,
    real_db_client,
) -> None:
    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    tenant_id = 97201
    user_id = 97202
    org_id = 97203
    phone = "13" + "9" + ("0" * 8)
    password = secrets.token_urlsafe(24)
    password_hash = hash_password(password).replace("'", "''")
    tenant_public_id = Uuid7Generator().generate()
    institution_invitation_id = uuid4()
    institution_application_id = uuid4()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'UUIDv7 county','UUIDV7-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'UUIDV7-TENANT','UUIDv7 institution','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({user_id},'13' || '9' || repeat('0',8),'{password_hash}','org_admin','active',{tenant_id});"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{institution_invitation_id}','UUIDv7 institution','HEALTH_STORE',decode('00','hex'),repeat('a',64),"
        f"'UUIDV7',{org_id},repeat('b',64),'ACTIVATED',0,now()+interval '1 day',{user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{institution_application_id}','{institution_invitation_id}',{user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3);"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )

    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.institution_application','SELECT')"
    ) is False

    login = real_db_client.post(
        "/api/v1/auth/login",
        json={"phone": phone, "password": password},
    )
    assert login.status_code == 200
    authorization = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    idempotency_key = "uuidv7-response-contract-create"

    created = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**authorization, "Idempotency-Key": idempotency_key},
        json={"mode": "SELF", "phone": "13" + "7" + ("1" * 8)},
    )
    listed = real_db_client.get(
        "/api/v1/institution/member-invitations",
        headers=authorization,
    )

    assert (created.status_code, listed.status_code) == (201, 200)
    created_data = created.json()
    invitation_id = UUID(created_data["invitation_id"])
    response_tenant_id = UUID(created_data["tenant_id"])
    assert invitation_id.version == 7
    assert response_tenant_id.version == 7
    assert response_tenant_id == tenant_public_id
    assert listed.json()["items"]
    listed_item = next(
        item for item in listed.json()["items"] if item["invitation_id"] == str(invitation_id)
    )
    assert UUID(listed_item["invitation_id"]).version == 7
    assert UUID(listed_item["tenant_id"]).version == 7

    side_effect_counts = (
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_service_invitation "
            f"WHERE invitation_id='{invitation_id}'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{invitation_id}' AND action='MEMBER_INVITATION_CREATED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_outbox "
            f"WHERE aggregate_id='{invitation_id}' AND event_type='MEMBER_INVITATION_CREATED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='INVITATION_CREATE' "
            f"AND idempotency_key='{idempotency_key}'"
        ),
    )
    assert side_effect_counts == (1, 1, 1, 1)

    conflict = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**authorization, "Idempotency-Key": idempotency_key},
        json={"mode": "SELF", "phone": "13" + "6" + ("2" * 8)},
    )
    assert conflict.status_code == 503
    assert conflict.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "request rejected",
    }

    replay = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**authorization, "Idempotency-Key": idempotency_key},
        json={"mode": "SELF", "phone": "13" + "7" + ("1" * 8)},
    )
    assert replay.status_code == 201
    assert replay.json() == created_data
    assert (
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_service_invitation "
            f"WHERE invitation_id='{invitation_id}'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{invitation_id}' AND action='MEMBER_INVITATION_CREATED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_outbox "
            f"WHERE aggregate_id='{invitation_id}' AND event_type='MEMBER_INVITATION_CREATED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='INVITATION_CREATE' "
            f"AND idempotency_key='{idempotency_key}'"
        ),
    ) == side_effect_counts
