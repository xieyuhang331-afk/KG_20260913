from __future__ import annotations

import hashlib
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


pytestmark = pytest.mark.integration


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


def _synthetic_mobile(prefix: str) -> str:
    return prefix + ("0" * 8)


def _mark_stage(stage: str) -> None:
    progress_path = os.environ.get("KG_SAFE_PROGRESS_FILE")
    if progress_path:
        with Path(progress_path).open("a", encoding="ascii") as stream:
            stream.write(f"STAGE_{stage}\n")


def _mark_safe_field(name: str, value: str) -> None:
    progress_path = os.environ.get("KG_SAFE_PROGRESS_FILE")
    if progress_path:
        with Path(progress_path).open("a", encoding="ascii") as stream:
            stream.write(f"{name}={value}\n")


def _safe_worker_error_code(exc: Exception) -> str:
    allowed = {
        "PRIVATE_FILE_NOT_FOUND",
        "PRIVATE_FILE_STATE_CONFLICT",
        "PRIVATE_FILE_EVIDENCE_MISMATCH",
        "PRIVATE_FILE_COMMIT_OUTCOME_UNKNOWN",
        "PRIVATE_FILE_COMMIT_ROLLED_BACK",
        "PRIVATE_FILE_WORKER_REJECTED",
    }
    if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] in allowed:
        return exc.args[0]
    return "UNKNOWN"


async def _seed_closure_sources(application_database) -> None:
    _mark_stage("SEED_ORGANIZATION")
    await application_database._execute("INSERT INTO public.platform_org(parent_id,org_name,org_code,org_type,status,version) VALUES (NULL,'一期测试总部','P1-HQ','headquarter','active',1), (1,'一期测试省','P1-P','province','active',1), (2,'一期测试市','P1-C','city','active',1), (3,'一期测试区县','P1-D','county','active',1)")
    _mark_stage("SEED_USER")
    await application_database._execute("INSERT INTO public.\"user\"(phone,password_hash,role,status) VALUES ('137' || repeat('0', 8),'test-only','super_admin','active')")


def test_0020对象与四身份最小权限(pg_database, application_database):
    assert pg_database.fetch_value("SELECT version_num FROM alembic_version") == "20260816_0020"
    assert pg_database.fetch_value("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('institution_invitation','institution_onboarding_account','institution_application','institution_application_revision','institution_license','private_file','institution_onboarding_idempotency','institution_onboarding_audit','institution_onboarding_outbox','institution_onboarding_delivery')") == 10
    application_role = os.environ["KG_TEST_APPLICATION_ROLE"]
    readonly_role = os.environ["KG_TEST_READONLY_ROLE"]
    for role in (application_role, readonly_role):
        assert not pg_database.fetch_value(f"SELECT has_table_privilege('{role}','public.institution_application','SELECT')")
        assert not pg_database.fetch_value(f"SELECT has_table_privilege('{role}','public.private_file','SELECT')")
    onboarding_role = os.environ["KG_TEST_INSTITUTION_ONBOARDING_WRITER_ROLE"]
    reviewer_role = os.environ["KG_TEST_INSTITUTION_REVIEW_WRITER_ROLE"]
    file_writer_role = os.environ["KG_TEST_PRIVATE_FILE_WRITER_ROLE"]
    reader_role = os.environ["KG_TEST_INSTITUTION_ONBOARDING_READER_ROLE"]
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{onboarding_role}','public.institution_invitation','institution_name','INSERT')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{onboarding_role}','public.institution_application','draft_payload','UPDATE')")
    assert not pg_database.fetch_value(f"SELECT has_column_privilege('{onboarding_role}','public.institution_invitation','applicant_phone_ciphertext','UPDATE')")
    assert not pg_database.fetch_value(f"SELECT has_table_privilege('{onboarding_role}','public.institution_onboarding_delivery','SELECT')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{reviewer_role}','public.institution_onboarding_delivery','event_id','INSERT')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{reviewer_role}','public.institution_onboarding_outbox','status','UPDATE')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{reviewer_role}','public.institution_application','correction_fields','UPDATE')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{reviewer_role}','public.institution_application','correction_reason_code','UPDATE')")
    assert not pg_database.fetch_value(f"SELECT has_column_privilege('{reviewer_role}','public.institution_application','service_ready','UPDATE')")
    assert not pg_database.fetch_value(f"SELECT has_column_privilege('{reviewer_role}','public.private_file','status','UPDATE')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{file_writer_role}','public.private_file','status','UPDATE')")
    assert not pg_database.fetch_value(f"SELECT has_table_privilege('{file_writer_role}','public.institution_application','SELECT')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{reader_role}','public.institution_application','status','SELECT')")
    assert pg_database.fetch_value(f"SELECT has_column_privilege('{reader_role}','public.private_file','status','SELECT')")
    assert not pg_database.fetch_value(f"SELECT has_column_privilege('{reader_role}','public.private_file','object_key','SELECT')")
    for role in (onboarding_role, reviewer_role, file_writer_role, reader_role):
        for privilege in ("DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.institution_application','{privilege}')"
            )
    assert pg_database.fetch_value(f"SELECT has_sequence_privilege('{onboarding_role}','public.user_id_seq','USAGE')")
    assert pg_database.fetch_value(f"SELECT has_sequence_privilege('{reviewer_role}','public.tenant_id_seq','USAGE')")
    for role in (file_writer_role, reader_role):
        assert not pg_database.fetch_value(f"SELECT has_sequence_privilege('{role}','public.user_id_seq','USAGE')")
        assert not pg_database.fetch_value(f"SELECT has_sequence_privilege('{role}','public.tenant_id_seq','USAGE')")


