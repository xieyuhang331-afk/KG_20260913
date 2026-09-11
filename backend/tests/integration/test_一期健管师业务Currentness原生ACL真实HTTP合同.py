from __future__ import annotations

import asyncio
import os
import secrets
from datetime import date, timedelta

import pytest
from alembic import command

from app.core.security import create_access_token
from app.core.uuid_generator import Uuid7Generator
from app.modules.auth.service import hash_password
from tests.integration.conftest import _build_alembic_config
from tests.integration.test_一期切片2健管师资质与ServiceReady数据库闭环 import (
    _cleanup_slice2_seed,
    _seed_approved_therapist,
    _seed_ready_institution,
)
from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _activate_org_admin_for_test,
    _login,
)

pytestmark = pytest.mark.integration


_SIGNATURES = {
    "public.slice2_institution_business_currentness_v1(bigint,bigint,text)": {
        "KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE",
        "KG_TEST_THERAPIST_READER_ROLE",
    },
    "public.slice2_therapist_activation_currentness_v1(uuid)": {
        "KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE",
    },
    "public.slice2_therapist_onboarding_currentness_v1(bigint,bigint)": {
        "KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE",
        "KG_TEST_THERAPIST_REVIEW_WRITER_ROLE",
        "KG_TEST_THERAPIST_READER_ROLE",
    },
    "public.slice3_therapist_service_currentness_v1(bigint,bigint)": {
        "KG_TEST_MEMBER_ENROLLMENT_READER_ROLE",
        "KG_TEST_MEMBER_CASE_WRITER_ROLE",
    },
    "public.slice2_therapist_reviewer_currentness_v1(bigint)": {
        "KG_TEST_THERAPIST_READER_ROLE",
    },
    "public.slice2_therapist_review_target_currentness_v1(bigint,uuid)": {
        "KG_TEST_THERAPIST_REVIEW_WRITER_ROLE",
    },
    "public.slice2_therapist_review_item_currentness_v1(bigint,uuid)": {
        "KG_TEST_THERAPIST_REVIEW_WRITER_ROLE",
    },
    "public.slice2_therapist_self_exit_currentness_v1(bigint,bigint,text,text)": {
        "KG_TEST_THERAPIST_REVIEW_WRITER_ROLE",
    },
}


def test_0043七个权威的EXECUTE与基础表权限精确隔离(pg_database) -> None:
    runtime_roles = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("KG_TEST_")
        and name.endswith("_ROLE")
        and name
        not in {
            "KG_TEST_MIGRATION_ROLE",
            "KG_TEST_DDL_OWNER_ROLE",
            "KG_TEST_ROLE_ADMIN_ROLE",
        }
    }
    assert "KG_TEST_APPLICATION_ROLE" in runtime_roles

    for signature, allowed_names in _SIGNATURES.items():
        assert not pg_database.fetch_value(
            "SELECT has_function_privilege('public','"
            + signature
            + "','EXECUTE')"
        )
        for environment_name, role in runtime_roles.items():
            expected = environment_name in allowed_names
            actual = pg_database.fetch_value(
                "SELECT has_function_privilege('"
                + role
                + "','"
                + signature
                + "','EXECUTE')"
            )
            assert actual is expected, f"R3_EXECUTE_SCOPE_{environment_name}"

    for environment_name in (
        "KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE",
        "KG_TEST_THERAPIST_REVIEW_WRITER_ROLE",
        "KG_TEST_THERAPIST_READER_ROLE",
        "KG_TEST_MEMBER_ENROLLMENT_READER_ROLE",
        "KG_TEST_MEMBER_CASE_WRITER_ROLE",
    ):
        role = runtime_roles[environment_name]
        for table in ('public."user"', "public.tenant", "public.therapist_profile"):
            assert not pg_database.fetch_value(
                "SELECT has_table_privilege('"
                + role
                + "','"
                + table.replace("'", "''")
                + "','SELECT')"
            ), f"R3_BASE_SELECT_{environment_name}"


