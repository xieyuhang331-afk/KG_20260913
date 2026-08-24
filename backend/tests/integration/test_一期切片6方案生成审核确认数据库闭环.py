from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib
import json
from uuid import UUID

from app.modules.health_plan.repository import expected_mutation_postimage


def _uuid7(suffix: int) -> str:
    return f"018f0f47-e4a8-7cc8-98f2-88d31f8b{suffix:04x}"


def _prepare_mutation(database, function: str, payload: dict) -> dict:
    operation = {
        "slice6_generation_request_v1": "PLAN_GENERATION_REQUEST",
        "slice6_plan_explanation_v1": "PLAN_EXPLANATION",
        "slice6_user_decision_v1": "PLAN_USER_DECISION",
        "slice6_template_governance_v1": f"PLAN_TEMPLATE_{payload.get('operation')}",
        "slice6_review_transition_v1": (
            "PLAN_REVIEW_CLAIM"
            if payload.get("operation") == "CLAIM"
            else "PLAN_REVIEW_DECISION"
        ),
    }.get(function)
    if operation is not None and "expected_confirmed_digest" not in payload:
        expected = expected_mutation_postimage(operation, payload)
        digest = asyncio.run(
            database._fetch_value(
                "SELECT public.slice6_mutation_expected_v1($1::jsonb)",
                json.dumps(expected, separators=(",", ":"), sort_keys=True),
            )
        )
        return {
            **payload,
            "expected_postimage": expected,
            "expected_confirmed_digest": digest,
        }
    return payload


def _call_json(database, function: str, payload: dict):
    payload = _prepare_mutation(database, function, payload)
    value = asyncio.run(
        database._fetch_value(
            f"SELECT public.{function}($1::jsonb)",
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        )
    )
    return json.loads(value) if isinstance(value, str) else value


