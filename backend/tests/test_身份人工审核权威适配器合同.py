import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from importlib import import_module

import pytest


EXPECTED_RED = "Manual identity review authority adapter is not implemented"
DECIDED_AT = datetime(2026, 8, 8, 9, 0, tzinfo=timezone.utc)


def _modules():
    try:
        return (
            import_module(
                "app.modules.auth.identity_verification_authority"
            ),
            import_module("app.modules.auth.manual_identity_review_adapter"),
        )
    except (ImportError, AttributeError):
        pytest.fail(EXPECTED_RED)


def _decision(**changes):
    authority, _ = _modules()
    values = {
        "authority_source": "manual_review",
        "authority_decision_id": "manual-review-decision-1042-v7",
        "reviewer_subject_id": 17,
        "reviewer_role": "super_admin",
        "reviewer_is_active": True,
        "reviewer_tenant_scope": None,
        "reviewer_org_scope": "platform",
        "user_ref": 1042,
        "subject_tenant_id": None,
        "subject_org_id": None,
        "subject_binding_started": False,
        "outcome": "verified",
        "facts_version": 19,
        "currentness_version": 7,
        "verification_epoch": 11,
        "predecessor_currentness_version": 6,
        "predecessor_verification_epoch": 10,
        "decided_at": DECIDED_AT,
        "evidence_digest": "b" * 64,
        "correlation_id": "registration-correlation-1042",
        "is_current": True,
        "revocation_reference": None,
    }
    values.update(changes)
    return authority.ManualIdentityReviewAuthorityDecision(**values)


class AuthorityPortStub:
    def __init__(self, decision=None, error=None):
        self.decision = decision or _decision()
        self.error = error
        self.calls = []

    async def load_current_decision(self, authority_decision_id):
        self.calls.append(authority_decision_id)
        if self.error is not None:
            raise self.error
        return self.decision


class WriterStub:
    def __init__(self, result="written", error=None):
        self.result = result
        self.error = error
        self.commands = []

    async def execute(self, command):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return self.result


def _service(port=None, writer=None):
    _, adapter = _modules()
    port = port or AuthorityPortStub()
    writer = writer or WriterStub()
    return adapter.ManualIdentityReviewVerifiedTransitionService(
        authority_port=port,
        transition_writer=writer,
    ), port, writer


def test_授权人工审核Authority适配器尚未实现():
    async def exercise():
        service, port, writer = _service()
        result = await service.execute("manual-review-decision-1042-v7")
        return result, port, writer

    result, port, writer = asyncio.run(exercise())
    assert result == "written"
    assert port.calls == ["manual-review-decision-1042-v7"]
    assert len(writer.commands) == 1


def test_适配器只接收决定标识并精确映射Writer命令():
    async def exercise():
        service, _, writer = _service()
        await service.execute("manual-review-decision-1042-v7")
        return writer.commands[0]

    command = asyncio.run(exercise())
    assert command.authority == "P1_MANUAL_IDENTITY_REVIEW"
    assert command.case_ref == "manual-review-decision-1042-v7"
    assert command.decision_version == 7
    assert command.target_facts_version == 19
    assert command.user_ref == 1042
    assert command.verification_epoch == 11
    assert command.outcome == "verified"
    assert command.evidence_digest == "b" * 64
    assert command.actor_type == "super_admin"
    assert command.actor_ref == "17"
    assert command.decided_at == DECIDED_AT
    assert not hasattr(command, "correlation_id")


def test_同一Authority决定稳定重放且不产生第二种命令():
    async def exercise():
        decision = _decision()
        port = AuthorityPortStub(decision)
        writer = WriterStub(result="replayed")
        service, _, _ = _service(port, writer)
        first = await service.execute(decision.authority_decision_id)
        second = await service.execute(decision.authority_decision_id)
        return first, second, writer.commands

    first, second, commands = asyncio.run(exercise())
    assert (first, second) == ("replayed", "replayed")
    assert commands[0] == commands[1]


@pytest.mark.parametrize(
    "value",
    [None, True, 7, "", " leading", "trailing ", "x" * 129],
)
def test_调用入口拒绝非规范Authority决定标识且零调用(value):
    async def exercise():
        service, port, writer = _service()
        authority, _ = _modules()
        with pytest.raises(
            authority.InvalidIdentityVerificationAuthorityDecision
        ):
            await service.execute(value)
        return port, writer

    port, writer = asyncio.run(exercise())
    assert port.calls == []
    assert writer.commands == []


def test_Authority_Port返回非决定对象时fail_closed且零Writer():
    async def exercise():
        service, _, writer = _service(
            AuthorityPortStub(decision=object())
        )
        authority, _ = _modules()
        with pytest.raises(authority.IdentityVerificationAuthorityRejected):
            await service.execute("manual-review-decision-1042-v7")
        return writer

    writer = asyncio.run(exercise())
    assert writer.commands == []


def test_Authority异常被安全切断且零Writer():
    async def exercise():
        secret = RuntimeError(
            "sql password=hunter2 phone=13800138000 database://secret"
        )
        service, _, writer = _service(
            AuthorityPortStub(error=secret)
        )
        authority, _ = _modules()
        with pytest.raises(
            authority.IdentityVerificationAuthorityUnavailable
        ) as caught:
            await service.execute("manual-review-decision-1042-v7")
        return caught.value, writer

    error, writer = asyncio.run(exercise())
    public = f"{error!s} {error!r} {error.__cause__} {error.__context__}"
    assert "hunter2" not in public
    assert "13800138000" not in public
    assert "database://" not in public
    assert error.__cause__ is None
    assert error.__context__ is None
    assert writer.commands == []


def test_Cancellation从Authority和Writer原样传播():
    async def exercise():
        for port, writer in (
            (AuthorityPortStub(error=asyncio.CancelledError()), WriterStub()),
            (AuthorityPortStub(), WriterStub(error=asyncio.CancelledError())),
        ):
            service, _, _ = _service(port, writer)
            with pytest.raises(asyncio.CancelledError):
                await service.execute("manual-review-decision-1042-v7")

    asyncio.run(exercise())

