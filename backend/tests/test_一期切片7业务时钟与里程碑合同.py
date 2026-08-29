from datetime import datetime, timedelta, timezone

import pytest

from app.modules.service_fulfillment.domain import (
    CaseLifecycleStatus,
    MilestoneCode,
    MilestoneStatus,
    SyntheticBusinessClock,
    SystemBusinessClock,
    build_milestone_windows,
    derive_case_risk,
    resume_schedule,
)


ACTIVATED_AT = datetime(2026, 8, 25, 16, 30, tzinfo=timezone.utc)


def test_D01_生产时钟拒绝合成覆盖且测试时钟显式注入() -> None:
    with pytest.raises(ValueError, match="SYNTHETIC_CLOCK_FORBIDDEN"):
        SystemBusinessClock(override=ACTIVATED_AT)

    clock = SyntheticBusinessClock(ACTIVATED_AT)
    assert clock.now() == ACTIVATED_AT
    clock.advance(timedelta(days=5))
    assert clock.now() == ACTIVATED_AT + timedelta(days=5)


def test_D02_五个里程碑严格使用上海业务日窗口() -> None:
    windows = build_milestone_windows(ACTIVATED_AT)
    assert tuple(windows) == tuple(MilestoneCode)
    assert windows[MilestoneCode.D0].start.isoformat() == "2026-08-26"
    assert windows[MilestoneCode.D0].end.isoformat() == "2026-08-26"
    assert windows[MilestoneCode.D7].start.isoformat() == "2026-08-30"
    assert windows[MilestoneCode.D7].end.isoformat() == "2026-09-03"
    assert windows[MilestoneCode.D14].start.isoformat() == "2026-09-07"
    assert windows[MilestoneCode.D14].end.isoformat() == "2026-09-11"
    assert windows[MilestoneCode.D21].start.isoformat() == "2026-09-14"
    assert windows[MilestoneCode.D21].end.isoformat() == "2026-09-18"
    assert windows[MilestoneCode.D28].start.isoformat() == "2026-09-20"
    assert windows[MilestoneCode.D28].end.isoformat() == "2026-09-26"


def test_D03_里程碑状态不混入案例风险和暂停状态() -> None:
    values = {item.value for item in MilestoneStatus}
    assert values == {
        "PENDING",
        "DUE",
        "COMPLETED",
        "MISSED",
        "INVALIDATED",
    }
    assert "AT_RISK" not in values
    assert "PAUSED" not in values
    assert CaseLifecycleStatus.PAUSED.value == "PAUSED"


def test_D07_D08_连续两个未完成节点才形成AT_RISK() -> None:
    assert derive_case_risk(
        {
            MilestoneCode.D0: MilestoneStatus.COMPLETED,
            MilestoneCode.D7: MilestoneStatus.MISSED,
            MilestoneCode.D14: MilestoneStatus.MISSED,
            MilestoneCode.D21: MilestoneStatus.PENDING,
            MilestoneCode.D28: MilestoneStatus.PENDING,
        }
    ) == "AT_RISK"
    assert (
        derive_case_risk(
            {
                MilestoneCode.D0: MilestoneStatus.MISSED,
                MilestoneCode.D7: MilestoneStatus.COMPLETED,
                MilestoneCode.D14: MilestoneStatus.MISSED,
                MilestoneCode.D21: MilestoneStatus.PENDING,
                MilestoneCode.D28: MilestoneStatus.PENDING,
            }
        )
        is None
    )


def test_D12_D13_恢复只顺延未开始窗口且不突破31天上限() -> None:
    original = build_milestone_windows(ACTIVATED_AT)
    resumed = resume_schedule(
        original,
        paused_on=original[MilestoneCode.D7].start,
        resumed_on=original[MilestoneCode.D7].start + timedelta(days=2),
        statuses={
            MilestoneCode.D0: MilestoneStatus.COMPLETED,
            MilestoneCode.D7: MilestoneStatus.DUE,
            MilestoneCode.D14: MilestoneStatus.PENDING,
            MilestoneCode.D21: MilestoneStatus.PENDING,
            MilestoneCode.D28: MilestoneStatus.PENDING,
        },
    )
    assert resumed[MilestoneCode.D0] == original[MilestoneCode.D0]
    assert resumed[MilestoneCode.D7] == original[MilestoneCode.D7]
    assert resumed[MilestoneCode.D14].start == original[MilestoneCode.D14].start + timedelta(days=2)

    with pytest.raises(ValueError, match="RESUME_REQUIRES_CLOSING"):
        resume_schedule(
            original,
            paused_on=original[MilestoneCode.D7].start,
            resumed_on=original[MilestoneCode.D7].start + timedelta(days=10),
            statuses={code: MilestoneStatus.PENDING for code in MilestoneCode},
            can_fit_within_limit=False,
        )