def test_机构当前性的真实HTTP成功与批准失效零副作用(
    pg_database, real_db_client
) -> None:
    generator = Uuid7Generator()
    suffix = secrets.randbelow(900_000) + 100_000
    org_id = 9_460_000_000 + suffix
    tenant_id = org_id
    phone = "196" + f"{suffix:08d}"
    password = "Synthetic-" + secrets.token_urlsafe(18)
    tenant_public_id = generator.generate()
    pg_database.execute(
        "INSERT INTO public.platform_org("
        "id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES({org_id},NULL,'Synthetic R3 Org','R3-{org_id}',"
        "'county','active',1);"
        "INSERT INTO public.tenant("
        "id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES({tenant_id},{org_id},'R3-{tenant_id}','Synthetic R3 Tenant',"
        "'store','Synthetic Province','Synthetic City','active',now(),now())"
    )
    activated = _activate_org_admin_for_test(
        pg_database,
        real_db_client,
        phone=phone,
        password=password,
        org_id=org_id,
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        institution_name="Synthetic R3 Institution",
        pilot_batch_code="R3HTTP",
        service_tags=("OBESITY",),
    )
    headers = _login(
        real_db_client,
        phone,
        password,
        totp_secret=str(activated["totp_secret"]),
    )
    response = real_db_client.get(
        "/api/v1/institution/therapist-invitations", headers=headers
    )
    assert response.status_code == 200

    tracked_tables = (
        "therapist_invitation",
        "therapist_workflow_audit",
        "therapist_workflow_outbox",
        "therapist_workflow_idempotency",
    )
    before = tuple(
        pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
        for table in tracked_tables
    )
    application_id = activated["application_id"]
    pg_database.execute(
        "UPDATE public.institution_application SET status='NEEDS_CORRECTION' "
        f"WHERE application_id='{application_id}'"
    )
    try:
        denied = real_db_client.get(
            "/api/v1/institution/therapist-invitations", headers=headers
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"
        assert tuple(
            pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
            for table in tracked_tables
        ) == before
    finally:
        pg_database.execute(
            "UPDATE public.institution_application SET status='APPROVED' "
            f"WHERE application_id='{application_id}'"
        )


def test_平台审核员列表使用无锁当前性真实HTTP(
    pg_database, real_db_client
) -> None:
    reviewer_id = 9_470_000_000 + secrets.randbelow(900_000)
    reviewer_phone = "195" + f"{reviewer_id % 100_000_000:08d}"
    password = "Synthetic-" + secrets.token_urlsafe(18)
    encoded = hash_password(password).replace("'", "''")
    pg_database.execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) '
        f"VALUES({reviewer_id},'{reviewer_phone}','{encoded}','super_admin','active',NULL)"
    )
    headers = _login(real_db_client, reviewer_phone, password)
    response = real_db_client.get(
        "/api/v1/platform/therapist-reviews", headers=headers
    )
    assert response.status_code == 200
    assert set(response.json()["data"]) == {"items", "next_cursor"}


def test_正式ReviewWriter本人退出提交后像与严格幂等回放(
    pg_database, real_db_client
) -> None:
    offset = secrets.randbelow(7_000_000) + 20_000_000

    async def seed():
        institution = await _seed_ready_institution(pg_database, offset=offset)
        tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = institution
        therapist = await _seed_approved_therapist(
            pg_database,
            tenant_id=tenant_id,
            issued_by=org_admin_id,
            offset=offset,
            valid_until=date.today() + timedelta(days=180),
        )
        return institution, therapist

    institution, therapist = asyncio.run(seed())
    tenant_id, org_admin_id, reviewer_id, _, county_id = institution
    token = create_access_token(
        {
            "sub": str(therapist["user_id"]),
            "role": "therapist",
            "tenant_id": tenant_id,
        }
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "r3-self-exit-stable-0001",
    }
    payload = {"expected_version": 1, "reason_code": "VOLUNTARY_EXIT"}
    tracked_tables = (
        "therapist_status_decision",
        "therapist_workflow_audit",
        "therapist_workflow_outbox",
        "therapist_workflow_idempotency",
    )
    before = tuple(
        pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
        for table in tracked_tables
    )
    try:
        first = real_db_client.post(
            "/api/v1/therapist-onboarding/exit", headers=headers, json=payload
        )
        assert first.status_code == 200, first.json()
        assert first.json()["data"]["status"] == "EXITED"
        assert first.json()["data"]["version"] == 2
        assert pg_database.fetch_value(
            "SELECT status='EXITED' AND version=2 FROM public.therapist_profile "
            f"WHERE therapist_id='{therapist['therapist_id']}'"
        )

        after_first = tuple(
            pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
            for table in tracked_tables
        )
        assert all(after > prior for prior, after in zip(before, after_first, strict=True))

        replay = real_db_client.post(
            "/api/v1/therapist-onboarding/exit", headers=headers, json=payload
        )
        assert replay.status_code == 200, replay.json()
        assert replay.json() == first.json()
        assert tuple(
            pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
            for table in tracked_tables
        ) == after_first

        other_key = real_db_client.post(
            "/api/v1/therapist-onboarding/exit",
            headers={**headers, "Idempotency-Key": "r3-self-exit-other-0002"},
            json={**payload, "expected_version": 2},
        )
        assert other_key.status_code == 403
        assert other_key.json()["code"] == "ACTOR_CURRENTNESS_FORBIDDEN"
        assert tuple(
            pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
            for table in tracked_tables
        ) == after_first
    finally:
        asyncio.run(
            _cleanup_slice2_seed(
                pg_database,
                tenant_id=tenant_id,
                org_admin_id=org_admin_id,
                reviewer_id=reviewer_id,
                county_id=county_id,
            )
        )


