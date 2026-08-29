from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import os
import secrets
from uuid import UUID, uuid4

import asyncpg
import pytest

from app.core.uuid_generator import Uuid7Generator
from app.core.security import create_access_token
from app.modules.auth.service import hash_password


pytestmark = pytest.mark.integration


def _authorization(user_id: int, role: str, tenant_id: int | None = None) -> dict[str, str]:
    claims = {"sub": str(user_id), "role": role}
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    return {"Authorization": f"Bearer {create_access_token(claims)}"}


def _value(database, query: str, *args):
    row = database.fetch_rows(query, *args)[0]
    return next(iter(row.values()))


def _seed_approved_therapist(
    pg_database,
    *,
    tenant_id: int,
    issued_by: int,
    therapist_user_id: int,
) -> UUID:
    values = Uuid7Generator()
    invitation_id = values.generate()
    therapist_id = values.generate()
    revision_id = values.generate()
    qualification_id = values.generate()
    file_id = values.generate()
    pg_database.execute(
        "BEGIN;"
        "INSERT INTO public.therapist_invitation("
        "invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,phone_digest,"
        "phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,expires_at,status,"
        "failed_attempts,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{invitation_id}',{tenant_id},decode('00','hex'),'k1',"
        f"md5('{therapist_id}')||md5('{therapist_id}'),'k1','*******0000',"
        f"md5('{invitation_id}')||md5('{invitation_id}'),'k1',now()+interval '1 day',"
        f"'ACTIVATED',0,{issued_by},now(),now(),1);"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES ("
        f"{therapist_user_id},'15{therapist_user_id:09d}','test-only','therapist','active',{tenant_id});"
        "INSERT INTO public.therapist_profile("
        "therapist_id,user_id,tenant_id,invitation_id,status,capacity_limit,active_case_count,"
        "current_revision_no,totp_secret_ciphertext,totp_encryption_key_id,totp_enabled,"
        "activated_at,created_at,updated_at,version) VALUES ("
        f"'{therapist_id}',{therapist_user_id},{tenant_id},'{invitation_id}','DRAFT',30,0,0,"
        "decode('00','hex'),'k1',true,now(),now(),now(),1);"
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
        "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,"
        "created_at,expires_at,scanned_at,bound_at) VALUES ("
        f"'{file_id}','THERAPIST_QUALIFICATION',{therapist_user_id},1,'application/pdf',"
        f"repeat('3',64),1,'application/pdf',repeat('3',64),'slice7/qualification/{file_id}',"
        "'CLEAN',NULL,now(),now()+interval '365 days',now(),now());"
        "INSERT INTO public.therapist_profile_revision("
        "revision_id,therapist_id,revision_no,profile_snapshot,input_digest,created_at) VALUES ("
        f"'{revision_id}','{therapist_id}',1,'{{\"v\":1}}'::jsonb,repeat('4',64),now());"
        "INSERT INTO public.therapist_qualification_version("
        "qualification_version_id,therapist_id,profile_revision_id,previous_version_id,"
        "qualification_type,certificate_no_ciphertext,certificate_encryption_key_id,"
        "certificate_no_digest,certificate_digest_key_id,certificate_no_masked,issuer_name,"
        "valid_from,valid_until,attachment_count,version_no,created_at) VALUES ("
        f"'{qualification_id}','{therapist_id}','{revision_id}',NULL,"
        "'METABOLIC_HEALTH_PRACTICE',decode('00','hex'),'k1',repeat('5',64),'k1',"
        "'****0001','Slice7 controlled issuer',current_date-30,current_date+365,1,1,now());"
        "INSERT INTO public.therapist_profile_revision_qualification("
        "therapist_id,revision_id,qualification_version_id,position) VALUES ("
        f"'{therapist_id}','{revision_id}','{qualification_id}',1);"
        "INSERT INTO public.therapist_qualification_attachment("
        "qualification_version_id,slot,private_file_id,created_at) VALUES ("
        f"'{qualification_id}',1,'{file_id}',now());"
        "UPDATE public.therapist_profile SET real_name_ciphertext=decode('00','hex'),"
        "real_name_encryption_key_id='k1',real_name_digest=repeat('6',64),"
        "real_name_digest_key_id='k1',display_name='Slice7 therapist',"
        "practice_summary='Controlled metabolic service',"
        "service_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb,status='APPROVED_ACTIVE',"
        f"current_revision_no=1,current_qualification_version_id='{qualification_id}',"
        "qualification_valid_until=current_date+365,submitted_at=now(),reviewed_at=now(),"
        f"updated_at=now() WHERE therapist_id='{therapist_id}';"
        "COMMIT;"
    )
    return therapist_id


def _seed_consent_documents(pg_database, *, published_by: int) -> tuple[dict[str, object], ...]:
    values = Uuid7Generator()
    documents: list[dict[str, object]] = []
    for ordinal, document_type in enumerate(
        (
            "USER_AGREEMENT",
            "PRIVACY_POLICY",
            "HEALTH_DATA_PROCESSING",
            "INSTITUTION_SERVICE",
            "NON_MEDICAL_RISK",
        ),
        start=1,
    ):
        existing = pg_database.fetch_rows(
            "SELECT d.document_version_id,r.rendition_id "
            "FROM public.consent_document_version d "
            "JOIN public.consent_document_rendition r "
            "ON r.document_version_id=d.document_version_id AND r.locale='zh-CN' "
            "WHERE d.document_type=$1 AND d.status='PUBLISHED'",
            document_type,
        )
        if existing:
            documents.append(
                {
                    "document_type": document_type,
                    "document_version_id": existing[0]["document_version_id"],
                    "rendition_id": existing[0]["rendition_id"],
                }
            )
            continue
        document_id = values.generate()
        rendition_id = values.generate()
        pg_database.execute(
            "INSERT INTO public.consent_document_version("
            "document_version_id,document_type,semantic_version,status,requires_reconsent,"
            "manifest_digest,effective_at,published_by,created_at,version) VALUES ("
            f"'{document_id}','{document_type}','slice7-b-{ordinal}','PUBLISHED',true,"
            f"repeat('{ordinal}',64),now()-interval '1 day',{published_by},now(),1);"
            "INSERT INTO public.consent_document_rendition("
            "rendition_id,document_version_id,locale,title,body,content_sha256,created_at) VALUES ("
            f"'{rendition_id}','{document_id}','zh-CN','Slice7 B {document_type}',"
            f"'Controlled consent text',repeat('{ordinal}',64),now())"
        )
        documents.append(
            {
                "document_type": document_type,
                "document_version_id": document_id,
                "rendition_id": rendition_id,
            }
        )
    return tuple(documents)


def _seed_concurrent_target_institution(pg_database) -> dict[str, object]:
    tenant_id = 100401
    admin_user_id = 100402
    org_id = 100404
    admin_phone = "18844444444"
    admin_password = secrets.token_urlsafe(24)
    password_hash = hash_password(admin_password).replace("'", "''")
    institution_invitation_id = uuid4()
    institution_application_id = uuid4()
    tenant_public_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'Concurrent target county','CONCURRENT-TARGET-COUNTY',"
        "'county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'CONCURRENT-TARGET-TENANT','Concurrent target institution',"
        "'store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) "
        f"VALUES ({admin_user_id},'{admin_phone}','{password_hash}','org_admin','active',{tenant_id});"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{institution_invitation_id}','Concurrent target institution','HEALTH_STORE',decode('00','hex'),"
        f"repeat('e',64),'CONCURRENT',{org_id},repeat('f',64),'ACTIVATED',0,"
        f"now()+interval '1 day',{admin_user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES ("
        f"'{institution_application_id}','{institution_invitation_id}',{admin_user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3);"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,"
        "evidence_version,input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('1',64),repeat('2',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    return {"admin_phone": admin_phone, "admin_password": admin_password}


def _complete_identity_http(
    real_db_client,
    *,
    enrollment_id: UUID,
    member_headers: dict[str, str],
    institution_headers: dict[str, str],
    reviewer_headers: dict[str, str],
    reviewer_password: str,
    key_prefix: str,
    id_number: str,
) -> dict[str, object]:
    detail = real_db_client.get(
        f"/api/v1/family/member-enrollments/{enrollment_id}", headers=member_headers
    )
    assert detail.status_code == 200, detail.json()
    submitted = real_db_client.put(
        f"/api/v1/family/member-enrollments/{enrollment_id}/identity-submission",
        headers={**member_headers, "Idempotency-Key": f"{key_prefix}-submit"},
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Member",
            "id_number": id_number,
            "expected_version": detail.json()["version"],
        },
    )
    assert submitted.status_code == 200, submitted.json()
    checked = real_db_client.post(
        f"/api/v1/institution/member-enrollments/{enrollment_id}/identity-check",
        headers={**institution_headers, "Idempotency-Key": f"{key_prefix}-check"},
        json={
            "revision_id": submitted.json()["current_revision_id"],
            "decision": "CHECKED",
            "attestation_code": "OFFLINE_IDENTITY_CHECKED",
            "expected_version": submitted.json()["version"],
        },
    )
    assert checked.status_code == 200, checked.json()
    claimed = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{submitted.json()['verification_id']}/claim",
        headers={**reviewer_headers, "Idempotency-Key": f"{key_prefix}-claim"},
        json={"expected_version": checked.json()["version"]},
    )
    assert claimed.status_code == 200, claimed.json()
    pii_access = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{submitted.json()['verification_id']}/pii-access",
        headers={**reviewer_headers, "Idempotency-Key": f"{key_prefix}-pii"},
        json={
            "current_password": reviewer_password,
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )
    assert pii_access.status_code == 200, pii_access.json()
    assert pii_access.headers["Cache-Control"] == "no-store"
    decided = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{submitted.json()['verification_id']}/decision",
        headers={**reviewer_headers, "Idempotency-Key": f"{key_prefix}-decision"},
        json={
            "revision_id": submitted.json()["current_revision_id"],
            "decision": "APPROVED",
            "expected_version": claimed.json()["version"],
        },
    )
    assert decided.status_code == 200, decided.json()
    assert decided.json()["status"] == "VERIFIED"
    return decided.json()


