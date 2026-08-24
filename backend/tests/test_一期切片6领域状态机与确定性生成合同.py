from __future__ import annotations

from dataclasses import asdict
from types import MappingProxyType
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.modules.health_plan.domain import (
    EligibilityFacts,
    GenerationState,
    ReviewDecision,
    UserDecision,
    deterministic_plan_content,
    eligibility_result,
    next_generation_state,
)
from app.modules.health_plan.service import (
    HealthPlanError,
    execute_generation,
    get_generation_eligibility,
    request_plan_generation,
)


def _eligible(**overrides):
    values = {
        "tenant_service_ready": True,
        "service_case_current": True,
        "consent_current": True,
        "primary_therapist_current": True,
        "assembly_ready": True,
        "assessment_completed": True,
        "assessment_disputed": False,
        "assessment_superseded": False,
        "high_risk_blocking": False,
        "published_template_available": True,
        "active_generation_exists": False,
        "active_plan_conflict": False,
    }
    values.update(overrides)
    return EligibilityFacts(**values)


def test_资格完全由权威事实计算且拒绝原因稳定有序():
    result = eligibility_result(
        _eligible(
            tenant_service_ready=False,
            consent_current=False,
            high_risk_blocking=True,
            published_template_available=False,
        )
    )

    assert result.eligible is False
    assert result.blocking_codes == (
        "TENANT_NOT_SERVICE_READY",
        "CONSENT_NOT_CURRENT",
        "HIGH_RISK_BLOCKING",
        "TEMPLATE_NOT_AVAILABLE",
    )
    assert eligibility_result(_eligible()).eligible is True


def test_方案状态机拒绝越级并保留补正和解释分支():
    assert next_generation_state(GenerationState.REQUESTED, GenerationState.GENERATING) is GenerationState.GENERATING
    assert next_generation_state(GenerationState.GENERATING, GenerationState.IN_REVIEW) is GenerationState.IN_REVIEW
    assert next_generation_state(GenerationState.IN_REVIEW, ReviewDecision.NEEDS_CORRECTION) is GenerationState.NEEDS_CORRECTION
    assert next_generation_state(GenerationState.IN_REVIEW, ReviewDecision.APPROVED) is GenerationState.USER_DECISION_PENDING
    assert next_generation_state(GenerationState.USER_DECISION_PENDING, UserDecision.NEEDS_EXPLANATION) is GenerationState.NEEDS_EXPLANATION
    assert next_generation_state(GenerationState.USER_DECISION_PENDING, UserDecision.ACCEPT) is GenerationState.ACTIVE
    assert next_generation_state(GenerationState.USER_DECISION_PENDING, UserDecision.DECLINE) is GenerationState.DECLINED

    with pytest.raises(ValueError, match="STATE_CONFLICT"):
        next_generation_state(GenerationState.REQUESTED, GenerationState.ACTIVE)


def test_确定性生成只填充模板允许字段并冻结医学内容():
    template = MappingProxyType(
        {
            "template_code": "CN_BASE_PLAN_V1",
            "template_version": 1,
            "applicable_modules": (
                "BLOOD_PRESSURE_CARDIOVASCULAR",
                "GLUCOSE_METABOLISM",
            ),
            "goals_by_module": {
                "BLOOD_PRESSURE_CARDIOVASCULAR": ("BP_GOAL_MONITOR",),
                "GLUCOSE_METABOLISM": ("GLUCOSE_GOAL_MONITOR",),
            },
            "stage_codes": ("BASELINE", "FOLLOW_UP"),
            "milestone_codes": ("M1", "M2"),
            "sop_codes": ("SOP_BP", "SOP_GLUCOSE"),
            "contraindication_codes": ("NO_MEDICATION_CHANGE",),
            "user_message_codes": ("FOLLOW_APPROVED_PLAN",),
            "therapist_action_codes": ("EXPLAIN_APPROVED_PLAN",),
        }
    )
    module_results = MappingProxyType(
        {
            "BLOOD_PRESSURE_CARDIOVASCULAR": "ATTENTION",
            "GLUCOSE_METABOLISM": "WITHIN_RANGE",
            "LIPID_METABOLISM": "NOT_ASSESSED",
            "WEIGHT_ABDOMINAL_OBESITY": "NOT_ASSESSED",
        }
    )

    first = deterministic_plan_content(template, module_results)
    second = deterministic_plan_content(template, module_results)

    assert first == second
    assert first["goals"] == ("BP_GOAL_MONITOR", "GLUCOSE_GOAL_MONITOR")
    assert first["module_summaries"] == (
        {"module_code": "BLOOD_PRESSURE_CARDIOVASCULAR", "risk_level": "ATTENTION"},
        {"module_code": "GLUCOSE_METABOLISM", "risk_level": "WITHIN_RANGE"},
    )
    assert first["stages"] == ("BASELINE", "FOLLOW_UP")
    assert "price" not in first
    assert "diagnosis" not in first
    assert "free_text" not in first
    with pytest.raises(TypeError):
        first["goals"] = ()


