from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import (
    _build_alembic_config,
    _get_test_database_url,
    _to_asyncpg_dsn,
)

pytestmark = pytest.mark.integration

CLASSIFICATION_RULE_HASH = (
    "9801dbb22c45cb679fdae43dd72dbf804e7f5cb4b0095d28f9d572f3d6757bc5"
)
WORKSET_SQL = (
    "SELECT * FROM identity.a2_identity_remediation_subject_workset_v1("
    "$1,$2,$3,$4,$5)"
)
MATERIAL_SQL = (
    "SELECT * FROM identity.a2_identity_remediation_h3_material_v1("
    "$1,$2,$3,$4)"
)
MUTATION_SQL = (
    "SELECT * FROM identity.a2_identity_remediation_h3_clear_legacy_v1("
    "$1,$2,$3,$4,$5,$6,$7)"
)
WRITER_SQL = (
    "SELECT * FROM identity.a2_identity_remediation_ledger_v1("
    + ",".join(f"${index}" for index in range(1, 30))
    + ")"
)
FUNCTIONS = (
    "identity.a2_identity_remediation_subject_workset_v1(uuid,bigint,varchar,varchar,varchar)",
    "identity.a2_identity_remediation_h3_material_v1(uuid,uuid,bigint,varchar)",
    "identity.a2_identity_remediation_h3_clear_legacy_v1(uuid,uuid,bigint,varchar,varchar,varchar,varchar)",
)

WORKSET_PREIMAGE_TAGS = (
    "subject_user_ref",
    "role",
    "legacy_pii_present",
    "identity_authority_signal",
    "formal_chain_complete",
    "tenant_present",
    "tenant_relation_known",
    "self_link_count",
    "enrollment_count",
    "current_enrollment_count",
    "tenant_matches_unique_current",
    "enrollment_scope_complete",
    "primary_class",
    "secondary_flags",
)


def _workset_field(tag: str, value: object | None) -> bytes:
    if value is None:
        return f"{tag}=N;".encode()
    if isinstance(value, bool):
        raw = str(value).lower().encode()
    else:
        raw = str(value).encode()
    return f"{tag}=V{len(raw)}:".encode() + raw + b";"


def _workset_preimage_digest(values: tuple[object | None, ...]) -> str:
    assert len(values) == len(WORKSET_PREIMAGE_TAGS)
    payload = b"A2_RP_WORKSET_PREIMAGE_V1;" + b"".join(
        _workset_field(tag, value)
        for tag, value in zip(WORKSET_PREIMAGE_TAGS, values, strict=True)
    )
    return hashlib.sha256(payload).hexdigest()


def test_A2_2_RP_Workset长度前缀语法区分分隔符空串NULL和多字节() -> None:
    assert _workset_field("value", None) != _workset_field("value", "")
    assert _workset_field("value", "康") == b"value=V3:\xe5\xba\xb7;"
    assert (
        _workset_field("left", "a|b") + _workset_field("right", "c")
        != _workset_field("left", "a") + _workset_field("right", "b|c")
    )


def _writer_args(
    operation: str,
    batch_ref: UUID,
    *,
    item_ref: UUID | None = None,
    subject_user_ref: int | None = None,
    primary_class: str | None = None,
    secondary_flags: int | None = None,
    expected_version: int | None = None,
    expected_target_state_digest: str | None = None,
    classification_rule_hash: str | None = None,
    snapshot_ref_hash: str | None = None,
    counts: tuple[int, ...] | None = None,
    preimage_digest: str | None = None,
    eligibility_action_gate_digest: str | None = None,
    mutation_digest: str | None = None,
    business_postimage_digest: str | None = None,
    reason_code: str,
    idempotency_key_digest: str | None = None,
) -> tuple[object, ...]:
    count_values = counts or (None,) * 9
    return (
        operation,
        batch_ref,
        item_ref,
        subject_user_ref,
        primary_class,
        secondary_flags,
        expected_version,
        expected_target_state_digest,
        classification_rule_hash,
        snapshot_ref_hash,
        *count_values,
        preimage_digest,
        eligibility_action_gate_digest,
        mutation_digest,
        business_postimage_digest,
        "a2-rp-synthetic-operator",
        reason_code,
        idempotency_key_digest or uuid4().hex * 2,
        uuid4(),
        uuid4(),
        datetime(2026, 9, 3, tzinfo=UTC),
    )


