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
