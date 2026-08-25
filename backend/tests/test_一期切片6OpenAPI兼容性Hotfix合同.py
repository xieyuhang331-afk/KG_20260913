from __future__ import annotations

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.modules.health_plan.api import routers
from app.modules.health_plan import api as health_plan_api
from app.modules.health_plan.schemas import (
    PlanReviewDetailDTO,
    PlanReviewListItemDTO,
    PlanReviewPageDTO,
    ReviewDecisionRequest,
)
from app.modules.health_plan.service import (
    HealthPlanError,
    decode_review_cursor,
    encode_review_cursor,
    review_detail_dto,
    review_list_item,
    review_plan,
)


NOW = datetime(2026, 8, 25, 9, 30, tzinfo=timezone.utc)
REVIEW_ID = UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d41")
REQUEST_ID = UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d42")
PLAN_ID = UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d43")
CASE_ID = UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d44")


def _app() -> FastAPI:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    return app


def _review(**changes):
    return {
        "review_id": REVIEW_ID,
        "request_id": REQUEST_ID,
        "plan_id": PLAN_ID,
        "service_case_id": CASE_ID,
        "status": "CLAIMED",
        "plan_version_no": 2,
        "claimed_at": NOW,
        "decided_at": None,
        "version": 3,
        **changes,
    }


def _plan(**changes):
    return {
        "plan_id": PLAN_ID,
        "service_case_id": CASE_ID,
        "subject_member_id": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d45"),
        "tenant_id": 71,
        "version_no": 2,
        "status": "IN_REVIEW",
        "template_code": "CN_SECONDARY_PLAN_V1",
        "template_version": 4,
        "overall_risk_level": "ATTENTION",
        "created_at": NOW,
        "updated_at": NOW,
        "version": 5,
        "module_summaries": [
            {"module_code": "GLUCOSE_METABOLISM", "risk_level": "ATTENTION"}
        ],
        "goals": ["GLUCOSE_MONITORING"],
        "stages": ["BASELINE"],
        "milestones": ["M1"],
        "sop_items": ["SOP_GLUCOSE"],
        "contraindication_codes": ["NO_MEDICATION_CHANGE"],
        "user_message_codes": ["FOLLOW_APPROVED_PLAN"],
        "therapist_action_codes": ["EXPLAIN_APPROVED_PLAN"],
        "review_summary": {
            "status": "CLAIMED",
            "decision_codes": [],
            "decided_at": None,
        },
        "user_decision_summary": {"decision": None, "decided_at": None},
        "explanations": [],
        **changes,
    }


def _assessment_context(**changes):
    return {
        "overall_risk": "ATTENTION",
        "input_evidence": {
            "profile_revision_ref": UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d47"),
            "watermark_status": "CURRENT",
        },
        "module_results": [
            {"module_code": "GLUCOSE_METABOLISM", "risk_level": "ATTENTION"}
        ],
        **changes,
    }


def test_审核列表与详情使用不同严格DTO并分离三类状态和两个版本语义() -> None:
    schemas = _app().openapi()["components"]["schemas"]
    list_operation = _app().openapi()["paths"]["/api/v1/platform/health-plan-reviews"]["get"]
    detail_operation = _app().openapi()["paths"]["/api/v1/platform/health-plan-reviews/{review_id}"]["get"]

    assert list_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PlanReviewPageDTO"
    )
    assert detail_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/PlanReviewDetailDTO"
    )
    assert list_operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    for action in ("claim", "decision"):
        mutation = _app().openapi()["paths"][
            f"/api/v1/platform/health-plan-reviews/{{review_id}}/{action}"
        ]["post"]
        assert mutation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/PlanReviewDetailDTO"
        )
        assert mutation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    assert schemas["PlanReviewPageDTO"]["properties"]["items"]["items"]["$ref"].endswith(
        "/PlanReviewListItemDTO"
    )
    assert schemas["PlanReviewListItemDTO"]["additionalProperties"] is False
    assert schemas["PlanReviewDetailDTO"]["additionalProperties"] is False
    assert "plan_version_no" in schemas["PlanReviewListItemDTO"]["properties"]
    assert "version" in schemas["PlanReviewListItemDTO"]["properties"]
    assert "status" in schemas["PlanReviewListItemDTO"]["properties"]
    assert "plan_status" in schemas["ReviewPlanSummaryDTO"]["properties"]
    assert "user_decision" in schemas["PlanReviewDetailDTO"]["properties"]

    with pytest.raises(ValidationError):
        PlanReviewListItemDTO.model_validate({**review_list_item(_review(), _plan()).model_dump(), "history": []})
    with pytest.raises(ValidationError):
        PlanReviewPageDTO.model_validate(
            {"items": [{**review_list_item(_review(), _plan()).model_dump(), "plan_status": "IN_REVIEW"}]}
        )


