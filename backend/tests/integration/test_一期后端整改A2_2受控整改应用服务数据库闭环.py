from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.integration.conftest import _build_alembic_config, _to_asyncpg_dsn

pytestmark = pytest.mark.integration

PRODUCTION_PATHS = (
    Path("app/modules/auth/identity_remediation_application.py"),
    Path("app/modules/auth/identity_remediation_ledger_repository.py"),
    Path("app/modules/auth/identity_remediation_subject_repository.py"),
    Path("app/composition/identity_remediation_runner.py"),
    Path("scripts/run_a2_identity_remediation.py"),
)

_SAFE_DATABASE_NAME = re.compile(r"^kg_it_[rc]_[a-z0-9][a-z0-9_]{5,56}$")
_SAFE_ROLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _module(name: str):
    return importlib.import_module(name)


def _async_url(name: str) -> str:
    value = os.environ[name]
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


def _synthetic_identity() -> str:
    first_seventeen = "110105" + "19881231" + "777"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    codes = "10X98765432"
    check = codes[
        sum(
            int(digit) * weight
            for digit, weight in zip(first_seventeen, weights, strict=True)
        )
        % 11
    ]
    return first_seventeen + check


def _parameterized_value(database, sql: str, *args):
    rows = database.fetch_rows(sql, *args)
    assert len(rows) == 1 and len(rows[0]) == 1
    return next(iter(rows[0].values()))


def _database_url_for_name(database_url: str, database_name: str) -> str:
    return make_url(database_url).set(database=database_name).render_as_string(
        hide_password=False
    )


def test_A2_2_R_EXPECTED_RED_Fresh闭环依赖五个生产模块() -> None:
    assert all(path.exists() for path in PRODUCTION_PATHS), (
        "A2_2_R_PRODUCTION_MODULES_MISSING"
    )


def test_A2_2_R_Repository以同一Writer事务调用0035与0036并可整体回滚(
    pg_database,
) -> None:
    ledger_module = _module("app.modules.auth.identity_remediation_ledger_repository")
    subject_module = _module("app.modules.auth.identity_remediation_subject_repository")
    batch_ref = UUID("00000000-0000-7036-8000-000000009001")
    engine = create_async_engine(
        _async_url("KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"),
        pool_pre_ping=True,
    )

    async def exercise() -> None:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                ledger = ledger_module.IdentityRemediationLedgerRepository(connection)
                subject = subject_module.IdentityRemediationSubjectRepository(connection)
                request = ledger_module.LedgerWriteRequest.plan_batch(
                    batch_ref=batch_ref,
                    classification_rule_hash=(
                        "9801dbb22c45cb679fdae43dd72dbf804"
                        "e7f5cb4b0095d28f9d572f3d6757bc5"
                    ),
                    snapshot_ref_hash="9" * 64,
                    class_counts={f"H{index}": 0 for index in range(8)},
                    actor_scope="synthetic-a2-r",
                    idempotency_key_digest="8" * 64,
                    receipt_id=UUID("00000000-0000-7036-8000-000000009002"),
                    audit_id=UUID("00000000-0000-7036-8000-000000009003"),
                    occurred_at=datetime(2026, 9, 3, tzinfo=UTC),
                )
                planned = await ledger.write(request)
                assert planned.state == "PLANNED"
                workset = await subject.fetch_workset(
                    batch_ref=batch_ref,
                    expected_batch_version=planned.version,
                    expected_batch_state_digest=planned.state_digest,
                    expected_classification_rule_hash=(
                        "9801dbb22c45cb679fdae43dd72dbf804"
                        "e7f5cb4b0095d28f9d572f3d6757bc5"
                    ),
                    expected_snapshot_ref_hash="9" * 64,
                )
                assert workset == ()
            finally:
                await transaction.rollback()
        await engine.dispose()

    asyncio.run(exercise())
    assert _parameterized_value(
        pg_database,
        "SELECT count(*) FROM identity.identity_remediation_batch WHERE batch_ref=$1",
        batch_ref,
    ) == 0


def test_A2_2_R_Confirmation三态Repository不获得账本底表权限(
    pg_database,
    a2_identity_remediation_confirmation_database,
) -> None:
    ledger_module = _module("app.modules.auth.identity_remediation_ledger_repository")
    role = os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"]
    for table_name in (
        "identity.identity_remediation_batch",
        "identity.identity_remediation_item",
        "identity.identity_remediation_audit",
        "identity.identity_remediation_receipt",
    ):
        assert _parameterized_value(
            pg_database,
            "SELECT has_table_privilege($1,to_regclass($2),'SELECT')",
            role,
            table_name,
        ) is False
    assert set(ledger_module.CommitOutcome) == {
        ledger_module.CommitOutcome.COMMITTED,
        ledger_module.CommitOutcome.NOT_COMMITTED,
        ledger_module.CommitOutcome.UNKNOWN,
    }
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        a2_identity_remediation_confirmation_database.fetch_rows(
            "SELECT * FROM identity.identity_remediation_batch"
        )


def test_A2_2_R_运行器只接受Fresh本地三身份且资源可关闭() -> None:
    runner = _module("app.composition.identity_remediation_runner")
    inspected = asyncio.run(runner.inspect_remediation_runtime())
    assert inspected == {
        "environment": "ephemeral_verified",
        "inventory_role": "verified",
        "writer_role": "verified",
        "confirmation_role": "verified",
        "database_sentinel": "verified",
        "direct_table_authority": "denied",
    }


