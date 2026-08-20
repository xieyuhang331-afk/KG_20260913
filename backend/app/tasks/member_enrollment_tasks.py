from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select, text, update

from app.core.database import dispose_slice3_runtime, get_slice3_session_factory
from app.core.uuid_generator import Uuid7Generator
from app.modules.member_enrollment.models import (
    MemberEnrollmentDeliveryModel,
    MemberEnrollmentOutboxModel,
)
from app.modules.member_enrollment.repository import MemberEnrollmentRepository
from app.modules.member_enrollment.service import (
    MemberEnrollmentSecrets,
    MemberEnrollmentService,
    digest,
)
from app.tasks.celery_app import MEMBER_ENROLLMENT_QUEUE, celery_app
from app.tasks.phase1_workflow_tasks import rollback_shielded, safe_task_error


DISPATCH_TASK = "phase1.member_enrollment.dispatch_outbox"
CONSUME_TASK = "phase1.member_enrollment.consume_outbox"
RECOVER_TASK = "phase1.member_enrollment.recover_outbox"
EXPIRE_INVITATIONS_TASK = "phase1.member_enrollment.expire_invitations"
EXPIRY_TASK = EXPIRE_INVITATIONS_TASK


async def _claim_confirmation(
    event_id: UUID, lease_owner: UUID, *, attempts: int, version: int
) -> bool:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        row = (await session.execute(
            select(
                MemberEnrollmentOutboxModel.status,
                MemberEnrollmentOutboxModel.attempts,
                MemberEnrollmentOutboxModel.lease_owner,
                MemberEnrollmentOutboxModel.processing_at,
                MemberEnrollmentOutboxModel.version,
            ).where(MemberEnrollmentOutboxModel.event_id == event_id)
        )).one_or_none()
        return bool(
            row is not None and row.status == "PROCESSING"
            and row.attempts == attempts and row.lease_owner == lease_owner
            and row.processing_at is not None and row.version == version
        )


def _run(operation):
    async def execute():
        try:
            return await operation()
        finally:
            task = asyncio.create_task(dispose_slice3_runtime("workflow_worker"))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
    return asyncio.run(execute())


async def _claim_one() -> tuple[str, str] | None:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        try:
            row = (await session.execute(
                select(
                    MemberEnrollmentOutboxModel.event_id,
                    MemberEnrollmentOutboxModel.status,
                    MemberEnrollmentOutboxModel.attempts,
                    MemberEnrollmentOutboxModel.version,
                )
                .where(MemberEnrollmentOutboxModel.status == "PENDING")
                .order_by(MemberEnrollmentOutboxModel.created_at, MemberEnrollmentOutboxModel.event_id)
                .limit(1).with_for_update(skip_locked=True)
            )).one_or_none()
            if row is None:
                await session.rollback()
                return None
            lease = Uuid7Generator().generate()
            expected_attempts = row.attempts + 1
            expected_version = row.version + 1
            await session.execute(
                update(MemberEnrollmentOutboxModel)
                .where(MemberEnrollmentOutboxModel.event_id == row.event_id)
                .values(
                    status="PROCESSING", attempts=MemberEnrollmentOutboxModel.attempts + 1,
                    processing_at=datetime.now(timezone.utc), lease_owner=lease,
                    version=MemberEnrollmentOutboxModel.version + 1,
                )
            )
            try:
                await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                await rollback_shielded(session)
                if not await _claim_confirmation(
                    row.event_id, lease, attempts=expected_attempts, version=expected_version
                ):
                    raise safe_task_error() from None
            return str(row.event_id), str(lease)
        except asyncio.CancelledError:
            await rollback_shielded(session); raise
        except Exception:
            await rollback_shielded(session); raise safe_task_error() from None


def _target_value(row) -> dict[str, object]:
    return {
        "recipient_platform_code": row["recipient_platform_code"],
        "recipient_scope": row["recipient_scope"],
        "recipient_tenant_id": row["recipient_tenant_id"],
        "recipient_user_id": row["recipient_user_id"],
    }


def _target_digest(box: MemberEnrollmentSecrets, key_id: str, target: dict[str, object]) -> str:
    material = box.delivery_keys.get(key_id)
    if material is None:
        raise safe_task_error() from None
    derived = bytes.fromhex(box._material_check(material))
    canonical = "|".join(
        "" if target[key] is None else str(target[key])
        for key in (
            "recipient_scope", "recipient_user_id",
            "recipient_tenant_id", "recipient_platform_code",
        )
    )
    return box._hmac(derived, "delivery-target", hashlib.sha256(canonical.encode()).hexdigest())


