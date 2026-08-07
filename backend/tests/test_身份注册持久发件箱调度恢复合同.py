import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import import_module
import json
from types import SimpleNamespace
from uuid import UUID

import pytest


EXPECTED_RED = (
    "Registration outbox dispatcher and recovery are not implemented"
)


def _worker_api():
    try:
        module = import_module(
            "app.modules.auth.registration_outbox_worker"
        )
        return (
            module.RegistrationOutboxDispatcher,
            module.RegistrationOutboxReconciler,
        )
    except (ImportError, AttributeError):
        pytest.fail(EXPECTED_RED)


def test_注册发件箱调度与恢复尚未实现():
    dispatcher_type, reconciler_type = _worker_api()

    assert dispatcher_type is not None
    assert reconciler_type is not None


EVENT_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29001")
OUTBOX_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29002")
VERIFICATION_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d29003")
NOW = datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc)


def _api():
    return import_module("app.modules.auth.registration_outbox_worker")


def _item(*, attempt_count=1, confirmation_only=False):
    module = _api()
    authority_key = "a" * 64
    semantic_key = (
        "identity.registration.verification_verified:v1:"
        f"p1_user:73:authority:{authority_key}"
    )
    canonical = json.dumps(
        {
            "authority_decision_key": authority_key,
            "event_id": str(EVENT_ID),
            "event_schema_version": 1,
            "event_type": "identity.registration.verification_verified",
            "facts_version": 11,
            "occurred_at": NOW.isoformat(),
            "source_ref": 73,
            "source_system": "P1_USER",
            "trace_ref": None,
            "verification_decision_ref": str(VERIFICATION_ID),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return module.RegistrationOutboxWorkItem(
        outbox_record_id=OUTBOX_ID,
        event_id=EVENT_ID,
        semantic_idempotency_key=semantic_key,
        event_type="identity.registration.verification_verified",
        event_schema_version=1,
        source_system="P1_USER",
        source_ref=73,
        verification_decision_ref=VERIFICATION_ID,
        authority_decision_key=authority_key,
        facts_version=11,
        occurred_at=NOW,
        payload_digest=hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest(),
        trace_ref=None,
        attempt_count=attempt_count,
        lease_owner="worker-1",
        lease_generation=3,
        locked_until=NOW + timedelta(seconds=90),
        confirmation_only=confirmation_only,
    )


class FakeUow:
    def __init__(
        self,
        *,
        claimed=(),
        transition_result=True,
        commit_error=None,
        state=None,
        candidates=(),
        inserted=True,
        winner=None,
        renew_result=True,
    ):
        self.claimed = claimed
        self.transition_result = transition_result
        self.commit_error = commit_error
        self.state = state
        self.candidates = candidates
        self.inserted = inserted
        self.winner = winner
        self.renew_result = renew_result
        self.calls = []

    async def __aenter__(self):
        self.calls.append(("enter",))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.calls.append(("exit", exc_type))

    async def claim(self, **kwargs):
        self.calls.append(("claim", kwargs))
        return self.claimed

    async def renew(self, **kwargs):
        self.calls.append(("renew", kwargs))
        return self.renew_result

    async def transition(self, **kwargs):
        self.calls.append(("transition", kwargs))
        return self.transition_result

    async def get_state(self, event_id):
        self.calls.append(("get_state", event_id))
        return self.state

    async def find_reconciliation_candidates(self, limit):
        self.calls.append(("candidates", limit))
        return self.candidates

    async def add_reconciled(self, candidate):
        self.calls.append(("add_reconciled", candidate))
        return self.inserted

    async def find_by_semantic_key(self, semantic_key):
        self.calls.append(("winner", semantic_key))
        return self.winner

    async def commit(self):
        self.calls.append(("commit",))
        if self.commit_error is not None:
            raise self.commit_error


class UowFactory:
    def __init__(self, *units):
        self.units = list(units)

    def __call__(self):
        return self.units.pop(0)


def test_Dispatcher短事务Claim后只对稳定完成结果ACK():
    module = _api()
    item = _item()
    claim = FakeUow(claimed=(item,))
    transition = FakeUow()

    async def orchestrator(received):
        assert received is item
        return module.RegistrationOutboxDeliveryResult(
            module.DeliveryStatus.COMPLETED
        )

    async def confirmer(_):
        raise AssertionError("stable completion must not confirm")

    summary = asyncio.run(
        module.RegistrationOutboxDispatcher(
            unit_of_work_factory=UowFactory(claim, transition),
            orchestrator=orchestrator,
            outcome_confirmer=confirmer,
        ).run_once(lease_owner="worker-1")
    )

    assert summary.claimed == 1
    assert summary.delivered == 1
    transition_call = next(
        call for call in transition.calls if call[0] == "transition"
    )
    assert transition_call[1]["target_status"] == "delivered"
    assert transition_call[1]["lease_generation"] == 3


def test_第八次Retryable直接DeadLetter且没有retry8():
    module = _api()
    item = _item(attempt_count=8)
    transition = FakeUow()

    async def orchestrator(_):
        return module.RegistrationOutboxDeliveryResult(
            module.DeliveryStatus.RETRYABLE,
            "DEPENDENCY_UNAVAILABLE",
        )

    summary = asyncio.run(
        module.RegistrationOutboxDispatcher(
            unit_of_work_factory=UowFactory(
                FakeUow(claimed=(item,)), transition
            ),
            orchestrator=orchestrator,
            outcome_confirmer=lambda _: None,
        ).run_once(lease_owner="worker-1")
    )

    assert summary.dead_lettered == 1
    values = next(
        call[1] for call in transition.calls if call[0] == "transition"
    )
    assert values["target_status"] == "dead_letter"
    assert values["error_code"] == "RETRY_EXHAUSTED"
    assert values["retry_delay_seconds"] is None


@pytest.mark.parametrize(
    ("confirmation", "target"),
    [
        ("COMPLETE", "delivered"),
        ("ABSENT", "retry"),
        ("PARTIAL_OR_UNKNOWN", "review_required"),
    ],
)
def test_OutcomeUnknown使用新Identity边界三分确认(confirmation, target):
    module = _api()
    transition = FakeUow()

    async def orchestrator(_):
        return module.RegistrationOutboxDeliveryResult(
            module.DeliveryStatus.OUTCOME_UNKNOWN
        )

    async def confirmer(_):
        return module.ConfirmationStatus(confirmation)

    asyncio.run(
        module.RegistrationOutboxDispatcher(
            unit_of_work_factory=UowFactory(
                FakeUow(claimed=(_item(),)), transition
            ),
            orchestrator=orchestrator,
            outcome_confirmer=confirmer,
        ).run_once(lease_owner="worker-1")
    )
    values = next(
        call[1] for call in transition.calls if call[0] == "transition"
    )
    assert values["target_status"] == target


def test_终态CommitUnknown使用新UoW确认且不盲写():
    module = _api()
    item = _item()
    digest = None
    committed = module.RegistrationOutboxState(
        event_id=EVENT_ID,
        status="delivered",
        lease_owner=None,
        lease_generation=3,
        locked_until=None,
        last_error_category=None,
        last_error_code=None,
        last_error_digest=digest,
    )
    uncertain = FakeUow(
        commit_error=module.RegistrationOutboxCommitOutcomeUnknown()
    )
    confirm = FakeUow(state=committed)

    async def orchestrator(_):
        return module.RegistrationOutboxDeliveryResult(
            module.DeliveryStatus.COMPLETED
        )

    summary = asyncio.run(
        module.RegistrationOutboxDispatcher(
            unit_of_work_factory=UowFactory(
                FakeUow(claimed=(item,)), uncertain, confirm
            ),
            orchestrator=orchestrator,
            outcome_confirmer=lambda _: None,
        ).run_once(lease_owner="worker-1")
    )
    assert summary.delivered == 1
    assert not any(call[0] == "transition" for call in confirm.calls)


@pytest.mark.parametrize("confirmed", [True, False])
def test_HeartbeatCommitUnknown必须使用新UoW精确确认租约截止时间(confirmed):
    module = _api()
    item = _item()
    renewed_until = NOW + timedelta(seconds=180)
    uncertain = FakeUow(
        renew_result=renewed_until,
        commit_error=module.RegistrationOutboxCommitOutcomeUnknown(),
    )
    state = module.RegistrationOutboxState(
        event_id=item.event_id,
        status="processing",
        lease_owner=item.lease_owner,
        lease_generation=item.lease_generation,
        locked_until=(
            renewed_until
            if confirmed
            else renewed_until + timedelta(seconds=1)
        ),
        last_error_category=None,
        last_error_code=None,
        last_error_digest=None,
    )
    confirmation = FakeUow(state=state)
    dispatcher = module.RegistrationOutboxDispatcher(
        unit_of_work_factory=UowFactory(uncertain, confirmation),
        orchestrator=lambda _: None,
        outcome_confirmer=lambda _: None,
    )

    assert asyncio.run(dispatcher._renew_lease(item)) is confirmed
    assert not any(call[0] == "commit" for call in confirmation.calls)


def test_Cancellation原样传播且不写终态():
    module = _api()
    cancellation = asyncio.CancelledError()

    async def orchestrator(_):
        raise cancellation

    async def scenario():
        with pytest.raises(asyncio.CancelledError) as caught:
            await module.RegistrationOutboxDispatcher(
                unit_of_work_factory=UowFactory(
                    FakeUow(claimed=(_item(),))
                ),
                orchestrator=orchestrator,
                outcome_confirmer=lambda _: None,
            ).run_once(lease_owner="worker-1")
        assert caught.value is cancellation

    asyncio.run(scenario())


def test_无效Envelope在调用Orchestrator前DeadLetter():
    module = _api()
    calls = {"orchestrator": 0, "confirmer": 0}

    async def orchestrator(_):
        calls["orchestrator"] += 1

    async def confirmer(_):
        calls["confirmer"] += 1

    transition = FakeUow()
    invalid = replace(_item(), payload_digest="0" * 64)
    summary = asyncio.run(
        module.RegistrationOutboxDispatcher(
            unit_of_work_factory=UowFactory(
                FakeUow(claimed=(invalid,)), transition
            ),
            orchestrator=orchestrator,
            outcome_confirmer=confirmer,
        ).run_once(lease_owner="worker-1")
    )
    assert calls == {"orchestrator": 0, "confirmer": 0}
    assert summary.dead_lettered == 1
    values = next(
        call[1] for call in transition.calls if call[0] == "transition"
    )
    assert values["error_code"] == "INVALID_ENVELOPE"


def test_未知内部异常进入ReviewRequired而非Retry():
    module = _api()
    transition = FakeUow()

    async def orchestrator(_):
        raise RuntimeError("vendor sql phone=13800138000")

    summary = asyncio.run(
        module.RegistrationOutboxDispatcher(
            unit_of_work_factory=UowFactory(
                FakeUow(claimed=(_item(),)), transition
            ),
            orchestrator=orchestrator,
            outcome_confirmer=lambda _: None,
        ).run_once(lease_owner="worker-1")
    )
    assert summary.review_required == 1
    values = next(
        call[1] for call in transition.calls if call[0] == "transition"
    )
    assert values["target_status"] == "review_required"
    assert values["error_category"] == "INTERNAL_UNKNOWN"
    assert values["error_code"] == "UNEXPECTED_INTERNAL"


def test_确定性退避有界且按Event和Attempt稳定():
    module = _api()
    first = module.retry_delay_seconds(EVENT_ID, 4)
    assert first == module.retry_delay_seconds(EVENT_ID, 4)
    assert 32 <= first <= 48
    assert 512 <= module.retry_delay_seconds(EVENT_ID, 8) <= 768


def test_Reconciliation复用CanonicalIdentity且CommitUnknown只读确认():
    module = _api()
    values = {
        "outbox_record_id": OUTBOX_ID,
        "event_id": EVENT_ID,
        "semantic_idempotency_key": "semantic-key",
        "event_type": "identity.registration.verification_verified",
        "event_schema_version": 1,
        "source_system": "P1_USER",
        "source_ref": 73,
        "verification_decision_ref": VERIFICATION_ID,
        "authority_decision_key": "a" * 64,
        "facts_version": 11,
        "occurred_at": NOW,
    }
    candidate = SimpleNamespace(**values, status="pending")
    changed_status_winner = SimpleNamespace(
        **values, status="delivered"
    )
    uncertain = FakeUow(
        commit_error=module.RegistrationOutboxCommitOutcomeUnknown()
    )
    confirmation = FakeUow(winner=changed_status_winner)
    completed = asyncio.run(
        module.RegistrationOutboxReconciler(
            unit_of_work_factory=UowFactory(
                FakeUow(candidates=(candidate,)),
                uncertain,
                confirmation,
            )
        ).run_once()
    )
    assert completed == 1
    assert any(call[0] == "winner" for call in confirmation.calls)
