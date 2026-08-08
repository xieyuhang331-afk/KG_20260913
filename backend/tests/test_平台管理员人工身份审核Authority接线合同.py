from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.security import CurrentUser
from app.modules.auth.manual_identity_review_application import (
    PlatformAdminManualIdentityReviewConflict,
    PlatformAdminManualIdentityReviewForbidden,
    PlatformAdminManualIdentityReviewRequest,
    PlatformAdminManualIdentityReviewService,
    PlatformAdminManualIdentityReviewUnavailable,
)


DECIDED_AT = datetime(2026, 8, 8, 10, 0, tzinfo=timezone.utc)


def _request(**changes):
    values = {
        "idempotency_key": "manual-review-request-1042-v1",
        "decided_at": DECIDED_AT,
        "evidence_digest": "a" * 64,
    }
    values.update(changes)
    return PlatformAdminManualIdentityReviewRequest(**values)


class _AuthorityPort:
    def __init__(self, decision_id, result="decision", error=None):
        self.decision_id = decision_id
        self.result = result
        self.error = error
        self.calls = []

    async def load_current_decision(self, authority_decision_id):
        self.calls.append(authority_decision_id)
        assert authority_decision_id == self.decision_id
        if self.error is not None:
            raise self.error
        return self.result


class _TransitionService:
    def __init__(self, port, result="verified", error=None):
        self.port = port
        self.result = result
        self.error = error
        self.calls = []

    async def execute(self, authority_decision_id):
        self.calls.append(authority_decision_id)
        await self.port.load_current_decision(authority_decision_id)
        if self.error is not None:
            raise self.error
        return self.result


def _service(*, transition_error=None):
    ports = []
    transitions = []

    def authority_factory(**kwargs):
        port = _AuthorityPort(kwargs["authority_decision_id"])
        port.arguments = kwargs
        ports.append(port)
        return port

    def transition_factory(port):
        transition = _TransitionService(port, error=transition_error)
        transitions.append(transition)
        return transition

    service = PlatformAdminManualIdentityReviewService(
        authority_port_factory=authority_factory,
        transition_service_factory=transition_factory,
    )
    return service, ports, transitions


def test_平台管理员人工审核Authority接线尚未实现():
    async def exercise():
        service, ports, transitions = _service()
        result = await service.execute(
            current_user=CurrentUser(id=17, role="super_admin"),
            user_ref=1042,
            request=_request(),
        )
        return result, ports, transitions

    result, ports, transitions = asyncio.run(exercise())
    assert result == "verified"
    assert len(ports) == 1
    assert len(transitions) == 1
    arguments = ports[0].arguments
    assert arguments["reviewer_subject_id"] == 17
    assert arguments["user_ref"] == 1042
    assert arguments["request"] == _request()
    assert arguments["authority_decision_id"].startswith("manual-review-")
    assert len(arguments["authority_decision_id"]) == 78


def test_同一用户和幂等键生成稳定服务端Authority决定标识():
    async def exercise():
        service, ports, _ = _service()
        user = CurrentUser(id=17, role="super_admin")
        await service.execute(current_user=user, user_ref=1042, request=_request())
        await service.execute(current_user=user, user_ref=1042, request=_request())
        await service.execute(
            current_user=user,
            user_ref=1042,
            request=_request(idempotency_key="manual-review-request-1042-v2"),
        )
        return [port.decision_id for port in ports]

    decision_ids = asyncio.run(exercise())
    assert decision_ids[0] == decision_ids[1]
    assert decision_ids[0] != decision_ids[2]
    assert "manual-review-request" not in decision_ids[0]


@pytest.mark.parametrize(
    "current_user",
    [
        CurrentUser(id=17, role="province_admin"),
        CurrentUser(id=17, role="city_admin"),
        CurrentUser(id=17, role="org_admin"),
        CurrentUser(id=17, role="member"),
        CurrentUser(id=17, role="super_admin", tenant_id=3),
        CurrentUser(id=17, role="super_admin", org_id=9),
        CurrentUser(id=1042, role="super_admin"),
    ],
)
def test_无权Scope和自我审核在Authority调用前fail_closed(current_user):
    async def exercise():
        service, ports, transitions = _service()
        with pytest.raises(PlatformAdminManualIdentityReviewForbidden):
            await service.execute(
                current_user=current_user,
                user_ref=1042,
                request=_request(),
            )
        return ports, transitions

    ports, transitions = asyncio.run(exercise())
    assert ports == []
    assert transitions == []


def test_未知Authority或Writer异常被切断为固定Unavailable():
    class UnsafeTransition:
        async def execute(self, authority_decision_id):
            raise RuntimeError(
                "sql=params credential database_url pii_marker"
            )

    async def exercise():
        service = PlatformAdminManualIdentityReviewService(
            authority_port_factory=lambda **kwargs: _AuthorityPort(
                kwargs["authority_decision_id"]
            ),
            transition_service_factory=lambda port: UnsafeTransition(),
        )
        with pytest.raises(
            PlatformAdminManualIdentityReviewUnavailable
        ) as caught:
            await service.execute(
                current_user=CurrentUser(id=17, role="super_admin"),
                user_ref=1042,
                request=_request(),
            )
        return caught.value

    error = asyncio.run(exercise())
    public = f"{error!s} {error!r} {error.__cause__} {error.__context__}"
    for forbidden in ("sql=", "credential", "database_url", "pii_marker"):
        assert forbidden not in public


