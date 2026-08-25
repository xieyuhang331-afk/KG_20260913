from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.uuid_generator import Uuid7Generator
from app.modules.service_fulfillment.repository import ServiceFulfillmentRepository
from app.modules.service_fulfillment.service import ServiceFulfillmentService
from tests.integration.test_一期切片7服务履约关闭转机构与导出数据库闭环 import (
    _FixedClock,
    _seed_active_plan,
)


def test_整改A_非连续MISSED不报风险而连续MISSED稳定报AT_RISK(
    pg_database,
    slice6_institution_writer_database,
) -> None:
    seeded = _seed_active_plan(
        pg_database,
        slice6_institution_writer_database,
        ordinal=78,
        accepted_at=datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc),
    )

    async def exercise() -> tuple[dict, dict]:
        milestone_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_MILESTONE_WRITER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        worker_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_EXPORT_WORKER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        family_engine = create_async_engine(
            os.environ["KG_TEST_SLICE7_FAMILY_READER_DATABASE_URL"],
            pool_pre_ping=True,
        )
        try:
            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                initial = await ServiceFulfillmentRepository(session).read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
            assert initial is not None
            d7 = next(item for item in initial["milestones"] if item["code"] == "D7")
            async with AsyncSession(milestone_engine, expire_on_commit=False) as session:
                service = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    _FixedClock(datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)),
                    Uuid7Generator().generate,
                )
                await service.complete_milestone(
                    milestone_id=UUID(str(d7["milestone_id"])),
                    actor_user_id=int(seeded["therapist_actor_id"]),
                    actor_role="therapist",
                    actor_tenant_id=int(seeded["tenant_id"]),
                    idempotency_key="slice7-risk-complete-d7",
                    expected_version=1,
                    record_summary={"EXECUTION_STATUS": "COMPLETED"},
                    evidence_refs=(),
                )
                await session.commit()
            async with AsyncSession(worker_engine, expire_on_commit=False) as session:
                worker = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    _FixedClock(datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)),
                    Uuid7Generator().generate,
                )
                assert len(await worker.mark_overdue("slice7-risk-worker", limit=2)) == 2
                await session.commit()
            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                nonconsecutive = await ServiceFulfillmentRepository(session).read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
            async with AsyncSession(worker_engine, expire_on_commit=False) as session:
                worker = ServiceFulfillmentService(
                    ServiceFulfillmentRepository(session),
                    _FixedClock(datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)),
                    Uuid7Generator().generate,
                )
                assert len(await worker.mark_overdue("slice7-risk-worker", limit=1)) == 1
                await session.commit()
            async with AsyncSession(family_engine, expire_on_commit=False) as session:
                consecutive = await ServiceFulfillmentRepository(session).read_one(
                    "FULFILLMENT",
                    UUID(str(seeded["case_id"])),
                    int(seeded["family_actor_id"]),
                    "member",
                )
            assert nonconsecutive is not None
            assert consecutive is not None
            return nonconsecutive, consecutive
        finally:
            await milestone_engine.dispose()
            await worker_engine.dispose()
            await family_engine.dispose()

    nonconsecutive, consecutive = asyncio.run(exercise())
    assert nonconsecutive["risk_flag"] is None
    assert consecutive["risk_flag"] == "AT_RISK"
    assert consecutive["lifecycle_status"] == "ACTIVE"