def test_审核列表支持ReviewStatus筛选且游标是字符串而不是裸UUID() -> None:
    operation = _app().openapi()["paths"]["/api/v1/platform/health-plan-reviews"]["get"]
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert set(parameters) == {"limit", "cursor", "status"}
    assert parameters["cursor"]["schema"].get("format") != "uuid"
    assert set(parameters["status"]["schema"]["anyOf"][0]["enum"]) == {
        "PENDING",
        "CLAIMED",
        "APPROVED",
        "NEEDS_CORRECTION",
        "REJECTED",
    }


def test_医学专家正式角色保持expert且不新增health_expert() -> None:
    source = inspect.getsource(health_plan_api)
    assert "health_expert" not in source
    assert source.count('_require(actor, {"expert"})') == 4


def test_审核游标签名绑定筛选且任何篡改都fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.health_plan.service.get_settings",
        lambda: SimpleNamespace(jwt_secret_key="slice6-cursor-test-key-with-32-bytes"),
    )
    cursor = encode_review_cursor(REVIEW_ID, "CLAIMED")
    assert str(REVIEW_ID) not in cursor
    assert decode_review_cursor(cursor, "CLAIMED") == REVIEW_ID

    with pytest.raises(HealthPlanError, match="INVALID_REQUEST"):
        decode_review_cursor(cursor, "PENDING")
    payload, signature = cursor.split(".")
    mutations = (
        payload[: len(payload) // 2]
        + ("A" if payload[len(payload) // 2] != "A" else "B")
        + payload[len(payload) // 2 + 1 :]
        + "."
        + signature,
        payload
        + "."
        + signature[: len(signature) // 2]
        + ("A" if signature[len(signature) // 2] != "A" else "B")
        + signature[len(signature) // 2 + 1 :],
        cursor[:-3],
        cursor + ".extra",
    )
    for tampered in mutations:
        with pytest.raises(HealthPlanError, match="INVALID_REQUEST"):
            decode_review_cursor(tampered, "CLAIMED")
    with pytest.raises(HealthPlanError, match="INVALID_REQUEST"):
        decode_review_cursor(str(REVIEW_ID), "CLAIMED")


def test_详情只聚合结构化安全代码并生成确定版本差异和审核历史() -> None:
    previous = _plan(
        plan_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d46"),
        version_no=1,
        status="SUPERSEDED",
        goals=["OLDER_GOAL"],
        updated_at=NOW,
        review_summary={
            "status": "NEEDS_CORRECTION",
            "decision_codes": ["TEMPLATE_REAPPLY"],
            "decided_at": NOW,
        },
    )
    prior_review = _review(
        review_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d48"),
        plan_id=previous["plan_id"],
        plan_version_no=1,
        status="NEEDS_CORRECTION",
        decided_at=NOW,
    )
    detail = review_detail_dto(
        _review(),
        _plan(),
        previous,
        confirmed_review=_review(),
        confirmed_plan=_plan(),
        assessment_context=_assessment_context(),
        plan_history=(previous, _plan()),
        review_history=(prior_review, _review()),
    )
    assert isinstance(detail, PlanReviewDetailDTO)
    assert detail.plan_summary.plan_status == "IN_REVIEW"
    assert detail.status == "CLAIMED"
    assert detail.plan_version_no == 2
    assert detail.version == 3
    assert detail.user_decision is None
    assert detail.customer_summary_codes == (
        "ASSESSMENT_INPUT_CURRENT",
        "PROFILE_CONTEXT_INCLUDED",
    )
    assert detail.assessment_summary_codes == (
        "OVERALL_RISK_ATTENTION",
        "GLUCOSE_METABOLISM_ATTENTION",
    )
    assert detail.version_diff_codes == ("GOALS_CHANGED",)
    assert tuple(item.action for item in detail.history) == (
        "PLAN_GENERATED",
        "REVIEW_CLAIMED",
        "REVIEW_DECIDED",
        "PLAN_GENERATED",
        "REVIEW_CLAIMED",
    )
    assert tuple(item.plan_version_no for item in detail.history) == (1, 1, 1, 2, 2)

    serialized = detail.model_dump_json().lower()
    for forbidden in (
        "subject_member_id",
        "tenant_id",
        "phone",
        "id_card",
        "token",
        "password",
        "credential",
        "raw_fact",
        "diagnosis",
    ):
        assert forbidden not in serialized


def test_审核详情聚合对任一审核后像漂移都fail_closed() -> None:
    previous = _plan(
        plan_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d46"),
        version_no=1,
        status="SUPERSEDED",
        review_summary={
            "status": "NEEDS_CORRECTION",
            "decision_codes": ["TEMPLATE_REAPPLY"],
            "decided_at": NOW,
        },
    )
    prior_review = _review(
        review_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8a8d48"),
        plan_id=previous["plan_id"],
        plan_version_no=1,
        status="NEEDS_CORRECTION",
        decided_at=NOW,
    )
    current_review = _review()
    current_plan = _plan()
    base = {
        "assessment_context": _assessment_context(),
        "plan_history": (previous, current_plan),
        "review_history": (prior_review, current_review),
    }

    with pytest.raises(HealthPlanError, match="DEPENDENCY_UNAVAILABLE"):
        review_detail_dto(
            current_review,
            current_plan,
            previous,
            confirmed_review=_review(version=4),
            confirmed_plan=current_plan,
            **base,
        )

    drifted_plan = _plan(
        status="USER_DECISION_PENDING",
        review_summary={
            "status": "APPROVED",
            "decision_codes": ["CONTENT_APPROVED"],
            "decided_at": NOW,
        },
    )
    with pytest.raises(HealthPlanError, match="DEPENDENCY_UNAVAILABLE"):
        review_detail_dto(
            current_review,
            drifted_plan,
            previous,
            confirmed_review=current_review,
            confirmed_plan=drifted_plan,
            **{**base, "plan_history": (previous, drifted_plan)},
        )

    with pytest.raises(HealthPlanError, match="DEPENDENCY_UNAVAILABLE"):
        review_detail_dto(
            current_review,
            current_plan,
            previous,
            confirmed_review=current_review,
            confirmed_plan=current_plan,
            **{
                **base,
                "review_history": (
                    prior_review,
                    _review(status="APPROVED", decided_at=NOW, version=4),
                ),
            },
        )

    invalid_reason_plan = _plan(
        review_summary={
            "status": "CLAIMED",
            "decision_codes": ["CONTENT_APPROVED"],
            "decided_at": None,
        }
    )
    with pytest.raises(HealthPlanError, match="DEPENDENCY_UNAVAILABLE"):
        review_detail_dto(
            current_review,
            invalid_reason_plan,
            previous,
            confirmed_review=current_review,
            confirmed_plan=invalid_reason_plan,
            **{**base, "plan_history": (previous, invalid_reason_plan)},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "reason_codes"),
    [
        ("APPROVED", ("TEMPLATE_REAPPLY",)),
        ("NEEDS_CORRECTION", ("CONTENT_APPROVED",)),
        ("REJECTED", ("CONTENT_APPROVED",)),
        ("REJECTED", ("UNKNOWN_REJECTED",)),
        ("NEEDS_CORRECTION", ("ANY_REQUIRES_CORRECTION",)),
        ("APPROVED", ()),
        ("REJECTED", ("MEDICAL_SAFETY_CONFLICT", "MEDICAL_SAFETY_CONFLICT")),
    ],
)
async def test_审核决策与原因码不匹配在Repository调用前拒绝(decision, reason_codes) -> None:
    class Repository:
        called = False

        async def mutation_replay(self, *args):
            self.called = True

    repository = Repository()
    with pytest.raises(HealthPlanError, match="INVALID_REQUEST"):
        await review_plan(
            repository,
            review_id=REVIEW_ID,
            decision=decision,
            reason_codes=reason_codes,
            expected_version=1,
            actor_user_id=7,
            actor_role="expert",
            idempotency_key="mismatch",
            now=NOW,
            id_factory=lambda: REVIEW_ID,
        )
    assert repository.called is False


@pytest.mark.parametrize(
    ("decision", "reason_codes"),
    [
        ("APPROVED", ("CONTENT_APPROVED",)),
        ("NEEDS_CORRECTION", ("TEMPLATE_REAPPLY",)),
        ("NEEDS_CORRECTION", ("DATA_CONTEXT_RECHECK",)),
        ("NEEDS_CORRECTION", ("TEMPLATE_REAPPLY", "DATA_CONTEXT_RECHECK")),
        ("REJECTED", ("MEDICAL_SAFETY_CONFLICT",)),
        ("REJECTED", ("TEMPLATE_SCOPE_UNSUITABLE",)),
        ("REJECTED", ("MEDICAL_SAFETY_CONFLICT", "TEMPLATE_SCOPE_UNSUITABLE")),
    ],
)
def test_审核请求接受与决策匹配的结构化原因码(decision, reason_codes) -> None:
    assert ReviewDecisionRequest(
        decision=decision,
        reason_codes=reason_codes,
        expected_version=1,
    ).reason_codes == reason_codes