def test_A2_2_R_顺序合同源码检查仅作为Fresh旅程补充证据() -> None:
    application = _module("app.modules.auth.identity_remediation_application")
    source = Path(application.__file__).read_text(encoding="utf-8")
    assert source.index("_register_workset(") < source.index('operation="START_BATCH"')
    loop_start = source.index("for subject, discovered in registrations")
    deferred_pause = source.index("if pause_required", loop_start)
    assert source.index('operation="PAUSE_BATCH"', deferred_pause) > deferred_pause
    assert source.index('operation="COMPLETE_BATCH"', deferred_pause) > deferred_pause


def test_A2_2_R_Fresh真实服务H4H5前置仍处理后部H3H0且最终仅Pause一次(
    pg_database,
    monkeypatch,
) -> None:
    runner = _module("app.composition.identity_remediation_runner")
    crypto_module = _module("app.modules.auth.identity_submission_crypto")
    crypto = crypto_module.IdentitySubmissionCrypto.from_environment()
    run_id = os.environ["KG_TEST_RUN_ID"]
    actor_scope = runner._actor_scope(run_id)
    tenant_ref = 8937001
    h4_user_ref = 8937101
    h5_user_ref = 8937102
    h3_user_ref = 8937103
    h0_user_ref = 8937104
    staff_user_ref = 8937105
    user_refs = (h4_user_ref, h5_user_ref, h3_user_ref, h0_user_ref, staff_user_ref)
    h4_member_ref = UUID("00000000-0000-7036-8000-000000093711")
    h5_member_ref = UUID("00000000-0000-7036-8000-000000093712")
    invitation_ref = UUID("00000000-0000-7036-8000-000000093713")
    enrollment_ref = UUID("00000000-0000-7036-8000-000000093714")
    decision_ref = UUID("00000000-0000-7036-8000-000000093715")
    submission_ref = UUID("00000000-0000-7036-8000-000000093716")
    identity_value = _synthetic_identity()
    synthetic_name = "合成甲"
    consent_version = "synthetic-a2-r-v1"
    name_aad = crypto.aad(
        submission_id=str(submission_ref),
        user_ref=h3_user_ref,
        version=1,
        field="real_name",
        key_id=crypto.key_id,
    )
    card_aad = crypto.aad(
        submission_id=str(submission_ref),
        user_ref=h3_user_ref,
        version=1,
        field="id_card",
        key_id=crypto.key_id,
    )
    encrypted_name = crypto.encrypt(synthetic_name, aad=name_aad)
    encrypted_card = crypto.encrypt(identity_value, aad=card_aad)
    content_digest = crypto.digest(
        "content", f"{synthetic_name}\x1f{identity_value}\x1f{consent_version}"
    )
    card_digest = crypto.digest("id-card", identity_value)

    task_database_name = f"kg_it_r_{run_id}"
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    if (
        not _SAFE_DATABASE_NAME.fullmatch(task_database_name)
        or len(task_database_name.encode("ascii")) > 63
        or not task_database_name.endswith(run_id)
        or not _SAFE_ROLE_NAME.fullmatch(migration_role)
    ):
        pytest.fail("A2_REMEDIATION_TASK_DATABASE_SCOPE_INVALID")
    admin_database_url = _database_url_for_name(
        os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], "postgres"
    )
    migration_database_url = _database_url_for_name(
        os.environ["KG_TEST_MIGRATION_DATABASE_URL"], task_database_name
    )
    task_database_url = _to_asyncpg_dsn(migration_database_url)
    for environment_name in (
        "KG_A2_IDENTITY_INVENTORY_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL",
    ):
        monkeypatch.setenv(
            environment_name,
            _database_url_for_name(os.environ[environment_name], task_database_name),
        )

    task_database_created = False

    async def prepare_task_database() -> None:
        nonlocal task_database_created
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_database_url))
        try:
            await admin.execute(
                f'CREATE DATABASE "{task_database_name}" OWNER "{migration_role}"'
            )
            task_database_created = True
            await admin.execute(
                f'COMMENT ON DATABASE "{task_database_name}" '
                f"IS 'kg-test-disposable:{run_id}'"
            )
        finally:
            await admin.close()
        task_admin = await asyncpg.connect(
            _to_asyncpg_dsn(
                _database_url_for_name(
                    os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database_name
                )
            )
        )
        try:
            await task_admin.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            await task_admin.execute(
                f'ALTER SCHEMA public OWNER TO "{migration_role}"'
            )
            await task_admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        finally:
            await task_admin.close()

    async def drop_task_database() -> None:
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_database_url))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                task_database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{task_database_name}"')
        finally:
            await admin.close()

    async def exercise() -> None:
        connection = await asyncpg.connect(task_database_url)
        batch_ref = None
        fixture_seeded = False
        try:
            fresh_baseline = await connection.fetchval(
                'SELECT (SELECT count(*) FROM public."user")=0 '
                "AND (SELECT count(*) FROM identity.identity_remediation_batch)=0"
            )
            if fresh_baseline is not True:
                pytest.fail("A2_REMEDIATION_FRESH_FIXTURE_NOT_ISOLATED")
            if await connection.fetchval(
                'SELECT count(*) FROM public."user" WHERE id=ANY($1::bigint[])',
                list(user_refs),
            ):
                pytest.fail("A2_REMEDIATION_SYNTHETIC_FIXTURE_COLLISION")
            algorithm_key = await connection.fetchval(
                "SELECT fingerprint_key_id "
                "FROM identity.identity_claim_algorithm_state WHERE singleton=1"
            )
            if algorithm_key != crypto.key_id:
                pytest.fail("A2_REMEDIATION_SYNTHETIC_KEY_CONTRACT_INVALID")

            async with connection.transaction():
                await connection.execute(
                    "INSERT INTO public.tenant("
                    "id,tenant_code,name,type,province,city,status,created_at,updated_at"
                    ") VALUES($1,$2,$3,'institution',$4,$5,'active',clock_timestamp(),clock_timestamp())",
                    tenant_ref,
                    "a2_r_service_tenant",
                    "Synthetic Tenant",
                    "Synthetic",
                    "Synthetic",
                )
                await connection.executemany(
                    'INSERT INTO public."user"('
                    "id,phone,password_hash,real_name,id_card,role,tenant_id,verify_status,status"
                    ") VALUES($1,$2,'synthetic',$3,$4,$5,$6,$7,'active')",
                    (
                        (h4_user_ref, "a2rsynt001", None, None, "member", tenant_ref, "unverified"),
                        (h5_user_ref, "a2rsynt002", None, None, "member", tenant_ref, "unverified"),
                        (h3_user_ref, "a2rsynt003", synthetic_name, identity_value, "member", None, "verified"),
                        (h0_user_ref, "a2rsynt004", None, None, "member", None, "unverified"),
                        (staff_user_ref, "a2rsynt005", "Synthetic", None, "org_admin", tenant_ref, "unverified"),
                    ),
                )
                await connection.executemany(
                    "INSERT INTO identity.member("
                    "member_id,member_no,creation_source,status,version,created_at,updated_at"
                    ") VALUES($1,$2,'synthetic','active',1,clock_timestamp(),clock_timestamp())",
                    ((h4_member_ref, "A2R-H4"), (h5_member_ref, "A2R-H5")),
                )
                await connection.executemany(
                    "INSERT INTO identity.user_member_self_link("
                    "link_id,user_ref,member_id,source,eligibility_decision_ref,"
                    "establishment_basis,establishment_record_ref,created_at"
                    ") VALUES($1,$2,$3,'REGISTRATION_VERIFIED',$4,"
                    "'REGISTRATION_VERIFIED_BOOTSTRAP',$5,clock_timestamp())",
                    (
                        (
                            UUID("00000000-0000-7036-8000-000000093721"),
                            h4_user_ref,
                            h4_member_ref,
                            UUID("00000000-0000-7036-8000-000000093722"),
                            UUID("00000000-0000-7036-8000-000000093723"),
                        ),
                        (
                            UUID("00000000-0000-7036-8000-000000093724"),
                            h5_user_ref,
                            h5_member_ref,
                            UUID("00000000-0000-7036-8000-000000093725"),
                            UUID("00000000-0000-7036-8000-000000093726"),
                        ),
                    ),
                )
                await connection.execute(
                    "INSERT INTO public.member_service_invitation("
                    "invitation_id,tenant_id,mode,phone_ciphertext,phone_key_id,"
                    "phone_digest,phone_digest_key_id,phone_masked,code_digest,code_key_id,"
                    "status,failed_attempts,expires_at,issued_by,issued_at,accepted_at,version"
                    ") VALUES($1,$2,'SELF',decode('01','hex'),'synthetic',repeat('1',64),"
                    "'synthetic','***',repeat('2',64),'synthetic','ACCEPTED',0,"
                    "clock_timestamp()+interval '1 day',$3,clock_timestamp(),clock_timestamp(),1)",
                    invitation_ref,
                    tenant_ref,
                    staff_user_ref,
                )
                await connection.execute(
                    "INSERT INTO public.service_enrollment("
                    "enrollment_id,invitation_id,tenant_id,subject_member_id,mode,status,"
                    "service_scope_tags,accepted_at,created_at,updated_at,version"
                    ") VALUES($1,$2,$3,$4,'SELF','ACCEPTED','[]'::jsonb,"
                    "clock_timestamp(),clock_timestamp(),clock_timestamp(),1)",
                    enrollment_ref,
                    invitation_ref,
                    tenant_ref,
                    h5_member_ref,
                )
                await connection.execute(
                    "INSERT INTO public.identity_verification_decision("
                    "decision_ref,user_ref,facts_version,verification_epoch,outcome,"
                    "evidence_digest,actor_type,actor_ref,decided_at"
                    ") VALUES($1,$2,1,1,'verified',repeat('a',64),'synthetic','synthetic',clock_timestamp())",
                    decision_ref,
                    h3_user_ref,
                )
                await connection.execute(
                    "INSERT INTO public.identity_verification_submission("
                    "submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
                    "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,"
                    "content_digest,id_card_digest,idempotency_key_digest,consent_version,"
                    "submitted_at,decided_at,reviewed_by,decision_basis_code,evidence_digest"
                    ") VALUES($1,$2,1,'verified',$3,$4,$5,$6,'110105********777X',$7,$8,$9,"
                    "repeat('d',64),$10,clock_timestamp(),clock_timestamp(),$11,"
                    "'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('a',64))",
                    submission_ref,
                    h3_user_ref,
                    encrypted_name.ciphertext,
                    encrypted_name.nonce,
                    encrypted_card.ciphertext,
                    encrypted_card.nonce,
                    crypto.key_id,
                    content_digest,
                    card_digest,
                    consent_version,
                    staff_user_ref,
                )
            fixture_seeded = True

            before_digests = await connection.fetchrow(
                "SELECT "
                "(SELECT md5((to_jsonb(u)-'real_name'-'id_card')::text) "
                "FROM public.\"user\" u WHERE id=$1) AS h3_stable,"
                "(SELECT md5(jsonb_agg(to_jsonb(u) ORDER BY id)::text) "
                "FROM public.\"user\" u WHERE id=ANY($2::bigint[])) AS other_users,"
                "(SELECT md5(to_jsonb(t)::text) FROM public.tenant t WHERE id=$3) AS tenant,"
                "(SELECT md5(jsonb_agg(to_jsonb(m) ORDER BY member_id)::text) "
                "FROM identity.member m WHERE member_id=ANY($4::uuid[])) AS members,"
                "(SELECT md5(jsonb_agg(to_jsonb(l) ORDER BY link_id)::text) "
                "FROM identity.user_member_self_link l WHERE user_ref=ANY($2::bigint[])) AS links,"
                "(SELECT md5(to_jsonb(i)::text) FROM public.member_service_invitation i "
                "WHERE invitation_id=$5) AS invitation,"
                "(SELECT md5(to_jsonb(e)::text) FROM public.service_enrollment e "
                "WHERE enrollment_id=$6) AS enrollment,"
                "(SELECT md5(to_jsonb(s)::text) FROM public.identity_verification_submission s "
                "WHERE submission_id=$7) AS submission,"
                "(SELECT md5(to_jsonb(d)::text) FROM public.identity_verification_decision d "
                "WHERE decision_ref=$8) AS decision,"
                "(SELECT md5(to_jsonb(c)::text) FROM identity.identity_subject_claim_registry c "
                "WHERE user_ref=$1) AS claim",
                h3_user_ref,
                [h4_user_ref, h5_user_ref, h0_user_ref, staff_user_ref],
                tenant_ref,
                [h4_member_ref, h5_member_ref],
                invitation_ref,
                enrollment_ref,
                submission_ref,
                decision_ref,
            )

            summary = await runner.run_identity_remediation()
            expected_status_counts = {
                "H0:EXCLUDED": 1,
                "H3:REMEDIATED": 1,
                "H4:REMEDIATION_REQUIRED": 1,
                "H5:REMEDIATION_REQUIRED": 1,
                "H7:EXCLUDED": 1,
            }
            if (
                summary.status != "PAUSED"
                or summary.processed_count != 5
                or summary.mutation_count != 1
                or summary.class_status_counts != expected_status_counts
            ):
                pytest.fail("A2_REMEDIATION_FRESH_SUMMARY_INVALID")
            public_summary = summary.to_public_dict()
            if (
                public_summary["processed_count"] != 5
                or public_summary["mutation_count"] != "SMALL_COUNT"
                or set(public_summary["class_status_counts"].values())
                != {"SMALL_COUNT"}
            ):
                pytest.fail("A2_REMEDIATION_PUBLIC_COUNT_SUPPRESSION_INVALID")
            public_text = repr(public_summary).lower()
            for forbidden in (
                str(h4_user_ref),
                str(h5_user_ref),
                str(h3_user_ref),
                str(h0_user_ref),
                str(staff_user_ref),
                synthetic_name.lower(),
                identity_value.lower(),
                "ciphertext",
                "database_url",
                "credential",
            ):
                if forbidden in public_text:
                    pytest.fail("A2_REMEDIATION_PUBLIC_OUTPUT_UNSAFE")

            batch_ref = await connection.fetchval(
                "SELECT batch_ref FROM identity.identity_remediation_batch "
                "WHERE initiated_by_public_ref=$1 ORDER BY created_at DESC LIMIT 1",
                actor_scope,
            )
            if batch_ref is None:
                pytest.fail("A2_REMEDIATION_BATCH_MISSING")
            status_matrix_ok = await connection.fetchval(
                "SELECT count(*)=5 FROM identity.identity_remediation_item WHERE batch_ref=$1 "
                "AND ((subject_user_ref=$2 AND primary_class='H4' AND status='REMEDIATION_REQUIRED') "
                "OR (subject_user_ref=$3 AND primary_class='H5' AND status='REMEDIATION_REQUIRED') "
                "OR (subject_user_ref=$4 AND primary_class='H3' AND status='REMEDIATED') "
                "OR (subject_user_ref=$5 AND primary_class='H0' AND status='EXCLUDED') "
                "OR (subject_user_ref=$6 AND primary_class='H7' AND status='EXCLUDED'))",
                batch_ref,
                h4_user_ref,
                h5_user_ref,
                h3_user_ref,
                h0_user_ref,
                staff_user_ref,
            )
            if status_matrix_ok is not True:
                pytest.fail("A2_REMEDIATION_FRESH_STATUS_MATRIX_INVALID")
            h3_mutation_ok = await connection.fetchval(
                'SELECT real_name IS NULL AND id_card IS NULL FROM public."user" WHERE id=$1',
                h3_user_ref,
            )
            tenant_unchanged = await connection.fetchval(
                'SELECT count(*)=2 FROM public."user" WHERE id=ANY($1::bigint[]) AND tenant_id=$2',
                [h4_user_ref, h5_user_ref],
                tenant_ref,
            )
            formal_chain_unchanged = await connection.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.identity_verification_submission "
                "WHERE submission_id=$1 AND status='verified') AND EXISTS("
                "SELECT 1 FROM public.identity_verification_decision WHERE decision_ref=$2) "
                "AND EXISTS(SELECT 1 FROM identity.identity_subject_claim_registry "
                "WHERE user_ref=$3 AND source_kind='P1')",
                submission_ref,
                decision_ref,
                h3_user_ref,
            )
            if not (h3_mutation_ok and tenant_unchanged and formal_chain_unchanged):
                pytest.fail("A2_REMEDIATION_FRESH_MUTATION_BOUNDARY_INVALID")

            after_digests = await connection.fetchrow(
                "SELECT "
                "(SELECT md5((to_jsonb(u)-'real_name'-'id_card')::text) "
                "FROM public.\"user\" u WHERE id=$1) AS h3_stable,"
                "(SELECT md5(jsonb_agg(to_jsonb(u) ORDER BY id)::text) "
                "FROM public.\"user\" u WHERE id=ANY($2::bigint[])) AS other_users,"
                "(SELECT md5(to_jsonb(t)::text) FROM public.tenant t WHERE id=$3) AS tenant,"
                "(SELECT md5(jsonb_agg(to_jsonb(m) ORDER BY member_id)::text) "
                "FROM identity.member m WHERE member_id=ANY($4::uuid[])) AS members,"
                "(SELECT md5(jsonb_agg(to_jsonb(l) ORDER BY link_id)::text) "
                "FROM identity.user_member_self_link l WHERE user_ref=ANY($2::bigint[])) AS links,"
                "(SELECT md5(to_jsonb(i)::text) FROM public.member_service_invitation i "
                "WHERE invitation_id=$5) AS invitation,"
                "(SELECT md5(to_jsonb(e)::text) FROM public.service_enrollment e "
                "WHERE enrollment_id=$6) AS enrollment,"
                "(SELECT md5(to_jsonb(s)::text) FROM public.identity_verification_submission s "
                "WHERE submission_id=$7) AS submission,"
                "(SELECT md5(to_jsonb(d)::text) FROM public.identity_verification_decision d "
                "WHERE decision_ref=$8) AS decision,"
                "(SELECT md5(to_jsonb(c)::text) FROM identity.identity_subject_claim_registry c "
                "WHERE user_ref=$1) AS claim",
                h3_user_ref,
                [h4_user_ref, h5_user_ref, h0_user_ref, staff_user_ref],
                tenant_ref,
                [h4_member_ref, h5_member_ref],
                invitation_ref,
                enrollment_ref,
                submission_ref,
                decision_ref,
            )
            if tuple(before_digests.values()) != tuple(after_digests.values()):
                pytest.fail("A2_REMEDIATION_NON_TARGET_POSTIMAGE_CHANGED")
            downstream_side_effects = await connection.fetchval(
                "SELECT (SELECT count(*) FROM public.consent_record "
                "WHERE subject_member_id=ANY($1::uuid[])) "
                "+ (SELECT count(*) FROM public.primary_therapist_assignment "
                "WHERE subject_member_id=ANY($1::uuid[])) "
                "+ (SELECT count(*) FROM public.service_case "
                "WHERE subject_member_id=ANY($1::uuid[]))",
                [h4_member_ref, h5_member_ref],
            )
            if downstream_side_effects != 0:
                pytest.fail("A2_REMEDIATION_DOWNSTREAM_SIDE_EFFECT_FORBIDDEN")

            sequence_ok = await connection.fetchval(
                "WITH points AS ("
                "SELECT max(occurred_at) FILTER (WHERE action='REGISTER_ITEM') AS last_register,"
                "min(occurred_at) FILTER (WHERE action='START_BATCH') AS started,"
                "max(occurred_at) FILTER (WHERE target_type='ITEM') AS last_item,"
                "min(occurred_at) FILTER (WHERE action='PAUSE_BATCH') AS paused,"
                "count(*) FILTER (WHERE action='PAUSE_BATCH') AS pause_count "
                "FROM identity.identity_remediation_audit WHERE batch_ref=$1) "
                "SELECT last_register <= started AND last_item <= paused AND pause_count=1 FROM points",
                batch_ref,
            )
            manifest_ok = await connection.fetchval(
                "SELECT count(item.item_ref)=batch.input_count "
                "FROM identity.identity_remediation_batch batch "
                "JOIN identity.identity_remediation_item item ON item.batch_ref=batch.batch_ref "
                "WHERE batch.batch_ref=$1 GROUP BY batch.input_count",
                batch_ref,
            )
            if not (sequence_ok and manifest_ok):
                pytest.fail("A2_REMEDIATION_FRESH_SEQUENCE_INVALID")

            roles = (
                os.environ["KG_TEST_A2_IDENTITY_INVENTORY_ROLE"],
                os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE"],
                os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"],
            )
            direct_authority = await connection.fetchval(
                "SELECT bool_or(has_table_privilege(role_name,to_regclass('public.user'),'SELECT') "
                "OR has_table_privilege(role_name,to_regclass('public.user'),'UPDATE')) "
                "FROM unnest($1::text[]) AS role_name",
                list(roles),
            )
            if direct_authority is not False:
                pytest.fail("A2_REMEDIATION_DIRECT_AUTHORITY_FORBIDDEN")
        finally:
            active_error = sys.exc_info()[1]
            cleanup_error: BaseException | None = None
            try:
                task_batch_refs = await connection.fetch(
                    "SELECT batch_ref FROM identity.identity_remediation_batch "
                    "WHERE initiated_by_public_ref=$1",
                    actor_scope,
                )
                for task_batch in task_batch_refs:
                    task_batch_ref = task_batch["batch_ref"]
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_receipt WHERE target_ref=$1 "
                        "OR audit_id IN (SELECT audit_id FROM identity.identity_remediation_audit WHERE batch_ref=$1)",
                        task_batch_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_audit WHERE batch_ref=$1",
                        task_batch_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_item WHERE batch_ref=$1",
                        task_batch_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_batch WHERE batch_ref=$1",
                        task_batch_ref,
                    )
                if fixture_seeded:
                    await connection.execute(
                        "DELETE FROM identity.identity_subject_claim_registry WHERE user_ref=$1",
                        h3_user_ref,
                    )
                    await connection.execute(
                        "DELETE FROM public.identity_verification_submission WHERE submission_id=$1",
                        submission_ref,
                    )
                    await connection.execute(
                        "DELETE FROM public.identity_verification_decision WHERE decision_ref=$1",
                        decision_ref,
                    )
                    await connection.execute(
                        "DELETE FROM public.service_enrollment WHERE enrollment_id=$1",
                        enrollment_ref,
                    )
                    await connection.execute(
                        "DELETE FROM public.member_service_invitation WHERE invitation_id=$1",
                        invitation_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.user_member_self_link WHERE user_ref=ANY($1::bigint[])",
                        [h4_user_ref, h5_user_ref],
                    )
                    await connection.execute(
                        "DELETE FROM identity.member WHERE member_id=ANY($1::uuid[])",
                        [h4_member_ref, h5_member_ref],
                    )
                    await connection.execute(
                        'DELETE FROM public."user" WHERE id=ANY($1::bigint[])',
                        list(user_refs),
                    )
                    await connection.execute(
                        "DELETE FROM public.tenant WHERE id=$1", tenant_ref
                    )
            except BaseException as error:
                cleanup_error = error
            finally:
                try:
                    await connection.close()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            if cleanup_error is not None and active_error is None:
                raise cleanup_error

    primary_error: BaseException | None = None
    try:
        asyncio.run(prepare_task_database())
        command.upgrade(_build_alembic_config(migration_database_url), "head")
        asyncio.run(exercise())
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if task_database_created:
            try:
                asyncio.run(drop_task_database())
            except BaseException:
                if primary_error is None:
                    raise


