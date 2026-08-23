from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from app.core.uuid_generator import Uuid7Generator


pytestmark = pytest.mark.integration


def test_G04_G06_Slice4队列路由Beat与Outbox真值合同() -> None:
    from app.tasks.celery_app import SLICE4_HEALTH_QUEUE, celery_app
    from app.tasks.slice4_health_data_tasks import (
        BUILD_PROJECTION_TASK,
        CONSUME_TASK,
        DISPATCH_TASK,
        RECOVER_TASK,
        RECOMPUTE_TASK,
        SWEEP_TASK,
    )

    assert SLICE4_HEALTH_QUEUE == "slice4-health-workflow"
    for task_name in (
        BUILD_PROJECTION_TASK,
        DISPATCH_TASK,
        CONSUME_TASK,
        RECOVER_TASK,
        RECOMPUTE_TASK,
        SWEEP_TASK,
    ):
        assert celery_app.conf.task_routes[task_name] == {"queue": SLICE4_HEALTH_QUEUE}
    assert celery_app.conf.beat_schedule["slice4-health-dispatch"]["schedule"] == 5.0
    assert celery_app.conf.beat_schedule["slice4-health-recovery"]["schedule"] == 60.0
    assert celery_app.conf.beat_schedule["slice4-readiness-sweep"]["schedule"] == 60.0

    migration = Path(
        "app/migrations/versions/20260823_0028_phase1_slice4_health_record_assessment_readiness.py"
    ).read_text(encoding="utf-8")
    normalized_migration = " ".join(migration.split()).replace('" "', "")
    for truth in (
        "status='PENDING' AND attempts BETWEEN 0 AND 2 AND lease_owner IS NULL AND lease_until IS NULL AND delivered_at IS NULL",
        "status='PROCESSING' AND attempts BETWEEN 1 AND 3 AND lease_owner IS NOT NULL AND lease_until IS NOT NULL AND delivered_at IS NULL",
        "status='DELIVERED' AND attempts BETWEEN 1 AND 3 AND lease_owner IS NULL AND lease_until IS NULL AND delivered_at IS NOT NULL",
        "status='FAILED' AND attempts=3 AND lease_owner IS NULL AND lease_until IS NULL AND delivered_at IS NULL",
    ):
        assert truth in normalized_migration


@pytest.mark.asyncio
async def test_G04_G06_真实WorkflowWorker投递幂等崩溃恢复与Rabbit闭环(
    pg_database,
    slice4_workflow_worker_database,
) -> None:
    from app.core.database import dispose_slice4_runtime
    from app.tasks.slice4_health_data_tasks import _claim_one, _consume, _recover

    async def insert_pending(event_id) -> None:
        await pg_database._execute(
            "INSERT INTO public.slice4_outbox("
            "event_id,aggregate_type,aggregate_ref,event_type,payload_digest,payload_json,"
            "status,attempts,created_at) VALUES ("
            f"'{event_id}','HEALTH_PROFILE','{Uuid7Generator().generate()}',"
            "'HEALTH_PROFILE_UPDATED',decode(repeat('4',64),'hex'),"
            f"'{{\"event_id\":\"{event_id}\"}}'::jsonb,'PENDING',0,now())"
        )

    try:
        event_id = Uuid7Generator().generate()
        await insert_pending(event_id)
        assert await _claim_one() == str(event_id)
        assert await slice4_workflow_worker_database._fetch_value(
            "SELECT status='PROCESSING' AND attempts=1 AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL FROM public.slice4_outbox WHERE event_id=$1",
            event_id,
        )
        assert await _consume(event_id) == "DELIVERED"
        assert await _consume(event_id) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.slice4_delivery WHERE event_id=$1", event_id
        )
        assert await pg_database._fetch_value(
            "SELECT status='DELIVERED' AND attempts=1 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NOT NULL "
            "FROM public.slice4_outbox WHERE event_id=$1",
            event_id,
        )

        stale_event = Uuid7Generator().generate()
        await insert_pending(stale_event)
        assert await _claim_one() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.slice4_outbox SET lease_until=now()-interval '1 second' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await _recover() == 1
        assert await pg_database._fetch_value(
            "SELECT status='PENDING' AND attempts=1 AND lease_owner IS NULL "
            "AND lease_until IS NULL FROM public.slice4_outbox WHERE event_id=$1",
            stale_event,
        )
        assert await _claim_one() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.slice4_outbox SET attempts=3,lease_until=now()-interval '1 second' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await _recover() == 1
        assert await pg_database._fetch_value(
            "SELECT status='FAILED' AND attempts=3 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NULL "
            "FROM public.slice4_outbox WHERE event_id=$1",
            stale_event,
        )

        if os.getenv("KG_TEST_REAL_RABBIT") == "1":
            from app.tasks.celery_app import SLICE4_HEALTH_QUEUE, celery_app
            from app.tasks.slice4_health_data_tasks import DISPATCH_TASK

            broker_event = Uuid7Generator().generate()
            await insert_pending(broker_event)
            celery_app.send_task(DISPATCH_TASK, queue=SLICE4_HEALTH_QUEUE)
            for _ in range(120):
                delivered = bool(
                    await pg_database._fetch_value(
                        "SELECT status='DELIVERED' FROM public.slice4_outbox WHERE event_id=$1",
                        broker_event,
                    )
                )
                if delivered:
                    break
                await asyncio.sleep(0.25)
            assert delivered
            assert await pg_database._fetch_value(
                "SELECT count(*)=1 FROM public.slice4_delivery WHERE event_id=$1",
                broker_event,
            )
    finally:
        await dispose_slice4_runtime("workflow_worker")