def _accept_consents_http(
    real_db_client,
    *,
    enrollment_id: UUID,
    member_headers: dict[str, str],
    documents: tuple[dict[str, object], ...],
    key_prefix: str,
) -> None:
    for ordinal, document in enumerate(documents, start=1):
        detail = real_db_client.get(
            f"/api/v1/family/member-enrollments/{enrollment_id}", headers=member_headers
        )
        assert detail.status_code == 200, detail.json()
        purpose = {
            "USER_AGREEMENT": "ACCOUNT_AND_SERVICE_ONBOARDING",
            "PRIVACY_POLICY": "ACCOUNT_AND_SERVICE_ONBOARDING",
            "HEALTH_DATA_PROCESSING": "HEALTH_DATA_PROCESSING",
            "INSTITUTION_SERVICE": "CARE_SERVICE_DELIVERY",
            "NON_MEDICAL_RISK": "CARE_SERVICE_DELIVERY",
        }[str(document["document_type"])]
        recorded = real_db_client.post(
            f"/api/v1/family/member-enrollments/{enrollment_id}/consent-records",
            headers={**member_headers, "Idempotency-Key": f"{key_prefix}-{ordinal}"},
            json={
                "document_version_id": str(document["document_version_id"]),
                "rendition_id": str(document["rendition_id"]),
                "choice": "ACCEPTED",
                "purpose_codes": [purpose],
                "expected_version": detail.json()["version"],
            },
        )
        assert recorded.status_code == 201, recorded.json()