def test_A2_2_R_Fresh无H4H5真实服务最终唯一Complete(
    pg_database,
    monkeypatch,
) -> None:
    runner = _module("app.composition.identity_remediation_runner")
    crypto_module = _module("app.modules.auth.identity_submission_crypto")
    crypto = crypto_module.IdentitySubmissionCrypto.from_environment()
    run_id = os.environ["KG_TEST_RUN_ID"]
    actor_scope = runner._actor_scope(run_id)
    h3_user_ref = 8937201
    h0_user_ref = 8937202
    staff_user_ref = 8937203
    user_refs = (h3_user_ref, h0_user_ref, staff_user_ref)
    decision_ref = UUID("00000000-0000-7036-8000-000000093731")
    submission_ref = UUID("00000000-0000-7036-8000-000000093732")
    identity_value = _synthetic_identity()
    synthetic_name = "合成乙"
    consent_version = "synthetic-a2-r-completed-v1"
    name_aad = crypto.aad(
        submission_id=str(submission_ref),
        user_ref=h3_user_ref,
        version=1,
        field="real_name",
        key_id=crypto.key_id,
    )
    card_aad = crypto.aad(
        submission_id=str(submission_ref),
        user_ref=h3_user_ref,
        version=1,
        field="id_card",
        key_id=crypto.key_id,
    )
    encrypted_name = crypto.encrypt(synthetic_name, aad=name_aad)
    encrypted_card = crypto.encrypt(identity_value, aad=card_aad)
    content_digest = crypto.digest(
        "content", f"{synthetic_name}\x1f{identity_value}\x1f{consent_version}"
    )
    card_digest = crypto.digest("id-card", identity_value)

    task_database_name = f"kg_it_c_{run_id}"
    migration_role = os.environ["KG_TEST_MIGRATION_ROLE"]
    if (
        not _SAFE_DATABASE_NAME.fullmatch(task_database_name)
        or len(task_database_name.encode("ascii")) > 63
        or not task_database_name.endswith(run_id)
        or not _SAFE_ROLE_NAME.fullmatch(migration_role)
    ):
        pytest.fail("A2_REMEDIATION_TASK_DATABASE_SCOPE_INVALID")
    admin_database_url = _database_url_for_name(
        os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], "postgres"
    )
    migration_database_url = _database_url_for_name(
        os.environ["KG_TEST_MIGRATION_DATABASE_URL"], task_database_name
    )
    task_database_url = _to_asyncpg_dsn(migration_database_url)
    for environment_name in (
        "KG_A2_IDENTITY_INVENTORY_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL",
    ):
        monkeypatch.setenv(
            environment_name,
            _database_url_for_name(os.environ[environment_name], task_database_name),
        )

    task_database_created = False

    async def prepare_task_database() -> None:
        nonlocal task_database_created
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_database_url))
        try:
            await admin.execute(
                f'CREATE DATABASE "{task_database_name}" OWNER "{migration_role}"'
            )
            task_database_created = True
            await admin.execute(
                f'COMMENT ON DATABASE "{task_database_name}" '
                f"IS 'kg-test-disposable:{run_id}'"
            )
        finally:
            await admin.close()
        task_admin = await asyncpg.connect(
            _to_asyncpg_dsn(
                _database_url_for_name(
                    os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"], task_database_name
                )
            )
        )
        try:
            await task_admin.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            await task_admin.execute(
                f'ALTER SCHEMA public OWNER TO "{migration_role}"'
            )
            await task_admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        finally:
            await task_admin.close()

    async def drop_task_database() -> None:
        admin = await asyncpg.connect(_to_asyncpg_dsn(admin_database_url))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=$1 AND pid<>pg_backend_pid()",
                task_database_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{task_database_name}"')
        finally:
            await admin.close()

    async def exercise() -> None:
        connection = await asyncpg.connect(task_database_url)
        fixture_seeded = False
        try:
            fresh_baseline = await connection.fetchval(
                'SELECT (SELECT count(*) FROM public."user")=0 '
                "AND (SELECT count(*) FROM identity.identity_remediation_batch)=0"
            )
            if fresh_baseline is not True:
                pytest.fail("A2_REMEDIATION_FRESH_FIXTURE_NOT_ISOLATED")
            algorithm_key = await connection.fetchval(
                "SELECT fingerprint_key_id "
                "FROM identity.identity_claim_algorithm_state WHERE singleton=1"
            )
            if algorithm_key != crypto.key_id:
                pytest.fail("A2_REMEDIATION_SYNTHETIC_KEY_CONTRACT_INVALID")

            async with connection.transaction():
                await connection.executemany(
                    'INSERT INTO public."user"('
                    "id,phone,password_hash,real_name,id_card,role,tenant_id,verify_status,status"
                    ") VALUES($1,$2,'synthetic',$3,$4,$5,NULL,$6,'active')",
                    (
                        (h3_user_ref, "a2rcomp001", synthetic_name, identity_value, "member", "verified"),
                        (h0_user_ref, "a2rcomp002", None, None, "member", "unverified"),
                        (staff_user_ref, "a2rcomp003", "Synthetic", None, "org_admin", "unverified"),
                    ),
                )
                await connection.execute(
                    "INSERT INTO public.identity_verification_decision("
                    "decision_ref,user_ref,facts_version,verification_epoch,outcome,"
                    "evidence_digest,actor_type,actor_ref,decided_at"
                    ") VALUES($1,$2,1,1,'verified',repeat('a',64),'synthetic','synthetic',clock_timestamp())",
                    decision_ref,
                    h3_user_ref,
                )
                await connection.execute(
                    "INSERT INTO public.identity_verification_submission("
                    "submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
                    "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,"
                    "content_digest,id_card_digest,idempotency_key_digest,consent_version,"
                    "submitted_at,decided_at,reviewed_by,decision_basis_code,evidence_digest"
                    ") VALUES($1,$2,1,'verified',$3,$4,$5,$6,'110105********777X',$7,$8,$9,"
                    "repeat('d',64),$10,clock_timestamp(),clock_timestamp(),$11,"
                    "'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('a',64))",
                    submission_ref,
                    h3_user_ref,
                    encrypted_name.ciphertext,
                    encrypted_name.nonce,
                    encrypted_card.ciphertext,
                    encrypted_card.nonce,
                    crypto.key_id,
                    content_digest,
                    card_digest,
                    consent_version,
                    staff_user_ref,
                )
            fixture_seeded = True

            before_digests = await connection.fetchrow(
                "SELECT "
                "(SELECT md5((to_jsonb(u)-'real_name'-'id_card')::text) "
                "FROM public.\"user\" u WHERE id=$1) AS h3_stable,"
                "(SELECT md5(jsonb_agg(to_jsonb(u) ORDER BY id)::text) "
                "FROM public.\"user\" u WHERE id=ANY($2::bigint[])) AS other_users,"
                "(SELECT md5(to_jsonb(s)::text) FROM public.identity_verification_submission s "
                "WHERE submission_id=$3) AS submission,"
                "(SELECT md5(to_jsonb(d)::text) FROM public.identity_verification_decision d "
                "WHERE decision_ref=$4) AS decision,"
                "(SELECT md5(to_jsonb(c)::text) FROM identity.identity_subject_claim_registry c "
                "WHERE user_ref=$1) AS claim,"
                "(SELECT count(*) FROM public.consent_record) AS consent_count,"
                "(SELECT count(*) FROM public.primary_therapist_assignment) AS assignment_count,"
                "(SELECT count(*) FROM public.service_case) AS case_count,"
                "(SELECT count(*) FROM public.service_enrollment) AS enrollment_count",
                h3_user_ref,
                [h0_user_ref, staff_user_ref],
                submission_ref,
                decision_ref,
            )

            summary = await runner.run_identity_remediation()
            if (
                summary.status != "COMPLETED"
                or summary.processed_count != 3
                or summary.mutation_count != 1
                or summary.class_status_counts
                != {
                    "H0:EXCLUDED": 1,
                    "H3:REMEDIATED": 1,
                    "H7:EXCLUDED": 1,
                }
            ):
                pytest.fail("A2_REMEDIATION_COMPLETED_SUMMARY_INVALID")
            public_summary = summary.to_public_dict()
            if (
                public_summary["processed_count"] != "SMALL_COUNT"
                or public_summary["mutation_count"] != "SMALL_COUNT"
                or set(public_summary["class_status_counts"].values())
                != {"SMALL_COUNT"}
            ):
                pytest.fail("A2_REMEDIATION_PUBLIC_COUNT_SUPPRESSION_INVALID")
            public_text = repr(public_summary).lower()
            for forbidden in (
                *(str(value) for value in user_refs),
                synthetic_name.lower(),
                identity_value.lower(),
                "ciphertext",
                "database_url",
                "credential",
            ):
                if forbidden in public_text:
                    pytest.fail("A2_REMEDIATION_PUBLIC_OUTPUT_UNSAFE")
            if re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", public_text):
                pytest.fail("A2_REMEDIATION_PUBLIC_OUTPUT_UNSAFE")

            batch_ref = await connection.fetchval(
                "SELECT batch_ref FROM identity.identity_remediation_batch "
                "WHERE initiated_by_public_ref=$1 ORDER BY created_at DESC LIMIT 1",
                actor_scope,
            )
            status_ok = await connection.fetchval(
                "SELECT batch.status='COMPLETED' "
                "AND count(item.item_ref)=3 "
                "AND bool_and((item.primary_class,item.status) IN "
                "(('H0','EXCLUDED'),('H3','REMEDIATED'),('H7','EXCLUDED'))) "
                "FROM identity.identity_remediation_batch batch "
                "JOIN identity.identity_remediation_item item ON item.batch_ref=batch.batch_ref "
                "WHERE batch.batch_ref=$1 GROUP BY batch.status",
                batch_ref,
            )
            audit_ok = await connection.fetchval(
                "SELECT count(*) FILTER (WHERE action='REGISTER_ITEM')=3 "
                "AND count(*) FILTER (WHERE action='START_BATCH')=1 "
                "AND count(*) FILTER (WHERE action='COMPLETE_BATCH')=1 "
                "AND count(*) FILTER (WHERE action='PAUSE_BATCH')=0 "
                "FROM identity.identity_remediation_audit WHERE batch_ref=$1",
                batch_ref,
            )
            h3_cleared = await connection.fetchval(
                'SELECT real_name IS NULL AND id_card IS NULL FROM public."user" WHERE id=$1',
                h3_user_ref,
            )
            after_digests = await connection.fetchrow(
                "SELECT "
                "(SELECT md5((to_jsonb(u)-'real_name'-'id_card')::text) "
                "FROM public.\"user\" u WHERE id=$1) AS h3_stable,"
                "(SELECT md5(jsonb_agg(to_jsonb(u) ORDER BY id)::text) "
                "FROM public.\"user\" u WHERE id=ANY($2::bigint[])) AS other_users,"
                "(SELECT md5(to_jsonb(s)::text) FROM public.identity_verification_submission s "
                "WHERE submission_id=$3) AS submission,"
                "(SELECT md5(to_jsonb(d)::text) FROM public.identity_verification_decision d "
                "WHERE decision_ref=$4) AS decision,"
                "(SELECT md5(to_jsonb(c)::text) FROM identity.identity_subject_claim_registry c "
                "WHERE user_ref=$1) AS claim,"
                "(SELECT count(*) FROM public.consent_record) AS consent_count,"
                "(SELECT count(*) FROM public.primary_therapist_assignment) AS assignment_count,"
                "(SELECT count(*) FROM public.service_case) AS case_count,"
                "(SELECT count(*) FROM public.service_enrollment) AS enrollment_count",
                h3_user_ref,
                [h0_user_ref, staff_user_ref],
                submission_ref,
                decision_ref,
            )
            if not (
                status_ok is True
                and audit_ok is True
                and h3_cleared is True
                and tuple(before_digests.values()) == tuple(after_digests.values())
            ):
                pytest.fail("A2_REMEDIATION_COMPLETED_POSTIMAGE_INVALID")

            roles = (
                os.environ["KG_TEST_A2_IDENTITY_INVENTORY_ROLE"],
                os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_WRITER_ROLE"],
                os.environ["KG_TEST_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"],
            )
            tables = tuple(f"{schema}.{table}" for schema, table in runner._BASE_TABLES)
            direct_authority = await connection.fetchval(
                "SELECT bool_or(has_table_privilege(role_name,relation.oid,privilege)) "
                "FROM unnest($1::text[]) role_name "
                "CROSS JOIN unnest($2::text[]) table_name "
                "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) privilege "
                "JOIN pg_catalog.pg_class relation ON relation.relname=split_part(table_name,'.',2) "
                "JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace "
                "AND namespace.nspname=split_part(table_name,'.',1)",
                list(roles),
                list(tables),
            )
            if direct_authority is not False:
                pytest.fail("A2_REMEDIATION_DIRECT_AUTHORITY_FORBIDDEN")
        finally:
            active_error = sys.exc_info()[1]
            cleanup_error: BaseException | None = None
            try:
                task_batch_refs = await connection.fetch(
                    "SELECT batch_ref FROM identity.identity_remediation_batch "
                    "WHERE initiated_by_public_ref=$1",
                    actor_scope,
                )
                for task_batch in task_batch_refs:
                    task_batch_ref = task_batch["batch_ref"]
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_receipt WHERE target_ref=$1 "
                        "OR audit_id IN (SELECT audit_id FROM identity.identity_remediation_audit WHERE batch_ref=$1)",
                        task_batch_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_audit WHERE batch_ref=$1",
                        task_batch_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_item WHERE batch_ref=$1",
                        task_batch_ref,
                    )
                    await connection.execute(
                        "DELETE FROM identity.identity_remediation_batch WHERE batch_ref=$1",
                        task_batch_ref,
                    )
                if fixture_seeded:
                    await connection.execute(
                        "DELETE FROM identity.identity_subject_claim_registry WHERE user_ref=$1",
                        h3_user_ref,
                    )
                    await connection.execute(
                        "DELETE FROM public.identity_verification_submission WHERE submission_id=$1",
                        submission_ref,
                    )
                    await connection.execute(
                        "DELETE FROM public.identity_verification_decision WHERE decision_ref=$1",
                        decision_ref,
                    )
                    await connection.execute(
                        'DELETE FROM public."user" WHERE id=ANY($1::bigint[])',
                        list(user_refs),
                    )
            except BaseException as error:
                cleanup_error = error
            finally:
                try:
                    await connection.close()
                except BaseException as error:
                    cleanup_error = cleanup_error or error
            if cleanup_error is not None and active_error is None:
                raise cleanup_error

    primary_error: BaseException | None = None
    try:
        asyncio.run(prepare_task_database())
        command.upgrade(_build_alembic_config(migration_database_url), "head")
        asyncio.run(exercise())
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if task_database_created:
            try:
                asyncio.run(drop_task_database())
            except BaseException:
                if primary_error is None:
                    raise
