from __future__ import annotations

import asyncio
import hmac
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.core.database import dispose_slice2_runtime, get_slice2_session_factory
from app.core.uuid_generator import Uuid7Generator
from app.modules.therapist_qualification.models import (
    TherapistProfileModel,
    TherapistWorkflowDeliveryModel,
    TherapistWorkflowOutboxModel,
)
from app.modules.therapist_qualification.repository import TherapistQualificationRepository
from app.modules.therapist_qualification.service import (
    TherapistSecrets,
    _audit,
    _digest,
    _outbox,
    _record,
    _replay,
    recompute_readiness,
)
from app.tasks.celery_app import celery_app
from app.tasks.phase1_workflow_tasks import rollback_shielded, safe_task_error


DISPATCH_TASK = "phase1.therapist.dispatch_outbox"
CONSUME_TASK = "phase1.therapist.consume_outbox"
RECOVER_TASK = "phase1.therapist.recover_workflow"
EXPIRY_TASK = "phase1.therapist.expire_qualifications"
READINESS_TASK = "phase1.therapist.recompute_readiness"
INVITATION_EXPIRY_TASK = "phase1.therapist.expire_invitations"
READINESS_SWEEP_TASK = "phase1.therapist.sweep_readiness"

_PLATFORM_EVENTS = frozenset({
    "THERAPIST_SUBMITTED",
    "THERAPIST_RESUBMITTED",
    "THERAPIST_QUALIFICATION_RENEWAL_SUBMITTED",
    "THERAPIST_QUALIFICATION_RENEWAL_RESUBMITTED",
})
_THERAPIST_ITEM_EVENTS = frozenset({
    "THERAPIST_CORRECTION_REQUESTED",
    "THERAPIST_QUALIFICATION_REVIEWED",
})
_THERAPIST_PROFILE_EVENTS = frozenset({
    "THERAPIST_REJECTED",
    "THERAPIST_APPROVED_ACTIVE",
    "THERAPIST_SUSPENDED",
    "THERAPIST_RESUMED",
})
_TENANT_ADMIN_EVENTS = frozenset({
    "THERAPIST_INVITED",
    "THERAPIST_INVITATION_REVOKED",
    "THERAPIST_INVITATION_EXPIRED",
    "THERAPIST_ACTIVATED",
    "THERAPIST_EXITED",
    "SERVICE_READINESS_RECOMPUTED",
})


def _run_worker(operation):
    async def execute():
        try:
            return await operation()
        finally:
            cleanup = asyncio.create_task(dispose_slice2_runtime("readiness_worker"))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise

    return asyncio.run(execute())


async def _outbox_snapshot(session, event_id: str):
    return (await session.execute(
        select(
            TherapistWorkflowOutboxModel.event_id,
            TherapistWorkflowOutboxModel.status,
            TherapistWorkflowOutboxModel.attempts,
            TherapistWorkflowOutboxModel.processing_at,
            TherapistWorkflowOutboxModel.lease_owner,
            TherapistWorkflowOutboxModel.delivered_at,
            TherapistWorkflowOutboxModel.failed_at,
            TherapistWorkflowOutboxModel.version,
        ).where(TherapistWorkflowOutboxModel.event_id == event_id)
    )).one_or_none()


async def _delivery_snapshot(session, event_id: str) -> tuple[tuple, ...]:
    rows = (await session.execute(
        select(
            TherapistWorkflowDeliveryModel.recipient_scope,
            TherapistWorkflowDeliveryModel.recipient_user_id,
            TherapistWorkflowDeliveryModel.recipient_tenant_id,
            TherapistWorkflowDeliveryModel.recipient_platform_code,
            TherapistWorkflowDeliveryModel.target_digest,
            TherapistWorkflowDeliveryModel.target_digest_key_id,
            TherapistWorkflowDeliveryModel.payload_digest,
            TherapistWorkflowDeliveryModel.created_at,
        )
        .where(TherapistWorkflowDeliveryModel.event_id == event_id)
        .order_by(
            TherapistWorkflowDeliveryModel.recipient_scope,
            TherapistWorkflowDeliveryModel.recipient_user_id.nulls_last(),
            TherapistWorkflowDeliveryModel.recipient_tenant_id.nulls_last(),
            TherapistWorkflowDeliveryModel.recipient_platform_code.nulls_last(),
        )
    )).all()
    return tuple(tuple(row) for row in rows)


