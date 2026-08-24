from __future__ import annotations

import asyncio
import json
import os

import pytest

from app.core.uuid_generator import Uuid7Generator


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_MQ01_MQ07_Outbox受限函数稳定投递恢复与RabbitMQ闭环(
    pg_database,
    slice5_workflow_worker_database,
) -> None:
    from app.core.database import dispose_slice5_runtime
    from app.tasks.slice5_assessment_tasks import (
        DISPATCH_TASK,
        _claim_one,
        _consume,
        _delivery_id,
        _recover,
    )

    generated = Uuid7Generator()
    author_user_id = 997501
    rule_set_version_id = generated.generate()
    event_ids: list = []

    async def insert_pending(*, aggregate_ref=None):
        event_id = generated.generate()
        event_ids.append(event_id)
        await pg_database._execute(
            "INSERT INTO public.slice5_outbox(event_id,aggregate_type,aggregate_ref,event_type,"
            "payload_digest,payload_json,status,attempts,created_at) VALUES ("
            f"'{event_id}','RULE_SET','{aggregate_ref or rule_set_version_id}','RULE_CREATE',"
            "decode(repeat('4',32),'hex'),'{}'::jsonb,'PENDING',0,now())"
        )
        return event_id

    await pg_database._execute(
        "INSERT INTO public.\"user\"(id,phone,password_hash,role,status,created_at,updated_at) VALUES "
        f"({author_user_id},'19900997501','synthetic','expert','active',now(),now());"
        "INSERT INTO public.assessment_rule_set_version(rule_set_version_id,rule_set_code,version_no,"
        "status,typed_rule_payload,content_digest,digest_key_id,author_user_id,approval_evidence_ref,"
        "created_at,version) VALUES ("
        f"'{rule_set_version_id}','CN_ADULT_BASELINE_V1',997501,'DRAFT','{{}}'::jsonb,"
        f"decode(repeat('5',32),'hex'),'slice5-test-k1',{author_user_id},'synthetic',now(),1)"
    )
    try:
        event_id = await insert_pending()
        assert await _claim_one() == str(event_id)
        assert await _consume(event_id) == "DELIVERED"
        assert await _consume(event_id) == "DELIVERED"
        assert await pg_database._fetch_value(
            "SELECT count(*)=1 FROM public.slice5_delivery WHERE event_id=$1 AND delivery_id=$2",
            event_id,
            _delivery_id(event_id),
        )
        assert await pg_database._fetch_value(
            "SELECT status='DELIVERED' AND attempts=1 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NOT NULL "
            "FROM public.slice5_outbox WHERE event_id=$1",
            event_id,
        )

        stale_event = await insert_pending()
        assert await _claim_one() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.slice5_outbox SET lease_until=now()-interval '1 second' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await _recover() == 1
        assert await pg_database._fetch_value(
            "SELECT status='RETRY' AND attempts=1 AND lease_owner IS NULL "
            "FROM public.slice5_outbox WHERE event_id=$1",
            stale_event,
        )
        assert await _claim_one() == str(stale_event)
        await pg_database._execute(
            "UPDATE public.slice5_outbox SET attempts=3,lease_until=now()-interval '1 second' "
            f"WHERE event_id='{stale_event}'"
        )
        assert await _recover() == 1
        assert await pg_database._fetch_value(
            "SELECT status='FAILED' AND attempts=3 FROM public.slice5_outbox WHERE event_id=$1",
            stale_event,
        )
        reopened = await slice5_workflow_worker_database._fetch_value(
            "SELECT public.slice5_outbox_reopen_v1($1,$2)", stale_event, 3
        )
        if isinstance(reopened, str):
            reopened = json.loads(reopened)
        assert reopened == {"status": "RETRY", "attempts": 3}
        replayed = await slice5_workflow_worker_database._fetch_value(
            "SELECT public.slice5_outbox_reopen_v1($1,$2)", stale_event, 3
        )
        if isinstance(replayed, str):
            replayed = json.loads(replayed)
        assert replayed == reopened
        assert await _claim_one() == str(stale_event)
        assert await _consume(stale_event) == "DELIVERED"

        invalid_event = await insert_pending(aggregate_ref=generated.generate())
        assert await _claim_one() == str(invalid_event)
        with pytest.raises(Exception, match="SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN"):
            await _consume(invalid_event)
        assert not await pg_database._fetch_value(
            "SELECT EXISTS(SELECT 1 FROM public.slice5_delivery WHERE event_id=$1)",
            invalid_event,
        )

        worker_role = os.environ["KG_TEST_SLICE5_WORKFLOW_WORKER_ROLE"]
        for table in ("slice5_outbox", "slice5_delivery"):
            assert not await pg_database._fetch_value(
                f"SELECT has_table_privilege('{worker_role}','public.{table}','SELECT')"
            )
            assert not await pg_database._fetch_value(
                f"SELECT has_table_privilege('{worker_role}','public.{table}','INSERT')"
            )
            assert not await pg_database._fetch_value(
                f"SELECT has_table_privilege('{worker_role}','public.{table}','UPDATE')"
            )

        if os.getenv("KG_TEST_SLICE5_REAL_RABBIT") == "1":
            from app.tasks.celery_app import SLICE5_ASSESSMENT_QUEUE, celery_app

            broker_event = await insert_pending()
            celery_app.send_task(DISPATCH_TASK, queue=SLICE5_ASSESSMENT_QUEUE)
            delivered = False
            for _ in range(120):
                delivered = bool(
                    await pg_database._fetch_value(
                        "SELECT status='DELIVERED' FROM public.slice5_outbox WHERE event_id=$1",
                        broker_event,
                    )
                )
                if delivered:
                    break
                await asyncio.sleep(0.25)
            assert delivered
            assert await pg_database._fetch_value(
                "SELECT count(*)=1 FROM public.slice5_delivery WHERE event_id=$1",
                broker_event,
            )
    finally:
        event_list = ",".join(f"'{event_id}'" for event_id in event_ids)
        await pg_database._execute(
            f"DELETE FROM public.slice5_delivery WHERE event_id IN ({event_list})"
        )
        await pg_database._execute(
            f"DELETE FROM public.slice5_outbox WHERE event_id IN ({event_list})"
        )
        await pg_database._execute(
            "DELETE FROM public.assessment_rule_set_version "
            f"WHERE rule_set_version_id='{rule_set_version_id}'"
        )
        await pg_database._execute(
            f'DELETE FROM public."user" WHERE id={author_user_id}'
        )
        await dispose_slice5_runtime("workflow_worker")
