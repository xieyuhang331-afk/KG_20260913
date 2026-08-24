from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from app.modules.health_plan.service import (
    CommitOutcome,
    HealthPlanError,
    claim_plan_review,
    classify_commit_outcome,
    commit_with_confirmation,
    decide_plan,
    review_plan,
)


UUIDS = tuple(
    UUID(f"018f0f47-e4a8-7cc8-98f2-88d31f8a8d{value}") for value in range(41, 55)
)
NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


class _Repository:
    def __init__(self, replay=None):
        self.replay = replay
        self.transitions = []
        self.decisions = []

    async def mutation_replay(self, actor_user_id, operation, idempotency_key, request_digest):
        return self.replay

    async def transition_review(self, operation, payload):
        self.transitions.append((operation, payload))
        return payload["response"]

    async def decide_plan(self, payload):
        self.decisions.append(payload)
        return payload["response"]


def test_提交确认只接受完整独立后像():
    expected = {"aggregate": {"status": "ACTIVE", "version": 4}, "audit": 1, "outbox": 1, "receipt": 1}
    assert classify_commit_outcome(expected=expected, actual=expected, preimage=None) is CommitOutcome.COMMITTED
    assert classify_commit_outcome(expected=expected, actual=None, preimage={"status": "USER_DECISION_PENDING"}) is CommitOutcome.NOT_COMMITTED
    assert classify_commit_outcome(expected=expected, actual={**expected, "outbox": 0}, preimage=None) is CommitOutcome.UNKNOWN
    assert classify_commit_outcome(expected=expected, actual={**expected, "extra": 1}, preimage=None) is CommitOutcome.UNKNOWN


class _CommitSession:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.rollbacks = 0

    async def commit(self):
        if self.error is not None:
            raise self.error

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_提交异常只接受独立数据库确认为COMMITTED且不自动重放():
    session = _CommitSession(RuntimeError("transport lost"))
    confirmations = 0

    async def confirm():
        nonlocal confirmations
        confirmations += 1
        return CommitOutcome.COMMITTED

    assert await commit_with_confirmation(session, confirm=confirm) is CommitOutcome.COMMITTED
    assert session.rollbacks == 1
    assert confirmations == 1

    with pytest.raises(RuntimeError, match="DEPENDENCY_UNAVAILABLE"):
        await commit_with_confirmation(
            _CommitSession(RuntimeError("transport lost")),
            confirm=lambda: _outcome(CommitOutcome.NOT_COMMITTED),
        )
    with pytest.raises(RuntimeError, match="COMMIT_OUTCOME_UNKNOWN"):
        await commit_with_confirmation(
            _CommitSession(RuntimeError("transport lost")),
            confirm=lambda: _outcome(CommitOutcome.UNKNOWN),
        )


async def _outcome(value: CommitOutcome) -> CommitOutcome:
    return value


@pytest.mark.asyncio
async def test_相同幂等请求返回首次审核后像且零重复副作用():
    first = {"review_id": UUIDS[0], "status": "APPROVED", "version": 2}
    repository = _Repository(replay=first)
    result = await review_plan(
        repository,
        review_id=UUIDS[0],
        decision="APPROVED",
        reason_codes=("CONTENT_APPROVED",),
        expected_version=1,
        actor_user_id=7,
        actor_role="expert",
        idempotency_key="review-key",
        now=NOW,
        id_factory=iter(UUIDS[1:5]).__next__,
    )
    assert result == first
    assert repository.transitions == []


@pytest.mark.asyncio
async def test_审核和用户决定只生成一次Audit_Outbox_Receipt并保持结构化内容():
    repository = _Repository()
    claimed = await claim_plan_review(
        repository,
        review_id=UUIDS[0],
        expected_version=1,
        actor_user_id=7,
        actor_role="expert",
        idempotency_key="claim-key",
        now=NOW,
        id_factory=iter(UUIDS[1:4]).__next__,
    )
    assert claimed["status"] == "CLAIMED"
    claim_payload = repository.transitions[0][1]
    assert claim_payload["audit_id"] != claim_payload["event_id"] != claim_payload["receipt_id"]

    reviewed = await review_plan(
        repository,
        review_id=UUIDS[0],
        decision="NEEDS_CORRECTION",
        reason_codes=("TEMPLATE_REAPPLY",),
        expected_version=1,
        actor_user_id=7,
        actor_role="expert",
        idempotency_key="review-key-2",
        now=NOW,
        id_factory=iter(UUIDS[1:5]).__next__,
    )
    assert reviewed["status"] == "NEEDS_CORRECTION"
    payload = repository.transitions[1][1]
    assert payload["audit_id"] != payload["event_id"] != payload["receipt_id"]
    assert "content" not in payload

    decided = await decide_plan(
        repository,
        plan_id=UUIDS[5],
        decision="ACCEPT",
        expected_version=3,
        actor_user_id=9,
        actor_role="member",
        actor_context="AUTO",
        proxy_grant_id=None,
        idempotency_key="decision-key",
        now=NOW,
        id_factory=iter(UUIDS[6:10]).__next__,
    )
    assert decided["status"] == "ACTIVE"
    assert len(repository.decisions) == 1

    with pytest.raises(HealthPlanError, match="USER_DECISION_FORBIDDEN"):
        await decide_plan(
            repository,
            plan_id=UUIDS[5],
            decision="ACCEPT",
            expected_version=3,
            actor_user_id=10,
            actor_role="org_admin",
            actor_context="INSTITUTION",
            proxy_grant_id=None,
            idempotency_key="forbidden",
            now=NOW,
            id_factory=iter(UUIDS[10:14]).__next__,
        )
