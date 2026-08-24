from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.modules.health_assessment.service import (
    CommitOutcome,
    build_completion_mutation_plan,
    classify_completion_confirmation,
    commit_with_confirmation,
    start_assessment,
)


ASSESSMENT_ID = UUID("018f62f6-52c7-7a51-8f75-0c4fe6b15290")


def _plan():
    return build_completion_mutation_plan(
        assessment_id=ASSESSMENT_ID,
        module_risks={
            "BLOOD_PRESSURE_CARDIOVASCULAR": "WITHIN_RANGE",
            "GLUCOSE_METABOLISM": "ATTENTION",
            "LIPID_METABOLISM": "NOT_ASSESSED",
            "WEIGHT_ABDOMINAL_OBESITY": "WITHIN_RANGE",
        },
        trigger_codes=(),
        completed_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
        preimage={"assessment": {"assessment_id": str(ASSESSMENT_ID), "status": "RUNNING"}},
    )


def test_完整业务后像精确一致才允许COMMITTED():
    plan = _plan()
    assert classify_completion_confirmation(plan, plan.expected_postimage) == "COMMITTED"
    assert classify_completion_confirmation(plan, plan.preimage) == "NOT_COMMITTED"


@pytest.mark.parametrize(
    "leaf",
    ("assessment", "module_results", "high_risk_task", "audit", "outbox", "receipt"),
)
def test_任一aggregate或伴随写入缺失都必须UNKNOWN(leaf):
    plan = _plan()
    actual = dict(plan.expected_postimage)
    actual.pop(leaf)
    assert classify_completion_confirmation(plan, actual) == "UNKNOWN"


def test_多余结果与字段或version不一致都必须UNKNOWN():
    plan = _plan()
    extra = dict(plan.expected_postimage)
    extra["unexpected"] = True
    assert classify_completion_confirmation(plan, extra) == "UNKNOWN"

    mismatch = dict(plan.expected_postimage)
    mismatch["assessment"] = dict(mismatch["assessment"], status="FAILED")
    assert classify_completion_confirmation(plan, mismatch) == "UNKNOWN"


class _CommitFails:
    async def commit(self):
        raise ConnectionError("synthetic")

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_commit异常只接受完整确认的COMMITTED():
    assert await commit_with_confirmation(
        _CommitFails(), confirm=lambda: _result(CommitOutcome.COMMITTED)
    ) is CommitOutcome.COMMITTED
    with pytest.raises(RuntimeError, match="COMMIT_OUTCOME_UNKNOWN"):
        await commit_with_confirmation(
            _CommitFails(), confirm=lambda: _result(CommitOutcome.UNKNOWN)
        )
    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        await commit_with_confirmation(
            _CommitFails(), confirm=lambda: _result(CommitOutcome.NOT_COMMITTED)
        )


@pytest.mark.asyncio
async def test_cancelled_error不得被提交确认边界吞掉():
    class _Cancelled:
        async def commit(self):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await commit_with_confirmation(
            _Cancelled(), confirm=lambda: _result(CommitOutcome.UNKNOWN)
        )


async def _result(value):
    return value


class _ReplaySecrets:
    def digest(self, purpose, value):
        return bytes([len(purpose)]) * 32, "slice5-test-k1"


class _ReplayRepository:
    def __init__(self, response):
        self.response = response
        self.authority_called = False

    async def assessment_start_replay(self, actor_user_id, idempotency_key, request_digest):
        assert actor_user_id == 91
        assert idempotency_key == "slice5-start-replay-0001"
        assert request_digest
        return self.response

    async def start_authority(self, service_case_id, actor_user_id):
        self.authority_called = True
        raise AssertionError("dynamic authority must not run for an exact replay")


@pytest.mark.asyncio
async def test_相同Start请求先验证不可变Receipt再回放原响应():
    expected = {
        "assessment_id": ASSESSMENT_ID,
        "service_case_id": ASSESSMENT_ID,
        "sequence_no": 1,
        "status": "DRAFT_SNAPSHOT",
        "overall_risk": None,
        "rule_version": "1",
        "input_snapshot_ref": ASSESSMENT_ID,
        "supersedes_assessment_id": None,
        "initiated_at": datetime(2026, 8, 24, tzinfo=timezone.utc),
        "completed_at": None,
        "version": 1,
    }
    repository = _ReplayRepository(expected)
    result = await start_assessment(
        repository,
        actor_user_id=91,
        service_case_id=ASSESSMENT_ID,
        expected_case_version=1,
        idempotency_key="slice5-start-replay-0001",
        request_id=ASSESSMENT_ID,
        secrets=_ReplaySecrets(),
    )
    assert result == expected
    assert repository.authority_called is False
