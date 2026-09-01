import secrets
from uuid import UUID

import pytest

from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)


pytestmark = pytest.mark.integration


def test_机构会员邀请创建与非空列表返回标准UUIDv7(
    pg_database,
    application_database,
    real_db_client,
) -> None:
    from app.core.uuid_generator import Uuid7Generator

    tenant_id = 97201
    org_id = 97203
    phone = "13" + "9" + ("0" * 8)
    password = secrets.token_urlsafe(24)
    tenant_public_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'UUIDv7 county','UUIDV7-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'UUIDV7-TENANT','UUIDv7 institution','store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database,
        real_db_client,
        phone=phone,
        password=password,
        org_id=org_id,
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        institution_name="UUIDv7 institution",
        pilot_batch_code="UUIDV7",
        service_tags=("GLUCOSE_METABOLISM",),
    )
    pg_database.execute(
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )

    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.institution_application','SELECT')"
    ) is False

    authorization = _login(
        real_db_client,
        phone,
        password,
        totp_secret=str(activated["totp_secret"]),
    )
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
