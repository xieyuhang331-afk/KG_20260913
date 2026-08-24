from __future__ import annotations

import asyncio
import importlib
import json
import os

import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url


pytestmark = pytest.mark.integration


FUNCTION_GRANTS = {
    "KG_TEST_SLICE5_ASSESSMENT_WRITER_ROLE": {
        "public.slice5_assessment_start_authority_v1(uuid,bigint)",
        "public.slice5_assessment_start_replay_v1(bigint,character varying,bytea)",
        "public.slice5_assessment_snapshot_write_v1(jsonb)",
        "public.slice5_assessment_confirm_v1(uuid)",
        "public.slice5_assessment_dispute_v1(jsonb)",
        "public.slice5_ordinary_plan_authority_v1(uuid)",
    },
    "KG_TEST_SLICE5_RISK_WORKFLOW_WRITER_ROLE": {
        "public.slice5_high_risk_transition_v1(jsonb)",
        "public.slice5_ordinary_plan_authority_v1(uuid)",
    },
    "KG_TEST_SLICE5_RULE_GOVERNANCE_WRITER_ROLE": {
        "public.slice5_rule_governance_v1(character varying,jsonb)",
    },
    "KG_TEST_SLICE5_WORKFLOW_WORKER_ROLE": {
        "public.slice5_assessment_worker_v1(character varying,jsonb)",
        "public.slice5_assessment_input_v1(uuid)",
        "public.slice5_assessment_complete_v1(jsonb)",
        "public.slice5_assessment_confirm_v1(uuid)",
        "public.slice5_ordinary_plan_authority_v1(uuid)",
        "public.slice5_outbox_claim_v1(uuid,bigint)",
        "public.slice5_outbox_consume_v1(jsonb)",
        "public.slice5_outbox_recover_v1(timestamp with time zone)",
        "public.slice5_outbox_reopen_v1(uuid,bigint)",
    },
    "KG_TEST_SLICE5_CLINICAL_READER_ROLE": {
        "public.slice5_actor_read_authority_v1(bigint,character varying,uuid,uuid,bigint)",
        "public.slice5_family_subject_authority_v1(bigint,uuid)",
    },
    "KG_TEST_SLICE5_OVERSIGHT_READER_ROLE": {
        "public.slice5_actor_read_authority_v1(bigint,character varying,uuid,uuid,bigint)",
    },
}

ALL_FUNCTIONS = set().union(*FUNCTION_GRANTS.values())
MODULE_TABLES = (
    "assessment_rule_set_version",
    "health_assessment",
    "assessment_input_snapshot",
    "assessment_module_result",
    "high_risk_task",
    "high_risk_task_action",
    "assessment_dispute",
    "slice5_idempotency",
    "slice5_audit",
    "slice5_outbox",
    "slice5_delivery",
)


def _uuid7(suffix: int) -> str:
    return f"018f62f6-52c7-7a51-8f75-{suffix:012x}"


def _governance_payload(index: int, *, actor: int, role: str, target: str, version: int | None = None, **extra):
    payload = {
        "rule_set_version_id": target,
        "rule_set_code": "CN_ADULT_BASELINE_V1",
        "actor_user_id": actor,
        "actor_role": role,
        "expected_version": version,
        "idempotency_key": f"slice5-rule-{index}",
        "request_digest": f"{index:02x}" * 32,
        "audit_id": _uuid7(100 + index),
        "event_id": _uuid7(200 + index),
        "receipt_id": _uuid7(300 + index),
        "evidence_digest": f"{index + 10:02x}" * 32,
        "outbox_digest": f"{index + 20:02x}" * 32,
        "postimage_digest": f"{index + 30:02x}" * 32,
        "digest_key_id": "slice5-test-k1",
        "response": {"rule_set_version_id": target, "status": "DRAFT", "version": 1},
        "created_at": "2026-08-23T08:00:00+00:00",
    }
    payload.update(extra)
    return payload


def _call_governance(database, operation: str, payload: dict):
    value = asyncio.run(
        database._fetch_value(
            "SELECT public.slice5_rule_governance_v1($1,$2::jsonb)",
            operation,
            json.dumps(payload, separators=(",", ":")),
        )
    )
    return json.loads(value) if isinstance(value, str) else value


def _call_json(database, sql: str, payload: dict):
    value = asyncio.run(
        database._fetch_value(sql, json.dumps(payload, separators=(",", ":")))
    )
    return json.loads(value) if isinstance(value, str) else value


def _task_action_payload(index: int, *, task_id: str, actor: int, version: int, action: str, status: str):
    return {
        "task_id": task_id,
        "actor_user_id": actor,
        "actor_role": "org_admin",
        "expected_version": version,
        "action_code": action,
        "contact_outcome_code": "CONTACTED" if action in {"REFER", "RESOLVE"} else None,
        "advice_code": "PROMPT_MEDICAL_CONTACT" if action in {"REFER", "RESOLVE"} else None,
        "reason_code": "CURRENT_REASSESSMENT_NON_HIGH_RISK" if action == "RESOLVE" else "CLAIMED_FOR_REVIEW",
        "occurred_at": f"2026-08-23T09:0{index}:00+00:00",
        "idempotency_key": f"slice5-task-{index}",
        "action_id": _uuid7(600 + index),
        "audit_id": _uuid7(610 + index),
        "event_id": _uuid7(620 + index),
        "receipt_id": _uuid7(630 + index),
        "request_digest": f"{90 + index:02x}" * 32,
        "evidence_digest": f"{100 + index:02x}" * 32,
        "outbox_digest": f"{110 + index:02x}" * 32,
        "postimage_digest": f"{120 + index:02x}" * 32,
        "digest_key_id": "slice5-test-k1",
        "response": {"task_id": task_id, "status": status, "version": version + 1},
    }