async def _delivery_confirmation(event_id: UUID, expected: tuple[tuple, ...]) -> bool:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        event = (await session.execute(
            select(
                MemberEnrollmentOutboxModel.status,
                MemberEnrollmentOutboxModel.processing_at,
                MemberEnrollmentOutboxModel.lease_owner,
                MemberEnrollmentOutboxModel.delivered_at,
                MemberEnrollmentOutboxModel.failed_at,
            ).where(MemberEnrollmentOutboxModel.event_id == event_id)
        )).one_or_none()
        deliveries = tuple((await session.execute(
            select(
                MemberEnrollmentDeliveryModel.recipient_scope,
                MemberEnrollmentDeliveryModel.recipient_user_id,
                MemberEnrollmentDeliveryModel.recipient_tenant_id,
                MemberEnrollmentDeliveryModel.recipient_platform_code,
                MemberEnrollmentDeliveryModel.target_digest,
                MemberEnrollmentDeliveryModel.target_key_id,
                MemberEnrollmentDeliveryModel.payload_digest,
            ).where(MemberEnrollmentDeliveryModel.event_id == event_id).order_by(
                MemberEnrollmentDeliveryModel.recipient_scope,
                MemberEnrollmentDeliveryModel.recipient_user_id.nulls_last(),
                MemberEnrollmentDeliveryModel.recipient_tenant_id.nulls_last(),
                MemberEnrollmentDeliveryModel.recipient_platform_code.nulls_last(),
            )
        )).all())
        return bool(
            event is not None and event.status == "DELIVERED"
            and event.processing_at is None and event.lease_owner is None
            and event.delivered_at is not None and event.failed_at is None
            and deliveries == expected
        )


async def _recovery_confirmation(expected: dict[UUID, tuple[str, int, int]]) -> bool:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        rows = tuple((await session.execute(
            select(
                MemberEnrollmentOutboxModel.event_id,
                MemberEnrollmentOutboxModel.status,
                MemberEnrollmentOutboxModel.attempts,
                MemberEnrollmentOutboxModel.processing_at,
                MemberEnrollmentOutboxModel.lease_owner,
                MemberEnrollmentOutboxModel.version,
            ).where(MemberEnrollmentOutboxModel.event_id.in_(tuple(expected)))
        )).all())
        actual = {
            row.event_id: (row.status, row.attempts, row.version)
            for row in rows
            if row.processing_at is None and row.lease_owner is None
        }
        return actual == expected


async def _reopen_confirmation(
    event_id: UUID, actor_scope: str, expected: dict[str, object], request_digest: str
) -> bool:
    factory = await get_slice3_session_factory("identity_review_writer")
    async with factory() as session:
        event = (await session.execute(
            select(
                MemberEnrollmentOutboxModel.status,
                MemberEnrollmentOutboxModel.attempts,
                MemberEnrollmentOutboxModel.version,
            ).where(MemberEnrollmentOutboxModel.event_id == event_id)
        )).one_or_none()
        receipt = (await session.execute(text(
            "SELECT count(*) FROM public.member_enrollment_idempotency "
            "WHERE actor_scope=:scope AND operation='OUTBOX_REOPEN' AND target_id=:event "
            "AND request_digest=:digest"
        ), {"scope":actor_scope,"event":event_id,"digest":request_digest})).scalar_one()
        audit = (await session.execute(text(
            "SELECT count(*) FROM public.member_enrollment_audit WHERE actor_scope=:scope "
            "AND action='OUTBOX_REOPENED' AND object_id=:event"
        ), {"scope":actor_scope,"event":event_id})).scalar_one()
        return bool(
            event is not None and receipt == 1 and audit == 1
            and event.status == expected["status"]
            and event.attempts == expected["attempts"]
            and event.version == expected["version"]
        )