class _Repository:
    def __init__(self, authority, replay=None):
        self.authority = authority
        self.replay = replay
        self.created = []

    async def generation_authority(self, service_case_id, actor_user_id, actor_role):
        return self.authority

    async def generation_replay(self, actor_user_id, idempotency_key, request_digest):
        return self.replay

    async def create_generation_request(self, payload):
        self.created.append(payload)
        return payload["response"]


def _authority(**overrides):
    values = {
        **asdict(_eligible()),
        "service_case_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d31"),
        "subject_member_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d32"),
        "tenant_id": 7,
        "service_case_version": 3,
        "assessment_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d33"),
        "assessment_version": 2,
        "assembly_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d34"),
        "template_version_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d35"),
        "template_version": 1,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_服务端重新计算资格且拒绝时零创建():
    repository = _Repository(_authority(high_risk_blocking=True))
    result = await get_generation_eligibility(
        repository,
        service_case_id=repository.authority["service_case_id"],
        actor_user_id=11,
        actor_role="org_admin",
        evaluated_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert result.eligible is False
    assert result.blocking_codes == ("HIGH_RISK_BLOCKING",)

    with pytest.raises(HealthPlanError, match="HIGH_RISK_BLOCKING"):
        await request_plan_generation(
            repository,
            service_case_id=repository.authority["service_case_id"],
            expected_service_case_version=3,
            actor_user_id=11,
            actor_role="org_admin",
            idempotency_key="slice6-key-1",
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
            id_factory=iter(
                UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{value}")
                for value in (36, 37, 38, 39)
            ).__next__,
        )
    assert repository.created == []


@pytest.mark.asyncio
async def test_生成请求只绑定权威快照并写入唯一请求审计和Outbox():
    repository = _Repository(_authority())
    response = await request_plan_generation(
        repository,
        service_case_id=repository.authority["service_case_id"],
        expected_service_case_version=3,
        actor_user_id=11,
        actor_role="org_operator",
        idempotency_key="slice6-key-2",
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        id_factory=iter(
            UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{value}")
            for value in (36, 37, 38, 39)
        ).__next__,
    )
    assert response["status"] == "REQUESTED"
    assert len(repository.created) == 1
    payload = repository.created[0]
    assert payload["assessment_id"] == repository.authority["assessment_id"]
    assert payload["assembly_id"] == repository.authority["assembly_id"]
    assert payload["template_version_id"] == repository.authority["template_version_id"]
    assert payload["expected_service_case_version"] == 3
    assert payload["request_digest"]
    assert payload["audit_id"] != payload["event_id"] != payload["receipt_id"]


class _CorrectionRepository:
    def __init__(self):
        self.completed = []

    async def claim_generation(self, request_id, lease_owner):
        return {"request_id": request_id, "status": "GENERATING"}

    async def generation_input(self, request_id):
        return {
            "request_id": request_id,
            "next_version_no": 2,
            "supersedes_plan_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d40"),
            "template": {
                "template_code": "CN_BASE_PLAN_V1",
                "template_version": 1,
                "applicable_modules": ("BLOOD_PRESSURE_CARDIOVASCULAR",),
                "goals_by_module": {
                    "BLOOD_PRESSURE_CARDIOVASCULAR": ("BP_GOAL_MONITOR",),
                },
                "stage_codes": ("BASELINE",),
                "milestone_codes": ("M1",),
                "sop_codes": ("SOP_BP",),
                "contraindication_codes": ("NO_MEDICATION_CHANGE",),
                "user_message_codes": ("FOLLOW_APPROVED_PLAN",),
                "therapist_action_codes": ("EXPLAIN_APPROVED_PLAN",),
            },
            "module_results": {
                "BLOOD_PRESSURE_CARDIOVASCULAR": "ATTENTION",
            },
        }

    async def complete_generation(self, payload):
        self.completed.append(payload)
        return payload["response"]


@pytest.mark.asyncio
async def test_补正后确定性再生成使用下一版本并绑定被替代方案():
    repository = _CorrectionRepository()
    result = await execute_generation(
        repository,
        request_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d41"),
        lease_owner="slice6-worker",
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        id_factory=iter(
            UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{value}")
            for value in (42, 43, 44, 45)
        ).__next__,
    )

    assert result["current_plan_version"] == 2
    assert repository.completed[0]["version_no"] == 2
    assert repository.completed[0]["supersedes_plan_id"] == UUID(
        "018f0f47-e4a8-7cc8-98f2-88d31f8a8d40"
    )