def test_PG01_PG11_0030单一Head六身份函数与基础表ACL精确闭合(pg_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"
    runtime_roles = {name: os.environ[name] for name in FUNCTION_GRANTS}
    assert len(set(runtime_roles.values())) == 6

    for variable, role in runtime_roles.items():
        assert pg_database.fetch_value(
            f"SELECT NOT rolsuper AND NOT rolinherit AND NOT rolcreaterole "
            f"AND NOT rolcreatedb AND NOT rolreplication AND NOT rolbypassrls "
            f"FROM pg_roles WHERE rolname='{role}'"
        )
        for signature in ALL_FUNCTIONS:
            expected = signature in FUNCTION_GRANTS[variable]
            assert pg_database.fetch_value(
                f"SELECT has_function_privilege('{role}','{signature}','EXECUTE')"
            ) is expected
        for table in MODULE_TABLES:
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','SELECT')"
            )
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','INSERT')"
            )
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','UPDATE')"
            )
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','DELETE')"
            )

    for signature in ALL_FUNCTIONS:
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('public','{signature}','EXECUTE')"
        )
    context_function = "public.slice5_measurement_context_projection_v1(uuid[])"
    health_builder = os.environ["KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE"]
    health_shadow = os.environ["KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE"]
    for role in (health_builder, health_shadow):
        assert pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{context_function}','EXECUTE')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_column_privilege('{role}','public.canonical_health_fact',"
            "'measurement_context','SELECT')"
        )
    for role in (*runtime_roles.values(), "public"):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{context_function}','EXECUTE')"
        )
    assembly_context_function = (
        "public.slice5_assembly_measurement_context_bind_v1(uuid,jsonb)"
    )
    readiness = os.environ["KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE"]
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{readiness}',"
        f"'{assembly_context_function}','EXECUTE')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_column_privilege('{readiness}',"
        "'public.assessment_input_assembly_fact','measurement_context','UPDATE')"
    )
    for role in (health_builder, health_shadow, *runtime_roles.values(), "public"):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}',"
            f"'{assembly_context_function}','EXECUTE')"
        )
    worker = runtime_roles["KG_TEST_SLICE5_WORKFLOW_WORKER_ROLE"]
    for table in ("slice5_outbox", "slice5_delivery"):
        columns = pg_database.fetch_column(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
            f"AND table_name='{table}'"
        )
        for column in columns:
            assert not pg_database.fetch_value(
                f"SELECT has_column_privilege('{worker}','public.{table}','{column}','SELECT')"
            )
            assert not pg_database.fetch_value(
                f"SELECT has_column_privilege('{worker}','public.{table}','{column}','INSERT')"
            )
            assert not pg_database.fetch_value(
                f"SELECT has_column_privilege('{worker}','public.{table}','{column}','UPDATE')"
            )


def test_PG05_PUBLIC及历史Runtime对Slice5受限函数全部拒绝(pg_database):
    unrelated = (
        os.environ["KG_TEST_APPLICATION_ROLE"],
        os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"],
        os.environ["KG_TEST_HEALTH_RECORD_WRITER_ROLE"],
        os.environ["KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE"],
    )
    for role in unrelated:
        for signature in ALL_FUNCTIONS:
            assert not pg_database.fetch_value(
                f"SELECT has_function_privilege('{role}','{signature}','EXECUTE')"
            )


def test_PG11_Reader只读取安全View且无规则payload或原始健康值(pg_database):
    clinical = os.environ["KG_TEST_SLICE5_CLINICAL_READER_ROLE"]
    oversight = os.environ["KG_TEST_SLICE5_OVERSIGHT_READER_ROLE"]
    for role in (clinical, oversight):
        assert pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.slice5_assessment_read_v1','SELECT')"
        )
        assert pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.slice5_high_risk_task_read_v1','SELECT')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.slice5_rule_set_governance_read_v1','SELECT')"
        )
    exposed = set(
        pg_database.fetch_column(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name IN ('slice5_assessment_read_v1','slice5_high_risk_task_read_v1')"
        )
    )
    assert not exposed.intersection(
        {"typed_rule_payload", "content_digest", "value_ciphertext", "profile_ciphertext"}
    )


def test_PG12_0028_0029_0030线性生命周期保持单一Head和权限对称(pg_database):
    config = _build_alembic_config(_get_test_database_url())
    hotfix_signature = (
        "public.slice3_case_enrollment_preimage_authority_v1(uuid,uuid,uuid,bigint)"
    )

    command.downgrade(config, "20260824_0029")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260824_0029"
    assert pg_database.fetch_value("SELECT to_regclass('public.health_assessment')") is None
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{hotfix_signature}') IS NOT NULL"
    )

    command.downgrade(config, "20260823_0028")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260823_0028"
    assert pg_database.fetch_value("SELECT to_regclass('public.health_assessment')") is None
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{hotfix_signature}') IS NULL"
    )
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.slice5_family_subject_authority_v1(bigint,uuid)')"
    ) is None
    assert pg_database.fetch_value(
        "SELECT to_regprocedure("
        "'public.slice4_subject_authority_v1(uuid,uuid,bigint,character varying)') IS NOT NULL"
    )

    command.upgrade(config, "20260824_0029")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260824_0029"
    assert pg_database.fetch_value(
        f"SELECT to_regprocedure('{hotfix_signature}') IS NOT NULL"
    )
    assert pg_database.fetch_value("SELECT to_regclass('public.health_assessment')") is None

    command.upgrade(config, "20260825_0030")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260825_0030"
    assert pg_database.fetch_value("SELECT to_regclass('public.health_assessment')") == "health_assessment"
    clinical_role = os.environ["KG_TEST_SLICE5_CLINICAL_READER_ROLE"]
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{clinical_role}',"
        "'public.slice5_family_subject_authority_v1(bigint,uuid)','EXECUTE')"
    )
    command.upgrade(config, "head")
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"


def test_PG13_0030非空降级在任何DDL前失败并保留Head(pg_database):
    target = _uuid7(399)
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,created_at,updated_at) "
        "VALUES (91999,'19900091999','synthetic','expert','active',now(),now());"
        "INSERT INTO public.assessment_rule_set_version(rule_set_version_id,rule_set_code,"
        "version_no,status,typed_rule_payload,content_digest,digest_key_id,author_user_id,"
        "approval_evidence_ref,created_at,version) VALUES ("
        f"'{target}','CN_ADULT_BASELINE_V1',91999,'DRAFT','{{}}'::jsonb,"
        "decode(repeat('9',32),'hex'),'slice5-test-k1',91999,'synthetic',now(),1)"
    )
    try:
        config = _build_alembic_config(_get_test_database_url())
        with pytest.raises(RuntimeError, match="Slice 5 downgrade requires empty module tables"):
            command.downgrade(config, "20260824_0029")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"
        assert pg_database.fetch_value("SELECT to_regclass('public.health_assessment')") == "health_assessment"
        assert pg_database.fetch_value(
            "SELECT to_regprocedure("
            "'public.slice5_family_subject_authority_v1(bigint,uuid)') IS NOT NULL"
        )
    finally:
        pg_database.execute(
            f"DELETE FROM public.assessment_rule_set_version WHERE rule_set_version_id='{target}';"
            'DELETE FROM public."user" WHERE id=91999'
        )
        command.upgrade(config, "head")
        assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260826_0031"