def test_Cancellation从Authority接线原样传播():
    class CancelledTransition:
        async def execute(self, authority_decision_id):
            raise asyncio.CancelledError()

    async def exercise():
        service = PlatformAdminManualIdentityReviewService(
            authority_port_factory=lambda **kwargs: _AuthorityPort(
                kwargs["authority_decision_id"]
            ),
            transition_service_factory=lambda port: CancelledTransition(),
        )
        with pytest.raises(asyncio.CancelledError):
            await service.execute(
                current_user=CurrentUser(id=17, role="super_admin"),
                user_ref=1042,
                request=_request(),
            )

    asyncio.run(exercise())


def test_SQLAlchemyAuthorityReader只读最小User投影且不读取PII():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "app/modules/auth/manual_identity_review_repository.py"
    ).read_text(encoding="utf-8")
    compact = "".join(source.split())
    assert "User.id,User.role,User.status,User.tenant_id,User.verify_status,User.updated_at" in compact
    assert "select(User)" not in compact
    for forbidden in ("User.phone", "User.real_name", "User.id_card", "User.password_hash"):
        assert forbidden not in compact


def test_RuntimeComposition复用传入SessionFactory且不创建Engine():
    from app.composition.p1_verified_transition import (
        create_platform_admin_manual_identity_review_service,
    )

    session_factory = object()
    uuid_generator = object()
    service = create_platform_admin_manual_identity_review_service(
        session_factory=session_factory,
        uuid_generator=uuid_generator,
    )
    assert service._session_factory is session_factory
    assert service._uuid_generator is uuid_generator


def _authority_port():
    from app.modules.auth.manual_identity_review_repository import (
        SqlAlchemyManualIdentityReviewAuthorityPort,
    )

    return SqlAlchemyManualIdentityReviewAuthorityPort(
        session_factory=object(),
        reviewer_subject_id=17,
        user_ref=1042,
        request=_request(),
        authority_decision_id="manual-review-" + "c" * 64,
    )


def _snapshot(**changes):
    values = {
        "reviewer": SimpleNamespace(
            id=17,
            role="super_admin",
            status="active",
            tenant_id=None,
            verify_status=None,
            updated_at=DECIDED_AT,
        ),
        "subject": SimpleNamespace(
            id=1042,
            role="member",
            status="active",
            tenant_id=None,
            verify_status="submitted",
            updated_at=DECIDED_AT,
        ),
        "classification": SimpleNamespace(
            user_ref=1042,
            account_class="natural_person",
            facts_version=19,
        ),
        "verification": None,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def test_AuthorityReader从当前最小投影构造不可变决定():
    decision = _authority_port()._build_decision(_snapshot())
    assert decision.authority_source == "manual_review"
    assert decision.reviewer_subject_id == 17
    assert decision.user_ref == 1042
    assert decision.subject_tenant_id is None
    assert decision.subject_org_id is None
    assert decision.facts_version == 19
    assert decision.currentness_version == 1
    assert decision.verification_epoch == 1
    assert decision.evidence_digest == "a" * 64
    assert decision.correlation_id == decision.authority_decision_id


@pytest.mark.parametrize(
    "snapshot",
    [
        _snapshot(reviewer=SimpleNamespace(id=17, role="province_admin", status="active", tenant_id=None)),
        _snapshot(reviewer=SimpleNamespace(id=17, role="super_admin", status="disabled", tenant_id=None)),
        _snapshot(reviewer=SimpleNamespace(id=17, role="super_admin", status="active", tenant_id=3)),
    ],
)
def test_AuthorityReader重新核验Reviewer当前投影(snapshot):
    with pytest.raises(PlatformAdminManualIdentityReviewForbidden):
        _authority_port()._build_decision(snapshot)


@pytest.mark.parametrize(
    "subject",
    [
        SimpleNamespace(id=1042, role="member", status="disabled", tenant_id=None, verify_status="submitted"),
        SimpleNamespace(id=1042, role="org_admin", status="active", tenant_id=None, verify_status="submitted"),
        SimpleNamespace(id=1042, role="member", status="active", tenant_id=3, verify_status="submitted"),
        SimpleNamespace(id=1042, role="member", status="active", tenant_id=None, verify_status="unverified"),
    ],
)
def test_AuthorityReader对目标状态或绑定漂移fail_closed(subject):
    with pytest.raises(PlatformAdminManualIdentityReviewConflict):
        _authority_port()._build_decision(_snapshot(subject=subject))


def test_AuthorityReader同一决定稳定重放而冲突决定不可覆盖():
    from app.modules.auth.registration_outbox import (
        P1VerificationTransitionCommand,
    )

    port = _authority_port()
    decision = port._build_decision(_snapshot())
    command = P1VerificationTransitionCommand(
        authority="P1_MANUAL_IDENTITY_REVIEW",
        case_ref=decision.authority_decision_id,
        decision_version=1,
        target_facts_version=19,
        user_ref=1042,
        verification_epoch=1,
        outcome="verified",
        evidence_digest="a" * 64,
        actor_type="super_admin",
        actor_ref="17",
        decided_at=DECIDED_AT,
    )
    verification = SimpleNamespace(
        authority_decision_key=command.authority_decision_key,
        user_ref=1042,
        facts_version=19,
        verification_epoch=1,
        outcome="verified",
        evidence_digest="a" * 64,
        actor_type="super_admin",
        actor_ref="17",
        decided_at=DECIDED_AT,
    )
    verified_subject = SimpleNamespace(
        id=1042,
        role="member",
        status="active",
        tenant_id=None,
        verify_status="verified",
        updated_at=DECIDED_AT,
    )
    replay = port._build_decision(
        _snapshot(subject=verified_subject, verification=verification)
    )
    assert replay == decision

    verification.authority_decision_key = "d" * 64
    with pytest.raises(PlatformAdminManualIdentityReviewConflict):
        port._build_decision(
            _snapshot(subject=verified_subject, verification=verification)
        )