async def _confirm_outbox(
    *,
    event_id: str,
    expected_outbox: tuple,
    expected_deliveries: tuple[tuple, ...],
) -> bool:
    factory = get_slice2_session_factory("reader")
    async with factory() as confirmation:
        actual_outbox = await _outbox_snapshot(confirmation, event_id)
        actual_deliveries = await _delivery_snapshot(confirmation, event_id)
        await confirmation.rollback()
    return actual_outbox == expected_outbox and actual_deliveries == expected_deliveries


async def _confirm_reopened_event(
    *,
    event_id: str,
    expected_outbox: tuple,
    scope: str,
    key: str,
    request_digest: str,
    postimage_digest: str,
) -> bool:
    if not await _confirm_outbox(
        event_id=event_id,
        expected_outbox=expected_outbox,
        expected_deliveries=(),
    ):
        return False
    factory = get_slice2_session_factory("reader")
    async with factory() as confirmation:
        receipt = (await confirmation.execute(text(
            "SELECT request_digest,postimage_digest "
            "FROM public.therapist_workflow_idempotency "
            "WHERE actor_scope=:scope AND operation='RECOVER_FAILED_OUTBOX' "
            "AND idempotency_key=:key"
        ), {"scope": scope, "key": key})).one_or_none()
        await confirmation.rollback()
    return bool(
        receipt is not None
        and hmac.compare_digest(receipt[0], request_digest)
        and hmac.compare_digest(receipt[1], postimage_digest)
    )


async def _commit_outbox(
    session,
    *,
    event_id: str,
    expected_outbox: tuple,
    expected_deliveries: tuple[tuple, ...] = (),
) -> None:
    try:
        await session.commit()
    except asyncio.CancelledError:
        await rollback_shielded(session)
        raise
    except Exception:
        await rollback_shielded(session)
        if await _confirm_outbox(
            event_id=event_id,
            expected_outbox=expected_outbox,
            expected_deliveries=expected_deliveries,
        ):
            return
        raise safe_task_error() from None


async def claim_next_event() -> str | None:
    factory = get_slice2_session_factory("readiness_worker")
    async with factory() as session:
        repo = TherapistQualificationRepository(session)
        now = datetime.now(timezone.utc)
        lease_owner = str(Uuid7Generator().generate())
        row = await repo.claim_outbox(now=now, lease_owner=lease_owner)
        if row is None:
            await session.rollback()
            return None
        event_id = row.event_id
        postimage = {
            "attempts": row.attempts,
            "event_id": event_id,
            "lease_owner": lease_owner,
            "processing_at": now,
            "status": "PROCESSING",
            "version": row.version,
        }
        await repo.add_audit(_audit(
            "THERAPIST_READINESS_WORKER_V1",
            "THERAPIST_OUTBOX_CLAIMED",
            event_id,
            event_id,
            {"attempts": row.attempts - 1, "status": "PENDING", "version": row.version - 1},
            postimage,
            now,
        ))
        expected = (
            event_id, "PROCESSING", row.attempts, now, lease_owner,
            None, None, row.version,
        )
        await _commit_outbox(session, event_id=event_id, expected_outbox=expected)
        return event_id


