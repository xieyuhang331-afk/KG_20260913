from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.modules.health_assessment.domain import HighRiskTaskState, next_high_risk_task_state
from app.modules.health_assessment.service import (
    build_completion_mutation_plan,
    ordinary_plan_authority,
)


MIGRATION_SOURCE = (
    __import__("pathlib").Path(__file__).parents[1]
    / "app"
    / "migrations"
    / "versions"
    / "20260825_0030_phase1_slice5_deterministic_assessment_high_risk.py"
).read_text(encoding="utf-8")


ASSESSMENT_ID = UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15220")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("OPEN", "CLAIMED"),
        ("OPEN", "REFERRED"),
        ("OPEN", "RESOLVED"),
        ("CLAIMED", "ESCALATED"),
        ("CLAIMED", "REFERRED"),
        ("CLAIMED", "RESOLVED"),
        ("ESCALATED", "REFERRED"),
        ("ESCALATED", "RESOLVED"),
    ],
)
def test_高风险任务只允许医学批准转换(current, target):
    assert next_high_risk_task_state(HighRiskTaskState(current), HighRiskTaskState(target)).value == target


def test_HIGH_RISK完成计划原子包含唯一机构任务与伴随写入():
    plan = build_completion_mutation_plan(
        assessment_id=ASSESSMENT_ID,
        module_risks={
            "BLOOD_PRESSURE_CARDIOVASCULAR": "HIGH_RISK",
            "GLUCOSE_METABOLISM": "WITHIN_RANGE",
            "LIPID_METABOLISM": "NOT_ASSESSED",
            "WEIGHT_ABDOMINAL_OBESITY": "ATTENTION",
        },
        trigger_codes=("HR-BP-SEVERE",),
        completed_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    assert plan.expected_postimage["assessment"]["overall_risk"] == "HIGH_RISK"
    assert len(plan.expected_postimage["module_results"]) == 4
    assert plan.expected_postimage["high_risk_task"]["status"] == "OPEN"
    assert plan.expected_postimage["high_risk_task"]["due_at"] == plan.expected_postimage["high_risk_task"]["created_at"]
    assert plan.expected_postimage["audit"]["event"] == "ASSESSMENT_COMPLETED"
    assert plan.expected_postimage["outbox"]["event"] == "ASSESSMENT_COMPLETED"
    assert plan.expected_postimage["receipt"]["aggregate_id"] == str(ASSESSMENT_ID)


def test_普通方案只有新的current非高风险完成评估才能解阻():
    assert ordinary_plan_authority(
        current_assessment_status="COMPLETED",
        current_overall_risk="ATTENTION",
        has_open_high_risk_task=False,
        all_modules_not_assessed=False,
    )
    assert not ordinary_plan_authority(
        current_assessment_status="COMPLETED",
        current_overall_risk="HIGH_RISK",
        has_open_high_risk_task=False,
        all_modules_not_assessed=False,
    )
    assert not ordinary_plan_authority(
        current_assessment_status="COMPLETED",
        current_overall_risk="ATTENTION",
        has_open_high_risk_task=True,
        all_modules_not_assessed=False,
    )
    assert not ordinary_plan_authority(
        current_assessment_status="COMPLETED",
        current_overall_risk="NOT_ASSESSED",
        has_open_high_risk_task=False,
        all_modules_not_assessed=True,
    )


def test_任务手工关闭不能在没有新current评估时直接解阻():
    assert not ordinary_plan_authority(
        current_assessment_status=None,
        current_overall_risk=None,
        has_open_high_risk_task=False,
        all_modules_not_assessed=False,
    )


def test_完成函数由数据库从四模块计算总体风险而不信任调用方():
    assert "computed_overall_risk" in MIGRATION_SOURCE
    assert "SLICE5_OVERALL_RISK_MISMATCH" in MIGRATION_SOURCE
    assert "count(DISTINCT x->>'module_code')=4" in MIGRATION_SOURCE


def test_任务转换同事务写入Audit_Outbox和Receipt():
    transition = MIGRATION_SOURCE.rsplit('"slice5_high_risk_transition_v1"', 1)[1]
    transition = transition.split('"slice5_rule_governance_v1"', 1)[0]
    assert "INSERT INTO public.slice5_audit" in transition
    assert "INSERT INTO public.slice5_outbox" in transition
    assert "INSERT INTO public.slice5_idempotency" in transition
    assert "IDEMPOTENCY_CONFLICT" in transition


def test_普通方案权威要求current非高风险评估晚于历史高风险评估():
    authority = MIGRATION_SOURCE.split('"slice5_ordinary_plan_authority_v1"', 2)[-1]
    assert "current_assembly_id=s.assembly_id" in authority
    assert "newer.sequence_no>older.sequence_no" in authority
    assert "NOT EXISTS(SELECT 1 FROM public.high_risk_task" in authority
