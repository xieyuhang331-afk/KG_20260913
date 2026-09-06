import copy
import inspect

import pytest

from app.modules.therapist_qualification import service
from app.modules.therapist_qualification.service import _commit, _replay


def test_目标绑定幂等与历史重放():
    assert "scope" in inspect.signature(_replay).parameters
    assert "request_digest" in inspect.getsource(_replay)


def test_所有mutation提交未知均fresh确认且零二次写():
    source = inspect.getsource(_commit)
    assert "COMMIT_OUTCOME_UNKNOWN" in source
    assert "await confirm()" in source


def test_partial_commit不能只凭幂等摘要误判COMMITTED():
    source = (
        inspect.getsource(service._mutation_postimage_snapshot)
        + inspect.getsource(service._confirm_mutation_outcome)
    )
    for required in (
        "aggregate", "revision", "qualification", "audit", "outbox",
        "request_digest", "postimage_digest", "COMMITTED", "NOT_COMMITTED", "UNKNOWN",
    ):
        assert required in source
    assert "receipt" in source
    assert "return COMMITTED" in source
    assert "return NOT_COMMITTED" in source
    assert "return UNKNOWN" in source


def test_commit_unknown后像定位上下文覆盖激活状态决定与审核决定():
    snapshot_signature = inspect.signature(service._mutation_postimage_snapshot)
    receipt_signature = inspect.signature(service._commit_receipt)
    assert "confirmation_context" in snapshot_signature.parameters
    assert "confirmation_context" in receipt_signature.parameters

    snapshot_source = inspect.getsource(service._mutation_postimage_snapshot)
    for required in (
        "status_decision_id",
        "decision_id",
        "therapist_status_decision",
        "reviewer_user_id",
        "request_digest",
        '"status_decision"',
    ):
        assert required in snapshot_source

    activate_source = inspect.getsource(service.activate)
    status_source = inspect.getsource(service.change_status)
    review_source = inspect.getsource(service.review_decision)
    assert 'confirmation_context={"invitation_id": invitation_id}' in activate_source
    assert '"status_decision_id": status_decision.status_decision_id' in status_source
    assert '"decision_id": decision_id' in review_source


def test_commit_unknown的Profile后像覆盖状态转换全部业务字段():
    source = inspect.getsource(service._mutation_postimage_snapshot)
    for field in (
        "activated_at",
        "submitted_at",
        "reviewed_at",
        "suspended_at",
        "resumed_at",
        "exited_at",
        "suspension_reason_code",
    ):
        assert field in source


def _full_confirmation_snapshot():
    return {
        "receipt": {
            "request_digest": "a" * 64,
            "postimage_digest": "b" * 64,
            "created_at": "2026-08-17T01:02:03+00:00",
        },
        "aggregate": {
            "invitation": {
                "invitation_id": "00000000-0000-7000-8000-000000000001",
                "status": "ACTIVATED",
                "version": 2,
                "activated_at": "2026-08-17T01:02:03+00:00",
            },
            "profile": {
                "therapist_id": "00000000-0000-7000-8000-000000000002",
                "status": "APPROVED_ACTIVE",
                "version": 7,
                "activated_at": "2026-08-17T01:02:03+00:00",
                "submitted_at": "2026-08-17T01:02:03+00:00",
                "reviewed_at": "2026-08-17T01:02:03+00:00",
                "suspended_at": "2026-08-17T01:02:03+00:00",
                "resumed_at": "2026-08-17T01:02:03+00:00",
                "exited_at": None,
                "suspension_reason_code": None,
            },
        },
        "revision": {
            "revision_id": "00000000-0000-7000-8000-000000000003",
            "revision_no": 3,
        },
        "qualification": [
            {
                "qualification_version_id": "00000000-0000-7000-8000-000000000004",
                "profile_revision_id": "00000000-0000-7000-8000-000000000003",
                "position": 1,
            }
        ],
        "review_item": {
            "review_item_id": "00000000-0000-7000-8000-000000000005",
            "revision_id": "00000000-0000-7000-8000-000000000003",
            "status": "DECIDED",
            "version": 2,
        },
        "review_decision": [
            {
                "decision_id": "00000000-0000-7000-8000-000000000006",
                "review_item_id": "00000000-0000-7000-8000-000000000005",
                "revision_id": "00000000-0000-7000-8000-000000000003",
                "reviewer_user_id": 41,
                "decision": "APPROVED",
                "qualification_outcomes": {
                    "00000000-0000-7000-8000-000000000004": "APPROVED"
                },
                "reason_code": None,
                "correction_fields": None,
                "request_digest": "a" * 64,
                "created_at": "2026-08-17T01:02:03+00:00",
            }
        ],
        "status_decision": [
            {
                "status_decision_id": "00000000-0000-7000-8000-000000000007",
                "therapist_id": "00000000-0000-7000-8000-000000000002",
                "actor_kind": "USER",
                "actor_user_id": 41,
                "worker_identity": None,
                "decision": "RESUMED",
                "reason_code": "QUALIFICATION_RENEWED",
                "expected_profile_version": 6,
                "request_digest": "a" * 64,
                "created_at": "2026-08-17T01:02:03+00:00",
            }
        ],
        "audit": [{"action": "THERAPIST_RESUMED", "postimage_digest": "b" * 64}],
        "outbox": [{"event_type": "THERAPIST_RESUMED", "aggregate_id": "00000000-0000-7000-8000-000000000002"}],
        "readiness": {"tenant_id": 7, "evidence_version": 9},
        "readiness_evidence": [{"tenant_id": 7, "evidence_version": 9}],
    }


