from __future__ import annotations

import hashlib
import os
import uuid
from datetime import date, timedelta

import pytest


pytestmark = pytest.mark.integration


class _CleanScanner:
    async def scan(self, path, *, mime_type: str) -> str:
        assert path.is_file()
        assert mime_type == "application/pdf"
        return "CLEAN"


class _CommitOutcomeSession:
    def __init__(self, session, *, committed: bool) -> None:
        self._session = session
        self._committed = committed

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self) -> None:
        if self._committed:
            await self._session.commit()
        else:
            await self._session.rollback()
        raise RuntimeError("synthetic commit outcome")


def _mark_stage(stage: str) -> None:
    progress_path = os.getenv("KG_SAFE_PROGRESS_FILE")
    if progress_path:
        with open(progress_path, "a", encoding="ascii") as stream:
            stream.write(f"{stage}\n")


def _request_id() -> str:
    return str(uuid.uuid4())


async def _seed_ready_institution(
    pg_database, *, offset: int = 0
) -> tuple[int, int, int, str, int]:
    tenant_id = 41001 + offset
    org_admin_id = 42001 + offset
    reviewer_id = 42002 + offset
    hq_id = 1 + offset
    province_id = 2 + offset
    city_id = 3 + offset
    county_id = 4 + offset
    invitation_id = str(uuid.uuid4())
    application_id = str(uuid.uuid4())
    tenant_public_id = str(uuid.uuid4())
    license_id = str(uuid.uuid4())
    license_file_id = str(uuid.uuid4())
    statements = (
        (
            "ORGANIZATION",
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) VALUES "
            f"({hq_id},NULL,'一期总部','SLICE2-HQ-{offset}','headquarter','active',1),"
            f"({province_id},{hq_id},'一期省','SLICE2-P-{offset}','province','active',1),"
            f"({city_id},{province_id},'一期市','SLICE2-C-{offset}','city','active',1),"
            f"({county_id},{city_id},'一期区县','SLICE2-D-{offset}','county','active',1)",
        ),
        (
            "TENANT",
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) VALUES "
            f"({tenant_id},{county_id},'SLICE2-TENANT-{offset}','一期切片2机构','store','一期省','一期市','active',now(),now())",
        ),
        (
            "USERS",
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES "
            f"({org_admin_id},'13' || '8' || lpad('{offset}',8,'0'),'test-only','org_admin','active',{tenant_id}),"
            f"({reviewer_id},'13' || '9' || lpad('{offset}',8,'0'),'test-only','super_admin','active',NULL)",
        ),
        (
            "INVITATION",
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES "
            f"('{invitation_id}','一期切片2机构','HEALTH_STORE',decode('00','hex'),repeat('a',64),'SLICE2',{county_id},repeat('b',64),'ACTIVATED',0,now()+interval '1 day',{reviewer_id},now(),now(),1)",
        ),
        (
            "APPLICATION",
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) VALUES "
        f"('{application_id}','{invitation_id}',{org_admin_id},'HEALTH_STORE','APPROVED',"
        "'{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}'::jsonb,'[]'::jsonb,1,"
            f"{tenant_id},'{tenant_public_id}',false,now(),now(),now(),now(),3)",
        ),
        (
            "LICENSE_FILE",
            "INSERT INTO public.private_file("
            "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
            "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,"
            "created_at,expires_at,scanned_at,bound_at) VALUES "
            f"('{license_file_id}','BUSINESS_LICENSE',{org_admin_id},1,'application/pdf',repeat('d',64),"
            f"1,'application/pdf',repeat('d',64),'slice2/license/{license_file_id}','CLEAN','{application_id}',"
            "now(),now()+interval '1 day',now(),now())",
        ),
        (
            "LICENSE",
        "INSERT INTO public.institution_license("
        "license_id,application_id,license_type,license_no_ciphertext,license_no_digest,private_file_id,created_at,valid_from,valid_until) VALUES "
            f"('{license_id}','{application_id}','BUSINESS_LICENSE',decode('00','hex'),repeat('c',64),'{license_file_id}',now(),current_date-1,current_date+180)",
        ),
    )
    safe_stages = (
        "STAGE_B1_SESSION_LOCK",
        "STAGE_B2_AUDIT_VIEW_REPLAY",
        "STAGE_B3_SOURCE_MAX_ID",
        "STAGE_B4_SOURCE_COUNT",
        "STAGE_B5_GENERATION_PERSIST",
        "STAGE_B6_CHECKPOINT_PERSIST",
        "STAGE_B7_TRANSACTION_COMMIT",
    )
    for (_, statement), safe_stage in zip(statements, safe_stages, strict=True):
        try:
            await pg_database._execute(statement)
        except Exception:
            raise RuntimeError("SLICE2_SEED_FAILED") from None
        _mark_stage(safe_stage)
    return tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id


@pytest.mark.asyncio
async def test_mutation_commit_unknown完整后像三态确认(pg_database):
    from fastapi import HTTPException

    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.core.security import CurrentUser
    from app.modules.therapist_qualification.schemas import TherapistInvitationCreate
    from app.modules.therapist_qualification.service import (
        COMMITTED,
        UNKNOWN,
        _confirm_mutation_outcome,
        _digest,
        _mutation_postimage_snapshot,
        create_invitation,
    )

    tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = (
        await _seed_ready_institution(pg_database, offset=1500)
    )
    actor = CurrentUser(
        id=org_admin_id, role="org_admin", tenant_id=tenant_id, org_id=county_id
    )
    scope = f"tenant:{tenant_id}:actor:{org_admin_id}"
    operation = "CREATE_INVITATION"
    key = "slice2-commit-confirmation"
    writer = get_slice2_session_factory("onboarding_writer")
    reader = get_slice2_session_factory("reader")
    try:
        async with writer() as session:
            response = await create_invitation(
                _CommitOutcomeSession(session, committed=True),
                actor,
                TherapistInvitationCreate(
                    phone="13" + "7" + ("0" * 8), expires_in_minutes=30
                ),
                _request_id(),
                key,
                tenant_public_id=tenant_public_id,
            )
        async with reader() as confirmation:
            expected = await _mutation_postimage_snapshot(
                confirmation,
                scope=scope,
                operation=operation,
                key=key,
                response=response,
            )
            assert await _confirm_mutation_outcome(
                confirmation,
                expected=expected,
                scope=scope,
                operation=operation,
                key=key,
                response=response,
            ) == COMMITTED
            partial = dict(expected)
            partial["audit"] = [*expected["audit"], {"postimage_digest": _digest(response)}]
            assert await _confirm_mutation_outcome(
                confirmation,
                expected=partial,
                scope=scope,
                operation=operation,
                key=key,
                response=response,
            ) == UNKNOWN
            await confirmation.rollback()

        async with writer() as session:
            with pytest.raises(HTTPException) as rolled_back:
                await create_invitation(
                    _CommitOutcomeSession(session, committed=False),
                    actor,
                    TherapistInvitationCreate(
                        phone="13" + "6" + ("0" * 8), expires_in_minutes=30
                    ),
                    _request_id(),
                    "slice2-commit-not-committed",
                    tenant_public_id=tenant_public_id,
                )
            assert rolled_back.value.status_code == 503
            assert rolled_back.value.detail == "COMMIT_OUTCOME_UNKNOWN"
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.therapist_invitation "
            f"WHERE tenant_id={tenant_id}"
        ) == 1
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )


@pytest.mark.asyncio
async def test_激活TOTP与五次失败锁定后正确凭证仍拒绝(pg_database):
    from fastapi import HTTPException

    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.core.security import CurrentUser
    from app.modules.institution_onboarding.domain import generate_totp
    from app.modules.therapist_qualification.schemas import (
        TherapistActivate,
        TherapistInvitationCreate,
    )
    from app.modules.therapist_qualification.service import (
        activate,
        create_invitation,
        utcnow,
    )

    tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = (
        await _seed_ready_institution(pg_database, offset=200)
    )
    factory = get_slice2_session_factory("onboarding_writer")
    actor = CurrentUser(
        id=org_admin_id, role="org_admin", tenant_id=tenant_id, org_id=county_id
    )
    phone = "13" + "8" + ("0" * 8)
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    try:
        async with factory() as session:
            issued = await create_invitation(
                session,
                actor,
                TherapistInvitationCreate(phone=phone, expires_in_minutes=30),
                _request_id(),
                "slice2-totp-lock-invite",
                tenant_public_id=tenant_public_id,
            )
        correct = generate_totp(secret, at=utcnow())
        wrong = "000000" if correct != "000000" else "000001"
        for attempt in range(5):
            async with factory() as session:
                with pytest.raises(HTTPException) as error:
                    await activate(
                        session,
                        TherapistActivate(
                            invitation_id=issued["invitation_id"],
                            phone=phone,
                            short_code=issued["short_code"],
                            password="SliceTwoStrongPassword!",
                            totp_secret=secret,
                            totp_code=wrong,
                        ),
                        _request_id(),
                        f"slice2-totp-lock-failure-{attempt}",
                        tenant_public_id=tenant_public_id,
                    )
                assert error.value.status_code == 401
                assert error.value.detail == "THERAPIST_INVITATION_INVALID"
        async with factory() as session:
            with pytest.raises(HTTPException) as exhausted:
                await activate(
                    session,
                    TherapistActivate(
                        invitation_id=issued["invitation_id"],
                        phone=phone,
                        short_code=issued["short_code"],
                        password="SliceTwoStrongPassword!",
                        totp_secret=secret,
                        totp_code=correct,
                    ),
                    _request_id(),
                    "slice2-totp-lock-correct-after-five",
                    tenant_public_id=tenant_public_id,
                )
            assert exhausted.value.detail == "THERAPIST_ACTIVATION_ATTEMPTS_EXHAUSTED"
        assert await pg_database._fetch_value(
            "SELECT failed_attempts FROM public.therapist_invitation "
            f"WHERE invitation_id='{issued['invitation_id']}'"
        ) == 5
        assert await pg_database._fetch_value(
            "SELECT count(*) FROM public.therapist_profile "
            f"WHERE invitation_id='{issued['invitation_id']}'"
        ) == 0
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )


async def _cleanup_slice2_seed(
    pg_database,
    *,
    tenant_id: int,
    org_admin_id: int,
    reviewer_id: int,
    county_id: int,
) -> None:
    await pg_database._execute(
        "TRUNCATE TABLE "
        "public.therapist_workflow_delivery,public.therapist_workflow_outbox,"
        "public.therapist_workflow_audit,public.therapist_workflow_idempotency,"
        "public.readiness_evidence,public.institution_service_readiness,"
        "public.therapist_status_decision,public.therapist_review_decision,"
        "public.therapist_review_item"
    )
    # Slice 3 adds cyclic current-pointer FKs around service_case and assignment,
    # while Slice 2 itself has a profile/current-qualification cycle. Scoped
    # cleanup preserves unrelated Slice 3/P1 facts and never disables an FK.
    await pg_database._execute(
        "BEGIN;"
        "UPDATE public.therapist_profile SET status='ACTIVATED',current_revision_no=0,"
        "current_qualification_version_id=NULL,qualification_valid_until=NULL,"
        "submitted_at=NULL,reviewed_at=NULL,suspended_at=NULL,resumed_at=NULL,"
        "exited_at=NULL,suspension_reason_code=NULL "
        f"WHERE tenant_id={tenant_id};"
        "DELETE FROM public.therapist_qualification_attachment a USING "
        "public.therapist_qualification_version q,public.therapist_profile p "
        "WHERE a.qualification_version_id=q.qualification_version_id "
        "AND q.therapist_id=p.therapist_id "
        f"AND p.tenant_id={tenant_id};"
        "DELETE FROM public.therapist_profile_revision_qualification rq USING "
        "public.therapist_profile p WHERE rq.therapist_id=p.therapist_id "
        f"AND p.tenant_id={tenant_id};"
        "DELETE FROM public.therapist_qualification_version q USING "
        "public.therapist_profile p WHERE q.therapist_id=p.therapist_id "
        f"AND p.tenant_id={tenant_id};"
        "DELETE FROM public.therapist_profile_revision r USING "
        "public.therapist_profile p WHERE r.therapist_id=p.therapist_id "
        f"AND p.tenant_id={tenant_id};"
        f"DELETE FROM public.therapist_profile WHERE tenant_id={tenant_id};"
        f"DELETE FROM public.therapist_invitation WHERE tenant_id={tenant_id};"
        "COMMIT"
    )
    await pg_database._execute(
        "DELETE FROM public.institution_license WHERE application_id IN "
        f"(SELECT application_id FROM public.institution_application WHERE tenant_internal_id={tenant_id})"
    )
    await pg_database._execute(
        "DELETE FROM public.private_file WHERE owner_user_id IN "
        f"(SELECT id FROM public.\"user\" WHERE tenant_id={tenant_id})"
    )
    await pg_database._execute(
        f"DELETE FROM public.institution_application WHERE tenant_internal_id={tenant_id}"
    )
    await pg_database._execute(
        "DELETE FROM public.institution_invitation WHERE issued_by="
        f"{reviewer_id} AND administrative_region_id={county_id}"
    )
    await pg_database._execute(
        f"DELETE FROM public.\"user\" WHERE tenant_id={tenant_id} OR id={reviewer_id}"
    )
    await pg_database._execute(f"DELETE FROM public.tenant WHERE id={tenant_id}")
    await pg_database._execute(
        "DELETE FROM public.platform_org WHERE id IN "
        f"({county_id},{county_id - 1},{county_id - 2},{county_id - 3})"
    )