def _write(database, args: tuple[object, ...]) -> dict[str, object]:
    rows = database.fetch_rows(WRITER_SQL, *args)
    assert len(rows) == 1
    return rows[0]


def _parameterized_value(database, sql: str, *args):
    rows = database.fetch_rows(sql, *args)
    assert len(rows) == 1 and len(rows[0]) == 1
    return next(iter(rows[0].values()))


def _transition_args(
    operation: str,
    batch_ref: UUID,
    prior: dict[str, object],
    *,
    item_ref: UUID | None = None,
    reason_code: str,
    gate_digest: str | None = None,
    mutation_digest: str | None = None,
    postimage_digest: str | None = None,
) -> tuple[object, ...]:
    return _writer_args(
        operation,
        batch_ref,
        item_ref=item_ref,
        expected_version=int(prior["version"]),
        expected_target_state_digest=str(prior["state_digest"]),
        eligibility_action_gate_digest=gate_digest,
        mutation_digest=mutation_digest,
        business_postimage_digest=postimage_digest,
        reason_code=reason_code,
    )


def _seed_h0_h7(pg_database) -> None:
    pg_database.execute(
        """
        INSERT INTO public.tenant(
            id,tenant_code,name,type,province,city,status,created_at,updated_at
        ) VALUES (
            8836001,'a2_rp_tenant','Synthetic Tenant','institution',
            'Synthetic','Synthetic','active',clock_timestamp(),clock_timestamp()
        );
        INSERT INTO public."user"(
            id,phone,password_hash,real_name,id_card,role,tenant_id,verify_status,status
        ) VALUES
            (8836001,'rp36h000001','synthetic',NULL,NULL,'member',NULL,'unverified','active'),
            (8836002,'rp36h000002','synthetic','Synthetic','SYNTHETIC-CARD','member',NULL,'unverified','active'),
            (8836003,'rp36h000003','synthetic',NULL,NULL,'member',NULL,NULL,'active'),
            (8836004,'rp36h000004','synthetic','Synthetic','SYNTHETIC-CARD','member',NULL,'verified','active'),
            (8836005,'rp36h000005','synthetic',NULL,NULL,'member',8836001,'unverified','active'),
            (8836006,'rp36h000006','synthetic',NULL,NULL,'member',8836001,'unverified','active'),
            (8836007,'rp36h000007','synthetic',NULL,NULL,'member',8836001,'unverified','active'),
            (8836008,'rp36h000008','synthetic','Synthetic',NULL,'org_admin',NULL,'unverified','active');
        INSERT INTO public.identity_verification_decision(
            decision_ref,user_ref,facts_version,verification_epoch,outcome,
            evidence_digest,actor_type,actor_ref,decided_at
        ) VALUES (
            '00000000-0000-7036-8000-000000000031',8836004,1,1,'verified',
            repeat('a',64),'synthetic','synthetic',clock_timestamp()
        );
        INSERT INTO public.identity_verification_submission(
            submission_id,user_ref,version,status,real_name_ciphertext,
            real_name_nonce,id_card_ciphertext,id_card_nonce,id_card_masked,
            encryption_key_id,content_digest,id_card_digest,
            idempotency_key_digest,consent_version,submitted_at,decided_at,
            reviewed_by,decision_basis_code,evidence_digest
        ) SELECT
            '00000000-0000-7036-8000-000000000032',8836004,1,'verified',
            decode('01','hex'),decode(repeat('00',12),'hex'),decode('02','hex'),
            decode(repeat('01',12),'hex'),'SYNTHETIC-MASKED',fingerprint_key_id,
            repeat('b',64),repeat('c',64),repeat('d',64),'synthetic-v1',
            clock_timestamp(),clock_timestamp(),8836008,
            'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('a',64)
        FROM identity.identity_claim_algorithm_state WHERE singleton=1;
        INSERT INTO identity.member(
            member_id,member_no,creation_source,status,version,created_at,updated_at
        ) VALUES
            ('00000000-0000-7036-8000-000000000041','A2RP4','synthetic','active',1,clock_timestamp(),clock_timestamp()),
            ('00000000-0000-7036-8000-000000000051','A2RP5','synthetic','active',1,clock_timestamp(),clock_timestamp());
        INSERT INTO identity.user_member_self_link(
            link_id,user_ref,member_id,source,eligibility_decision_ref,
            establishment_basis,establishment_record_ref,created_at
        ) VALUES
            ('00000000-0000-7036-8000-000000000042',8836005,
             '00000000-0000-7036-8000-000000000041','REGISTRATION_VERIFIED',
             '00000000-0000-7036-8000-000000000043',
             'REGISTRATION_VERIFIED_BOOTSTRAP',
             '00000000-0000-7036-8000-000000000044',clock_timestamp()),
            ('00000000-0000-7036-8000-000000000052',8836006,
             '00000000-0000-7036-8000-000000000051','REGISTRATION_VERIFIED',
             '00000000-0000-7036-8000-000000000053',
             'REGISTRATION_VERIFIED_BOOTSTRAP',
             '00000000-0000-7036-8000-000000000054',clock_timestamp());
        INSERT INTO public.member_service_invitation(
            invitation_id,tenant_id,mode,phone_ciphertext,phone_key_id,
            phone_digest,phone_digest_key_id,phone_masked,code_digest,code_key_id,
            status,failed_attempts,expires_at,issued_by,issued_at,accepted_at,version
        ) VALUES
        ('00000000-0000-7036-8000-000000000055',8836001,'SELF',decode('03','hex'),
         'synthetic',repeat('f',64),'synthetic','***',repeat('1',64),'synthetic',
         'ACCEPTED',0,clock_timestamp()+interval '1 day',8836008,clock_timestamp(),clock_timestamp(),1),
        ('00000000-0000-7036-8000-000000000057',8836001,'SELF',decode('04','hex'),
         'synthetic',repeat('2',64),'synthetic','***',repeat('3',64),'synthetic',
         'ACCEPTED',0,clock_timestamp()+interval '1 day',8836008,clock_timestamp(),clock_timestamp(),1);
        INSERT INTO public.service_enrollment(
            enrollment_id,invitation_id,tenant_id,subject_member_id,mode,status,
            service_scope_tags,accepted_at,created_at,updated_at,version
        ) VALUES
        ('00000000-0000-7036-8000-000000000056','00000000-0000-7036-8000-000000000055',8836001,
         '00000000-0000-7036-8000-000000000051','SELF','ACCEPTED','[]'::jsonb,
         clock_timestamp(),clock_timestamp(),clock_timestamp(),1),
        ('00000000-0000-7036-8000-000000000058','00000000-0000-7036-8000-000000000057',8836001,
         '00000000-0000-7036-8000-000000000051','SELF','REVOKED','[]'::jsonb,
         clock_timestamp(),clock_timestamp(),clock_timestamp(),1);
        """
    )