def test_D11_D12_规则治理作者审核人分离且mutation伴随事实完整(
    pg_database, slice5_rule_governance_writer_database
):
    target = _uuid7(400)
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,created_at,updated_at) VALUES "
        "(92001,'19900092001','synthetic','expert','active',now(),now()),"
        "(92002,'19900092002','synthetic','expert','active',now(),now()),"
        "(92003,'19900092003','synthetic','sys_admin','active',now(),now())"
    )
    try:
        create = _governance_payload(
            1,
            actor=92001,
            role="expert",
            target=target,
            version_no=1,
            typed_rule_payload={"included_rule_ids": [], "deferred_rule_ids": [], "golden_cases": []},
            content_digest="ab" * 32,
            approval_evidence_ref="synthetic-medical-double-sign",
        )
        assert _call_governance(slice5_rule_governance_writer_database, "CREATE", create)["status"] == "DRAFT"
        assert _call_governance(slice5_rule_governance_writer_database, "CREATE", create)["status"] == "DRAFT"

        submit = _governance_payload(2, actor=92001, role="expert", target=target, version=1)
        submit["response"] = {"rule_set_version_id": target, "status": "IN_REVIEW", "version": 2}
        assert _call_governance(slice5_rule_governance_writer_database, "SUBMIT", submit)["version"] == 2

        review = _governance_payload(3, actor=92002, role="expert", target=target, version=2)
        review["response"] = {"rule_set_version_id": target, "status": "IN_REVIEW", "version": 3}
        assert _call_governance(slice5_rule_governance_writer_database, "REVIEW_APPROVE", review)["version"] == 3

        publish = _governance_payload(4, actor=92003, role="sys_admin", target=target, version=3)
        publish.update(effective_from="2026-08-23T08:00:00+00:00")
        publish["response"] = {"rule_set_version_id": target, "status": "PUBLISHED", "version": 4}
        assert _call_governance(slice5_rule_governance_writer_database, "PUBLISH", publish)["status"] == "PUBLISHED"
        late_create_replay = _call_governance(
            slice5_rule_governance_writer_database, "CREATE", create
        )
        assert late_create_replay["status"] == "DRAFT"
        assert late_create_replay["version"] == 1
        assert pg_database.fetch_value(
            f"SELECT status='PUBLISHED' AND author_user_id=92001 AND reviewer_user_id=92002 "
            f"FROM public.assessment_rule_set_version WHERE rule_set_version_id='{target}'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice5_audit WHERE target_id='{target}'"
        ) == 4
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice5_outbox WHERE aggregate_ref='{target}'"
        ) == 4
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.slice5_idempotency WHERE target_id='{target}'"
        ) == 4
    finally:
        pg_database.execute(f"DELETE FROM public.slice5_idempotency WHERE target_id='{target}'")
        pg_database.execute(f"DELETE FROM public.slice5_outbox WHERE aggregate_ref='{target}'")
        pg_database.execute(f"DELETE FROM public.slice5_audit WHERE target_id='{target}'")
        pg_database.execute(f"DELETE FROM public.assessment_rule_set_version WHERE rule_set_version_id='{target}'")
        pg_database.execute("DELETE FROM public.\"user\" WHERE id IN (92001,92002,92003)")