async def _targets(session, event) -> tuple[dict, ...]:
    if event.event_type in _PLATFORM_EVENTS:
        return ({
            "event_id": event.event_id,
            "recipient_platform_code": "THERAPIST_REVIEW_QUEUE",
            "recipient_scope": "PLATFORM",
            "recipient_tenant_id": None,
            "recipient_user_id": None,
            "v": 1,
        },)
    if event.event_type in _TENANT_ADMIN_EVENTS:
        rows = (await session.execute(
            text(
                "SELECT recipient_user_id,recipient_tenant_id "
                "FROM public.resolve_tenant_admin_delivery_targets_v1(:event_id) "
                "ORDER BY recipient_user_id"
            ),
            {"event_id": event.event_id},
        )).all()
        return tuple({
            "event_id": event.event_id,
            "recipient_platform_code": None,
            "recipient_scope": "TENANT_ADMIN",
            "recipient_tenant_id": tenant_id,
            "recipient_user_id": user_id,
            "v": 1,
        } for user_id, tenant_id in rows)
    if event.event_type in _THERAPIST_ITEM_EVENTS:
        user_id = (await session.execute(text(
            "SELECT p.user_id FROM public.therapist_review_item i "
            "JOIN public.therapist_profile p ON p.therapist_id=i.therapist_id "
            "WHERE i.review_item_id=:aggregate_id AND p.tenant_id=:tenant_id"
        ), {"aggregate_id": event.aggregate_id, "tenant_id": event.tenant_id})).scalar_one_or_none()
    elif event.event_type in _THERAPIST_PROFILE_EVENTS:
        user_id = (await session.execute(text(
            "SELECT user_id FROM public.therapist_profile "
            "WHERE therapist_id=:aggregate_id AND tenant_id=:tenant_id"
        ), {"aggregate_id": event.aggregate_id, "tenant_id": event.tenant_id})).scalar_one_or_none()
    else:
        return ()
    if user_id is None:
        return ()
    return ({
        "event_id": event.event_id,
        "recipient_platform_code": None,
        "recipient_scope": "THERAPIST",
        "recipient_tenant_id": None,
        "recipient_user_id": user_id,
        "v": 1,
    },)


async def _target_is_current(session, target: dict) -> bool:
    value = await session.scalar(
        text(
            "SELECT public.lock_delivery_recipient_currentness_v1("
            ":event_id,:scope,:user_id,:tenant_id,:platform_code)"
        ),
        {
            "event_id": target["event_id"],
            "scope": target["recipient_scope"],
            "user_id": target["recipient_user_id"],
            "tenant_id": target["recipient_tenant_id"],
            "platform_code": target["recipient_platform_code"],
        },
    )
    return value is True


async def _transition_delivery_failure(session, repo, event, now: datetime) -> str:
    if await _delivery_snapshot(session, event.event_id):
        raise safe_task_error() from None
    terminal = event.attempts == 3
    event.status = "FAILED" if terminal else "PENDING"
    event.processing_at = None
    event.lease_owner = None
    event.failed_at = now if terminal else None
    event.version += 1
    action = "THERAPIST_OUTBOX_FAILED" if terminal else "THERAPIST_OUTBOX_RETRY_SCHEDULED"
    postimage = {
        "attempts": event.attempts,
        "failed_at": event.failed_at,
        "status": event.status,
        "version": event.version,
    }
    await repo.add_audit(_audit(
        "THERAPIST_READINESS_WORKER_V1", action, event.event_id, event.event_id,
        {"attempts": event.attempts, "status": "PROCESSING", "version": event.version - 1},
        postimage, now,
    ))
    expected = (
        event.event_id, event.status, event.attempts, None, None,
        None, event.failed_at, event.version,
    )
    await _commit_outbox(session, event_id=event.event_id, expected_outbox=expected)
    return event.status


