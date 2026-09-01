import os
import secrets
from uuid import UUID

import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url
from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)


pytestmark = pytest.mark.integration

NEW_COLUMNS = ("code_digest", "code_key_id", "expires_at", "issued_at")
EXISTING_COLUMNS = (
    "status",
    "failed_attempts",
    "accepted_at",
    "revoked_at",
    "version",
)


def _seed_active_institution(pg_database, real_db_client) -> tuple[str, str, str]:
    from app.core.uuid_generator import Uuid7Generator

    tenant_id = 97301
    org_id = 97303
    phone = "13" + "9" + ("3" * 8)
    password = secrets.token_urlsafe(24)
    tenant_public_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'ACL county','ACL-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'ACL-TENANT','ACL institution','store','test','test','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database,
        real_db_client,
        phone=phone,
        password=password,
        org_id=org_id,
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        institution_name="ACL institution",
        pilot_batch_code="ACL",
        service_tags=("GLUCOSE_METABOLISM",),
    )
    pg_database.execute(
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    return phone, password, str(activated["totp_secret"])


def _resend_counts(pg_database, invitation_id: UUID, idempotency_key: str) -> tuple[int, ...]:
    return (
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            f"WHERE object_id='{invitation_id}' AND action='MEMBER_INVITATION_RESENT'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_outbox "
            f"WHERE aggregate_id='{invitation_id}' AND event_type='MEMBER_INVITATION_RESENT'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='INVITATION_RESEND' "
            f"AND idempotency_key='{idempotency_key}'"
        ),
    )


def _assert_acl_matrix(pg_database) -> None:
    writer = os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"]
    other_roles = (
        os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_CASE_WRITER_ROLE"],
        os.environ["KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_READER_ROLE"],
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{writer}',"
        "'public.member_service_invitation','UPDATE')"
    )
    for column in NEW_COLUMNS + EXISTING_COLUMNS:
        assert pg_database.fetch_value(
            f"SELECT has_column_privilege('{writer}',"
            f"'public.member_service_invitation','{column}','UPDATE')"
        )
    assert not pg_database.fetch_value(
        f"SELECT has_column_privilege('{writer}',"
        "'public.member_service_invitation','phone_ciphertext','UPDATE')"
    )
    for role in other_roles:
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}',"
            "'public.member_service_invitation','UPDATE')"
        )
        for column in NEW_COLUMNS:
            assert not pg_database.fetch_value(
                f"SELECT has_column_privilege('{role}',"
                f"'public.member_service_invitation','{column}','UPDATE')"
            )
    assert not pg_database.fetch_value(
        "SELECT EXISTS(SELECT 1 FROM information_schema.column_privileges "
        "WHERE table_schema='public' AND table_name='member_service_invitation' "
        "AND privilege_type='UPDATE' AND grantee='PUBLIC' "
        "AND column_name=ANY(ARRAY['code_digest','code_key_id','expires_at','issued_at']))"
    )


def test_真实Runtime邀请重发与幂等及陈旧版本闭环(
    pg_database,
    real_db_client,
) -> None:
    phone, password, totp_secret = _seed_active_institution(
        pg_database, real_db_client
    )
    authorization = _login(
        real_db_client,
        phone,
        password,
        totp_secret=totp_secret,
    )
    created = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**authorization, "Idempotency-Key": "acl-create-invitation"},
        json={"mode": "SELF", "phone": "13" + "7" + ("4" * 8)},
    )
    listed = real_db_client.get(
        "/api/v1/institution/member-invitations",
        headers=authorization,
    )
    assert (created.status_code, listed.status_code) == (201, 200)
    invitation_id = UUID(created.json()["invitation_id"])
    assert invitation_id.version == 7
    assert any(item["invitation_id"] == str(invitation_id) for item in listed.json()["items"])
    initial_issued_at = created.json()["issued_at"]
    initial_expires_at = created.json()["expires_at"]
    idempotency_key = "acl-resend-invitation"

    resent = real_db_client.post(
        f"/api/v1/institution/member-invitations/{invitation_id}/resend",
        headers={**authorization, "Idempotency-Key": idempotency_key},
        json={"expected_version": 1},
    )
    assert resent.status_code == 200
    result = resent.json()
    assert result["version"] == 2
    assert result["issued_at"] != initial_issued_at
    assert result["expires_at"] != initial_expires_at
    assert result["short_code"]
    expected_counts = (1, 1, 1)
    assert _resend_counts(pg_database, invitation_id, idempotency_key) == expected_counts

    replay = real_db_client.post(
        f"/api/v1/institution/member-invitations/{invitation_id}/resend",
        headers={**authorization, "Idempotency-Key": idempotency_key},
        json={"expected_version": 1},
    )
    assert replay.status_code == 200
    assert replay.json() == result
    assert _resend_counts(pg_database, invitation_id, idempotency_key) == expected_counts

    conflict = real_db_client.post(
        f"/api/v1/institution/member-invitations/{invitation_id}/resend",
        headers={**authorization, "Idempotency-Key": idempotency_key},
        json={"expected_version": 2},
    )
    assert conflict.status_code == 503
    assert conflict.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "request rejected",
    }
    assert _resend_counts(pg_database, invitation_id, idempotency_key) == expected_counts

    stale = real_db_client.post(
        f"/api/v1/institution/member-invitations/{invitation_id}/resend",
        headers={**authorization, "Idempotency-Key": "acl-resend-stale"},
        json={"expected_version": 1},
    )
    assert stale.status_code == 409
    assert stale.json() == {"code": "VERSION_CONFLICT", "message": "request rejected"}
    assert _resend_counts(pg_database, invitation_id, "acl-resend-stale") == (1, 1, 0)
    assert pg_database.fetch_value(
        "SELECT version FROM public.member_service_invitation "
        f"WHERE invitation_id='{invitation_id}'"
    ) == 2

    revoked = real_db_client.post(
        f"/api/v1/institution/member-invitations/{invitation_id}/revoke",
        headers={**authorization, "Idempotency-Key": "acl-revoke-invitation"},
        json={"expected_version": 2, "reason_code": "INSTITUTION_CANCELLED"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["version"] == 3
    _assert_acl_matrix(pg_database)
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260901_0034"


def test_0023升级降级再升级只对称改变四列权限(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    before_rows = pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_service_invitation"
    )
    _assert_acl_matrix(pg_database)

    command.downgrade(config, "20260818_0022")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260818_0022"
    writer = os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"]
    for column in NEW_COLUMNS:
        assert not pg_database.fetch_value(
            f"SELECT has_column_privilege('{writer}',"
            f"'public.member_service_invitation','{column}','UPDATE')"
        )
    for column in EXISTING_COLUMNS:
        assert pg_database.fetch_value(
            f"SELECT has_column_privilege('{writer}',"
            f"'public.member_service_invitation','{column}','UPDATE')"
        )
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_service_invitation"
    ) == before_rows

    command.upgrade(config, "20260821_0023")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260821_0023"
    _assert_acl_matrix(pg_database)
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_service_invitation"
    ) == before_rows
    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num FROM alembic_version"
    ) == "20260901_0034"
    _assert_acl_matrix(pg_database)
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_service_invitation"
    ) == before_rows