def _create_case_http(
    real_db_client,
    *,
    enrollment_id: UUID,
    therapist_id: UUID,
    therapist_user_id: int,
    tenant_id: int,
    institution_headers: dict[str, str],
    key_prefix: str,
) -> dict[str, object]:
    detail = real_db_client.get(
        f"/api/v1/institution/member-enrollments/{enrollment_id}",
        headers=institution_headers,
    )
    assert detail.status_code == 200, detail.json()
    assigned = real_db_client.post(
        f"/api/v1/institution/member-enrollments/{enrollment_id}/primary-assignments",
        headers={**institution_headers, "Idempotency-Key": f"{key_prefix}-assign"},
        json={
            "therapist_id": str(therapist_id),
            "service_scope_tags": ["GLUCOSE_METABOLISM"],
            "expected_version": detail.json()["version"],
        },
    )
    assert assigned.status_code == 201, assigned.json()
    accepted = real_db_client.post(
        f"/api/v1/therapist/primary-assignments/{assigned.json()['assignment_id']}/accept",
        headers={
            **_authorization(therapist_user_id, "therapist", tenant_id),
            "Idempotency-Key": f"{key_prefix}-accept",
        },
        json={"expected_version": assigned.json()["version"]},
    )
    assert accepted.status_code == 201, accepted.json()
    assert accepted.json()["status"] == "PREPARING"
    return accepted.json()


