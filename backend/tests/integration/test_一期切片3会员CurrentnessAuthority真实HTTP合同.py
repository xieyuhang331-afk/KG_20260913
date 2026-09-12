from __future__ import annotations

import base64
import json
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
IDENTITY_SUMMARY_SIGNATURE = (
    "public.slice3_identity_revision_summary_v1(uuid,uuid)"
)
IDENTITY_CORRECTION_SIGNATURE = (
    "public.slice3_identity_revision_correction_v1(uuid,uuid)"
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


def _seed_current_member_and_institution(
    pg_database, real_db_client, *, variant: int = 0
) -> dict[str, object]:
    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    offset = variant * 1000
    tenant_id = 97401 + offset
    member_user_id = 97403 + offset
    org_id = 97404 + offset
    member_id = Uuid7Generator().generate()
    tenant_public_id = Uuid7Generator().generate()
    self_link_id = uuid4()
    evidence_id = uuid4()
    phone_prefixes = (("139", "137"), ("158", "157"), ("186", "187"))
    admin_prefix, member_prefix = phone_prefixes[variant]
    admin_phone = admin_prefix + ("4" * 8)
    member_phone = member_prefix + ("5" * 8)
    member_no = ("M0123456789ABCDEFGHJK", "M8123456789ABCDEFGHJK", "M7123456789ABCDEFGHJK")[variant]
    admin_password = secrets.token_urlsafe(24)
    member_password = secrets.token_urlsafe(24)
    member_hash = hash_password(member_password).replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'Authority county','AUTHORITY-COUNTY-{variant}','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'AUTHORITY-TENANT-{variant}','Authority institution','store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database,
        real_db_client,
        phone=admin_phone,
        password=admin_password,
        org_id=org_id,
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        institution_name="Authority institution",
        pilot_batch_code=f"AUTHORITY-{variant}",
        service_tags=("GLUCOSE_METABOLISM",),
    )
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({member_user_id},'{member_phone}','{member_hash}','member','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{member_id}','{member_no}','registration','created',1,now(),now());"
        "INSERT INTO identity.user_member_self_link("
        "link_id,user_ref,member_id,source,eligibility_decision_ref,establishment_basis,"
        "establishment_record_ref,created_at) VALUES ("
        f"'{self_link_id}',{member_user_id},'{member_id}','REGISTRATION_VERIFIED','{evidence_id}',"
        f"'REGISTRATION_VERIFIED_BOOTSTRAP','{evidence_id}',now());"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    return {
        "admin_phone": admin_phone,
        "admin_password": admin_password,
        "admin_totp_secret": activated["totp_secret"],
        "member_phone": member_phone,
        "member_password": member_password,
        "member_user_id": member_user_id,
        "member_id": member_id,
}


