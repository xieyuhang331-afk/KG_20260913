from datetime import date, datetime, timezone
from uuid import UUID

import pytest

from app.modules.service_fulfillment.domain import SyntheticBusinessClock
from app.modules.service_fulfillment.service import (
    ServiceFulfillmentError,
    ServiceFulfillmentService,
)


MILESTONE_ID = UUID("0198f1c0-0000-7000-8000-000000000001")


class SequentialIds:
    def __init__(self) -> None:
        self.value = 10

    def __call__(self) -> UUID:
        self.value += 1
        return UUID(f"0198f1c0-0000-7000-8000-{self.value:012d}")


class FakeRepository:
    def __init__(self) -> None:
        self.receipts = {}
        self.mutations = []
        self.authority_value = {
            "case_status": "ACTIVE",
            "service_case_id": UUID("0198f1c0-0000-7000-8000-000000000002"),
            "milestone_code": "D0",
            "window_start": date(2026, 8, 25),
            "window_end": date(2026, 8, 29),
            "milestone_version": 1,
            "therapist_current": True,
        }

    async def authority(self, *args):
        return dict(self.authority_value) if self.authority_value is not None else None

    async def replay(self, actor_user_id, operation, idempotency_key, request_digest):
        key = (actor_user_id, operation, idempotency_key)
        prior = self.receipts.get(key)
        if prior is None:
            return None
        if prior[0] != request_digest:
            raise ServiceFulfillmentError("IDEMPOTENCY_CONFLICT")
        return prior[1]

    async def mutate(self, operation, payload):
        response = {
            "milestone_id": payload.get("target_id", payload.get("milestone_id")),
            "status": "COMPLETED" if operation == "COMPLETE_MILESTONE" else operation,
            "version": 2,
        }
        key = (payload["actor_user_id"], operation, payload["idempotency_key"])
        self.receipts[key] = (payload["request_digest"], response)
        self.mutations.append(dict(payload))
        return response

    async def claim_work(self, *args):
        return []


@pytest.mark.asyncio
async def test_D05_D06_相同请求稳定重放且不同请求固定冲突() -> None:
    repository = FakeRepository()
    service = ServiceFulfillmentService(
        repository,
        SyntheticBusinessClock(datetime(2026, 8, 25, tzinfo=timezone.utc)),
        SequentialIds(),
    )
    kwargs = dict(
        milestone_id=MILESTONE_ID,
        actor_user_id=9,
        actor_role="therapist",
        actor_tenant_id=3,
        idempotency_key="0198f1c0-0000-7000-8000-000000000099",
        expected_version=1,
        record_summary={"EXECUTION_STATUS": "COMPLETED"},
        evidence_refs=(),
    )
    first = await service.complete_milestone(**kwargs)
    second = await service.complete_milestone(**kwargs)
    assert first == second
    assert len(repository.mutations) == 1

    with pytest.raises(ServiceFulfillmentError, match="IDEMPOTENCY_CONFLICT"):
        await service.complete_milestone(
            **{**kwargs, "record_summary": {"EXECUTION_STATUS": "DIFFERENT"}}
        )
    assert len(repository.mutations) == 1


@pytest.mark.asyncio
async def test_D09_D10_Currentness失败和暂停状态均零写入() -> None:
    repository = FakeRepository()
    service = ServiceFulfillmentService(
        repository,
        SyntheticBusinessClock(datetime(2026, 8, 25, tzinfo=timezone.utc)),
        SequentialIds(),
    )
    repository.authority_value = {"error_code": "CURRENTNESS_FORBIDDEN"}
    with pytest.raises(ServiceFulfillmentError, match="CURRENTNESS_FORBIDDEN"):
        await service.complete_milestone(
            milestone_id=MILESTONE_ID,
            actor_user_id=9,
            actor_role="therapist",
            actor_tenant_id=3,
            idempotency_key="key-currentness",
            expected_version=1,
            record_summary={"EXECUTION_STATUS": "COMPLETED"},
            evidence_refs=(),
        )
    repository.authority_value = {
        "case_status": "PAUSED",
        "window_start": date(2026, 8, 25),
        "window_end": date(2026, 8, 29),
    }
    with pytest.raises(ServiceFulfillmentError, match="CASE_STATE_CONFLICT"):
        await service.complete_milestone(
            milestone_id=MILESTONE_ID,
            actor_user_id=9,
            actor_role="therapist",
            actor_tenant_id=3,
            idempotency_key="key-paused",
            expected_version=1,
            record_summary={"EXECUTION_STATUS": "COMPLETED"},
            evidence_refs=(),
        )
    assert repository.mutations == []