def _plan_and_register(pg_database, writer):
    batch_ref = uuid4()
    planned = _write(
        writer,
        _writer_args(
            "PLAN_BATCH",
            batch_ref,
            classification_rule_hash=CLASSIFICATION_RULE_HASH,
            snapshot_ref_hash="6" * 64,
            counts=(8, 1, 1, 1, 1, 1, 1, 1, 1),
            reason_code="A2_BATCH_PLANNED",
        ),
    )
    rows = writer.fetch_rows(
        WORKSET_SQL,
        batch_ref,
        planned["version"],
        planned["state_digest"],
        CLASSIFICATION_RULE_HASH,
        "6" * 64,
    )
    assert len(rows) == 8
    assert {row["primary_class"] for row in rows} == {f"H{i}" for i in range(8)}
    items = {}
    for row in rows:
        item_ref = uuid4()
        item = _write(
            writer,
            _writer_args(
                "REGISTER_ITEM",
                batch_ref,
                item_ref=item_ref,
                subject_user_ref=int(row["subject_user_ref"]),
                primary_class=str(row["primary_class"]),
                secondary_flags=int(row["secondary_flags"]),
                preimage_digest=str(row["preimage_digest"]),
                reason_code="A2_ITEM_DISCOVERED",
            ),
        )
        items[str(row["primary_class"])] = (item_ref, item, row)
    running = _write(
        writer,
        _transition_args(
            "START_BATCH",
            batch_ref,
            planned,
            reason_code="A2_BATCH_CONTROLLED",
        ),
    )
    assert running["state"] == "RUNNING"
    assert pg_database.fetch_value("SELECT count(*) FROM identity.identity_remediation_item") == 8
    return batch_ref, running, items