def _approved_source(
    pg_database,
    *,
    template_enrollment_id: UUID,
    member_id: UUID,
    fingerprint: str,
) -> dict[str, object]:
    uuids = Uuid7Generator()
    invitation_id = uuids.generate()
    enrollment_id = uuids.generate()
    verification_id = uuids.generate()
    revision_id = uuids.generate()
    platform_decision_id = uuids.generate()
    evidence_digest = "8" * 64
    pg_database.execute(
        "BEGIN; SET CONSTRAINTS ALL DEFERRED;"
        "INSERT INTO public.member_service_invitation("
        "invitation_id,tenant_id,mode,phone_ciphertext,phone_key_id,phone_digest,"
        "phone_digest_key_id,phone_masked,code_digest,code_key_id,status,failed_attempts,"
        "expires_at,issued_by,issued_at,accepted_at,revoked_at,version) "
        f"SELECT '{invitation_id}',tenant_id,'SELF',phone_ciphertext,phone_key_id,"
        "md5(random()::text)||md5(random()::text),phone_digest_key_id,phone_masked,"
        "md5(random()::text)||md5(random()::text),code_key_id,'ACCEPTED',0,"
        "now()+interval '1 day',issued_by,now(),now(),NULL,2 "
        "FROM public.member_service_invitation WHERE invitation_id=("
        "SELECT invitation_id FROM public.service_enrollment "
        f"WHERE enrollment_id='{template_enrollment_id}');"
        "INSERT INTO public.service_enrollment("
        "enrollment_id,invitation_id,tenant_id,subject_member_id,proxy_member_id,mode,status,"
        "service_scope_tags,current_identity_verification_id,current_assignment_id,service_case_id,"
        "accepted_at,identity_verified_at,case_created_at,created_at,updated_at,version) "
        f"SELECT '{enrollment_id}','{invitation_id}',tenant_id,'{member_id}',NULL,'SELF',"
        "'IDENTITY_VERIFIED','[]'::jsonb,NULL,NULL,NULL,now(),now(),NULL,now(),now(),4 "
        f"FROM public.service_enrollment WHERE enrollment_id='{template_enrollment_id}';"
        "INSERT INTO public.member_identity_verification("
        "verification_id,enrollment_id,member_id,current_revision_id,status,institution_decision_id,"
        "platform_decision_id,submitted_at,institution_checked_at,platform_decided_at,version) VALUES ("
        f"'{verification_id}','{enrollment_id}','{member_id}',NULL,'VERIFIED',"
        "NULL,NULL,now(),now(),now(),4);"
        "INSERT INTO public.member_identity_revision("
        "revision_id,verification_id,revision_no,document_type,real_name_ciphertext,real_name_key_id,"
        "id_ciphertext,id_key_id,birth_date_ciphertext,birth_date_key_id,id_masked,identity_fingerprint,"
        "fingerprint_key_id,input_digest,submitted_by_member_id,created_at) "
        f"SELECT '{revision_id}','{verification_id}',1,document_type,real_name_ciphertext,real_name_key_id,"
        f"id_ciphertext,id_key_id,birth_date_ciphertext,birth_date_key_id,id_masked,'{fingerprint}',"
        f"fingerprint_key_id,input_digest,'{member_id}',now() FROM public.member_identity_revision "
        f"WHERE revision_id=(SELECT current_revision_id FROM public.member_identity_verification "
        f"WHERE enrollment_id='{template_enrollment_id}');"
        "INSERT INTO public.member_identity_review_decision("
        "decision_id,verification_id,revision_id,phase,reviewer_user_id,decision,reason_code,"
        "correction_fields,attestation_code,represented_elder_eligible,request_digest,evidence_digest,created_at) "
        f"SELECT '{platform_decision_id}','{verification_id}','{revision_id}','PLATFORM',"
        f"reviewer_user_id,'APPROVED',NULL,NULL,NULL,NULL,repeat('8',64),'{evidence_digest}',now() "
        "FROM public.member_identity_review_decision WHERE phase='PLATFORM' LIMIT 1;"
        "UPDATE public.member_identity_verification SET current_revision_id="
        f"'{revision_id}',platform_decision_id='{platform_decision_id}' "
        f"WHERE verification_id='{verification_id}';"
        "UPDATE public.service_enrollment SET current_identity_verification_id="
        f"'{verification_id}' WHERE enrollment_id='{enrollment_id}';"
        "COMMIT;"
    )
    return {
        "enrollment_id": enrollment_id,
        "verification_id": verification_id,
        "revision_id": revision_id,
        "decision_id": platform_decision_id,
        "evidence_digest": evidence_digest,
    }


def _call_reuse(database, *, claim_id: UUID, member_id: UUID, fingerprint: str, source: dict[str, object]):
    return database.fetch_rows(
        "SELECT * FROM identity.slice7_identity_claim_reuse_v2("
        "$1,$2,$3,$4,$5,$6,$7,$8,$9,now())",
        claim_id,
        member_id,
        fingerprint,
        os.environ["KG_IDENTITY_PII_KEY_ID"],
        source["revision_id"],
        source["decision_id"],
        4,
        source["evidence_digest"],
        None,
    )[0]


