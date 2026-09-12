from __future__ import annotations

import asyncio
import base64
import json
import os

import asyncpg
import pytest
from alembic import command
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.uuid_generator import Uuid7Generator
from tests.integration.conftest import _build_alembic_config, _get_test_database_url

pytestmark = pytest.mark.integration


async def _seed_pre_0044(database_url: str, values: dict[str, object]) -> None:
    connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
                "VALUES($1,NULL,'合成总部','DIRECT44-HQ','headquarter','active',1),"
                "($2,$1,'合成省','DIRECT44-P','province','active',1),"
                "($3,$2,'合成市','DIRECT44-C','city','active',1),"
                "($4,$3,'合成区县','DIRECT44-D','county','active',1)",
                values["hq_id"], values["province_id"], values["city_id"], values["county_id"],
            )
            await connection.execute(
                "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
                "VALUES($1,$2,'DIRECT44-T','合成机构','store','合成省','合成市','active',now(),now())",
                values["tenant_id"], values["county_id"],
            )
            await connection.execute(
                'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
                "($1,$2,'synthetic','super_admin','active',NULL),"
                "($3,$4,'synthetic','org_admin','active',$5)",
                values["reviewer_id"], values["user_phone"], values["org_admin_id"],
                values["org_admin_phone"], values["tenant_id"],
            )
            await connection.execute(
                "INSERT INTO public.institution_invitation("
                "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,"
                "applicant_phone_digest,pilot_batch_code,administrative_region_id,code_digest,"
                "status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) "
                "VALUES($1,'合成机构','HEALTH_STORE',$2,repeat('a',64),'DIRECT44',$3,"
                "repeat('b',64),'ISSUED',0,now()+interval '1 day',$4,now(),NULL,1)",
                values["institution_invitation_id"], values["controlled_ciphertext"],
                values["county_id"], values["reviewer_id"],
            )
            await connection.execute(
                "INSERT INTO public.institution_application("
                "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,"
                "correction_fields,current_revision_no,tenant_internal_id,tenant_public_id,service_ready,"
                "created_at,updated_at,submitted_at,reviewed_at,version) "
                "VALUES($1,$2,$3,'HEALTH_STORE','APPROVED','{}'::jsonb,'[]'::jsonb,1,$4,$5,"
                "false,now(),now(),now(),now(),3)",
                values["application_id"], values["institution_invitation_id"],
                values["org_admin_id"], values["tenant_id"], values["tenant_public_id"],
            )
            await connection.execute(
                "INSERT INTO public.therapist_invitation("
                "invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,phone_digest,"
                "phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,expires_at,status,"
                "failed_attempts,issued_by,issued_at,activated_at,revoked_at,version) "
                "VALUES($1,$2,$3,$4,repeat('c',64),'legacy-digest','139****0004',repeat('d',64),"
                "'legacy-code',now()+interval '1 day','INVITED',0,$5,now(),NULL,NULL,1)",
                values["therapist_invitation_id"], values["tenant_id"],
                values["therapist_ciphertext"], values["therapist_key_id"], values["org_admin_id"],
            )
    finally:
        await connection.close()


async def _cleanup(database_url: str, values: dict[str, object]) -> None:
    connection = await asyncpg.connect(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM public.identity_phone_claim WHERE user_id=ANY($1::bigint[]) "
                "OR claim_ref=ANY($2::uuid[])",
                [values["reviewer_id"], values["org_admin_id"]],
                [values["institution_invitation_id"], values["therapist_invitation_id"]],
            )
            await connection.execute(
                "DELETE FROM public.institution_tenant_origin WHERE tenant_id=$1",
                values["tenant_id"],
            )
            await connection.execute(
                "DELETE FROM public.therapist_invitation WHERE invitation_id=$1",
                values["therapist_invitation_id"],
            )
            await connection.execute(
                "DELETE FROM public.institution_application WHERE application_id=$1",
                values["application_id"],
            )
            await connection.execute(
                "DELETE FROM public.institution_invitation WHERE invitation_id=$1",
                values["institution_invitation_id"],
            )
            await connection.execute(
                'DELETE FROM public."user" WHERE id=ANY($1::bigint[])',
                [values["reviewer_id"], values["org_admin_id"]],
            )
            await connection.execute("DELETE FROM public.tenant WHERE id=$1", values["tenant_id"])
            await connection.execute(
                "DELETE FROM public.platform_org WHERE id=ANY($1::bigint[])",
                [values["county_id"], values["city_id"], values["province_id"], values["hq_id"]],
            )
    finally:
        await connection.close()