async def _consume(event_id: str, lease_owner: str) -> str:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        try:
            event = (await session.execute(
                select(MemberEnrollmentOutboxModel)
                .where(
                    MemberEnrollmentOutboxModel.event_id == event_id,
                    MemberEnrollmentOutboxModel.status == "PROCESSING",
                    MemberEnrollmentOutboxModel.lease_owner == lease_owner,
                ).with_for_update()
            )).scalar_one_or_none()
            if event is None:
                status = (await session.execute(
                    select(MemberEnrollmentOutboxModel.status).where(
                        MemberEnrollmentOutboxModel.event_id == event_id
                    )
                )).scalar_one_or_none()
                await session.rollback()
                return status or "NOT_FOUND"
            box = MemberEnrollmentSecrets()
            targets = tuple((await session.execute(
                text("SELECT * FROM public.slice3_outbox_recipient_targets_v1(:event,:lease)"),
                {"event": event.event_id, "lease": event.lease_owner},
            )).mappings())
            if not targets:
                now = datetime.now(timezone.utc)
                if event.attempts >= 3:
                    event.status = "FAILED"; event.failed_at = now
                else:
                    event.status = "PENDING"
                event.processing_at = None; event.lease_owner = None; event.version += 1
                expected_retry = {
                    event.event_id: (event.status, event.attempts, event.version)
                }
                try:
                    await session.commit()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await rollback_shielded(session)
                    if not await _recovery_confirmation(expected_retry):
                        raise safe_task_error() from None
                return event.status
            existing_rows = tuple((await session.execute(
                select(MemberEnrollmentDeliveryModel).where(
                    MemberEnrollmentDeliveryModel.event_id == event.event_id
                )
            )).scalars())
            expected = []
            for row in targets:
                target = _target_value(row)
                candidates = {
                    (key_id, _target_digest(box, key_id, target))
                    for key_id in box.delivery_keys
                }
                current_digest = _target_digest(box, box.delivery_key_id, target)
                if row["target_key_id"] != box.delivery_key_id or row["target_digest"] != current_digest:
                    raise safe_task_error() from None
                matching = [
                    delivery for delivery in existing_rows
                    if (delivery.target_key_id, delivery.target_digest) in candidates
                ]
                if len(matching) > 1:
                    raise safe_task_error() from None
                if matching:
                    delivery = matching[0]
                    if (
                        delivery.recipient_scope != row["recipient_scope"]
                        or delivery.recipient_user_id != row["recipient_user_id"]
                        or delivery.recipient_tenant_id != row["recipient_tenant_id"]
                        or delivery.recipient_platform_code != row["recipient_platform_code"]
                        or delivery.payload != row["payload"]
                        or delivery.payload_digest != row["payload_digest"]
                    ):
                        raise safe_task_error() from None
                else:
                    delivery = MemberEnrollmentDeliveryModel(
                        delivery_id=Uuid7Generator().generate(), event_id=event.event_id,
                        recipient_scope=row["recipient_scope"],
                        recipient_user_id=row["recipient_user_id"],
                        recipient_tenant_id=row["recipient_tenant_id"],
                        recipient_platform_code=row["recipient_platform_code"],
                        target_digest=current_digest, target_key_id=box.delivery_key_id,
                        payload=row["payload"], payload_digest=row["payload_digest"],
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(delivery)
                expected.append((
                    row["recipient_scope"], row["recipient_user_id"], row["recipient_tenant_id"],
                    row["recipient_platform_code"], current_digest, box.delivery_key_id,
                    row["payload_digest"],
                ))
            now = datetime.now(timezone.utc)
            event.status = "DELIVERED"; event.delivered_at = now
            event.processing_at = None; event.lease_owner = None; event.version += 1
            expected_value = tuple(sorted(expected, key=lambda value: tuple("" if item is None else str(item) for item in value[:4])))
            try:
                await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                await rollback_shielded(session)
                if not await _delivery_confirmation(event.event_id, expected_value):
                    raise safe_task_error() from None
            return "DELIVERED"
        except asyncio.CancelledError:
            await rollback_shielded(session); raise
        except Exception:
            await rollback_shielded(session); raise safe_task_error() from None


async def _recover() -> int:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        try:
            threshold = datetime.now(timezone.utc) - timedelta(minutes=5)
            rows = (await session.execute(
                select(MemberEnrollmentOutboxModel)
                .where(
                    MemberEnrollmentOutboxModel.status == "PROCESSING",
                    MemberEnrollmentOutboxModel.processing_at < threshold,
                ).with_for_update(skip_locked=True)
            )).scalars().all()
            expected = {}
            for row in rows:
                if row.attempts >= 3:
                    row.status = "FAILED"; row.failed_at = datetime.now(timezone.utc)
                else:
                    row.status = "PENDING"
                row.processing_at = None; row.lease_owner = None; row.version += 1
                expected[row.event_id] = (row.status, row.attempts, row.version)
            try:
                await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                await rollback_shielded(session)
                if not await _recovery_confirmation(expected):
                    raise safe_task_error() from None
            return len(rows)
        except asyncio.CancelledError:
            await rollback_shielded(session); raise
        except Exception:
            await rollback_shielded(session); raise safe_task_error() from None


async def claim_next_event() -> str | None:
    claimed = await _claim_one()
    return claimed[0] if claimed is not None else None


async def consume_event(event_id: str) -> str:
    factory = await get_slice3_session_factory("workflow_worker")
    async with factory() as session:
        lease = (await session.execute(
            select(MemberEnrollmentOutboxModel.lease_owner).where(
                MemberEnrollmentOutboxModel.event_id == event_id,
                MemberEnrollmentOutboxModel.status == "PROCESSING",
            )
        )).scalar_one_or_none()
    if lease is None:
        factory = await get_slice3_session_factory("workflow_worker")
        async with factory() as session:
            return (await session.execute(
                select(MemberEnrollmentOutboxModel.status).where(
                    MemberEnrollmentOutboxModel.event_id == event_id
                )
            )).scalar_one_or_none() or "NOT_FOUND"
    return await _consume(event_id, str(lease))


async def recover_stale_events() -> int:
    return await _recover()


async def reopen_failed_event(
    *, actor_user_id: int, event_id: str, expected_version: int, idempotency_key: str
) -> dict[str, object]:
    event_uuid = UUID(event_id)
    actor_scope = f"user:{actor_user_id}:platform"
    box = MemberEnrollmentSecrets()
    request_digest = box.request_digest({
        "actor_scope": actor_scope,
        "event_id": event_id,
        "expected_version": expected_version,
        "reason_code": "MANUAL_RETRY_APPROVED",
    })
    expected = {
        "attempts": 0,
        "event_id": event_id,
        "status": "PENDING",
        "version": expected_version + 1,
    }
    factory = await get_slice3_session_factory("identity_review_writer")
    async with factory() as session:
        try:
            row = (await session.execute(
                text(
                    "SELECT * FROM public.slice3_outbox_reopen_v1("
                    ":event,:actor,:scope,'MANUAL_RETRY_APPROVED',:version,:key,:digest,:expected)"
                ),
                {
                    "event": event_uuid, "actor": actor_user_id, "scope": actor_scope,
                    "version": expected_version, "key": idempotency_key,
                    "digest": request_digest,
                    "expected": json.dumps(expected, sort_keys=True, separators=(",", ":")),
                },
            )).mappings().one()
            try:
                await session.commit()
            except asyncio.CancelledError:
                raise
            except Exception:
                await rollback_shielded(session)
                if not await _reopen_confirmation(
                    event_uuid, actor_scope, expected, request_digest
                ):
                    raise safe_task_error() from None
            return dict(row)
        except asyncio.CancelledError:
            await rollback_shielded(session)
            raise
        except Exception:
            await rollback_shielded(session)
            raise safe_task_error() from None


async def _expire_invitations() -> int:
    factory = await get_slice3_session_factory("enrollment_writer")
    async with factory() as session:
        try:
            count = await MemberEnrollmentService(
                MemberEnrollmentRepository(session),
                secrets_port=MemberEnrollmentSecrets(),
            ).expire_invitations()
            await session.commit()
            return count
        except asyncio.CancelledError:
            await rollback_shielded(session)
            raise
        except Exception:
            await rollback_shielded(session)
            raise safe_task_error() from None


def _run_enrollment_writer(operation):
    async def execute():
        try:
            return await operation()
        finally:
            task = asyncio.create_task(dispose_slice3_runtime("enrollment_writer"))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
    return asyncio.run(execute())


@celery_app.task(name=DISPATCH_TASK)
def dispatch_outbox():
    claimed = _run(_claim_one)
    if claimed is not None:
        celery_app.send_task(CONSUME_TASK, args=claimed, queue=MEMBER_ENROLLMENT_QUEUE)
    return bool(claimed)


@celery_app.task(name=CONSUME_TASK, bind=True, max_retries=2)
def consume_outbox(self, event_id: str, lease_owner: str):
    try:
        _run(lambda: _consume(event_id, lease_owner))
    except Exception as error:
        raise self.retry(exc=safe_task_error(), countdown=2) from None


@celery_app.task(name=RECOVER_TASK)
def recover_outbox():
    return _run(_recover)


@celery_app.task(name=EXPIRE_INVITATIONS_TASK)
def expire_member_invitations_task():
    return _run_enrollment_writer(_expire_invitations)
