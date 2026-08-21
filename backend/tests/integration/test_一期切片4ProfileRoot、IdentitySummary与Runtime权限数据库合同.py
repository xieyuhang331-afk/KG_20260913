from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.core.uuid_generator import Uuid7Generator


pytestmark = pytest.mark.integration


def _role(name: str) -> str:
    value = os.environ[name]
    assert value
    return value


def _uuid7() -> UUID:
    return Uuid7Generator().generate()


async def _seed_case(
    pg_database,
    *,
    ordinal: int,
    mode: str,
    tenant_id: int,
    tenant_public_id: UUID,
    actor_user_id: int,
    actor_member_id: UUID,
    subject_member_id: UUID,
    proxy_member_id: UUID | None,
) -> tuple[UUID, UUID, UUID, int]:
    invitation_id = _uuid7()
    enrollment_id = _uuid7()
    verification_id = _uuid7()
    revision_id = _uuid7()
    decision_id = _uuid7()
    claim_id = _uuid7()
    therapist_invitation_id = _uuid7()
    therapist_id = _uuid7()
    assignment_id = _uuid7()
    case_id = _uuid7()
    application_invitation_id = _uuid7()
    application_id = _uuid7()
    grant_id = _uuid7()
    document_id = _uuid7()
    actor_link_id = _uuid7()
    therapist_user_id = actor_user_id + 100
    issuer_user_id = actor_user_id + 200
    reviewer_user_id = actor_user_id + 300
    proxy_sql = "NULL" if proxy_member_id is None else f"'{proxy_member_id}'"
    actor_link_member = actor_member_id
    member_rows_sql = (
        f"('{subject_member_id}','M{ordinal:020d}','registration','created',1,now(),now())"
    )
    if actor_member_id != subject_member_id:
        member_rows_sql += (
            f",('{actor_member_id}','M{ordinal + 100:020d}','registration','created',1,now(),now())"
        )
    grant_sql = ""
    if mode == "PROXY_ELDER":
        assert proxy_member_id == actor_member_id
        grant_sql = (
            "UPDATE public.consent_document_version SET status='RETIRED',retired_at=now(),version=version+1 "
            "WHERE document_type='PROXY_AUTHORIZATION' AND status='PUBLISHED';"
            "INSERT INTO public.consent_document_version(document_version_id,document_type,"
            "semantic_version,status,requires_reconsent,manifest_digest,effective_at,retired_at,"
            "published_by,created_at,version) VALUES ("
            f"'{document_id}','PROXY_AUTHORIZATION','1.{ordinal}','PUBLISHED',true,repeat('7',64),"
            f"now(),NULL,{issuer_user_id},now(),1);"
            "INSERT INTO public.proxy_grant(grant_id,enrollment_id,principal_member_id,"
            "proxy_member_id,slot_no,permission_codes,authorization_document_version_id,"
            "witness_decision_id,status,valid_from,valid_until,revoked_at,version) VALUES ("
            f"'{grant_id}','{enrollment_id}','{subject_member_id}','{proxy_member_id}',1,"
            f"'[\"DAILY_INPUT\"]'::jsonb,'{document_id}','{decision_id}','ACTIVE',"
            "now(),now()+interval '30 days',NULL,1);"
        )

    await pg_database._execute(
        "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES ("
        f"{tenant_id + 10},NULL,'Slice4 PG root {ordinal}','S4-PG-{ordinal}','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) VALUES ("
        f"{tenant_id},{tenant_id + 10},'S4-PG-T-{ordinal}','Slice4 PG tenant {ordinal}',"
        "'store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES "
        f"({actor_user_id},'00000000{ordinal:03d}','test-only','member','active',NULL),"
        f"({therapist_user_id},'00000001{ordinal:03d}','test-only','therapist','active',{tenant_id}),"
        f"({issuer_user_id},'00000002{ordinal:03d}','test-only','org_admin','active',{tenant_id}),"
        f"({reviewer_user_id},'00000003{ordinal:03d}','test-only','super_admin','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) VALUES "
        f"{member_rows_sql};"
        "INSERT INTO identity.user_member_self_link(link_id,user_ref,member_id,source,"
        "eligibility_decision_ref,establishment_basis,establishment_record_ref,created_at) VALUES ("
        f"'{actor_link_id}',{actor_user_id},'{actor_link_member}','REGISTRATION_VERIFIED',"
        f"'{_uuid7()}','REGISTRATION_VERIFIED_BOOTSTRAP','{_uuid7()}',now());"
        "INSERT INTO public.institution_invitation(invitation_id,institution_name,institution_type,"
        "applicant_phone_ciphertext,applicant_phone_digest,pilot_batch_code,administrative_region_id,"
        "code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{application_invitation_id}','Slice4 institution {ordinal}','HEALTH_STORE',decode('00','hex'),"
        f"repeat('8',64),'SLICE4',{tenant_id + 10},repeat('9',64),'ACTIVATED',0,now()+interval '1 day',"
        f"{issuer_user_id},now(),now(),1);"
        "INSERT INTO public.institution_application(application_id,invitation_id,applicant_user_id,"
        "institution_type,status,draft_payload,correction_fields,current_revision_no,tenant_internal_id,"
        "tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{application_id}','{application_invitation_id}',{issuer_user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3);"
        "INSERT INTO public.member_service_invitation(invitation_id,tenant_id,mode,phone_ciphertext,"
        "phone_key_id,phone_digest,phone_digest_key_id,phone_masked,code_digest,code_key_id,status,"
        "failed_attempts,expires_at,issued_by,issued_at,accepted_at,revoked_at,version) VALUES ("
        f"'{invitation_id}',{tenant_id},'{mode}',decode('00','hex'),'k1',repeat('a',63)||'{ordinal}',"
        f"'k1','*******0000',repeat('b',63)||'{ordinal}','k1','ACCEPTED',0,now()+interval '1 day',"
        f"{issuer_user_id},now(),now(),NULL,1);"
        "INSERT INTO public.service_enrollment(enrollment_id,invitation_id,tenant_id,subject_member_id,"
        "proxy_member_id,mode,status,service_scope_tags,current_identity_verification_id,current_assignment_id,"
        "service_case_id,accepted_at,identity_verified_at,case_created_at,created_at,updated_at,version) VALUES ("
        f"'{enrollment_id}','{invitation_id}',{tenant_id},'{subject_member_id}',{proxy_sql},'{mode}',"
        "'CASE_CREATED','[\"GLUCOSE_METABOLISM\"]'::jsonb,NULL,NULL,NULL,"
        "now(),now(),now(),now(),now(),10);"
        "INSERT INTO public.member_identity_verification(verification_id,enrollment_id,member_id,"
        "current_revision_id,status,institution_decision_id,platform_decision_id,submitted_at,"
        "institution_checked_at,platform_decided_at,version) VALUES ("
        f"'{verification_id}','{enrollment_id}','{subject_member_id}','{revision_id}','APPROVED',"
        "NULL,NULL,now(),now(),now(),3);"
        "INSERT INTO public.member_identity_revision(revision_id,verification_id,revision_no,document_type,"
        "real_name_ciphertext,real_name_key_id,id_ciphertext,id_key_id,birth_date_ciphertext,birth_date_key_id,"
        "id_masked,identity_fingerprint,fingerprint_key_id,input_digest,submitted_by_member_id,created_at) VALUES ("
        f"'{revision_id}','{verification_id}',1,'PRC_RESIDENT_ID',decode('00','hex'),'k1',decode('01','hex'),"
        f"'k1',decode('02','hex'),'k1','**************0000',repeat('c',63)||'{ordinal}','k1',"
        f"repeat('d',63)||'{ordinal}','{subject_member_id}',now());"
        "INSERT INTO public.member_identity_review_decision(decision_id,verification_id,revision_id,phase,"
        "reviewer_user_id,decision,reason_code,correction_fields,attestation_code,represented_elder_eligible,"
        "request_digest,evidence_digest,created_at) VALUES ("
        f"'{decision_id}','{verification_id}','{revision_id}','PLATFORM',{reviewer_user_id},'APPROVED',"
        f"'OFFLINE_VERIFIED',NULL,'SLICE4',true,repeat('e',63)||'{ordinal}',repeat('f',63)||'{ordinal}',now());"
        "INSERT INTO identity.identity_subject_claim_registry(claim_id,identity_fingerprint,fingerprint_key_id,"
        "user_ref,member_id,source_kind,p1_submission_id,p1_decision_ref,slice3_revision_id,slice3_decision_id,"
        "source_facts_version,source_evidence_digest,adult_eligible,represented_elder_eligible,claimed_at,version) VALUES ("
        f"'{claim_id}',repeat('{ordinal}',64),'k1',NULL,'{subject_member_id}','SLICE3',NULL,NULL,"
        f"'{revision_id}','{decision_id}',1,repeat('1',64),NULL,true,now(),1);"
        "INSERT INTO public.therapist_invitation(invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,"
        "phone_digest,phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,expires_at,status,"
        "failed_attempts,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{therapist_invitation_id}',{tenant_id},decode('00','hex'),'k1',repeat('2',63)||'{ordinal}','k1',"
        f"'*******0000',repeat('3',63)||'{ordinal}','k1',now()+interval '1 day','ACTIVATED',0,"
        f"{issuer_user_id},now(),now(),1);"
        "INSERT INTO public.therapist_profile(therapist_id,user_id,tenant_id,invitation_id,status,capacity_limit,"
        "active_case_count,current_revision_no,totp_secret_ciphertext,totp_encryption_key_id,totp_enabled,"
        "activated_at,created_at,updated_at,version) VALUES ("
        f"'{therapist_id}',{therapist_user_id},{tenant_id},'{therapist_invitation_id}','DRAFT',30,1,0,"
        "decode('00','hex'),'k1',true,now(),now(),now(),1);"
        "INSERT INTO public.primary_therapist_assignment(assignment_id,enrollment_id,tenant_id,subject_member_id,"
        "therapist_id,status,service_scope_tags,reason_code,service_case_id,created_by,created_at,decided_at,version) VALUES ("
        f"'{assignment_id}','{enrollment_id}',{tenant_id},'{subject_member_id}','{therapist_id}','PENDING_ACCEPTANCE',"
        f"'[\"GLUCOSE_METABOLISM\"]'::jsonb,NULL,NULL,{issuer_user_id},now(),NULL,1);"
        "INSERT INTO public.service_case(case_id,enrollment_id,subject_member_id,tenant_id,primary_therapist_id,"
        "assignment_id,status,identity_verification_id,identity_revision_id,consent_set_digest,"
        "readiness_evidence_version,readiness_result_digest,service_scope_tags,created_at,updated_at,version) VALUES ("
        f"'{case_id}','{enrollment_id}','{subject_member_id}',{tenant_id},'{therapist_id}','{assignment_id}',"
        f"'PREPARING','{verification_id}','{revision_id}',repeat('4',64),1,repeat('5',64),"
        "'[\"GLUCOSE_METABOLISM\"]'::jsonb,now(),now(),1);"
        "UPDATE public.member_identity_verification SET platform_decision_id="
        f"'{decision_id}' WHERE verification_id='{verification_id}';"
        "UPDATE public.primary_therapist_assignment SET status='ACCEPTED',service_case_id="
        f"'{case_id}',decided_at=now(),version=2 WHERE assignment_id='{assignment_id}';"
        "UPDATE public.service_enrollment SET current_identity_verification_id="
        f"'{verification_id}',current_assignment_id='{assignment_id}',service_case_id='{case_id}' "
        f"WHERE enrollment_id='{enrollment_id}';"
        f"{grant_sql}COMMIT;"
    )
    return enrollment_id, case_id, revision_id, actor_user_id


