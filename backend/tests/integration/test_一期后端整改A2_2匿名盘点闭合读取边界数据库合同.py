import os
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config

pytestmark = pytest.mark.integration

FUNCTION = "identity.a2_identity_inventory_snapshot_v1()"
CURRENT_ENROLLMENT_STATES = (
    "ACCEPTED",
    "IDENTITY_SUBMITTED",
    "INSTITUTION_CHECKED",
    "PLATFORM_REVIEWING",
    "NEEDS_CORRECTION",
    "RESUBMITTED",
    "IDENTITY_VERIFIED",
    "CONSENT_PENDING",
    "THERAPIST_PENDING",
    "CASE_CREATED",
)
BASE_TABLES = (
    'public."user"',
    "public.identity_verification_submission",
    "public.identity_verification_decision",
    "identity.identity_subject_claim_registry",
    "identity.user_member_self_link",
    "public.service_enrollment",
)
OUTPUT_FIELDS = {
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
}


def _snapshot_mutation_counts(pg_database) -> dict[str, int]:
    return {
        relation: pg_database.fetch_value(f"SELECT count(*) FROM {relation}")
        for relation in BASE_TABLES
    }


def _insert_synthetic_facts(pg_database) -> int:
    staff_id = 9913401
    unknown_id = 9913402
    incomplete_id = 9913403
    complete_id = 9913404
    decision_id = uuid4()
    submission_id = uuid4()
    incomplete_submission_id = uuid4()
    fingerprint = "f" * 64
    key_id = os.environ["KG_IDENTITY_PII_KEY_ID"].replace("'", "''")
    pg_database.execute(
        'INSERT INTO public."user" '
        "(id,phone,password_hash,role,status,verify_status,real_name,id_card,created_at,updated_at) VALUES "
        f"({staff_id},'13'||'1'||repeat('0',8),'synthetic','org_admin','active',NULL,NULL,NULL,now(),now()),"
        f"({unknown_id},'13'||'2'||repeat('0',8),'synthetic','member','active',NULL,NULL,NULL,now(),now()),"
        f"({incomplete_id},'13'||'3'||repeat('0',8),'synthetic','member','active','pending',NULL,NULL,now(),now()),"
        f"({complete_id},'13'||'4'||repeat('0',8),'synthetic','member','active','verified','synthetic','marker',now(),now());"
        "INSERT INTO public.identity_verification_submission("
        "submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
        "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,content_digest,"
        "id_card_digest,idempotency_key_digest,consent_version,submitted_at) VALUES ("
        f"'{incomplete_submission_id}',{incomplete_id},1,'submitted',decode('01','hex'),"
        "decode('000000000000000000000001','hex'),decode('02','hex'),"
        f"decode('000000000000000000000002','hex'),'synthetic-mask','{key_id}',"
        "repeat('a',64),repeat('b',64),repeat('c',64),'identity-consent-v1',now());"
        "INSERT INTO public.identity_verification_decision("
        "decision_ref,user_ref,facts_version,verification_epoch,outcome,evidence_digest,"
        "actor_type,actor_ref,decided_at) VALUES ("
        f"'{decision_id}',{complete_id},1,1,'verified',repeat('d',64),"
        "'trusted_provider','synthetic',now());"
        "INSERT INTO public.identity_verification_submission("
        "submission_id,user_ref,version,status,real_name_ciphertext,real_name_nonce,"
        "id_card_ciphertext,id_card_nonce,id_card_masked,encryption_key_id,content_digest,"
        "id_card_digest,idempotency_key_digest,consent_version,submitted_at,decided_at,"
        "reviewed_by,decision_basis_code,evidence_digest) VALUES ("
        f"'{submission_id}',{complete_id},1,'verified',decode('03','hex'),"
        "decode('000000000000000000000003','hex'),decode('04','hex'),"
        f"decode('000000000000000000000004','hex'),'synthetic-mask','{key_id}',"
        f"repeat('e',64),'{fingerprint}',repeat('1',64),'identity-consent-v1',now(),now(),"
        f"{staff_id},'APPROVED_OFFLINE_IDENTITY_CHECK',repeat('2',64))"
    )
    return 4


