from __future__ import annotations

import asyncio
import json
import os
import queue
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import _build_alembic_config, _get_test_database_url
from tests.integration.test_一期切片3会员CurrentnessAuthority真实HTTP合同 import (
    _create_accepted_self_enrollment,
    _login,
    _synthetic_prc_identity,
)


pytestmark = pytest.mark.integration


def _hold_identity_review_boundary(
    *,
    verification_id: UUID,
    locked: Event,
    release: Event,
    backend_pid: queue.Queue,
    commit: bool,
) -> None:
    async def run() -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.modules.member_enrollment.repository import MemberEnrollmentRepository

        engine = create_async_engine(
            os.environ["KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"],
            poolclass=NullPool,
        )
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                repository = MemberEnrollmentRepository(session)
                await repository.lock_identity_review_boundary(verification_id)
                backend_pid.put(
                    (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                )
                locked.set()
                if not await asyncio.to_thread(release.wait, 60):
                    raise AssertionError(
                        "identity review boundary release barrier timed out"
                    )
                if commit:
                    await session.commit()
                else:
                    await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(run())


def _acquire_identity_review_boundary(
    *,
    verification_id: UUID,
    started: Event,
    acquired: Event,
    backend_pid: queue.Queue,
) -> None:
    async def run() -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.modules.member_enrollment.repository import MemberEnrollmentRepository

        engine = create_async_engine(
            os.environ["KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"],
            poolclass=NullPool,
        )
        try:
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions() as session:
                backend_pid.put(
                    (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                )
                started.set()
                repository = MemberEnrollmentRepository(session)
                await repository.lock_identity_review_boundary(verification_id)
                acquired.set()
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


def _blocked_session_count(pg_database, blocker_pid: int) -> int:
    return pg_database.fetch_value(
        "WITH RECURSIVE blocked(pid) AS ("
        "SELECT pid FROM pg_stat_activity "
        f"WHERE pid<>{blocker_pid} "
        f"AND {blocker_pid}=ANY(pg_blocking_pids(pid)) "
        "UNION "
        "SELECT candidate.pid FROM pg_stat_activity candidate "
        "JOIN blocked predecessor "
        "ON predecessor.pid=ANY(pg_blocking_pids(candidate.pid))"
        ") SELECT count(*) FROM blocked"
    )


def _wait_for_blocked_sessions(
    pg_database, *, blocker_pid: int, expected_count: int
) -> None:
    deadline = monotonic() + 30
    poll = Event()
    while monotonic() < deadline:
        count = _blocked_session_count(pg_database, blocker_pid)
        if count >= expected_count:
            return
        poll.wait(0.025)
    raise AssertionError(
        f"expected {expected_count} sessions at the reviewer lock barrier"
    )


def _direct_platform_decision(
    *,
    verification_id: UUID,
    revision_id: UUID,
    source_member_id: UUID,
    tenant_id: int,
    tenant_public_id: UUID,
    expected_version: int,
    access_token_digest: str,
    currentness_digest: str,
) -> dict[str, object]:
    async def run() -> dict[str, object]:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.security import CurrentUser
        from app.core.uuid_generator import Uuid7Generator
        from app.modules.member_enrollment.api import (
            _begin_mutation,
            _finish_mutation,
            _identity,
            _request_value,
        )
        from app.modules.member_enrollment.repository import MemberEnrollmentRepository
        from app.modules.member_enrollment.schemas import (
            IdentityStatusDTO,
            PlatformIdentityDecisionRequest,
        )
        from app.modules.member_enrollment.service import (
            MemberEnrollmentService,
            MutationContext,
        )

        engine = create_async_engine(
            os.environ["KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"],
            poolclass=NullPool,
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as session:
                context = MutationContext(
                    actor=CurrentUser(id=98604, role="super_admin"),
                    tenant_id=tenant_id,
                    tenant_public_id=tenant_public_id,
                    idempotency_key="pii-real-http-approved",
                    request_id=Uuid7Generator().generate(),
                    platform_scope=True,
                )
                request = PlatformIdentityDecisionRequest(
                    revision_id=revision_id,
                    decision="APPROVED",
                    expected_version=expected_version,
                )
                request_value = _request_value(request, verification_id)
                target, secrets, replay = await _begin_mutation(
                    session,
                    context,
                    "IDENTITY_REVIEW_DECIDE",
                    request_value,
                    target_id=verification_id,
                )
                if replay is not None:
                    return {"status_code": 200, "body": replay}
                repository = MemberEnrollmentRepository(session)
                await MemberEnrollmentService(
                    repository, secrets_port=secrets
                ).platform_identity_decide(
                    context,
                    verification_id,
                    request,
                    source_member_id=source_member_id,
                    enrollment_mode="SELF",
                    access_token_digest=access_token_digest,
                    currentness_digest=currentness_digest,
                )
                row = await repository.verification_for_update(verification_id)
                revision = await repository.current_identity_revision(
                    verification_id, row["current_revision_id"]
                )
                result = _identity(row, revision)
                body = await _finish_mutation(
                    session,
                    kind="identity_review_writer",
                    context=context,
                    operation="IDENTITY_REVIEW_DECIDE",
                    target_id=target,
                    request_value=request_value,
                    result=result,
                    secrets=secrets,
                )
                return {
                    "status_code": 200,
                    "body": IdentityStatusDTO.model_validate(body).model_dump(
                        mode="json"
                    ),
                }
        except BaseException as exc:
            sqlstate = None
            current: BaseException | None = exc
            visited: set[int] = set()
            while current is not None and id(current) not in visited:
                visited.add(id(current))
                sqlstate = getattr(current, "sqlstate", None) or getattr(
                    current, "pgcode", None
                )
                if sqlstate is not None:
                    break
                current = (
                    getattr(current, "orig", None)
                    or current.__cause__
                    or current.__context__
                )
            return {
                "status_code": 503,
                "error": type(exc).__name__,
                "sqlstate": sqlstate,
            }
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _seed_real_journey_actors(pg_database) -> dict[str, object]:
    from app.core.uuid_generator import Uuid7Generator
    from app.modules.auth.service import hash_password

    tenant_id = 98601
    admin_user_id = 98602
    member_user_id = 98603
    reviewer_user_id = 98604
    org_id = 98605
    other_reviewer_user_id = 98606
    member_id = Uuid7Generator().generate()
    tenant_public_id = Uuid7Generator().generate()
    admin_phone = "18944444444"
    member_phone = "13655555555"
    reviewer_phone = "18866667777"
    other_reviewer_phone = "18777777777"
    admin_password = secrets.token_urlsafe(24)
    member_password = secrets.token_urlsafe(24)
    reviewer_password = secrets.token_urlsafe(24)
    other_reviewer_password = secrets.token_urlsafe(24)
    values = {
        "admin_password_hash": hash_password(admin_password).replace("'", "''"),
        "member_password_hash": hash_password(member_password).replace("'", "''"),
        "reviewer_password_hash": hash_password(reviewer_password).replace("'", "''"),
        "other_reviewer_password_hash": hash_password(other_reviewer_password).replace("'", "''"),
    }
    pg_database.execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        f"VALUES ({org_id},NULL,'PII journey county','PII-JOURNEY-COUNTY','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) "
        f"VALUES ({tenant_id},{org_id},'PII-JOURNEY-TENANT','PII journey institution','store','test','test','active',now(),now());"
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,tenant_id) VALUES "
        f"({admin_user_id},'{admin_phone}','{values['admin_password_hash']}','org_admin','active',{tenant_id}),"
        f"({member_user_id},'{member_phone}','{values['member_password_hash']}','member','active',NULL),"
        f"({reviewer_user_id},'{reviewer_phone}','{values['reviewer_password_hash']}','super_admin','active',NULL),"
        f"({other_reviewer_user_id},'{other_reviewer_phone}','{values['other_reviewer_password_hash']}','super_admin','active',NULL);"
        "INSERT INTO identity.member(member_id,member_no,creation_source,status,version,created_at,updated_at) "
        f"VALUES ('{member_id}','M9123456789ABCDEFGHJK','registration','created',1,now(),now());"
        "INSERT INTO identity.user_member_self_link("
        "link_id,user_ref,member_id,source,eligibility_decision_ref,establishment_basis,"
        "establishment_record_ref,created_at) VALUES ("
        f"'{uuid4()}',{member_user_id},'{member_id}','REGISTRATION_VERIFIED','{uuid4()}',"
        f"'REGISTRATION_VERIFIED_BOOTSTRAP','{uuid4()}',now());"
        "INSERT INTO public.institution_invitation("
        "invitation_id,institution_name,institution_type,applicant_phone_ciphertext,applicant_phone_digest,"
        "pilot_batch_code,administrative_region_id,code_digest,status,failed_attempts,expires_at,issued_by,issued_at,activated_at,version) VALUES ("
        f"'{uuid4()}','PII journey institution','HEALTH_STORE',decode('00','hex'),repeat('a',64),"
        f"'PII-JOURNEY',{org_id},repeat('b',64),'ACTIVATED',0,now()+interval '1 day',{admin_user_id},now(),now(),1);"
        "INSERT INTO public.institution_application("
        "application_id,invitation_id,applicant_user_id,institution_type,status,draft_payload,correction_fields,"
        "current_revision_no,tenant_internal_id,tenant_public_id,service_ready,created_at,updated_at,submitted_at,reviewed_at,version) "
        "SELECT "
        f"'{uuid4()}',invitation_id,{admin_user_id},'HEALTH_STORE','APPROVED',"
        f"'{{\"service_tags\":[\"GLUCOSE_METABOLISM\"]}}'::jsonb,'[]'::jsonb,1,{tenant_id},"
        f"'{tenant_public_id}',false,now(),now(),now(),now(),3 "
        "FROM public.institution_invitation WHERE issued_by=" + str(admin_user_id) + ";"
        "INSERT INTO public.institution_service_readiness("
        "tenant_id,readiness_status,reason_codes,qualified_therapist_count,computed_at,evidence_version,"
        "input_digest,result_digest,source_versions,next_expiry_at,version) VALUES ("
        f"{tenant_id},'SERVICE_READY',ARRAY[]::text[],1,now(),1,repeat('c',64),repeat('d',64),"
        "'{}'::jsonb,current_date+30,1)"
    )
    return {
        "admin_phone": admin_phone,
        "admin_password": admin_password,
        "member_phone": member_phone,
        "member_password": member_password,
        "reviewer_phone": reviewer_phone,
        "reviewer_password": reviewer_password,
        "other_reviewer_phone": other_reviewer_phone,
        "other_reviewer_password": other_reviewer_password,
        "member_id": member_id,
        "tenant_id": tenant_id,
        "tenant_public_id": tenant_public_id,
    }


def test_平台审核员一次性PII访问使用真实ASGI和正式Runtime(
    pg_database,
    real_db_client,
    application_database,
    member_enrollment_writer_database,
    member_identity_review_writer_database,
) -> None:
    seeded = _seed_real_journey_actors(pg_database)
    admin_headers = _login(
        real_db_client,
        str(seeded["admin_phone"]),
        str(seeded["admin_password"]),
    )
    member_headers = _login(
        real_db_client,
        str(seeded["member_phone"]),
        str(seeded["member_password"]),
    )
    reviewer_headers = _login(
        real_db_client,
        str(seeded["reviewer_phone"]),
        str(seeded["reviewer_password"]),
    )
    other_reviewer_headers = _login(
        real_db_client,
        str(seeded["other_reviewer_phone"]),
        str(seeded["other_reviewer_password"]),
    )
    enrollment_id = _create_accepted_self_enrollment(
        real_db_client,
        admin_authorization=admin_headers,
        member_authorization=member_headers,
        member_phone=seeded["member_phone"],
        key_prefix="pii-real-http",
    )
    submitted = real_db_client.put(
        f"/api/v1/family/member-enrollments/{enrollment_id}/identity-submission",
        headers={**member_headers, "Idempotency-Key": "pii-real-http-submit"},
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Member",
            "id_number": _synthetic_prc_identity(),
            "expected_version": 1,
        },
    )
    assert submitted.status_code == 200
    verification_id = UUID(submitted.json()["verification_id"])
    first_revision_id = UUID(submitted.json()["current_revision_id"])
    correction = real_db_client.post(
        f"/api/v1/institution/member-enrollments/{enrollment_id}/identity-check",
        headers={**admin_headers, "Idempotency-Key": "pii-real-http-correction"},
        json={
            "revision_id": str(first_revision_id),
            "decision": "NEEDS_CORRECTION",
            "reason_code": "IDENTITY_INFORMATION_INCONSISTENT",
            "correction_fields": ["real_name"],
            "expected_version": 1,
        },
    )
    assert correction.status_code == 200
    resubmitted = real_db_client.post(
        f"/api/v1/family/member-enrollments/{enrollment_id}/identity-resubmit",
        headers={**member_headers, "Idempotency-Key": "pii-real-http-resubmit"},
        json={
            "document_type": "PRC_RESIDENT_ID",
            "real_name": "Synthetic Member Corrected",
            "expected_version": 3,
        },
    )
    assert resubmitted.status_code == 200
    current_revision_id = UUID(resubmitted.json()["current_revision_id"])
    checked = real_db_client.post(
        f"/api/v1/institution/member-enrollments/{enrollment_id}/identity-check",
        headers={**admin_headers, "Idempotency-Key": "pii-real-http-checked"},
        json={
            "revision_id": str(current_revision_id),
            "decision": "CHECKED",
            "attestation_code": "OFFLINE_IDENTITY_CHECKED",
            "expected_version": 3,
        },
    )
    assert checked.status_code == 200
    detail = real_db_client.get(
        f"/api/v1/platform/member-identity-reviews/{verification_id}",
        headers=reviewer_headers,
    )
    assert detail.status_code == 200
    unclaimed = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-unclaimed"},
        json={
            "current_password": seeded["reviewer_password"],
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )
    assert unclaimed.status_code == 403
    claimed = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/claim",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-claim"},
        json={"expected_version": detail.json()["version"]},
    )
    assert claimed.status_code == 200
    wrong_reviewer = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
        headers={**other_reviewer_headers, "Idempotency-Key": "pii-real-http-other-reviewer"},
        json={
            "current_password": seeded["other_reviewer_password"],
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )
    assert wrong_reviewer.status_code == 403

    claim_facts = pg_database.fetch_rows(
        "SELECT v.status,"
        "count(a.audit_id) FILTER (WHERE a.action='IDENTITY_REVIEW_CLAIMED') AS claim_count,"
        "count(a.audit_id) FILTER (WHERE a.action='IDENTITY_REVIEW_CLAIMED' "
        "AND a.actor_scope='user:98604:platform') AS matching_count "
        "FROM public.member_identity_verification v "
        "LEFT JOIN public.member_enrollment_audit a ON a.object_id=v.verification_id "
        "WHERE v.verification_id=$1 GROUP BY v.status",
        verification_id,
    )[0]
    authority_result = member_identity_review_writer_database.fetch_rows(
        "SELECT public.slice3_reviewer_claim_authority_v1($1,$2)",
        verification_id,
        98604,
    )[0]["slice3_reviewer_claim_authority_v1"]
    assert claim_facts == {
        "status": "PLATFORM_REVIEWING",
        "claim_count": 1,
        "matching_count": 1,
    }
    assert authority_result is True
    assert member_identity_review_writer_database.fetch_rows(
        "SELECT enrollment_id,status,version FROM public.service_enrollment "
        "WHERE enrollment_id=$1",
        enrollment_id,
    ) == [
        {
            "enrollment_id": enrollment_id,
            "status": "INSTITUTION_CHECKED",
            "version": 5,
        }
    ]
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        member_identity_review_writer_database.fetch_rows(
            "SELECT * FROM public.service_enrollment WHERE enrollment_id=$1",
            enrollment_id,
        )
    enrollment_preimage = member_identity_review_writer_database.fetch_rows(
        "SELECT public.slice3_review_enrollment_preimage_authority_v1($1,$2,$3) "
        "AS preimage",
        verification_id,
        enrollment_id,
        98604,
    )[0]["preimage"]
    if isinstance(enrollment_preimage, str):
        enrollment_preimage = json.loads(enrollment_preimage)
    assert set(enrollment_preimage) == {
        "enrollment_id",
        "invitation_id",
        "tenant_id",
        "subject_member_id",
        "proxy_member_id",
        "mode",
        "status",
        "service_scope_tags",
        "current_identity_verification_id",
        "current_assignment_id",
        "service_case_id",
        "accepted_at",
        "identity_verified_at",
        "case_created_at",
        "created_at",
        "updated_at",
        "version",
    }
    assert member_identity_review_writer_database.fetch_rows(
        "SELECT public.slice3_review_enrollment_preimage_authority_v1($1,$2,$3) "
        "AS preimage",
        verification_id,
        enrollment_id,
        98606,
    )[0]["preimage"] is None
    assert member_identity_review_writer_database.fetch_rows(
        "SELECT public.slice3_review_enrollment_preimage_authority_v1($1,$2,$3) "
        "AS preimage",
        verification_id,
        uuid4(),
        98604,
    )[0]["preimage"] is None
    signature = (
        "public.slice3_review_enrollment_preimage_authority_v1(uuid,uuid,bigint)"
    )
    assert pg_database.fetch_rows(
        "SELECT has_function_privilege($1,$2,'EXECUTE') AS allowed",
        os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"],
        signature,
    )[0]["allowed"] is True
    for role_name in (
        "KG_TEST_APPLICATION_ROLE",
        "KG_TEST_MEMBER_ENROLLMENT_WRITER_ROLE",
        "KG_TEST_MEMBER_CASE_WRITER_ROLE",
        "KG_TEST_MEMBER_WORKFLOW_WORKER_ROLE",
        "KG_TEST_MEMBER_ENROLLMENT_READER_ROLE",
    ):
        assert pg_database.fetch_rows(
            "SELECT has_function_privilege($1,$2,'EXECUTE') AS allowed",
            os.environ[role_name],
            signature,
        )[0]["allowed"] is False
    assert pg_database.fetch_rows(
        "SELECT has_function_privilege('public',$1,'EXECUTE') AS allowed",
        signature,
    )[0]["allowed"] is False
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        application_database.fetch_rows(
            "SELECT public.slice3_review_enrollment_preimage_authority_v1($1,$2,$3)",
            verification_id,
            enrollment_id,
            98604,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        member_enrollment_writer_database.fetch_rows(
            "SELECT public.slice3_review_enrollment_preimage_authority_v1($1,$2,$3)",
            verification_id,
            enrollment_id,
            98604,
        )

    pii_count_before_wrong_password = pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_identity_pii_access"
    )
    wrong_password = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-wrong-password"},
        json={
            "current_password": "synthetic-wrong-password",
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )
    assert wrong_password.status_code == 403
    assert pg_database.fetch_value(
        "SELECT COUNT(*) FROM public.member_identity_pii_access"
    ) == pii_count_before_wrong_password

    before = (
        pg_database.fetch_value("SELECT COUNT(*) FROM public.member_identity_pii_access"),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            "WHERE action='IDENTITY_PII_ACCESSED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='IDENTITY_PII_ACCESS'"
        ),
    )
    accessed = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-access"},
        json={
            "current_password": seeded["reviewer_password"],
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )

    assert accessed.status_code == 200
    assert accessed.headers["Cache-Control"] == "no-store"
    assert set(accessed.json()) == {
        "review_id",
        "revision_id",
        "real_name",
        "id_number",
        "birth_date",
        "access_id",
    }
    assert accessed.json()["review_id"] == str(verification_id)
    assert accessed.json()["revision_id"] == str(current_revision_id)
    after = (
        pg_database.fetch_value("SELECT COUNT(*) FROM public.member_identity_pii_access"),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            "WHERE action='IDENTITY_PII_ACCESSED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='IDENTITY_PII_ACCESS'"
        ),
    )
    assert after == tuple(value + 1 for value in before)

    replayed = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-access"},
        json={
            "current_password": seeded["reviewer_password"],
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )
    assert replayed.status_code == 200
    assert replayed.headers["Cache-Control"] == "no-store"
    assert replayed.json() == accessed.json()
    assert (
        pg_database.fetch_value("SELECT COUNT(*) FROM public.member_identity_pii_access"),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_audit "
            "WHERE action='IDENTITY_PII_ACCESSED'"
        ),
        pg_database.fetch_value(
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency "
            "WHERE operation='IDENTITY_PII_ACCESS'"
        ),
    ) == after

    access_binding = pg_database.fetch_rows(
        "UPDATE public.member_identity_pii_access "
        "SET expires_at=transaction_timestamp()+interval '5 minutes' "
        "WHERE access_id=$1 "
        "RETURNING access_token_digest,currentness_digest",
        UUID(accessed.json()["access_id"]),
    )[0]
    decision_side_effects_before = tuple(
        pg_database.fetch_value(statement)
        for statement in (
            "SELECT COUNT(*) FROM public.member_identity_review_decision",
            "SELECT COUNT(*) FROM public.member_enrollment_audit",
            "SELECT COUNT(*) FROM public.member_enrollment_outbox",
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency",
            "SELECT COUNT(*) FROM public.member_identity_pii_access",
            "SELECT COUNT(*) FROM public.member_identity_pii_access "
            "WHERE status='CONSUMED'",
        )
    )
    decision_payload = {
        "revision_id": str(current_revision_id),
        "decision": "APPROVED",
        "expected_version": claimed.json()["version"],
    }
    boundary_locked = Event()
    release_boundary = Event()
    boundary_blocker_pid: queue.Queue[int] = queue.Queue(maxsize=1)
    with ThreadPoolExecutor(max_workers=3) as executor:
        boundary_blocker = executor.submit(
            _hold_identity_review_boundary,
            verification_id=verification_id,
            locked=boundary_locked,
            release=release_boundary,
            backend_pid=boundary_blocker_pid,
            commit=True,
        )
        assert boundary_locked.wait(10)
        boundary_locked_by = boundary_blocker_pid.get(timeout=1)
        concurrent_pii = executor.submit(
            real_db_client.post,
            f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
            headers={
                **reviewer_headers,
                "Idempotency-Key": "pii-real-http-concurrent-access",
            },
            json={
                "current_password": "synthetic-wrong-password",
                "reason_code": "PLATFORM_IDENTITY_REVIEW",
            },
        )
        concurrent_decision = executor.submit(
            _direct_platform_decision,
            verification_id=verification_id,
            revision_id=current_revision_id,
            source_member_id=seeded["member_id"],
            tenant_id=seeded["tenant_id"],
            tenant_public_id=seeded["tenant_public_id"],
            expected_version=claimed.json()["version"],
            access_token_digest=access_binding["access_token_digest"],
            currentness_digest=access_binding["currentness_digest"],
        )
        _wait_for_blocked_sessions(
            pg_database,
            blocker_pid=boundary_locked_by,
            expected_count=2,
        )
        release_boundary.set()
        pii_during_decision = concurrent_pii.result(timeout=10)
        boundary_blocker.result(timeout=10)
        approved_result = concurrent_decision.result(timeout=10)

    decision_side_effects_after = tuple(
        pg_database.fetch_value(statement)
        for statement in (
            "SELECT COUNT(*) FROM public.member_identity_review_decision",
            "SELECT COUNT(*) FROM public.member_enrollment_audit",
            "SELECT COUNT(*) FROM public.member_enrollment_outbox",
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency",
            "SELECT COUNT(*) FROM public.member_identity_pii_access",
            "SELECT COUNT(*) FROM public.member_identity_pii_access "
            "WHERE status='CONSUMED'",
        )
    )
    assert pii_during_decision.status_code == 403
    assert pii_during_decision.json()["code"] == "STEP_UP_FORBIDDEN"
    assert approved_result["status_code"] == 200, {
        "error": approved_result.get("error"),
        "sqlstate": approved_result.get("sqlstate"),
    }
    approved_body = approved_result["body"]
    assert decision_side_effects_after == (
        decision_side_effects_before[0] + 1,
        decision_side_effects_before[1] + 1,
        decision_side_effects_before[2] + 1,
        decision_side_effects_before[3] + 1,
        decision_side_effects_before[4],
        decision_side_effects_before[5],
    )
    approved_replay = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/decision",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-approved"},
        json=decision_payload,
    )
    assert approved_replay.status_code == 200
    assert approved_replay.json() == approved_body
    assert tuple(
        pg_database.fetch_value(statement)
        for statement in (
            "SELECT COUNT(*) FROM public.member_identity_review_decision",
            "SELECT COUNT(*) FROM public.member_enrollment_audit",
            "SELECT COUNT(*) FROM public.member_enrollment_outbox",
            "SELECT COUNT(*) FROM public.member_enrollment_idempotency",
            "SELECT COUNT(*) FROM public.member_identity_pii_access",
            "SELECT COUNT(*) FROM public.member_identity_pii_access "
            "WHERE status='CONSUMED'",
        )
    ) == decision_side_effects_after
    institution_detail = real_db_client.get(
        f"/api/v1/institution/member-enrollments/{enrollment_id}",
        headers=admin_headers,
    )
    assert institution_detail.status_code == 200
    assert institution_detail.json()["status"] == "IDENTITY_VERIFIED"

    invalidated_task = real_db_client.post(
        f"/api/v1/platform/member-identity-reviews/{verification_id}/pii-access",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-invalidated"},
        json={
            "current_password": seeded["reviewer_password"],
            "reason_code": "PLATFORM_IDENTITY_REVIEW",
        },
    )
    assert invalidated_task.status_code == 403

    created_document = real_db_client.post(
        "/api/v1/platform/consent-documents",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-document-create"},
        json={
            "document_type": "USER_AGREEMENT",
            "semantic_version": "pii-hotfix-1",
            "requires_reconsent": True,
            "renditions": [
                {
                    "locale": "zh-CN",
                    "title": "Synthetic agreement",
                    "body": "Synthetic agreement body for disposable verification.",
                }
            ],
        },
    )
    assert created_document.status_code == 201
    document_id = UUID(created_document.json()["document_version_id"])
    published_document = real_db_client.post(
        f"/api/v1/platform/consent-documents/{document_id}/publish",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-document-publish"},
        json={
            "effective_at": (
                datetime.now(timezone.utc) - timedelta(seconds=1)
            ).isoformat(),
            "expected_version": created_document.json()["version"],
        },
    )
    assert published_document.status_code == 200
    retired_document = real_db_client.post(
        f"/api/v1/platform/consent-documents/{document_id}/retire",
        headers={**reviewer_headers, "Idempotency-Key": "pii-real-http-document-retire"},
        json={
            "reason_code": "LEGAL_WITHDRAWAL",
            "expected_version": published_document.json()["version"],
        },
    )
    assert retired_document.status_code == 200
    assert retired_document.json()["status"] == "RETIRED"


