from __future__ import annotations

import os

import pytest

from app.core.uuid_generator import Uuid7Generator


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_MQ01_MQ07_Outbox受限函数稳定投递恢复与RabbitMQ闭环(
    pg_database,
) -> None:
    from app.core.database import dispose_slice6_runtime
    from app.tasks.slice6_health_plan_tasks import (
        DISPATCH_TASK,
        _claim_one,
        _consume,
        _delivery_id,
        _recover,
    )

    generated = Uuid7Generator()
    event_ids = []

    async def insert_pending():
        event_id = generated.generate()
        event_ids.append(event_id)
        await pg_database._execute(
            "INSERT INTO public.health_plan_outbox(event_id,aggregate_type,aggregate_ref,event_type,"
            "payload_json,payload_digest,status,attempts,created_at,version) VALUES ("
            f"'{event_id}','GENERATION','{event_id}','PLAN_GENERATION_REQUESTED',"
            "'{}'::jsonb,decode(repeat('4',32),'hex'),'PENDING',0,now(),1)"
        )
        return event_id

    try:
        event_id = await insert_pending()
        assert await _claim_one() == str(event_id)
        assert await _consume(event_id) == "DELIVERED"
        assert await _consume(event_id) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.health_plan_delivery "
            "WHERE event_id=$1 AND delivery_id=$2",
            event_id,
            _delivery_id(event_id),
        )
        assert await pg_database._fetch_value(
            "SELECT status='DELIVERED' AND attempts=1 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NOT NULL "
            "FROM public.health_plan_outbox WHERE event_id=$1",
            event_id,
        )

        stale_event = await insert_pending()
        assert await _claim_one() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.health_plan_outbox SET lease_until=now()-interval '1 second' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await _recover() == 1
        assert await pg_database._fetch_value(
            "SELECT status='FAILED' AND attempts=1 AND lease_owner IS NULL "
            "FROM public.health_plan_outbox WHERE event_id=$1",
            stale_event,
        )
        assert await _claim_one() == str(stale_event)
        assert await _consume(stale_event) == "DELIVERED"

        worker_role = os.environ["KG_TEST_SLICE6_WORKFLOW_WORKER_ROLE"]
        for table in ("health_plan_outbox", "health_plan_delivery"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not await pg_database._fetch_value(
                    f"SELECT has_table_privilege('{worker_role}','public.{table}','{privilege}')"
                )

        if os.getenv("KG_TEST_SLICE6_REAL_RABBIT") == "1":
            from app.tasks.celery_app import SLICE6_HEALTH_PLAN_QUEUE, celery_app

            broker_event = await insert_pending()
            celery_app.send_task(DISPATCH_TASK, queue=SLICE6_HEALTH_PLAN_QUEUE)
            delivered = False
            for _ in range(120):
                delivered = bool(
                    await pg_database._fetch_value(
                        "SELECT status='DELIVERED' FROM public.health_plan_outbox "
                        "WHERE event_id=$1",
                        broker_event,
                    )
                )
                if delivered:
                    break
                await __import__("asyncio").sleep(0.25)
            assert delivered
    finally:
        if event_ids:
            event_list = ",".join(f"'{event_id}'" for event_id in event_ids)
            await pg_database._execute(
                f"DELETE FROM public.health_plan_delivery WHERE event_id IN ({event_list})"
            )
            await pg_database._execute(
                f"DELETE FROM public.health_plan_outbox WHERE event_id IN ({event_list})"
            )
        await dispose_slice6_runtime("workflow_worker")