@pytest.mark.asyncio
async def test_PG27_SELF与PROXY老人仅经受限函数原子创建ProfileRoot(
    pg_database,
    health_record_writer_database,
) -> None:
    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    self_member_id = generated.generate()
    proxy_actor_member_id = generated.generate()
    elder_member_id = generated.generate()
    self_enrollment_id, self_case_id, self_identity_revision_id, self_actor_id = (
        await _seed_case(
            pg_database,
            ordinal=1,
            mode="SELF",
            tenant_id=98410,
            tenant_public_id=tenant_public_id,
            actor_user_id=98411,
            actor_member_id=self_member_id,
            subject_member_id=self_member_id,
            proxy_member_id=None,
        )
    )
    proxy_enrollment_id, proxy_case_id, proxy_identity_revision_id, proxy_actor_id = (
        await _seed_case(
            pg_database,
            ordinal=2,
            mode="PROXY_ELDER",
            tenant_id=98420,
            tenant_public_id=generated.generate(),
            actor_user_id=98421,
            actor_member_id=proxy_actor_member_id,
            subject_member_id=elder_member_id,
            proxy_member_id=proxy_actor_member_id,
        )
    )

    async def create_root(
        *,
        actor_user_id: int,
        actor_context: str,
        subject_member_id: UUID,
        case_id: UUID,
        enrollment_id: UUID,
        identity_revision_id: UUID,
        tenant_public: UUID,
    ) -> tuple[dict, UUID, UUID, UUID]:
        profile_id = generated.generate()
        profile_revision_id = generated.generate()
        idempotency_key = generated.generate()
        values = (
            actor_user_id,
            actor_context,
            subject_member_id,
            case_id,
            enrollment_id,
            profile_id,
            profile_revision_id,
            tenant_public,
            identity_revision_id,
            1,
            b"profile-snapshot",
            "profile-k1",
            b"profile-digest-value",
            "digest-k1",
            datetime(2026, 8, 21, tzinfo=timezone.utc),
            "APP",
            '["medical_history"]',
            idempotency_key,
            b"request-digest-value",
            b"expected-postimage-value",
        )
        sql = (
            "SELECT * FROM public.slice4_health_profile_root_create_v1("
            "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamptz,"
            "$16,$17::jsonb,$18,$19,$20)"
        )
        rows = await health_record_writer_database._fetch_rows(sql, *values)
        assert len(rows) == 1
        return rows[0], profile_id, profile_revision_id, idempotency_key

    self_row, self_profile_id, self_revision_id, _ = await create_root(
        actor_user_id=self_actor_id,
        actor_context="SELF",
        subject_member_id=self_member_id,
        case_id=self_case_id,
        enrollment_id=self_enrollment_id,
        identity_revision_id=self_identity_revision_id,
        tenant_public=tenant_public_id,
    )
    proxy_tenant_public_id = await pg_database._fetch_value(
        "SELECT a.tenant_public_id FROM public.service_case c "
        "JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id "
        "JOIN public.institution_application a ON a.tenant_internal_id=e.tenant_id "
        "WHERE c.case_id=$1",
        proxy_case_id,
    )
    proxy_row, proxy_profile_id, proxy_revision_id, proxy_key = await create_root(
        actor_user_id=proxy_actor_id,
        actor_context="PROXY",
        subject_member_id=elder_member_id,
        case_id=proxy_case_id,
        enrollment_id=proxy_enrollment_id,
        identity_revision_id=proxy_identity_revision_id,
        tenant_public=proxy_tenant_public_id,
    )

    assert self_row == {
        "profile_public_id": self_profile_id,
        "subject_member_id": self_member_id,
        "subject_user_id": self_actor_id,
        "current_revision_id": self_revision_id,
        "version": 1,
    }
    assert proxy_row == {
        "profile_public_id": proxy_profile_id,
        "subject_member_id": elder_member_id,
        "subject_user_id": None,
        "current_revision_id": proxy_revision_id,
        "version": 1,
    }
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_profile WHERE subject_member_id=ANY($1::uuid[])",
        [self_member_id, elder_member_id],
    ) == 2
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_profile_revision WHERE subject_member_id=ANY($1::uuid[])",
        [self_member_id, elder_member_id],
    ) == 2
    for profile_id in (self_profile_id, proxy_profile_id):
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1", profile_id
        ) == 1
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref=$1", profile_id
        ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref=$1 AND idempotency_key=$2",
        elder_member_id,
        proxy_key,
    ) == 1