async def _seed_approved_therapist(
    pg_database,
    *,
    tenant_id: int,
    issued_by: int,
    offset: int,
    valid_until: date,
) -> dict[str, str | int]:
    invitation_id = str(uuid.uuid4())
    therapist_id = str(uuid.uuid4())
    revision_id = str(uuid.uuid4())
    qualification_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    user_id = 43001 + offset
    await pg_database._execute(
        "INSERT INTO public.therapist_invitation("
        "invitation_id,tenant_id,phone_ciphertext,phone_encryption_key_id,"
        "phone_digest,phone_digest_key_id,phone_masked,code_digest,code_digest_key_id,"
        "expires_at,status,failed_attempts,issued_by,issued_at,activated_at,version) VALUES "
        f"('{invitation_id}',{tenant_id},decode('00','hex'),'k1',repeat('1',64),'k1',"
        f"'*******0000',repeat('2',64),'k1',now()+interval '1 day','ACTIVATED',0,{issued_by},now(),now(),1);"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES "
        f"({user_id},'15' || '0' || lpad('{offset}',8,'0'),'test-only','therapist','active',{tenant_id});"
        "INSERT INTO public.therapist_profile("
        "therapist_id,user_id,tenant_id,invitation_id,status,capacity_limit,active_case_count,"
        "current_revision_no,totp_secret_ciphertext,totp_encryption_key_id,totp_enabled,"
        "activated_at,created_at,updated_at,version) VALUES "
        f"('{therapist_id}',{user_id},{tenant_id},'{invitation_id}','DRAFT',30,0,0,decode('00','hex'),'k1',true,now(),now(),now(),1);"
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
        "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,"
        "created_at,expires_at,scanned_at,bound_at) VALUES "
        f"('{file_id}','THERAPIST_QUALIFICATION',{user_id},1,'application/pdf',repeat('3',64),"
        f"1,'application/pdf',repeat('3',64),'slice2/qualification/{file_id}','CLEAN',NULL,now(),now()+interval '1 day',now(),now());"
        "INSERT INTO public.therapist_profile_revision("
        "revision_id,therapist_id,revision_no,profile_snapshot,input_digest,created_at) VALUES "
        f"('{revision_id}','{therapist_id}',1,'{{\"v\":1}}'::jsonb,repeat('4',64),now());"
        "INSERT INTO public.therapist_qualification_version("
        "qualification_version_id,therapist_id,profile_revision_id,previous_version_id,"
        "qualification_type,certificate_no_ciphertext,certificate_encryption_key_id,"
        "certificate_no_digest,certificate_digest_key_id,certificate_no_masked,issuer_name,"
        "valid_from,valid_until,attachment_count,version_no,created_at) VALUES "
        f"('{qualification_id}','{therapist_id}','{revision_id}',NULL,'METABOLIC_HEALTH_PRACTICE',"
        f"decode('00','hex'),'k1',repeat('5',64),'k1','****0001','一期测试签发机构',current_date-30,'{valid_until.isoformat()}',1,1,now());"
        "INSERT INTO public.therapist_profile_revision_qualification("
        "therapist_id,revision_id,qualification_version_id,position) VALUES "
        f"('{therapist_id}','{revision_id}','{qualification_id}',1);"
        "INSERT INTO public.therapist_qualification_attachment("
        "qualification_version_id,slot,private_file_id,created_at) VALUES "
        f"('{qualification_id}',1,'{file_id}',now());"
        "UPDATE public.therapist_profile SET "
        "real_name_ciphertext=decode('00','hex'),real_name_encryption_key_id='k1',"
        "real_name_digest=repeat('6',64),real_name_digest_key_id='k1',"
        "display_name='测试健管师',practice_summary='代谢健康管理',"
        "service_tags='[\"GLUCOSE_METABOLISM\"]'::jsonb,status='APPROVED_ACTIVE',"
        f"current_revision_no=1,current_qualification_version_id='{qualification_id}',"
        f"qualification_valid_until='{valid_until.isoformat()}',submitted_at=now(),reviewed_at=now(),"
        f"updated_at=now() WHERE therapist_id='{therapist_id}'"
    )
    return {
        "file_id": file_id,
        "qualification_id": qualification_id,
        "therapist_id": therapist_id,
        "user_id": user_id,
    }