def _snapshot_for(operation):
    value = _full_confirmation_snapshot()
    if operation == "ACTIVATE":
        value.update({
            "revision": None,
            "qualification": [],
            "review_item": None,
            "review_decision": [],
            "status_decision": [],
            "readiness": None,
            "readiness_evidence": [],
        })
        return value, (
            ("aggregate", "invitation", "status"),
            ("aggregate", "invitation", "version"),
            ("aggregate", "invitation", "activated_at"),
            ("aggregate", "profile", "status"),
            ("aggregate", "profile", "version"),
            ("audit", 0, "action"),
            ("outbox", 0, "event_type"),
            ("receipt", "request_digest"),
        )
    if operation in {"SUSPENDED", "RESUMED", "EXITED"}:
        value.update({
            "revision": None,
            "qualification": [],
            "review_item": None,
            "review_decision": [],
        })
        value["aggregate"]["invitation"] = None
        value["status_decision"][0]["decision"] = operation
        value["status_decision"][0]["reason_code"] = "STATUS_REASON"
        return value, (
            ("aggregate", "profile", "status"),
            ("aggregate", "profile", "version"),
            ("aggregate", "profile", "suspension_reason_code"),
            ("aggregate", "profile", "suspended_at"),
            ("aggregate", "profile", "resumed_at"),
            ("aggregate", "profile", "exited_at"),
            ("status_decision", 0, "status_decision_id"),
            ("status_decision", 0, "decision"),
            ("status_decision", 0, "reason_code"),
            ("status_decision", 0, "actor_user_id"),
            ("status_decision", 0, "created_at"),
            ("audit", 0, "action"),
            ("outbox", 0, "event_type"),
            ("receipt", "request_digest"),
        )
    value["aggregate"]["invitation"] = None
    value["status_decision"] = []
    return value, (
        ("aggregate", "profile", "status"),
        ("aggregate", "profile", "version"),
        ("aggregate", "profile", "submitted_at"),
        ("aggregate", "profile", "reviewed_at"),
        ("revision", "revision_id"),
        ("qualification", 0, "qualification_version_id"),
        ("review_item", "review_item_id"),
        ("review_item", "revision_id"),
        ("review_decision", 0, "decision_id"),
        ("review_decision", 0, "qualification_outcomes"),
        ("review_decision", 0, "decision"),
        ("review_decision", 0, "reason_code"),
        ("review_decision", 0, "correction_fields"),
        ("review_decision", 0, "reviewer_user_id"),
        ("review_decision", 0, "request_digest"),
        ("audit", 0, "action"),
        ("outbox", 0, "event_type"),
        ("receipt", "request_digest"),
    )


def _leaf_paths(value, prefix=()):
    if isinstance(value, dict):
        paths = []
        for key, item in value.items():
            paths.extend(_leaf_paths(item, (*prefix, key)))
        return tuple(paths)
    if isinstance(value, list):
        paths = []
        for index, item in enumerate(value):
            paths.extend(_leaf_paths(item, (*prefix, index)))
        return tuple(paths)
    return (prefix,)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("ACTIVATE", "SUSPENDED", "RESUMED", "EXITED", "REVIEW_DECISION"))