@pytest.mark.asyncio
async def test_PG06_PG29_报告Owner函数原子绑定排序附件并稳定回放(
    pg_database,
    health_record_writer_database,
) -> None:
    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    enrollment_id, case_id, _, actor_user_id = await _seed_case(
        pg_database,
        ordinal=4,
        mode="SELF",
        tenant_id=99210,
        tenant_public_id=tenant_public_id,
        actor_user_id=99211,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    file_ids = [generated.generate(), generated.generate()]
    for position, file_id in enumerate(file_ids, start=1):
        await pg_database._execute(
            "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,"
            "declared_mime_type,declared_sha256,actual_size,actual_mime_type,actual_sha256,"
            "object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) "
            f"VALUES('{file_id}','DETECTION_REPORT',{actor_user_id},8,'application/pdf',"
            "repeat('a',64),8,'application/pdf',repeat('a',64),"
            f"'slice4-report-{position}-{file_id}','CLEAN',NULL,now(),"
            "now()+interval '1 day',now(),NULL,NULL)"
        )
    requested_report_id = generated.generate()
    idempotency_key = generated.generate()
    measured_at = datetime(2026, 8, 20, 8, 30, tzinfo=timezone.utc)
    values = (
        actor_user_id,
        "SELF",
        subject_member_id,
        case_id,
        enrollment_id,
        requested_report_id,
        "LAB_REPORT",
        measured_at,
        "APP",
        list(reversed(file_ids)),
        idempotency_key,
        b"report-request-digest",
        b"report-postimage-digest",
    )
    sql = (
        "SELECT * FROM public.slice4_detection_report_create_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8::timestamptz,$9,$10::uuid[],$11,$12,$13)"
    )
    first = await health_record_writer_database._fetch_rows(sql, *values)
    replay = await health_record_writer_database._fetch_rows(sql, *values)
    assert len(first) == len(replay) == 1
    assert first[0] == replay[0]
    assert first[0]["report_id"] == requested_report_id
    assert first[0]["attachment_count"] == 2
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.detection_report WHERE report_id=$1", requested_report_id
    ) == 1
    attachments = await pg_database._fetch_rows(
        "SELECT private_file_id,position FROM public.detection_report_attachment "
        "WHERE report_id=$1 ORDER BY position",
        requested_report_id,
    )
    assert attachments == [
        {"private_file_id": file_id, "position": index}
        for index, file_id in enumerate(sorted(file_ids), start=1)
    ]
    for table in ("slice4_audit", "slice4_outbox"):
        assert await pg_database._fetch_value(
            f"SELECT count(*) FROM public.{table} WHERE aggregate_ref=$1",
            requested_report_id,
        ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_idempotency "
        "WHERE operation='CREATE_DETECTION_REPORT' AND scope_ref=$1 AND idempotency_key=$2",
        subject_member_id,
        idempotency_key,
    ) == 1


