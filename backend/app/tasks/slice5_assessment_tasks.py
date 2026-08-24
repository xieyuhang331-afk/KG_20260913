from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from app.core.database import (
    dispose_slice5_runtime,
    get_slice4_session_factory,
    get_slice5_session_factory,
)
from app.core.uuid_generator import Uuid7Generator
from app.modules.health_assessment.repository import HealthAssessmentRepository
from app.modules.health_assessment.service import Slice5Secrets, execute_assessment, fail_assessment
from app.modules.member_enrollment.identity_authority import Slice4IdentitySummaryAuthority
from app.tasks.celery_app import SLICE5_ASSESSMENT_QUEUE, celery_app


RUN_TASK = "phase1.slice5.run_assessment"
DISPATCH_TASK = "phase1.slice5.dispatch_outbox"
CONSUME_TASK = "phase1.slice5.consume_outbox"
RECOVER_TASK = "phase1.slice5.recover_outbox"
TASK_NAMES = {RUN_TASK, DISPATCH_TASK, CONSUME_TASK, RECOVER_TASK}


def _delivery_id(event_id: UUID) -> UUID:
    raw = bytearray(sha256(b"slice5-delivery-v1:" + event_id.bytes).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return UUID(bytes=bytes(raw))


def _run(operation):
    async def execute():
        try:
            return await operation()
        finally:
            for kind in ("workflow_worker",):
                try:
                    await dispose_slice5_runtime(kind)
                except Exception:
                    pass

    return asyncio.run(execute())


async def _run_assessment(assessment_id: UUID) -> dict:
    factory = await get_slice5_session_factory("workflow_worker")
    identity_factory = await get_slice4_session_factory("identity_authority")
    async with factory() as session, identity_factory() as identity_session:
        repository = HealthAssessmentRepository(session)
        try:
            return await execute_assessment(
                repository,
                assessment_id=assessment_id,
                lease_owner=str(Uuid7Generator().generate()),
                identity_authority=Slice4IdentitySummaryAuthority(identity_session),
                confirmation_session_factory=factory,
            )
        except RuntimeError as exc:
            if str(exc) != "RULE_EVALUATION_UNAVAILABLE":
                raise
            await session.rollback()
            current = await repository.confirm_assessment(assessment_id)
            if current is None or current.get("assessment", {}).get("status") != "RUNNING":
                raise RuntimeError("SLICE5_WORKFLOW_STATE_CONFLICT") from None
            return await fail_assessment(
                repository,
                assessment_id=assessment_id,
                expected_version=int(current["assessment"]["version"]),
                failure_code="RULE_EVALUATION_UNAVAILABLE",
                failed_at=datetime.fromtimestamp(
                    int(current["assessment"]["initiated_at_us"]) / 1_000_000,
                    timezone.utc,
                ),
                confirmation_session_factory=factory,
            )


async def _claim_one() -> str | None:
    factory = await get_slice5_session_factory("workflow_worker")
    async with factory() as session:
        repository = HealthAssessmentRepository(session)
        rows = await repository.outbox_claim(str(Uuid7Generator().generate()), 1)
        if not rows:
            await session.rollback()
            return None
        await session.commit()
        return str(rows[0]["event_id"])


async def _consume(event_id: UUID) -> str:
    factory = await get_slice5_session_factory("workflow_worker")
    async with factory() as session:
        repository = HealthAssessmentRepository(session)
        now = datetime.now(timezone.utc)
        digest, _ = Slice5Secrets().digest(
            "OUTBOX",
            {
                "event_id": event_id,
                "target_type": "INTERNAL_EVENT",
            },
        )
        result = await repository.outbox_consume(
            {
                "event_id": event_id,
                "delivery_id": _delivery_id(event_id),
                "target_digest": digest.hex(),
                "delivered_at": now,
            }
        )
        if result.get("new_delivery") and result.get("event_type") == "ASSESSMENT_RUN_REQUESTED":
            run_assessment.apply_async(
                args=(str(result["aggregate_ref"]),), queue=SLICE5_ASSESSMENT_QUEUE
            )
        await session.commit()
        return str(result["status"])


async def _recover() -> int:
    factory = await get_slice5_session_factory("workflow_worker")
    async with factory() as session:
        repository = HealthAssessmentRepository(session)
        result = await repository.outbox_recover(datetime.now(timezone.utc))
        await session.commit()
        return int(result["recovered"])


@celery_app.task(name=RUN_TASK, queue=SLICE5_ASSESSMENT_QUEUE)
def run_assessment(assessment_id: str):
    return _run(lambda: _run_assessment(UUID(assessment_id)))


@celery_app.task(name=DISPATCH_TASK, queue=SLICE5_ASSESSMENT_QUEUE)
def dispatch_outbox():
    event_id = _run(_claim_one)
    if event_id is not None:
        consume_outbox.apply_async(args=(event_id,), queue=SLICE5_ASSESSMENT_QUEUE)
    return event_id


@celery_app.task(name=CONSUME_TASK, queue=SLICE5_ASSESSMENT_QUEUE)
def consume_outbox(event_id: str):
    return _run(lambda: _consume(UUID(event_id)))


@celery_app.task(name=RECOVER_TASK, queue=SLICE5_ASSESSMENT_QUEUE)
def recover_outbox():
    return _run(_recover)