def test_实名复用V2同会员同指纹且原claim不变(
    pg_database,
    member_identity_review_writer_database,
    member_enrollment_writer_database,
) -> None:
    seed_module = importlib.import_module(
        "tests.integration.test_一期切片6方案生成审核确认数据库闭环"
    )
    seeded = seed_module._seed_ready_case(pg_database, ordinal=187)
    member_id = UUID(str(seeded["subject_member_id"]))
    old_enrollment_id = UUID(str(seeded["enrollment_id"]))
    algorithm_key = pg_database.fetch_value(
        "SELECT fingerprint_key_id FROM identity.identity_claim_algorithm_state "
        "WHERE singleton=1"
    )
    preliminary = pg_database.fetch_rows(
        "SELECT * FROM identity.identity_subject_claim_registry WHERE member_id=$1",
        member_id,
    )[0]
    pg_database.execute(
        "UPDATE public.member_identity_revision SET fingerprint_key_id="
        f"'{algorithm_key}',identity_fingerprint='{preliminary['identity_fingerprint']}' "
        f"WHERE revision_id='{preliminary['slice3_revision_id']}';"
        "UPDATE public.member_identity_verification SET status='VERIFIED',version="
        f"{preliminary['source_facts_version']} WHERE current_revision_id="
        f"'{preliminary['slice3_revision_id']}';"
        "UPDATE public.member_identity_review_decision SET evidence_digest="
        f"'{preliminary['source_evidence_digest']}' WHERE decision_id="
        f"'{preliminary['slice3_decision_id']}';"
        "UPDATE identity.identity_subject_claim_registry SET fingerprint_key_id="
        f"'{algorithm_key}' WHERE member_id='{member_id}'"
    )
    original = pg_database.fetch_rows(
        "SELECT * FROM identity.identity_subject_claim_registry WHERE member_id=$1",
        member_id,
    )[0]
    original_snapshot = tuple(original.values())
    pg_database.execute(
        "UPDATE public.service_enrollment SET status='REVOKED',version=version+1,updated_at=now() "
        f"WHERE enrollment_id='{old_enrollment_id}'"
    )
    source = _approved_source(
        pg_database,
        template_enrollment_id=old_enrollment_id,
        member_id=member_id,
        fingerprint=original["identity_fingerprint"],
    )
    first = _call_reuse(
        member_identity_review_writer_database,
        claim_id=Uuid7Generator().generate(),
        member_id=member_id,
        fingerprint=original["identity_fingerprint"],
        source=source,
    )
    replay = _call_reuse(
        member_identity_review_writer_database,
        claim_id=Uuid7Generator().generate(),
        member_id=member_id,
        fingerprint=original["identity_fingerprint"],
        source=source,
    )
    for result in (first, replay):
        assert result["outcome"] == "REUSED"
        assert result["resolved_claim_id"] == original["claim_id"]
        claim = (
            json.loads(result["resolved_claim"])
            if isinstance(result["resolved_claim"], str)
            else result["resolved_claim"]
        )
        assert claim["claim_id"] == str(original["claim_id"])
        assert claim["slice3_revision_id"] == str(original["slice3_revision_id"])
        assert claim["slice3_decision_id"] == str(original["slice3_decision_id"])
    assert tuple(
        pg_database.fetch_rows(
            "SELECT * FROM identity.identity_subject_claim_registry WHERE member_id=$1",
            member_id,
        )[0].values()
    ) == original_snapshot
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM identity.identity_subject_claim_registry "
        f"WHERE member_id='{member_id}'",
    ) == 1

    changed_fingerprint = "f" * 64
    pg_database.execute(
        "UPDATE public.member_identity_revision SET identity_fingerprint="
        f"'{changed_fingerprint}' WHERE revision_id='{source['revision_id']}'"
    )
    mismatch = _call_reuse(
        member_identity_review_writer_database,
        claim_id=Uuid7Generator().generate(),
        member_id=member_id,
        fingerprint=changed_fingerprint,
        source=source,
    )
    assert mismatch == {
        "outcome": "IDENTITY_REUSE_FINGERPRINT_MISMATCH",
        "resolved_claim_id": None,
        "resolved_claim": None,
    }

    other_member_id = Uuid7Generator().generate()
    pg_database.execute(
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{other_member_id}','M9876543210ABCDEFGHJK','registration','created',1,now(),now())"
    )
    other_source = _approved_source(
        pg_database,
        template_enrollment_id=old_enrollment_id,
        member_id=other_member_id,
        fingerprint=original["identity_fingerprint"],
    )
    wrong_member = _call_reuse(
        member_identity_review_writer_database,
        claim_id=Uuid7Generator().generate(),
        member_id=other_member_id,
        fingerprint=original["identity_fingerprint"],
        source=other_source,
    )
    assert wrong_member == {
        "outcome": "IDENTITY_REUSE_MEMBER_MISMATCH",
        "resolved_claim_id": None,
        "resolved_claim": None,
    }
    pg_database.execute(
        "UPDATE public.member_identity_verification SET status='REJECTED' "
        f"WHERE verification_id='{other_source['verification_id']}'"
    )
    not_current = _call_reuse(
        member_identity_review_writer_database,
        claim_id=Uuid7Generator().generate(),
        member_id=other_member_id,
        fingerprint=original["identity_fingerprint"],
        source=other_source,
    )
    assert not_current == {
        "outcome": "IDENTITY_REUSE_SOURCE_NOT_CURRENT",
        "resolved_claim_id": None,
        "resolved_claim": None,
    }

    function_oid = pg_database.fetch_value(
        "SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='identity' AND p.proname='slice7_identity_claim_reuse_v2'"
    )
    review_role = os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"]
    assert pg_database.fetch_value(
        f"SELECT has_function_privilege('{review_role}',{function_oid},'EXECUTE')"
    )
    for role in (
        "public",
        os.environ["KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE"],
        os.environ["KG_TEST_SLICE7_TRANSFER_WRITER_ROLE"],
        os.environ["KG_TEST_APPLICATION_ROLE"],
    ):
        assert not pg_database.fetch_value(
            f"SELECT has_function_privilege('{role}',{function_oid},'EXECUTE')"
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        _call_reuse(
            member_enrollment_writer_database,
            claim_id=Uuid7Generator().generate(),
            member_id=member_id,
            fingerprint=original["identity_fingerprint"],
            source=source,
        )


def test_转机构后同会员实名复用并经正式Assignment创建新Case真实HTTP闭环(
    pg_database,
    real_db_client,
    slice7_transfer_writer_database,
) -> None:
    currentness = importlib.import_module(
        "tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同"
    )
    source = currentness._seed_current_member_and_institution(pg_database, variant=0)
    target = currentness._seed_current_member_and_institution(pg_database, variant=1)
    member_headers = currentness._login(
        real_db_client, source["member_phone"], source["member_password"]
    )
    source_headers = currentness._login(
        real_db_client, source["admin_phone"], source["admin_password"]
    )
    target_headers = currentness._login(
        real_db_client, target["admin_phone"], target["admin_password"]
    )
    source_admin = int(
        _value(
            pg_database,
            "SELECT id FROM public.\"user\" WHERE phone=$1", source["admin_phone"]
        )
    )
    target_admin = int(
        _value(
            pg_database,
            "SELECT id FROM public.\"user\" WHERE phone=$1", target["admin_phone"]
        )
    )
    source_tenant = int(
        _value(
            pg_database,
            "SELECT tenant_id FROM public.\"user\" WHERE id=$1", source_admin
        )
    )
    target_tenant = int(
        _value(
            pg_database,
            "SELECT tenant_id FROM public.\"user\" WHERE id=$1", target_admin
        )
    )
    target_public_id = UUID(
        str(
            _value(
                pg_database,
                "SELECT tenant_public_id FROM public.institution_application "
                "WHERE tenant_internal_id=$1",
                target_tenant,
            )
        )
    )
    reviewer_user_id = 97901
    reviewer_password = "Slice7-Reviewer-Only-Password"
    reviewer_password_hash = hash_password(reviewer_password).replace("'", "''")
    pg_database.execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES ("
        f"{reviewer_user_id},'19900097901','{reviewer_password_hash}',"
        "'super_admin','active',NULL)"
    )
    reviewer_headers = _authorization(reviewer_user_id, "super_admin")
    source_therapist_user_id = 97902
    target_therapist_user_id = 97903
    source_therapist = _seed_approved_therapist(
        pg_database,
        tenant_id=source_tenant,
        issued_by=source_admin,
        therapist_user_id=source_therapist_user_id,
    )
    target_therapist = _seed_approved_therapist(
        pg_database,
        tenant_id=target_tenant,
        issued_by=target_admin,
        therapist_user_id=target_therapist_user_id,
    )
    documents = _seed_consent_documents(pg_database, published_by=reviewer_user_id)
    identity_number = currentness._synthetic_prc_identity()

    source_enrollment = currentness._create_accepted_self_enrollment(
        real_db_client,
        admin_authorization=source_headers,
        member_authorization=member_headers,
        member_phone=source["member_phone"],
        key_prefix="slice7-b-source",
    )
    _complete_identity_http(
        real_db_client,
        enrollment_id=source_enrollment,
        member_headers=member_headers,
        institution_headers=source_headers,
        reviewer_headers=reviewer_headers,
        reviewer_password=reviewer_password,
        key_prefix="slice7-b-source-identity",
        id_number=identity_number,
    )
    _accept_consents_http(
        real_db_client,
        enrollment_id=source_enrollment,
        member_headers=member_headers,
        documents=documents,
        key_prefix="slice7-b-source-consent",
    )
    source_case = _create_case_http(
        real_db_client,
        enrollment_id=source_enrollment,
        therapist_id=source_therapist,
        therapist_user_id=source_therapist_user_id,
        tenant_id=source_tenant,
        institution_headers=source_headers,
        key_prefix="slice7-b-source-case",
    )
    member_id = UUID(str(source["member_id"]))
    original_claim = pg_database.fetch_rows(
        "SELECT * FROM identity.identity_subject_claim_registry WHERE member_id=$1",
        member_id,
    )[0]

    target_invitation = real_db_client.post(
        "/api/v1/institution/member-invitations",
        headers={**target_headers, "Idempotency-Key": "slice7-b-target-early-create"},
        json={"mode": "SELF", "phone": source["member_phone"]},
    )
    assert target_invitation.status_code == 201, target_invitation.json()
    blocked_before_transfer = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**member_headers, "Idempotency-Key": "slice7-b-target-early-accept"},
        json={
            "invitation_id": target_invitation.json()["invitation_id"],
            "phone": source["member_phone"],
            "short_code": target_invitation.json()["short_code"],
        },
    )
    assert blocked_before_transfer.status_code == 409
    assert blocked_before_transfer.json()["code"] == "ACTIVE_ENROLLMENT_EXISTS"

    created_transfer = real_db_client.post(
        f"/api/v1/family/service-cases/{source_case['case_id']}/transfers",
        headers={**member_headers, "Idempotency-Key": "slice7-b-transfer-create"},
        json={
            "target_tenant_id": str(target_public_id),
            "requested_scope": ["PROFILE", "ASSESSMENT"],
            "expected_version": source_case["version"],
        },
    )
    assert created_transfer.status_code == 201, created_transfer.json()
    transfer = created_transfer.json()
    transfer_id = transfer["transfer_id"]
    target_authority = asyncio.run(
        slice7_transfer_writer_database._fetch_value(
            "SELECT public.slice7_authority_v1($1,$2,$3,$4,$5)",
            "START_REVIEW_TRANSFER",
            UUID(transfer_id),
            target_admin,
            "org_admin",
            target_tenant,
        )
    )
    assert target_authority is not None
    start_review = real_db_client.post(
        f"/api/v1/institutions/service-transfers/{transfer_id}/start-review",
        headers={**target_headers, "Idempotency-Key": "slice7-b-transfer-review"},
        json={"expected_version": transfer["version"], "reason_code": "REVIEW_STARTED"},
    )
    assert start_review.status_code == 200, start_review.json()
    accepted_transfer = real_db_client.post(
        f"/api/v1/institutions/service-transfers/{transfer_id}/accept",
        headers={**target_headers, "Idempotency-Key": "slice7-b-transfer-accept"},
        json={
            "expected_version": start_review.json()["version"],
            "reason_code": "SERVICE_CONTINUATION_ACCEPTED",
            "service_label": "GLUCOSE_METABOLISM",
        },
    )
    assert accepted_transfer.status_code == 200, accepted_transfer.json()
    source_closed = real_db_client.post(
        f"/api/v1/institutions/service-transfers/{transfer_id}/source-close",
        headers={**source_headers, "Idempotency-Key": "slice7-b-transfer-source-close"},
        json={
            "expected_version": accepted_transfer.json()["version"],
            "summary_id": str(Uuid7Generator().generate()),
            "risk_disposition": "CONTINUE_WITH_TARGET",
        },
    )
    assert source_closed.status_code == 200, source_closed.json()
    confirmed = real_db_client.post(
        f"/api/v1/family/service-transfers/{transfer_id}/confirm-scope",
        headers={**member_headers, "Idempotency-Key": "slice7-b-transfer-confirm"},
        json={
            "expected_version": source_closed.json()["version"],
            "exact_scope": ["PROFILE", "ASSESSMENT"],
        },
    )
    assert confirmed.status_code == 200, confirmed.json()
    coordinate_headers = {
        **reviewer_headers,
        "Idempotency-Key": "slice7-b-transfer-coordinate",
    }
    transferred = real_db_client.post(
        f"/api/v1/platform/service-transfers/{transfer_id}/coordinate-close",
        headers=coordinate_headers,
        json={
            "expected_version": confirmed.json()["version"],
            "reason_code": "HANDOFF_READY",
        },
    )
    assert transferred.status_code == 200, transferred.json()
    assert transferred.json()["status"] == "TRANSFERRED"
    replayed_transfer = real_db_client.post(
        f"/api/v1/platform/service-transfers/{transfer_id}/coordinate-close",
        headers=coordinate_headers,
        json={
            "expected_version": confirmed.json()["version"],
            "reason_code": "HANDOFF_READY",
        },
    )
    assert replayed_transfer.status_code == 200
    assert replayed_transfer.json() == transferred.json()
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.service_transfer_continuation_handoff "
        "WHERE transfer_id=$1",
        UUID(transfer_id),
    ) == 1
    assert _value(
        pg_database,
        "SELECT status FROM public.service_enrollment WHERE enrollment_id=$1",
        source_enrollment,
    ) == "REVOKED"
    assert _value(
        pg_database,
        "SELECT to_status FROM public.service_case_lifecycle_event "
        "WHERE service_case_id=$1 ORDER BY version DESC LIMIT 1",
        UUID(source_case["case_id"]),
    ) == "TRANSFERRED"
    source_handoff = real_db_client.get(
        f"/api/v1/institutions/service-transfers/{transfer_id}/continuation-handoff",
        headers=source_headers,
    )
    assert source_handoff.status_code in {403, 404}
    handoff = real_db_client.get(
        f"/api/v1/institutions/service-transfers/{transfer_id}/continuation-handoff",
        headers=target_headers,
    )
    assert handoff.status_code == 200, handoff.json()
    assert handoff.json()["status"] == "PENDING_TARGET_ENROLLMENT"

    accepted_target = real_db_client.post(
        "/api/v1/family/member-enrollments/accept",
        headers={**member_headers, "Idempotency-Key": "slice7-b-target-after-transfer"},
        json={
            "invitation_id": target_invitation.json()["invitation_id"],
            "phone": source["member_phone"],
            "short_code": target_invitation.json()["short_code"],
        },
    )
    assert accepted_target.status_code == 201, accepted_target.json()
    target_enrollment = UUID(accepted_target.json()["enrollment_id"])
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.consent_record WHERE enrollment_id=$1",
        target_enrollment,
    ) == 0
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.primary_therapist_assignment WHERE enrollment_id=$1",
        target_enrollment,
    ) == 0
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.service_case WHERE enrollment_id=$1",
        target_enrollment,
    ) == 0
    target_identity = _complete_identity_http(
        real_db_client,
        enrollment_id=target_enrollment,
        member_headers=member_headers,
        institution_headers=target_headers,
        reviewer_headers=reviewer_headers,
        reviewer_password=reviewer_password,
        key_prefix="slice7-b-target-identity",
        id_number=identity_number,
    )
    assert target_identity["member_id"] == str(member_id)
    current_claim = pg_database.fetch_rows(
        "SELECT * FROM identity.identity_subject_claim_registry WHERE member_id=$1",
        member_id,
    )[0]
    assert current_claim == original_claim
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM identity.identity_subject_claim_registry WHERE member_id=$1",
        member_id,
    ) == 1
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.consent_record WHERE enrollment_id=$1",
        target_enrollment,
    ) == 0
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.service_case WHERE enrollment_id=$1",
        target_enrollment,
    ) == 0

    _accept_consents_http(
        real_db_client,
        enrollment_id=target_enrollment,
        member_headers=member_headers,
        documents=documents,
        key_prefix="slice7-b-target-consent",
    )
    target_case = _create_case_http(
        real_db_client,
        enrollment_id=target_enrollment,
        therapist_id=target_therapist,
        therapist_user_id=target_therapist_user_id,
        tenant_id=target_tenant,
        institution_headers=target_headers,
        key_prefix="slice7-b-target-case",
    )
    linked_headers = {
        **target_headers,
        "Idempotency-Key": "slice7-b-continuation-link",
    }
    linked = real_db_client.post(
        f"/api/v1/institutions/service-transfers/{transfer_id}/continuation-case",
        headers=linked_headers,
        json={
            "new_service_case_id": target_case["case_id"],
            "expected_version": handoff.json()["version"],
        },
    )
    assert linked.status_code == 200, linked.json()
    assert linked.json()["status"] == "CONTINUATION_CASE_LINKED"
    assert linked.json()["linked_enrollment_id"] == str(target_enrollment)
    assert linked.json()["linked_service_case_id"] == target_case["case_id"]
    replayed_link = real_db_client.post(
        f"/api/v1/institutions/service-transfers/{transfer_id}/continuation-case",
        headers=linked_headers,
        json={
            "new_service_case_id": target_case["case_id"],
            "expected_version": handoff.json()["version"],
        },
    )
    assert replayed_link.status_code == 200
    assert replayed_link.json() == linked.json()
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.service_case WHERE enrollment_id=$1",
        target_enrollment,
    ) == 1
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.health_plan_version "
        "WHERE service_case_id=$1 AND status='ACTIVE'",
        UUID(target_case["case_id"]),
    ) == 0