@pytest.mark.asyncio
async def test_PG18_PG29_AssessmentOwner函数原子追加并更新当前指针(
    pg_database,
    health_record_writer_database,
    health_fact_writer_database,
    assessment_readiness_writer_database,
) -> None:
    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    enrollment_id, case_id, identity_revision_id, actor_user_id = await _seed_case(
        pg_database,
        ordinal=5,
        mode="SELF",
        tenant_id=98810,
        tenant_public_id=tenant_public_id,
        actor_user_id=98811,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    therapist_id = await pg_database._fetch_value(
        "SELECT primary_therapist_id FROM public.service_case WHERE case_id=$1", case_id
    )
    therapist_user_id = await pg_database._fetch_value(
        "SELECT user_id FROM public.therapist_profile WHERE therapist_id=$1", therapist_id
    )
    therapist_revision_id = generated.generate()
    qualification_id = generated.generate()
    qualification_file_id = generated.generate()
    await pg_database._execute(
        "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
        "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,"
        "declared_mime_type,declared_sha256,actual_size,actual_mime_type,actual_sha256,"
        "object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) "
        f"VALUES('{qualification_file_id}','THERAPIST_QUALIFICATION',{therapist_user_id},8,"
        "'application/pdf',repeat('a',64),8,'application/pdf',repeat('a',64),"
        f"'slice4-qualification-{qualification_file_id}','CLEAN',NULL,now(),"
        "now()+interval '365 days',now(),NULL,NULL);"
        "INSERT INTO public.therapist_profile_revision(revision_id,therapist_id,revision_no,"
        "profile_snapshot,input_digest,created_at) VALUES ("
        f"'{therapist_revision_id}','{therapist_id}',1,'{{}}'::jsonb,repeat('1',64),now());"
        "INSERT INTO public.therapist_qualification_version(qualification_version_id,therapist_id,"
        "profile_revision_id,previous_version_id,qualification_type,certificate_no_ciphertext,"
        "certificate_encryption_key_id,certificate_no_digest,certificate_digest_key_id,"
        "certificate_no_masked,issuer_name,valid_from,valid_until,attachment_count,version_no,created_at) VALUES ("
        f"'{qualification_id}','{therapist_id}','{therapist_revision_id}',NULL,"
        "'METABOLIC_HEALTH_PRACTICE',decode('00','hex'),'k1',repeat('2',64),'k1',"
        "'*******0000','Slice4 Authority',current_date,current_date+365,1,1,now());"
        "INSERT INTO public.therapist_profile_revision_qualification(therapist_id,revision_id,"
        "qualification_version_id,position) VALUES ("
        f"'{therapist_id}','{therapist_revision_id}','{qualification_id}',1);"
        "INSERT INTO public.therapist_qualification_attachment(qualification_version_id,slot,"
        f"private_file_id,created_at) VALUES ('{qualification_id}',1,'{qualification_file_id}',now());"
        "UPDATE public.therapist_profile SET real_name_ciphertext=decode('00','hex'),"
        "real_name_encryption_key_id='k1',real_name_digest=repeat('3',64),"
        "real_name_digest_key_id='k1',display_name='Slice4 Therapist',practice_summary='Slice4',"
        "service_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb,status='APPROVED_ACTIVE',"
        f"current_qualification_version_id='{qualification_id}',current_revision_no=1,"
        "qualification_valid_until=current_date+365,submitted_at=now(),reviewed_at=now(),"
        f"updated_at=now(),version=2 WHERE therapist_id='{therapist_id}'; COMMIT;"
    )
    profile_id = generated.generate()
    profile_revision_id = generated.generate()
    profile_values = (
        actor_user_id,
        "SELF",
        subject_member_id,
        case_id,
        enrollment_id,
        profile_id,
        profile_revision_id,
        tenant_public_id,
        identity_revision_id,
        1,
        b"profile-snapshot",
        "profile-k1",
        b"profile-digest-value",
        "digest-k1",
        datetime(2026, 8, 21, tzinfo=timezone.utc),
        "APP",
        '["medical_history"]',
        generated.generate(),
        b"profile-request-digest",
        b"profile-postimage-digest",
    )
    await health_record_writer_database._fetch_rows(
        "SELECT * FROM public.slice4_health_profile_root_create_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamptz,"
        "$16,$17::jsonb,$18,$19,$20)",
        *profile_values,
    )
    policy_version_id = generated.generate()
    reviewer_user_id = actor_user_id + 300
    await pg_database._execute(
        "INSERT INTO public.assessment_readiness_policy_version(policy_version_id,version_no,"
        "status,required_profile_sections,required_indicators,allowed_states,projection_version,"
        "rule_version,professionally_approved,approved_by,approved_at,effective_from,retired_at,"
        "policy_digest,digest_key_id,created_at) VALUES ("
        f"'{policy_version_id}',1,'PUBLISHED','[\"medical_history\"]'::jsonb,"
        "'[\"height\",\"weight\"]'::jsonb,'[\"VERIFIED\"]'::jsonb,2,'slice4-v1',true,"
        f"{reviewer_user_id},now(),now()-interval '1 minute',NULL,decode(repeat('a',64),'hex'),"
        "'policy-k1',now())"
    )
    assembly_id = generated.generate()
    idempotency_key = generated.generate()
    source_vector = {
        "consent_version_ids": [],
        "resolved_generation_id": "",
        "required_max_fact_id": 0,
        "required_max_status_event_seq": 0,
        "missing_codes": ["height", "weight"],
        "expired_codes": [],
        "disputed_codes": [],
        "source_vector_digest": "b" * 64,
        "source_digest": "c" * 64,
        "assembly_digest": "d" * 64,
        "digest_key_id": "assembly-k1",
        "generated_at": datetime(2026, 8, 21, 1, tzinfo=timezone.utc).isoformat(),
        "audit_id": str(generated.generate()),
        "event_id": str(generated.generate()),
        "receipt_id": str(generated.generate()),
    }
    values = (
        case_id,
        assembly_id,
        subject_member_id,
        tenant_public_id,
        therapist_id,
        profile_revision_id,
        policy_version_id,
        2,
        "slice4-v1",
        "snapshot-v1",
        json.dumps(source_vector, separators=(",", ":")),
        "[]",
        "DATA_INSUFFICIENT",
        '["MISSING_PROFILE_DATA"]',
        idempotency_key,
        b"assembly-request-digest",
        b"assembly-postimage-digest",
    )
    sql = (
        "SELECT * FROM public.slice4_assessment_assembly_write_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13,$14::jsonb,"
        "$15,$16,$17)"
    )
    first = await assessment_readiness_writer_database._fetch_rows(sql, *values)
    replay = await assessment_readiness_writer_database._fetch_rows(sql, *values)
    assert first == replay
    assert first[0]["assembly_id"] == assembly_id
    assert first[0]["service_case_id"] == case_id
    assert first[0]["readiness_status"] == "DATA_INSUFFICIENT"
    assert first[0]["pointer_version"] == 1
    assert await pg_database._fetch_value(
        "SELECT current_assembly_id FROM public.assessment_readiness_case_pointer "
        "WHERE service_case_id=$1",
        case_id,
    ) == assembly_id
    for table in ("assessment_input_assembly", "slice4_audit", "slice4_outbox"):
        assert await pg_database._fetch_value(
            f"SELECT count(*) FROM public.{table} WHERE "
            + ("assembly_id=$1" if table == "assessment_input_assembly" else "aggregate_ref=$1"),
            assembly_id,
        ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_idempotency "
        "WHERE operation='WRITE_ASSESSMENT_ASSEMBLY' AND scope_ref=$1 AND idempotency_key=$2",
        case_id,
        idempotency_key,
    ) == 1

    from app.core.database import (
        dispose_projection_runtime,
        dispose_slice4_runtime,
        get_slice4_session_factory,
    )
    from app.modules.assessment_readiness.repository import AssessmentReadinessRepository
    from app.modules.assessment_readiness.service import recompute_assessment_readiness

    factory = await get_slice4_session_factory("assessment_readiness_writer")
    try:
        async with factory() as session:
            async with session.begin():
                recomputed = await recompute_assessment_readiness(
                    AssessmentReadinessRepository(session), service_case_id=case_id
                )
    finally:
        await dispose_projection_runtime("health_reader")
        await dispose_slice4_runtime("assessment_readiness_writer")
    assert recomputed["readiness_status"] == "DATA_INSUFFICIENT"
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.assessment_input_assembly WHERE service_case_id=$1",
        case_id,
    ) == 2

    fact_ref = generated.generate()
    initial_event_id = generated.generate()
    fact_id = await pg_database._fetch_value(
        "INSERT INTO public.canonical_health_fact(subject_user_id,indicator_code,catalog_version,"
        "value_kind,numeric_value,unit,measured_at,received_at,created_at,source_type,"
        "source_identity_digest,producer_event_key,payload_digest,digest_key_id,supersedes_fact_id,"
        "correction_reason_code,created_by,fact_ref,subject_member_id) VALUES (NULL,'fasting_glucose',"
        "2,'NUMERIC',6.10,'mmol/L',now()-interval '1 hour',now(),now(),'APP',repeat('4',64),"
        f"'slice4-state-{fact_ref}',repeat('5',64),'health-k1',NULL,NULL,{actor_user_id},"
        f"'{fact_ref}','{subject_member_id}') RETURNING id"
    )
    await pg_database._execute(
        "INSERT INTO public.health_fact_status_event(status_event_id,fact_id,event_no,"
        "predecessor_event_id,state,reason_code,actor_user_id,service_case_id,event_digest,"
        "digest_key_id,created_at) VALUES ("
        f"'{initial_event_id}',{fact_id},1,NULL,'SELF_REPORTED','INITIAL',{actor_user_id},"
        f"'{case_id}',decode(repeat('6',64),'hex'),'health-k1',now())"
    )
    state_values = (
        fact_ref,
        "VERIFIED",
        "SELF_REPORTED",
        actor_user_id + 100,
        case_id,
        1,
        "THERAPIST_VERIFIED",
        "e" * 64,
    )
    state_sql = (
        "SELECT * FROM public.slice4_health_fact_state_transition_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8)"
    )
    state_first = await health_fact_writer_database._fetch_rows(state_sql, *state_values)
    state_replay = await health_fact_writer_database._fetch_rows(state_sql, *state_values)
    assert state_first == state_replay
    assert state_first[0]["fact_ref"] == fact_ref
    assert state_first[0]["state"] == "VERIFIED"
    assert state_first[0]["event_no"] == 2
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_fact_status_event WHERE fact_id=$1", fact_id
    ) == 2
    for table in ("slice4_audit", "slice4_outbox"):
        assert await pg_database._fetch_value(
            f"SELECT count(*) FROM public.{table} WHERE aggregate_ref=$1 "
            "AND event_type='HEALTH_FACT_STATE_CHANGED'",
            fact_ref,
        ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_idempotency "
        "WHERE operation='TRANSITION_HEALTH_FACT_STATE' AND scope_ref=$1",
        fact_ref,
    ) == 1


