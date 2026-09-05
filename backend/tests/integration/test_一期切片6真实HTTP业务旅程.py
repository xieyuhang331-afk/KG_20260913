from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import UUID

from app.core.database import (
    get_slice6_clinical_reader_session,
    get_slice6_review_writer_session,
    get_slice6_template_writer_session,
)
from app.core.security import create_access_token
from app.core.uuid_generator import Uuid7Generator
from app.modules.health_plan.repository import HealthPlanRepository
from app.modules.health_plan.service import govern_template, review_list_item
from app.tasks.slice6_health_plan_tasks import _generate

from tests.integration.test_一期切片6方案生成审核确认数据库闭环 import _seed_ready_case


def _headers(
    actor_id: int, role: str, key: str | None = None, *,
    tenant_id: int | None = None, org_id: int | None = None,
    province: str | None = None, city: str | None = None,
) -> dict[str, str]:
    result = {
        "Authorization": "Bearer "
        + create_access_token({
            "sub": str(actor_id), "role": role, "tenant_id": tenant_id,
            "org_id": org_id, "province": province, "city": city,
        })
    }
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def test_API01_API10_真实ASGI完成机构发起平台审核家庭确认旅程(
    pg_database,
    real_db_client,
    slice5_clinical_reader_database,
    monkeypatch,
):
    seeded = _seed_ready_case(pg_database, ordinal=67)
    case_id = str(seeded["case_id"])
    slice5_reader = os.environ["KG_TEST_SLICE5_CLINICAL_READER_ROLE"]
    slice6_readers = tuple(
        os.environ[name]
        for name in (
            "KG_TEST_SLICE6_INSTITUTION_WRITER_ROLE",
            "KG_TEST_SLICE6_TEMPLATE_WRITER_ROLE",
            "KG_TEST_SLICE6_REVIEW_WRITER_ROLE",
            "KG_TEST_SLICE6_WORKFLOW_WORKER_ROLE",
            "KG_TEST_SLICE6_CLINICAL_READER_ROLE",
            "KG_TEST_SLICE6_FAMILY_READER_ROLE",
        )
    )
    assessment_rows = slice5_clinical_reader_database.fetch_rows(
        "SELECT assessment_id,service_case_id,status,overall_risk "
        "FROM public.slice5_assessment_read_v1 WHERE service_case_id=$1::uuid",
        UUID(case_id),
    )
    assert assessment_rows == [
        {
            "assessment_id": UUID(str(seeded["assessment_id"])),
            "service_case_id": UUID(case_id),
            "status": "COMPLETED",
            "overall_risk": "ATTENTION",
        }
    ]
    for role in (slice5_reader, *slice6_readers):
        for table in (
            "health_assessment",
            "assessment_input_snapshot",
            "assessment_module_result",
        ):
            assert not pg_database.fetch_value(
                f"SELECT has_table_privilege('{role}','public.{table}','SELECT')"
            )
    org_headers = _headers(
        int(seeded["org_actor_id"]), "org_admin", "slice6-http-generation-0001",
        tenant_id=int(seeded["tenant_id"]),
        org_id=pg_database.fetch_value(
            f"SELECT org_id FROM public.tenant WHERE id={int(seeded['tenant_id'])}"
        ),
    )
    expert_headers = _headers(
        int(seeded["expert_actor_id"]), "expert", "slice6-http-review-claim-0001"
    )
    family_headers = _headers(
        int(seeded["family_actor_id"]), "member", "slice6-http-user-decision-0001"
    )
    empty_family_list = real_db_client.get(
        f"/api/v1/family/service-cases/{case_id}/plans", headers=family_headers
    )
    assert empty_family_list.status_code == 200, empty_family_list.json()
    assert empty_family_list.json()["items"] == []
    missing_family_list = real_db_client.get(
        "/api/v1/family/service-cases/018f0f47-e4a8-7cc8-98f2-88d31f8bfffe/plans",
        headers=family_headers,
    )
    assert missing_family_list.status_code == 404

    template_payload = {
        "template_code": "CN_SECONDARY_PLAN_V1",
        "applicable_modules": ["BLOOD_PRESSURE_CARDIOVASCULAR"],
        "goals_by_module": {
            "BLOOD_PRESSURE_CARDIOVASCULAR": ["BP_GOAL_MONITOR"]
        },
        "stage_codes": ["BASELINE"],
        "milestone_codes": ["M1"],
        "sop_codes": ["SOP_BP"],
        "contraindication_codes": ["NO_MEDICATION_CHANGE"],
        "user_message_codes": ["FOLLOW_APPROVED_PLAN"],
        "therapist_action_codes": ["EXPLAIN_APPROVED_PLAN"],
        "medical_approval_ref": "MEDICAL_SIGNOFF_2026_08_24",
    }
    template_headers = _headers(
        int(seeded["expert_actor_id"]), "expert", "slice6-http-template-create-0001"
    )

    async def _direct_template_runtime_contract():
        sessions = get_slice6_template_writer_session()
        session = await anext(sessions)
        try:
            result = await govern_template(
                HealthPlanRepository(session),
                operation="CREATE",
                template_version_id=UUID("018f0f47-e4a8-7cc8-98f2-88d31f8b0090"),
                template_payload={
                    **template_payload,
                    "template_code": "CN_DIRECT_RUNTIME_V1",
                },
                expected_version=None,
                actor_user_id=int(seeded["expert_actor_id"]),
                actor_role="expert",
                idempotency_key="slice6-direct-template-create-0001",
                now=datetime.now(timezone.utc),
                id_factory=Uuid7Generator().generate,
            )
            await session.commit()
            return result
        finally:
            await sessions.aclose()

    assert asyncio.run(_direct_template_runtime_contract())["status"] == "DRAFT"
    template_created = real_db_client.post(
        "/api/v1/platform/health-plan-templates",
        headers=template_headers,
        json=template_payload,
    )
    assert template_created.status_code == 201, template_created.json()
    template_replay = real_db_client.post(
        "/api/v1/platform/health-plan-templates",
        headers=template_headers,
        json=template_payload,
    )
    assert template_replay.status_code == 201
    assert template_replay.json() == template_created.json()
    template_conflict = real_db_client.post(
        "/api/v1/platform/health-plan-templates",
        headers=template_headers,
        json={**template_payload, "medical_approval_ref": "DIFFERENT_APPROVAL"},
    )
    assert template_conflict.status_code == 409
    assert template_conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    template_id = template_created.json()["template_version_id"]
    publish_headers = _headers(
        int(seeded["expert_actor_id"]), "expert", "slice6-http-template-publish-0001"
    )
    published = real_db_client.post(
        f"/api/v1/platform/health-plan-templates/{template_id}/publish",
        headers=publish_headers,
        json={"expected_version": 1},
    )
    assert published.status_code == 200, published.json()
    assert published.json()["status"] == "PUBLISHED"
    assert real_db_client.post(
        f"/api/v1/platform/health-plan-templates/{template_id}/publish",
        headers=publish_headers,
        json={"expected_version": 1},
    ).json() == published.json()
    retired = real_db_client.post(
        f"/api/v1/platform/health-plan-templates/{template_id}/retire",
        headers=_headers(
            int(seeded["expert_actor_id"]),
            "expert",
            "slice6-http-template-retire-0001",
        ),
        json={"expected_version": 2},
    )
    assert retired.status_code == 200, retired.json()
    assert retired.json()["status"] == "RETIRED"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_receipt WHERE target_id='{template_id}'"
    ) == 3
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_audit WHERE target_id='{template_id}'"
    ) == 3
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_outbox WHERE aggregate_ref='{template_id}'"
    ) == 3

    eligibility_path = (
        f"/api/v1/institutions/service-cases/{case_id}/plan-generation-eligibility"
    )
    assert real_db_client.get(eligibility_path).status_code == 401
    malformed = real_db_client.get(
        eligibility_path, headers={"Authorization": "not-bearer"}
    )
    assert malformed.status_code == 401
    forbidden = real_db_client.get(
        eligibility_path,
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert forbidden.status_code == 403
    eligibility = real_db_client.get(eligibility_path, headers=org_headers)
    assert eligibility.status_code == 200, eligibility.json()
    assert eligibility.json()["eligible"] is True

    case_version = int(eligibility.json()["expected_service_case_version"])
    generation_path = f"/api/v1/institutions/service-cases/{case_id}/plan-generations"
    created = real_db_client.post(
        generation_path,
        headers=org_headers,
        json={"expected_service_case_version": case_version},
    )
    assert created.status_code == 202, created.json()
    replayed = real_db_client.post(
        generation_path,
        headers=org_headers,
        json={"expected_service_case_version": case_version},
    )
    assert replayed.status_code == 202
    assert replayed.json() == created.json()
    request_id = created.json()["request_id"]
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_generation_request WHERE request_id='{request_id}'"
    ) == 1

    generated = asyncio.run(_generate(__import__("uuid").UUID(request_id)))
    assert generated["status"] == "IN_REVIEW"
    review_id = str(
        pg_database.fetch_value(
            f"SELECT review_id FROM public.health_plan_review WHERE request_id='{request_id}'"
        )
    )
    plan_id = str(
        pg_database.fetch_value(
            f"SELECT current_plan_id FROM public.health_plan_generation_request WHERE request_id='{request_id}'"
        )
    )

    async def _direct_review_read_contract():
        review_sessions = get_slice6_review_writer_session()
        clinical_sessions = get_slice6_clinical_reader_session()
        review_session = await anext(review_sessions)
        clinical_session = await anext(clinical_sessions)
        try:
            rows = await HealthPlanRepository(review_session).review_page(
                cursor_id=None, status=None, limit=2
            )
            plans = await HealthPlanRepository(clinical_session).plan_details(
                tuple(UUID(str(row["plan_id"])) for row in rows)
            )
            return review_list_item(rows[0], plans[0])
        finally:
            await review_sessions.aclose()
            await clinical_sessions.aclose()

    assert asyncio.run(_direct_review_read_contract()).status == "PENDING"

    assert real_db_client.get("/api/v1/platform/health-plan-reviews").status_code == 401
    actor_by_role = {
        "org_admin": int(seeded["org_actor_id"]),
        "therapist": int(seeded["therapist_actor_id"]),
        "member": int(seeded["family_actor_id"]),
    }
    independent_roles = (
        "super_admin",
        "province_admin",
        "city_admin",
        "sys_admin",
        "org_operator",
        "host",
    )
    for offset, role in enumerate(independent_roles):
        actor_id = 9906701 + offset
        pg_database.execute(
            'INSERT INTO public."user" (id,phone,password_hash,role,status) VALUES '
            f"({actor_id},'1980990670{offset}','synthetic','{role}','active')"
        )
        actor_by_role[role] = actor_id
    from app.core.config import get_settings

    context = {
        str(actor_by_role["province_admin"]): {"province": "C1_TEST_PROVINCE"},
        str(actor_by_role["city_admin"]): {"province": "C1_TEST_PROVINCE", "city": "C1_TEST_CITY"},
    }
    try:
        with monkeypatch.context() as scope:
            scope.setenv("KG_AUTH_CONTEXT_MAP", json.dumps(context))
            get_settings.cache_clear()
            for role in (
                "super_admin", "province_admin", "city_admin", "sys_admin",
                "org_admin", "org_operator", "therapist", "host", "member",
            ):
                actor_id = actor_by_role[role]
                row = pg_database.fetch_rows(
                    'SELECT u.tenant_id,t.org_id FROM public."user" u '
                    'LEFT JOIN public.tenant t ON t.id=u.tenant_id WHERE u.id=$1', actor_id,
                )[0]
                forbidden = real_db_client.get(
                    "/api/v1/platform/health-plan-reviews",
                    headers=_headers(
                        actor_id, role, tenant_id=row["tenant_id"], org_id=row["org_id"] if role == "org_admin" else None,
                        **context.get(str(actor_id), {}),
                    ),
                )
                assert forbidden.status_code == 403, (role, forbidden.json())
    finally:
        get_settings.cache_clear()
    reviews = real_db_client.get(
        "/api/v1/platform/health-plan-reviews",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert reviews.status_code == 200, reviews.json()
    assert reviews.headers["Cache-Control"] == "no-store"
    assert any(row["review_id"] == review_id for row in reviews.json()["items"])
    assert reviews.json()["items"][0]["plan_version_no"] == 1
    assert reviews.json()["items"][0]["version"] == 1
    assert reviews.json()["items"][0]["status"] == "PENDING"
    pending_reviews = real_db_client.get(
        "/api/v1/platform/health-plan-reviews?status=PENDING",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert pending_reviews.status_code == 200
    assert [row["review_id"] for row in pending_reviews.json()["items"]] == [review_id]
    invalid_plan_status = real_db_client.get(
        "/api/v1/platform/health-plan-reviews?status=IN_REVIEW",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert invalid_plan_status.status_code == 422
    assert invalid_plan_status.json()["code"] == "INVALID_REQUEST"

    review_detail = real_db_client.get(
        f"/api/v1/platform/health-plan-reviews/{review_id}",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert review_detail.status_code == 200, review_detail.json()
    assert review_detail.headers["Cache-Control"] == "no-store"
    assert review_detail.json()["plan_summary"]["plan_status"] == "IN_REVIEW"
    assert review_detail.json()["version_diff_codes"] == ["INITIAL_VERSION"]
    assert review_detail.json()["customer_summary_codes"] == [
        "ASSESSMENT_INPUT_CURRENT",
        "PROFILE_CONTEXT_INCLUDED",
    ]
    assert review_detail.json()["assessment_summary_codes"]
    assert review_detail.json()["assessment_summary_codes"][0].startswith("OVERALL_RISK_")
    serialized_review = review_detail.text.lower()
    for forbidden_field in (
        "subject_member_id",
        "tenant_id",
        "phone",
        "id_card",
        "token",
        "password",
        "credential",
        "raw_fact",
    ):
        assert forbidden_field not in serialized_review

    claim = real_db_client.post(
        f"/api/v1/platform/health-plan-reviews/{review_id}/claim",
        headers=expert_headers,
        json={"expected_version": 1},
    )
    assert claim.status_code == 200, claim.json()
    assert claim.json()["status"] == "CLAIMED"
    assert claim.headers["Cache-Control"] == "no-store"

    mismatched_reason = real_db_client.post(
        f"/api/v1/platform/health-plan-reviews/{review_id}/decision",
        headers={
            **_headers(int(seeded["expert_actor_id"]), "expert"),
            "Idempotency-Key": "slice6-http-review-mismatch-0001",
        },
        json={
            "decision": "NEEDS_CORRECTION",
            "reason_codes": ["CONTENT_APPROVED"],
            "expected_version": 2,
        },
    )
    assert mismatched_reason.status_code == 422
    assert mismatched_reason.json()["code"] == "INVALID_REQUEST"

    for index, (decision, reason_codes) in enumerate(
        (
            ("REJECTED", ["UNKNOWN_REJECTED"]),
            ("NEEDS_CORRECTION", ["ANY_REQUIRES_CORRECTION"]),
            ("APPROVED", []),
            (
                "REJECTED",
                ["MEDICAL_SAFETY_CONFLICT", "MEDICAL_SAFETY_CONFLICT"],
            ),
        )
    ):
        rejected_reason = real_db_client.post(
            f"/api/v1/platform/health-plan-reviews/{review_id}/decision",
            headers={
                **_headers(int(seeded["expert_actor_id"]), "expert"),
                "Idempotency-Key": f"slice6-http-review-reason-rejected-{index}",
            },
            json={
                "decision": decision,
                "reason_codes": reason_codes,
                "expected_version": 2,
            },
        )
        assert rejected_reason.status_code == 422
        assert rejected_reason.json()["code"] == "INVALID_REQUEST"

    decision_headers = {
        **_headers(int(seeded["expert_actor_id"]), "expert"),
        "Idempotency-Key": "slice6-http-review-correction-0001",
    }
    correction = real_db_client.post(
        f"/api/v1/platform/health-plan-reviews/{review_id}/decision",
        headers=decision_headers,
        json={
            "decision": "NEEDS_CORRECTION",
            "reason_codes": ["TEMPLATE_REAPPLY"],
            "expected_version": 2,
        },
    )
    assert correction.status_code == 200, correction.json()
    assert correction.json()["status"] == "NEEDS_CORRECTION"
    assert correction.json()["reason_codes"] == ["TEMPLATE_REAPPLY"]

    regenerated = asyncio.run(_generate(__import__("uuid").UUID(request_id)))
    assert regenerated["status"] == "IN_REVIEW"
    first_review_id = review_id
    review_id = str(
        pg_database.fetch_value(
            "SELECT r.review_id FROM public.health_plan_review r "
            "JOIN public.health_plan_version p ON p.plan_id=r.plan_id "
            f"WHERE r.request_id='{request_id}' ORDER BY p.version_no DESC LIMIT 1"
        )
    )
    plan_id = str(
        pg_database.fetch_value(
            f"SELECT current_plan_id FROM public.health_plan_generation_request WHERE request_id='{request_id}'"
        )
    )
    assert review_id != first_review_id

    page_one = real_db_client.get(
        "/api/v1/platform/health-plan-reviews?limit=1",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert page_one.status_code == 200, page_one.json()
    cursor = page_one.json()["next_cursor"]
    assert cursor and first_review_id not in cursor
    page_two = real_db_client.get(
        f"/api/v1/platform/health-plan-reviews?limit=1&cursor={cursor}",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert page_two.status_code == 200, page_two.json()
    assert page_two.json()["items"][0]["review_id"] == review_id
    tampered = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    rejected_cursor = real_db_client.get(
        f"/api/v1/platform/health-plan-reviews?limit=1&cursor={tampered}",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert rejected_cursor.status_code == 422
    assert rejected_cursor.json()["code"] == "INVALID_REQUEST"
    rebound_cursor = real_db_client.get(
        f"/api/v1/platform/health-plan-reviews?limit=1&status=PENDING&cursor={cursor}",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert rebound_cursor.status_code == 422

    second_detail = real_db_client.get(
        f"/api/v1/platform/health-plan-reviews/{review_id}",
        headers=_headers(int(seeded["expert_actor_id"]), "expert"),
    )
    assert second_detail.status_code == 200, second_detail.json()
    assert second_detail.json()["plan_version_no"] == 2
    assert second_detail.json()["version"] == 1
    assert [item["action"] for item in second_detail.json()["history"]] == [
        "PLAN_GENERATED",
        "REVIEW_CLAIMED",
        "REVIEW_DECIDED",
        "PLAN_GENERATED",
    ]
    assert [item["plan_version_no"] for item in second_detail.json()["history"]] == [
        1,
        1,
        1,
        2,
    ]

    second_claim = real_db_client.post(
        f"/api/v1/platform/health-plan-reviews/{review_id}/claim",
        headers=_headers(
            int(seeded["expert_actor_id"]),
            "expert",
            "slice6-http-review-claim-0002",
        ),
        json={"expected_version": 1},
    )
    assert second_claim.status_code == 200, second_claim.json()
    reviewed = real_db_client.post(
        f"/api/v1/platform/health-plan-reviews/{review_id}/decision",
        headers={
            **_headers(int(seeded["expert_actor_id"]), "expert"),
            "Idempotency-Key": "slice6-http-review-decision-0002",
        },
        json={
            "decision": "APPROVED",
            "reason_codes": ["CONTENT_APPROVED"],
            "expected_version": 2,
        },
    )
    assert reviewed.status_code == 200, reviewed.json()
    assert reviewed.json()["status"] == "APPROVED"
    assert reviewed.json()["plan_summary"]["plan_status"] == "USER_DECISION_PENDING"
    assert reviewed.json()["user_decision"] is None

    family_list = real_db_client.get(
        f"/api/v1/family/service-cases/{case_id}/plans", headers=family_headers
    )
    assert family_list.status_code == 200, family_list.json()
    assert plan_id in [row["plan_id"] for row in family_list.json()["items"]]
    detail_path = f"/api/v1/family/plans/{plan_id}"
    family_detail = real_db_client.get(detail_path, headers=family_headers)
    assert family_detail.status_code == 200, family_detail.json()
    assert family_detail.headers["Cache-Control"] == "no-store"
    assert family_detail.json()["status"] == "USER_DECISION_PENDING"

    explanation_requested = real_db_client.post(
        f"{detail_path}/decision",
        headers=family_headers,
        json={"decision": "NEEDS_EXPLANATION", "expected_version": 2},
    )
    assert explanation_requested.status_code == 200, explanation_requested.json()
    assert explanation_requested.json()["status"] == "NEEDS_EXPLANATION"
    assert explanation_requested.headers["Cache-Control"] == "no-store"
    bypass_explanation = real_db_client.post(
        f"{detail_path}/decision",
        headers=_headers(
            int(seeded["family_actor_id"]),
            "member",
            "slice6-http-bypass-explanation-0001",
        ),
        json={"decision": "ACCEPT", "expected_version": 3},
    )
    assert bypass_explanation.status_code == 409
    assert bypass_explanation.json()["code"] == "USER_DECISION_CONFLICT"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_user_decision WHERE plan_id='{plan_id}'"
    ) == 1
    explained = real_db_client.post(
        f"/api/v1/therapist/plans/{plan_id}/explanations",
        headers=_headers(
            int(seeded["therapist_actor_id"]),
            "therapist",
            "slice6-http-explanation-0001",
            tenant_id=int(seeded["tenant_id"]),
        ),
        json={"explanation_codes": ["PLAN_SCOPE_EXPLAINED"], "expected_version": 3},
    )
    assert explained.status_code == 200, explained.json()
    assert explained.json()["status"] == "USER_DECISION_PENDING"
    assert explained.headers["Cache-Control"] == "no-store"

    accepted_headers = _headers(
        int(seeded["family_actor_id"]), "member", "slice6-http-user-decision-0002"
    )
    accepted = real_db_client.post(
        f"{detail_path}/decision",
        headers=accepted_headers,
        json={"decision": "ACCEPT", "expected_version": 4},
    )
    assert accepted.status_code == 200, accepted.json()
    assert accepted.headers["Cache-Control"] == "no-store"
    assert accepted.json()["status"] == "ACTIVE"
    accepted_replay = real_db_client.post(
        f"{detail_path}/decision",
        headers=accepted_headers,
        json={"decision": "ACCEPT", "expected_version": 4},
    )
    assert accepted_replay.status_code == 200
    assert accepted_replay.json() == accepted.json()

    conflict = real_db_client.post(
        f"{detail_path}/decision",
        headers=accepted_headers,
        json={"decision": "DECLINE", "expected_version": 4},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    assert pg_database.fetch_value(
        f"SELECT count(*) FROM public.health_plan_user_decision WHERE plan_id='{plan_id}'"
    ) == 2

    missing = real_db_client.get(
        "/api/v1/family/plans/018f0f47-e4a8-7cc8-98f2-88d31f8bffff",
        headers=family_headers,
    )
    assert missing.status_code == 404
    invalid = real_db_client.get("/api/v1/family/plans/not-a-uuid", headers=family_headers)
    assert invalid.status_code == 422
