import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.modules.auth.registration_outbox_worker import (
    ConfirmationStatus,
    DeliveryStatus,
    RegistrationOutboxDeliveryResult,
    RegistrationOutboxDispatcher,
    RegistrationOutboxWorkItem,
)
from app.modules.member.application.member_no_allocator import (
    InvalidMemberNoAllocationCommand,
    MemberNoAllocationConflict,
    MemberNoAllocationFailed,
    MemberNoAllocationOutcomeUnknown,
    MemberNoAllocationUnavailable,
)
from app.modules.member.application.registration_bootstrap import (
    InvalidRegistrationIdentityBootstrapCommand,
    RegistrationIdentityBootstrapConflict,
    RegistrationIdentityBootstrapFailed,
    RegistrationIdentityBootstrapInconsistent,
    RegistrationIdentityBootstrapOutcomeUnknown,
)
from app.modules.member.application.registration_orchestrator import (
    RegistrationOrchestrator,
)
from app.modules.member.application.registration_orchestrator_ports import (
    RegistrationOrchestratorEligibilityInconsistent,
    RegistrationOrchestratorEligibilityProof,
    RegistrationOrchestratorEligibilityProofMissing,
    RegistrationOrchestratorEligibilityStale,
    RegistrationOrchestratorEligibilityUnavailable,
    RegistrationOrchestratorNotEligible,
)


EVENT_ID = UUID("018f0000-0000-7000-8000-000000000101")
VERIFICATION_REF = UUID("018f0000-0000-7000-8000-000000000102")
ELIGIBILITY_REF = UUID("018f0000-0000-7000-8000-000000000103")
ALLOCATION_REF = UUID("018f0000-0000-7000-8000-000000000104")


def _item(**changes):
    occurred_at = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
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
            "facts_version": 4,
            "occurred_at": occurred_at.isoformat(),
            "source_ref": 73,
            "source_system": "P1_USER",
            "trace_ref": None,
            "verification_decision_ref": str(VERIFICATION_REF),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    values = dict(
        outbox_record_id=EVENT_ID,
        event_id=EVENT_ID,
        semantic_idempotency_key=semantic_key,
        event_type="identity.registration.verification_verified",
        event_schema_version=1,
        source_system="P1_USER",
        source_ref=73,
        verification_decision_ref=VERIFICATION_REF,
        authority_decision_key=authority_key,
        facts_version=4,
        occurred_at=occurred_at,
        payload_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        trace_ref=None,
        attempt_count=1,
        lease_owner="worker",
        lease_generation=1,
        locked_until=datetime.now(UTC) + timedelta(seconds=90),
    )
    values.update(changes)
    return RegistrationOutboxWorkItem(**values)


class EligibilityReader:
    error = None

    async def get_current_for_verified_transition(self, **kwargs):
        if self.error:
            raise self.error
        return RegistrationOrchestratorEligibilityProof(
            decision_ref=ELIGIBILITY_REF,
            user_ref=kwargs["user_ref"],
            verification_decision_ref=kwargs["verification_decision_ref"],
            facts_version=kwargs["facts_version"],
        )


class Allocator:
    error = None
    calls = 0

    async def allocate(self, command):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(allocation_id=ALLOCATION_REF, replayed=False)


class Bootstrap:
    error = None
    replayed = False
    calls = 0

    async def execute(self, command):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(replayed=self.replayed)


class OutcomeReader:
    result = ConfirmationStatus.COMPLETE

    async def confirm(self, item):
        return self.result


def _service():
    return RegistrationOrchestrator(
        eligibility_reader=EligibilityReader(),
        member_no_allocator=Allocator(),
        bootstrap_service=Bootstrap(),
        outcome_reader=OutcomeReader(),
    )


def test_P1权威验证转换驱动内部注册编排完成():
    result = asyncio.run(_service().deliver(_item()))
    assert result.status is DeliveryStatus.COMPLETED
    assert result.error_code is None


def test_完整canonical结果稳定重放():
    service = _service()
    service._bootstrap_service.replayed = True
    result = asyncio.run(service.deliver(_item()))
    assert result.status is DeliveryStatus.REPLAYED