@pytest.mark.asyncio
async def test_PG31_PG32_IdentityAuthority只返回六字段当前证据(pg_database) -> None:
    from app.core.database import dispose_slice4_runtime, get_slice4_session_factory
    from app.modules.member_enrollment.identity_authority import Slice4IdentitySummaryAuthority
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    actor_member_id = generated.generate()
    subject_member_id = generated.generate()
    _, case_id, revision_id, _ = await _seed_case(
        pg_database,
        ordinal=3,
        mode="PROXY_ELDER",
        tenant_id=98430,
        tenant_public_id=tenant_public_id,
        actor_user_id=98431,
        actor_member_id=actor_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=actor_member_id,
    )
    secrets = MemberEnrollmentSecrets()
    identity_number = "11010519491231002X"
    identity_ciphertext, identity_key_id = secrets.encrypt(
        identity_number,
        field="identity-number",
        tenant_public_id=tenant_public_id,
        object_id=revision_id,
    )
    birth_ciphertext, birth_key_id = secrets.encrypt(
        "1949-12-31",
        field="identity-birth-date",
        tenant_public_id=tenant_public_id,
        object_id=revision_id,
    )
    await pg_database._execute(
        "UPDATE public.member_identity_revision SET "
        f"id_ciphertext=decode('{identity_ciphertext.hex()}','hex'),id_key_id='{identity_key_id}',"
        f"birth_date_ciphertext=decode('{birth_ciphertext.hex()}','hex'),birth_date_key_id='{birth_key_id}' "
        f"WHERE revision_id='{revision_id}'"
    )

    factory = await get_slice4_session_factory("identity_authority")
    try:
        async with factory() as session:
            summary = await Slice4IdentitySummaryAuthority(session).verified_identity_summary(
                subject_member_id=subject_member_id,
                service_case_id=case_id,
            )
    finally:
        await dispose_slice4_runtime("identity_authority")
    assert summary.gender == "FEMALE"
    assert summary.birth_date.isoformat() == "1949-12-31"
    assert summary.identity_revision_ref == revision_id
    assert summary.source_version == 1
    assert summary.tenant_public_id == tenant_public_id
    assert summary.evidence_status == "VERIFIED"