@pytest.mark.asyncio
async def test_邀请激活文件扫描提交审核批准完整闭环(pg_database, application_database, tmp_path: Path, monkeypatch, request):
    from app.core.config import get_settings
    from app.core.database import _SLICE1_RUNTIMES, get_session_factory, get_slice1_session_factory
    from app.modules.institution_onboarding.domain import generate_totp
    from app.modules.institution_onboarding.schemas import ActivationRequest, ApplicationDraftRequest, ApplicationResubmitRequest, ApplicationSubmitRequest, InvitationCreate, InvitationResendRequest, InvitationRevokeRequest, LicenseBinding, ReviewDecisionRequest
    from app.modules.institution_onboarding.service import activate, create_invitation, get_application, resend_invitation, resubmit_application, review_decision, revoke_invitation, save_draft, submit_application, utcnow
    from app.modules.private_file.schemas import UploadCompleteRequest, UploadInitiateRequest
    from app.modules.private_file.service import authorize_file_access, complete_upload, initiate_upload, read_authorized_content, upload_content
    from app.tasks import institution_onboarding_tasks as slice1_tasks

    class CleanTestScanner:
        async def scan(self, path, *, mime_type: str) -> str:
            assert path.is_file() and mime_type == "application/pdf"
            return "CLEAN"

    os.environ["KG_PRIVATE_FILE_STORAGE_ROOT"] = str(tmp_path)
    monkeypatch.setenv("KG_TEST_ENVIRONMENT", "ci_ephemeral")
    monkeypatch.setenv("KG_PRIVATE_FILE_ACCESS_SIGNING_KEY", "slice1-test-only-signing-key-32-bytes")
    get_settings.cache_clear(); _SLICE1_RUNTIMES.clear()
    use_worker = os.getenv("KG_RUN_SLICE1_CELERY_WORKER") == "1"
    worker = None
    if use_worker:
        monkeypatch.setenv(
            "KG_PRIVATE_FILE_SCANNER_FACTORY",
            "tests.test_一期切片1私有文件Celery合同:create_ci_scanner",
        )
        worker = subprocess.Popen(
            [
                sys.executable, "-m", "celery", "-A",
                "app.tasks.celery_app:celery_app", "worker", "--pool=solo",
                "--loglevel=WARNING", "-Q", "private-file,notification",
                "--hostname=slice1-ci@%h",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        def stop_worker():
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    worker.kill(); worker.wait(timeout=10)
        request.addfinalizer(stop_worker)
        for _ in range(30):
            replies = await asyncio.to_thread(slice1_tasks.celery_app.control.ping, timeout=1)
            if replies:
                break
            if worker.poll() is not None:
                raise AssertionError("SLICE1_CELERY_WORKER_EXITED")
            await asyncio.sleep(1)
        else:
            raise AssertionError("SLICE1_CELERY_WORKER_NOT_READY")
    _mark_stage("SEED")
    await _seed_closure_sources(application_database)
    platform_actor = SimpleNamespace(
        id=1,
        role="super_admin",
        tenant_id=None,
        org_id=None,
    )
    applicant_phone = _synthetic_mobile("138")
    contact_phone = _synthetic_mobile("139")
    writer = get_slice1_session_factory("onboarding_writer")
    _mark_stage("INVITATION")
    identity = get_session_factory()
    from app.modules.institution_onboarding.repository import InstitutionOnboardingRepository

    async with identity() as locked_region_session:
        assert await InstitutionOnboardingRepository(
            locked_region_session
        ).active_canonical_county(4) == (4, 3, 2, 1)
        blocked_archive = asyncio.create_task(
            pg_database._execute(
                "UPDATE public.platform_org SET status='archived' WHERE id=2"
            )
        )
        await asyncio.sleep(0.2)
        assert not blocked_archive.done()
        await locked_region_session.rollback()
        await asyncio.wait_for(blocked_archive, timeout=5)
    await pg_database._execute(
        "UPDATE public.platform_org SET status='active' WHERE id=2"
    )
    async with writer() as session, identity() as region_session:
        with pytest.raises(HTTPException) as invalid_region:
            await create_invitation(
                session,
                platform_actor,
                InvitationCreate(
                    institution_name="无效行政节点机构",
                    institution_type="HEALTH_STORE",
                    applicant_phone=applicant_phone,
                    pilot_batch_code="P1-S1",
                    administrative_region_id=1,
                ),
                "request-invalid-region",
                "idem-invalid-region-0001",
                region_session=region_session,
            )
        assert invalid_region.value.detail == "ONBOARDING_ADMINISTRATIVE_REGION_INVALID"
    await pg_database._execute(
        "UPDATE public.platform_org SET status='archived' WHERE id=2"
    )
    async with writer() as session, identity() as region_session:
        with pytest.raises(HTTPException) as invalid_ancestor:
            await create_invitation(
                session,
                platform_actor,
                InvitationCreate(
                    institution_name="无效祖先链机构",
                    institution_type="HEALTH_STORE",
                    applicant_phone=applicant_phone,
                    pilot_batch_code="P1-S1",
                    administrative_region_id=4,
                ),
                "request-invalid-ancestor",
                "idem-invalid-ancestor-0001",
                region_session=region_session,
            )
        assert invalid_ancestor.value.detail == "ONBOARDING_ADMINISTRATIVE_REGION_INVALID"
    await pg_database._execute(
        "UPDATE public.platform_org SET status='active' WHERE id=2"
    )
    async with writer() as session, identity() as region_session:
        issued = await create_invitation(
            session,
            platform_actor,
            InvitationCreate(institution_name="一期闭环健康门店", institution_type="HEALTH_STORE", applicant_phone=applicant_phone, pilot_batch_code="P1-S1", administrative_region_id=4),
            "request-invite",
            "idem-invite-0001",
            region_session=region_session,
        )
    async with writer() as session:
        issued = await resend_invitation(
            session,
            platform_actor,
            issued["invitation_id"],
            InvitationResendRequest(expected_version=1),
            "request-invite-resend",
            "idem-invite-resend-0001",
        )
    assert await pg_database._fetch_value(
        "SELECT bool_and(NOT (response_payload ? 'short_code') "
        "AND response_payload ? 'short_code_ciphertext') "
        "FROM public.institution_onboarding_idempotency "
        "WHERE operation IN ('INVITATION_CREATE','INVITATION_RESEND')"
    )
    async with writer() as session, identity() as region_session:
        revoked = await create_invitation(
            session,
            platform_actor,
            InvitationCreate(
                institution_name="一期撤销验证机构",
                institution_type="HEALTH_STORE",
                applicant_phone=applicant_phone,
                pilot_batch_code="P1-S1",
                administrative_region_id=4,
            ),
            "request-invite-revoke-create",
            "idem-invite-revoke-create-0001",
            region_session=region_session,
        )
    async with writer() as session:
        revoked = await revoke_invitation(
            session,
            platform_actor,
            revoked["invitation_id"],
            InvitationRevokeRequest(expected_version=1),
            "request-invite-revoke",
            "idem-invite-revoke-0001",
        )
        assert revoked["status"] == "REVOKED" and revoked["version"] == 2
    async with writer() as session, identity() as region_session:
        second_revoked = await create_invitation(
            session,
            platform_actor,
            InvitationCreate(
                institution_name="一期第二撤销验证机构",
                institution_type="HEALTH_STORE",
                applicant_phone=applicant_phone,
                pilot_batch_code="P1-S1",
                administrative_region_id=4,
            ),
            "request-invite-revoke-create-two",
            "idem-invite-revoke-create-0002",
            region_session=region_session,
        )
    async with writer() as session:
        second_revoked = await revoke_invitation(
            session,
            platform_actor,
            second_revoked["invitation_id"],
            InvitationRevokeRequest(expected_version=1),
            "request-invite-revoke-two",
            "idem-invite-revoke-0001",
        )
        assert second_revoked["status"] == "REVOKED"
        assert second_revoked["invitation_id"] != revoked["invitation_id"]
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    _mark_stage("ACTIVATION")
    activation_payload = ActivationRequest(invitation_id=issued["invitation_id"], phone=applicant_phone, short_code=issued["short_code"], password="StrongPassword123!", totp_secret=secret, totp_code=generate_totp(secret, at=utcnow()))
    wrong_code = "000000" if issued["short_code"] != "000000" else "000001"
    rejected_payload = activation_payload.model_copy(update={"short_code": wrong_code})
    async with writer() as session:
        with pytest.raises(HTTPException) as committed_failure:
            await activate(
                _CommitOutcomeSession(session, committed=True),
                rejected_payload,
                "request-activate-failed",
                "idem-activate-failed-0001",
            )
        assert committed_failure.value.detail == "ONBOARDING_INVITATION_CODE_MISMATCH"
    assert await pg_database._fetch_value(
        "SELECT failed_attempts FROM public.institution_invitation "
        f"WHERE invitation_id='{issued['invitation_id']}'"
    ) == 1
    async with writer() as session:
        with pytest.raises(HTTPException) as rolled_back_failure:
            await activate(
                _CommitOutcomeSession(session, committed=False),
                rejected_payload,
                "request-activate-rollback",
                "idem-activate-failed-0002",
            )
        assert rolled_back_failure.value.detail == "ONBOARDING_COMMIT_ROLLED_BACK"
    assert await pg_database._fetch_value(
        "SELECT failed_attempts FROM public.institution_invitation "
        f"WHERE invitation_id='{issued['invitation_id']}'"
    ) == 1
    async def activate_once():
        async with writer() as session:
            return await activate(session, activation_payload, "request-activate", "idem-activate-0001")
    activated, activation_replay = await asyncio.gather(activate_once(), activate_once())
    assert activated == activation_replay
    user_id = activated["user_id"]

    content = b"%PDF-1.7\nphase-one-slice-one\n%%EOF"
    digest = hashlib.sha256(content).hexdigest(); file_writer = get_slice1_session_factory("file_writer")
    _mark_stage("PRIVATE_FILE")
    async with file_writer() as session:
        upload = await initiate_upload(session, user_id, UploadInitiateRequest(purpose="BUSINESS_LICENSE", size=len(content), mime_type="application/pdf", sha256=digest))
    async with file_writer() as session: await upload_content(session, user_id, upload["file_id"], content)
    async with file_writer() as session: await complete_upload(session, user_id, upload["file_id"], UploadCompleteRequest(size=len(content), mime_type="application/pdf", sha256=digest))
    if use_worker:
        scan_result = slice1_tasks.scan_private_file_task.delay(upload["file_id"])
        try:
            scan_payload = await asyncio.to_thread(scan_result.get, timeout=30)
        except Exception as exc:
            status_code = getattr(exc, "status_code", 0)
            safe_detail = _safe_worker_error_code(exc)
            persisted_status = await pg_database._fetch_value(
                f"SELECT status FROM public.private_file WHERE file_id='{upload['file_id']}'"
            )
            if persisted_status not in {
                "UPLOAD_INITIATED", "PENDING_SCAN", "CLEAN", "REJECTED",
                "SCAN_FAILED", "DELETED",
            }:
                persisted_status = "UNKNOWN"
            safe_detail = f"{safe_detail}_{persisted_status}"
            _mark_safe_field("ERROR_CATEGORY", safe_detail)
            raise AssertionError(
                f"SLICE1_CELERY_SCAN_{type(exc).__name__}_{status_code}_{safe_detail}"
            ) from None
        safe_status = scan_payload.get("status") if type(scan_payload) is dict else None
        if safe_status not in {"CLEAN", "REJECTED", "SCAN_FAILED"}:
            safe_status = "UNKNOWN"
        if safe_status != "CLEAN":
            _mark_safe_field("ERROR_CATEGORY", f"SCAN_RESULT_{safe_status}")
            raise AssertionError(f"SLICE1_CELERY_SCAN_RESULT_{safe_status}")
        for _ in range(30):
            if await pg_database._fetch_value(
                f"SELECT status FROM public.private_file WHERE file_id='{upload['file_id']}'"
            ) == "CLEAN":
                break
            await asyncio.sleep(1)
        else:
            raise AssertionError("SLICE1_CELERY_SCAN_NOT_CLEAN")
    else:
        slice1_tasks.configure_private_file_scanner_for_test(CleanTestScanner())
        try:
            scanned = await slice1_tasks._run_scan(upload["file_id"])
            assert scanned["status"] == "CLEAN"
        finally:
            slice1_tasks.reset_private_file_scanner_for_test()
    expires_at = 2_000_000_000
    async with file_writer() as session:
        token = await authorize_file_access(session, user_id, upload["file_id"], "REVIEW", expires_at)
    reader = get_slice1_session_factory("reader")
    async with reader() as session:
        loaded, mime_type = await read_authorized_content(session, user_id, upload["file_id"], token)
        assert loaded == content and mime_type == "application/pdf"

    _mark_stage("DRAFT")
    async with writer() as session:
        try:
            draft = await save_draft(_CommitOutcomeSession(session, committed=True), user_id, ApplicationDraftRequest(credit_code="91310000TEST000001", legal_representative_name="一期负责人", registered_address="一期测试注册地址", service_address="一期测试服务地址", contact_name="一期联系人", contact_phone=contact_phone, contact_email="slice1@example.invalid", service_tags=("HYPERTENSION",), expected_version=1))
        except HTTPException as exc:
            safe_detail = exc.detail if exc.detail in {
                "ONBOARDING_COMMIT_ROLLED_BACK",
                "ONBOARDING_COMMIT_OUTCOME_UNKNOWN",
            } else "UNKNOWN"
            _mark_safe_field("ERROR_CATEGORY", safe_detail)
            raise AssertionError(f"DRAFT_COMMIT_{safe_detail}") from None
        assert draft["version"] == 2
    async with writer() as session:
        with pytest.raises(HTTPException) as rolled_back:
            await save_draft(_CommitOutcomeSession(session, committed=False), user_id, ApplicationDraftRequest(credit_code="91310000TEST000001", legal_representative_name="一期负责人", registered_address="一期测试注册地址", service_address="一期测试服务地址", contact_name="一期联系人", contact_phone=contact_phone, contact_email="slice1@example.invalid", service_tags=("HYPERTENSION",), expected_version=2))
        assert rolled_back.value.detail == "ONBOARDING_COMMIT_ROLLED_BACK"
    _mark_stage("SUBMIT")
    async with writer() as session:
        submitted = await submit_application(session, user_id, ApplicationSubmitRequest(expected_version=2, licenses=(LicenseBinding(license_type="BUSINESS_LICENSE", private_file_id=upload["file_id"]),)), "idem-submit-0001")
        assert submitted["status"] == "SUBMITTED"

    reviewer = get_slice1_session_factory("review_writer")
    _mark_stage("REVIEW")
    identity = get_session_factory()
    async with reviewer() as session, identity() as identity_session:
        correction = await review_decision(
            session,
            platform_actor,
            activated["application_id"],
            ReviewDecisionRequest(
                decision="NEEDS_CORRECTION",
                expected_version=3,
                correction_fields=("registered_address", "business_license"),
                reason_code="ADDRESS_UNCLEAR",
            ),
            "request-correction",
            "idem-review-correction-0001",
            currentness_session=identity_session,
        )
        assert correction["status"] == "NEEDS_CORRECTION"

    replacement_content = b"%PDF-1.7\nphase-one-replacement\n%%EOF"
    replacement_digest = hashlib.sha256(replacement_content).hexdigest()
    async with file_writer() as session:
        replacement = await initiate_upload(
            session,
            user_id,
            UploadInitiateRequest(
                purpose="BUSINESS_LICENSE",
                size=len(replacement_content),
                mime_type="application/pdf",
                sha256=replacement_digest,
            ),
        )
    async with file_writer() as session:
        await upload_content(
            session, user_id, replacement["file_id"], replacement_content
        )
    async with file_writer() as session:
        await complete_upload(
            session,
            user_id,
            replacement["file_id"],
            UploadCompleteRequest(
                size=len(replacement_content),
                mime_type="application/pdf",
                sha256=replacement_digest,
            ),
        )
    if use_worker:
        replacement_scan = slice1_tasks.scan_private_file_task.delay(
            replacement["file_id"]
        )
        assert (await asyncio.to_thread(replacement_scan.get, timeout=30))["status"] == "CLEAN"
    else:
        slice1_tasks.configure_private_file_scanner_for_test(CleanTestScanner())
        try:
            assert (await slice1_tasks._run_scan(replacement["file_id"]))["status"] == "CLEAN"
        finally:
            slice1_tasks.reset_private_file_scanner_for_test()
    async with writer() as session:
        resubmitted = await resubmit_application(
            session,
            user_id,
            ApplicationResubmitRequest(
                credit_code="91310000TEST000001",
                legal_representative_name="一期负责人",
                registered_address="一期补正注册地址",
                service_address="一期测试服务地址",
                contact_name="一期联系人",
                contact_phone=contact_phone,
                contact_email="slice1@example.invalid",
                service_tags=("HYPERTENSION",),
                expected_version=5,
                licenses=(LicenseBinding(license_type="BUSINESS_LICENSE", private_file_id=replacement["file_id"]),),
            ),
            "idem-resubmit-0001",
        )
        assert resubmitted["status"] == "SUBMITTED"
        assert resubmitted["version"] == 7
    assert await pg_database._fetch_value(
        "SELECT bound_application_id IS NULL FROM public.private_file "
        f"WHERE file_id='{upload['file_id']}'"
    )
    assert await pg_database._fetch_value(
        "SELECT bound_application_id IS NOT NULL FROM public.private_file "
        f"WHERE file_id='{replacement['file_id']}'"
    )
    review_payload = ReviewDecisionRequest(decision="APPROVED", expected_version=7)
    await pg_database._execute(
        "UPDATE public.platform_org SET status='archived' WHERE id=2"
    )
    async with reviewer() as session, identity() as identity_session:
        with pytest.raises(HTTPException) as stale_region:
            await review_decision(
                session,
                platform_actor,
                activated["application_id"],
                review_payload,
                "request-review-stale-region",
                "idem-review-stale-region-0001",
                currentness_session=identity_session,
            )
        assert stale_region.value.detail == "ONBOARDING_ADMINISTRATIVE_REGION_INVALID"
    assert await pg_database._fetch_value(
        "SELECT status FROM public.institution_application "
        f"WHERE application_id='{activated['application_id']}'"
    ) == "SUBMITTED"
    assert await pg_database._fetch_value("SELECT count(*) FROM public.tenant") == 0
    await pg_database._execute(
        "UPDATE public.platform_org SET status='active' WHERE id=2"
    )
    async def approve_once():
        async with reviewer() as session, identity() as identity_session:
            return await review_decision(session, platform_actor, activated["application_id"], review_payload, "request-review", "idem-review-0001", currentness_session=identity_session)
    approved, review_replay = await asyncio.gather(approve_once(), approve_once())
    assert approved == review_replay
    assert approved["tenant_active"] is True and approved["service_ready"] is False
    _mark_stage("READER")
    async with reader() as session:
        final = await get_application(session, user_id)
        assert final["status"] == "APPROVED" and final["tenant_id"]

    assert await pg_database._fetch_value("SELECT count(*) FROM public.institution_application_revision") == 2
    assert await pg_database._fetch_value("SELECT count(*) FROM public.institution_onboarding_audit") >= 2
    assert await pg_database._fetch_value("SELECT count(*) FROM public.institution_onboarding_outbox WHERE event_type='INSTITUTION_APPROVED'") == 1
    if use_worker:
        slice1_tasks.dispatch_institution_outbox_task.delay()
        for _ in range(30):
            if await pg_database._fetch_value(
                "SELECT status FROM public.institution_onboarding_outbox LIMIT 1"
            ) == "DELIVERED":
                break
            await asyncio.sleep(1)
        else:
            raise AssertionError("SLICE1_OUTBOX_NOT_DELIVERED")
    else:
        delivered = []
        monkeypatch.setattr(slice1_tasks.celery_app, "send_task", lambda *args, **kwargs: delivered.append((args, kwargs)))
        dispatched = await slice1_tasks._dispatch_one_outbox()
        assert dispatched["status"] == "PROCESSING"
        assert len(delivered) == 1
        event_payload = delivered[0][1]["kwargs"]
        assert (await slice1_tasks._consume_institution_approved_event(
            event_payload["event_id"], event_payload["payload"]
        ))["status"] == "DELIVERED"
        recovery_event_id = str(uuid.uuid4())
        recovery_payload = (
            '{"application_id":"' + activated["application_id"]
            + '","tenant_id":"' + final["tenant_id"] + '"}'
        )
        await pg_database._execute(
            "INSERT INTO public.institution_onboarding_outbox"
            "(event_id,event_type,aggregate_id,payload,status,attempts,created_at,processing_at) "
            f"VALUES ('{recovery_event_id}','INSTITUTION_APPROVED',"
            f"'{activated['application_id']}','{recovery_payload}'::jsonb,"
            "'PROCESSING',1,now()-interval '10 minutes',now()-interval '10 minutes')"
        )
        recovered = await slice1_tasks._dispatch_one_outbox()
        assert recovered == {"event_id": recovery_event_id, "status": "PROCESSING"}
        assert await pg_database._fetch_value(
            "SELECT attempts FROM public.institution_onboarding_outbox "
            f"WHERE event_id='{recovery_event_id}'"
        ) == 2
        await pg_database._execute(
            "INSERT INTO public.institution_onboarding_delivery"
            "(event_id,event_type,recipient_user_id,recipient_scope,payload,payload_digest,created_at) "
            f"VALUES ('{recovery_event_id}','INSTITUTION_APPROVED',{user_id},"
            "'INSTITUTION_ADMIN','{}'::jsonb,'"
            + ("0" * 64)
            + "',now())"
        )
        with pytest.raises(slice1_tasks.InstitutionOutboxDispatchUnavailable):
            await slice1_tasks._consume_institution_approved_event(
                recovery_event_id,
                {
                    "application_id": activated["application_id"],
                    "tenant_id": final["tenant_id"],
                },
            )
        assert await pg_database._fetch_value(
            "SELECT status FROM public.institution_onboarding_outbox "
            f"WHERE event_id='{recovery_event_id}'"
        ) == "PROCESSING"
        assert await pg_database._fetch_value(
            "SELECT payload_digest FROM public.institution_onboarding_delivery "
            f"WHERE event_id='{recovery_event_id}'"
        ) == "0" * 64
        await pg_database._execute(
            "DELETE FROM public.institution_onboarding_delivery "
            f"WHERE event_id='{recovery_event_id}'"
        )
        await pg_database._execute(
            "UPDATE public.institution_onboarding_outbox SET attempts=3, "
            "processing_at=now()-interval '10 minutes' "
            f"WHERE event_id='{recovery_event_id}'"
        )
        exhausted = await slice1_tasks._dispatch_one_outbox()
        assert exhausted == {"event_id": recovery_event_id, "status": "FAILED"}
        assert (await slice1_tasks._consume_institution_approved_event(
            event_payload["event_id"], event_payload["payload"]
        ))["status"] == "DELIVERED"
    assert await pg_database._fetch_value("SELECT count(*) FROM public.institution_onboarding_outbox WHERE status='DELIVERED'") == 1
    assert await pg_database._fetch_value("SELECT count(*) FROM public.institution_onboarding_delivery WHERE event_type='INSTITUTION_APPROVED'") == 1
    with pytest.raises(Exception):
        await pg_database._execute(
            f"UPDATE public.institution_application SET service_ready=true WHERE application_id='{activated['application_id']}'"
        )
