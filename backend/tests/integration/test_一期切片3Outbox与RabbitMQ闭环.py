from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest


pytestmark = pytest.mark.integration


EXPECTED_RECIPIENT_SCOPES = {
    "MEMBER_INVITATION_CREATED": ("TENANT",),
    "MEMBER_INVITATION_RESENT": ("TENANT",),
    "MEMBER_INVITATION_REVOKED": ("TENANT",),
    "MEMBER_INVITATION_EXPIRED": ("TENANT",),
    "MEMBER_ENROLLMENT_ACCEPTED": ("TENANT",),
    "MEMBER_IDENTITY_SUBMITTED": ("TENANT",),
    "MEMBER_IDENTITY_RESUBMITTED": ("TENANT",),
    "MEMBER_IDENTITY_INSTITUTION_CHECKED": ("PLATFORM",),
    "MEMBER_IDENTITY_CORRECTION_REQUESTED": ("SUBJECT",),
    "MEMBER_IDENTITY_REJECTED": ("SUBJECT",),
    "MEMBER_IDENTITY_VERIFIED": ("SUBJECT",),
    "PROXY_GRANT_ACTIVATED": ("SUBJECT",),
    "PROXY_GRANT_REVOKED": ("SUBJECT",),
    "PROXY_GRANT_EXPIRED": ("SUBJECT",),
    "CONSENT_DOCUMENT_PUBLISHED": ("PLATFORM",),
    "CONSENT_DOCUMENT_RETIRED": ("PLATFORM",),
    "CONSENT_ACCEPTED": ("SUBJECT",),
    "CONSENT_DECLINED": ("SUBJECT",),
    "CONSENT_WITHDRAWN": ("SUBJECT",),
    "CONSENT_SUPERSEDED": ("SUBJECT",),
    "PRIMARY_ASSIGNMENT_CREATED": ("THERAPIST",),
    "PRIMARY_ASSIGNMENT_DECLINED": ("THERAPIST",),
    "PRIMARY_ASSIGNMENT_CANCELLED": ("THERAPIST",),
    "SERVICE_CASE_PREPARING_CREATED": ("SUBJECT", "THERAPIST", "TENANT"),
}