@pytest.mark.asyncio
async def test_PG10_PG22_Clinical与Institution只经受限函数按当前Case读取(
    pg_database,
    health_record_writer_database,
    slice4_clinical_reader_database,
    slice4_institution_reader_database,
) -> None:
    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    enrollment_id, case_id, identity_revision_id, actor_user_id = await _seed_case(
        pg_database,
        ordinal=6,
        mode="SELF",
        tenant_id=99610,
        tenant_public_id=tenant_public_id,
        actor_user_id=99611,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    profile_id = generated.generate()
    profile_revision_id = generated.generate()
    await health_record_writer_database._fetch_rows(
        "SELECT * FROM public.slice4_health_profile_root_create_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamptz,"
        "$16,$17::jsonb,$18,$19,$20)",
        actor_user_id,
        "SELF",
        subject_member_id,
        case_id,
        enrollment_id,
        profile_id,
        profile_revision_id,
        tenant_public_id,
        identity_revision_id,
        1,
        b"clinical-profile-snapshot",
        "profile-k1",
        b"clinical-profile-digest",
        "digest-k1",
        datetime(2026, 8, 21, tzinfo=timezone.utc),
        "APP",
        '["medical_history"]',
        generated.generate(),
        b"clinical-request-digest",
        b"clinical-postimage-digest",
    )
    clinical = await slice4_clinical_reader_database._fetch_rows(
        "SELECT * FROM public.slice4_clinical_profile_read_v1($1,$2,$3,$4,$5)",
        actor_user_id,
        "SELF",
        subject_member_id,
        case_id,
        enrollment_id,
    )
    assert len(clinical) == 1
    assert clinical[0]["profile_public_id"] == profile_id
    assert clinical[0]["subject_ref"] == subject_member_id
    with pytest.raises(Exception):
        await slice4_clinical_reader_database._fetch_rows(
            "SELECT * FROM public.slice4_clinical_profile_read_v1($1,$2,$3,$4,$5)",
            actor_user_id + 999,
            "SELF",
            subject_member_id,
            case_id,
            enrollment_id,
        )

    institution_actor_id = actor_user_id + 200
    summary = await slice4_institution_reader_database._fetch_rows(
        "SELECT * FROM public.slice4_institution_health_read_v1($1,$2,$3,$4::jsonb)",
        institution_actor_id,
        case_id,
        "HEALTH_RECORD",
        "{}",
    )
    assert len(summary) == 1
    assert summary[0]["case_id"] == case_id
    assert summary[0]["subject_ref"] == subject_member_id
    assert summary[0]["profile_complete"] is True
    with pytest.raises(Exception):
        await slice4_institution_reader_database._fetch_rows(
            "SELECT * FROM public.slice4_institution_health_read_v1($1,$2,$3,$4::jsonb)",
            actor_user_id,
            case_id,
            "HEALTH_RECORD",
            "{}",
        )


def test_PG29_PG31_六身份函数与基础表权限精确隔离(
    pg_database,
    health_record_writer_database,
    assessment_readiness_writer_database,
    slice4_workflow_worker_database,
    slice4_clinical_reader_database,
    slice4_institution_reader_database,
    slice4_identity_authority_database,
) -> None:
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260821_0024"
    databases = (
        health_record_writer_database,
        assessment_readiness_writer_database,
        slice4_workflow_worker_database,
        slice4_clinical_reader_database,
        slice4_institution_reader_database,
        slice4_identity_authority_database,
    )
    expected_roles = tuple(
        _role(name)
        for name in (
            "KG_TEST_HEALTH_RECORD_WRITER_ROLE",
            "KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE",
            "KG_TEST_SLICE4_WORKFLOW_WORKER_ROLE",
            "KG_TEST_SLICE4_CLINICAL_READER_ROLE",
            "KG_TEST_SLICE4_INSTITUTION_READER_ROLE",
            "KG_TEST_SLICE4_IDENTITY_AUTHORITY_ROLE",
        )
    )
    assert tuple(database.fetch_value("SELECT current_user") for database in databases) == expected_roles
    assert len(set(expected_roles)) == 6

    writer, readiness, worker, clinical, institution, identity = expected_roles
    functions = {
        "root": "public.slice4_health_profile_root_create_v1(bigint,character varying,uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,bytea,character varying,bytea,character varying,timestamp with time zone,character varying,jsonb,uuid,bytea,bytea)",
        "report": "public.slice4_detection_report_create_v1(bigint,character varying,uuid,uuid,uuid,uuid,character varying,timestamp with time zone,character varying,uuid[],uuid,bytea,bytea)",
        "state": "public.slice4_health_fact_state_transition_v1(uuid,character varying,character varying,bigint,uuid,bigint,character varying,character varying)",
        "assembly": "public.slice4_assessment_assembly_write_v1(uuid,uuid,uuid,uuid,uuid,uuid,uuid,smallint,character varying,character varying,jsonb,jsonb,character varying,jsonb,uuid,bytea,bytea)",
        "source": "public.slice4_identity_summary_source_v1(uuid,uuid)",
        "current": "public.slice4_identity_summary_current_v1(uuid,uuid,uuid,bigint,uuid)",
        "readiness": "public.slice4_assessment_readiness_read_v1(uuid)",
    }
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{writer}','{functions['root']}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{writer}','{functions['report']}','EXECUTE')"
    )
    health_fact_writer = _role("KG_TEST_HEALTH_FACT_WRITER_ROLE")
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{health_fact_writer}','{functions['state']}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{readiness}','{functions['assembly']}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{identity}','{functions['source']}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{readiness}','{functions['current']}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{clinical}','{functions['readiness']}','EXECUTE')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{institution}','{functions['readiness']}','EXECUTE')"
    )

    for role in (readiness, worker, clinical, institution, identity):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{functions['root']}','EXECUTE')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{functions['report']}','EXECUTE')"
        )
    for role in (writer, readiness, worker, clinical, institution, identity):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{functions['state']}','EXECUTE')"
        )
    for role in (writer, worker, clinical, institution, identity):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{functions['assembly']}','EXECUTE')"
        )
    for role in (writer, readiness, worker, clinical, institution):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{functions['source']}','EXECUTE')"
        )
    for role in (worker, clinical, institution, identity):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}','{functions['current']}','EXECUTE')"
        )
    for signature in functions.values():
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('public','{signature}','EXECUTE')"
        )

    health_reader = _role("KG_TEST_HEALTH_PROJECTION_READER_ROLE")
    health_builder = _role("KG_TEST_HEALTH_PROJECTION_BUILDER_ROLE")
    evidence_roles = {
        _role("KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE"),
        _role("KG_TEST_PROJECTION_READY_GATE_ROLE"),
        _role("KG_TEST_PROJECTION_CONFIRMATION_ROLE"),
        _role("KG_TEST_PROJECTION_SHADOW_CONFIRMATION_ROLE"),
    }
    extended = {
        "subject": "public.slice4_subject_authority_v1(uuid,uuid,bigint,character varying)",
        "readiness_current": "public.slice4_readiness_currentness_v1(uuid,bigint)",
        "coverage": "public.slice4_projection_coverage_v2(uuid,jsonb)",
        "file_authority": "public.slice4_report_file_authority_v1(uuid,uuid,bigint,character varying)",
        "clinical_profile": "public.slice4_clinical_profile_read_v1(bigint,character varying,uuid,uuid,uuid)",
        "clinical_report": "public.slice4_clinical_report_read_v1(bigint,character varying,uuid,uuid,uuid,jsonb)",
        "clinical_fact": "public.slice4_clinical_fact_read_v1(bigint,character varying,uuid,uuid,uuid,jsonb)",
        "institution": "public.slice4_institution_health_read_v1(bigint,uuid,character varying,jsonb)",
        "builder": "public.health_projection_builder_source_v2(bigint,jsonb,character varying)",
        "evidence": "public.health_projection_subject_evidence_verify_v2(bigint)",
        "profile_confirm": "public.slice4_profile_confirm_v1(uuid,uuid,uuid)",
        "report_confirm": "public.slice4_report_confirm_v1(uuid,uuid,uuid)",
        "fact_confirm": "public.slice4_health_fact_confirm_v1(uuid,uuid,uuid)",
        "assembly_confirm": "public.slice4_assembly_confirm_v1(uuid,uuid,uuid)",
    }
    allowed = {
        "subject": {writer, readiness, clinical},
        "readiness_current": {readiness, worker},
        "coverage": {health_reader},
        "file_authority": {writer, clinical},
        "clinical_profile": {clinical},
        "clinical_report": {clinical},
        "clinical_fact": {clinical},
        "institution": {institution},
        "builder": {health_builder},
        "evidence": evidence_roles,
        "profile_confirm": {writer},
        "report_confirm": {writer},
        "fact_confirm": {health_fact_writer},
        "assembly_confirm": {readiness},
    }
    checked_roles = {
        writer, readiness, worker, clinical, institution, identity,
        health_fact_writer, health_reader, health_builder, *evidence_roles,
    }
    for key, signature in extended.items():
        for role in checked_roles:
            assert pg_database.fetch_value(
                f"SELECT has_function_privilege('{role}','{signature}','EXECUTE')"
            ) is (role in allowed[key])
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('public','{signature}','EXECUTE')"
        )

    source_views = (
        "health_projection_source_visibility_v2",
        "health_projection_status_visibility_v2",
        "slice4_projection_coverage_source_v2",
    )
    for view in source_views:
        for role in checked_roles:
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{view}','SELECT')"
            )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('public','public.{view}','SELECT')"
        )
    assert pg_database.fetch_value(
        f"SELECT has_table_privilege('{health_reader}',"
        "'public.health_ready_projection_resolution_v2','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_table_privilege('{health_reader}',"
        "'public.health_ready_subject_indicator_evidence_v2','SELECT')"
    )
    assert pg_database.fetch_value(
        f"SELECT has_table_privilege('{worker}','public.slice4_recompute_candidate_v1','SELECT')"
    )

    for role in expected_roles:
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.health_profile','INSERT')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_sequence_privilege('{role}','public.health_profile_id_seq','USAGE')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.member_identity_revision','SELECT')"
        )
    for role in (readiness, worker, clinical, institution, identity):
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{role}','public.health_profile_revision','SELECT')"
        )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{writer}','public.detection_report','INSERT')"
    )
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{writer}','public.detection_report_attachment','INSERT')"
    )
    for table in (
        "assessment_input_assembly",
        "assessment_input_assembly_fact",
        "assessment_readiness_case_pointer",
    ):
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{readiness}','public.{table}','INSERT')"
        )
        assert not pg_database.fetch_value(
            f"SELECT has_table_privilege('{readiness}','public.{table}','UPDATE')"
        )
    sequences = (
        "public.health_profile_id_seq",
        "public.detection_report_id_seq",
        "public.health_fact_status_event_status_event_seq_seq",
        "public.assessment_input_assembly_assembly_seq_seq",
    )
    for role in (*expected_roles, health_fact_writer):
        for sequence in sequences:
            for privilege in ("USAGE", "SELECT", "UPDATE"):
                assert not pg_database.fetch_value(
                    f"SELECT has_sequence_privilege('{role}','{sequence}','{privilege}')"
                )


def test_PG28_PG36_受限函数owner不是Runtime且固定search_path(pg_database) -> None:
    runtime_roles = {
        _role(name)
        for name in (
            "KG_TEST_HEALTH_RECORD_WRITER_ROLE",
            "KG_TEST_ASSESSMENT_READINESS_WRITER_ROLE",
            "KG_TEST_SLICE4_WORKFLOW_WORKER_ROLE",
            "KG_TEST_SLICE4_CLINICAL_READER_ROLE",
            "KG_TEST_SLICE4_INSTITUTION_READER_ROLE",
            "KG_TEST_SLICE4_IDENTITY_AUTHORITY_ROLE",
        )
    }
    rows = pg_database.fetch_rows(
        "SELECT p.proname,r.rolname AS owner,p.prosecdef,p.proconfig "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_roles r ON r.oid=p.proowner "
        "WHERE n.nspname='public' AND p.proname LIKE 'slice4_%'"
    )
    assert rows
    for row in rows:
        assert row["owner"] not in runtime_roles
        assert row["prosecdef"] is True
        assert "search_path=pg_catalog, pg_temp" in row["proconfig"]


