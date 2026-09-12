from __future__ import annotations

import os
import secrets

import asyncpg
import pytest

from app.modules.auth.service import hash_password

pytestmark = pytest.mark.integration

_CURRENTNESS_SIGNATURE = "public.auth_user_currentness_v1(bigint)"
_REVIEWER_ID = 9955101
_REGION_ID = 9955102
_REVIEWER_PHONE = "13655555555"
_MEMBER_ID = 9955103
_MEMBER_PHONE = "13755555555"
_TENANT_ID = 9955104


def _login(client, phone: str, password: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"phone": phone, "password": password})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["data"]["access_token"]}


@pytest.fixture(scope="module")
def reviewer_and_region(pg_database) -> tuple[str, str]:
    reviewer_password = secrets.token_urlsafe(24)
    member_password = secrets.token_urlsafe(24)
    reviewer_hash = hash_password(reviewer_password).replace("'", "''")
    member_hash = hash_password(member_password).replace("'", "''")
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({_REVIEWER_ID},'{_REVIEWER_PHONE}','{reviewer_hash}','super_admin','active',NULL),"
        f"({_MEMBER_ID},'{_MEMBER_PHONE}','{member_hash}','member','active',NULL);"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES "
        "(9955199,NULL,'Synthetic headquarter','CURRENT-REVIEWER-HQ','headquarter','active',1),"
        "(9955200,9955199,'Synthetic province','CURRENT-REVIEWER-PROVINCE','province','active',1),"
        "(9955201,9955200,'Synthetic city','CURRENT-REVIEWER-CITY','city','active',1),"
        f"({_REGION_ID},9955201,'Synthetic county','CURRENT-REVIEWER-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status) "
        f"VALUES ({_TENANT_ID},{_REGION_ID},'CURRENT-REVIEWER-TENANT','Synthetic tenant','store','test','test','active')"
    )
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    pg_database.execute(
        'REVOKE ALL ON TABLE public."user" FROM "' + application_role + '"'
    )
    return reviewer_password, member_password


def _invitation_count(pg_database) -> int:
    return int(pg_database.fetch_value("SELECT count(*) FROM public.institution_invitation"))


def _post_invitation(client, authorization: dict[str, str], suffix: str):
    return client.post(
        "/api/v1/platform/institution-invitations",
        headers={**authorization, "Idempotency-Key": f"current-reviewer-{suffix}"},
        json={
            "institution_name": "Synthetic current reviewer institution",
            "institution_type": "HEALTH_STORE",
            "applicant_phone": "13855555555",
            "pilot_batch_code": "CURRENT-REVIEWER",
            "administrative_region_id": _REGION_ID,
            "expires_in_minutes": 60,
        },
    )


def test_真实登录与机构邀请通过闭合CurrentReviewer持久化(
    pg_database, application_database, real_db_client, reviewer_and_region
):
    reviewer_password, _ = reviewer_and_region
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]

    assert application_database.fetch_value(
        "SELECT has_table_privilege(current_user,'public.user','SELECT')"
    ) is False
    assert pg_database.fetch_value(
        "SELECT has_function_privilege('"
        + application_role
        + "','"
        + _CURRENTNESS_SIGNATURE
        + "','EXECUTE')"
    ) is True
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        application_database.fetch_value('SELECT id FROM public."user" LIMIT 1')

    authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    before = _invitation_count(pg_database)
    response = _post_invitation(real_db_client, authorization, "success")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ISSUED"
    assert _invitation_count(pg_database) == before + 1
    assert pg_database.fetch_value(
        "SELECT count(*) FROM public.institution_onboarding_audit "
        "WHERE actor_user_id=" + str(_REVIEWER_ID) + " AND action='INVITATION_CREATE'"
    ) == 1
    rendered = response.text
    assert _REVIEWER_PHONE not in rendered
    assert reviewer_password not in rendered


def test_认证与Currentness拒绝边界均不产生邀请侧效(
    pg_database, real_db_client, reviewer_and_region
):
    reviewer_password, member_password = reviewer_and_region
    before = _invitation_count(pg_database)

    missing = _post_invitation(real_db_client, {}, "missing")
    invalid = _post_invitation(
        real_db_client, {"Authorization": "Bearer synthetic-invalid"}, "invalid"
    )
    member = _post_invitation(
        real_db_client,
        _login(real_db_client, _MEMBER_PHONE, member_password),
        "member",
    )
    reviewer_authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    pg_database.execute(
        f"UPDATE public.\"user\" SET status='disabled' WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_status = _post_invitation(
            real_db_client, reviewer_authorization, "stale-status"
        )
    finally:
        pg_database.execute(
            f"UPDATE public.\"user\" SET status='active' WHERE id={_REVIEWER_ID}"
        )
    pg_database.execute(
        f"UPDATE public.\"user\" SET role='member' WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_role = _post_invitation(
            real_db_client, reviewer_authorization, "stale-role"
        )
    finally:
        pg_database.execute(
            f"UPDATE public.\"user\" SET role='super_admin' WHERE id={_REVIEWER_ID}"
        )
    pg_database.execute(
        f"UPDATE public.\"user\" SET tenant_id={_TENANT_ID} WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_tenant = _post_invitation(
            real_db_client, reviewer_authorization, "stale-tenant"
        )
    finally:
        pg_database.execute(
            f"UPDATE public.\"user\" SET tenant_id=NULL WHERE id={_REVIEWER_ID}"
        )
    changed_hash = hash_password(secrets.token_urlsafe(24)).replace("'", "''")
    pg_database.execute(
        f"UPDATE public.\"user\" SET password_hash='{changed_hash}' WHERE id={_REVIEWER_ID}"
    )
    try:
        stale_credential = real_db_client.post(
            "/api/v1/auth/login",
            json={"phone": _REVIEWER_PHONE, "password": reviewer_password},
        )
    finally:
        original_hash = hash_password(reviewer_password).replace("'", "''")
        pg_database.execute(
            f"UPDATE public.\"user\" SET password_hash='{original_hash}' WHERE id={_REVIEWER_ID}"
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert member.status_code == 403
    for stale in (stale_status, stale_role, stale_tenant):
        assert stale.status_code == 401
        assert stale.json()["code"] == "AUTHENTICATION_REQUIRED"
    assert stale_credential.status_code == 401
    assert stale_credential.json()["code"] == "INVALID_CREDENTIALS"
    assert _invitation_count(pg_database) == before


def test_CurrentReviewer闭合函数权限不可用时稳定503且零侧效(
    pg_database, real_db_client, reviewer_and_region
):
    reviewer_password, _ = reviewer_and_region
    authorization = _login(real_db_client, _REVIEWER_PHONE, reviewer_password)
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    before = _invitation_count(pg_database)
    pg_database.execute(
        'REVOKE EXECUTE ON FUNCTION public.auth_user_currentness_v1(BIGINT) FROM "'
        + application_role
        + '"'
    )
    try:
        response = _post_invitation(real_db_client, authorization, "unavailable")
    finally:
        pg_database.execute(
            'GRANT EXECUTE ON FUNCTION public.auth_user_currentness_v1(BIGINT) TO "'
            + application_role
            + '"'
        )

    assert response.status_code == 503
    assert response.json()["code"] == "DEPENDENCY_UNAVAILABLE"
    assert _invitation_count(pg_database) == before
    for forbidden in (
        _REVIEWER_PHONE,
        reviewer_password,
        "auth_user_currentness_v1",
        "SELECT",
        "postgresql",
    ):
        assert forbidden not in response.text