@pytest.mark.asyncio
async def test_邀请至批准与SERVICE_READY完整闭环(
    pg_database, tmp_path, monkeypatch
):
    from app.core.config import get_settings
    from app.core.database import (
        dispose_database_runtimes,
        get_slice1_session_factory,
        get_slice2_session_factory,
    )
    from app.core.security import CurrentUser
    from app.modules.institution_onboarding.domain import generate_totp
    from app.modules.private_file.repository import PrivateFileRepository
    from app.modules.private_file.schemas import UploadCompleteRequest, UploadInitiateRequest
    from app.modules.private_file.service import (
        complete_upload,
        initiate_upload,
        record_scan,
        upload_content,
    )
    from app.modules.therapist_qualification.repository import TherapistQualificationRepository
    from app.modules.therapist_qualification.schemas import (
        QualificationInput,
        TherapistActivate,
        TherapistInvitationCreate,
        TherapistProfileDraft,
        TherapistReviewDecisionRequest,
        TherapistSubmit,
    )
    from app.modules.therapist_qualification.service import (
        activate,
        create_invitation,
        read_readiness_fail_closed,
        review_decision,
        save_profile,
        submit,
        utcnow,
    )

    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv(
        "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY",
        "slice2-disposable-private-file-access-key",
    )
    get_settings.cache_clear()
    tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = await _seed_ready_institution(
        pg_database
    )
    onboarding = get_slice2_session_factory("onboarding_writer")
    reviewer = get_slice2_session_factory("review_writer")
    reader = get_slice2_session_factory("reader")
    file_writer = get_slice1_session_factory("file_writer")
    file_reader = get_slice1_session_factory("reader")
    admin_actor = CurrentUser(
        id=org_admin_id,
        role="org_admin",
        tenant_id=tenant_id,
        org_id=county_id,
    )
    platform_actor = CurrentUser(id=reviewer_id, role="super_admin")
    phone = "13" + "7" + ("0" * 8)
    totp_secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    try:
        async with onboarding() as session:
            issued = await create_invitation(
                session,
                admin_actor,
                TherapistInvitationCreate(phone=phone, expires_in_minutes=30),
                _request_id(),
                "slice2-invite-idempotency",
                tenant_public_id=tenant_public_id,
            )
        _mark_stage("STAGE_H1_SESSION_LOCK")
        async with onboarding() as session:
            activated = await activate(
                session,
                TherapistActivate(
                    invitation_id=issued["invitation_id"],
                    phone=phone,
                    short_code=issued["short_code"],
                    password="SliceTwoStrongPassword!",
                    totp_secret=totp_secret,
                    totp_code=generate_totp(totp_secret, at=utcnow()),
                ),
                _request_id(),
                "slice2-activate-idempotency",
                tenant_public_id=tenant_public_id,
            )
        _mark_stage("STAGE_H2_AUDIT_REPLAY")
        therapist_actor = CurrentUser(
            id=await pg_database._fetch_value(
                f"SELECT user_id FROM public.therapist_profile WHERE therapist_id='{activated['therapist_id']}'"
            ),
            role="therapist",
            tenant_id=tenant_id,
            org_id=county_id,
        )
        async with onboarding() as session:
            draft = await save_profile(
                session,
                therapist_actor,
                TherapistProfileDraft(
                    real_name="一期测试健管师",
                    display_name="测试健管师",
                    practice_summary="代谢健康管理",
                    service_tags=("GLUCOSE_METABOLISM",),
                    expected_version=activated["version"],
                ),
                _request_id(),
                "slice2-draft-idempotency",
                tenant_public_id=tenant_public_id,
            )
        _mark_stage("STAGE_H3_SNAPSHOT_EXPORT")

        content = b"%PDF-1.7\nslice-two-qualification\n%%EOF"
        digest = hashlib.sha256(content).hexdigest()
        async with file_writer() as session:
            upload = await initiate_upload(
                session,
                therapist_actor.id,
                UploadInitiateRequest(
                    purpose="THERAPIST_QUALIFICATION",
                    size=len(content),
                    mime_type="application/pdf",
                    sha256=digest,
                ),
            )
        async with file_writer() as session:
            await upload_content(session, therapist_actor.id, upload["file_id"], content)
        async with file_writer() as session:
            await complete_upload(
                session,
                therapist_actor.id,
                upload["file_id"],
                UploadCompleteRequest(
                    size=len(content),
                    mime_type="application/pdf",
                    sha256=digest,
                ),
            )
        async with file_writer() as session:
            scanned = await record_scan(
                session,
                upload["file_id"],
                scanner=_CleanScanner(),
            )
            assert scanned["status"] == "CLEAN"
        _mark_stage("STAGE_H4_SOURCE_MAX_VISIBLE_ID")

        qualification = QualificationInput(
            qualification_type="METABOLIC_HEALTH_PRACTICE",
            certificate_no="SLICE2CERT001",
            issuer_name="一期测试签发机构",
            valid_from=date.today() - timedelta(days=1),
            valid_until=date.today() + timedelta(days=365),
            attachment_file_ids=(upload["file_id"],),
        )
        async with onboarding() as session:
            submitted = await submit(
                session,
                therapist_actor,
                TherapistSubmit(
                    expected_version=draft["version"],
                    qualification=qualification,
                ),
                _request_id(),
                "slice2-submit-idempotency",
                tenant_public_id=tenant_public_id,
            )
        _mark_stage("STAGE_H5_SOURCE_VISIBLE_COUNT_AND_SUCCESSOR")
        async with file_reader() as session:
            relation = await PrivateFileRepository(session).qualification_relation(
                upload["file_id"]
            )
            assert relation == {"qualification_bound": True, "reviewer_access": True}

        async with reviewer() as session:
            claimed = await review_decision(
                session,
                platform_actor,
                activated["therapist_id"],
                TherapistReviewDecisionRequest(
                    decision="START_REVIEW",
                    expected_version=submitted["version"],
                ),
                _request_id(),
                "slice2-review-start-idempotency",
            )
            assert claimed["review_item"]["status"] == "UNDER_REVIEW"
        _mark_stage("STAGE_H6_GENERATION_AND_CHECKPOINT")

        qualification_id = await pg_database._fetch_value(
            f"SELECT qualification_version_id FROM public.therapist_qualification_version WHERE therapist_id='{activated['therapist_id']}'"
        )
        async with reviewer() as session:
            approved = await review_decision(
                session,
                platform_actor,
                activated["therapist_id"],
                TherapistReviewDecisionRequest(
                    decision="APPROVED",
                    qualification_outcomes={qualification_id: "APPROVED"},
                    expected_version=claimed["profile"]["version"],
                ),
                _request_id(),
                "slice2-review-approve-idempotency",
            )
            assert approved["profile"]["status"] == "APPROVED_ACTIVE"
        _mark_stage("STAGE_H7_AUDIT_AND_COMMIT")
        async with reader() as session:
            public_repo = TherapistQualificationRepository(session)
            profile_summary = await public_repo.profile_for_user_summary(therapist_actor.id)
            assert profile_summary is not None
            assert profile_summary["practice_summary"] == "代谢健康管理"
            assert profile_summary["therapist_id"] == activated["therapist_id"]
            qualifications = await public_repo.qualifications(activated["therapist_id"])
            assert len(qualifications) == 1
            assert qualifications[0]["masked_certificate_no"]
            assert "certificate_no_ciphertext" not in qualifications[0]
            assert len(await public_repo.revisions(activated["therapist_id"])) == 1
            assert await public_repo.current_review_item(activated["therapist_id"]) is not None
            public_evidence = await public_repo.readiness_evidence(tenant_id)
            assert len(public_evidence) >= 1
            assert set(public_evidence[0]) == {
                "tenant_id", "evidence_version", "readiness_status", "reason_codes",
                "qualified_therapist_count", "computed_at", "input_digest", "result_digest",
            }
        async with reader() as session:
            readiness, stale = await read_readiness_fail_closed(session, tenant_id)
            assert stale is False
            assert readiness is not None
            assert readiness["tenant_id"] == tenant_public_id
            assert readiness["readiness_status"] == "SERVICE_READY"
            assert readiness["qualified_therapist_count"] == 1
        assert await pg_database._fetch_value(
            "SELECT next_expiry_at=current_date+180 "
            f"FROM public.institution_service_readiness WHERE tenant_id={tenant_id}"
        )
        async with file_reader() as session:
            relation = await PrivateFileRepository(session).qualification_relation(
                upload["file_id"]
            )
            assert relation == {"qualification_bound": True, "reviewer_access": False}
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )


@pytest.mark.asyncio
async def test_四身份逐列最小权限与跨租户拒绝(pg_database):
    roles = {
        "onboarding": os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_ROLE"],
        "reviewer": os.environ["KG_TEST_THERAPIST_REVIEW_WRITER_ROLE"],
        "worker": os.environ["KG_TEST_THERAPIST_READINESS_WORKER_ROLE"],
        "reader": os.environ["KG_TEST_THERAPIST_READER_ROLE"],
    }

    positive_columns = (
        (roles["onboarding"], "therapist_invitation", "invitation_id", "INSERT"),
        (roles["onboarding"], "therapist_profile", "reviewed_at", "UPDATE"),
        (roles["reviewer"], "therapist_review_decision", "decision", "INSERT"),
        (roles["reviewer"], "therapist_profile", "status", "UPDATE"),
        (roles["worker"], "therapist_workflow_outbox", "attempts", "UPDATE"),
        (roles["worker"], "therapist_workflow_outbox", "event_id", "INSERT"),
        (roles["worker"], "therapist_workflow_delivery", "event_id", "INSERT"),
        (roles["reader"], "therapist_invitation", "phone_masked", "SELECT"),
        (roles["reader"], "therapist_profile", "practice_summary", "SELECT"),
        (roles["reader"], "institution_service_readiness", "readiness_status", "SELECT"),
        (roles["reader"], "readiness_evidence", "result_digest", "SELECT"),
    )
    for role, table, column, privilege in positive_columns:
        assert await pg_database._fetch_value(
            "SELECT has_column_privilege("
            f"'{role}','public.{table}','{column}','{privilege}')"
        )

    denied_columns = (
        (roles["onboarding"], "therapist_review_decision", "decision", "INSERT"),
        (roles["reviewer"], "therapist_workflow_outbox", "status", "UPDATE"),
        (roles["reviewer"], "therapist_invitation", "phone_ciphertext", "SELECT"),
        (roles["worker"], "user", "phone", "SELECT"),
        (roles["worker"], "therapist_profile", "display_name", "SELECT"),
        (roles["reader"], "therapist_invitation", "phone_ciphertext", "SELECT"),
        (roles["reader"], "readiness_evidence", "source_versions", "SELECT"),
        (roles["reader"], "therapist_workflow_delivery", "payload", "SELECT"),
    )
    for role, table, column, privilege in denied_columns:
        assert not await pg_database._fetch_value(
            "SELECT has_column_privilege("
            f"'{role}','public.{table}','{column}','{privilege}')"
        )

    protected_tables = (
        "therapist_invitation",
        "therapist_profile",
        "therapist_review_decision",
        "institution_service_readiness",
        "therapist_workflow_outbox",
        "therapist_workflow_delivery",
    )
    for role in roles.values():
        for table in protected_tables:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert not await pg_database._fetch_value(
                    "SELECT has_table_privilege("
                    f"'{role}','public.{table}','{privilege}')"
                )

    assert await pg_database._fetch_value(
        "SELECT has_function_privilege("
        f"'{roles['worker']}',"
        "'public.record_therapist_expiry_suspension_v1(uuid,uuid,bigint,character)','EXECUTE')"
    )
    assert await pg_database._fetch_value(
        "SELECT has_function_privilege("
        f"'{roles['worker']}',"
        "'public.is_current_slice2_recovery_actor_v1(bigint)','EXECUTE')"
    )
    assert not await pg_database._fetch_value(
        "SELECT has_function_privilege("
        f"'{roles['reader']}',"
        "'public.is_current_slice2_recovery_actor_v1(bigint)','EXECUTE')"
    )


def test_0020至0021升级降级再升级及权限残留零(pg_database):
    from alembic import command
    from conftest import _build_alembic_config, _get_test_database_url

    config = _build_alembic_config(_get_test_database_url())
    command.downgrade(config, "20260816_0020")
    assert pg_database.fetch_value(
        "SELECT version_num='20260816_0020' FROM public.alembic_version"
    )
    assert pg_database.fetch_value(
        "SELECT to_regclass('public.therapist_profile') IS NULL"
    )
    assert pg_database.fetch_value(
        "SELECT to_regclass('public.institution_readiness_guard_v1') IS NULL"
    )
    assert pg_database.fetch_value(
        "SELECT to_regprocedure('public.therapist_totp_for_login_v1(bigint)') IS NULL"
    )

    command.upgrade(config, "20260818_0022")
    assert pg_database.fetch_value(
        "SELECT version_num='20260818_0022' FROM public.alembic_version"
    )
    assert pg_database.fetch_value(
        "SELECT to_regclass('public.therapist_profile') IS NOT NULL"
    )
    assert pg_database.fetch_value(
        "SELECT to_regclass('public.institution_readiness_guard_v1') IS NOT NULL"
    )


