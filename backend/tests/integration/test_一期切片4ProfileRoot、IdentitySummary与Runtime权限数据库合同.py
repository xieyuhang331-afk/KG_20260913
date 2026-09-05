from __future__ import annotations

import json
import hashlib
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
    digest_digit = format(ordinal % 16, "x")
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
        f"'{invitation_id}',{tenant_id},'{mode}',decode('00','hex'),'k1',repeat('a',63)||'{digest_digit}',"
        f"'k1','*******0000',repeat('b',63)||'{digest_digit}','k1','ACCEPTED',0,now()+interval '1 day',"
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
        f"'k1',decode('02','hex'),'k1','**************0000',repeat('c',63)||'{digest_digit}','k1',"
        f"repeat('d',63)||'{digest_digit}','{subject_member_id}',now());"
        "INSERT INTO public.member_identity_review_decision(decision_id,verification_id,revision_id,phase,"
        "reviewer_user_id,decision,reason_code,correction_fields,attestation_code,represented_elder_eligible,"
        "request_digest,evidence_digest,created_at) VALUES ("
        f"'{decision_id}','{verification_id}','{revision_id}','PLATFORM',{reviewer_user_id},'APPROVED',"
        f"'OFFLINE_VERIFIED',NULL,'SLICE4',true,repeat('e',63)||'{digest_digit}',repeat('f',63)||'{digest_digit}',now());"
        "INSERT INTO identity.identity_subject_claim_registry(claim_id,identity_fingerprint,fingerprint_key_id,"
        "user_ref,member_id,source_kind,p1_submission_id,p1_decision_ref,slice3_revision_id,slice3_decision_id,"
        "source_facts_version,source_evidence_digest,adult_eligible,represented_elder_eligible,claimed_at,version) VALUES ("
        f"'{claim_id}',repeat('{digest_digit}',64),'k1',NULL,'{subject_member_id}','SLICE3',NULL,NULL,"
        f"'{revision_id}','{decision_id}',1,repeat('1',64),NULL,true,now(),1);"
        "INSERT INTO public.therapist_invitation(invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,"
        "phone_digest,phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,expires_at,status,"
        "failed_attempts,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{therapist_invitation_id}',{tenant_id},decode('00','hex'),'k1',repeat('2',63)||'{digest_digit}','k1',"
        f"'*******0000',repeat('3',63)||'{digest_digit}','k1',now()+interval '1 day','ACTIVATED',0,"
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
            0,
            idempotency_key,
            b"request-digest-value",
            b"expected-postimage-value",
        )
        sql = (
            "SELECT * FROM public.slice4_health_profile_root_create_v1("
            "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamptz,"
            "$16,$17::jsonb,$18,$19,$20,$21)"
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
    health_fact_writer_database,
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
    authority_sql = (
        "SELECT public.slice4_report_fact_authority_v1($1,$2,$3)"
    )
    assert await health_fact_writer_database._fetch_value(
        authority_sql, requested_report_id, subject_member_id, case_id
    ) is True
    assert await health_fact_writer_database._fetch_value(
        "SELECT has_table_privilege(current_user,'public.detection_report','SELECT')"
    ) is False


@pytest.mark.asyncio
async def test_最终整改_C_REPORT事实权威只允许同TenantMemberCase的CLEAN报告(
    pg_database,
    health_fact_writer_database,
) -> None:
    generated = Uuid7Generator()
    first_member = generated.generate()
    second_member = generated.generate()
    first_tenant = generated.generate()
    second_tenant = generated.generate()
    _, first_case, _, first_user = await _seed_case(
        pg_database,
        ordinal=11,
        mode="SELF",
        tenant_id=141010,
        tenant_public_id=first_tenant,
        actor_user_id=141011,
        actor_member_id=first_member,
        subject_member_id=first_member,
        proxy_member_id=None,
    )
    _, second_case, _, _ = await _seed_case(
        pg_database,
        ordinal=12,
        mode="SELF",
        tenant_id=142010,
        tenant_public_id=second_tenant,
        actor_user_id=142011,
        actor_member_id=second_member,
        subject_member_id=second_member,
        proxy_member_id=None,
    )
    report_id = generated.generate()
    await pg_database._execute(
        "INSERT INTO public.detection_report(user_id,store_id,report_type,detection_time,report_data,"
        "report_id,subject_member_id,tenant_id,service_case_id,schema_version,source_type,"
        "measured_at,received_at,report_status,version,supersedes_report_id,created_by) "
        f"VALUES(NULL,NULL,'LAB_REPORT',now(),'{{}}'::jsonb,'{report_id}','{first_member}',141010,"
        f"'{first_case}',1,'APP',now(),now(),'CLEAN',1,NULL,{first_user})"
    )
    authority_sql = "SELECT public.slice4_report_fact_authority_v1($1,$2,$3)"
    assert await health_fact_writer_database._fetch_value(
        authority_sql, report_id, first_member, first_case
    ) is True
    for member_id, case_id in (
        (second_member, first_case),
        (first_member, second_case),
        (second_member, second_case),
    ):
        assert await health_fact_writer_database._fetch_value(
            authority_sql, report_id, member_id, case_id
        ) is False
    assert await health_fact_writer_database._fetch_value(
        authority_sql, generated.generate(), first_member, first_case
    ) is False
    before = {
        table: await pg_database._fetch_value(f"SELECT count(*) FROM public.{table}")
        for table in ("canonical_health_fact", "slice4_audit", "slice4_outbox")
    }
    await pg_database._execute(
        f"UPDATE public.detection_report SET report_status='SUPERSEDED' "
        f"WHERE report_id='{report_id}'"
    )
    assert await health_fact_writer_database._fetch_value(
        authority_sql, report_id, first_member, first_case
    ) is False
    assert {
        table: await pg_database._fetch_value(f"SELECT count(*) FROM public.{table}")
        for table in before
    } == before


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
    await pg_database._execute(
        "INSERT INTO public.institution_service_readiness(tenant_id,readiness_status,"
        "reason_codes,qualified_therapist_count,computed_at,evidence_version,input_digest,"
        "result_digest,source_versions,next_expiry_at,version) VALUES ("
        "98810,'SERVICE_READY','{}'::text[],1,now(),1,repeat('7',64),repeat('5',64),"
        "'{}'::jsonb,current_date+365,1)"
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
        0,
        generated.generate(),
        b"profile-request-digest",
        b"profile-postimage-digest",
    )
    await health_record_writer_database._fetch_rows(
        "SELECT * FROM public.slice4_health_profile_root_create_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamptz,"
        "$16,$17::jsonb,$18,$19,$20,$21)",
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
        "'[\"fasting_glucose\"]'::jsonb,'[\"VERIFIED\"]'::jsonb,2,'health-daily-selection-v2',true,"
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
        "health-daily-selection-v2",
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
        "correction_reason_code,created_by,fact_ref,subject_member_id,measurement_context) "
        "VALUES (NULL,'fasting_glucose',"
        "2,'NUMERIC',6.10,'mmol/L',now()-interval '1 hour',now(),now(),'APP',repeat('4',64),"
        f"'slice4-state-{fact_ref}',repeat('5',64),'health-k1',NULL,NULL,{actor_user_id},"
        f"'{fact_ref}','{subject_member_id}','FASTING_VENOUS') RETURNING id"
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

    document_version_id = generated.generate()
    rendition_id = generated.generate()
    consent_record_id = generated.generate()
    await pg_database._execute(
        "INSERT INTO public.consent_document_version(document_version_id,document_type,"
        "semantic_version,status,requires_reconsent,manifest_digest,effective_at,retired_at,"
        "published_by,created_at,version) VALUES ("
        f"'{document_version_id}','ASSESSMENT_CONSENT','slice4-ready-v1','PUBLISHED',true,"
        f"repeat('8',64),now()-interval '1 minute',NULL,{reviewer_user_id},now(),1);"
        "INSERT INTO public.consent_document_rendition(rendition_id,document_version_id,locale,"
        "title,body,content_sha256,created_at) VALUES ("
        f"'{rendition_id}','{document_version_id}','zh-CN','Slice4 Consent','Synthetic consent',"
        "repeat('9',64),now());"
        "INSERT INTO public.consent_record(consent_record_id,enrollment_id,subject_member_id,"
        "proxy_member_id,document_type,document_version_id,rendition_id,purpose_codes,choice,"
        "status,predecessor_id,presented_at,accepted_at,withdrawn_at,version) VALUES ("
        f"'{consent_record_id}','{enrollment_id}','{subject_member_id}',NULL,'ASSESSMENT_CONSENT',"
        f"'{document_version_id}','{rendition_id}','[\"ASSESSMENT\"]'::jsonb,'ACCEPTED',"
        "'ACCEPTED',NULL,now()-interval '2 minutes',now()-interval '1 minute',NULL,1)"
    )

    from app.tasks import slice4_health_data_tasks as projection_tasks

    try:
        generation_id = await projection_tasks._build_projection_v2(
            generation_no=8805,
            builder_id=generated.generate(),
            operation_id=generated.generate(),
        )
    finally:
        for kind in (
            "health", "confirmation", "health_shadow", "ready_gate",
            "shadow_confirmation",
        ):
            await dispose_projection_runtime(kind)
    assert await pg_database._fetch_value(
        "SELECT status FROM public.health_projection_generation WHERE id=$1",
        generation_id,
    ) == "READY"

    factory = await get_slice4_session_factory("assessment_readiness_writer")
    try:
        async with factory() as session:
            async with session.begin():
                ready = await recompute_assessment_readiness(
                    AssessmentReadinessRepository(session), service_case_id=case_id
                )
    finally:
        await dispose_projection_runtime("health_reader")
        await dispose_slice4_runtime("assessment_readiness_writer")
    assert ready["readiness_status"] == "ASSESSMENT_READY"
    ready_assembly = (await pg_database._fetch_rows(
        "SELECT resolved_generation_id,required_max_fact_id,required_max_status_event_seq "
        "FROM public.assessment_input_assembly WHERE assembly_id=$1",
        ready["assembly_id"],
    ))[0]
    assert ready_assembly["resolved_generation_id"] == generation_id
    assert ready_assembly["required_max_fact_id"] >= fact_id
    assert ready_assembly["required_max_status_event_seq"] >= state_first[0]["status_event_seq"]

    stored = (await pg_database._fetch_rows(
        "SELECT * FROM public.assessment_input_assembly WHERE assembly_id=$1",
        ready["assembly_id"],
    ))[0]
    stored_facts = await pg_database._fetch_rows(
        "SELECT f.*,p.status_event_seq FROM public.assessment_input_assembly_fact f "
        "JOIN public.health_ready_projection_fact_v2 p "
        "ON p.generation_id=$2 AND p.fact_ref=f.fact_ref "
        "WHERE f.assembly_id=$1 ORDER BY f.indicator_code",
        ready["assembly_id"], generation_id,
    )
    assert stored_facts[0]["measurement_context"] == "FASTING_VENOUS"

    def _json(value):
        return json.loads(value) if isinstance(value, str) else value

    base_vector = {
        "consent_version_ids": _json(stored["consent_version_ids"]),
        "resolved_generation_id": str(stored["resolved_generation_id"]),
        "required_max_fact_id": stored["required_max_fact_id"],
        "required_max_status_event_seq": stored["required_max_status_event_seq"],
        "missing_codes": _json(stored["missing_codes"]),
        "expired_codes": _json(stored["expired_codes"]),
        "disputed_codes": _json(stored["disputed_codes"]),
        "source_vector_digest": bytes(stored["source_vector_digest"]).hex(),
        "source_digest": bytes(stored["source_digest"]).hex(),
        "assembly_digest": bytes(stored["assembly_digest"]).hex(),
        "digest_key_id": stored["digest_key_id"],
        "generated_at": stored["generated_at"].isoformat(),
    }
    base_fact_rows = [{
        "indicator_code": row["indicator_code"],
        "fact_ref": str(row["fact_ref"]),
        "measured_at": row["measured_at"].isoformat(),
        "received_at": row["received_at"].isoformat(),
        "source_type": row["source_type"],
        "verification_state": row["verification_state"],
        "status_event_seq": row["status_event_seq"],
        "value_ciphertext": bytes(row["value_ciphertext"]).hex(),
        "value_key_id": row["value_key_id"],
        "unit": row["unit"],
        "row_digest": bytes(row["row_digest"]).hex(),
    } for row in stored_facts]

    async def assert_ready_rejected(*, vector=None, facts=None, profile_revision=profile_revision_id):
        rejected_assembly_id = generated.generate()
        audit_id = generated.generate()
        event_id = generated.generate()
        receipt_id = generated.generate()
        candidate = dict(base_vector if vector is None else vector)
        candidate.update({
            "audit_id": str(audit_id),
            "event_id": str(event_id),
            "receipt_id": str(receipt_id),
        })
        with pytest.raises(Exception, match="SLICE4_ASSEMBLY_CURRENTNESS_INVALID"):
            await assessment_readiness_writer_database._fetch_rows(
                sql,
                case_id, rejected_assembly_id, subject_member_id, tenant_public_id,
                therapist_id, profile_revision, policy_version_id, 2,
                "health-daily-selection-v2", stored["source_snapshot"],
                json.dumps(candidate, separators=(",", ":")),
                json.dumps(base_fact_rows if facts is None else facts, separators=(",", ":")),
                "ASSESSMENT_READY", "[]", generated.generate(),
                b"rejected-request-digest", b"rejected-postimage-digest",
            )
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.assessment_input_assembly WHERE assembly_id=$1",
            rejected_assembly_id,
        ) == 0
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.slice4_audit WHERE audit_id=$1", audit_id
        ) == 0
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.slice4_outbox WHERE event_id=$1", event_id
        ) == 0
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.slice4_idempotency WHERE receipt_id=$1", receipt_id
        ) == 0

    tampered_vector = dict(base_vector)
    tampered_vector["required_max_fact_id"] += 1
    await assert_ready_rejected(vector=tampered_vector)
    tampered_facts = [dict(item) for item in base_fact_rows]
    tampered_facts[0]["fact_ref"] = str(generated.generate())
    await assert_ready_rejected(facts=tampered_facts)

    await pg_database._execute(
        "UPDATE public.consent_record SET status='WITHDRAWN',withdrawn_at=now(),version=version+1 "
        f"WHERE consent_record_id='{consent_record_id}'"
    )
    await assert_ready_rejected()
    await pg_database._execute(
        "UPDATE public.consent_record SET status='ACCEPTED',withdrawn_at=NULL,version=version+1 "
        f"WHERE consent_record_id='{consent_record_id}'"
    )

    next_profile_revision_id = generated.generate()
    await pg_database._execute(
        "INSERT INTO public.health_profile_revision(profile_revision_id,subject_member_id,"
        "subject_user_id,revision_no,tenant_public_id,identity_source_kind,identity_revision_ref,"
        "identity_source_version,snapshot_ciphertext,snapshot_key_id,reconfirmed_at,source_type,"
        "changed_fields,supersedes_revision_id,actor_user_id,actor_type,snapshot_digest,"
        "digest_key_id,created_at) SELECT "
        f"'{next_profile_revision_id}',subject_member_id,subject_user_id,revision_no+1,"
        "tenant_public_id,identity_source_kind,identity_revision_ref,identity_source_version,"
        "snapshot_ciphertext,snapshot_key_id,reconfirmed_at,source_type,changed_fields,"
        f"profile_revision_id,actor_user_id,actor_type,snapshot_digest,digest_key_id,now() "
        f"FROM public.health_profile_revision WHERE profile_revision_id='{profile_revision_id}';"
        "UPDATE public.health_profile SET "
        f"current_revision_id='{next_profile_revision_id}',version=version+1,updated_at=now() "
        f"WHERE profile_public_id='{profile_id}'"
    )
    await assert_ready_rejected(profile_revision=profile_revision_id)
    await pg_database._execute(
        "UPDATE public.health_profile SET "
        f"current_revision_id='{profile_revision_id}',version=version+1,updated_at=now() "
        f"WHERE profile_public_id='{profile_id}'"
    )

    replacement_fact_ref = generated.generate()
    replacement_event_id = generated.generate()
    replacement_fact_id = await pg_database._fetch_value(
        "INSERT INTO public.canonical_health_fact(subject_user_id,indicator_code,catalog_version,"
        "value_kind,numeric_value,unit,measured_at,received_at,created_at,source_type,"
        "source_identity_digest,producer_event_key,payload_digest,digest_key_id,supersedes_fact_id,"
        "correction_reason_code,created_by,fact_ref,subject_member_id) VALUES (NULL,'fasting_glucose',"
        "2,'NUMERIC',6.20,'mmol/L',now()-interval '30 minutes',now(),now(),'APP',repeat('a',64),"
        f"'slice4-replacement-{replacement_fact_ref}',repeat('b',64),'health-k1',{fact_id},"
        f"'CORRECTION',{actor_user_id},'{replacement_fact_ref}','{subject_member_id}') RETURNING id"
    )
    await pg_database._execute(
        "INSERT INTO public.health_fact_status_event(status_event_id,fact_id,event_no,"
        "predecessor_event_id,state,reason_code,actor_user_id,service_case_id,event_digest,"
        "digest_key_id,created_at) VALUES ("
        f"'{replacement_event_id}',{replacement_fact_id},1,NULL,'VERIFIED','THERAPIST_VERIFIED',"
        f"{actor_user_id + 100},'{case_id}',decode(repeat('c',64),'hex'),'health-k1',now())"
    )
    await assert_ready_rejected()

    try:
        replacement_generation_id = await projection_tasks._build_projection_v2(
            generation_no=8806,
            builder_id=generated.generate(),
            operation_id=generated.generate(),
        )
    finally:
        for kind in (
            "health", "confirmation", "health_shadow", "ready_gate",
            "shadow_confirmation",
        ):
            await dispose_projection_runtime(kind)
    factory = await get_slice4_session_factory("assessment_readiness_writer")
    try:
        async with factory() as session:
            async with session.begin():
                replacement_ready = await recompute_assessment_readiness(
                    AssessmentReadinessRepository(session), service_case_id=case_id
                )
    finally:
        await dispose_projection_runtime("health_reader")
        await dispose_slice4_runtime("assessment_readiness_writer")
    assert replacement_ready["readiness_status"] == "ASSESSMENT_READY"
    replacement_stored = (await pg_database._fetch_rows(
        "SELECT * FROM public.assessment_input_assembly WHERE assembly_id=$1",
        replacement_ready["assembly_id"],
    ))[0]
    assert replacement_stored["resolved_generation_id"] == replacement_generation_id
    replacement_rows = await pg_database._fetch_rows(
        "SELECT f.*,p.status_event_seq FROM public.assessment_input_assembly_fact f "
        "JOIN public.health_ready_projection_fact_v2 p "
        "ON p.generation_id=$2 AND p.fact_ref=f.fact_ref "
        "WHERE f.assembly_id=$1 ORDER BY f.indicator_code",
        replacement_ready["assembly_id"], replacement_generation_id,
    )
    stored = replacement_stored
    base_fact_rows = [{
        "indicator_code": row["indicator_code"],
        "fact_ref": str(row["fact_ref"]),
        "measured_at": row["measured_at"].isoformat(),
        "received_at": row["received_at"].isoformat(),
        "source_type": row["source_type"],
        "verification_state": row["verification_state"],
        "status_event_seq": row["status_event_seq"],
        "value_ciphertext": bytes(row["value_ciphertext"]).hex(),
        "value_key_id": row["value_key_id"],
        "unit": row["unit"],
        "row_digest": bytes(row["row_digest"]).hex(),
    } for row in replacement_rows]
    base_vector = {
        "consent_version_ids": _json(stored["consent_version_ids"]),
        "resolved_generation_id": str(stored["resolved_generation_id"]),
        "required_max_fact_id": stored["required_max_fact_id"],
        "required_max_status_event_seq": stored["required_max_status_event_seq"],
        "missing_codes": _json(stored["missing_codes"]),
        "expired_codes": _json(stored["expired_codes"]),
        "disputed_codes": _json(stored["disputed_codes"]),
        "source_vector_digest": bytes(stored["source_vector_digest"]).hex(),
        "source_digest": bytes(stored["source_digest"]).hex(),
        "assembly_digest": bytes(stored["assembly_digest"]).hex(),
        "digest_key_id": stored["digest_key_id"],
        "generated_at": stored["generated_at"].isoformat(),
    }
    stale_generation_vector = dict(base_vector)
    stale_generation_vector["resolved_generation_id"] = str(generation_id)
    await assert_ready_rejected(vector=stale_generation_vector)


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
        "$16,$17::jsonb,$18,$19,$20,$21)",
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
        0,
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