@pytest.mark.asyncio
async def test_事件recipient_delivery_retry_reopen与崩溃恢复(
    pg_database, member_workflow_worker_database, member_identity_review_writer_database
):
    from app.modules.member_enrollment.models import EVENT_RECIPIENT_SCOPES, EVENT_TYPES
    from app.core.database import dispose_database_runtimes
    from app.tasks.member_enrollment_tasks import (
        _target_digest,
        _target_value,
        claim_next_event,
        consume_event,
        recover_stale_events,
        reopen_failed_event,
    )

    assert EVENT_RECIPIENT_SCOPES == EXPECTED_RECIPIENT_SCOPES
    assert set(EVENT_TYPES) == set(EXPECTED_RECIPIENT_SCOPES)

    source = Path("app/tasks/member_enrollment_tasks.py").read_text(encoding="utf-8")
    migration = Path(
        "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")

    assert "slice3_outbox_recipient_targets_v1" in source
    assert 'target = {"scope": "PLATFORM"' not in source
    assert "async def reopen_failed_event(" in source
    assert "async def claim_next_event(" in source
    assert "async def consume_event(" in source
    assert "async def recover_stale_events(" in source

    assert "CREATE OR REPLACE FUNCTION public.slice3_outbox_recipient_targets_v1" in migration
    assert "CREATE OR REPLACE FUNCTION public.slice3_outbox_reopen_v1" in migration
    assert "OUTBOX_REOPENED" in migration
    assert "MANUAL_RETRY_APPROVED" in migration
    assert "REVOKE ALL ON FUNCTION public.slice3_outbox_recipient_targets_v1" in migration
    assert "REVOKE ALL ON FUNCTION public.slice3_outbox_reopen_v1" in migration

    tenant_id = 96301
    inactive_tenant_id = 96302
    reviewer_id = 96303
    await pg_database._execute(
        "INSERT INTO public.platform_org(id,parent_id,org_name,org_code,org_type,status,version) "
        "VALUES (96304,NULL,'Slice3 outbox county','SLICE3-OUTBOX','county','active',1);"
        "INSERT INTO public.tenant(id,org_id,tenant_code,name,type,province,city,status,created_at,updated_at) VALUES "
        f"({tenant_id},96304,'S3-OB-A','Slice3 active','store','test','test','active',now(),now()),"
        f"({inactive_tenant_id},96304,'S3-OB-I','Slice3 inactive','store','test','test','closed',now(),now());"
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) VALUES '
        f"({reviewer_id},'13' || '5' || repeat('0',8),'test-only','super_admin','active',NULL)"
    )

    async def insert_event(event_id, event_type: str, event_tenant: int | None) -> None:
        tenant_sql = "NULL" if event_tenant is None else str(event_tenant)
        await pg_database._execute(
            "INSERT INTO public.member_enrollment_outbox("
            "event_id,event_type,aggregate_id,tenant_id,payload,payload_digest,status,attempts,"
            "processing_at,lease_owner,delivered_at,failed_at,created_at,version) VALUES ("
            f"'{event_id}','{event_type}','{uuid4()}',{tenant_sql},"
            f"'{{\"v\":1,\"event_id\":\"{event_id}\"}}'::jsonb,repeat('7',64),"
            "'PENDING',0,NULL,NULL,NULL,NULL,now(),1)"
        )

    try:
        tenant_event = uuid4()
        await insert_event(tenant_event, "MEMBER_INVITATION_CREATED", tenant_id)
        assert await claim_next_event() == str(tenant_event)
        assert await member_workflow_worker_database._fetch_value(
            "SELECT count(*)=1 FROM public.slice3_outbox_recipient_targets_v1("
            "$1,(SELECT lease_owner FROM public.member_enrollment_outbox WHERE event_id=$1))",
            tenant_event,
        )
        resolver_row = (await member_workflow_worker_database._fetch_rows(
            "SELECT * FROM public.slice3_outbox_recipient_targets_v1("
            "$1,(SELECT lease_owner FROM public.member_enrollment_outbox WHERE event_id=$1))",
            tenant_event,
        ))[0]
        from app.modules.member_enrollment.service import MemberEnrollmentSecrets
        box = MemberEnrollmentSecrets()
        assert resolver_row["target_key_id"] == box.delivery_key_id
        assert resolver_row["target_digest"] == _target_digest(
            box, box.delivery_key_id, _target_value(resolver_row)
        )
        assert await consume_event(str(tenant_event)) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT recipient_scope='TENANT' AND recipient_tenant_id=$1 "
            "FROM public.member_enrollment_delivery WHERE event_id=$2", tenant_id, tenant_event
        )

        platform_event = uuid4()
        await insert_event(platform_event, "CONSENT_DOCUMENT_PUBLISHED", None)
        assert await claim_next_event() == str(platform_event)
        assert await consume_event(str(platform_event)) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT recipient_scope='PLATFORM' AND recipient_platform_code='MEMBER_IDENTITY_REVIEW_QUEUE' "
            "FROM public.member_enrollment_delivery WHERE event_id=$1", platform_event
        )

        failed_event = uuid4()
        await insert_event(failed_event, "MEMBER_INVITATION_CREATED", inactive_tenant_id)
        for attempt, status in ((1, "PENDING"), (2, "PENDING"), (3, "FAILED")):
            assert await claim_next_event() == str(failed_event)
            assert await consume_event(str(failed_event)) == status
            assert await pg_database._fetch_value(
                "SELECT attempts=$1 AND status=$2 FROM public.member_enrollment_outbox WHERE event_id=$3",
                attempt, status, failed_event,
            )
        failed_version = await pg_database._fetch_value(
            "SELECT version FROM public.member_enrollment_outbox WHERE event_id=$1", failed_event
        )
        reopen_scope = f"user:{reviewer_id}:platform"
        reopen_request = {
            "actor_scope": reopen_scope,
            "event_id": str(failed_event),
            "expected_version": failed_version,
            "reason_code": "MANUAL_RETRY_APPROVED",
        }
        reopen_expected = {
            "attempts": 0,
            "event_id": str(failed_event),
            "status": "PENDING",
            "version": failed_version + 1,
        }
        direct_reopen = await member_identity_review_writer_database._fetch_rows(
            "SELECT * FROM public.slice3_outbox_reopen_v1($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
            failed_event, reviewer_id, reopen_scope, "MANUAL_RETRY_APPROVED", failed_version,
            "slice3-outbox-reopen-001", box.request_digest(reopen_request),
            json.dumps(reopen_expected, sort_keys=True, separators=(",", ":")),
        )
        assert direct_reopen[0]["status"] == "PENDING"
        reopened = await reopen_failed_event(
            actor_user_id=reviewer_id,
            event_id=str(failed_event),
            expected_version=failed_version,
            idempotency_key="slice3-outbox-reopen-001",
        )
        assert reopened["status"] == "PENDING"
        assert reopened["attempts"] == 0
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.member_enrollment_audit "
            "WHERE object_id=$1 AND action='OUTBOX_REOPENED'", failed_event
        )
        await pg_database._execute(
            "UPDATE public.tenant SET status='active',updated_at=now() "
            f"WHERE id={inactive_tenant_id}"
        )
        assert await claim_next_event() == str(failed_event)
        assert await consume_event(str(failed_event)) == "DELIVERED"

        stale_event = uuid4()
        await insert_event(stale_event, "MEMBER_INVITATION_CREATED", tenant_id)
        assert await claim_next_event() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.member_enrollment_outbox SET processing_at=now()-interval '10 minutes' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await recover_stale_events() == 1
        assert await pg_database._fetch_value(
            "SELECT status='PENDING' AND attempts=1 AND lease_owner IS NULL "
            "FROM public.member_enrollment_outbox WHERE event_id=$1", stale_event
        )

        assert await claim_next_event() == str(stale_event)
        assert await consume_event(str(stale_event)) == "DELIVERED"

        if os.getenv("KG_TEST_REAL_RABBIT") == "1":
            from app.tasks.celery_app import MEMBER_ENROLLMENT_QUEUE, celery_app
            from app.tasks.member_enrollment_tasks import DISPATCH_TASK

            broker_event = uuid4()
            await insert_event(broker_event, "MEMBER_INVITATION_CREATED", tenant_id)
            celery_app.send_task(DISPATCH_TASK, queue=MEMBER_ENROLLMENT_QUEUE)
            delivered = False
            for _ in range(120):
                delivered = bool(await pg_database._fetch_value(
                    "SELECT status='DELIVERED' FROM public.member_enrollment_outbox WHERE event_id=$1",
                    broker_event,
                ))
                if delivered:
                    break
                await asyncio.sleep(0.25)
            assert delivered
            assert await pg_database._fetch_value(
                "SELECT count(*)=1 FROM public.member_enrollment_delivery WHERE event_id=$1",
                broker_event,
            )
    finally:
        await dispose_database_runtimes()