def _activate_org_admin_for_test(
    pg_database,
    real_db_client,
    *,
    phone: str,
    password: str,
    org_id: int,
    tenant_id: int,
    tenant_public_id,
    institution_name: str,
    pilot_batch_code: str,
    service_tags: tuple[str, ...],
) -> dict[str, object]:
    from app.modules.institution_onboarding.domain import generate_totp
    from app.modules.institution_onboarding.service import OnboardingSecrets, utcnow

    invitation_id = uuid4()
    short_code = f"{secrets.randbelow(1_000_000):06d}"
    totp_secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii")
    secrets_box = OnboardingSecrets()
    now = utcnow()
    encrypted_phone = secrets_box.encrypt(phone).hex()
    phone_digest = secrets_box.digest(phone)
    code_digest = secrets_box.digest(short_code)
    safe_name = institution_name.replace("'", "''")
    safe_batch = pilot_batch_code.replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{invitation_id}','{safe_name}','HEALTH_STORE',decode('{encrypted_phone}','hex'),'{phone_digest}',"
        f"'{safe_batch}',{org_id},'{code_digest}','ISSUED',0,now()+interval '1 day',0,now(),NULL,1)"
    )
    activated = real_db_client.post(
        "/api/v1/institution-onboarding/activate",
        headers={"Idempotency-Key": f"a1-activate-{invitation_id}"},
        json={
            "invitation_id": str(invitation_id),
            "phone": phone,
            "short_code": short_code,
            "password": password,
            "totp_secret": totp_secret,
            "totp_code": generate_totp(totp_secret, at=now),
        },
    )
    assert activated.status_code == 200, activated.json().get("detail")
    activation = activated.json()["data"]
    draft_payload = json.dumps(
        {"service_tags": list(service_tags)}, separators=(",", ":")
    ).replace("'", "''")
    pg_database.execute(
        "UPDATE public.\"user\" "
        f"SET tenant_id={tenant_id} WHERE id={int(activation['user_id'])};"
        "UPDATE public.institution_application SET "
        f"status='APPROVED',draft_payload='{draft_payload}'::jsonb,current_revision_no=1,"
        f"tenant_internal_id={tenant_id},tenant_public_id='{tenant_public_id}',"
        "updated_at=now(),submitted_at=now(),reviewed_at=now(),version=3 "
        f"WHERE application_id='{activation['application_id']}';"
        "INSERT INTO public.institution_tenant_origin(tenant_id,tenant_public_id,origin_type,"
        "controlled_application_id) VALUES ("
        f"{tenant_id},'{tenant_public_id}','CONTROLLED_APPLICATION','{activation['application_id']}')"
    )
    return {
        "user_id": int(activation["user_id"]),
        "application_id": activation["application_id"],
        "invitation_id": invitation_id,
        "totp_secret": totp_secret,
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


def _login(
    real_db_client,
    phone: str,
    password: str,
    *,
    totp_secret: str | None = None,
) -> dict[str, str]:
    payload = {"phone": phone, "password": password}
    if totp_secret is not None:
        from app.modules.institution_onboarding.domain import generate_totp
        from app.modules.institution_onboarding.service import utcnow

        payload["totp_code"] = generate_totp(totp_secret, at=utcnow())
    response = real_db_client.post(
        "/api/v1/auth/login", json=payload
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _synthetic_prc_identity(birth_date: str = "19800101") -> str:
    first_seventeen = "110101" + birth_date + "001"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    check_codes = "10X98765432"
    return first_seventeen + check_codes[
        sum(int(value) * weight for value, weight in zip(first_seventeen, weights)) % 11
    ]


def _identity_submission_snapshot(pg_database, enrollment_id: UUID, key: str) -> tuple[object, ...]:
    enrollment = pg_database.fetch_rows(
        "SELECT status,version,current_identity_verification_id "
        "FROM public.service_enrollment WHERE enrollment_id=$1",
        enrollment_id,
    )
    return (
        pg_database.fetch_rows(
            "SELECT COUNT(*) AS value FROM public.member_identity_verification "
            "WHERE enrollment_id=$1",
            enrollment_id,
        )[0]["value"],
        pg_database.fetch_rows(
            "SELECT COUNT(*) AS value FROM public.member_identity_revision r "
            "JOIN public.member_identity_verification v "
            "ON v.verification_id=r.verification_id WHERE v.enrollment_id=$1",
            enrollment_id,
        )[0]["value"],
        enrollment[0]["status"],
        enrollment[0]["version"],
        enrollment[0]["current_identity_verification_id"],
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            "WHERE action='MEMBER_IDENTITY_SUBMITTED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_outbox "
            "WHERE event_type='MEMBER_IDENTITY_SUBMITTED'"
        ),
        pg_database.fetch_rows(
            "SELECT COUNT(*) AS value FROM public.member_enrollment_idempotency "
            "WHERE operation='IDENTITY_SUBMIT' AND idempotency_key=$1",
            key,
        )[0]["value"],
    )


def _create_accepted_self_enrollment(
    real_db_client,
    *,
    admin_authorization: dict[str, str],
    member_authorization: dict[str, str],
    member_phone: object,
    key_prefix: str,
) -> UUID:
    created = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={
            **admin_authorization,
            "Idempotency-Key": f"{key_prefix}-create",
        },
        json={"mode": "SELF", "phone": member_phone},
    )
    assert created.status_code == 201
    accepted = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={
            **member_authorization,
            "Idempotency-Key": f"{key_prefix}-accept",
        },
        json={
            "invitation_id": created.json()["invitation_id"],
            "phone": member_phone,
            "short_code": created.json()["short_code"],
        },
    )
    assert accepted.status_code == 201
    return UUID(accepted.json()["enrollment_id"])


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
    _assert_error_response_dto(
        response,
        status_code=status_code,
        error_code=error_code,
        retryable=status_code == 503,
    )
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert _business_snapshot(pg_database, invitation_id) == before


