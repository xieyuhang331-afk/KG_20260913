from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.health_plan.schemas import (
    CreatePlanGenerationRequest,
    HealthPlanTemplateCreateRequest,
    PlanGenerationEligibilityDTO,
    ReviewDecisionRequest,
    UserDecisionRequest,
)
from app.modules.health_plan.service import govern_template


UUID7 = UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d31")


def _template_payload():
    return {
        "template_code": "CN_BASE_PLAN_V1",
        "applicable_modules": ["BLOOD_PRESSURE_CARDIOVASCULAR"],
        "goals_by_module": {"BLOOD_PRESSURE_CARDIOVASCULAR": ["BP_GOAL_MONITOR"]},
        "stage_codes": ["BASELINE"],
        "milestone_codes": ["M1"],
        "sop_codes": ["SOP_BP"],
        "contraindication_codes": ["NO_MEDICATION_CHANGE"],
        "user_message_codes": ["FOLLOW_APPROVED_PLAN"],
        "therapist_action_codes": ["EXPLAIN_APPROVED_PLAN"],
        "medical_approval_ref": "MEDICAL-SIGNOFF-2026-08-24",
    }


def test_模板只接受结构化医学锁定字段并拒绝商业和自由文本():
    model = HealthPlanTemplateCreateRequest.model_validate(_template_payload())
    assert model.template_code == "CN_BASE_PLAN_V1"

    for forbidden in ("price", "bom", "diagnosis", "free_text", "llm_prompt", "rule_codes"):
        payload = _template_payload()
        payload[forbidden] = "forbidden"
        with pytest.raises(ValidationError):
            HealthPlanTemplateCreateRequest.model_validate(payload)

    for field in (
        "stage_codes",
        "milestone_codes",
        "sop_codes",
        "contraindication_codes",
        "user_message_codes",
        "therapist_action_codes",
    ):
        payload = _template_payload()
        payload[field] = ["free text is not a governed code"]
        with pytest.raises(ValidationError):
            HealthPlanTemplateCreateRequest.model_validate(payload)

    payload = _template_payload()
    payload["goals_by_module"] = {
        "BLOOD_PRESSURE_CARDIOVASCULAR": ["free text goal"]
    }
    with pytest.raises(ValidationError):
        HealthPlanTemplateCreateRequest.model_validate(payload)


def test_客户端不能提交资格风险或内部generation事实():
    assert CreatePlanGenerationRequest.model_validate({"expected_service_case_version": 1}).expected_service_case_version == 1
    for forbidden in ("eligible", "overall_risk", "assessment_id", "template_version_id", "generation_id"):
        with pytest.raises(ValidationError):
            CreatePlanGenerationRequest.model_validate(
                {"expected_service_case_version": 1, forbidden: str(UUID7)}
            )


def test_资格DTO与审核用户决定枚举冻结():
    dto = PlanGenerationEligibilityDTO(
        service_case_id=UUID7,
        eligible=False,
        blocking_codes=("HIGH_RISK_BLOCKING",),
        current_assessment_id=UUID7,
        assessment_version=2,
        published_template_version_id=UUID7,
        active_generation_request_id=None,
        active_plan_id=None,
        expected_service_case_version=3,
        evaluated_at=datetime.now(timezone.utc),
    )
    assert dto.blocking_codes == ("HIGH_RISK_BLOCKING",)
    assert ReviewDecisionRequest(decision="NEEDS_CORRECTION", reason_codes=("TEMPLATE_REAPPLY",), expected_version=1).decision == "NEEDS_CORRECTION"
    assert UserDecisionRequest(decision="NEEDS_EXPLANATION", expected_version=1).decision == "NEEDS_EXPLANATION"

    with pytest.raises(ValidationError):
        ReviewDecisionRequest(decision="EDIT", reason_codes=(), expected_version=1)
    with pytest.raises(ValidationError):
        UserDecisionRequest(decision="BUY", expected_version=1)


class _TemplateRepository:
    def __init__(self, replay=None):
        self.replay = replay
        self.payloads = []

    async def mutation_replay(self, actor_user_id, operation, idempotency_key, request_digest):
        return self.replay

    async def next_template_version(self, template_code):
        return 1

    async def govern_template(self, operation, payload):
        self.payloads.append((operation, payload))
        return payload["response"]


@pytest.mark.asyncio
async def test_模板治理写前冻结后像并生成唯一审计_Outbox_Receipt():
    repository = _TemplateRepository()
    ids = iter(
        UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{suffix}")
        for suffix in (32, 33, 34)
    )
    response = await govern_template(
        repository,
        operation="CREATE",
        template_version_id=UUID7,
        template_payload=HealthPlanTemplateCreateRequest.model_validate(
            _template_payload()
        ).model_dump(),
        expected_version=None,
        actor_user_id=7,
        actor_role="expert",
        idempotency_key="slice6-template-create-1",
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        id_factory=ids.__next__,
    )

    assert response["status"] == "DRAFT"
    operation, payload = repository.payloads[0]
    assert operation == "CREATE"
    assert payload["response"] == response
    assert payload["request_digest"]
    assert payload["postimage_digest"]
    assert len({payload["audit_id"], payload["event_id"], payload["receipt_id"]}) == 3

    replay_repository = _TemplateRepository(replay=response)
    replay = await govern_template(
        replay_repository,
        operation="CREATE",
        template_version_id=UUID7,
        template_payload=HealthPlanTemplateCreateRequest.model_validate(
            _template_payload()
        ).model_dump(),
        expected_version=None,
        actor_user_id=7,
        actor_role="expert",
        idempotency_key="slice6-template-create-1",
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        id_factory=lambda: UUID7,
    )
    assert replay == response
    assert replay_repository.payloads == []


@pytest.mark.asyncio
async def test_模板创建请求摘要不依赖服务端生成的模板UUID():
    first = _TemplateRepository()
    second = _TemplateRepository()
    common = {
        "operation": "CREATE",
        "template_payload": HealthPlanTemplateCreateRequest.model_validate(
            _template_payload()
        ).model_dump(),
        "expected_version": None,
        "actor_user_id": 7,
        "actor_role": "expert",
        "idempotency_key": "slice6-template-create-stable",
        "now": datetime(2026, 8, 24, tzinfo=timezone.utc),
    }
    await govern_template(
        first,
        template_version_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d35"),
        id_factory=iter(
            UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{suffix}")
            for suffix in (36, 37, 38)
        ).__next__,
        **common,
    )
    await govern_template(
        second,
        template_version_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d39"),
        id_factory=iter(
            UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{suffix}")
            for suffix in (40, 41, 42)
        ).__next__,
        **common,
    )
    assert first.payloads[0][1]["request_digest"] == second.payloads[0][1]["request_digest"]
