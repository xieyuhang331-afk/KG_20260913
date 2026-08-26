from __future__ import annotations

import importlib
import os
import shutil
import time
from pathlib import Path

import pytest

from app.core.security import create_access_token
from app.core.uuid_generator import Uuid7Generator


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_D37_D40_Outbox受限投递恢复与RabbitMQ闭环(pg_database) -> None:
    from app.core.database import dispose_slice7_runtime
    from app.tasks.slice7_service_fulfillment_tasks import (
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
            "INSERT INTO public.service_fulfillment_outbox("
            "event_id,aggregate_ref,event_type,payload_json,payload_digest,status,"
            "attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ("
            f"'{event_id}','{event_id}','EXPORT_READY','{{}}'::jsonb,"
            "decode(repeat('4',32),'hex'),'PENDING',0,NULL,NULL,now(),NULL,1)"
        )
        return event_id

    try:
        event_id = await insert_pending()
        assert await _claim_one() == str(event_id)
        assert await _consume(event_id) == "DELIVERED"
        assert await _consume(event_id) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.service_fulfillment_delivery "
            "WHERE event_id=$1 AND delivery_id=$2",
            event_id,
            _delivery_id(event_id),
        )

        stale_event = await insert_pending()
        assert await _claim_one() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.service_fulfillment_outbox "
            "SET lease_until=now()-interval '1 second' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await _recover() == 1
        assert await pg_database._fetch_value(
            "SELECT status='FAILED' AND attempts=1 AND lease_owner IS NULL "
            "FROM public.service_fulfillment_outbox WHERE event_id=$1",
            stale_event,
        )
        assert await _claim_one() == str(stale_event)
        assert await _consume(stale_event) == "DELIVERED"

        worker_role = os.environ["KG_TEST_SLICE7_EXPORT_WORKER_ROLE"]
        for table in ("service_fulfillment_outbox", "service_fulfillment_delivery"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not await pg_database._fetch_value(
                    f"SELECT has_table_privilege('{worker_role}',"
                    f"'public.{table}','{privilege}')"
                )

        if os.getenv("KG_TEST_SLICE7_REAL_RABBIT") == "1":
            from app.tasks.celery_app import SLICE7_SERVICE_FULFILLMENT_QUEUE, celery_app

            broker_event = await insert_pending()
            celery_app.send_task(DISPATCH_TASK, queue=SLICE7_SERVICE_FULFILLMENT_QUEUE)
            delivered = False
            for _ in range(120):
                delivered = bool(
                    await pg_database._fetch_value(
                        "SELECT status='DELIVERED' "
                        "FROM public.service_fulfillment_outbox WHERE event_id=$1",
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
                "DELETE FROM public.service_fulfillment_delivery "
                f"WHERE event_id IN ({event_list})"
            )
            await pg_database._execute(
                "DELETE FROM public.service_fulfillment_outbox "
                f"WHERE event_id IN ({event_list})"
            )
        await dispose_slice7_runtime("export_worker")


def test_C03_真实HTTP经RabbitMQ独立Worker生成同一Export并进入READY(
    pg_database,
    real_db_client,
    slice6_institution_writer_database,
) -> None:
    if os.getenv("KG_TEST_SLICE7_REAL_RABBIT") != "1":
        pytest.skip("set KG_TEST_SLICE7_REAL_RABBIT=1 for the real worker journey")

    from app.tasks.celery_app import SLICE7_SERVICE_FULFILLMENT_QUEUE, celery_app
    from app.tasks.slice7_service_fulfillment_tasks import DISPATCH_TASK

    integration = importlib.import_module(
        "tests.integration.test_一期切片7服务履约关闭转机构与导出数据库闭环"
    )
    seeded = integration._seed_active_plan(
        pg_database,
        slice6_institution_writer_database,
        ordinal=703,
    )
    pg_database.execute("DELETE FROM public.service_fulfillment_delivery")
    pg_database.execute("DELETE FROM public.service_fulfillment_outbox")

    actor_id = int(seeded["family_actor_id"])
    token = create_access_token({"sub": str(actor_id), "role": "member"})
    created = real_db_client.post(
        "/api/v1/family/data-exports",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Step-Up-Token": token,
            "Idempotency-Key": "slice7-rabbit-export-create-0001",
        },
        json={
            "requested_scope": ["ASSESSMENT"],
            "reason": "PERSONAL_ARCHIVE",
        },
    )
    assert created.status_code == 202, created.json()
    export_id = created.json()["export_id"]
    assert pg_database.fetch_value(
        "SELECT count(*)=1 FROM public.service_fulfillment_outbox "
        f"WHERE event_type='CREATE_EXPORT' AND aggregate_ref='{export_id}' "
        f"AND payload_json->>'target_id'='{export_id}'"
    )

    try:
        celery_app.send_task(
            DISPATCH_TASK,
            queue=SLICE7_SERVICE_FULFILLMENT_QUEUE,
        )
        ready = False
        for _ in range(240):
            ready = bool(
                pg_database.fetch_value(
                    "SELECT status='READY' FROM public.personal_data_export_request "
                    f"WHERE export_id='{export_id}'"
                )
            )
            if ready:
                break
            time.sleep(0.25)
        assert ready
        assert pg_database.fetch_value(
            "SELECT status='DELIVERED' FROM public.service_fulfillment_outbox "
            f"WHERE event_type='CREATE_EXPORT' AND aggregate_ref='{export_id}'"
        )
        assert pg_database.fetch_value(
            "SELECT count(*)=1 FROM public.personal_data_export_artifact "
            f"WHERE export_id='{export_id}' AND private_file_id='{export_id}'"
        )
    finally:
        storage_root = os.getenv("KG_PRIVATE_FILE_STORAGE_ROOT")
        if storage_root:
            shutil.rmtree(Path(storage_root), ignore_errors=True)