def test_0044在DDL前解析非空账号手机号并原子建立全局claim(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260912_0043")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260912_0043"
    application_role = os.environ["KG_DATABASE_USER"]
    legacy_signature = "public.auth_register_member_v1(character varying,character varying)"
    assert pg_database.fetch_value(
        "SELECT has_function_privilege(" 
        f"'{application_role}',to_regprocedure('{legacy_signature}'),'EXECUTE')"
    ) is True

    generator = Uuid7Generator()
    values: dict[str, object] = {
        "hq_id": 9944001,
        "province_id": 9944002,
        "city_id": 9944003,
        "county_id": 9944004,
        "tenant_id": 9944005,
        "reviewer_id": 9944006,
        "org_admin_id": 9944007,
        "user_phone": "13900000001",
        "org_admin_phone": "13900000002",
        "controlled_phone": "13900000003",
        "therapist_phone": "13900000004",
        "institution_invitation_id": generator.generate(),
        "application_id": generator.generate(),
        "tenant_public_id": generator.generate(),
        "therapist_invitation_id": generator.generate(),
    }
    onboarding_key = base64.b64decode(os.environ["KG_ONBOARDING_PII_KEK_B64"], validate=True)
    therapist_key_id = os.environ["KG_THERAPIST_PII_ENCRYPTION_CURRENT_KEY_ID"]
    therapist_keys = json.loads(os.environ["KG_THERAPIST_PII_ENCRYPTION_KEYRING_JSON"])
    therapist_key = base64.b64decode(therapist_keys[therapist_key_id], validate=True)
    nonce = b"\x01" * 12
    values["controlled_ciphertext"] = nonce + AESGCM(onboarding_key).encrypt(
        nonce, str(values["controlled_phone"]).encode(), b"institution-onboarding-v1"
    )
    therapist_nonce = b"\x02" * 12
    therapist_aad = (
        "phase1-slice2/invitation-phone/v1\0"
        f"{str(values['tenant_public_id']).lower()}\0"
        f"{str(values['therapist_invitation_id']).lower()}"
    ).encode()
    values["therapist_ciphertext"] = therapist_nonce + AESGCM(therapist_key).encrypt(
        therapist_nonce, str(values["therapist_phone"]).encode(), therapist_aad
    )
    values["therapist_key_id"] = therapist_key_id

    asyncio.run(_seed_pre_0044(_get_test_database_url(), values))
    try:
        command.upgrade(config, "20260913_0044")

        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260913_0044"
        assert pg_database.fetch_value(
            "SELECT has_function_privilege(" 
            f"'{application_role}',to_regprocedure('{legacy_signature}'),'EXECUTE')"
        ) is False
        assert pg_database.fetch_value(
            "SELECT has_function_privilege(" 
            f"'{application_role}',to_regprocedure('public.auth_register_member_v2(uuid,character varying,character varying,character,character varying,jsonb)'),'EXECUTE')"
        ) is True
        assert pg_database.fetch_value(
            "SELECT count(*) FROM public.identity_phone_claim WHERE state='BOUND' AND claim_kind='EXISTING_USER'"
        ) == 2
        assert pg_database.fetch_value(
            "SELECT count(*) FROM public.identity_phone_claim WHERE state='PENDING' "
            "AND claim_kind IN ('CONTROLLED_ORG_ADMIN','THERAPIST_ACCOUNT')"
        ) == 2
        assert pg_database.fetch_value(
            "SELECT count(*)=count(DISTINCT (phone_digest_key_id,phone_digest)) "
            "FROM public.identity_phone_claim WHERE state IN ('PENDING','BOUND')"
        ) is True
    finally:
        if pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260913_0044":
            asyncio.run(_cleanup(_get_test_database_url(), values))


def test_0044下游Runtime只能经既有外层受限入口使用机构来源权威(pg_database) -> None:
    runtime_roles = (
        os.environ["KG_HEALTH_RECORD_WRITER_ROLE"],
        os.environ["KG_ASSESSMENT_READINESS_WRITER_ROLE"],
        os.environ["KG_SLICE4_IDENTITY_AUTHORITY_ROLE"],
        os.environ["KG_SLICE5_WORKFLOW_WORKER_ROLE"],
        os.environ["KG_SLICE7_MILESTONE_WRITER_ROLE"],
        os.environ["KG_SLICE7_CASE_WRITER_ROLE"],
        os.environ["KG_SLICE7_TRANSFER_WRITER_ROLE"],
        os.environ["KG_SLICE7_EXPORT_WORKER_ROLE"],
        os.environ["KG_SLICE7_FAMILY_READER_ROLE"],
        os.environ["KG_SLICE7_OVERSIGHT_READER_ROLE"],
    )
    helper = "public.institution_tenant_origin_current_v1(bigint,uuid)"
    for role in runtime_roles:
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{helper}','EXECUTE')"
        ) is False
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert pg_database.fetch_value(
                "SELECT has_table_privilege("
                f"'{role}','public.institution_tenant_origin','{privilege}')"
            ) is False