async def consume_event(event_id: str) -> str:
    factory = get_slice2_session_factory("readiness_worker")
    async with factory() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        repo = TherapistQualificationRepository(session)
        event = await repo.outbox_for_update(event_id)
        if event is None:
            await session.rollback()
            return "ABSENT"
        if event.status == "DELIVERED":
            await session.rollback()
            return "DELIVERED"
        if event.status != "PROCESSING":
            await session.rollback()
            return "IGNORED"
        now = datetime.now(timezone.utc)
        targets = await _targets(session, event)
        targets_are_current = bool(targets)
        for target in targets:
            if not await _target_is_current(session, target):
                targets_are_current = False
                break
        if not targets_are_current:
            return await _transition_delivery_failure(session, repo, event, now)
        if await _delivery_snapshot(session, event_id):
            raise safe_task_error() from None
        secret = TherapistSecrets()
        deliveries = []
        expected_deliveries = []
        for target in targets:
            digest = secret.delivery_target_digest(target)
            delivery = TherapistWorkflowDeliveryModel(
                delivery_id=str(Uuid7Generator().generate()),
                event_id=event_id,
                recipient_scope=target["recipient_scope"],
                recipient_user_id=target["recipient_user_id"],
                recipient_tenant_id=target["recipient_tenant_id"],
                recipient_platform_code=target["recipient_platform_code"],
                target_digest=digest,
                target_digest_key_id=secret.delivery_key_id,
                payload=event.payload,
                payload_digest=event.payload_digest,
                created_at=now,
            )
            deliveries.append(delivery)
            expected_deliveries.append((
                delivery.recipient_scope,
                delivery.recipient_user_id,
                delivery.recipient_tenant_id,
                delivery.recipient_platform_code,
                delivery.target_digest,
                delivery.target_digest_key_id,
                delivery.payload_digest,
                delivery.created_at,
            ))
        event.status = "DELIVERED"
        event.processing_at = None
        event.lease_owner = None
        event.delivered_at = now
        event.version += 1
        await repo.add_all(deliveries)
        await repo.add_audit(_audit(
            "THERAPIST_READINESS_WORKER_V1",
            "THERAPIST_OUTBOX_DELIVERED",
            event_id,
            event_id,
            {"attempts": event.attempts, "status": "PROCESSING", "version": event.version - 1},
            {"attempts": event.attempts, "deliveries": tuple(targets), "status": "DELIVERED", "version": event.version},
            now,
        ))
        expected = (
            event_id, "DELIVERED", event.attempts, None, None,
            now, None, event.version,
        )
        await _commit_outbox(
            session,
            event_id=event_id,
            expected_outbox=expected,
            expected_deliveries=tuple(expected_deliveries),
        )
        return "DELIVERED"


async def recover_stale_events() -> int:
    factory = get_slice2_session_factory("readiness_worker")
    threshold = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with factory() as discovery:
        event_ids = tuple((await discovery.execute(
            select(TherapistWorkflowOutboxModel.event_id)
            .where(
                TherapistWorkflowOutboxModel.status == "PROCESSING",
                TherapistWorkflowOutboxModel.processing_at < threshold,
            )
            .order_by(TherapistWorkflowOutboxModel.processing_at, TherapistWorkflowOutboxModel.event_id)
            .limit(100)
        )).scalars())
        await discovery.rollback()
    recovered = 0
    for event_id in event_ids:
        async with factory() as session:
            repo = TherapistQualificationRepository(session)
            event = await repo.outbox_for_update(event_id)
            if event is None or event.status != "PROCESSING" or event.processing_at is None or event.processing_at >= threshold:
                await session.rollback()
                continue
            await _transition_delivery_failure(session, repo, event, datetime.now(timezone.utc))
            recovered += 1
    return recovered