def _assert_error_response_dto(
    response,
    *,
    status_code: int,
    error_code: str,
    retryable: bool,
) -> dict[str, object]:
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "retryable", "field_errors"}
    assert body["code"] == error_code
    assert body["message"] == "request rejected"
    request_id = UUID(body["request_id"])
    assert request_id.version == 7
    assert body["request_id"] == response.headers["x-request-id"]
    assert body["retryable"] is retryable
    assert body["field_errors"] == []
    return body


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
    seeded = _seed_current_member_and_institution(pg_database, real_db_client)
    admin_authorization = _login(
        real_db_client,
        str(seeded["admin_phone"]),
        str(seeded["admin_password"]),
        totp_secret=str(seeded["admin_totp_secret"]),
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
    _assert_error_response_dto(
        invalid_cursor,
        status_code=400,
        error_code="INVALID_CURSOR",
        retryable=False,
    )
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
    original_status = pg_database.fetch_value(
        f'SELECT status FROM public."user" WHERE id={member_user_id}'
    )
    try:
        pg_database.execute(
            f'UPDATE public."user" SET status=\'disabled\' WHERE id={member_user_id}'
        )
        for path in (
            "/api/v1/family/member-enrollments",
            f"/api/v1/family/member-enrollments/{enrollment_id}",
        ):
            disabled = real_db_client.get(path, headers=member_authorization)
            _assert_safe_failure_without_mutation(
                disabled,
                status_code=401,
                error_code="ACCESS_TOKEN_STALE",
                before=unchanged,
                pg_database=pg_database,
                invitation_id=invitation_id,
            )
            assert disabled.headers["WWW-Authenticate"] == "Bearer"
            assert disabled.headers["Cache-Control"] == "no-store, private"
    finally:
        pg_database.execute(
            'UPDATE public."user" SET status=\'' + original_status.replace("'", "''")
            + f'\' WHERE id={member_user_id}'
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


def test_会员实名首次提交使用真实Writer并返回安全IdentityStatus(
    pg_database,
    real_db_client,
    member_enrollment_writer_database,
) -> None:
    seeded = _seed_current_member_and_institution(
        pg_database, real_db_client, variant=1
    )
    admin_authorization = _login(
        real_db_client,
        str(seeded["admin_phone"]),
        str(seeded["admin_password"]),
        totp_secret=str(seeded["admin_totp_secret"]),
    )
    member_authorization = _login(
        real_db_client,
        str(seeded["member_phone"]),
        str(seeded["member_password"]),
    )
    created = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**admin_authorization, "Idempotency-Key": "identity-read-create"},
        json={"mode": "SELF", "phone": seeded["member_phone"]},
    )
    assert created.status_code == 201
    invitation_id = UUID(created.json()["invitation_id"])
    accepted = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**member_authorization, "Idempotency-Key": "identity-read-accept"},
        json={
            "invitation_id": str(invitation_id),
            "phone": seeded["member_phone"],
            "short_code": created.json()["short_code"],
        },
    )
    assert accepted.status_code == 201
    enrollment_id = UUID(accepted.json()["enrollment_id"])
    key = "identity-read-submit"
    before = _identity_submission_snapshot(pg_database, enrollment_id, key)

    with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied:
        member_enrollment_writer_database.fetch_value(
            "SELECT revision_id FROM public.member_identity_revision LIMIT 1"
        )
    assert denied.value.sqlstate == "42501"

    submitted = real_db_client.put(
        f"/api/v1/family/member-enrollments/{enrollment_id}/identity-submission",
        headers={**member_authorization, "Idempotency-Key": key},
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Member",
            "id_number": _synthetic_prc_identity(),
            "expected_version": 1,
        },
    )
    if submitted.status_code == 503:
        _assert_error_response_dto(
            submitted,
            status_code=503,
            error_code="DEPENDENCY_UNAVAILABLE",
            retryable=True,
        )
        assert _identity_submission_snapshot(pg_database, enrollment_id, key) == before

    assert submitted.status_code == 200
    result = submitted.json()
    assert set(result) == {
        "verification_id",
        "enrollment_id",
        "member_id",
        "current_revision_id",
        "status",
        "id_masked",
        "submitted_at",
        "institution_checked_at",
        "platform_decided_at",
        "reason_codes",
        "version",
    }
    assert result["enrollment_id"] == str(enrollment_id)
    assert result["status"] == "SUBMITTED"
    after = _identity_submission_snapshot(pg_database, enrollment_id, key)
    assert after[0] == before[0] + 1
    assert after[1] == before[1] + 1
    assert after[2:5] == ("IDENTITY_SUBMITTED", before[3] + 1, UUID(result["verification_id"]))
    assert after[5:] == tuple(value + 1 for value in before[5:])


