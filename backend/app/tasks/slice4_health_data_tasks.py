from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from app.core.database import (
    dispose_projection_runtime,
    dispose_slice4_runtime,
    get_projection_session_factory,
    get_slice4_session_factory,
)
from app.core.uuid_generator import Uuid7Generator
from app.modules.assessment_readiness.repository import AssessmentReadinessRepository
from app.modules.assessment_readiness.service import recompute_assessment_readiness
from app.modules.health_projection.repository import HealthProjectionRepository
from app.modules.health_projection.service import (
    HealthProjectionBuilderV2,
    ProjectionCheckpointConflict,
    ProjectionCommitOutcomeUnknown,
    ProjectionDigestKeyring,
    ProjectionDigestKeyUnavailable,
    ProjectionLeaseConflict,
    ProjectionUnitOfWork,
    ProjectionUnavailable,
)
from app.modules.organization_projection.domain import ProjectionSourceInvalid
from app.modules.organization_projection.service import (
    ProjectionReadyGate,
    ProjectionShadowService,
)
from app.core.config import get_settings
from app.modules.user_health.service import Slice4Secrets
from app.tasks.celery_app import SLICE4_HEALTH_QUEUE, celery_app


DISPATCH_TASK = "phase1.slice4.dispatch_outbox"
CONSUME_TASK = "phase1.slice4.consume_outbox"
RECOVER_TASK = "phase1.slice4.recover_outbox"
RECOMPUTE_TASK = "phase1.slice4.recompute_readiness"
SWEEP_TASK = "phase1.slice4.sweep_readiness"
BUILD_PROJECTION_TASK = "phase1.slice4.build_projection_v2"


def _projection_operation_id(root: UUID, stage: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"slice4/projection/{root}/{stage}"))


def _projection_failure_code(error: Exception) -> str | None:
    if isinstance(error, ProjectionCommitOutcomeUnknown):
        return None
    if isinstance(error, ProjectionSourceInvalid):
        return "PROJECTION_SOURCE_INVALID"
    if isinstance(error, ProjectionDigestKeyUnavailable):
        return "PROJECTION_DIGEST_KEY_UNAVAILABLE"
    if isinstance(error, ProjectionCheckpointConflict):
        return "PROJECTION_CHECKPOINT_CONFLICT"
    if isinstance(error, ProjectionLeaseConflict):
        return "PROJECTION_LEASE_CONFLICT"
    if isinstance(error, ProjectionUnavailable):
        return "PROJECTION_UNAVAILABLE"
    return "PROJECTION_UNAVAILABLE"


def _run(operation):
    async def execute():
        try:
            return await operation()
        finally:
            for kind in (
                "health_reader", "health", "confirmation", "health_shadow",
                "ready_gate", "shadow_confirmation",
            ):
                try:
                    await dispose_projection_runtime(kind)
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