def test_正式ReviewWriter本人退出有活动案例保持409且零副作用(
    pg_database, real_db_client
) -> None:
    offset = secrets.randbelow(7_000_000) + 27_000_000

    async def seed():
        institution = await _seed_ready_institution(pg_database, offset=offset)
        tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = institution
        therapist = await _seed_approved_therapist(
            pg_database,
            tenant_id=tenant_id,
            issued_by=org_admin_id,
            offset=offset,
            valid_until=date.today() + timedelta(days=180),
        )
        await pg_database._execute(
            "UPDATE public.therapist_profile SET active_case_count=1 "
            f"WHERE therapist_id='{therapist['therapist_id']}'"
        )
        return institution, therapist

    institution, therapist = asyncio.run(seed())
    tenant_id, org_admin_id, reviewer_id, _, county_id = institution
    token = create_access_token(
        {
            "sub": str(therapist["user_id"]),
            "role": "therapist",
            "tenant_id": tenant_id,
        }
    )
    tracked_tables = (
        "therapist_status_decision",
        "therapist_workflow_audit",
        "therapist_workflow_outbox",
        "therapist_workflow_idempotency",
    )
    before = tuple(
        pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
        for table in tracked_tables
    )
    try:
        response = real_db_client.post(
            "/api/v1/therapist-onboarding/exit",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "r3-self-exit-active-case-0001",
            },
            json={"expected_version": 1, "reason_code": "VOLUNTARY_EXIT"},
        )
        assert response.status_code == 409, response.json()
        assert response.json()["code"] == "THERAPIST_STATE_CONFLICT"
        assert pg_database.fetch_value(
            "SELECT status='APPROVED_ACTIVE' AND version=1 AND active_case_count=1 "
            "FROM public.therapist_profile "
            f"WHERE therapist_id='{therapist['therapist_id']}'"
        )
        assert tuple(
            pg_database.fetch_value(f"SELECT count(*) FROM public.{table}")
            for table in tracked_tables
        ) == before
    finally:
        asyncio.run(
            _cleanup_slice2_seed(
                pg_database,
                tenant_id=tenant_id,
                org_admin_id=org_admin_id,
                reviewer_id=reviewer_id,
                county_id=county_id,
            )
        )


def test_0042到0043对称往返且无对象残留(pg_database) -> None:
    config = _build_alembic_config(os.environ["KG_TEST_MIGRATION_DATABASE_URL"])

    def object_counts() -> tuple[int, int, int]:
        return tuple(
            pg_database.fetch_value(
                "SELECT count(*) FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relkind='" + kind + "'"
            )
            for kind in ("r", "v", "S")
        )

    before = object_counts()
    command.downgrade(config, "20260911_0042")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260911_0042"
    for signature in _SIGNATURES:
        assert pg_database.fetch_value(
            "SELECT to_regprocedure('" + signature + "') IS NULL"
        )
    assert object_counts() == before

    command.upgrade(config, "20260912_0043")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260912_0043"
    for signature in _SIGNATURES:
        assert pg_database.fetch_value(
            "SELECT to_regprocedure('" + signature + "') IS NOT NULL"
        )
    assert object_counts() == before
