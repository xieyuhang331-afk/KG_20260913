from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.therapist_qualification import service
from app.modules.therapist_qualification.domain import CORRECTION_FIELDS, validate_correction_scope
from app.modules.therapist_qualification.schemas import TherapistReviewDecisionRequest


def test_补正字段资质目标和原子重提():
    assert validate_correction_scope(requested_profile_fields=("display_name",), allowed_profile_fields=CORRECTION_FIELDS) == ("display_name",)


def test_review_item平台决定即终态且重提只新增item():
    assert CORRECTION_FIELDS == {"real_name", "display_name", "practice_summary", "service_tags"}


def test_领取审核是独立的无结果审计动作():
    value = TherapistReviewDecisionRequest(
        decision="START_REVIEW",
        expected_version=1,
    )
    assert value.decision == "START_REVIEW"
    assert value.qualification_outcomes == {}
    assert value.profile_fields == ()
    assert value.qualification_targets == ()


@pytest.mark.asyncio
async def test_跨Revision资格固定拒绝且零写入(monkeypatch):
    current_qualification_id = "00000000-0000-7000-8000-000000000014"
    added = []

    class Repo:
        def __init__(self, _session):
            pass

        async def profile_for_update(self, _therapist_id):
            return SimpleNamespace(
                therapist_id="00000000-0000-7000-8000-000000000012",
                tenant_id=7,
                status="UNDER_REVIEW",
                version=2,
            )

        async def review_item_for_update(self, _review_item_id):
            return SimpleNamespace(
                review_item_id="00000000-0000-7000-8000-000000000011",
                therapist_id="00000000-0000-7000-8000-000000000012",
                revision_id="00000000-0000-7000-8000-000000000013",
                review_kind="INITIAL",
                status="UNDER_REVIEW",
                reviewer_user_id=41,
                version=1,
            )

        async def qualification_for_revision(self, _therapist_id, _revision_id):
            return SimpleNamespace(qualification_version_id=current_qualification_id)

        async def add_all(self, values):
            added.extend(values)

        async def add_audit(self, value):
            added.append(value)

    async def no_replay(*_args, **_kwargs):
        return "a" * 64, None

    monkeypatch.setattr(service, "TherapistQualificationRepository", Repo)
    monkeypatch.setattr(service, "_replay", no_replay)
    payload = TherapistReviewDecisionRequest(
        decision="APPROVED",
        expected_version=2,
        qualification_outcomes={
            "00000000-0000-7000-8000-000000000099": "APPROVED"
        },
    )
    actor = SimpleNamespace(id=41)
    with pytest.raises(HTTPException) as exc:
        await service.review_decision(
            object(), actor, "00000000-0000-7000-8000-000000000012",
            payload, "request", "idempotency-key",
            review_item_id="00000000-0000-7000-8000-000000000011",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "THERAPIST_REVIEW_OUTCOME_CONFLICT"
    assert added == []


def test_空资格结果在Schema边界固定拒绝且不进入Service():
    with pytest.raises(Exception):
        TherapistReviewDecisionRequest(
            decision="APPROVED",
            expected_version=2,
            qualification_outcomes={},
        )
