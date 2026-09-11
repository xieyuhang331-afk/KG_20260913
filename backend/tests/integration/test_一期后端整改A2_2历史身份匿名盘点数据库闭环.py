from __future__ import annotations

import asyncio
import importlib
import json
import os

import asyncpg
import pytest

from tests.integration.conftest import _to_asyncpg_dsn

pytestmark = pytest.mark.integration


def _configure_inventory(monkeypatch) -> None:
    monkeypatch.setenv(
        "KG_A2_IDENTITY_INVENTORY_DATABASE_URL",
        os.environ["KG_TEST_A2_IDENTITY_INVENTORY_DATABASE_URL"],
    )
    monkeypatch.setenv(
        "KG_A2_IDENTITY_INVENTORY_ROLE",
        os.environ["KG_TEST_A2_IDENTITY_INVENTORY_ROLE"],
    )


def _seed_h0_h7(pg_database) -> None:
    pg_database.execute(
        """
        INSERT INTO public.tenant(
            id,tenant_code,name,type,province,city,status,created_at,updated_at
        ) VALUES (
            8800001,'a2_inventory_tenant','Synthetic Tenant','institution',
            'Synthetic','Synthetic','active',clock_timestamp(),clock_timestamp()
        );
        INSERT INTO public."user"(
            id,phone,password_hash,real_name,role,tenant_id,verify_status,status
        ) VALUES
            (8800001,'synh0000001','synthetic',NULL,'member',NULL,'unverified','active'),
            (8800002,'synh0000002','synthetic','Synthetic','member',NULL,'unverified','active'),
            (8800003,'synh0000003','synthetic',NULL,'member',NULL,NULL,'active'),
            (8800004,'synh0000004','synthetic','Synthetic','member',NULL,'verified','active'),
            (8800005,'synh0000005','synthetic',NULL,'member',8800001,'unverified','active'),
            (8800006,'synh0000006','synthetic',NULL,'member',8800001,'unverified','active'),
            (8800007,'synh0000007','synthetic',NULL,'member',8800001,'unverified','active'),
            (8800008,'synh0000008','synthetic',NULL,'org_admin',NULL,'unverified','active');
        INSERT INTO public.identity_verification_decision(
            decision_ref,user_ref,facts_version,verification_epoch,outcome,
            evidence_digest,actor_type,actor_ref,decided_at
        ) VALUES (
            '00000000-0000-7000-8000-000000000031',8800004,1,1,'verified',
            repeat('a',64),'synthetic','synthetic',clock_timestamp()
        );
        INSERT INTO public.identity_verification_submission(
            submission_id,user_ref,version,status,real_name_ciphertext,
            real_name_nonce,id_card_ciphertext,id_card_nonce,id_card_masked,
            encryption_key_id,content_digest,id_card_digest,
            idempotency_key_digest,consent_version,submitted_at,decided_at,
            reviewed_by,decision_basis_code,evidence_digest
        ) SELECT
            '00000000-0000-7000-8000-000000000032',8800004,1,'verified',
            decode('01','hex'),decode(repeat('00',12),'hex'),decode('02','hex'),
            decode(repeat('01',12),'hex'),'AAAAAA********BBBB',fingerprint_key_id,
            repeat('b',64),repeat('c',64),repeat('d',64),'synthetic-v1',
            clock_timestamp(),clock_timestamp(),8800008,
            'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('e',64)
        FROM identity.identity_claim_algorithm_state WHERE singleton=1;
        INSERT INTO identity.member(
            member_id,member_no,creation_source,status,version,created_at,updated_at
        ) VALUES
            ('00000000-0000-7000-8000-000000000041','A2H4','synthetic','active',1,clock_timestamp(),clock_timestamp()),
            ('00000000-0000-7000-8000-000000000051','A2H5','synthetic','active',1,clock_timestamp(),clock_timestamp());
        INSERT INTO identity.user_member_self_link(
            link_id,user_ref,member_id,source,eligibility_decision_ref,
            establishment_basis,establishment_record_ref,created_at
        ) VALUES
            ('00000000-0000-7000-8000-000000000042',8800005,
             '00000000-0000-7000-8000-000000000041','REGISTRATION_VERIFIED',
             '00000000-0000-7000-8000-000000000043',
             'REGISTRATION_VERIFIED_BOOTSTRAP',
             '00000000-0000-7000-8000-000000000044',clock_timestamp()),
            ('00000000-0000-7000-8000-000000000052',8800006,
             '00000000-0000-7000-8000-000000000051','REGISTRATION_VERIFIED',
             '00000000-0000-7000-8000-000000000053',
             'REGISTRATION_VERIFIED_BOOTSTRAP',
             '00000000-0000-7000-8000-000000000054',clock_timestamp());
        INSERT INTO public.member_service_invitation(
            invitation_id,tenant_id,mode,phone_ciphertext,phone_key_id,
            phone_digest,phone_digest_key_id,phone_masked,code_digest,code_key_id,
            status,failed_attempts,expires_at,issued_by,issued_at,accepted_at,version
        ) VALUES
        (
            '00000000-0000-7000-8000-000000000055',8800001,'SELF',
            decode('03','hex'),'synthetic',repeat('f',64),'synthetic','***',
            repeat('1',64),'synthetic','ACCEPTED',0,
            clock_timestamp()+interval '1 day',8800008,clock_timestamp(),
            clock_timestamp(),1
        ),(
            '00000000-0000-7000-8000-000000000057',8800001,'SELF',
            decode('04','hex'),'synthetic',repeat('2',64),'synthetic','***',
            repeat('3',64),'synthetic','ACCEPTED',0,
            clock_timestamp()+interval '1 day',8800008,clock_timestamp(),
            clock_timestamp(),1
        );
        INSERT INTO public.service_enrollment(
            enrollment_id,invitation_id,tenant_id,subject_member_id,mode,status,
            service_scope_tags,accepted_at,created_at,updated_at,version
        ) VALUES
        (
            '00000000-0000-7000-8000-000000000056',
            '00000000-0000-7000-8000-000000000055',8800001,
            '00000000-0000-7000-8000-000000000051','SELF','ACCEPTED',
            '[]'::jsonb,clock_timestamp(),clock_timestamp(),clock_timestamp(),1
        ),(
            '00000000-0000-7000-8000-000000000058',
            '00000000-0000-7000-8000-000000000057',8800001,
            '00000000-0000-7000-8000-000000000051','SELF','REVOKED',
            '[]'::jsonb,clock_timestamp(),clock_timestamp(),clock_timestamp(),1
        );
        """
    )