@pytest.mark.asyncio
async def test_Slice4四个只读接口真实HTTP错误边界与正向响应保持稳定(
    pg_database,
    health_record_writer_database,
    real_db_client,
) -> None:
    from app.core.database import dispose_projection_runtime
    from app.core.security import create_access_token
    from app.tasks import slice4_health_data_tasks as projection_tasks

    generated = Uuid7Generator()
    tenant_id = 99670
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    enrollment_id, case_id, identity_revision_id, actor_user_id = await _seed_case(
        pg_database,
        ordinal=13,
        mode="SELF",
        tenant_id=tenant_id,
        tenant_public_id=tenant_public_id,
        actor_user_id=99671,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    await health_record_writer_database._fetch_rows(
        "SELECT * FROM public.slice4_health_profile_root_create_v1("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamptz,"
        "$16,$17::jsonb,$18,$19,$20,$21)",
        actor_user_id,
        "SELF",
        subject_member_id,
        case_id,
        enrollment_id,
        generated.generate(),
        generated.generate(),
        tenant_public_id,
        identity_revision_id,
        1,
        b"http-contract-profile-snapshot",
        "profile-k1",
        b"http-contract-profile-digest",
        "digest-k1",
        datetime(2026, 8, 23, tzinfo=timezone.utc),
        "APP",
        '["medical_history"]',
        0,
        generated.generate(),
        b"http-contract-request-digest",
        b"http-contract-postimage-digest",
    )
    fact_ref = generated.generate()
    fact_id = await pg_database._fetch_value(
        "INSERT INTO public.canonical_health_fact(subject_user_id,subject_member_id,fact_ref,"
        "report_id,indicator_code,catalog_version,value_kind,numeric_value,unit,measured_at,"
        "source_type,source_identity_digest,producer_event_key,payload_digest,digest_key_id,"
        "created_by) VALUES($1,$2,$3,NULL,'weight',2,'NUMERIC',71.20,'kg',"
        "'2026-08-23T04:00:00+00','APP',$4,$5,$6,'k1',$1) RETURNING id",
        actor_user_id,
        subject_member_id,
        fact_ref,
        "a" * 64,
        "slice4-http-contract-fact-0067",
        "b" * 64,
    )
    await pg_database._execute(
        "INSERT INTO public.health_fact_status_event(status_event_id,fact_id,event_no,"
        "predecessor_event_id,state,reason_code,actor_user_id,service_case_id,event_digest,"
        "digest_key_id,created_at) VALUES ("
        f"'{generated.generate()}',{fact_id},1,NULL,'SELF_REPORTED','FACT_CREATED',"
        f"{actor_user_id},'{case_id}',decode(repeat('c',64),'hex'),'k1',now())"
    )
    try:
        await projection_tasks._build_projection_v2(
            generation_no=99670,
            builder_id=generated.generate(),
            operation_id=generated.generate(),
        )
    finally:
        for kind in (
            "health",
            "confirmation",
            "health_shadow",
            "ready_gate",
            "shadow_confirmation",
        ):
            await dispose_projection_runtime(kind)

    institution_user_id = actor_user_id + 200
    institution_org_id = await pg_database._fetch_value(
        "SELECT org_id FROM public.tenant WHERE id=$1", tenant_id
    )
    institution_headers = {
        "Authorization": "Bearer "
        + create_access_token(
            {
                "sub": str(institution_user_id),
                "role": "org_admin",
                "tenant_id": tenant_id,
                "org_id": institution_org_id,
            }
        )
    }
    routes = (
        f"/api/v1/institution/service-cases/{case_id}/health-record",
        f"/api/v1/institution/service-cases/{case_id}/detection-reports",
        f"/api/v1/institution/service-cases/{case_id}/health-indicators/latest?indicator_codes=weight",
        f"/api/v1/service-cases/{case_id}/assessment-readiness",
    )
    for route in routes:
        response = real_db_client.get(route, headers=institution_headers)
        assert response.status_code == 200, (route, response.json())

    invalid_authorizations = (
        {},
        {"Authorization": "Basic invalid"},
        {"Authorization": "Bearer invalid"},
        {
            "Authorization": "Bearer "
            + create_access_token(
                {"sub": str(institution_user_id), "role": "org_admin", "exp": 0}
            )
        },
    )
    for route in routes:
        for headers in invalid_authorizations:
            response = real_db_client.get(route, headers=headers)
            assert response.status_code == 401
            assert response.json()["code"] == "AUTHENTICATION_REQUIRED"

    platform_headers = {
        "Authorization": "Bearer "
        + create_access_token({"sub": str(actor_user_id + 300), "role": "super_admin"})
    }
    for route in routes:
        response = real_db_client.get(route, headers=platform_headers)
        assert response.status_code == 403

    await pg_database._execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        "VALUES (199671,NULL,'Slice4 HTTP other root','S4-HTTP-OTHER','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        "VALUES (199670,199671,'S4-HTTP-OTHER-TENANT','Slice4 HTTP other tenant',"
        "'store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        "VALUES (199672,'00000009998','test-only','org_admin','active',199670)"
    )
    cross_tenant_headers = {
        "Authorization": "Bearer "
        + create_access_token(
            {"sub": "199672", "role": "org_admin", "tenant_id": 199670, "org_id": 199671}
        )
    }
    for route in routes:
        response = real_db_client.get(route, headers=cross_tenant_headers)
        assert response.status_code == 403
        assert response.json()["code"] == "INSTITUTION_SCOPE_FORBIDDEN"

    missing_case_id = generated.generate()
    for route in routes:
        response = real_db_client.get(
            route.replace(str(case_id), str(missing_case_id)),
            headers=institution_headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "SERVICE_CASE_NOT_FOUND"

    for route in routes:
        response = real_db_client.get(
            route.replace(str(case_id), "not-a-uuid"), headers=institution_headers
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_REQUEST"


def test_PG29_PG31_六身份函数与基础表权限精确隔离(
    pg_database,
    health_record_writer_database,
    assessment_readiness_writer_database,
    slice4_workflow_worker_database,
    slice4_clinical_reader_database,
    slice4_institution_reader_database,
    slice4_identity_authority_database,
) -> None:
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260904_0038"
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
        "root": "public.slice4_health_profile_root_create_v1(bigint,character varying,uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,bytea,character varying,bytea,character varying,timestamp with time zone,character varying,jsonb,bigint,uuid,bytea,bytea)",
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
        "report_fact_authority": "public.slice4_report_fact_authority_v1(uuid,uuid,uuid)",
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
        "report_fact_authority": {health_fact_writer},
        "file_authority": {writer, clinical, institution},
        "clinical_profile": {clinical},
        "clinical_report": {clinical},
        "clinical_fact": {clinical},
        "institution": {institution},
        "builder": {health_builder, _role("KG_TEST_HEALTH_PROJECTION_SHADOW_ROLE")},
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
    assert not pg_database.fetch_value(
        f"SELECT has_table_privilege('{health_fact_writer}',"
        "'public.detection_report','SELECT')"
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
    assert replay.status_code == 200, replay.json()
    replay_mismatches = {
        key for key in first.json() if first.json().get(key) != replay.json().get(key)
    }
    assert replay_mismatches == set(), replay_mismatches
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
    slice4_clinical_reader_database,
    slice4_institution_reader_database,
    tmp_path,
    monkeypatch,
) -> None:
    from app.core.security import create_access_token
    from app.modules.private_file.storage import LocalFilesystemAdapter

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    _, case_id, _, actor_user_id = await _seed_case(
        pg_database,
        ordinal=10,
        mode="SELF",
        tenant_id=99640,
        tenant_public_id=tenant_public_id,
        actor_user_id=99641,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    file_id = generated.generate()
    file_content = b"PDFDATA8"
    file_digest = hashlib.sha256(file_content).hexdigest()
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY",
        "slice4-disposable-private-file-access-key",
    )
    real_db_client.app.state.private_object_store = LocalFilesystemAdapter(
        tmp_path.resolve()
    )
    await pg_database._execute(
        "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,"
        "declared_mime_type,declared_sha256,actual_size,actual_mime_type,actual_sha256,"
        "object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) "
        f"VALUES('{file_id}','DETECTION_REPORT',{actor_user_id},8,'application/pdf',"
        f"'{file_digest}',8,'application/pdf','{file_digest}',"
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
    object_key = await pg_database._fetch_value(
        "SELECT object_key FROM public.private_file WHERE file_id=$1", file_id
    )
    stored_path = tmp_path / str(object_key)
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_bytes(file_content)
    access = real_db_client.post(
        f"/api/v1/private-files/{file_id}/access",
        headers=authorization,
        json={"reason_code": "DETECTION_REPORT"},
    )
    assert access.status_code == 200, access.json()
    content = real_db_client.get(
        access.json()["data"]["content_path"],
        headers={
            **authorization,
            "X-Private-File-Access": access.json()["data"]["access_credential"],
        },
    )
    assert content.status_code == 200
    assert content.content == file_content
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1 "
        "AND event_type='REPORT_ORIGINAL_ACCESSED'",
        report_id,
    ) == 1
    institution_user_id = await pg_database._fetch_value(
        "SELECT id FROM public.\"user\" WHERE tenant_id=$1 AND role='org_admin'",
        99640,
    )
    institution_authorization = {
        "Authorization": "Bearer " + create_access_token({
            "sub": str(institution_user_id), "role": "org_admin", "tenant_id": 99640,
            "org_id": await pg_database._fetch_value(
                "SELECT org_id FROM public.tenant WHERE id=$1", 99640
            ),
        })
    }
    institution_access = real_db_client.post(
        f"/api/v1/private-files/{file_id}/access",
        headers=institution_authorization,
        json={"reason_code": "DETECTION_REPORT"},
    )
    assert institution_access.status_code == 200, institution_access.json()
    institution_content = real_db_client.get(
        institution_access.json()["data"]["content_path"],
        headers={
            **institution_authorization,
            "X-Private-File-Access": institution_access.json()["data"][
                "access_credential"
            ],
        },
    )
    assert institution_content.status_code == 200
    assert institution_content.content == file_content
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1 "
        "AND event_type='REPORT_ORIGINAL_ACCESSED'",
        report_id,
    ) == 2
    await pg_database._execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        "VALUES (199641,NULL,'Slice4 other root','S4-OTHER-ROOT','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        "VALUES (199640,199641,'S4-OTHER-TENANT','Slice4 other tenant','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        "VALUES (199642,'00000009999','test-only','org_admin','active',199640);"
    )
    cross_tenant_authorization = {
        "Authorization": f"Bearer {create_access_token({'sub': '199642', 'role': 'org_admin', 'tenant_id': 199640, 'org_id': 199641})}"
    }
    cross_tenant_access = real_db_client.post(
        f"/api/v1/private-files/{file_id}/access",
        headers=cross_tenant_authorization,
        json={"reason_code": "DETECTION_REPORT"},
    )
    assert cross_tenant_access.status_code == 404
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1 "
        "AND event_type='REPORT_ORIGINAL_ACCESSED'",
        report_id,
    ) == 2
    therapist_user_id = await pg_database._fetch_value(
        "SELECT p.user_id FROM public.service_case c "
        "JOIN public.therapist_profile p ON p.therapist_id=c.primary_therapist_id "
        "WHERE c.case_id=$1",
        case_id,
    )
    therapist_id = await pg_database._fetch_value(
        "SELECT primary_therapist_id FROM public.service_case WHERE case_id=$1",
        case_id,
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
        f"'slice4-report-qualification-{qualification_file_id}','CLEAN',NULL,now(),"
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
        "'*******0000','Slice4 Report',current_date,current_date+365,1,1,now());"
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
    snapshot_sql = (
        "SELECT count(*) FROM public.batch_b_private_file_access_snapshot_v1("
        f"'{file_id}',{{actor}},'{{context}}')"
    )
    platform_user_id = await pg_database._fetch_value(
        'SELECT id FROM public."user" WHERE id=$1 AND role=\'super_admin\' '
        "AND status='active'",
        actor_user_id + 300,
    )
    assert platform_user_id is not None
    clinical_matrix = (
        (actor_user_id, "FAMILY"),
        (therapist_user_id, "THERAPIST"),
        (platform_user_id, "PLATFORM"),
    )
    for matrix_actor, prefix in clinical_matrix:
        for suffix in ("AUTHORIZE", "CONTENT"):
            assert await slice4_clinical_reader_database._fetch_value(
                snapshot_sql.format(
                    actor=matrix_actor,
                    context=f"{prefix}_{suffix}",
                )
            ) == 1
            assert await slice4_clinical_reader_database._fetch_value(
                snapshot_sql.format(
                    actor=199642,
                    context=f"{prefix}_{suffix}",
                )
            ) == 0
    for suffix in ("AUTHORIZE", "CONTENT"):
        assert await slice4_institution_reader_database._fetch_value(
            snapshot_sql.format(
                actor=institution_user_id,
                context=f"INSTITUTION_{suffix}",
            )
        ) == 1
        assert await slice4_institution_reader_database._fetch_value(
            snapshot_sql.format(
                actor=199642,
                context=f"INSTITUTION_{suffix}",
            )
        ) == 0

    therapist_authorization = {
        "Authorization": "Bearer " + create_access_token({
            "sub": str(therapist_user_id), "role": "therapist",
            "tenant_id": await pg_database._fetch_value(
                'SELECT tenant_id FROM public."user" WHERE id=$1', therapist_user_id
            ),
        })
    }
    fact = real_db_client.post(
        f"/api/v1/therapist/service-cases/{case_id}/health-indicators",
        headers={
            **therapist_authorization,
            "Idempotency-Key": "slice4-report-fact-http-0010",
        },
        json={"items": [{
            "indicator_code": "weight",
            "value": "72.30",
            "unit": "kg",
            "measured_at": "2026-08-20T09:00:00+08:00",
            "source_type": "REPORT",
            "report_id": str(report_id),
        }]},
    )
    assert fact.status_code == 201, fact.json()
    fact_ref = UUID(fact.json()["items"][0]["fact_ref"])
    assert await pg_database._fetch_value(
        "SELECT report_id=$2 FROM public.canonical_health_fact WHERE fact_ref=$1",
        fact_ref,
        report_id,
    )
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.detection_report WHERE report_id=$1", report_id
    ) == 1
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.slice4_audit WHERE aggregate_ref=$1 "
        "AND event_type='DETECTION_REPORT_CREATED'",
        report_id,
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
    from app.core.security import create_access_token
    from app.core.database import dispose_projection_runtime
    from app.tasks import slice4_health_data_tasks as projection_tasks

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
            "items": [
                {
                    "indicator_code": code, "value": value, "unit": unit,
                    "measured_at": "2026-08-20T10:00:00+08:00",
                    "source_type": "APP", "report_id": None,
                    "measurement_context": context,
                }
                for code, value, unit, context in (
                    ("hba1c", "5.60", "%", "LAB"),
                    ("fasting_glucose", "5.60", "mmol/L", "FASTING_VENOUS"),
                    ("postprandial_glucose_2h", "7.50", "mmol/L", "OGTT_2H_VENOUS"),
                    ("total_cholesterol", "5.10", "mmol/L", "FASTING_LAB"),
                    ("triglyceride", "1.20", "mmol/L", "FASTING_LAB"),
                    ("hdl_c", "1.30", "mmol/L", "FASTING_LAB"),
                    ("ldl_c", "2.80", "mmol/L", "FASTING_LAB"),
                    ("systolic_bp", "119", "mmHg", "OFFICE"),
                    ("diastolic_bp", "79", "mmHg", "OFFICE"),
                )
            ]
        },
    )
    assert created.status_code == 201, created.json()
    fact_ref = UUID(next(
        item["fact_ref"]
        for item in created.json()["items"]
        if item["indicator_code"] == "hba1c"
    ))
    expected_contexts = {
        "hba1c": "LAB", "fasting_glucose": "FASTING_VENOUS",
        "postprandial_glucose_2h": "OGTT_2H_VENOUS",
        "total_cholesterol": "FASTING_LAB", "triglyceride": "FASTING_LAB",
        "hdl_c": "FASTING_LAB", "ldl_c": "FASTING_LAB",
        "systolic_bp": "OFFICE", "diastolic_bp": "OFFICE",
    }
    assert {
        row["indicator_code"]: row["measurement_context"]
        for row in await pg_database._fetch_rows(
            "SELECT indicator_code,measurement_context FROM public.canonical_health_fact "
            "WHERE fact_ref=ANY($1::uuid[])",
            [UUID(item["fact_ref"]) for item in created.json()["items"]],
        )
    } == expected_contexts
    fact_id = await pg_database._fetch_value(
        "SELECT id FROM public.canonical_health_fact WHERE fact_ref=$1", fact_ref
    )
    status_seq = await pg_database._fetch_value(
        "SELECT e.status_event_seq FROM public.health_fact_status_event e "
        "JOIN public.canonical_health_fact f ON f.id=e.fact_id WHERE f.fact_ref=$1", fact_ref
    )
    try:
        generation_id = await projection_tasks._build_projection_v2(
            generation_no=9001,
            builder_id=generated.generate(),
            operation_id=generated.generate(),
        )
    finally:
        for kind in (
            "health", "confirmation", "health_shadow", "ready_gate",
            "shadow_confirmation",
        ):
            await dispose_projection_runtime(kind)
    generation = await pg_database._fetch_rows(
        "SELECT status,shadow_success_count,high_watermark FROM "
        "public.health_projection_generation WHERE id=$1",
        generation_id,
    )
    assert generation[0]["status"] == "READY"
    assert generation[0]["shadow_success_count"] == 2
    high_watermark = generation[0]["high_watermark"]
    if isinstance(high_watermark, str):
        high_watermark = json.loads(high_watermark)
    assert high_watermark["max_fact_id"] >= fact_id
    assert high_watermark["max_status_event_seq"] >= status_seq
    response = real_db_client.get(
        "/api/v1/family/health-indicators/history?indicator_code=hba1c",
        headers=authorization,
    )
    assert response.status_code == 200, response.json()
    assert response.json()["items"][0]["fact_ref"] == str(fact_ref)
    assert response.json()["items"][0]["verification_state"] == "SELF_REPORTED"
    assert {
        row["indicator_code"]: row["measurement_context"]
        for row in await pg_database._fetch_rows(
            "SELECT indicator_code,measurement_context FROM public.health_projection_fact "
            "WHERE generation_id=$1 AND fact_ref=ANY($2::uuid[])",
            generation_id, [UUID(item["fact_ref"]) for item in created.json()["items"]],
        )
    } == expected_contexts