def test_C2_reopen状态变化后稳定重放并按UserTenantTherapist锁序():
    import inspect
    from app.tasks import member_enrollment_tasks

    source = inspect.getsource(member_enrollment_tasks)
    for token in (
        "_claim_confirmation",
        "_delivery_confirmation",
        "_recovery_confirmation",
        "recipient_scope",
        "recipient_user_id",
        "recipient_tenant_id",
        "recipient_platform_code",
        "target_digest",
        "target_key_id",
        "payload_digest",
    ):
        assert token in source
    assert source.index("MemberEnrollmentOutboxModel") < source.index("slice3_outbox_recipient_targets_v1")


def test_F3_reopen统一Outbox_User_Replay锁序() -> None:
    migration = Path(
        "app/migrations/versions/20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
    ).read_text(encoding="utf-8")
    function = migration[
        migration.index("CREATE OR REPLACE FUNCTION public.slice3_outbox_reopen_v1") :
        migration.index(
            "REVOKE ALL ON FUNCTION public.slice3_outbox_reopen_v1",
            migration.index("CREATE OR REPLACE FUNCTION public.slice3_outbox_reopen_v1"),
        )
    ]
    outbox_lock = function.index(
        "FROM public.member_enrollment_outbox o\n          WHERE o.event_id=value_event_id FOR UPDATE"
    )
    user_lock = function.index(
        'FROM public."user" u WHERE u.id=value_reviewer_user_id FOR SHARE'
    )
    replay_lock = function.index(
        "FROM public.member_enrollment_idempotency i"
    )
    assert outbox_lock < user_lock < replay_lock


