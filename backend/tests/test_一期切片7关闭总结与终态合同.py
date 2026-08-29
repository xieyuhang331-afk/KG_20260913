from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.service_fulfillment.domain import (
    ClosingReadiness,
    MilestoneCode,
    MilestoneStatus,
    closing_readiness,
)
from app.modules.service_fulfillment.schemas import (
    ServiceSummaryCreateRequest,
    SummaryAcknowledgementDTO,
)
from app.modules.service_fulfillment.service import (
    ServiceFulfillmentError,
    ServiceFulfillmentService,
)


UUID7 = UUID("0198f1c0-0000-7000-8000-000000000001")


def _complete():
    return {code: MilestoneStatus.COMPLETED for code in MilestoneCode}


def test_D14_D17_关闭条件由服务端完整计算且任一缺失均阻断() -> None:
    assert closing_readiness(
        _complete(),
        closing_assessment_complete=True,
        summary_complete=True,
        user_acknowledged=True,
        open_high_risk_count=1,
    ) is ClosingReadiness.BLOCKED_BY_HIGH_RISK
    assert closing_readiness(
        _complete(),
        closing_assessment_complete=True,
        summary_complete=True,
        user_acknowledged=False,
        open_high_risk_count=0,
    ) is ClosingReadiness.BLOCKED_BY_USER_ACK
    assert closing_readiness(
        _complete(),
        closing_assessment_complete=True,
        summary_complete=True,
        user_acknowledged=True,
        open_high_risk_count=0,
    ) is ClosingReadiness.READY_TO_CLOSE


def test_D18_结构化总结拒绝诊断处方和任意额外字段() -> None:
    payload = {
        "expected_version": 3,
        "assessment_id": UUID7,
        "final_retest_evidence": ("REPORT_CLEAN",),
        "milestone_outcomes": {"D28": "COMPLETED"},
        "safety_follow_up": ("FOLLOW_UP_PRIMARY_CARE",),
        "next_step": ("CONTINUE_MONITORING",),
    }
    assert ServiceSummaryCreateRequest.model_validate(payload).expected_version == 3
    with pytest.raises(ValidationError):
        ServiceSummaryCreateRequest.model_validate({**payload, "diagnosis": "forbidden"})
    with pytest.raises(ValidationError):
        ServiceSummaryCreateRequest.model_validate({**payload, "prescription": "forbidden"})


def test_D19_已阅只表达查看事实不表达疗效接受() -> None:
    dto = SummaryAcknowledgementDTO(
        acknowledgement_id=UUID7,
        summary_id=UUID("0198f1c0-0000-7000-8000-000000000002"),
        viewed_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        version=1,
    )
    assert "accepted" not in dto.model_dump()
    assert "outcome_accepted" not in dto.model_dump()


class _ClosingRepository:
    def __init__(self, authority: dict) -> None:
        self.authority_value = authority
        self.mutations: list[tuple[str, dict]] = []

    async def authority(self, *_args):
        return dict(self.authority_value)

    async def replay(self, *_args):
        return None

    async def mutate(self, operation, payload):
        self.mutations.append((operation, dict(payload)))
        return dict(payload["response"])


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 26, tzinfo=timezone.utc)


def _ids():
    value = 100
    while True:
        value += 1
        yield UUID(f"0198f1c0-0000-7000-8000-{value:012d}")


@pytest.mark.asyncio
async def test_整改A_ACTIVE状态不得提前创建closing_assessment() -> None:
    repository = _ClosingRepository(
        {
            "service_case_id": UUID7,
            "case_status": "ACTIVE",
            "milestones": [
                {"code": code.value, "status": "COMPLETED"}
                for code in MilestoneCode
            ],
            "latest_assessment_id": UUID7,
        }
    )
    values = _ids()
    service = ServiceFulfillmentService(repository, _Clock(), lambda: next(values))
    with pytest.raises(ServiceFulfillmentError, match="CASE_STATE_CONFLICT"):
        await service.case_transition(
            operation="CREATE_CLOSING_ASSESSMENT",
            service_case_id=UUID7,
            actor_user_id=7,
            actor_role="therapist",
            actor_tenant_id=3,
            idempotency_key="closing-red",
            request={
                "expected_version": 1,
                "assessment_id": UUID7,
                "final_retest_evidence": ("REPORT_CLEAN",),
            },
        )
    assert repository.mutations == []


@pytest.mark.asyncio
async def test_整改A_ACK满足全部条件时沿用总结响应且由数据库原子完成案例() -> None:
    summary_id = UUID("0198f1c0-0000-7000-8000-000000000002")
    repository = _ClosingRepository(
        {
            "service_case_id": UUID7,
            "subject_member_id": UUID("0198f1c0-0000-7000-8000-000000000003"),
            "case_status": "CLOSING",
            "service_case_version": 8,
            "summary_version": 1,
            "summary_assessment_id": UUID7,
            "summary_content": {
                "final_retest_evidence": ["REPORT_CLEAN"],
                "milestone_outcomes": {"D28": "COMPLETED"},
                "safety_follow_up": ["FOLLOW_UP_PRIMARY_CARE"],
                "next_step": ["CONTINUE_MONITORING"],
            },
            "summary_created_at": datetime(2026, 8, 25, tzinfo=timezone.utc),
            "closing_assessment_complete": True,
            "summary_complete": True,
            "summary_acknowledged": False,
            "high_risk_count": 0,
            "milestones": [
                {"code": code.value, "status": "COMPLETED"}
                for code in MilestoneCode
            ],
        }
    )
    values = _ids()
    service = ServiceFulfillmentService(repository, _Clock(), lambda: next(values))
    response = await service.case_transition(
        operation="ACK_SUMMARY",
        service_case_id=summary_id,
        actor_user_id=7,
        actor_role="member",
        actor_tenant_id=None,
        idempotency_key="ack-red",
        request={"expected_version": 1},
    )
    assert response["summary_id"] == summary_id
    assert repository.mutations[0][0] == "ACK_SUMMARY"
    assert "lifecycle_status" not in repository.mutations[0][1]["response"]