async def _build_projection_v2(
    *, generation_no: int, builder_id: UUID, operation_id: UUID
) -> int:
    health_factory = await get_projection_session_factory("health")
    confirmation_factory = await get_projection_session_factory("confirmation")
    shadow_factory = await get_projection_session_factory("health_shadow")
    ready_factory = await get_projection_session_factory("ready_gate")
    shadow_confirmation_factory = await get_projection_session_factory(
        "shadow_confirmation"
    )
    settings = get_settings()
    keyring = ProjectionDigestKeyring.from_json(
        settings.health_projection_digest_current_key_id,
        settings.health_projection_digest_keyring_json,
    )

    async def lock_connection():
        return await health_factory.kw["bind"].connect()

    async def shadow_lock_connection():
        return await shadow_factory.kw["bind"].connect()

    async def ready_lock_connection():
        return await ready_factory.kw["bind"].connect()

    builder = HealthProjectionBuilderV2(
        lambda: ProjectionUnitOfWork(health_factory, HealthProjectionRepository),
        lambda: ProjectionUnitOfWork(
            confirmation_factory,
            HealthProjectionRepository,
            isolation_level="READ COMMITTED",
        ),
        keyring,
        lock_connection,
    )
    generation_id = await builder.start(
        generation_no=generation_no,
        builder_id=str(builder_id),
        operation_id=str(operation_id),
    )
    try:
        page_no = 0
        while True:
            async with ProjectionUnitOfWork(
                health_factory, HealthProjectionRepository
            ) as uow:
                generation = await uow.repository.get_generation(generation_id)
                checkpoint = await uow.repository.get_checkpoint(generation_id)
                if generation is None or checkpoint is None:
                    raise ProjectionUnavailable("Projection state is unavailable")
                if generation.status == "READY":
                    return generation_id
                if generation.status != "BUILDING":
                    break
                remaining_count = checkpoint.remaining_count
                lease_epoch = generation.lease_epoch
                generation_version = generation.version
                checkpoint_digest = checkpoint.checkpoint_digest
            if remaining_count == 0:
                await builder.complete(
                    generation_id=generation_id,
                    builder_id=str(builder_id),
                    lease_epoch=lease_epoch,
                    expected_generation_version=generation_version,
                    expected_checkpoint_digest=checkpoint_digest,
                    operation_id=_projection_operation_id(operation_id, "complete"),
                )
                break
            page_no += 1
            if page_no > 10000:
                raise ProjectionUnavailable("Projection page limit is exceeded")
            await builder.build_page(
                generation_id=generation_id,
                builder_id=str(builder_id),
                lease_epoch=lease_epoch,
                expected_checkpoint_digest=checkpoint_digest,
                operation_id=_projection_operation_id(operation_id, f"page/{page_no}"),
                heartbeat_operation_id=_projection_operation_id(
                    operation_id, f"heartbeat/{page_no}"
                ),
                page_size=100,
            )

        shadow = ProjectionShadowService(
            domain="health",
            uow_factory=lambda: ProjectionUnitOfWork(
                shadow_factory, HealthProjectionRepository
            ),
            confirmation_uow_factory=lambda: ProjectionUnitOfWork(
                shadow_confirmation_factory,
                HealthProjectionRepository,
                isolation_level="READ COMMITTED",
            ),
            keyring=keyring,
            lock_connection_factory=shadow_lock_connection,
        )
        ready_gate = ProjectionReadyGate(
            domain="health",
            uow_factory=lambda: ProjectionUnitOfWork(
                ready_factory, HealthProjectionRepository
            ),
            confirmation_uow_factory=lambda: ProjectionUnitOfWork(
                shadow_confirmation_factory,
                HealthProjectionRepository,
                isolation_level="READ COMMITTED",
            ),
            keyring=keyring,
            lock_connection_factory=ready_lock_connection,
        )
        while True:
            async with ProjectionUnitOfWork(
                shadow_factory, HealthProjectionRepository
            ) as uow:
                generation = await uow.repository.get_shadow_generation(generation_id)
                if generation is None:
                    raise ProjectionUnavailable("Projection state is unavailable")
                if generation.status == "READY":
                    return generation_id
                if generation.shadow_success_count == 2:
                    expected_version = generation.version
                    action = "ready"
                    sequence = None
                elif generation.status in {
                    "BUILD_COMPLETE", "SHADOW_FAILED", "SHADOW_PASSED"
                }:
                    sequence = await uow.repository.next_shadow_sequence(generation_id)
                    action = "shadow"
                    expected_version = None
                else:
                    raise ProjectionUnavailable("Projection state is unavailable")
            if action == "ready":
                await ready_gate.mark_ready(
                    generation_id=generation_id,
                    expected_version=expected_version,
                    operation_id=_projection_operation_id(operation_id, "ready"),
                )
                continue
            run_id = _projection_operation_id(
                operation_id, f"shadow/{sequence}/run"
            )
            await shadow.shadow_start(
                generation_id=generation_id,
                run_id=run_id,
                validator_id=str(builder_id),
                operation_id=_projection_operation_id(
                    operation_id, f"shadow/{sequence}/start"
                ),
            )
            outcome = await shadow.shadow_complete(
                generation_id=generation_id,
                run_id=run_id,
                operation_id=_projection_operation_id(
                    operation_id, f"shadow/{sequence}/complete"
                ),
            )
            if outcome != "PASSED":
                raise ProjectionUnavailable("Projection shadow evidence is unavailable")
    except asyncio.CancelledError:
        raise
    except Exception as error:
        failure_code = _projection_failure_code(error)
        if failure_code is not None:
            async with ProjectionUnitOfWork(
                health_factory, HealthProjectionRepository
            ) as uow:
                generation = await uow.repository.get_generation(generation_id)
                can_fail = (
                    generation is not None
                    and generation.status == "BUILDING"
                    and generation.builder_id == str(builder_id)
                )
                lease_epoch = generation.lease_epoch if can_fail else None
                generation_version = generation.version if can_fail else None
            if can_fail:
                await builder.fail(
                    generation_id=generation_id,
                    builder_id=str(builder_id),
                    lease_epoch=lease_epoch,
                    expected_generation_version=generation_version,
                    failure_code=failure_code,
                    operation_id=_projection_operation_id(operation_id, "fail"),
                )
        raise


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


@celery_app.task(name=BUILD_PROJECTION_TASK)
def build_projection_v2(generation_no: int, builder_id: str, operation_id: str):
    return _run(
        lambda: _build_projection_v2(
            generation_no=generation_no,
            builder_id=UUID(builder_id),
            operation_id=UUID(operation_id),
        )
    )