def _seed_ready_case(pg_database, *, ordinal: int = 66) -> dict[str, object]:
    seed_module = importlib.import_module(
        "tests.integration.test_一期切片4ProfileRoot、IdentitySummary与Runtime权限数据库合同"
    )
    tenant_id = 98660
    tenant_public_id = UUID(_uuid7(1))
    subject_member_id = UUID(_uuid7(2))
    enrollment_id, case_id, identity_revision_id, _ = asyncio.run(
        seed_module._seed_case(
            pg_database,
            ordinal=ordinal,
            mode="SELF",
            tenant_id=tenant_id,
            tenant_public_id=tenant_public_id,
            actor_user_id=98661,
            actor_member_id=subject_member_id,
            subject_member_id=subject_member_id,
            proxy_member_id=None,
        )
    )
    therapist_id = pg_database.fetch_value(
        f"SELECT primary_therapist_id FROM public.service_case WHERE case_id='{case_id}'"
    )
    therapist_user_id = pg_database.fetch_value(
        f"SELECT user_id FROM public.therapist_profile WHERE therapist_id='{therapist_id}'"
    )
    identity_verification_id = pg_database.fetch_value(
        f"SELECT identity_verification_id FROM public.service_case WHERE case_id='{case_id}'"
    )
    org_actor_id = pg_database.fetch_value(
        "SELECT applicant_user_id FROM public.institution_application "
        f"WHERE tenant_internal_id={tenant_id}"
    )
    ids = {
        "profile_revision_id": _uuid7(10),
        "policy_id": _uuid7(11),
        "assembly_id": _uuid7(12),
        "rule_id": _uuid7(13),
        "assessment_id": _uuid7(14),
        "snapshot_id": _uuid7(15),
        "qualification_file_id": _uuid7(16),
        "therapist_revision_id": _uuid7(17),
        "qualification_id": _uuid7(18),
        "template_id": _uuid7(19),
    }
    pg_database.execute(
        "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
        "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) VALUES ("
        f"'{ids['qualification_file_id']}','THERAPIST_QUALIFICATION',{therapist_user_id},8,'application/pdf',repeat('a',64),8,'application/pdf',repeat('a',64),'slice6-qualification-{ids['qualification_file_id']}','CLEAN',NULL,now(),now()+interval '365 days',now(),NULL,NULL);"
        "INSERT INTO public.therapist_profile_revision(revision_id,therapist_id,revision_no,profile_snapshot,input_digest,created_at) VALUES ("
        f"'{ids['therapist_revision_id']}','{therapist_id}',1,'{{}}'::jsonb,repeat('1',64),now());"
        "INSERT INTO public.therapist_qualification_version(qualification_version_id,therapist_id,profile_revision_id,previous_version_id,qualification_type,certificate_no_ciphertext,certificate_encryption_key_id,certificate_no_digest,certificate_digest_key_id,certificate_no_masked,issuer_name,valid_from,valid_until,attachment_count,version_no,created_at) VALUES ("
        f"'{ids['qualification_id']}','{therapist_id}','{ids['therapist_revision_id']}',NULL,'METABOLIC_HEALTH_PRACTICE',decode('00','hex'),'k1',repeat('2',64),'k1','*******0000','Slice6 Authority',current_date,current_date+365,1,1,now());"
        "INSERT INTO public.therapist_profile_revision_qualification(therapist_id,revision_id,qualification_version_id,position) VALUES ("
        f"'{therapist_id}','{ids['therapist_revision_id']}','{ids['qualification_id']}',1);"
        "INSERT INTO public.therapist_qualification_attachment(qualification_version_id,slot,private_file_id,created_at) VALUES ("
        f"'{ids['qualification_id']}',1,'{ids['qualification_file_id']}',now());"
        "UPDATE public.therapist_profile SET real_name_ciphertext=decode('00','hex'),real_name_encryption_key_id='k1',real_name_digest=repeat('3',64),real_name_digest_key_id='k1',display_name='Slice6 Therapist',practice_summary='Slice6',status='APPROVED_ACTIVE',service_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb,"
        f"current_qualification_version_id='{ids['qualification_id']}',current_revision_no=1,qualification_valid_until=current_date+365,submitted_at=now(),reviewed_at=now(),updated_at=now(),version=version+1 WHERE therapist_id='{therapist_id}';"
        f"UPDATE public.service_case SET service_scope_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb WHERE case_id='{case_id}';"
        f"UPDATE public.primary_therapist_assignment SET service_scope_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb WHERE assignment_id=(SELECT assignment_id FROM public.service_case WHERE case_id='{case_id}');"
        "INSERT INTO public.institution_service_readiness(tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY','{{}}'::text[],1,now(),1,repeat('7',64),repeat('5',64),'{{}}'::jsonb,current_date+365,1);"
        "INSERT INTO public.health_profile_revision(profile_revision_id,subject_member_id,subject_user_id,revision_no,tenant_public_id,identity_source_kind,identity_revision_ref,identity_source_version,snapshot_ciphertext,snapshot_key_id,reconfirmed_at,source_type,changed_fields,actor_user_id,actor_type,snapshot_digest,digest_key_id,created_at) VALUES ("
        f"'{ids['profile_revision_id']}','{subject_member_id}',98661,1,'{tenant_public_id}','SLICE3','{identity_revision_id}',1,decode('00','hex'),'slice6-test-k1',now(),'APP','[]'::jsonb,98661,'SELF',decode(repeat('11',32),'hex'),'slice6-test-k1',now());"
        "INSERT INTO public.assessment_readiness_policy_version(policy_version_id,version_no,status,required_profile_sections,required_indicators,allowed_states,projection_version,rule_version,professionally_approved,approved_by,approved_at,effective_from,policy_digest,digest_key_id,created_at) VALUES ("
        f"'{ids['policy_id']}',98660,'PUBLISHED','[]'::jsonb,'[]'::jsonb,'[\"VERIFIED\"]'::jsonb,2,'slice6-test',true,98661,now(),now()-interval '1 day',decode(repeat('22',32),'hex'),'slice6-test-k1',now());"
        "INSERT INTO public.assessment_input_assembly(assembly_id,service_case_id,subject_member_id,tenant_id,primary_therapist_id,profile_revision_id,consent_version_ids,policy_version_id,projection_version,rule_version,required_max_fact_id,required_max_status_event_seq,source_snapshot,status,reason_codes,missing_codes,expired_codes,disputed_codes,source_vector_digest,source_digest,assembly_digest,digest_key_id,generated_at) VALUES ("
        f"'{ids['assembly_id']}','{case_id}','{subject_member_id}',{tenant_id},'{therapist_id}','{ids['profile_revision_id']}','[]'::jsonb,'{ids['policy_id']}',2,'slice6-test',0,0,'synthetic','ASSESSMENT_READY','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,decode(repeat('33',32),'hex'),decode(repeat('44',32),'hex'),decode(repeat('55',32),'hex'),'slice6-test-k1',now());"
        "INSERT INTO public.assessment_readiness_case_pointer(service_case_id,current_assembly_id,source_vector_digest,version,updated_at) VALUES ("
        f"'{case_id}','{ids['assembly_id']}',decode(repeat('33',32),'hex'),1,now());"
        "INSERT INTO public.assessment_rule_set_version(rule_set_version_id,rule_set_code,version_no,status,typed_rule_payload,content_digest,digest_key_id,author_user_id,reviewer_user_id,approval_evidence_ref,effective_from,created_at,version) VALUES ("
        f"'{ids['rule_id']}','CN_ADULT_BASELINE_V1',98660,'PUBLISHED','{{}}'::jsonb,decode(repeat('66',32),'hex'),'SHA256_V1',98661,98662,'synthetic-double-sign',now()-interval '1 day',now(),1);"
        "INSERT INTO public.health_assessment(assessment_id,service_case_id,subject_member_id,tenant_id,sequence_no,snapshot_id,rule_set_version_id,status,overall_risk,initiated_by,initiated_at,started_at,completed_at,version) VALUES ("
        f"'{ids['assessment_id']}','{case_id}','{subject_member_id}',{tenant_id},1,'{ids['snapshot_id']}','{ids['rule_id']}','COMPLETED','ATTENTION',{therapist_user_id},now()-interval '2 hours',now()-interval '90 minutes',now()-interval '1 hour',3);"
        "INSERT INTO public.assessment_input_snapshot(snapshot_id,assessment_id,service_case_id,subject_member_id,tenant_id,assembly_id,assembly_digest,source_vector_digest,profile_revision_id,consent_version_ids,rule_set_version_id,projection_version,projection_rule_version,required_max_fact_id,required_max_status_event_seq,source_snapshot,fact_ref_manifest_digest,snapshot_digest,digest_key_id,created_at) VALUES ("
        f"'{ids['snapshot_id']}','{ids['assessment_id']}','{case_id}','{subject_member_id}',{tenant_id},'{ids['assembly_id']}',decode(repeat('55',32),'hex'),decode(repeat('33',32),'hex'),'{ids['profile_revision_id']}','[]'::jsonb,'{ids['rule_id']}',2,'slice6-test',0,0,'synthetic',decode(repeat('71',32),'hex'),decode(repeat('81',32),'hex'),'slice6-test-k1',now());"
        "INSERT INTO public.assessment_module_result(module_result_id,assessment_id,module_code,risk_level,reason_codes,evidence_manifest,message_codes,rule_fragment_digest,created_at) VALUES "
        f"('{_uuid7(30)}','{ids['assessment_id']}','BLOOD_PRESSURE_CARDIOVASCULAR','WITHIN_RANGE','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,decode(repeat('91',32),'hex'),now()),"
        f"('{_uuid7(31)}','{ids['assessment_id']}','GLUCOSE_METABOLISM','ATTENTION','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,decode(repeat('92',32),'hex'),now()),"
        f"('{_uuid7(32)}','{ids['assessment_id']}','LIPID_METABOLISM','WITHIN_RANGE','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,decode(repeat('93',32),'hex'),now()),"
        f"('{_uuid7(33)}','{ids['assessment_id']}','WEIGHT_ABDOMINAL_OBESITY','ATTENTION','[]'::jsonb,'[]'::jsonb,'[]'::jsonb,decode(repeat('94',32),'hex'),now());"
        "INSERT INTO public.health_plan_template_version(template_version_id,template_code,version_no,status,content,content_digest,medical_approval_ref,author_user_id,created_at,published_at,version) VALUES ("
        f"'{ids['template_id']}','PHASE1_STANDARD',1,'PUBLISHED','{{\"applicable_modules\":[\"BLOOD_PRESSURE_CARDIOVASCULAR\",\"GLUCOSE_METABOLISM\",\"LIPID_METABOLISM\",\"WEIGHT_ABDOMINAL_OBESITY\"],\"goals_by_module\":{{\"BLOOD_PRESSURE_CARDIOVASCULAR\":[\"GOAL_BP\"],\"GLUCOSE_METABOLISM\":[\"GOAL_GLUCOSE\"],\"LIPID_METABOLISM\":[\"GOAL_LIPID\"],\"WEIGHT_ABDOMINAL_OBESITY\":[\"GOAL_WEIGHT\"]}},\"stage_codes\":[\"STAGE_BASELINE\"],\"milestone_codes\":[\"MILESTONE_REVIEW\"],\"sop_codes\":[\"SOP_FOLLOW_UP\"],\"contraindication_codes\":[\"NO_DIAGNOSIS\"],\"user_message_codes\":[\"FOLLOW_PLAN\"],\"therapist_action_codes\":[\"REVIEW_PROGRESS\"]}}'::jsonb,decode(repeat('aa',32),'hex'),'synthetic-medical-approval',98662,now(),now(),1);"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,created_at,updated_at) VALUES (98662,'19900098662','synthetic','expert','active',now(),now()) ON CONFLICT (id) DO NOTHING;"
        "INSERT INTO public.consent_document_version(document_version_id,document_type,semantic_version,status,requires_reconsent,manifest_digest,effective_at,published_by,created_at,version) VALUES "
        f"('{_uuid7(60)}','USER_AGREEMENT','slice6-1','PUBLISHED',true,repeat('a',64),now()-interval '1 day',98662,now(),1),"
        f"('{_uuid7(61)}','PRIVACY_POLICY','slice6-1','PUBLISHED',true,repeat('b',64),now()-interval '1 day',98662,now(),1),"
        f"('{_uuid7(62)}','HEALTH_DATA_PROCESSING','slice6-1','PUBLISHED',true,repeat('c',64),now()-interval '1 day',98662,now(),1),"
        f"('{_uuid7(63)}','INSTITUTION_SERVICE','slice6-1','PUBLISHED',true,repeat('d',64),now()-interval '1 day',98662,now(),1),"
        f"('{_uuid7(64)}','NON_MEDICAL_RISK','slice6-1','PUBLISHED',true,repeat('e',64),now()-interval '1 day',98662,now(),1);"
        "INSERT INTO public.consent_document_rendition(rendition_id,document_version_id,locale,title,body,content_sha256,created_at) VALUES "
        f"('{_uuid7(65)}','{_uuid7(60)}','zh-CN','Slice6 agreement','Synthetic controlled text',repeat('1',64),now()),"
        f"('{_uuid7(66)}','{_uuid7(61)}','zh-CN','Slice6 privacy','Synthetic controlled text',repeat('2',64),now()),"
        f"('{_uuid7(67)}','{_uuid7(62)}','zh-CN','Slice6 health data','Synthetic controlled text',repeat('3',64),now()),"
        f"('{_uuid7(68)}','{_uuid7(63)}','zh-CN','Slice6 service','Synthetic controlled text',repeat('4',64),now()),"
        f"('{_uuid7(69)}','{_uuid7(64)}','zh-CN','Slice6 risk','Synthetic controlled text',repeat('5',64),now());"
        "INSERT INTO public.consent_record(consent_record_id,enrollment_id,subject_member_id,proxy_member_id,document_type,document_version_id,rendition_id,purpose_codes,choice,status,predecessor_id,presented_at,accepted_at,withdrawn_at,version) VALUES "
        f"('{_uuid7(70)}','{enrollment_id}','{subject_member_id}',NULL,'USER_AGREEMENT','{_uuid7(60)}','{_uuid7(65)}','[\"ACCOUNT_AND_SERVICE_ONBOARDING\"]'::jsonb,'ACCEPTED','ACCEPTED',NULL,now(),now(),NULL,1),"
        f"('{_uuid7(71)}','{enrollment_id}','{subject_member_id}',NULL,'PRIVACY_POLICY','{_uuid7(61)}','{_uuid7(66)}','[\"ACCOUNT_AND_SERVICE_ONBOARDING\"]'::jsonb,'ACCEPTED','ACCEPTED',NULL,now(),now(),NULL,1),"
        f"('{_uuid7(72)}','{enrollment_id}','{subject_member_id}',NULL,'HEALTH_DATA_PROCESSING','{_uuid7(62)}','{_uuid7(67)}','[\"ACCOUNT_AND_SERVICE_ONBOARDING\"]'::jsonb,'ACCEPTED','ACCEPTED',NULL,now(),now(),NULL,1),"
        f"('{_uuid7(73)}','{enrollment_id}','{subject_member_id}',NULL,'INSTITUTION_SERVICE','{_uuid7(63)}','{_uuid7(68)}','[\"ACCOUNT_AND_SERVICE_ONBOARDING\"]'::jsonb,'ACCEPTED','ACCEPTED',NULL,now(),now(),NULL,1),"
        f"('{_uuid7(74)}','{enrollment_id}','{subject_member_id}',NULL,'NON_MEDICAL_RISK','{_uuid7(64)}','{_uuid7(69)}','[\"ACCOUNT_AND_SERVICE_ONBOARDING\"]'::jsonb,'ACCEPTED','ACCEPTED',NULL,now(),now(),NULL,1);"
        "COMMIT;"
    )
    return {
        **ids,
        "tenant_id": tenant_id,
        "subject_member_id": str(subject_member_id),
        "enrollment_id": str(enrollment_id),
        "case_id": str(case_id),
        "identity_verification_id": str(identity_verification_id),
        "org_actor_id": int(org_actor_id),
        "family_actor_id": 98661,
        "expert_actor_id": 98662,
        "therapist_actor_id": int(therapist_user_id),
    }