def test_并发重新入组只能形成一个当前Enrollment真实HTTP合同(
    pg_database,
    real_db_client,
) -> None:
    currentness = importlib.import_module(
        "tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同"
    )
    first = currentness._seed_current_member_and_institution(pg_database, variant=2)
    second = _seed_concurrent_target_institution(pg_database)
    member_headers = currentness._login(
        real_db_client, first["member_phone"], first["member_password"]
    )
    first_headers = currentness._login(
        real_db_client, first["admin_phone"], first["admin_password"]
    )
    second_headers = currentness._login(
        real_db_client, second["admin_phone"], second["admin_password"]
    )

    invitations = []
    for ordinal, headers in enumerate((first_headers, second_headers), start=1):
        response = real_db_client.post(
            "/api/v1/institution/member-invitations",
            headers={**headers, "Idempotency-Key": f"slice7-b-concurrent-create-{ordinal}"},
            json={"mode": "SELF", "phone": first["member_phone"]},
        )
        assert response.status_code == 201, response.json()
        invitations.append(response.json())

    def accept(ordinal: int):
        invitation = invitations[ordinal - 1]
        return real_db_client.post(
            "/api/v1/family/member-enrollments/accept",
            headers={
                **member_headers,
                "Idempotency-Key": f"slice7-b-concurrent-accept-{ordinal}",
            },
            json={
                "invitation_id": invitation["invitation_id"],
                "phone": first["member_phone"],
                "short_code": invitation["short_code"],
            },
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = tuple(pool.map(accept, (1, 2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["code"] == "ACTIVE_ENROLLMENT_EXISTS"
    member_id = UUID(str(first["member_id"]))
    assert _value(
        pg_database,
        "SELECT COUNT(*) FROM public.service_enrollment WHERE subject_member_id=$1 "
        "AND status IN ('ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',"
        "'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED','IDENTITY_VERIFIED',"
        "'CONSENT_PENDING','THERAPIST_PENDING','CASE_CREATED')",
        member_id,
    ) == 1