@pytest.mark.asyncio
async def test_D01_D13_PG10_本人正式HTTP原子创建Profile并可稳定重放(
    pg_database,
    real_db_client,
) -> None:
    from app.core.security import create_access_token
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    _, _, revision_id, actor_user_id = await _seed_case(
        pg_database,
        ordinal=7,
        mode="SELF",
        tenant_id=99630,
        tenant_public_id=tenant_public_id,
        actor_user_id=99631,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    secrets = MemberEnrollmentSecrets()
    identity_ciphertext, identity_key_id = secrets.encrypt(
        "11010519491231002X",
        field="identity-number",
        tenant_public_id=tenant_public_id,
        object_id=revision_id,
    )
    birth_ciphertext, birth_key_id = secrets.encrypt(
        "1949-12-31",
        field="identity-birth-date",
        tenant_public_id=tenant_public_id,
        object_id=revision_id,
    )
    await pg_database._execute(
        "UPDATE public.member_identity_revision SET "
        f"id_ciphertext=decode('{identity_ciphertext.hex()}','hex'),id_key_id='{identity_key_id}',"
        f"birth_date_ciphertext=decode('{birth_ciphertext.hex()}','hex'),birth_date_key_id='{birth_key_id}' "
        f"WHERE revision_id='{revision_id}'"
    )
    authorization = {
        "Authorization": f"Bearer {create_access_token({'sub': str(actor_user_id), 'role': 'member'})}"
    }
    headers = {**authorization, "Idempotency-Key": "slice4-profile-root-http-0009"}
    payload = {
        "medical_history": [
            {
                "code": "hypertension",
                "onset_date": "2020-01-01",
                "resolved_date": None,
                "status": "ACTIVE",
                "note": None,
            }
        ],
        "allergies": [],
        "medications": [],
        "symptoms": [],
        "pregnancy_status": "NOT_APPLICABLE",
        "pregnancy_week": None,
        "lactation_status": "NOT_APPLICABLE",
        "reconfirmed_at": "2026-08-20T09:00:00+08:00",
        "source_type": "APP",
        "expected_version": 0,
    }
    first = real_db_client.put("/api/v1/family/health-profile", headers=headers, json=payload)
    assert first.status_code == 200, first.json()
    replay = real_db_client.put("/api/v1/family/health-profile", headers=headers, json=payload)
    assert replay.status_code == 200 and replay.json() == first.json()
    read = real_db_client.get("/api/v1/family/health-profile", headers=authorization)
    assert read.status_code == 200 and read.json() == first.json()
    assert UUID(first.json()["profile_id"]).version == 7
    assert UUID(first.json()["revision_id"]).version == 7
    assert first.json()["identity_summary"]["source_version"] == 1
    profile_id = await pg_database._fetch_value(
        "SELECT profile_public_id FROM public.health_profile WHERE subject_member_id=$1",
        subject_member_id,
    )
    assert profile_id is not None
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_profile_revision WHERE subject_member_id=$1",
        subject_member_id,
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1", profile_id
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref=$1", profile_id
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref=$1", subject_member_id
    ) == 1


@pytest.mark.asyncio
async def test_D11_PG06_本人正式HTTP创建报告重放并可读取非空列表(
    pg_database,
    real_db_client,
) -> None:
    from app.core.security import create_access_token

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    _, _, _, actor_user_id = await _seed_case(
        pg_database,
        ordinal=0,
        mode="SELF",
        tenant_id=99640,
        tenant_public_id=tenant_public_id,
        actor_user_id=99641,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    file_id = generated.generate()
    await pg_database._execute(
        "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,"
        "declared_mime_type,declared_sha256,actual_size,actual_mime_type,actual_sha256,"
        "object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) "
        f"VALUES('{file_id}','DETECTION_REPORT',{actor_user_id},8,'application/pdf',"
        "repeat('a',64),8,'application/pdf',repeat('a',64),"
        f"'slice4-http-report-{file_id}','CLEAN',NULL,now(),"
        "now()+interval '1 day',now(),NULL,NULL)"
    )
    authorization = {
        "Authorization": f"Bearer {create_access_token({'sub': str(actor_user_id), 'role': 'member'})}"
    }
    headers = {**authorization, "Idempotency-Key": "slice4-report-http-0010"}
    payload = {
        "report_type": "LAB_REPORT",
        "measured_at": "2026-08-20T09:00:00+08:00",
        "source_type": "APP",
        "file_ids": [str(file_id)],
    }
    first = real_db_client.post("/api/v1/family/detection-reports", headers=headers, json=payload)
    assert first.status_code == 201, first.json()
    replay = real_db_client.post("/api/v1/family/detection-reports", headers=headers, json=payload)
    assert replay.status_code == 201 and replay.json() == first.json()
    report_id = UUID(first.json()["report_id"])
    assert report_id.version == 7
    page = real_db_client.get("/api/v1/family/detection-reports", headers=authorization)
    assert page.status_code == 200
    assert [item["report_id"] for item in page.json()["items"]] == [str(report_id)]
    detail = real_db_client.get(
        f"/api/v1/family/detection-reports/{report_id}",
        headers=authorization,
    )
    assert detail.status_code == 200
    assert detail.json()["attachments"] == [
        {"file_id": str(file_id), "mime_type": "application/pdf", "size": 8, "status": "CLEAN"}
    ]
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.detection_report WHERE report_id=$1", report_id
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1", report_id
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref=$1", report_id
    ) == 1


@pytest.mark.asyncio
async def test_D05_D07_D10_本人正式HTTP追加事实状态与重放保持原子(
    pg_database,
    real_db_client,
) -> None:
    from app.core.security import create_access_token

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    _, _, _, actor_user_id = await _seed_case(
        pg_database,
        ordinal=8,
        mode="SELF",
        tenant_id=99650,
        tenant_public_id=tenant_public_id,
        actor_user_id=99651,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    authorization = {
        "Authorization": f"Bearer {create_access_token({'sub': str(actor_user_id), 'role': 'member'})}"
    }
    headers = {**authorization, "Idempotency-Key": "slice4-fact-http-0010"}
    payload = {
        "items": [
            {
                "indicator_code": "weight",
                "value": "70.20",
                "unit": "kg",
                "measured_at": "2026-08-20T09:00:00+08:00",
                "source_type": "APP",
                "report_id": None,
            }
        ]
    }
    first = real_db_client.post(
        "/api/v1/family/health-indicators", headers=headers, json=payload
    )
    assert first.status_code == 201, first.json()
    replay = real_db_client.post(
        "/api/v1/family/health-indicators", headers=headers, json=payload
    )
    assert replay.status_code == 201 and replay.json() == first.json()
    item = first.json()["items"][0]
    fact_ref = UUID(item["fact_ref"])
    assert fact_ref.version == 7
    assert item["verification_state"] == "SELF_REPORTED"
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.canonical_health_fact WHERE fact_ref=$1", fact_ref
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_fact_status_event e "
        "JOIN public.canonical_health_fact f ON f.id=e.fact_id WHERE f.fact_ref=$1",
        fact_ref,
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1", fact_ref
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_outbox WHERE aggregate_ref=$1", fact_ref
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_idempotency WHERE scope_ref=$1", fact_ref
    ) == 1