def test_PG11_PG20_六身份函数完成生成审核与用户确认原子闭环(
    pg_database,
    slice6_institution_writer_database,
    slice6_review_writer_database,
    slice6_workflow_worker_database,
):
    seeded = _seed_ready_case(pg_database)
    authority_raw = asyncio.run(
        slice6_institution_writer_database._fetch_value(
            "SELECT public.slice6_generation_authority_v1($1::uuid,$2::bigint,$3::varchar)",
            UUID(str(seeded["case_id"])),
            seeded["org_actor_id"],
            "org_admin",
        )
    )
    authority = json.loads(authority_raw) if isinstance(authority_raw, str) else authority_raw
    assert authority["tenant_service_ready"] is True
    assert authority["service_case_current"] is True
    assert authority["consent_current"] is True
    assert authority["primary_therapist_current"] is True
    assert authority["assembly_ready"] is True
    assert authority["assessment_completed"] is True
    assert authority["high_risk_blocking"] is False

    request_id, plan_id, review_id = _uuid7(40), _uuid7(41), _uuid7(42)
    created_at = datetime.now(timezone.utc).isoformat()
    generation_response = {
        "request_id": request_id,
        "service_case_id": seeded["case_id"],
        "status": "REQUESTED",
        "current_plan_id": None,
        "current_plan_version": None,
        "failure_code": None,
        "created_at": created_at,
        "updated_at": created_at,
        "version": 1,
    }
    request_payload = {
        "request_id": request_id,
        "service_case_id": seeded["case_id"],
        "subject_member_id": seeded["subject_member_id"],
        "tenant_id": seeded["tenant_id"],
        "assessment_id": seeded["assessment_id"],
        "assessment_version": 3,
        "assembly_id": seeded["assembly_id"],
        "template_version_id": seeded["template_id"],
        "template_version": 1,
        "expected_service_case_version": authority["service_case_version"],
        "initiated_by": seeded["org_actor_id"],
        "initiated_role": "org_admin",
        "idempotency_key": "slice6-db-generation-0001",
        "request_digest": "11" * 32,
        "authority_digest": "12" * 32,
        "audit_id": _uuid7(43),
        "event_id": _uuid7(44),
        "receipt_id": _uuid7(45),
        "created_at": created_at,
        "response": generation_response,
        "postimage_digest": "13" * 32,
    }
    assert _call_json(
        slice6_institution_writer_database, "slice6_generation_request_v1", request_payload
    )["status"] == "REQUESTED"
    assert _call_json(
        slice6_institution_writer_database, "slice6_generation_request_v1", request_payload
    )["status"] == "REQUESTED"

    assert _call_json(
        slice6_workflow_worker_database,
        "slice6_generation_worker_v1",
        {"operation": "CLAIM", "request_id": request_id, "lease_owner": "slice6-worker"},
    )["status"] == "GENERATING"
    source_raw = asyncio.run(
        slice6_workflow_worker_database._fetch_value(
            "SELECT public.slice6_generation_input_v1($1::uuid)", UUID(request_id)
        )
    )
    source = json.loads(source_raw) if isinstance(source_raw, str) else source_raw
    assert source["assessment_id"] == seeded["assessment_id"]
    completed = _call_json(
        slice6_workflow_worker_database,
        "slice6_generation_complete_v1",
        {
            "request_id": request_id,
            "plan_id": plan_id,
            "review_id": review_id,
            "audit_id": _uuid7(46),
            "event_id": _uuid7(47),
            "version_no": 1,
            "content": {
                "module_summaries": [],
                "goals": [],
                "stages": [],
                "milestones": [],
                "sop_items": [],
                "contraindication_codes": [],
                "user_message_codes": [],
                "therapist_action_codes": [],
            },
            "content_digest": "14" * 32,
            "event_digest": "15" * 32,
            "created_at": created_at,
            "response": {
                "request_id": request_id,
                "status": "IN_REVIEW",
                "current_plan_id": plan_id,
                "current_plan_version": 1,
                "failure_code": None,
                "updated_at": created_at,
            },
        },
    )
    assert completed["status"] == "IN_REVIEW"

    claim_payload = {
        "operation": "CLAIM",
        "review_id": review_id,
        "expected_version": 1,
        "actor_user_id": seeded["expert_actor_id"],
        "actor_role": "expert",
        "idempotency_key": "slice6-db-claim-0001",
        "request_digest": "16" * 32,
        "audit_id": _uuid7(48),
        "event_id": _uuid7(49),
        "receipt_id": _uuid7(50),
        "occurred_at": created_at,
        "response": {"review_id": review_id, "status": "CLAIMED", "version": 2},
        "postimage_digest": "17" * 32,
    }
    assert _call_json(
        slice6_review_writer_database, "slice6_review_transition_v1", claim_payload
    )["status"] == "CLAIMED"
    assert _call_json(
        slice6_review_writer_database, "slice6_review_transition_v1", claim_payload
    )["status"] == "CLAIMED"

    correction_payload = {
        "operation": "DECIDE",
        "review_id": review_id,
        "decision": "NEEDS_CORRECTION",
        "reason_codes": ["TEMPLATE_REAPPLY"],
        "expected_version": 2,
        "actor_user_id": seeded["expert_actor_id"],
        "actor_role": "expert",
        "idempotency_key": "slice6-db-correction-0001",
        "request_digest": "18" * 32,
        "audit_id": _uuid7(51),
        "event_id": _uuid7(52),
        "receipt_id": _uuid7(53),
        "occurred_at": created_at,
        "response": {
            "review_id": review_id,
            "status": "NEEDS_CORRECTION",
            "version": 3,
        },
        "postimage_digest": "19" * 32,
    }
    assert _call_json(
        slice6_review_writer_database, "slice6_review_transition_v1", correction_payload
    )["status"] == "NEEDS_CORRECTION"

    corrected_plan_id = _uuid7(58)
    corrected_review_id = _uuid7(59)
    assert _call_json(
        slice6_workflow_worker_database,
        "slice6_generation_worker_v1",
        {"operation": "CLAIM", "request_id": request_id, "lease_owner": "slice6-worker"},
    )["status"] == "GENERATING"
    corrected_source_raw = asyncio.run(
        slice6_workflow_worker_database._fetch_value(
            "SELECT public.slice6_generation_input_v1($1::uuid)", UUID(request_id)
        )
    )
    corrected_source = (
        json.loads(corrected_source_raw)
        if isinstance(corrected_source_raw, str)
        else corrected_source_raw
    )
    assert corrected_source["next_version_no"] == 2
    assert corrected_source["supersedes_plan_id"] == plan_id
    corrected = _call_json(
        slice6_workflow_worker_database,
        "slice6_generation_complete_v1",
        {
            "request_id": request_id,
            "plan_id": corrected_plan_id,
            "review_id": corrected_review_id,
            "audit_id": _uuid7(60),
            "event_id": _uuid7(61),
            "version_no": 2,
            "supersedes_plan_id": plan_id,
            "content": {
                "module_summaries": [],
                "goals": [],
                "stages": [],
                "milestones": [],
                "sop_items": [],
                "contraindication_codes": [],
                "user_message_codes": [],
                "therapist_action_codes": [],
            },
            "content_digest": "22" * 32,
            "event_digest": "23" * 32,
            "created_at": created_at,
            "response": {
                "request_id": request_id,
                "status": "IN_REVIEW",
                "current_plan_id": corrected_plan_id,
                "current_plan_version": 2,
                "failure_code": None,
                "updated_at": created_at,
            },
        },
    )
    assert corrected["current_plan_version"] == 2

    corrected_claim_payload = {
        "operation": "CLAIM",
        "review_id": corrected_review_id,
        "expected_version": 1,
        "actor_user_id": seeded["expert_actor_id"],
        "actor_role": "expert",
        "idempotency_key": "slice6-db-claim-0002",
        "request_digest": "24" * 32,
        "audit_id": _uuid7(62),
        "event_id": _uuid7(63),
        "receipt_id": _uuid7(64),
        "occurred_at": created_at,
        "response": {"review_id": corrected_review_id, "status": "CLAIMED", "version": 2},
        "postimage_digest": "25" * 32,
    }
    assert _call_json(
        slice6_review_writer_database,
        "slice6_review_transition_v1",
        corrected_claim_payload,
    )["status"] == "CLAIMED"

    decision_payload = {
        "operation": "DECIDE",
        "review_id": corrected_review_id,
        "decision": "APPROVED",
        "reason_codes": ["CONTENT_APPROVED"],
        "expected_version": 2,
        "actor_user_id": seeded["expert_actor_id"],
        "actor_role": "expert",
        "idempotency_key": "slice6-db-review-0002",
        "request_digest": "26" * 32,
        "audit_id": _uuid7(65),
        "event_id": _uuid7(66),
        "receipt_id": _uuid7(67),
        "occurred_at": created_at,
        "response": {
            "review_id": corrected_review_id,
            "status": "APPROVED",
            "version": 3,
        },
        "postimage_digest": "27" * 32,
    }
    assert _call_json(
        slice6_review_writer_database, "slice6_review_transition_v1", decision_payload
    )["status"] == "APPROVED"

    user_payload = {
        "decision_id": _uuid7(68),
        "plan_id": corrected_plan_id,
        "decision": "ACCEPT",
        "expected_version": 2,
        "actor_user_id": seeded["family_actor_id"],
        "actor_role": "member",
        "actor_context": "AUTO",
        "proxy_grant_id": None,
        "idempotency_key": "slice6-db-user-0001",
        "request_digest": "20" * 32,
        "audit_id": _uuid7(69),
        "event_id": _uuid7(70),
        "receipt_id": _uuid7(71),
        "occurred_at": created_at,
        "response": {
            "decision_id": _uuid7(68),
            "plan_id": corrected_plan_id,
            "decision": "ACCEPT",
            "status": "ACTIVE",
            "version": 3,
        },
        "postimage_digest": "21" * 32,
    }
    assert _call_json(
        slice6_institution_writer_database, "slice6_user_decision_v1", user_payload
    )["status"] == "ACTIVE"
    assert _call_json(
        slice6_institution_writer_database, "slice6_user_decision_v1", user_payload
    )["status"] == "ACTIVE"

    assert pg_database.fetch_value(
        f"SELECT status FROM public.health_plan_version WHERE plan_id='{plan_id}'"
    ) == "SUPERSEDED"
    assert pg_database.fetch_value(
        f"SELECT status FROM public.health_plan_version WHERE plan_id='{corrected_plan_id}'"
    ) == "ACTIVE"
    assert str(
        pg_database.fetch_value(
            f"SELECT supersedes_plan_id FROM public.health_plan_version WHERE plan_id='{corrected_plan_id}'"
        )
    ) == corrected_source["supersedes_plan_id"]
    assert pg_database.fetch_value(
        f"SELECT actor_context FROM public.health_plan_user_decision WHERE plan_id='{corrected_plan_id}'"
    ) == "SELF"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_user_decision WHERE plan_id='{corrected_plan_id}'"
    ) == 1
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_receipt WHERE target_id IN ('{request_id}','{review_id}','{corrected_review_id}','{plan_id}','{corrected_plan_id}')"
    ) == 6
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_audit WHERE target_id IN ('{request_id}','{review_id}','{corrected_review_id}','{plan_id}','{corrected_plan_id}')"
    ) == 8
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_outbox WHERE aggregate_ref IN ('{request_id}','{review_id}','{corrected_review_id}','{plan_id}','{corrected_plan_id}')"
    ) == 8

    committed_payload = _prepare_mutation(
        slice6_institution_writer_database, "slice6_user_decision_v1", user_payload
    )
    committed = _call_json(
        slice6_institution_writer_database,
        "slice6_mutation_confirm_v1",
        {
            "expected_postimage": committed_payload["expected_postimage"],
            "expected_confirmed_digest": committed_payload["expected_confirmed_digest"],
        },
    )
    assert committed["outcome"] == "COMMITTED"
    assert len(committed["confirmed_postimage_digest"]) == 64

    not_committed_request = {
        **request_payload,
        "request_id": _uuid7(72),
        "audit_id": _uuid7(73),
        "event_id": _uuid7(74),
        "receipt_id": _uuid7(75),
        "idempotency_key": "slice6-db-never-written-0001",
        "response": {
            **generation_response,
            "request_id": _uuid7(72),
        },
    }
    not_committed_payload = _prepare_mutation(
        slice6_institution_writer_database,
        "slice6_generation_request_v1",
        not_committed_request,
    )
    not_committed = _call_json(
        slice6_institution_writer_database,
        "slice6_mutation_confirm_v1",
        {
            "expected_postimage": not_committed_payload["expected_postimage"],
            "expected_confirmed_digest": not_committed_payload[
                "expected_confirmed_digest"
            ],
        },
    )
    assert not_committed["outcome"] == "NOT_COMMITTED"

    pg_database.execute(
        f"DELETE FROM public.health_plan_outbox WHERE event_id='{user_payload['event_id']}'"
    )
    partial = _call_json(
        slice6_institution_writer_database,
        "slice6_mutation_confirm_v1",
        {
            "expected_postimage": committed_payload["expected_postimage"],
            "expected_confirmed_digest": committed_payload["expected_confirmed_digest"],
        },
    )
    assert partial["outcome"] == "UNKNOWN"