def test_IdentityReview边界按Verification串行且事务结束自动释放(pg_database) -> None:
    verification_id = uuid4()
    different_verification_id = uuid4()

    def run_release_case(*, commit: bool) -> None:
        holder_locked = Event()
        release_holder = Event()
        holder_pid: queue.Queue[int] = queue.Queue(maxsize=1)
        same_started = Event()
        same_acquired = Event()
        same_pid: queue.Queue[int] = queue.Queue(maxsize=1)
        with ThreadPoolExecutor(max_workers=3) as executor:
            holder = executor.submit(
                _hold_identity_review_boundary,
                verification_id=verification_id,
                locked=holder_locked,
                release=release_holder,
                backend_pid=holder_pid,
                commit=commit,
            )
            assert holder_locked.wait(10)
            locked_by = holder_pid.get(timeout=1)
            if commit:
                different_started = Event()
                different_acquired = Event()
                different_pid: queue.Queue[int] = queue.Queue(maxsize=1)
                different = executor.submit(
                    _acquire_identity_review_boundary,
                    verification_id=different_verification_id,
                    started=different_started,
                    acquired=different_acquired,
                    backend_pid=different_pid,
                )
                assert different_started.wait(10)
                different.result(timeout=10)
                assert different_acquired.is_set()
                different_pid.get(timeout=1)
            contender = executor.submit(
                _acquire_identity_review_boundary,
                verification_id=verification_id,
                started=same_started,
                acquired=same_acquired,
                backend_pid=same_pid,
            )
            assert same_started.wait(10)
            same_pid.get(timeout=1)
            _wait_for_blocked_sessions(
                pg_database, blocker_pid=locked_by, expected_count=1
            )
            assert not same_acquired.is_set()
            release_holder.set()
            holder.result(timeout=10)
            contender.result(timeout=10)
            assert same_acquired.is_set()

    run_release_case(commit=True)
    run_release_case(commit=False)