def test_D10_D17_D19_D24_数据库计算最高风险并原子创建唯一任务(
    pg_database,
    slice5_assessment_writer_database,
    slice5_workflow_worker_database,
    slice5_risk_workflow_writer_database,
    real_db_client,
):
    seed_module = importlib.import_module(
        "tests.integration.test_一期切片4ProfileRoot、IdentitySummary与Runtime权限数据库合同"
    )
    tenant_id = 98550
    tenant_public_id = _uuid7(501)
    subject_member_id = _uuid7(502)
    enrollment_id, case_id, identity_revision_id, _ = asyncio.run(
        seed_module._seed_case(
            pg_database,
            ordinal=50,
            mode="SELF",
            tenant_id=tenant_id,
            tenant_public_id=__import__("uuid").UUID(tenant_public_id),
            actor_user_id=98551,
            actor_member_id=__import__("uuid").UUID(subject_member_id),
            subject_member_id=__import__("uuid").UUID(subject_member_id),
            proxy_member_id=None,
        )
    )
    case_id = str(case_id)
    enrollment_id = str(enrollment_id)
    identity_revision_id = str(identity_revision_id)
    assessment_id = _uuid7(510)
    snapshot_id = _uuid7(511)
    profile_revision_id = _uuid7(512)
    assembly_id = _uuid7(513)
    policy_id = _uuid7(514)
    rule_id = _uuid7(515)
    task_id = _uuid7(516)
    old_assembly_id = _uuid7(517)
    old_assessment_id = _uuid7(518)
    old_snapshot_id = _uuid7(519)
    old_dispute_id = _uuid7(520)
    therapist_revision_id = _uuid7(540)
    qualification_id = _uuid7(541)
    qualification_file_id = _uuid7(542)
    failed_assessment_id = _uuid7(550)
    failed_snapshot_id = _uuid7(551)
    therapist_id = pg_database.fetch_value(
        f"SELECT primary_therapist_id FROM public.service_case WHERE case_id='{case_id}'"
    )
    therapist_user_id = pg_database.fetch_value(
        f"SELECT user_id FROM public.therapist_profile WHERE therapist_id='{therapist_id}'"
    )
    assignment_id = pg_database.fetch_value(
        f"SELECT assignment_id FROM public.service_case WHERE case_id='{case_id}'"
    )
    verification_id = pg_database.fetch_value(
        f"SELECT identity_verification_id FROM public.service_case WHERE case_id='{case_id}'"
    )
    invitation_id = pg_database.fetch_value(
        f"SELECT invitation_id FROM public.service_enrollment WHERE enrollment_id='{enrollment_id}'"
    )
    therapist_invitation_id = pg_database.fetch_value(
        f"SELECT invitation_id FROM public.therapist_profile WHERE therapist_id='{therapist_id}'"
    )
    application_id = pg_database.fetch_value(
        f"SELECT application_id FROM public.institution_application WHERE tenant_internal_id={tenant_id}"
    )
    institution_invitation_id = pg_database.fetch_value(
        f"SELECT invitation_id FROM public.institution_application WHERE application_id='{application_id}'"
    )
    issuer_user_id = pg_database.fetch_value(
        f"SELECT applicant_user_id FROM public.institution_application WHERE application_id='{application_id}'"
    )
    reviewer_user_id = pg_database.fetch_value(
        f"SELECT reviewer_user_id FROM public.member_identity_review_decision WHERE verification_id='{verification_id}'"
    )
    try:
        pg_database.execute(
            "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
            "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
            "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) VALUES ("
            f"'{qualification_file_id}','THERAPIST_QUALIFICATION',{therapist_user_id},8,'application/pdf',repeat('a',64),"
            f"8,'application/pdf',repeat('a',64),'slice5-qualification-{qualification_file_id}','CLEAN',NULL,now(),"
            "now()+interval '365 days',now(),NULL,NULL);"
            "INSERT INTO public.therapist_profile_revision(revision_id,therapist_id,revision_no,profile_snapshot,input_digest,created_at) VALUES ("
            f"'{therapist_revision_id}','{therapist_id}',1,'{{}}'::jsonb,repeat('1',64),now());"
            "INSERT INTO public.therapist_qualification_version(qualification_version_id,therapist_id,profile_revision_id,previous_version_id,"
            "qualification_type,certificate_no_ciphertext,certificate_encryption_key_id,certificate_no_digest,certificate_digest_key_id,"
            "certificate_no_masked,issuer_name,valid_from,valid_until,attachment_count,version_no,created_at) VALUES ("
            f"'{qualification_id}','{therapist_id}','{therapist_revision_id}',NULL,'METABOLIC_HEALTH_PRACTICE',decode('00','hex'),'k1',"
            "repeat('2',64),'k1','*******0000','Slice5 Authority',current_date,current_date+365,1,1,now());"
            "INSERT INTO public.therapist_profile_revision_qualification(therapist_id,revision_id,qualification_version_id,position) VALUES ("
            f"'{therapist_id}','{therapist_revision_id}','{qualification_id}',1);"
            "INSERT INTO public.therapist_qualification_attachment(qualification_version_id,slot,private_file_id,created_at) VALUES ("
            f"'{qualification_id}',1,'{qualification_file_id}',now());"
            "UPDATE public.therapist_profile SET real_name_ciphertext=decode('00','hex'),real_name_encryption_key_id='k1',"
            "real_name_digest=repeat('3',64),real_name_digest_key_id='k1',display_name='Slice5 Therapist',practice_summary='Slice5',"
            "status='APPROVED_ACTIVE',service_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb,"
            f"current_qualification_version_id='{qualification_id}',current_revision_no=1,qualification_valid_until=current_date+365,"
            "submitted_at=now(),reviewed_at=now(),"
            "updated_at=now(),version=2 "
            f"WHERE therapist_id='{therapist_id}';"
            "INSERT INTO public.institution_service_readiness(tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
            "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
            f"{tenant_id},'SERVICE_READY','{{}}'::text[],1,now(),1,repeat('7',64),repeat('5',64),'{{}}'::jsonb,current_date+365,1);"
            "INSERT INTO public.health_profile_revision(profile_revision_id,subject_member_id,subject_user_id,revision_no,"
            "tenant_public_id,identity_source_kind,identity_revision_ref,identity_source_version,snapshot_ciphertext,"
            "snapshot_key_id,reconfirmed_at,source_type,changed_fields,actor_user_id,actor_type,snapshot_digest,digest_key_id,created_at) VALUES ("
            f"'{profile_revision_id}','{subject_member_id}',98551,1,'{tenant_public_id}','SLICE3','{identity_revision_id}',1,"
            "decode('00','hex'),'slice4-test-k1',now(),'APP','[]'::jsonb,98551,'SELF',decode(repeat('11',32),'hex'),'slice4-test-k1',now());"
            "INSERT INTO public.assessment_readiness_policy_version(policy_version_id,version_no,status,required_profile_sections,"
            "required_indicators,allowed_states,projection_version,rule_version,professionally_approved,approved_by,approved_at,"
            "effective_from,policy_digest,digest_key_id,created_at) VALUES ("
            f"'{policy_id}',98550,'PUBLISHED','[]'::jsonb,'[]'::jsonb,'[\"VERIFIED\"]'::jsonb,2,'slice4-test',true,98551,now(),"
            "now()-interval '1 day',decode(repeat('22',32),'hex'),'slice4-test-k1',now());"
            "INSERT INTO public.assessment_input_assembly(assembly_id,service_case_id,subject_member_id,tenant_id,primary_therapist_id,"
            "profile_revision_id,consent_version_ids,policy_version_id,projection_version,rule_version,required_max_fact_id,"
            "required_max_status_event_seq,source_snapshot,status,reason_codes,missing_codes,expired_codes,disputed_codes,"
            "source_vector_digest,source_digest,assembly_digest,digest_key_id,generated_at) VALUES ("
            f"'{old_assembly_id}','{case_id}','{subject_member_id}',{tenant_id},'{therapist_id}','{profile_revision_id}','[]'::jsonb,"
            f"'{policy_id}',2,'slice4-test',0,0,'synthetic-old','ASSESSMENT_READY','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
            "decode(repeat('31',32),'hex'),decode(repeat('41',32),'hex'),decode(repeat('51',32),'hex'),'slice4-test-k1',now());"
            "INSERT INTO public.assessment_input_assembly(assembly_id,service_case_id,subject_member_id,tenant_id,primary_therapist_id,"
            "profile_revision_id,consent_version_ids,policy_version_id,projection_version,rule_version,required_max_fact_id,"
            "required_max_status_event_seq,source_snapshot,status,reason_codes,missing_codes,expired_codes,disputed_codes,"
            "source_vector_digest,source_digest,assembly_digest,digest_key_id,generated_at) VALUES ("
            f"'{assembly_id}','{case_id}','{subject_member_id}',{tenant_id},'{therapist_id}','{profile_revision_id}','[]'::jsonb,"
            f"'{policy_id}',2,'slice4-test',0,0,'synthetic','ASSESSMENT_READY','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,"
            "decode(repeat('33',32),'hex'),decode(repeat('44',32),'hex'),decode(repeat('55',32),'hex'),'slice4-test-k1',now());"
            "INSERT INTO public.assessment_readiness_case_pointer(service_case_id,current_assembly_id,source_vector_digest,version,updated_at) VALUES ("
            f"'{case_id}','{assembly_id}',decode(repeat('33',32),'hex'),1,now());"
            "INSERT INTO public.assessment_rule_set_version(rule_set_version_id,rule_set_code,version_no,status,typed_rule_payload,"
            "content_digest,digest_key_id,author_user_id,reviewer_user_id,approval_evidence_ref,effective_from,created_at,version) VALUES ("
            f"'{rule_id}','CN_ADULT_BASELINE_V1',98550,'PUBLISHED','{{}}'::jsonb,decode(repeat('66',32),'hex'),'SHA256_V1',"
            "98551,98552,'synthetic-double-sign',now()-interval '1 day',now(),1);"
            "INSERT INTO public.health_assessment(assessment_id,service_case_id,subject_member_id,tenant_id,sequence_no,snapshot_id,"
            "rule_set_version_id,status,overall_risk,initiated_by,initiated_at,completed_at,version) VALUES ("
            f"'{old_assessment_id}','{case_id}','{subject_member_id}',{tenant_id},1,'{old_snapshot_id}','{rule_id}',"
            f"'UNDER_REVIEW','ATTENTION',{therapist_user_id},now()-interval '2 hours',now()-interval '90 minutes',2);"
            "INSERT INTO public.assessment_input_snapshot(snapshot_id,assessment_id,service_case_id,subject_member_id,tenant_id,assembly_id,"
            "assembly_digest,source_vector_digest,profile_revision_id,consent_version_ids,rule_set_version_id,projection_version,"
            "projection_rule_version,required_max_fact_id,required_max_status_event_seq,source_snapshot,fact_ref_manifest_digest,"
            "snapshot_digest,digest_key_id,created_at) VALUES ("
            f"'{old_snapshot_id}','{old_assessment_id}','{case_id}','{subject_member_id}',{tenant_id},'{old_assembly_id}',"
            f"decode(repeat('51',32),'hex'),decode(repeat('31',32),'hex'),'{profile_revision_id}','[]'::jsonb,'{rule_id}',2,"
            "'slice4-test',0,0,'synthetic-old',decode(repeat('71',32),'hex'),decode(repeat('81',32),'hex'),'slice5-test-k1',now());"
            "INSERT INTO public.assessment_dispute(dispute_id,assessment_id,raised_by,actor_context,reason_code,status,created_at,version) VALUES ("
            f"'{old_dispute_id}','{old_assessment_id}',98551,'SELF','DATA_DISPUTED','OPEN',now()-interval '1 hour',1);COMMIT;"
        )
        from app.core.security import create_access_token

        therapist_headers = {
            "Authorization": "Bearer "
            + create_access_token({"sub": str(therapist_user_id), "role": "therapist"}),
            "Idempotency-Key": "slice5-http-assessment-start-0001",
        }
        case_version = pg_database.fetch_value(
            f"SELECT version FROM public.service_case WHERE case_id='{case_id}'"
        )
        start_path = f"/api/v1/therapist/service-cases/{case_id}/assessments"
        unauthenticated = real_db_client.post(
            start_path, json={"expected_case_version": case_version}
        )
        assert unauthenticated.status_code == 401
        started = real_db_client.post(
            start_path,
            headers=therapist_headers,
            json={"expected_case_version": case_version},
        )
        assert started.status_code == 202, started.json()
        replayed = real_db_client.post(
            start_path,
            headers=therapist_headers,
            json={"expected_case_version": case_version},
        )
        assert replayed.status_code == 202
        assert replayed.json() == started.json()
        assessment_id = started.json()["assessment_id"]
        snapshot_id = started.json()["input_snapshot_ref"]
        draft_page = real_db_client.get(start_path, headers=therapist_headers)
        assert draft_page.status_code == 200
        assert any(item["assessment_id"] == assessment_id for item in draft_page.json()["items"])
        draft_detail = real_db_client.get(
            f"{start_path}/{assessment_id}", headers=therapist_headers
        )
        assert draft_detail.status_code == 200
        claimed = _call_json(
            slice5_workflow_worker_database,
            "SELECT public.slice5_assessment_worker_v1('CLAIM',$1::jsonb)",
            {"assessment_id": assessment_id, "lease_owner": _uuid7(509)},
        )
        assert claimed["version"] == 2
        authority = asyncio.run(
            slice5_assessment_writer_database._fetch_value(
                "SELECT public.slice5_assessment_start_authority_v1($1::uuid,$2::bigint)",
                case_id,
                therapist_user_id,
            )
        )
        authority = json.loads(authority) if isinstance(authority, str) else authority
        assert authority["supersedes_assessment_id"] == old_assessment_id
        modules = [
            ("BLOOD_PRESSURE_CARDIOVASCULAR", "HIGH_RISK"),
            ("GLUCOSE_METABOLISM", "WITHIN_RANGE"),
            ("LIPID_METABOLISM", "NOT_ASSESSED"),
            ("WEIGHT_ABDOMINAL_OBESITY", "ATTENTION"),
        ]
        rows = [
            {
                "module_result_id": _uuid7(520 + index),
                "module_code": code,
                "risk_level": risk,
                "reason_codes": ["SYNTHETIC"],
                "evidence_manifest": [],
                "message_codes": [],
                "rule_fragment_digest": f"{70 + index:02x}" * 32,
            }
            for index, (code, risk) in enumerate(modules)
        ]
        complete = {
            "assessment_id": assessment_id,
            "service_case_id": case_id,
            "subject_member_id": subject_member_id,
            "tenant_id": tenant_id,
            "expected_version": 2,
            "overall_risk": "ATTENTION",
            "module_results": rows,
            "trigger_codes": ["HR-BP-SEVERE"],
            "task_id": task_id,
            "audit_id": _uuid7(530),
            "event_id": _uuid7(531),
            "receipt_id": _uuid7(532),
            "request_digest": "89" * 32,
            "audit_digest": "99" * 32,
            "outbox_digest": "aa" * 32,
            "postimage_digest": "bb" * 32,
            "digest_key_id": "slice5-test-k1",
            "completed_at": "2026-08-23T09:00:00+00:00",
            "supersedes_assessment_id": old_assessment_id,
            "superseded_assessment_version": 2,
            "superseded_dispute_id": old_dispute_id,
            "superseded_dispute_version": 1,
            "response": {
                "assessment_id": assessment_id,
                "status": "COMPLETED",
                "overall_risk": "HIGH_RISK",
                "version": 3,
            },
        }
        with pytest.raises(Exception, match="SLICE5_OVERALL_RISK_MISMATCH"):
            _call_json(
                slice5_workflow_worker_database,
                "SELECT public.slice5_assessment_complete_v1($1::jsonb)",
                complete,
            )
        assert pg_database.fetch_value(
            f"SELECT count(*) FROM public.assessment_module_result WHERE assessment_id='{assessment_id}'"
        ) == 0
        complete.pop("overall_risk")
        result = _call_json(
            slice5_workflow_worker_database,
            "SELECT public.slice5_assessment_complete_v1($1::jsonb)",
            complete,
        )
        assert result["overall_risk"] == "HIGH_RISK"
        assert pg_database.fetch_value(
            f"SELECT count(*)=4 FROM public.assessment_module_result WHERE assessment_id='{assessment_id}'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*)=1 FROM public.high_risk_task WHERE assessment_id='{assessment_id}' AND status='OPEN'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*)=1 FROM public.slice5_audit WHERE target_id='{assessment_id}' AND action='ASSESSMENT_COMPLETED'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*)=1 FROM public.slice5_outbox WHERE aggregate_ref='{assessment_id}' AND event_type='ASSESSMENT_COMPLETED'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*)=1 FROM public.slice5_idempotency WHERE target_id='{assessment_id}' AND operation='ASSESSMENT_COMPLETE'"
        )
        confirmed = asyncio.run(
            slice5_workflow_worker_database._fetch_value(
                "SELECT public.slice5_assessment_confirm_v1($1::uuid)", assessment_id
            )
        )
        confirmed = json.loads(confirmed) if isinstance(confirmed, str) else confirmed
        assert confirmed["assessment"]["overall_risk"] == "HIGH_RISK"
        assert len(confirmed["module_results"]) == 4
        assert confirmed["high_risk_task"]["status"] == "OPEN"
        assert confirmed["audit"]["evidence_digest"] == "99" * 32
        assert confirmed["outbox"]["payload_digest"] == "aa" * 32
        assert confirmed["receipt"]["postimage_digest"] == "bb" * 32
        assert confirmed["superseded_assessment"] == {
            "assessment_id": old_assessment_id,
            "status": "SUPERSEDED",
            "version": 3,
        }
        assert confirmed["superseded_dispute"]["status"] == "SUPERSEDED"
        assert confirmed["superseded_dispute"]["superseding_assessment_id"] == assessment_id
        platform_user_id = 98559
        pg_database.execute(
            "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,created_at,updated_at) VALUES "
            f"({platform_user_id},'19900098559','synthetic','sys_admin','active',now(),now())"
        )
        family_headers = {
            "Authorization": "Bearer "
            + create_access_token({"sub": "98551", "role": "member"})
        }
        institution_headers = {
            "Authorization": "Bearer "
            + create_access_token(
                {"sub": str(issuer_user_id), "role": "org_admin", "tenant_id": tenant_id}
            )
        }
        platform_headers = {
            "Authorization": "Bearer "
            + create_access_token({"sub": str(platform_user_id), "role": "sys_admin"})
        }
        for path, headers in (
            (start_path, therapist_headers),
            (f"/api/v1/institution/service-cases/{case_id}/assessments", institution_headers),
            ("/api/v1/family/assessments", family_headers),
            ("/api/v1/therapist/high-risk-tasks", therapist_headers),
            ("/api/v1/institution/high-risk-tasks", institution_headers),
            ("/api/v1/platform/high-risk-tasks", platform_headers),
        ):
            response = real_db_client.get(path, headers=headers)
            assert response.status_code == 200, (path, response.json())
            assert response.json()["items"]
        for path, headers in (
            (f"{start_path}/{assessment_id}", therapist_headers),
            (f"/api/v1/family/assessments/{assessment_id}", family_headers),
            (f"/api/v1/institution/high-risk-tasks/{task_id}", institution_headers),
            (f"/api/v1/platform/high-risk-tasks/{task_id}", platform_headers),
        ):
            response = real_db_client.get(path, headers=headers)
            assert response.status_code == 200, (path, response.json())
        forbidden = real_db_client.get(start_path, headers=family_headers)
        assert forbidden.status_code == 403
        assert forbidden.json()["code"] == "THERAPIST_SCOPE_FORBIDDEN"
        missing = real_db_client.get(
            f"{start_path}/{_uuid7(599)}", headers=therapist_headers
        )
        assert missing.status_code == 404
        malformed = real_db_client.get(f"{start_path}/not-a-uuid", headers=therapist_headers)
        assert malformed.status_code == 422
        claim = _task_action_payload(
            1, task_id=task_id, actor=issuer_user_id, version=1, action="CLAIM", status="CLAIMED"
        )
        claim_path = f"/api/v1/institution/high-risk-tasks/{task_id}/actions"
        claimed_http = real_db_client.post(
            claim_path,
            headers={**institution_headers, "Idempotency-Key": claim["idempotency_key"]},
            json={
                "expected_version": 1,
                "action_code": "CLAIM",
                "occurred_at": claim["occurred_at"],
            },
        )
        assert claimed_http.status_code == 200, claimed_http.json()
        claimed_replay = real_db_client.post(
            claim_path,
            headers={**institution_headers, "Idempotency-Key": claim["idempotency_key"]},
            json={
                "expected_version": 1,
                "action_code": "CLAIM",
                "occurred_at": claim["occurred_at"],
            },
        )
        assert claimed_replay.status_code == 200
        assert claimed_replay.json() == claimed_http.json()
        assert pg_database.fetch_value(
            f"SELECT count(*)=1 FROM public.high_risk_task_action WHERE task_id='{task_id}'"
        )
        assert pg_database.fetch_value(
            f"SELECT count(*)=1 FROM public.slice5_idempotency WHERE target_id='{task_id}' AND operation='HIGH_RISK_ACTION'"
        )
        resolve = _task_action_payload(
            2, task_id=task_id, actor=issuer_user_id, version=2, action="RESOLVE", status="RESOLVED"
        )
        assert _call_json(
            slice5_risk_workflow_writer_database,
            "SELECT public.slice5_high_risk_transition_v1($1::jsonb)",
            resolve,
        )["status"] == "RESOLVED"
        with pytest.raises(Exception, match="ordinary plan blocked"):
            asyncio.run(
                slice5_risk_workflow_writer_database._fetch_value(
                    "SELECT public.slice5_ordinary_plan_authority_v1($1::uuid)", case_id
                )
            )
        dispute_response = {
            "dispute_id": _uuid7(560),
            "assessment_id": assessment_id,
            "status": "OPEN",
            "reason_code": "DATA_DISPUTED",
            "created_at": "2026-08-23T09:30:00+00:00",
            "version": 1,
        }
        dispute_payload = {
            **dispute_response,
            "actor_user_id": therapist_user_id,
            "actor_role": "therapist",
            "actor_context": "THERAPIST",
            "expected_version": 3,
            "idempotency_key": "slice5-dispute-1",
            "request_digest": "d1" * 32,
            "evidence_digest": "d2" * 32,
            "outbox_digest": "d3" * 32,
            "postimage_digest": "d4" * 32,
            "digest_key_id": "slice5-test-k1",
            "audit_id": _uuid7(561),
            "event_id": _uuid7(562),
            "receipt_id": _uuid7(563),
            "response": dispute_response,
        }
        assert _call_json(
            slice5_assessment_writer_database,
            "SELECT public.slice5_assessment_dispute_v1($1::jsonb)",
            dispute_payload,
        ) == dispute_response
        assert _call_json(
            slice5_assessment_writer_database,
            "SELECT public.slice5_assessment_dispute_v1($1::jsonb)",
            dispute_payload,
        ) == dispute_response
        conflicting_dispute = dict(dispute_payload, request_digest="df" * 32)
        with pytest.raises(Exception, match="IDEMPOTENCY_CONFLICT"):
            _call_json(
                slice5_assessment_writer_database,
                "SELECT public.slice5_assessment_dispute_v1($1::jsonb)",
                conflicting_dispute,
            )
        dispute_confirmed = asyncio.run(
            slice5_assessment_writer_database._fetch_value(
                "SELECT public.slice5_assessment_confirm_v1($1::uuid)", assessment_id
            )
        )
        dispute_confirmed = json.loads(dispute_confirmed) if isinstance(dispute_confirmed, str) else dispute_confirmed
        assert dispute_confirmed["assessment"]["status"] == "UNDER_REVIEW"
        assert dispute_confirmed["dispute"]["dispute_id"] == dispute_response["dispute_id"]
        assert dispute_confirmed["outbox"]["payload_digest"] == "d3" * 32
        assert dispute_confirmed["receipt"]["postimage_digest"] == "d4" * 32
        pg_database.execute(
            "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
            "INSERT INTO public.health_assessment(assessment_id,service_case_id,subject_member_id,tenant_id,sequence_no,snapshot_id,"
            "rule_set_version_id,status,initiated_by,initiated_at,started_at,version) VALUES ("
            f"'{failed_assessment_id}','{case_id}','{subject_member_id}',{tenant_id},3,'{failed_snapshot_id}','{rule_id}',"
            f"'RUNNING',{therapist_user_id},'2026-08-23T10:00:00+00:00','2026-08-23T10:00:00+00:00',2);"
            "INSERT INTO public.assessment_input_snapshot(snapshot_id,assessment_id,service_case_id,subject_member_id,tenant_id,assembly_id,"
            "assembly_digest,source_vector_digest,profile_revision_id,consent_version_ids,rule_set_version_id,projection_version,"
            "projection_rule_version,required_max_fact_id,required_max_status_event_seq,source_snapshot,fact_ref_manifest_digest,"
            "snapshot_digest,digest_key_id,created_at) VALUES ("
            f"'{failed_snapshot_id}','{failed_assessment_id}','{case_id}','{subject_member_id}',{tenant_id},'{assembly_id}',"
            f"decode(repeat('55',32),'hex'),decode(repeat('33',32),'hex'),'{profile_revision_id}','[]'::jsonb,'{rule_id}',2,"
            "'slice4-test',0,0,'synthetic',decode(repeat('77',32),'hex'),decode(repeat('88',32),'hex'),'slice5-test-k1',"
            "'2026-08-23T10:00:00+00:00');COMMIT;"
        )
        failed_response = {
            "assessment_id": failed_assessment_id,
            "status": "FAILED",
            "failure_code": "RULE_EVALUATION_UNAVAILABLE",
            "version": 3,
        }
        failure_payload = {
            "assessment_id": failed_assessment_id,
            "expected_version": 2,
            "failure_code": "RULE_EVALUATION_UNAVAILABLE",
            "failed_at": "2026-08-23T10:00:00+00:00",
            "audit_id": _uuid7(552),
            "event_id": _uuid7(553),
            "receipt_id": _uuid7(554),
            "request_digest": "c1" * 32,
            "audit_digest": "c2" * 32,
            "outbox_digest": "c3" * 32,
            "postimage_digest": "c4" * 32,
            "digest_key_id": "slice5-test-k1",
            "response": failed_response,
        }
        assert _call_json(
            slice5_workflow_worker_database,
            "SELECT public.slice5_assessment_worker_v1('FAIL',$1::jsonb)",
            failure_payload,
        ) == failed_response
        assert _call_json(
            slice5_workflow_worker_database,
            "SELECT public.slice5_assessment_worker_v1('FAIL',$1::jsonb)",
            failure_payload,
        ) == failed_response
        failed_confirmed = asyncio.run(
            slice5_workflow_worker_database._fetch_value(
                "SELECT public.slice5_assessment_confirm_v1($1::uuid)", failed_assessment_id
            )
        )
        failed_confirmed = json.loads(failed_confirmed) if isinstance(failed_confirmed, str) else failed_confirmed
        assert failed_confirmed["assessment"]["failure_code"] == "RULE_EVALUATION_UNAVAILABLE"
        assert failed_confirmed["audit"]["action"] == "ASSESSMENT_FAILED"
        assert failed_confirmed["outbox"]["event_type"] == "ASSESSMENT_FAILED"
        assert failed_confirmed["receipt"]["postimage_digest"] == "c4" * 32
    finally:
        pg_database.execute(
            "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
            f"DELETE FROM public.slice5_delivery WHERE event_id IN (SELECT event_id FROM public.slice5_outbox WHERE aggregate_ref IN ('{assessment_id}','{task_id}','{failed_assessment_id}'));"
            f"DELETE FROM public.slice5_idempotency WHERE target_id IN ('{assessment_id}','{task_id}','{failed_assessment_id}');"
            f"DELETE FROM public.slice5_outbox WHERE aggregate_ref IN ('{assessment_id}','{task_id}','{failed_assessment_id}');"
            f"DELETE FROM public.slice5_audit WHERE target_id IN ('{assessment_id}','{task_id}','{failed_assessment_id}');"
            f"DELETE FROM public.high_risk_task_action WHERE task_id='{task_id}';"
            f"DELETE FROM public.high_risk_task WHERE assessment_id='{assessment_id}';"
            f"DELETE FROM public.assessment_module_result WHERE assessment_id='{assessment_id}';"
            f"DELETE FROM public.assessment_dispute WHERE assessment_id IN ('{assessment_id}','{old_assessment_id}');"
            f"DELETE FROM public.assessment_input_snapshot WHERE assessment_id IN ('{assessment_id}','{old_assessment_id}','{failed_assessment_id}');"
            f"UPDATE public.health_assessment SET supersedes_assessment_id=NULL WHERE assessment_id IN ('{assessment_id}','{old_assessment_id}','{failed_assessment_id}');"
            f"DELETE FROM public.health_assessment WHERE assessment_id IN ('{assessment_id}','{old_assessment_id}','{failed_assessment_id}');"
            f"DELETE FROM public.assessment_rule_set_version WHERE rule_set_version_id='{rule_id}';"
            f"DELETE FROM public.assessment_readiness_case_pointer WHERE service_case_id='{case_id}';"
            f"DELETE FROM public.assessment_input_assembly_fact WHERE assembly_id='{assembly_id}';"
            f"DELETE FROM public.assessment_input_assembly WHERE assembly_id IN ('{assembly_id}','{old_assembly_id}');"
            f"DELETE FROM public.assessment_readiness_policy_version WHERE policy_version_id='{policy_id}';"
            f"DELETE FROM public.health_profile_revision WHERE profile_revision_id='{profile_revision_id}';"
            "DELETE FROM public.\"user\" WHERE id=98559;"
            f"UPDATE public.service_enrollment SET service_case_id=NULL,current_assignment_id=NULL,current_identity_verification_id=NULL WHERE enrollment_id='{enrollment_id}';"
            f"UPDATE public.primary_therapist_assignment SET status='CANCELLED',reason_code='TEST_CLEANUP',service_case_id=NULL,decided_at=now() WHERE assignment_id='{assignment_id}';"
            f"DELETE FROM public.service_case WHERE case_id='{case_id}';"
            f"DELETE FROM public.primary_therapist_assignment WHERE assignment_id='{assignment_id}';"
            f"DELETE FROM identity.identity_subject_claim_registry WHERE member_id='{subject_member_id}';"
            f"UPDATE public.member_identity_verification SET current_revision_id=NULL,platform_decision_id=NULL,institution_decision_id=NULL WHERE verification_id='{verification_id}';"
            f"DELETE FROM public.member_identity_review_decision WHERE verification_id='{verification_id}';"
            f"DELETE FROM public.member_identity_revision WHERE verification_id='{verification_id}';"
            f"DELETE FROM public.member_identity_verification WHERE verification_id='{verification_id}';"
            f"DELETE FROM public.service_enrollment WHERE enrollment_id='{enrollment_id}';"
            f"DELETE FROM public.member_service_invitation WHERE invitation_id='{invitation_id}';"
            f"DELETE FROM public.institution_service_readiness WHERE tenant_id={tenant_id};"
            f"UPDATE public.therapist_profile SET status='DRAFT',current_qualification_version_id=NULL,current_revision_no=0,"
            f"qualification_valid_until=NULL,submitted_at=NULL,reviewed_at=NULL WHERE therapist_id='{therapist_id}';"
            f"DELETE FROM public.therapist_qualification_attachment WHERE qualification_version_id='{qualification_id}';"
            f"DELETE FROM public.therapist_profile_revision_qualification WHERE revision_id='{therapist_revision_id}';"
            f"DELETE FROM public.therapist_qualification_version WHERE qualification_version_id='{qualification_id}';"
            f"DELETE FROM public.therapist_profile_revision WHERE revision_id='{therapist_revision_id}';"
            f"DELETE FROM public.private_file WHERE file_id='{qualification_file_id}';"
            f"DELETE FROM public.therapist_profile WHERE therapist_id='{therapist_id}';"
            f"DELETE FROM public.therapist_invitation WHERE invitation_id='{therapist_invitation_id}';"
            f"DELETE FROM public.institution_application WHERE application_id='{application_id}';"
            f"DELETE FROM public.institution_invitation WHERE invitation_id='{institution_invitation_id}';"
            f"DELETE FROM identity.user_member_self_link WHERE user_ref=98551;"
            f"DELETE FROM identity.member WHERE member_id='{subject_member_id}';"
            f"DELETE FROM public.\"user\" WHERE id IN (98551,{therapist_user_id},{issuer_user_id},{reviewer_user_id});"
            f"DELETE FROM public.tenant WHERE id={tenant_id};"
            f"DELETE FROM public.platform_org WHERE id={tenant_id + 10};COMMIT;"
        )