def test_资格拒绝时不分配编号也不Bootstrap():
    service = _service()
    service._eligibility_reader.error = RegistrationOrchestratorNotEligible()
    result = asyncio.run(service.deliver(_item()))
    assert result.status is DeliveryStatus.REVIEW_REQUIRED
    assert result.error_code == "ELIGIBILITY_INCONSISTENT"
    assert service._member_no_allocator.calls == 0
    assert service._bootstrap_service.calls == 0


def test_不支持的事件永久拒绝():
    result = asyncio.run(_service().deliver(_item(event_schema_version=2)))
    assert result.status is DeliveryStatus.PERMANENT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("semantic_idempotency_key", "forged"),
        ("authority_decision_key", "z" * 64),
        ("payload_digest", "0" * 64),
        ("trace_ref", EVENT_ID),
    ],
)
def test_伪造canonical_envelope永久拒绝(field, value):
    result = asyncio.run(_service().deliver(_item(**{field: value})))
    assert (result.status, result.error_code) == (
        DeliveryStatus.PERMANENT,
        "INVALID_ENVELOPE",
    )


def test_MemberNo瞬时不可用映射为可重试且Identity零写入():
    service = _service()
    service._member_no_allocator.error = MemberNoAllocationUnavailable()
    result = asyncio.run(service.deliver(_item()))
    assert result.status is DeliveryStatus.RETRYABLE
    assert service._bootstrap_service.calls == 0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (RegistrationOrchestratorEligibilityProofMissing(), DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_PROOF_MISSING"),
        (RegistrationOrchestratorEligibilityStale(), DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_STALE"),
        (RegistrationOrchestratorEligibilityInconsistent(), DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_INCONSISTENT"),
        (RegistrationOrchestratorEligibilityUnavailable(), DeliveryStatus.RETRYABLE, "ORCHESTRATOR_RETRYABLE"),
    ],
)
def test_Eligibility异常使用R2批准的固定分类(error, status, code):
    service = _service()
    service._eligibility_reader.error = error
    result = asyncio.run(service.deliver(_item()))
    assert (result.status, result.error_code) == (status, code)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidMemberNoAllocationCommand(), DeliveryStatus.PERMANENT, "INVALID_ENVELOPE"),
        (MemberNoAllocationUnavailable(), DeliveryStatus.RETRYABLE, "ORCHESTRATOR_RETRYABLE"),
        (MemberNoAllocationOutcomeUnknown(), DeliveryStatus.OUTCOME_UNKNOWN, "OUTCOME_UNKNOWN_UNCONFIRMED"),
        (MemberNoAllocationConflict(), DeliveryStatus.REVIEW_REQUIRED, "BOOTSTRAP_CONFLICT"),
        (MemberNoAllocationFailed(), DeliveryStatus.INTERNAL_UNKNOWN, "UNEXPECTED_INTERNAL"),
    ],
)
def test_MemberNo异常使用R2批准的固定分类(error, status, code):
    service = _service()
    service._member_no_allocator.error = error
    result = asyncio.run(service.deliver(_item()))
    assert (result.status, result.error_code) == (status, code)
    assert service._bootstrap_service.calls == 0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidRegistrationIdentityBootstrapCommand(), DeliveryStatus.PERMANENT, "INVALID_ENVELOPE"),
        (RegistrationIdentityBootstrapConflict(), DeliveryStatus.REVIEW_REQUIRED, "BOOTSTRAP_CONFLICT"),
        (RegistrationIdentityBootstrapInconsistent(), DeliveryStatus.REVIEW_REQUIRED, "BOOTSTRAP_CONFLICT"),
        (RegistrationIdentityBootstrapOutcomeUnknown(), DeliveryStatus.OUTCOME_UNKNOWN, "OUTCOME_UNKNOWN_UNCONFIRMED"),
        (RegistrationIdentityBootstrapFailed(), DeliveryStatus.INTERNAL_UNKNOWN, "UNEXPECTED_INTERNAL"),
    ],
)
def test_Bootstrap异常使用R2批准的固定分类(error, status, code):
    service = _service()
    service._bootstrap_service.error = error
    result = asyncio.run(service.deliver(_item()))
    assert (result.status, result.error_code) == (status, code)