def test_A2_2_P专用角色只可读取闭合匿名快照(
    pg_database,
    a2_identity_inventory_database,
):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260906_0039"
    )
    initial_user_count = pg_database.fetch_value('SELECT count(*) FROM public."user"')
    inserted_rows = _insert_synthetic_facts(pg_database)
    before = _snapshot_mutation_counts(pg_database)
    rows = a2_identity_inventory_database.fetch_rows(f"SELECT * FROM {FUNCTION}")
    after = _snapshot_mutation_counts(pg_database)

    assert len(rows) == initial_user_count + inserted_rows
    assert before == after
    assert all(set(row) == OUTPUT_FIELDS for row in rows)
    assert any(row["formal_chain_complete"] is True for row in rows)
    assert any(
        row["identity_authority_signal"] is True
        and row["formal_chain_complete"] is False
        for row in rows
    )
    assert any(row["identity_authority_signal"] is None for row in rows)
    assert all(not isinstance(value, (bytes, bytearray)) for row in rows for value in row.values())

    expected_role = os.environ["KG_TEST_A2_IDENTITY_INVENTORY_ROLE"]
    assert a2_identity_inventory_database.fetch_value("SELECT current_user") == expected_role
    attributes = a2_identity_inventory_database.fetch_rows(
        "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,"
        "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user"
    )[0]
    assert attributes == {
        "rolcanlogin": True,
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolinherit": False,
        "rolreplication": False,
        "rolbypassrls": False,
    }
    assert a2_identity_inventory_database.fetch_value(
        "SELECT EXISTS(SELECT 1 FROM pg_auth_members m "
        "JOIN pg_roles a ON a.oid=m.member JOIN pg_roles b ON b.oid=m.roleid "
        "WHERE a.rolname=current_user OR b.rolname=current_user)"
    ) is False

    for relation in BASE_TABLES:
        assert pg_database.fetch_value(
            f"SELECT has_table_privilege('{expected_role}','{relation}','SELECT')"
        ) is False
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            a2_identity_inventory_database.fetch_value(f"SELECT count(*) FROM {relation}")
    for schema in ("public", "identity"):
        assert pg_database.fetch_value(
            f"SELECT has_schema_privilege('{expected_role}','{schema}','CREATE')"
        ) is False
    assert pg_database.fetch_value(
        f"SELECT has_schema_privilege('{expected_role}','public','USAGE')"
    ) is False
    assert pg_database.fetch_value(
        f"SELECT has_schema_privilege('{expected_role}','identity','USAGE')"
    ) is True


def test_A2_2_P无关角色与角色伪装均fail_closed(
    pg_database,
    application_database,
    readonly_database,
    member_enrollment_writer_database,
    member_workflow_worker_database,
    slice7_oversight_reader_database,
):
    databases = (
        application_database,
        readonly_database,
        member_enrollment_writer_database,
        member_workflow_worker_database,
        slice7_oversight_reader_database,
    )
    assert pg_database.fetch_value(
        "SELECT NOT EXISTS("
        "SELECT 1 FROM pg_proc p, "
        "LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl "
        f"WHERE p.oid=to_regprocedure('{FUNCTION}') "
        "AND acl.grantee=0 AND acl.privilege_type='EXECUTE')"
    ) is True
    for database in databases:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            database.fetch_rows(f"SELECT * FROM {FUNCTION}")
    inventory_role = os.environ["KG_TEST_A2_IDENTITY_INVENTORY_ROLE"]
    for database in databases:
        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.InvalidAuthorizationSpecificationError)):
            database.execute(f'SET ROLE "{inventory_role}"')


def test_A2_2_P_upgrade_downgrade_reupgrade撤销并恢复最小权限(
    pg_database,
    a2_identity_inventory_database,
):
    config = _build_alembic_config(os.environ["KG_TEST_MIGRATION_DATABASE_URL"])
    try:
        command.downgrade(config, "20260830_0033")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
            "20260830_0033"
        )
        assert pg_database.fetch_value(
            f"SELECT to_regprocedure('{FUNCTION}') IS NULL"
        ) is True
        with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.UndefinedFunctionError)):
            a2_identity_inventory_database.fetch_rows(f"SELECT * FROM {FUNCTION}")
    finally:
        command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == (
        "20260906_0039"
    )
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{FUNCTION}') IS NOT NULL"
    ) is True
    assert a2_identity_inventory_database.fetch_value(
        f"SELECT count(*) >= 0 FROM {FUNCTION}"
    ) is True