@pytest.mark.asyncio
async def test_F3_reopen与consumerRecovery及重复请求并发无死锁且后像稳定(
    pg_database,
    member_identity_review_writer_database,
):
    from app.modules.member_enrollment.service import MemberEnrollmentSecrets

    reviewer_id = 96391
    actor_scope = f"user:{reviewer_id}:platform"
    await pg_database._execute(
        'INSERT INTO public."user"(id,phone,password_hash,role,status,tenant_id) '
        f"VALUES ({reviewer_id},'13' || '5' || repeat('1',8),'test-only','super_admin','active',NULL)"
    )
    box = MemberEnrollmentSecrets()

    async def insert_failed(event_id) -> None:
        await pg_database._execute(
            "INSERT INTO public.member_enrollment_outbox("
            "event_id,event_type,aggregate_id,tenant_id,payload,payload_digest,status,attempts,"
            "processing_at,lease_owner,delivered_at,failed_at,created_at,version) VALUES ("
            f"'{event_id}','CONSENT_DOCUMENT_PUBLISHED','{uuid4()}',NULL,"
            f"'{{\"v\":1,\"event_id\":\"{event_id}\"}}'::jsonb,repeat('7',64),"
            "'FAILED',3,NULL,NULL,NULL,now(),now(),1)"
        )

    async def reopen(connection, event_id, key: str):
        request = {
            "actor_scope": actor_scope,
            "event_id": str(event_id),
            "expected_version": 1,
            "reason_code": "MANUAL_RETRY_APPROVED",
        }
        expected = {
            "attempts": 0,
            "event_id": str(event_id),
            "status": "PENDING",
            "version": 2,
        }
        return await connection.fetchrow(
            "SELECT * FROM public.slice3_outbox_reopen_v1($1,$2,$3,$4,$5,$6,$7,$8::jsonb)",
            event_id,
            reviewer_id,
            actor_scope,
            "MANUAL_RETRY_APPROVED",
            1,
            key,
            box.request_digest(request),
            json.dumps(expected, sort_keys=True, separators=(",", ":")),
        )

    review_connection = await asyncpg.connect(
        member_identity_review_writer_database.database_url
    )
    worker_connection = await asyncpg.connect(pg_database.database_url)
    try:
        for holder in ("consumer", "recovery"):
            event_id = uuid4()
            await insert_failed(event_id)
            transaction = worker_connection.transaction()
            await transaction.start()
            await worker_connection.fetchval(
                "SELECT event_id FROM public.member_enrollment_outbox "
                "WHERE event_id=$1 FOR UPDATE",
                event_id,
            )
            if holder == "consumer":
                await worker_connection.fetchval(
                    'SELECT id FROM public."user" WHERE id=$1 FOR SHARE',
                    reviewer_id,
                )
            blocked = asyncio.create_task(
                reopen(review_connection, event_id, f"slice3-f3-{holder}")
            )
            await asyncio.sleep(0.1)
            assert not blocked.done()
            await transaction.commit()
            row = await asyncio.wait_for(blocked, timeout=3)
            assert row["status"] == "PENDING"

        repeated_event = uuid4()
        await insert_failed(repeated_event)
        second_review_connection = await asyncpg.connect(
            member_identity_review_writer_database.database_url
        )
        try:
            first, second = await asyncio.gather(
                reopen(review_connection, repeated_event, "slice3-f3-repeat"),
                reopen(second_review_connection, repeated_event, "slice3-f3-repeat"),
            )
            assert first["status"] == second["status"] == "PENDING"
            assert first["version"] == second["version"] == 2
        finally:
            await second_review_connection.close()
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.member_enrollment_audit "
            "WHERE object_id=$1 AND action='OUTBOX_REOPENED'",
            repeated_event,
        )
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.member_enrollment_idempotency "
            "WHERE target_id=$1 AND operation='OUTBOX_REOPEN'",
            repeated_event,
        )

        drift_event = uuid4()
        await insert_failed(drift_event)
        await pg_database._execute(
            f'UPDATE public."user" SET status=\'disabled\' WHERE id={reviewer_id}'
        )
        with pytest.raises(asyncpg.PostgresError, match="SLICE3_OUTBOX_REOPEN_FORBIDDEN"):
            await reopen(review_connection, drift_event, "slice3-f3-currentness")
        assert await pg_database._fetch_value(
            "SELECT status='FAILED' AND version=1 FROM public.member_enrollment_outbox "
            "WHERE event_id=$1",
            drift_event,
        )
        assert await pg_database._fetch_value(
            "SELECT count(*)=0 FROM public.member_enrollment_audit WHERE object_id=$1",
            drift_event,
        )
    finally:
        await review_connection.close()
        await worker_connection.close()