def test_实名Revision受限读只允许当前范围并支持补正重提(
    pg_database,
    real_db_client,
    application_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
    member_case_writer_database,
    member_workflow_worker_database,
    member_enrollment_reader_database,
) -> None:
    seeded = _seed_current_member_and_institution(
        pg_database, real_db_client, variant=2
    )
    admin_authorization = _login(
        real_db_client,
        str(seeded["admin_phone"]),
        str(seeded["admin_password"]),
        totp_secret=str(seeded["admin_totp_secret"]),
    )
    member_authorization = _login(
        real_db_client,
        str(seeded["member_phone"]),
        str(seeded["member_password"]),
    )
    enrollment_id = _create_accepted_self_enrollment(
        real_db_client,
        admin_authorization=admin_authorization,
        member_authorization=member_authorization,
        member_phone=seeded["member_phone"],
        key_prefix="identity-bounded-primary",
    )
    first = real_db_client.put(
        f"/api/v1/family/member-enrollments/{enrollment_id}/identity-submission",
        headers={
            **member_authorization,
            "Idempotency-Key": "identity-bounded-submit",
        },
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Member",
            "id_number": _synthetic_prc_identity(),
            "expected_version": 1,
        },
    )
    assert first.status_code == 200
    verification_id = UUID(first.json()["verification_id"])
    first_revision_id = UUID(first.json()["current_revision_id"])

    for column in ("revision_id", "real_name_ciphertext"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied_select:
            member_enrollment_writer_database.fetch_value(
                f"SELECT {column} FROM public.member_identity_revision LIMIT 1"
            )
        assert denied_select.value.sqlstate == "42501"

    safe_summary = member_enrollment_writer_database.fetch_rows(
        "SELECT * FROM public.slice3_identity_revision_summary_v1($1,$2)",
        verification_id,
        first_revision_id,
    )
    assert len(safe_summary) == 1
    assert set(safe_summary[0]) == {
        "revision_id",
        "verification_id",
        "revision_no",
        "document_type",
        "id_masked",
        "identity_fingerprint",
        "fingerprint_key_id",
        "input_digest",
        "created_at",
    }

    correction_requested = real_db_client.post(
        f"/api/v1/institution/member-enrollments/{enrollment_id}/identity-check",
        headers={
            **admin_authorization,
            "Idempotency-Key": "identity-bounded-correction",
        },
        json={
            "revision_id": str(first_revision_id),
            "decision": "NEEDS_CORRECTION",
            "reason_code": "IDENTITY_INFORMATION_INCONSISTENT",
            "correction_fields": ["real_name"],
            "expected_version": 1,
        },
    )
    assert correction_requested.status_code == 200
    correction_material = member_enrollment_writer_database.fetch_rows(
        "SELECT * FROM public.slice3_identity_revision_correction_v1($1,$2)",
        verification_id,
        first_revision_id,
    )
    assert len(correction_material) == 1
    assert set(correction_material[0]) == {
        "revision_id",
        "verification_id",
        "revision_no",
        "document_type",
        "real_name_ciphertext",
        "real_name_key_id",
        "id_ciphertext",
        "id_key_id",
        "birth_date_ciphertext",
        "birth_date_key_id",
    }

    old_revision_count = pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_identity_revision"
    )
    resubmitted = real_db_client.post(
        f"/api/v1/family/member-enrollments/{enrollment_id}/identity-resubmit",
        headers={
            **member_authorization,
            "Idempotency-Key": "identity-bounded-resubmit",
        },
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Member Corrected",
            "expected_version": 3,
        },
    )
    assert resubmitted.status_code == 200
    second_revision_id = UUID(resubmitted.json()["current_revision_id"])
    assert second_revision_id != first_revision_id
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_identity_revision"
    ) == old_revision_count + 1
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_identity_revision "
        f"WHERE revision_id='{first_revision_id}'"
    ) == 1

    pg_database.execute(
        "UPDATE public.member_identity_verification SET status='NEEDS_CORRECTION' "
        f"WHERE verification_id='{verification_id}'"
    )
    assert member_enrollment_writer_database.fetch_rows(
        "SELECT * FROM public.slice3_identity_revision_correction_v1($1,$2)",
        verification_id,
        first_revision_id,
    ) == []
    pg_database.execute(
        "UPDATE public.member_identity_verification SET status='RESUBMITTED' "
        f"WHERE verification_id='{verification_id}'"
    )
    assert member_enrollment_writer_database.fetch_rows(
        "SELECT * FROM public.slice3_identity_revision_correction_v1($1,$2)",
        verification_id,
        second_revision_id,
    ) == []

    other_member = _seed_current_member_only(
        pg_database,
        user_id=97523,
        phone_digit="9",
        member_no="M3123456789ABCDEFGHJK",
    )
    other_authorization = _login(
        real_db_client,
        str(other_member["phone"]),
        str(other_member["password"]),
    )
    other_enrollment_id = _create_accepted_self_enrollment(
        real_db_client,
        admin_authorization=admin_authorization,
        member_authorization=other_authorization,
        member_phone=other_member["phone"],
        key_prefix="identity-bounded-other",
    )
    other_submission = real_db_client.put(
        f"/api/v1/family/member-enrollments/{other_enrollment_id}/identity-submission",
        headers={
            **other_authorization,
            "Idempotency-Key": "identity-bounded-other-submit",
        },
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Other",
            "id_number": _synthetic_prc_identity("19751231"),
            "expected_version": 1,
        },
    )
    assert other_submission.status_code == 200
    other_verification_id = UUID(other_submission.json()["verification_id"])
    assert member_enrollment_writer_database.fetch_rows(
        "SELECT * FROM public.slice3_identity_revision_summary_v1($1,$2)",
        other_verification_id,
        second_revision_id,
    ) == []

    assert member_identity_review_writer_database.fetch_rows(
        "SELECT * FROM public.slice3_identity_revision_summary_v1($1,$2)",
        verification_id,
        second_revision_id,
    )
    denied_databases = (
        application_database,
        member_case_writer_database,
        member_workflow_worker_database,
        member_enrollment_reader_database,
    )
    for database in denied_databases:
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied_summary:
            database.fetch_value(
                "SELECT * FROM public.slice3_identity_revision_summary_v1(NULL,NULL)"
            )
        assert denied_summary.value.sqlstate == "42501"
    for database in (member_identity_review_writer_database, *denied_databases):
        with pytest.raises(asyncpg.InsufficientPrivilegeError) as denied_correction:
            database.fetch_value(
                "SELECT * FROM public.slice3_identity_revision_correction_v1(NULL,NULL)"
            )
        assert denied_correction.value.sqlstate == "42501"
    assert pg_database.fetch_value(
        "SELECT COUNT(*)=0 FROM information_schema.routine_privileges "
        "WHERE grantee='PUBLIC' AND routine_name IN "
        "('slice3_identity_revision_summary_v1',"
        "'slice3_identity_revision_correction_v1')"
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


def test_0023到0025生命周期只改受限Authority与精确ACL(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260913_0044"
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
    for column in ("user_ref", "member_id"):
        assert not pg_database.fetch_value(
            f"SELECT has_column_privilege('{application_role}',"
            f"'identity.user_member_self_link','{column}','SELECT')"
        )

    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260913_0044"
    assert pg_database.fetch_value(
        "SELECT to_regprocedure("
        "'public.slice3_member_currentness_authority_v1(bigint,character varying)') IS NOT NULL"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{application_role}',"
        f"'{FUNCTION_SIGNATURE}','EXECUTE')"
    )


def test_0024到0025往返仅增加Revision受限读与两列补正权限(
    pg_database,
) -> None:
    config = _build_alembic_config(_get_test_database_url())
    enrollment_role = os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"]
    review_role = os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"]

    def assert_0025_present() -> None:
        assert pg_database.fetch_value(
            "SELECT version_num FROM alembic_version"
        ) == "20260913_0044"
        assert pg_database.fetch_value(
            "SELECT to_regprocedure($$"
            + IDENTITY_SUMMARY_SIGNATURE
            + "$$) IS NOT NULL"
        )
        assert pg_database.fetch_value(
            "SELECT to_regprocedure($$"
            + IDENTITY_CORRECTION_SIGNATURE
            + "$$) IS NOT NULL"
        )
        for column in ("platform_decision_id", "platform_decided_at"):
            assert pg_database.fetch_value(
                f"SELECT has_column_privilege('{enrollment_role}',"
                f"'public.member_identity_verification','{column}','UPDATE')"
            )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{enrollment_role}',"
            "'public.member_identity_verification','UPDATE')"
        )
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('{review_role}',"
            f"'{IDENTITY_SUMMARY_SIGNATURE}','EXECUTE')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{review_role}',"
            f"'{IDENTITY_CORRECTION_SIGNATURE}','EXECUTE')"
        )

    assert_0025_present()
    command.downgrade(config, "20260821_0024")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260821_0024"
    for signature in (IDENTITY_SUMMARY_SIGNATURE, IDENTITY_CORRECTION_SIGNATURE):
        assert pg_database.fetch_value(
            f"SELECT to_regprocedure('{signature}')"
        ) is None
    for column in ("platform_decision_id", "platform_decided_at"):
        assert not pg_database.fetch_value(
            f"SELECT has_column_privilege('{enrollment_role}',"
            f"'public.member_identity_verification','{column}','UPDATE')"
        )
    assert pg_database.fetch_value(
        f"SELECT has_column_privilege('{enrollment_role}',"
        "'public.member_identity_verification','status','UPDATE')"
    )

    command.upgrade(config, "head")
    assert_0025_present()