def _business_state_hash(pg_database) -> str:
    return pg_database.fetch_value(
        """
        SELECT md5(concat_ws('|',
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY id)), '')
             FROM public."user" t),
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY submission_id)), '')
             FROM public.identity_verification_submission t),
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY decision_ref)), '')
             FROM public.identity_verification_decision t),
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY claim_id)), '')
             FROM identity.identity_subject_claim_registry t),
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY member_id)), '')
             FROM identity.member t),
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY link_id)), '')
             FROM identity.user_member_self_link t),
            (SELECT COALESCE(md5(string_agg(to_jsonb(t)::text,'|' ORDER BY enrollment_id)), '')
             FROM public.service_enrollment t)
        ))
        """
    )


def test_A2_2匿名盘点Fresh数据库只读稳定且不输出行级事实(pg_database, monkeypatch, capsys) -> None:
    composition = importlib.import_module("app.composition.identity_remediation")
    cli = importlib.import_module("scripts.run_a2_identity_inventory")

    _configure_inventory(monkeypatch)
    _seed_h0_h7(pg_database)
    before = _business_state_hash(pg_database)

    first = asyncio.run(composition.run_identity_inventory())
    second = asyncio.run(composition.run_identity_inventory())
    first_public = first.to_public_dict()
    second_public = second.to_public_dict()
    assert first.evidence_hash == second.evidence_hash
    assert first_public["classification_rule_hash"] == second_public[
        "classification_rule_hash"
    ]
    assert first_public["code_sha256"] == second_public["code_sha256"]
    assert first_public["batch_ref"] != second_public["batch_ref"]
    assert first.input_count == 8
    assert first.class_counts == {f"H{index}": 1 for index in range(8)}
    assert first.input_count == sum(first.class_counts.values())
    assert first.multi_primary_match_count == 0
    assert first.unclassified_count == 0

    assert cli.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["evidence_hash"] == first_public["evidence_hash"]
    assert output["class_counts"] == {
        f"H{index}": "SMALL_COUNT" for index in range(8)
    }
    assert output["small_count_present"] is True
    assert output["batch_ref"] not in {
        first_public["batch_ref"],
        second_public["batch_ref"],
    }

    after = _business_state_hash(pg_database)
    assert before == after
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260912_0043"


def test_A2_2匿名盘点事务为RepeatableRead且禁止DML_DDL(pg_database, monkeypatch) -> None:
    composition = importlib.import_module("app.composition.identity_remediation")
    _configure_inventory(monkeypatch)

    proof = asyncio.run(composition.inspect_read_only_transaction())
    assert proof == {
        "transaction_read_only": "on",
        "transaction_isolation": "repeatable read",
        "database_role": "inventory_reader_verified",
    }

    async def assert_forbidden() -> None:
        connection = await asyncpg.connect(_to_asyncpg_dsn(pg_database.database_url))
        try:
            transaction = connection.transaction(isolation="repeatable_read", readonly=True)
            await transaction.start()
            try:
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    await connection.execute('UPDATE public."user" SET status = status')
                await transaction.rollback()

                transaction = connection.transaction(isolation="repeatable_read", readonly=True)
                await transaction.start()
                with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
                    await connection.execute("CREATE TABLE public.a2_inventory_forbidden(id integer)")
                await transaction.rollback()
            finally:
                if connection.is_in_transaction():
                    await connection.execute("ROLLBACK")
        finally:
            await connection.close()

    asyncio.run(assert_forbidden())


def test_A2_2匿名盘点CLI拒绝凭据参数且不回显环境值(pg_database, monkeypatch, capsys) -> None:
    cli = importlib.import_module("scripts.run_a2_identity_inventory")
    _configure_inventory(monkeypatch)

    with pytest.raises(SystemExit):
        cli.main(["--database-url", "forbidden-value"])
    captured = capsys.readouterr()
    assert "forbidden-value" not in captured.out
    assert "forbidden-value" not in captured.err
    if pg_database.database_url in captured.out or pg_database.database_url in captured.err:
        pytest.fail("A2_INVENTORY_CREDENTIAL_OUTPUT_DETECTED", pytrace=False)