@pytest.mark.asyncio
async def test_D16_D19_生产v2Builder真实生成Member投影选择与覆盖证据(
    pg_database,
) -> None:
    from app.core.database import dispose_projection_runtime
    from app.tasks import slice4_health_data_tasks as projection_tasks

    generated = Uuid7Generator()
    tenant_public_id = generated.generate()
    subject_member_id = generated.generate()
    _, case_id, _, actor_user_id = await _seed_case(
        pg_database,
        ordinal=0,
        mode="SELF",
        tenant_id=130050,
        tenant_public_id=tenant_public_id,
        actor_user_id=130051,
        actor_member_id=subject_member_id,
        subject_member_id=subject_member_id,
        proxy_member_id=None,
    )
    fact_ref = generated.generate()
    fact_id = await pg_database._fetch_value(
        "INSERT INTO public.canonical_health_fact(subject_user_id,subject_member_id,fact_ref,"
        "report_id,indicator_code,catalog_version,value_kind,numeric_value,unit,measured_at,"
        "source_type,source_identity_digest,producer_event_key,payload_digest,digest_key_id,"
        "created_by) VALUES($1,$2,$3,NULL,'weight',2,'NUMERIC',73.20,'kg',"
        "'2026-08-20T04:00:00+00','APP',$4,$5,$6,'k1',$1) RETURNING id",
        actor_user_id, subject_member_id, fact_ref, "a" * 64,
        "slice4-v2-builder-fact-0001", "b" * 64,
    )
    inserted = await pg_database._fetch_value(
        "INSERT INTO public.health_fact_status_event(status_event_id,fact_id,event_no,"
        "predecessor_event_id,state,reason_code,actor_user_id,service_case_id,event_digest,"
        "digest_key_id,created_at) VALUES($1,$2,1,NULL,'SELF_REPORTED','FACT_CREATED',"
        "$3,$4,$5,'k1',now()) RETURNING 1",
        generated.generate(), fact_id, actor_user_id, case_id, b"status-digest",
    )
    assert inserted == 1

    primary = None
    builder_id = generated.generate()
    operation_id = generated.generate()
    try:
        generation_id = await projection_tasks._build_projection_v2(
            generation_no=9100,
            builder_id=builder_id,
            operation_id=operation_id,
        )
    except BaseException as exc:
        primary = exc
        raise
    finally:
        for kind in (
            "health", "confirmation", "health_shadow", "ready_gate",
            "shadow_confirmation",
        ):
            try:
                await dispose_projection_runtime(kind)
            except BaseException as exc:
                if primary is None:
                    raise AssertionError(f"V2_DISPOSE_{type(exc).__name__.upper()}") from None
    generation = await pg_database._fetch_rows(
        "SELECT projection_version,status,high_watermark FROM public.health_projection_generation "
        "WHERE id=$1",
        generation_id,
    )
    assert len(generation) == 1
    assert generation[0]["projection_version"] == 2
    assert generation[0]["status"] == "READY"
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_projection_shadow_run "
        "WHERE generation_id=$1 AND status='PASSED'",
        generation_id,
    ) == 2
    high_watermark = generation[0]["high_watermark"]
    if isinstance(high_watermark, str):
        high_watermark = json.loads(high_watermark)
    assert set(high_watermark) == {
        "max_fact_id", "max_status_event_seq", "source_snapshot"
    }
    projection = await pg_database._fetch_rows(
        "SELECT subject_member_id,fact_ref,status_event_seq FROM public.health_projection_fact "
        "WHERE generation_id=$1 AND subject_member_id=$2",
        generation_id, subject_member_id,
    )
    assert projection == [{
        "subject_member_id": subject_member_id,
        "fact_ref": fact_ref,
        "status_event_seq": projection[0]["status_event_seq"],
    }]
    assert projection[0]["status_event_seq"] >= 1
    selections = await pg_database._fetch_rows(
        "SELECT subject_member_id,winner_fact_ref,rule_version "
        "FROM public.health_projection_window_selection WHERE generation_id=$1 "
        "AND subject_member_id=$2",
        generation_id, subject_member_id,
    )
    assert selections == [{
        "subject_member_id": subject_member_id,
        "winner_fact_ref": fact_ref,
        "rule_version": "health-daily-selection-v2",
    }]
    evidence = await pg_database._fetch_rows(
        "SELECT subject_member_id,indicator_code,fact_count,status_event_count "
        "FROM public.health_projection_subject_indicator_evidence_v2 WHERE generation_id=$1 "
        "AND subject_member_id=$2",
        generation_id, subject_member_id,
    )
    assert evidence == [{
        "subject_member_id": subject_member_id,
        "indicator_code": "weight",
        "fact_count": 1,
        "status_event_count": 1,
    }]
    try:
        replay_generation_id = await projection_tasks._build_projection_v2(
            generation_no=9100,
            builder_id=builder_id,
            operation_id=operation_id,
        )
    finally:
        for kind in (
            "health", "confirmation", "health_shadow", "ready_gate",
            "shadow_confirmation",
        ):
            await dispose_projection_runtime(kind)
    assert replay_generation_id == generation_id
    assert await pg_database._fetch_value(
        "SELECT count(*) FROM public.health_projection_shadow_run WHERE generation_id=$1",
        generation_id,
    ) == 2