async def reopen_failed_event(
    *,
    actor_user_id: int,
    event_id: str,
    expected_version: int,
    idempotency_key: str,
) -> dict:
    factory = get_slice2_session_factory("readiness_worker")
    async with factory() as session:
        permitted = await session.scalar(text(
            "SELECT public.is_current_slice2_recovery_actor_v1(:actor_user_id)"
        ), {"actor_user_id": actor_user_id})
        if permitted is not True:
            raise RuntimeError("THERAPIST_OUTBOX_RECOVERY_FORBIDDEN") from None
        repo = TherapistQualificationRepository(session)
        scope = f"platform-super-admin:{actor_user_id}:outbox:{event_id}"
        request = {
            "event_id": event_id,
            "expected_version": expected_version,
            "reason_code": "MANUAL_RECOVERY",
        }
        request_digest, replay = await _replay(
            repo,
            scope=scope,
            operation="RECOVER_FAILED_OUTBOX",
            key=idempotency_key,
            request=request,
        )
        if replay is not None:
            return replay
        event = await repo.outbox_for_update(event_id)
        if (
            event is None
            or event.status != "FAILED"
            or event.attempts != 3
            or event.version != expected_version
            or await _delivery_snapshot(session, event_id)
        ):
            raise RuntimeError("THERAPIST_OUTBOX_RECOVERY_CONFLICT") from None
        now = datetime.now(timezone.utc)
        event.status = "PENDING"
        event.attempts = 0
        event.processing_at = None
        event.lease_owner = None
        event.delivered_at = None
        event.failed_at = None
        event.version += 1
        response = {
            "attempts": 0,
            "event_id": event_id,
            "status": "PENDING",
            "version": event.version,
        }
        await repo.add_audit(_audit(
            scope,
            "THERAPIST_OUTBOX_RECOVERED",
            event_id,
            event_id,
            request,
            response,
            now,
        ))
        await _record(
            repo,
            scope=scope,
            operation="RECOVER_FAILED_OUTBOX",
            key=idempotency_key,
            request_digest=request_digest,
            response=response,
            now=now,
        )
        expected = (event_id, "PENDING", 0, None, None, None, None, event.version)
        try:
            await session.commit()
        except asyncio.CancelledError:
            await rollback_shielded(session)
            raise
        except Exception:
            await rollback_shielded(session)
            if not await _confirm_reopened_event(
                event_id=event_id,
                expected_outbox=expected,
                scope=scope,
                key=idempotency_key,
                request_digest=request_digest,
                postimage_digest=_digest(response),
            ):
                raise safe_task_error() from None
        return response


async def _expire_due_invitations(limit: int = 100) -> int:
    factory = get_slice2_session_factory("readiness_worker")
    now = datetime.now(timezone.utc)
    async with factory() as discovery:
        invitation_ids = tuple((await discovery.execute(text(
            "SELECT invitation_id FROM public.therapist_invitation "
            "WHERE status='INVITED' AND expires_at<=:now "
            "ORDER BY expires_at,invitation_id LIMIT :limit"
        ), {"now": now, "limit": limit})).scalars())
        await discovery.rollback()
    expired = 0
    for invitation_id in invitation_ids:
        async with factory() as session:
            row = (await session.execute(text(
                "UPDATE public.therapist_invitation SET status='EXPIRED',version=version+1 "
                "WHERE invitation_id=:invitation_id AND status='INVITED' AND expires_at<=:now "
                "RETURNING tenant_id,version"
            ), {"invitation_id": invitation_id, "now": now})).one_or_none()
            if row is None:
                await session.rollback()
                continue
            tenant_id, version = row
            event = _outbox("THERAPIST_INVITATION_EXPIRED", invitation_id, tenant_id, now)
            repo = TherapistQualificationRepository(session)
            await repo.add_all((event,))
            await repo.add_audit(_audit(
                "THERAPIST_READINESS_WORKER_V1",
                "THERAPIST_INVITATION_EXPIRED",
                invitation_id,
                event.event_id,
                {"status": "INVITED", "version": version - 1},
                {"status": "EXPIRED", "version": version},
                now,
            ))
            expected_outbox = (
                event.event_id, "PENDING", 0, None, None, None, None, 1,
            )
            try:
                await session.commit()
            except asyncio.CancelledError:
                await rollback_shielded(session)
                raise
            except Exception:
                await rollback_shielded(session)
                async with get_slice2_session_factory("reader")() as confirmation:
                    invitation = (await confirmation.execute(text(
                        "SELECT status,version FROM public.therapist_invitation "
                        "WHERE invitation_id=:invitation_id"
                    ), {"invitation_id": invitation_id})).one_or_none()
                    audit_exists = await confirmation.scalar(text(
                        "SELECT EXISTS(SELECT 1 FROM public.therapist_workflow_audit "
                        "WHERE action='THERAPIST_INVITATION_EXPIRED' AND request_id=:event_id)"
                    ), {"event_id": event.event_id})
                    await confirmation.rollback()
                if (
                    invitation != ("EXPIRED", version)
                    or audit_exists is not True
                    or not await _confirm_outbox(
                        event_id=event.event_id,
                        expected_outbox=expected_outbox,
                        expected_deliveries=(),
                    )
                ):
                    raise safe_task_error() from None
            expired += 1
    return expired


