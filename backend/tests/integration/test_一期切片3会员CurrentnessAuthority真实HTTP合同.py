from __future__ import annotations

import os
import secrets
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url


pytestmark = pytest.mark.integration

FUNCTION_SIGNATURE = (
    "public.slice3_member_currentness_authority_v1(bigint,character varying)"
)
ENROLLMENT_LIST_FIELDS = {
    "enrollment_id",
    "tenant_id",
    "subject_member_id",
    "proxy_member_id",
    "mode",
    "status",
    "service_scope_tags",
    "current_identity_verification_id",
    "current_assignment_id",
    "service_case_id",
    "accepted_at",
    "identity_verified_at",
    "case_created_at",
    "version",
}
ENROLLMENT_DETAIL_FIELDS = {"identity", "proxy", "consents", "assignment"}


def _seed_current_member_and_institution(pg_database) -> dict[str, object]:
    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    tenant_id = 97401
    admin_user_id = 97402
    member_user_id = 97403
    org_id = 97404
    member_id = Uuid7Generator().generate()
    tenant_public_id = Uuid7Generator().generate()
    institution_invitation_id = uuid4()
    institution_application_id = uuid4()
    self_link_id = uuid4()
    evidence_id = uuid4()
    admin_phone = "13" + "9" + ("4" * 8)
    member_phone = "13" + "7" + ("5" * 8)
    admin_password = secrets.token_urlsafe(24)
    member_password = secrets.token_urlsafe(24)
    admin_hash = hash_password(admin_password).replace("'", "''")
    member_hash = hash_password(member_password).replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'Authority county','AUTHORITY-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'AUTHORITY-TENANT','Authority institution','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({admin_user_id},'13' || '9' || repeat('4',8),'{admin_hash}','org_admin','active',{tenant_id}),"
        f"({member_user_id},'13' || '7' || repeat('5',8),'{member_hash}','member','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{member_id}','M0123456789ABCDEFGHJK','registration','created',1,now(),now());"
        "INSERT INTO identity.user_member_self_link("
        "link_id,user_ref,member_id,source,eligibility_decision_ref,establishment_basis,"
        "establishment_record_ref,created_at) VALUES ("
        f"'{self_link_id}',{member_user_id},'{member_id}','REGISTRATION_VERIFIED','{evidence_id}',"
        f"'REGISTRATION_VERIFIED_BOOTSTRAP','{evidence_id}',now());"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{institution_invitation_id}','Authority institution','HEALTH_STORE',decode('00','hex'),repeat('a',64),"
        f"'AUTHORITY',{org_id},repeat('b',64),'ACTIVATED',0,now()+interval '1 day',{admin_user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{institution_application_id}','{institution_invitation_id}',{admin_user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3);"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    return {
        "admin_phone": admin_phone,
        "admin_password": admin_password,
        "member_phone": member_phone,
        "member_password": member_password,
        "member_user_id": member_user_id,
        "member_id": member_id,
    }


def _seed_current_member_only(
    pg_database,
    *,
    user_id: int = 97503,
    phone_digit: str = "6",
    member_no: str = "M1123456789ABCDEFGHJK",
) -> dict[str, object]:
    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    member_id = Uuid7Generator().generate()
    phone = "13" + phone_digit + (phone_digit * 8)
    password = secrets.token_urlsafe(24)
    password_hash = hash_password(password).replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({user_id},'{phone}','{password_hash}','member','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{member_id}','{member_no}','registration','created',1,now(),now());"
        "INSERT INTO identity.user_member_self_link("
        "link_id,user_ref,member_id,source,eligibility_decision_ref,establishment_basis,"
        "establishment_record_ref,created_at) VALUES ("
        f"'{uuid4()}',{user_id},'{member_id}','REGISTRATION_VERIFIED','{uuid4()}',"
        f"'REGISTRATION_VERIFIED_BOOTSTRAP','{uuid4()}',now())"
    )
    return {
        "user_id": user_id,
        "member_id": member_id,
        "phone": phone,
        "password": password,
    }