def test_A2_2_RP_Workset与0034分类逐项一致且不输出PII(
    pg_database,
    a2_identity_remediation_writer_database,
) -> None:
    _seed_h0_h7(pg_database)
    batch_ref, _, items = _plan_and_register(
        pg_database, a2_identity_remediation_writer_database
    )
    assert set(items) == {f"H{i}" for i in range(8)}
    assert all(
        set(row) == {
            "subject_user_ref",
            "primary_class",
            "secondary_flags",
            "preimage_digest",
        }
        for _, _, row in items.values()
    )
    h3_subject = int(items["H3"][2]["subject_user_ref"])
    assert h3_subject == 8836004
    assert items["H0"][2]["preimage_digest"] == _workset_preimage_digest(
        (
            8836001,
            "member",
            False,
            False,
            False,
            False,
            True,
            0,
            None,
            None,
            None,
            None,
            "H0",
            0,
        )
    )
    with pytest.raises(asyncpg.RaiseError, match="A2_REMEDIATION_STALE_VERSION"):
        a2_identity_remediation_writer_database.fetch_rows(
            WORKSET_SQL,
            batch_ref,
            999,
            "0" * 64,
            CLASSIFICATION_RULE_HASH,
            "6" * 64,
        )


def test_A2_2_RP_H3Material与Mutation同事务原子且漂移拒绝(
    pg_database,
    a2_identity_remediation_writer_database,
) -> None:
    writer = a2_identity_remediation_writer_database
    batch_ref = pg_database.fetch_value(
        "SELECT batch_ref FROM identity.identity_remediation_batch ORDER BY created_at DESC LIMIT 1"
    )
    item_row = pg_database.fetch_rows(
        "SELECT item_ref,version,state_digest FROM identity.identity_remediation_item "
        "WHERE batch_ref=$1 AND primary_class='H3'", batch_ref
    )
    item_ref = item_row[0]["item_ref"]
    discovered = item_row[0]
    discovered_material = writer.fetch_rows(
        MATERIAL_SQL,
        batch_ref,
        item_ref,
        discovered["version"],
        discovered["state_digest"],
    )
    assert len(discovered_material) == 1
    discovered_chain_digest = str(discovered_material[0]["chain_digest"])
    pg_database.execute(
        "UPDATE identity.identity_subject_claim_registry "
        "SET fingerprint_key_id='synthetic-drift' WHERE user_ref=8836004"
    )
    with pytest.raises(asyncpg.RaiseError, match="A2_REMEDIATION_H3_CHAIN_INVALID"):
        writer.fetch_rows(
            MATERIAL_SQL,
            batch_ref,
            item_ref,
            discovered["version"],
            discovered["state_digest"],
        )
    assert pg_database.fetch_value(
        'SELECT real_name IS NOT NULL AND id_card IS NOT NULL FROM public."user" WHERE id=8836004'
    ) is True
    pg_database.execute(
        "UPDATE identity.identity_subject_claim_registry claim "
        "SET identity_fingerprint=submission.id_card_digest,"
        "fingerprint_key_id=submission.encryption_key_id "
        "FROM public.identity_verification_submission submission "
        "WHERE claim.user_ref=8836004 AND submission.user_ref=claim.user_ref "
        "AND submission.status='verified'"
    )
    gate_digest = "7" * 64
    ready = _write(
        writer,
        _transition_args(
            "MARK_ITEM_READY",
            batch_ref,
            discovered,
            item_ref=item_ref,
            reason_code="A2_CANONICAL_MATCH_APPROVED",
            gate_digest=gate_digest,
        ),
    )
    processing = _write(
        writer,
        _transition_args(
            "START_ITEM",
            batch_ref,
            ready,
            item_ref=item_ref,
            reason_code="A2_APPROVED_REMEDIATION",
        ),
    )
    processing_material = writer.fetch_rows(
        MATERIAL_SQL,
        batch_ref,
        item_ref,
        processing["version"],
        processing["state_digest"],
    )
    assert len(processing_material) == 1
    processing_chain_digest = str(processing_material[0]["chain_digest"])
    assert processing_chain_digest != discovered_chain_digest

    async def run_rollback_proofs() -> None:
        dsn = _to_asyncpg_dsn(
            os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"]
        )
        connection = await asyncpg.connect(dsn)
        try:
            transaction = connection.transaction()
            await transaction.start()
            with pytest.raises(asyncpg.RaiseError, match="A2_REMEDIATION_H3_CHAIN_INVALID"):
                await connection.fetchrow(
                    MUTATION_SQL,
                    batch_ref,
                    item_ref,
                    processing["version"],
                    processing["state_digest"],
                    discovered_chain_digest,
                    gate_digest,
                    "A2_CANONICAL_MATCH_APPROVED",
                )
            await transaction.rollback()

            transaction = connection.transaction()
            await transaction.start()
            mutation = await connection.fetchrow(
                MUTATION_SQL,
                batch_ref,
                item_ref,
                processing["version"],
                processing["state_digest"],
                processing_chain_digest,
                gate_digest,
                "A2_CANONICAL_MATCH_APPROVED",
            )
            assert mutation is not None
            bad_complete = _transition_args(
                "COMPLETE_ITEM",
                batch_ref,
                {**processing, "version": 999},
                item_ref=item_ref,
                reason_code="A2_APPROVED_REMEDIATION",
                mutation_digest=str(mutation["mutation_digest"]),
                postimage_digest=str(mutation["postimage_digest"]),
            )
            with pytest.raises(asyncpg.RaiseError, match="A2_REMEDIATION_STALE_VERSION"):
                await connection.fetchrow(WRITER_SQL, *bad_complete)
            await transaction.rollback()

        finally:
            await connection.close()

    asyncio.run(run_rollback_proofs())
    assert pg_database.fetch_value('SELECT real_name IS NOT NULL AND id_card IS NOT NULL FROM public."user" WHERE id=8836004') is True
    assert _parameterized_value(
        pg_database,
        "SELECT status FROM identity.identity_remediation_item WHERE item_ref=$1",
        item_ref,
    ) == "PROCESSING"

    async def run_commit() -> None:
        dsn = _to_asyncpg_dsn(
            os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"]
        )
        connection = await asyncpg.connect(dsn)
        try:
            transaction = connection.transaction()
            await transaction.start()
            fresh_material = await connection.fetchrow(
                MATERIAL_SQL,
                batch_ref,
                item_ref,
                processing["version"],
                processing["state_digest"],
            )
            assert fresh_material is not None
            mutation = await connection.fetchrow(
                MUTATION_SQL,
                batch_ref,
                item_ref,
                processing["version"],
                processing["state_digest"],
                fresh_material["chain_digest"],
                gate_digest,
                "A2_CANONICAL_MATCH_APPROVED",
            )
            assert mutation is not None
            complete_args = _transition_args(
                "COMPLETE_ITEM",
                batch_ref,
                processing,
                item_ref=item_ref,
                reason_code="A2_APPROVED_REMEDIATION",
                mutation_digest=str(mutation["mutation_digest"]),
                postimage_digest=str(mutation["postimage_digest"]),
            )
            completed = await connection.fetchrow(WRITER_SQL, *complete_args)
            assert completed is not None and completed["state"] == "REMEDIATED"
            await transaction.commit()
        finally:
            await connection.close()

    asyncio.run(run_commit())
    assert pg_database.fetch_value('SELECT real_name IS NULL AND id_card IS NULL FROM public."user" WHERE id=8836004') is True
    assert _parameterized_value(
        pg_database,
        "SELECT status FROM identity.identity_remediation_item WHERE item_ref=$1",
        item_ref,
    ) == "REMEDIATED"


