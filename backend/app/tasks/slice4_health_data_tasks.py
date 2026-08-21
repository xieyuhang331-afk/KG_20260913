from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text

from app.core.database import (
    dispose_projection_runtime,
    dispose_slice4_runtime,
    get_slice4_session_factory,
)
from app.core.uuid_generator import Uuid7Generator
from app.modules.assessment_readiness.repository import AssessmentReadinessRepository
from app.modules.assessment_readiness.service import recompute_assessment_readiness
from app.modules.user_health.service import Slice4Secrets
from app.tasks.celery_app import SLICE4_HEALTH_QUEUE, celery_app


DISPATCH_TASK = "phase1.slice4.dispatch_outbox"
CONSUME_TASK = "phase1.slice4.consume_outbox"
RECOVER_TASK = "phase1.slice4.recover_outbox"
RECOMPUTE_TASK = "phase1.slice4.recompute_readiness"
SWEEP_TASK = "phase1.slice4.sweep_readiness"


def _run(operation):
    async def execute():
        try:
            return await operation()
        finally:
            try:
                await dispose_projection_runtime("health_reader")
            except Exception:
                pass
            for kind in ("workflow_worker", "assessment_readiness_writer"):
                try:
                    await dispose_slice4_runtime(kind)
                except Exception:
                    pass

    return asyncio.run(execute())


async def _claim_one() -> str | None:
    factory = await get_slice4_session_factory("workflow_worker")
    lease_owner = str(Uuid7Generator().generate())
    now = datetime.now(timezone.utc)
    async with factory() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT event_id,attempts FROM public.slice4_outbox "
                        "WHERE status='PENDING' ORDER BY created_at,event_id "
                        "LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            await session.execute(
                text(
                    "UPDATE public.slice4_outbox SET status='PROCESSING',attempts=attempts+1,"
                    "lease_owner=:owner,lease_until=:lease_until WHERE event_id=:event_id"
                ),
                {
                    "owner": lease_owner,
                    "lease_until": now + timedelta(seconds=60),
                    "event_id": row.event_id,
                },
            )
            return str(row.event_id)


async def _recompute(case_id: UUID) -> dict:
    factory = await get_slice4_session_factory("assessment_readiness_writer")
    async with factory() as session:
        async with session.begin():
            return await recompute_assessment_readiness(
                AssessmentReadinessRepository(session), service_case_id=case_id
            )


async def _consume(event_id: UUID) -> str:
    factory = await get_slice4_session_factory("workflow_worker")
    async with factory() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        "SELECT event_id,aggregate_ref,event_type,payload_json,status,attempts "
                        "FROM public.slice4_outbox WHERE event_id=:event_id FOR UPDATE"
                    ),
                    {"event_id": event_id},
                )
            ).mappings().one_or_none()
            if row is None:
                return "NOT_FOUND"
            if row["status"] == "DELIVERED":
                return "DELIVERED"
            if row["status"] != "PROCESSING":
                raise RuntimeError("SLICE4_WORKFLOW_STATE_CONFLICT") from None
            payload = dict(row["payload_json"])
            case_value = payload.get("service_case_id")
            event_type = row["event_type"]
        if case_value is not None and event_type != "ASSESSMENT_ASSEMBLY_WRITTEN":
            await _recompute(UUID(str(case_value)))

    delivery_id = Uuid7Generator().generate()
    secrets = Slice4Secrets()
    target_digest, _ = secrets.digest(
        "DELIVERY", {"event_id": str(event_id), "target_ref": str(row["aggregate_ref"])}
    )
    factory = await get_slice4_session_factory("workflow_worker")
    async with factory() as session:
        async with session.begin():
            locked = (
                await session.execute(
                    text(
                        "SELECT status,attempts FROM public.slice4_outbox "
                        "WHERE event_id=:event_id FOR UPDATE"
                    ),
                    {"event_id": event_id},
                )
            ).one_or_none()
            if locked is None:
                return "NOT_FOUND"
            if locked.status == "DELIVERED":
                return "DELIVERED"
            if locked.status != "PROCESSING":
                raise RuntimeError("SLICE4_WORKFLOW_STATE_CONFLICT") from None
            await session.execute(
                text(
                    "INSERT INTO public.slice4_delivery(delivery_id,event_id,target_type,"
                    "target_ref,target_digest,delivered_at) VALUES "
                    "(:delivery_id,:event_id,'INTERNAL_HEALTH_EVENT',:target_ref,:digest,:now) "
                    "ON CONFLICT(event_id,target_digest) DO NOTHING"
                ),
                {
                    "delivery_id": delivery_id,
                    "event_id": event_id,
                    "target_ref": row["aggregate_ref"],
                    "digest": target_digest,
                    "now": datetime.now(timezone.utc),
                },
            )
            await session.execute(
                text(
                    "UPDATE public.slice4_outbox SET status='DELIVERED',lease_owner=NULL,"
                    "lease_until=NULL,delivered_at=:now WHERE event_id=:event_id"
                ),
                {"event_id": event_id, "now": datetime.now(timezone.utc)},
            )
    return "DELIVERED"


async def _recover() -> int:
    factory = await get_slice4_session_factory("workflow_worker")
    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                text(
                    "UPDATE public.slice4_outbox SET "
                    "status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'PENDING' END,"
                    "lease_owner=NULL,lease_until=NULL WHERE status='PROCESSING' "
                    "AND lease_until<clock_timestamp() RETURNING event_id"
                )
            )
            return len(result.all())


async def _candidates(limit: int = 100) -> tuple[str, ...]:
    factory = await get_slice4_session_factory("workflow_worker")
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT case_id FROM public.slice4_recompute_candidate_v1 "
                    "ORDER BY case_id LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).all()
        await session.rollback()
        return tuple(str(row.case_id) for row in rows)


@celery_app.task(name=DISPATCH_TASK)
def dispatch_outbox():
    event_id = _run(_claim_one)
    if event_id is not None:
        celery_app.send_task(CONSUME_TASK, args=[event_id], queue=SLICE4_HEALTH_QUEUE)
    return event_id


@celery_app.task(name=CONSUME_TASK)
def consume_outbox(event_id: str):
    return _run(lambda: _consume(UUID(event_id)))


@celery_app.task(name=RECOVER_TASK)
def recover_outbox():
    return _run(_recover)


@celery_app.task(name=RECOMPUTE_TASK)
def recompute_readiness(case_id: str):
    return _run(lambda: _recompute(UUID(case_id)))


@celery_app.task(name=SWEEP_TASK)
def sweep_readiness():
    values = _run(_candidates)
    for case_id in values:
        celery_app.send_task(RECOMPUTE_TASK, args=[case_id], queue=SLICE4_HEALTH_QUEUE)
    return len(values)