@pytest.mark.asyncio
async def test_PG07_D16_D19_真实HealthReader按Member双水位解析READY并读取(
    pg_database,
    real_db_client,
) -> None:
    import asyncpg
    from app.core.security import create_access_token

    async def execute(sql: str, *args) -> None:
        connection = await asyncpg.connect(pg_database.database_url)
        try:
            await connection.execute(sql, *args)
        finally:
            await connection.close()

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    _, _, _, actor_user_id = await _seed_case(
        pg_database,
        ordinal=9,
        mode="SELF",
        tenant_id=110050,
        tenant_public_id=tenant_public_id,
        actor_user_id=110051,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    authorization = {
        "Authorization": f"Bearer {create_access_token({'sub': str(actor_user_id), 'role': 'member'})}"
    }
    created = real_db_client.post(
        "/api/v1/family/health-indicators",
        headers={**authorization, "Idempotency-Key": "slice4-ready-reader-0001"},
        json={
            "items": [{
                "indicator_code": "weight", "value": "71.20", "unit": "kg",
                "measured_at": "2026-08-20T10:00:00+08:00",
                "source_type": "APP", "report_id": None,
            }]
        },
    )
    assert created.status_code == 201, created.json()
    fact_ref = UUID(created.json()["items"][0]["fact_ref"])
    fact_id = await pg_database._fetch_value(
        "SELECT id FROM public.canonical_health_fact WHERE fact_ref=$1", fact_ref
    )
    status_seq = await pg_database._fetch_value(
        "SELECT e.status_event_seq FROM public.health_fact_status_event e "
        "JOIN public.canonical_health_fact f ON f.id=e.fact_id WHERE f.fact_ref=$1", fact_ref
    )
    digest = "a" * 64
    upper_digest = "A" * 64
    generation_id = await pg_database._fetch_value(
        "INSERT INTO public.health_projection_generation("
        "projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,"
        "start_operation_id,builder_id,lease_epoch,lease_expires_at,completed_at,version) "
        "VALUES(2,9001,'BUILD_COMPLETE',jsonb_build_object('max_fact_id',$1::bigint,"
        "'max_status_event_seq',$2::bigint,'source_snapshot','slice4:ready:9'),'k1',$3,$4,"
        "NULL,0,NULL,now(),1) RETURNING id",
        fact_id, status_seq, digest, generated.generate(),
    )
    run_id = generated.generate()
    await execute(
        "INSERT INTO public.health_projection_shadow_run("
        "run_id,generation_id,run_sequence,status,projection_version,rule_version,high_watermark,"
        "high_watermark_digest,digest_key_id,generation_input_digest,source_digest,mapping_digest,"
        "projection_digest,coverage_digest,evidence_digest,currentness_digest,selection_digest,"
        "blocker_count,review_required_count,informational_count,category_counts,source_count,"
        "current_fact_count,projection_fact_count,expected_selection_count,actual_selection_count,"
        "fact_coverage_numerator,fact_coverage_denominator,selection_coverage_numerator,"
        "selection_coverage_denominator,status_event_count,start_operation_id,complete_operation_id,"
        "validator_id,lease_epoch,lease_expires_at,completed_at,version) VALUES("
        "$1,$2,1,'PASSED',2,'health-daily-selection-v2',jsonb_build_object('max_fact_id',$3::bigint,"
        "'max_status_event_seq',$4::bigint,'source_snapshot','slice4:ready:9'),$5,'k1',$6,"
        "$5,$5,$5,$5,$5,$5,$5,0,0,0,'{}'::jsonb,1,1,1,1,1,1,1,1,1,1,$7,$8,$9,0,NULL,now(),1)",
        run_id, generation_id, fact_id, status_seq, upper_digest, digest,
        generated.generate(), generated.generate(), generated.generate(),
    )
    measured_at = await pg_database._fetch_value(
        "SELECT measured_at FROM public.canonical_health_fact WHERE id=$1", fact_id
    )
    await execute(
        "INSERT INTO public.health_projection_fact("
        "generation_id,fact_id,subject_user_id,subject_member_id,fact_ref,status_event_seq,"
        "indicator_code,numeric_value,unit,measured_at,received_at,source_type,business_day,"
        "window_start_utc,window_end_utc,row_digest,digest_key_id) SELECT "
        "$1,f.id,f.subject_user_id,f.subject_member_id,f.fact_ref,$2,f.indicator_code,"
        "f.numeric_value,f.unit,f.measured_at,f.received_at,f.source_type,"
        "(f.measured_at AT TIME ZONE 'Asia/Shanghai')::date,"
        "(((f.measured_at AT TIME ZONE 'Asia/Shanghai')::date)::timestamp AT TIME ZONE 'Asia/Shanghai'),"
        "((((f.measured_at AT TIME ZONE 'Asia/Shanghai')::date+1)::timestamp) AT TIME ZONE 'Asia/Shanghai'),"
        "$3,'k1' FROM public.canonical_health_fact f WHERE f.id=$4",
        generation_id, status_seq, digest, fact_id,
    )
    fact_count = await pg_database._fetch_value(
        "SELECT fact_count FROM public.slice4_projection_coverage_source_v2 "
        "WHERE subject_member_id=$1 AND indicator_code='weight'", subject_member_id
    )
    status_count = await pg_database._fetch_value(
        "SELECT status_event_count FROM public.slice4_projection_coverage_source_v2 "
        "WHERE subject_member_id=$1 AND indicator_code='weight'", subject_member_id
    )
    fact_digest = await pg_database._fetch_value(
        "SELECT fact_set_digest FROM public.slice4_projection_coverage_source_v2 "
        "WHERE subject_member_id=$1 AND indicator_code='weight'", subject_member_id
    )
    status_digest = await pg_database._fetch_value(
        "SELECT status_set_digest FROM public.slice4_projection_coverage_source_v2 "
        "WHERE subject_member_id=$1 AND indicator_code='weight'", subject_member_id
    )
    await execute(
        "INSERT INTO public.health_projection_subject_indicator_evidence_v2("
        "generation_id,subject_member_id,indicator_code,fact_count,status_event_count,max_fact_id,"
        "max_status_event_seq,source_snapshot,fact_set_digest,status_set_digest,evidence_digest,"
        "digest_key_id,created_at) VALUES($1,$2,'weight',$3,$4,$5,$6,'slice4:ready:9',"
        "$7,$8,$9,'k1',now())",
        generation_id, subject_member_id, fact_count, status_count, fact_id, status_seq,
        fact_digest, status_digest, digest,
    )
    await execute(
        "UPDATE public.health_projection_generation SET status='READY',current_shadow_run_id=$1,"
        "shadow_success_count=2,ready_at=now(),ready_operation_id=$2,version=2 WHERE id=$3",
        run_id, generated.generate(), generation_id,
    )
    response = real_db_client.get(
        "/api/v1/family/health-indicators/history?indicator_code=weight",
        headers=authorization,
    )
    assert response.status_code == 200, response.json()
    assert response.json()["items"][0]["fact_ref"] == str(fact_ref)
    assert response.json()["items"][0]["verification_state"] == "SELF_REPORTED"