async def _sweep_readiness(limit: int = 100) -> int:
    factory = get_slice2_session_factory("readiness_worker")
    local_date = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date()
    async with factory() as discovery:
        tenant_ids = tuple((await discovery.execute(text(
            "SELECT g.tenant_id FROM public.institution_readiness_guard_v1 g "
            "LEFT JOIN public.institution_service_readiness r ON r.tenant_id=g.tenant_id "
            "WHERE r.tenant_id IS NULL "
            "OR r.source_versions->>'tenant_public_id' IS DISTINCT FROM g.tenant_public_id::text "
            "OR r.source_versions->>'tenant_status' IS DISTINCT FROM g.tenant_status::text "
            "OR r.source_versions->>'application_id' IS DISTINCT FROM g.application_id::text "
            "OR (r.source_versions->>'application_version')::bigint IS DISTINCT FROM g.application_version "
            "OR r.source_versions->'license_versions' IS DISTINCT FROM g.license_versions "
            "OR r.source_versions->'current_therapist_versions' IS DISTINCT FROM g.current_therapist_versions "
            "OR r.next_expiry_at IS DISTINCT FROM g.next_expiry_at "
            "OR (r.readiness_status='SERVICE_READY' AND g.next_expiry_at<=CAST(:local_date AS DATE)) "
            "OR (r.readiness_status='NOT_READY' "
            "    AND 'INSTITUTION_LICENSE_INVALID'=ANY(r.reason_codes) "
            "    AND EXISTS(SELECT 1 FROM public.institution_readiness_source_v1 s WHERE s.tenant_id=g.tenant_id) "
            "    AND NOT EXISTS(SELECT 1 FROM public.institution_readiness_source_v1 s "
            "                   WHERE s.tenant_id=g.tenant_id "
            "                     AND NOT (s.valid_from<=CAST(:local_date AS DATE) "
            "                              AND CAST(:local_date AS DATE)<=s.valid_until))) "
            "ORDER BY g.tenant_id LIMIT :limit"
        ), {"local_date": local_date, "limit": limit})).scalars())
        await discovery.rollback()
    recomputed = 0
    for tenant_id in tenant_ids:
        async with factory() as session:
            await recompute_readiness(
                session,
                tenant_id,
                trigger_event_id=str(Uuid7Generator().generate()),
            )
            recomputed += 1
    return recomputed


