from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from app.core.database import dispose_slice6_runtime, get_slice6_session_factory
from app.core.uuid_generator import Uuid7Generator
from app.modules.health_plan.repository import HealthPlanRepository
from app.modules.health_plan.service import execute_generation
from app.tasks.celery_app import SLICE6_HEALTH_PLAN_QUEUE, celery_app


GENERATE_TASK = "phase1.slice6.generate_plan"
DISPATCH_TASK = "phase1.slice6.dispatch_outbox"
CONSUME_TASK = "phase1.slice6.consume_outbox"
RECOVER_TASK = "phase1.slice6.recover_outbox"
TASK_NAMES = {GENERATE_TASK, DISPATCH_TASK, CONSUME_TASK, RECOVER_TASK}


def _delivery_id(event_id: UUID) -> UUID:
    raw = bytearray(sha256(b"slice6-delivery-v1:" + event_id.bytes).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


def _run(operation):
    async def execute():
        try:
            return await operation()
        finally:
            try:
                await dispose_slice6_runtime("workflow_worker")
            except Exception:
                pass

    return asyncio.run(execute())


async def _generate(request_id: UUID) -> dict:
    factory = await get_slice6_session_factory("workflow_worker")
    async with factory() as session:
        repository = HealthPlanRepository(session)
        try:
            result = await execute_generation(
                repository,
                request_id=request_id,
                lease_owner=str(Uuid7Generator().generate()),
                now=datetime.now(timezone.utc),
                id_factory=Uuid7Generator().generate,
            )
            await session.commit()
            return result
        except Exception:
            await session.rollback()
            raise


async def _claim_one() -> str | None:
    factory = await get_slice6_session_factory("workflow_worker")
    async with factory() as session:
        repository = HealthPlanRepository(session)
        claimed = await repository.outbox_claim(None, 60)
        if claimed is None:
            await session.rollback()
            return None
        await session.commit()
        return str(claimed["event_id"])


async def _consume(event_id: UUID) -> str:
    factory = await get_slice6_session_factory("workflow_worker")
    async with factory() as session:
        repository = HealthPlanRepository(session)
        now = datetime.now(timezone.utc)
        result = await repository.outbox_consume(
            {
                "event_id": event_id,
                "delivery_id": _delivery_id(event_id),
                "target_type": "INTERNAL_EVENT",
                "target_ref": event_id,
                "target_digest": sha256(event_id.bytes).hexdigest(),
                "delivered_at": now,
            }
        )
        if result.get("event_type") == "PLAN_GENERATION_REQUESTED":
            generate_plan.apply_async(
                args=(str(result["aggregate_ref"]),), queue=SLICE6_HEALTH_PLAN_QUEUE
            )
        await session.commit()
        return str(result.get("status", "DELIVERED"))


async def _recover() -> int:
    factory = await get_slice6_session_factory("workflow_worker")
    async with factory() as session:
        result = await HealthPlanRepository(session).outbox_recover(datetime.now(timezone.utc))
        await session.commit()
        return int(result["recovered"])


@celery_app.task(name=GENERATE_TASK, queue=SLICE6_HEALTH_PLAN_QUEUE)
def generate_plan(request_id: str):
    return _run(lambda: _generate(UUID(request_id)))


@celery_app.task(name=DISPATCH_TASK, queue=SLICE6_HEALTH_PLAN_QUEUE)
def dispatch_outbox():
    event_id = _run(_claim_one)
    if event_id is not None:
        consume_outbox.apply_async(args=(event_id,), queue=SLICE6_HEALTH_PLAN_QUEUE)
    return event_id


@celery_app.task(name=CONSUME_TASK, queue=SLICE6_HEALTH_PLAN_QUEUE)
def consume_outbox(event_id: str):
    return _run(lambda: _consume(UUID(event_id)))


@celery_app.task(name=RECOVER_TASK, queue=SLICE6_HEALTH_PLAN_QUEUE)
def recover_outbox():
    return _run(_recover)