def test_0027受限Preimage权威升级降级权限完全对称(pg_database) -> None:
    review_role = os.environ["KG_TEST_MEMBER_IDENTITY_REVIEW_WRITER_ROLE"]
    signature = (
        "public.slice3_review_enrollment_preimage_authority_v1(uuid,uuid,bigint)"
    )
    config = _build_alembic_config(_get_test_database_url())

    def assert_base_acl() -> None:
        assert pg_database.fetch_value(
            f"SELECT NOT has_table_privilege('{review_role}',"
            "'public.service_enrollment','SELECT')"
        )
        assert pg_database.fetch_value(
            f"SELECT has_column_privilege('{review_role}',"
            "'public.service_enrollment','enrollment_id','SELECT') AND "
            f"has_column_privilege('{review_role}',"
            "'public.service_enrollment','status','SELECT') AND "
            f"has_column_privilege('{review_role}',"
            "'public.service_enrollment','version','SELECT') AND "
            f"has_column_privilege('{review_role}',"
            "'public.service_enrollment','status','UPDATE') AND "
            f"NOT has_column_privilege('{review_role}',"
            "'public.service_enrollment','tenant_id','SELECT')"
        )

    assert pg_database.fetch_value(
        "SELECT version_num='20260825_0030' FROM alembic_version"
    )
    assert pg_database.fetch_rows(
        "SELECT has_function_privilege($1,$2,'EXECUTE') AS allowed",
        review_role,
        signature,
    )[0]["allowed"] is True
    assert_base_acl()
    command.downgrade(config, "20260822_0026")
    assert pg_database.fetch_value(
        "SELECT version_num='20260822_0026' FROM alembic_version"
    )
    assert pg_database.fetch_value(
        "SELECT to_regprocedure("
        "'public.slice3_review_enrollment_preimage_authority_v1(uuid,uuid,bigint)') "
        "IS NULL"
    )
    assert pg_database.fetch_value(
        "SELECT to_regprocedure("
        "'public.slice3_reviewer_claim_authority_v1(uuid,bigint)') IS NOT NULL"
    )
    assert_base_acl()

    command.upgrade(config, "20260822_0027")
    assert pg_database.fetch_value(
        "SELECT version_num='20260822_0027' FROM alembic_version"
    )
    assert pg_database.fetch_rows(
        "SELECT has_function_privilege($1,$2,'EXECUTE') AS allowed",
        review_role,
        signature,
    )[0]["allowed"] is True
    assert_base_acl()
    command.upgrade(config, "head")
    assert pg_database.fetch_value(
        "SELECT version_num='20260825_0030' FROM alembic_version"
    )