def _login(real_db_client, phone: str, password: str) -> dict[str, str]:
    response = real_db_client.post(
        "/api/v1/auth/login", json={"phone": phone, "password": password}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _enforce_application_self_link_select_denied(pg_database) -> None:
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    pg_database.execute(
        "REVOKE SELECT ON TABLE identity.user_member_self_link "
        f'FROM "{application_role}"'
    )


def _accept_side_effects(pg_database, invitation_id: UUID, key: str) -> tuple[int, ...]:
    return (
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.service_enrollment "
            f"WHERE invitation_id='{invitation_id}'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            "WHERE action='MEMBER_ENROLLMENT_ACCEPTED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_outbox "
            "WHERE event_type='MEMBER_ENROLLMENT_ACCEPTED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='ENROLLMENT_ACCEPT' "
            f"AND idempotency_key='{key}'"
        ),
    )


def _business_snapshot(pg_database, invitation_id: UUID) -> tuple[object, ...]:
    enrollment = pg_database.fetch_rows(
        "SELECT status,version FROM public.service_enrollment "
        "WHERE invitation_id=$1",
        invitation_id,
    )
    return (
        pg_database.fetch_value("SELECT COUNT(*) FROM public.service_enrollment"),
        len(enrollment),
        enrollment[0]["status"] if enrollment else None,
        enrollment[0]["version"] if enrollment else None,
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_outbox"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency"
        ),
    )


def _assert_safe_failure_without_mutation(
    response,
    *,
    status_code: int,
    error_code: str,
    before: tuple[object, ...],
    pg_database,
    invitation_id: UUID,
) -> None:
    assert response.status_code == status_code
    assert response.json() == {"code": error_code, "message": "request rejected"}
    assert _business_snapshot(pg_database, invitation_id) == before


def test_Application基础表SELECT保持42501(
    pg_database,
    application_database,
) -> None:
    _enforce_application_self_link_select_denied(pg_database)
    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,"
        "'identity.user_member_self_link','SELECT')"
    ) is False
    with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
        application_database.fetch_value(
            "SELECT member_id FROM identity.user_member_self_link LIMIT 1"
    )
    assert denied.value.sqlstate == "42501"


def test_Authority仅向必要ApplicationRuntime开放(
    pg_database,
    member_enrollment_writer_database,
) -> None:
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    denied_roles = (
        os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_CASE_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_READER_ROLE"],
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{application_role}',"
        f"'{FUNCTION_SIGNATURE}','EXECUTE')"
    )
    for role in (*denied_roles, "public"):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}',"
            f"'{FUNCTION_SIGNATURE}','EXECUTE')"
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied_execute:
        member_enrollment_writer_database.fetch_value(
            "SELECT public.slice3_member_currentness_authority_v1(1,NULL)"
        )
    assert denied_execute.value.sqlstate == "42501"