def test_A2_2_RP_Writer仅有函数EXECUTE且底表权限保持零(
    pg_database,
    a2_identity_remediation_writer_database,
    a2_identity_remediation_confirmation_database,
) -> None:
    writer_role = os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE"]
    confirmation_role = os.environ[
        "KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"
    ]
    for function in FUNCTIONS:
        assert _parameterized_value(
            pg_database,
            "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
            writer_role,
            function,
        ) is True
        assert _parameterized_value(
            pg_database,
            "SELECT has_function_privilege($1,to_regprocedure($2),'EXECUTE')",
            confirmation_role,
            function,
        ) is False
    for table in (
        'public."user"',
        "public.identity_verification_submission",
        "public.identity_verification_decision",
        "identity.identity_subject_claim_registry",
        "identity.identity_remediation_batch",
        "identity.identity_remediation_item",
    ):
        assert _parameterized_value(
            pg_database,
            "SELECT has_table_privilege($1,to_regclass($2),'SELECT')",
            writer_role,
            table,
        ) is False
        assert _parameterized_value(
            pg_database,
            "SELECT has_table_privilege($1,to_regclass($2),'UPDATE')",
            writer_role,
            table,
        ) is False
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        a2_identity_remediation_confirmation_database.fetch_rows(
            MATERIAL_SQL, uuid4(), uuid4(), 1, "0" * 64
        )


def test_Z_A2_2_RP_upgrade_downgrade_reupgrade只影响0036对象(pg_database) -> None:
    config = _build_alembic_config(_get_test_database_url())
    before = pg_database.fetch_value(
        "SELECT md5(concat_ws('|',(SELECT count(*)::text FROM public.\"user\"),"
        "(SELECT count(*)::text FROM identity.identity_remediation_batch),"
        "(SELECT count(*)::text FROM identity.identity_remediation_item)))"
    )
    try:
        command.downgrade(config, "20260902_0035")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260902_0035"
        assert all(
            _parameterized_value(
                pg_database, "SELECT to_regprocedure($1)", function
            ) is None
            for function in FUNCTIONS
        )
        assert pg_database.fetch_value(
            "SELECT to_regprocedure('identity.a2_identity_remediation_ledger_v1(varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,timestamp with time zone)') IS NOT NULL"
        ) is True
        assert before == pg_database.fetch_value(
            "SELECT md5(concat_ws('|',(SELECT count(*)::text FROM public.\"user\"),"
            "(SELECT count(*)::text FROM identity.identity_remediation_batch),"
            "(SELECT count(*)::text FROM identity.identity_remediation_item)))"
        )
    finally:
        command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260911_0042"