def test_role_url_membership误配全部zero_DDL(pg_database, monkeypatch):
    import asyncio

    import asyncpg
    from alembic import command
    from conftest import PgDatabase, _build_alembic_config, _get_test_database_url

    config = _build_alembic_config(_get_test_database_url())
    admin_url = PgDatabase(os.environ["KG_TEST_ROLE_ADMIN_DATABASE_URL"]).database_url
    reader = os.environ["KG_TEST_THERAPIST_READER_ROLE"]
    external = f"kg_slice2_external_{os.environ['KG_TEST_RUN_ID']}"

    async def admin_execute(sql: str) -> None:
        connection = await asyncpg.connect(admin_url)
        try:
            await connection.execute(sql)
        finally:
            await connection.close()

    def assert_upgrade_rejected() -> None:
        with pytest.raises(RuntimeError) as error:
            command.upgrade(config, "20260818_0022")
        assert str(error.value) == "Slice 2 database role configuration is invalid"
        assert error.value.__cause__ is None
        assert pg_database.fetch_value(
            "SELECT version_num='20260816_0020' FROM public.alembic_version"
        )
        assert pg_database.fetch_value(
            "SELECT to_regclass('public.therapist_profile') IS NULL"
        )

    command.downgrade(config, "20260816_0020")
    asyncio.run(admin_execute(f'CREATE ROLE "{external}" NOLOGIN NOINHERIT'))
    try:
        asyncio.run(admin_execute(f'GRANT "{external}" TO "{reader}"'))
        assert_upgrade_rejected()
        asyncio.run(admin_execute(f'REVOKE "{external}" FROM "{reader}"'))

        asyncio.run(admin_execute(f'GRANT "{reader}" TO "{external}"'))
        assert_upgrade_rejected()
        asyncio.run(admin_execute(f'REVOKE "{reader}" FROM "{external}"'))

        monkeypatch.setenv("KG_THERAPIST_READER_ROLE", os.environ["KG_DATABASE_USER"])
        assert_upgrade_rejected()
        monkeypatch.setenv("KG_THERAPIST_READER_ROLE", reader)

        command.upgrade(config, "20260818_0022")
        asyncio.run(admin_execute(f'GRANT "{external}" TO "{reader}"'))
        try:
            with pytest.raises(RuntimeError) as error:
                command.downgrade(config, "20260816_0020")
            assert str(error.value) == "Slice 3 database role configuration is invalid"
            assert pg_database.fetch_value(
                "SELECT version_num='20260818_0022' FROM public.alembic_version"
            )
            assert pg_database.fetch_value(
                "SELECT to_regclass('public.therapist_profile') IS NOT NULL"
            )
        finally:
            asyncio.run(admin_execute(f'REVOKE "{external}" FROM "{reader}"'))
    finally:
        if pg_database.fetch_value(
            "SELECT version_num FROM public.alembic_version"
        ) != "20260818_0022":
            command.upgrade(config, "20260818_0022")
        asyncio.run(admin_execute(f'DROP ROLE IF EXISTS "{external}"'))


@pytest.mark.asyncio
async def test_readiness_bootstrap许可证到期tenant漂移与stale_read拒绝(
    pg_database,
):
    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.modules.therapist_qualification.service import read_readiness_fail_closed
    from app.tasks.therapist_qualification_tasks import _sweep_readiness

    tenant_id, org_admin_id, reviewer_id, _, county_id = await _seed_ready_institution(
        pg_database, offset=1000
    )
    reader = get_slice2_session_factory("reader")
    try:
        assert await _sweep_readiness() == 1
        async with reader() as session:
            value, stale = await read_readiness_fail_closed(session, tenant_id)
            assert stale is False
            assert value is not None
            assert value["readiness_status"] == "NOT_READY"
            assert "NO_APPROVED_ACTIVE_THERAPIST" in value["reason_codes"]

        await pg_database._execute(
            f"UPDATE public.tenant SET status='closed' WHERE id={tenant_id}"
        )
        async with reader() as session:
            value, stale = await read_readiness_fail_closed(session, tenant_id)
            assert stale is True
            assert value is not None
            assert value["readiness_status"] == "NOT_READY"
            assert "INSTITUTION_APPROVAL_SOURCE_INVALID" in value["reason_codes"]

        assert await _sweep_readiness() == 1
        assert await pg_database._fetch_value(
            "SELECT readiness_status='NOT_READY' "
            "AND 'TENANT_NOT_ACTIVE'=ANY(reason_codes) "
            "AND 'COMPLIANCE_SUSPENDED'=ANY(reason_codes) "
            f"FROM public.institution_service_readiness WHERE tenant_id={tenant_id}"
        )

        await pg_database._execute(
            f"UPDATE public.tenant SET status='active' WHERE id={tenant_id};"
            "UPDATE public.institution_license SET valid_until=current_date-1 "
            f"WHERE application_id IN (SELECT application_id FROM public.institution_application WHERE tenant_internal_id={tenant_id})"
        )
        assert await _sweep_readiness() == 1
        assert await pg_database._fetch_value(
            "SELECT readiness_status='NOT_READY' "
            "AND 'INSTITUTION_LICENSE_INVALID'=ANY(reason_codes) "
            f"FROM public.institution_service_readiness WHERE tenant_id={tenant_id}"
        )
        assert await pg_database._fetch_value(
            "SELECT count(*)=3 FROM public.readiness_evidence "
            f"WHERE tenant_id={tenant_id}"
        )
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )


@pytest.mark.asyncio
async def test_资格到期恢复与readiness证据round_trip(pg_database):
    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.core.security import CurrentUser
    from app.modules.therapist_qualification.schemas import (
        QualificationInput,
        TherapistRenew,
        TherapistReviewDecisionRequest,
    )
    from app.modules.therapist_qualification.service import review_decision, renew
    from app.tasks.therapist_qualification_tasks import _expire_due_qualifications

    tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = (
        await _seed_ready_institution(pg_database, offset=2000)
    )
    seeded = await _seed_approved_therapist(
        pg_database,
        tenant_id=tenant_id,
        issued_by=org_admin_id,
        offset=2000,
        valid_until=date.today() - timedelta(days=1),
    )
    onboarding = get_slice2_session_factory("onboarding_writer")
    reviewer = get_slice2_session_factory("review_writer")
    therapist_actor = CurrentUser(
        id=int(seeded["user_id"]), role="therapist", tenant_id=tenant_id, org_id=county_id
    )
    platform_actor = CurrentUser(id=reviewer_id, role="super_admin")
    renewal_file_id = str(uuid.uuid4())
    try:
        assert await _expire_due_qualifications() == 1
        assert await pg_database._fetch_value(
            "SELECT status='SUSPENDED' AND suspension_reason_code='QUALIFICATION_EXPIRED' "
            f"FROM public.therapist_profile WHERE therapist_id='{seeded['therapist_id']}'"
        )
        assert await pg_database._fetch_value(
            "SELECT readiness_status='NOT_READY' "
            "AND 'NO_APPROVED_ACTIVE_THERAPIST'=ANY(reason_codes) "
            f"FROM public.institution_service_readiness WHERE tenant_id={tenant_id}"
        )

        await pg_database._execute(
            "INSERT INTO public.private_file("
            "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
            "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,"
            "created_at,expires_at,scanned_at,bound_at) VALUES "
            f"('{renewal_file_id}','THERAPIST_QUALIFICATION',{therapist_actor.id},1,"
            f"'application/pdf',repeat('7',64),1,'application/pdf',repeat('7',64),"
            f"'slice2/qualification/{renewal_file_id}','CLEAN',NULL,now(),now()+interval '1 day',now(),now())"
        )
        renewal_input = QualificationInput(
            qualification_type="METABOLIC_HEALTH_PRACTICE",
            certificate_no="SLICE2RENEW001",
            issuer_name="一期续期签发机构",
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=365),
            attachment_file_ids=(renewal_file_id,),
        )
        async with onboarding() as session:
            renewed = await renew(
                session,
                therapist_actor,
                TherapistRenew(
                    expected_version=2,
                    predecessor_version_id=seeded["qualification_id"],
                    qualification=renewal_input,
                ),
                _request_id(),
                "slice2-renew-idempotency",
                tenant_public_id=tenant_public_id,
            )
        review_item_id = renewed["review_item_id"]
        async with reviewer() as session:
            claimed = await review_decision(
                session,
                platform_actor,
                str(seeded["therapist_id"]),
                TherapistReviewDecisionRequest(
                    decision="START_REVIEW", expected_version=1
                ),
                _request_id(),
                "slice2-renew-review-start",
                review_item_id=review_item_id,
            )
        qualification_id = await pg_database._fetch_value(
            "SELECT qualification_version_id FROM public.therapist_review_item "
            f"WHERE review_item_id='{review_item_id}'"
        )
        async with reviewer() as session:
            approved = await review_decision(
                session,
                platform_actor,
                str(seeded["therapist_id"]),
                TherapistReviewDecisionRequest(
                    decision="APPROVED",
                    qualification_outcomes={qualification_id: "APPROVED"},
                    expected_version=claimed["review_item"]["version"],
                ),
                _request_id(),
                "slice2-renew-review-approve",
                review_item_id=review_item_id,
            )
        assert approved["profile"]["status"] == "APPROVED_ACTIVE"
        assert await pg_database._fetch_value(
            "SELECT readiness_status='SERVICE_READY' AND qualified_therapist_count=1 "
            f"FROM public.institution_service_readiness WHERE tenant_id={tenant_id}"
        )
        assert await pg_database._fetch_value(
            "SELECT count(*)>=2 FROM public.readiness_evidence "
            f"WHERE tenant_id={tenant_id}"
        )
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )


@pytest.mark.asyncio
async def test_资质附件约束与PrivateFile并发绑定(pg_database):
    import asyncio

    import asyncpg
    from sqlalchemy.engine import make_url

    from app.core.database import dispose_database_runtimes, get_slice2_session_factory
    from app.modules.therapist_qualification.repository import (
        TherapistQualificationRepository,
    )

    tenant_id, org_admin_id, reviewer_id, _, county_id = await _seed_ready_institution(
        pg_database, offset=3000
    )
    seeded = await _seed_approved_therapist(
        pg_database,
        tenant_id=tenant_id,
        issued_by=org_admin_id,
        offset=3000,
        valid_until=date.today() + timedelta(days=365),
    )
    owner_id = int(seeded["user_id"])
    clean_ids = tuple(str(uuid.uuid4()) for _ in range(4))
    pending_id = str(uuid.uuid4())
    cross_owner_id = str(uuid.uuid4())
    values = []
    for file_id in clean_ids:
        values.append(
            f"('{file_id}','THERAPIST_QUALIFICATION',{owner_id},'CLEAN')"
        )
    values.extend(
        (
            f"('{pending_id}','THERAPIST_QUALIFICATION',{owner_id},'PENDING_SCAN')",
            f"('{cross_owner_id}','THERAPIST_QUALIFICATION',{org_admin_id},'CLEAN')",
        )
    )
    await pg_database._execute(
        "INSERT INTO public.private_file("
        "file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,"
        "actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,"
        "created_at,expires_at,scanned_at,bound_at) "
        "SELECT source.file_id::uuid,source.purpose,source.owner_id,1,'application/pdf',"
        "repeat('8',64),1,'application/pdf',repeat('8',64),"
        "'slice2/qualification/'||source.file_id,source.status,NULL,now(),now()+interval '1 day',"
        "CASE WHEN source.status='CLEAN' THEN now() ELSE NULL END,"
        "CASE WHEN source.status='CLEAN' THEN now() ELSE NULL END FROM (VALUES "
        + ",".join(values)
        + ") AS source(file_id,purpose,owner_id,status)"
    )
    onboarding = get_slice2_session_factory("onboarding_writer")
    try:
        async with onboarding() as session:
            repo = TherapistQualificationRepository(session)
            assert await repo.lock_clean_files(file_ids=(), owner_user_id=owner_id) == ()
            assert await repo.lock_clean_files(
                file_ids=clean_ids, owner_user_id=owner_id
            ) == ()
            assert await repo.lock_clean_files(
                file_ids=(clean_ids[0], clean_ids[0]), owner_user_id=owner_id
            ) == ()
            assert await repo.lock_clean_files(
                file_ids=(pending_id,), owner_user_id=owner_id
            ) == ()
            assert await repo.lock_clean_files(
                file_ids=(cross_owner_id,), owner_user_id=owner_id
            ) == ()
            locked = await repo.lock_clean_files(
                file_ids=clean_ids[:3], owner_user_id=owner_id
            )
            assert tuple(str(row[0]) for row in locked) == tuple(sorted(clean_ids[:3]))
            await session.rollback()

        parsed = make_url(os.environ["KG_TEST_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"])
        first = await asyncpg.connect(
            user=parsed.username,
            password=parsed.password,
            host=parsed.host,
            port=parsed.port,
            database=parsed.database,
        )
        second = await asyncpg.connect(
            user=parsed.username,
            password=parsed.password,
            host=parsed.host,
            port=parsed.port,
            database=parsed.database,
        )
        first_tx = first.transaction()
        second_tx = second.transaction()
        await first_tx.start()
        await second_tx.start()
        revision_id = str(uuid.uuid4())
        qualification_id = str(uuid.uuid4())
        try:
            first_rows = await first.fetch(
                "SELECT * FROM public.lock_therapist_qualification_files_v1($1::uuid[],$2)",
                [clean_ids[0]],
                owner_id,
            )
            assert len(first_rows) == 1
            waiting = asyncio.create_task(
                second.fetch(
                    "SELECT * FROM public.lock_therapist_qualification_files_v1($1::uuid[],$2)",
                    [clean_ids[0]],
                    owner_id,
                )
            )
            await asyncio.sleep(0.1)
            assert waiting.done() is False
            await first.execute(
                "INSERT INTO public.therapist_profile_revision("
                "revision_id,therapist_id,revision_no,profile_snapshot,input_digest,created_at) "
                f"VALUES('{revision_id}','{seeded['therapist_id']}',2,'{{\"v\":1}}'::jsonb,repeat('9',64),now());"
                "INSERT INTO public.therapist_qualification_version("
                "qualification_version_id,therapist_id,profile_revision_id,previous_version_id,"
                "qualification_type,certificate_no_ciphertext,certificate_encryption_key_id,"
                "certificate_no_digest,certificate_digest_key_id,certificate_no_masked,issuer_name,"
                "valid_from,valid_until,attachment_count,version_no,created_at) VALUES "
                f"('{qualification_id}','{seeded['therapist_id']}','{revision_id}','{seeded['qualification_id']}',"
                "'METABOLIC_HEALTH_PRACTICE',decode('00','hex'),'k1',repeat('a',64),'k1',"
                "'****0002','一期测试签发机构',current_date,current_date+365,1,2,now());"
                "INSERT INTO public.therapist_profile_revision_qualification("
                "therapist_id,revision_id,qualification_version_id,position) VALUES "
                f"('{seeded['therapist_id']}','{revision_id}','{qualification_id}',1);"
                "INSERT INTO public.therapist_qualification_attachment("
                "qualification_version_id,slot,private_file_id,created_at) VALUES "
                f"('{qualification_id}',1,'{clean_ids[0]}',now())"
            )
            await first_tx.commit()
            assert await waiting == []
            await second_tx.rollback()
        finally:
            if not first.is_closed():
                await first.close()
            if not second.is_closed():
                await second.close()
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.therapist_qualification_attachment "
            f"WHERE private_file_id='{clean_ids[0]}'"
        )
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )


@pytest.mark.asyncio
async def test_outbox事件目录_attempt推进_admin扇出_RabbitMQ崩溃接管(pg_database):
    from app.core.database import dispose_database_runtimes
    from app.tasks.therapist_qualification_tasks import (
        claim_next_event,
        consume_event,
        recover_stale_events,
        reopen_failed_event,
    )

    tenant_id, org_admin_id, reviewer_id, tenant_public_id, county_id = (
        await _seed_ready_institution(pg_database, offset=4000)
    )
    event_id = str(uuid.uuid4())
    stale_event_id = str(uuid.uuid4())

    async def insert_event(value: str, *, created_offset: int) -> None:
        await pg_database._execute(
            "INSERT INTO public.therapist_workflow_outbox("
            "event_id,event_type,aggregate_id,tenant_id,payload,payload_digest,status,"
            "attempts,processing_at,lease_owner,delivered_at,failed_at,created_at,version) VALUES "
            f"('{value}','SERVICE_READINESS_RECOMPUTED','{tenant_public_id}',{tenant_id},"
            f"'{{\"v\":1}}'::jsonb,repeat('7',64),'PENDING',0,NULL,NULL,NULL,NULL,"
            f"now()+({created_offset})*interval '1 second',1)"
        )

    try:
        await insert_event(event_id, created_offset=0)
        await pg_database._execute(
            f'UPDATE public."user" SET status=\'disabled\' WHERE id={org_admin_id}'
        )
        for attempt, expected_status in ((1, "PENDING"), (2, "PENDING"), (3, "FAILED")):
            assert await claim_next_event() == event_id
            assert await consume_event(event_id) == expected_status
            assert await pg_database._fetch_value(
                "SELECT attempts="
                f"{attempt} AND status='{expected_status}' FROM public.therapist_workflow_outbox "
                f"WHERE event_id='{event_id}'"
            )
        failed_version = await pg_database._fetch_value(
            "SELECT version FROM public.therapist_workflow_outbox "
            f"WHERE event_id='{event_id}'"
        )
        reopened = await reopen_failed_event(
            actor_user_id=reviewer_id,
            event_id=event_id,
            expected_version=failed_version,
            idempotency_key="slice2-pg07-reopen",
        )
        assert reopened["status"] == "PENDING"
        assert reopened["attempts"] == 0

        operator_id = org_admin_id + 100
        await pg_database._execute(
            f'UPDATE public."user" SET status=\'active\' WHERE id={org_admin_id};'
            'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
            f"({operator_id},'1370000{operator_id % 10000:04d}','test-only','org_operator','active',{tenant_id})"
        )
        assert await claim_next_event() == event_id
        assert await consume_event(event_id) == "DELIVERED"
        assert await consume_event(event_id) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT count(*)=2 FROM public.therapist_workflow_delivery "
            f"WHERE event_id='{event_id}'"
        )

        await insert_event(stale_event_id, created_offset=1)
        assert await claim_next_event() == stale_event_id
        await pg_database._execute(
            "UPDATE public.therapist_workflow_outbox SET processing_at=now()-interval '10 minutes' "
            f"WHERE event_id='{stale_event_id}'"
        )
        assert await recover_stale_events() == 1
        assert await pg_database._fetch_value(
            "SELECT status='PENDING' AND attempts=1 AND processing_at IS NULL "
            "AND lease_owner IS NULL FROM public.therapist_workflow_outbox "
            f"WHERE event_id='{stale_event_id}'"
        )
    finally:
        await dispose_database_runtimes()
        await _cleanup_slice2_seed(
            pg_database,
            tenant_id=tenant_id,
            org_admin_id=org_admin_id,
            reviewer_id=reviewer_id,
            county_id=county_id,
        )