async def _expire_due_qualifications(limit: int = 100) -> int:
    factory = get_slice2_session_factory("readiness_worker")
    async with factory() as discovery:
        candidates = tuple((await discovery.execute(
            select(
                TherapistProfileModel.therapist_id,
                TherapistProfileModel.tenant_id,
                TherapistProfileModel.version,
            )
            .where(
                TherapistProfileModel.status == "APPROVED_ACTIVE",
                TherapistProfileModel.qualification_valid_until
                < text("(transaction_timestamp() AT TIME ZONE 'Asia/Shanghai')::date"),
            )
            .order_by(TherapistProfileModel.qualification_valid_until, TherapistProfileModel.therapist_id)
            .limit(limit)
        )).all())
        await discovery.rollback()
    expired = 0
    for therapist_id, tenant_id, expected_version in candidates:
        async with factory() as session:
            now = datetime.now(timezone.utc)
            decision_id = str(Uuid7Generator().generate())
            request = {
                "decision": "SUSPENDED",
                "expected_version": expected_version,
                "reason_code": "QUALIFICATION_EXPIRED",
                "therapist_id": therapist_id,
            }
            accepted = await session.scalar(text(
                "SELECT public.record_therapist_expiry_suspension_v1("
                ":decision_id,:therapist_id,:expected_version,:request_digest)"
            ), {
                "decision_id": decision_id,
                "therapist_id": therapist_id,
                "expected_version": expected_version,
                "request_digest": _digest(request),
            })
            if accepted is not True:
                await session.rollback()
                continue
            updated = (await session.execute(text(
                "UPDATE public.therapist_profile SET status='SUSPENDED',"
                "suspension_reason_code='QUALIFICATION_EXPIRED',suspended_at=:now,"
                "updated_at=:now,version=version+1 "
                "WHERE therapist_id=:therapist_id AND status='APPROVED_ACTIVE' "
                "AND version=:expected_version RETURNING tenant_id,version"
            ), {"now": now, "therapist_id": therapist_id, "expected_version": expected_version})).one_or_none()
            if updated is None or updated[0] != tenant_id:
                await session.rollback()
                continue
            event = _outbox("THERAPIST_SUSPENDED", therapist_id, tenant_id, now)
            repo = TherapistQualificationRepository(session)
            await repo.add_all((event,))
            await repo.add_audit(_audit(
                "THERAPIST_EXPIRY_WORKER_V1",
                "THERAPIST_SUSPENDED",
                therapist_id,
                decision_id,
                request,
                {"status": "SUSPENDED", "version": updated[1]},
                now,
            ))
            await recompute_readiness(
                session,
                tenant_id,
                trigger_event_id=event.event_id,
                now=now,
                commit=False,
            )
            try:
                await session.commit()
            except asyncio.CancelledError:
                await rollback_shielded(session)
                raise
            except Exception:
                await rollback_shielded(session)
                async with get_slice2_session_factory("reader")() as confirmation:
                    state = (await confirmation.execute(text(
                        "SELECT status,version FROM public.therapist_profile "
                        "WHERE therapist_id=:therapist_id"
                    ), {"therapist_id": therapist_id})).one_or_none()
                    decision = await confirmation.scalar(text(
                        "SELECT status_decision_id FROM public.therapist_status_decision "
                        "WHERE status_decision_id=:decision_id"
                    ), {"decision_id": decision_id})
                    await confirmation.rollback()
                if state != ("SUSPENDED", updated[1]) or decision != decision_id:
                    raise safe_task_error() from None
            expired += 1
    return expired


@celery_app.task(name=DISPATCH_TASK, acks_late=True, reject_on_worker_lost=True)
def dispatch_outbox() -> str | None:
    try:
        event_id = _run_worker(claim_next_event)
        if event_id:
            celery_app.send_task(CONSUME_TASK, args=(event_id,), queue="therapist-workflow")
        return event_id
    except Exception:
        raise safe_task_error() from None


@celery_app.task(name=CONSUME_TASK, bind=True, max_retries=3, acks_late=True, reject_on_worker_lost=True)
def consume_outbox(self, event_id: str):
    try:
        return _run_worker(lambda: consume_event(event_id))
    except Exception:
        raise self.retry(exc=safe_task_error(), countdown=2 ** self.request.retries) from None


@celery_app.task(name=RECOVER_TASK, acks_late=True, reject_on_worker_lost=True)
def recover_workflow() -> int:
    try:
        return _run_worker(recover_stale_events)
    except Exception:
        raise safe_task_error() from None


@celery_app.task(name=READINESS_TASK, acks_late=True, reject_on_worker_lost=True)
def recompute_tenant_readiness(tenant_id: int, trigger_event_id: str) -> str:
    async def run() -> str:
        factory = get_slice2_session_factory("readiness_worker")
        async with factory() as session:
            await recompute_readiness(session, tenant_id, trigger_event_id=trigger_event_id)
        return trigger_event_id

    try:
        return _run_worker(run)
    except Exception:
        raise safe_task_error() from None


@celery_app.task(name=EXPIRY_TASK, acks_late=True, reject_on_worker_lost=True)
def expire_qualifications() -> int:
    try:
        return _run_worker(_expire_due_qualifications)
    except Exception:
        raise safe_task_error() from None


@celery_app.task(name=INVITATION_EXPIRY_TASK, acks_late=True, reject_on_worker_lost=True)
def expire_invitations() -> int:
    try:
        return _run_worker(_expire_due_invitations)
    except Exception:
        raise safe_task_error() from None


@celery_app.task(name=READINESS_SWEEP_TASK, acks_late=True, reject_on_worker_lost=True)
def sweep_readiness() -> int:
    try:
        return _run_worker(_sweep_readiness)
    except Exception:
        raise safe_task_error() from None