@pytest.mark.parametrize(
    ("status", "code", "target", "category"),
    [
        (DeliveryStatus.RETRYABLE, "ORCHESTRATOR_RETRYABLE", "retry", "RETRYABLE"),
        (DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_PROOF_MISSING", "review_required", "REVIEW_REQUIRED"),
        (DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_STALE", "review_required", "REVIEW_REQUIRED"),
        (DeliveryStatus.REVIEW_REQUIRED, "ELIGIBILITY_INCONSISTENT", "review_required", "REVIEW_REQUIRED"),
        (DeliveryStatus.REVIEW_REQUIRED, "BOOTSTRAP_CONFLICT", "review_required", "REVIEW_REQUIRED"),
        (DeliveryStatus.PERMANENT, "INVALID_ENVELOPE", "dead_letter", "PERMANENT"),
        (DeliveryStatus.INTERNAL_UNKNOWN, "UNEXPECTED_INTERNAL", "review_required", "INTERNAL_UNKNOWN"),
    ],
)
def test_编排结果经Dispatcher后不丢失固定分类(status, code, target, category):
    result = SimpleNamespace(status=status, error_code=code, error_digest=None)
    actual = RegistrationOutboxDispatcher._target(_item(), result)
    assert actual[:3] == (target, category, code)


def test_结果确认直接兼容Dispatcher接口():
    assert asyncio.run(_service().confirm(_item())) is ConfirmationStatus.COMPLETE


def test_Cancellation原样传播():
    service = _service()
    service._eligibility_reader.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.deliver(_item()))


@pytest.mark.parametrize("stage", ["allocator", "bootstrap", "confirm"])
def test_各阶段Cancellation原样传播(stage):
    service = _service()
    if stage == "allocator":
        service._member_no_allocator.error = asyncio.CancelledError()
        call = service.deliver(_item())
    elif stage == "bootstrap":
        service._bootstrap_service.error = asyncio.CancelledError()
        call = service.deliver(_item())
    else:
        async def cancelled(_):
            raise asyncio.CancelledError
        service._outcome_reader.confirm = cancelled
        call = service.confirm(_item())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call)


def test_confirm异常和非法返回均fail_closed():
    service = _service()
    service._outcome_reader.result = "COMPLETE"
    assert asyncio.run(service.confirm(_item())) is ConfirmationStatus.PARTIAL_OR_UNKNOWN

    async def failed(_):
        raise RuntimeError("postgresql://credential@host/db")
    service._outcome_reader.confirm = failed
    assert asyncio.run(service.confirm(_item())) is ConfirmationStatus.PARTIAL_OR_UNKNOWN
    assert asyncio.run(service.confirm(_item(event_schema_version=2))) is ConfirmationStatus.PARTIAL_OR_UNKNOWN


def test_outcome_unknown经Dispatcher全路径确认而不二次写入():
    transitions = []

    class Uow:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def transition(self, **kwargs):
            transitions.append(kwargs)
            return True

        async def commit(self):
            return None

    async def orchestrator(_):
        return RegistrationOutboxDeliveryResult(
            status=DeliveryStatus.OUTCOME_UNKNOWN,
            error_code="OUTCOME_UNKNOWN_UNCONFIRMED",
            error_digest=None,
        )

    async def confirmer(_):
        return ConfirmationStatus.COMPLETE

    dispatcher = RegistrationOutboxDispatcher(
        unit_of_work_factory=Uow,
        orchestrator=orchestrator,
        outcome_confirmer=confirmer,
    )
    assert asyncio.run(dispatcher._deliver(_item())) == "delivered"
    assert transitions[0]["target_status"] == "delivered"


def test_公开错误不包含供应商异常内容():
    class UnsafeReader:
        async def get_current_for_verified_transition(self, **kwargs):
            raise RuntimeError("postgresql://user:password@host/db SELECT phone")

    service = _service()
    service._eligibility_reader = UnsafeReader()
    result = asyncio.run(service.deliver(_item()))
    public = f"{result!r}"
    assert result.status is DeliveryStatus.INTERNAL_UNKNOWN
    assert "password" not in public
    assert "SELECT" not in public