async def test_partial_commit任一业务后像缺失都只能UNKNOWN(monkeypatch, operation):
    expected, semantic_paths = _snapshot_for(operation)
    required_paths = _leaf_paths(expected)
    assert semantic_paths

    async def outcome_for(actual):
        async def snapshot(*_args, **_kwargs):
            return actual

        monkeypatch.setattr(service, "_mutation_postimage_snapshot", snapshot)
        return await service._confirm_mutation_outcome(
            object(),
            expected=expected,
            scope="reviewer:41:review:current",
            operation=operation,
            key="partial-commit-contract",
            response={"decision_id": "00000000-0000-7000-8000-000000000006"},
            confirmation_context={
                "invitation_id": (
                    "00000000-0000-7000-8000-000000000001"
                    if operation == "ACTIVATE" else None
                ),
                "decision_id": (
                    "00000000-0000-7000-8000-000000000006"
                    if operation == "REVIEW_DECISION" else None
                ),
                "status_decision_id": (
                    "00000000-0000-7000-8000-000000000007"
                    if operation in {"SUSPENDED", "RESUMED", "EXITED"} else None
                ),
            },
        )

    assert await outcome_for(copy.deepcopy(expected)) == service.COMMITTED
    for path in required_paths:
        partial = copy.deepcopy(expected)
        target = partial
        for part in path[:-1]:
            target = target[part]
        target.pop(path[-1])
        assert await outcome_for(partial) == service.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("ACTIVATE", "SUSPENDED", "RESUMED", "EXITED", "REVIEW_DECISION"))
async def test_无业务写入才可NOT_COMMITTED且混合部分提交保持UNKNOWN(monkeypatch, operation):
    expected, _ = _snapshot_for(operation)
    rolled_back = copy.deepcopy(expected)
    rolled_back.update({
        "receipt": None,
        "revision": None,
        "qualification": [],
        "review_item": None,
        "review_decision": [],
        "status_decision": [],
        "audit": [],
        "outbox": [],
        "readiness": None,
        "readiness_evidence": [],
    })
    if rolled_back["aggregate"]["invitation"] is not None:
        rolled_back["aggregate"]["invitation"]["version"] -= 1
        rolled_back["aggregate"]["invitation"]["status"] = "INVITED"
        rolled_back["aggregate"]["invitation"]["activated_at"] = None
    if rolled_back["aggregate"]["profile"] is not None:
        if operation == "ACTIVATE":
            rolled_back["aggregate"]["profile"] = None
        else:
            rolled_back["aggregate"]["profile"]["version"] -= 1

    async def confirm(actual):
        async def snapshot(*_args, **_kwargs):
            return actual

        monkeypatch.setattr(service, "_mutation_postimage_snapshot", snapshot)
        return await service._confirm_mutation_outcome(
            object(), expected=expected, scope="scope", operation=operation,
            key="contract-key", response={}, confirmation_context={},
        )

    assert await confirm(rolled_back) == service.NOT_COMMITTED
    mixed = copy.deepcopy(rolled_back)
    mixed["audit"] = copy.deepcopy(expected["audit"])
    assert await confirm(mixed) == service.UNKNOWN


@pytest.mark.asyncio
async def test_UNKNOWN固定503且commit不发生二次写():
    from fastapi import HTTPException

    events = []

    class Session:
        commit_calls = 0
        rollback_calls = 0
        close_calls = 0
        closed = False

        async def commit(self):
            self.commit_calls += 1
            events.append("commit")
            raise RuntimeError("commit result unavailable")

        async def rollback(self):
            self.rollback_calls += 1
            events.append("rollback")

        async def close(self):
            self.close_calls += 1
            self.closed = True
            events.append("close")

    confirm_calls = 0

    async def confirm():
        nonlocal confirm_calls
        assert session.closed, "GATE_CONFIRM_BEFORE_CLOSE"
        confirm_calls += 1
        events.append("confirm")
        return service.UNKNOWN

    session = Session()
    with pytest.raises(HTTPException) as exc:
        await service._commit(session, confirm=confirm)
    assert getattr(exc.value, "status_code", None) == 503
    assert getattr(exc.value, "detail", None) == "COMMIT_OUTCOME_UNKNOWN"
    assert (session.commit_calls, session.rollback_calls, session.close_calls, confirm_calls) == (1, 1, 1, 1)
    assert events == ["commit", "rollback", "close", "confirm"], "GATE_COMMIT_RELEASE_CONFIRM_ORDER_INVALID"