def test_家庭会员接受邀请真实ASGI与Currentness负向零副作用(
    pg_database,
    real_db_client,
) -> None:
    _enforce_application_self_link_select_denied(pg_database)
    seeded = _seed_current_member_and_institution(pg_database)
    admin_authorization = _login(
        real_db_client,
        str(seeded["admin_phone"]),
        str(seeded["admin_password"]),
    )
    member_authorization = _login(
        real_db_client,
        str(seeded["member_phone"]),
        str(seeded["member_password"]),
    )
    created = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**admin_authorization, "Idempotency-Key": "authority-create-invite"},
        json={"mode": "SELF", "phone": seeded["member_phone"]},
    )
    assert created.status_code == 201
    invitation_id = UUID(created.json()["invitation_id"])
    short_code = created.json()["short_code"]
    accept_key = "authority-accept-enrollment"
    payload = {
        "invitation_id": str(invitation_id),
        "phone": seeded["member_phone"],
        "short_code": short_code,
    }

    accepted = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**member_authorization, "Idempotency-Key": accept_key},
        json=payload,
    )
    if accepted.status_code == 503:
        assert _accept_side_effects(pg_database, invitation_id, accept_key) == (
            0,
            0,
            0,
            0,
        )
    assert accepted.status_code == 201
    result = accepted.json()
    assert UUID(result["enrollment_id"]).version == 7
    assert UUID(result["subject_member_id"]) == seeded["member_id"]
    expected_side_effects = (1, 1, 1, 1)
    assert _accept_side_effects(pg_database, invitation_id, accept_key) == expected_side_effects

    replay = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**member_authorization, "Idempotency-Key": accept_key},
        json=payload,
    )
    assert replay.status_code == 201
    assert replay.json() == result
    assert _accept_side_effects(pg_database, invitation_id, accept_key) == expected_side_effects

    enrollment_id = result["enrollment_id"]
    family_list = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=member_authorization,
    )
    assert family_list.status_code == 200
    list_items = family_list.json()["items"]
    assert [item["enrollment_id"] for item in list_items] == [enrollment_id]
    assert all(set(item) == ENROLLMENT_LIST_FIELDS for item in list_items)
    assert all(
        ENROLLMENT_DETAIL_FIELDS.isdisjoint(item)
        for item in list_items
    )
    cursor_page = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=member_authorization,
        params={"cursor": enrollment_id},
    )
    assert cursor_page.status_code == 200
    assert cursor_page.json() == {"items": [], "next_cursor": None}
    family_detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}",
        headers=member_authorization,
    )
    assert family_detail.status_code == 200
    detail = family_detail.json()
    assert detail["enrollment_id"] == enrollment_id
    assert set(detail) == ENROLLMENT_LIST_FIELDS | ENROLLMENT_DETAIL_FIELDS
    assert ENROLLMENT_DETAIL_FIELDS <= set(detail)
    for field in ("enrollment_id", "mode", "status", "version"):
        assert list_items[0][field] == detail[field]

    unchanged = _business_snapshot(pg_database, invitation_id)
    invalid_cursor = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=member_authorization,
        params={"cursor": str(uuid4())},
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json() == {
        "code": "INVALID_CURSOR",
        "message": "request rejected",
    }
    assert _business_snapshot(pg_database, invitation_id) == unchanged
    wrong_role_list = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=admin_authorization,
    )
    _assert_safe_failure_without_mutation(
        wrong_role_list,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )
    wrong_role_detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}",
        headers=admin_authorization,
    )
    _assert_safe_failure_without_mutation(
        wrong_role_detail,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )

    other_member = _seed_current_member_only(pg_database)
    other_member_authorization = _login(
        real_db_client,
        str(other_member["phone"]),
        str(other_member["password"]),
    )
    other_member_list = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=other_member_authorization,
    )
    assert other_member_list.status_code == 200
    assert other_member_list.json()["items"] == []
    other_member_detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}",
        headers=other_member_authorization,
    )
    _assert_safe_failure_without_mutation(
        other_member_detail,
        status_code=404,
        error_code="ENROLLMENT_NOT_FOUND",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )

    member_user_id = int(seeded["member_user_id"])
    pg_database.execute(
        f'UPDATE public."user" SET status=\'disabled\' WHERE id={member_user_id}'
    )
    disabled_list = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=member_authorization,
    )
    _assert_safe_failure_without_mutation(
        disabled_list,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )
    disabled_detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}",
        headers=member_authorization,
    )
    _assert_safe_failure_without_mutation(
        disabled_detail,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )
    pg_database.execute(
        f'UPDATE public."user" SET status=\'active\' WHERE id={member_user_id}'
    )

    pg_database.execute(
        "UPDATE identity.member SET status='archived' "
        f"WHERE member_id='{seeded['member_id']}'"
    )
    abnormal_member_list = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=member_authorization,
    )
    _assert_safe_failure_without_mutation(
        abnormal_member_list,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )
    abnormal_member_detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}",
        headers=member_authorization,
    )
    _assert_safe_failure_without_mutation(
        abnormal_member_detail,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )
    pg_database.execute(
        "UPDATE identity.member SET status='created' "
        f"WHERE member_id='{seeded['member_id']}'"
    )

    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    pg_database.execute(
        "REVOKE EXECUTE ON FUNCTION "
        f'{FUNCTION_SIGNATURE} FROM "{application_role}"'
    )
    try:
        unavailable = real_db_client.get(
            "/api/v1/family/member-enrollments",
            headers=member_authorization,
        )
        _assert_safe_failure_without_mutation(
            unavailable,
            status_code=503,
            error_code="DEPENDENCY_UNAVAILABLE",
            before=unchanged,
            pg_database=pg_database,
            invitation_id=invitation_id,
        )
    finally:
        pg_database.execute(
            "GRANT EXECUTE ON FUNCTION "
            f'{FUNCTION_SIGNATURE} TO "{application_role}"'
        )

    wrong_role = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**admin_authorization, "Idempotency-Key": "authority-wrong-role"},
        json=payload,
    )
    assert wrong_role.status_code == 403
    assert wrong_role.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"
    assert _accept_side_effects(pg_database, invitation_id, accept_key) == expected_side_effects
    assert _accept_side_effects(
        pg_database, invitation_id, "authority-wrong-role"
    )[-1] == 0

    wrong_phone = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**member_authorization, "Idempotency-Key": "authority-wrong-phone"},
        json={**payload, "phone": "13" + "6" + ("8" * 8)},
    )
    assert wrong_phone.status_code == 403
    assert wrong_phone.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"
    assert _accept_side_effects(pg_database, invitation_id, accept_key) == expected_side_effects
    assert _accept_side_effects(
        pg_database, invitation_id, "authority-wrong-phone"
    )[-1] == 0

    pg_database.execute(
        "DELETE FROM identity.user_member_self_link "
        f"WHERE user_ref={member_user_id}"
    )
    missing_link_list = real_db_client.get(
        "/api/v1/family/member-enrollments",
        headers=member_authorization,
    )
    _assert_safe_failure_without_mutation(
        missing_link_list,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )
    missing_link_detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}",
        headers=member_authorization,
    )
    _assert_safe_failure_without_mutation(
        missing_link_detail,
        status_code=403,
        error_code="ACTOR_CURRENTNESS_FORBIDDEN",
        before=unchanged,
        pg_database=pg_database,
        invitation_id=invitation_id,
    )


def test_Authority对无Link禁用用户与异常Member状态FailClosed(
    pg_database,
    application_database,
) -> None:
    seeded = _seed_current_member_only(
        pg_database,
        user_id=97513,
        phone_digit="8",
        member_no="M2123456789ABCDEFGHJK",
    )
    user_id = int(seeded["user_id"])
    member_id = seeded["member_id"]

    actual_member_id = application_database.fetch_value(
        "SELECT public.slice3_member_currentness_authority_v1("
        f"{user_id},'{seeded['phone']}')"
    )
    assert UUID(int=actual_member_id.int) == member_id
    assert application_database.fetch_value(
        "SELECT public.slice3_member_currentness_authority_v1(999999,NULL)"
    ) is None

    pg_database.execute(
        f"UPDATE public.\"user\" SET status='disabled' WHERE id={user_id}"
    )
    assert application_database.fetch_value(
        f"SELECT public.slice3_member_currentness_authority_v1({user_id},NULL)"
    ) is None
    pg_database.execute(
        f"UPDATE public.\"user\" SET status='active' WHERE id={user_id};"
        f"UPDATE identity.member SET status='archived' WHERE member_id='{member_id}'"
    )
    assert application_database.fetch_value(
        f"SELECT public.slice3_member_currentness_authority_v1({user_id},NULL)"
    ) is None


def test_0023到0024生命周期只改变Authority对象与精确ACL(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260821_0024"
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{application_role}',"
        f"'{FUNCTION_SIGNATURE}','EXECUTE')"
    )

    command.downgrade(config, "20260821_0023")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260821_0023"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure("
        "'public.slice3_member_currentness_authority_v1(bigint,character varying)')"
    ) is None
    assert not pg_database.fetch_value(
        "SELECT has_table_privilege("
        f"'{application_role}','identity.user_member_self_link','SELECT')"
    )

    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260821_0024"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure("
        "'public.slice3_member_currentness_authority_v1(bigint,character varying)') IS NOT NULL"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{application_role}',"
        f"'{FUNCTION_SIGNATURE}','EXECUTE')"
    )
