import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
import json
import traceback
from uuid import UUID

import pytest

from app.modules.auth.eligibility_evidence import build_p1_projection_digest
from app.modules.member.application.registration_eligibility import (
    AccountClass,
    RegistrationAccountStatus,
    RegistrationUserRole,
    TrustedRegistrationEligibilityFacts,
    VerificationOutcome,
    VerificationStatus,
)


EXPECTED_RED = "P1 durable registration outbox is not implemented"
NOT_READY = "P1 verification transition prerequisites are not ready"
INCONSISTENT = "P1 verification transition is inconsistent"
USER_REF = 73
TARGET_FACTS_VERSION = 11
VERIFICATION_DECISION_REF = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d20001"
)
REGISTRATION_EVENT_ID = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d20002"
)
ELIGIBILITY_DECISION_REF = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d20003"
)
OUTBOX_RECORD_ID = UUID(
    "01890f3e-7b7d-7cc3-88c8-2f5a12d20004"
)
DECIDED_AT = datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc)
_DEFAULT = object()


def _api():
    try:
        module = import_module("app.modules.auth.registration_outbox")
        return (
            module.P1VerificationTransitionCommand,
            module.P1VerificationTransitionWriter,
        )
    except (ImportError, AttributeError):
        pytest.fail(EXPECTED_RED)


def _module():
    _api()
    return import_module("app.modules.auth.registration_outbox")


def _authority_decision_key() -> str:
    canonical = json.dumps(
        {
            "authority": "P1_IDENTITY_REVIEW",
            "case_ref": "review-case-73-v4",
            "decision_version": 4,
            "target_facts_version": TARGET_FACTS_VERSION,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _transition_digest(**changes) -> str:
    values = {
        "authority_decision_key": _authority_decision_key(),
        "user_ref": USER_REF,
        "verification_epoch": 4,
        "outcome": "verified",
        "evidence_digest": "verification-evidence-digest-v4",
        "actor_type": "platform_reviewer",
        "actor_ref": "reviewer-ref-17",
        "decided_at": DECIDED_AT.isoformat(),
    }
    values.update(changes)
    canonical = json.dumps(
        values,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class UserProjectionStub:
    id: int = USER_REF
    role: str = "member"
    status: str = "active"
    verify_status: str = "pending"
    updated_at: datetime = DECIDED_AT


@dataclass(frozen=True)
class ClassificationEvidenceStub:
    decision_ref: UUID = UUID(
        "01890f3e-7b7d-7cc3-98c8-2f5a12d20005"
    )
    user_ref: int = USER_REF
    facts_version: int = TARGET_FACTS_VERSION
    classification_version: int = 4
    account_class: str = "natural_person"
    is_current: bool = True


@dataclass(frozen=True)
class VerificationEvidenceStub:
    decision_ref: UUID = UUID(
        "01890f3e-7b7d-7cc3-a8c8-2f5a12d20006"
    )
    user_ref: int = USER_REF
    facts_version: int = TARGET_FACTS_VERSION - 1
    verification_epoch: int = 3


class RegistrationOutboxUnitOfWorkStub:
    def __init__(
        self,
        *,
        existing=None,
        user=_DEFAULT,
        classification=_DEFAULT,
        predecessor=None,
        fail_on=None,
        commit_error=None,
    ):
        self.existing = existing
        self.user = UserProjectionStub() if user is _DEFAULT else user
        self.classification = (
            ClassificationEvidenceStub()
            if classification is _DEFAULT
            else classification
        )
        self.fail_on = fail_on
        self.predecessor = predecessor
        self.commit_error = commit_error
        self.verification_adds = []
        self.eligibility_adds = []
        self.outbox_adds = []
        self.authority_key_lookups = []
        self.user_lookups = []
        self.classification_lookups = []
        self.predecessor_lookups = []
        self.commit_calls = 0
        self.enter_calls = 0
        self.exit_calls = []

    async def __aenter__(self):
        self.enter_calls += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exit_calls.append(exc_type)
        if exc_type is not None:
            self.verification_adds.clear()
            self.eligibility_adds.clear()
            self.outbox_adds.clear()
            if self.user is not None:
                self.user.verify_status = "pending"
                self.user.updated_at = DECIDED_AT
        return False

    async def find_by_authority_decision_key(self, authority_decision_key):
        self.authority_key_lookups.append(authority_decision_key)
        return self.existing

    async def get_user_for_update(self, user_ref):
        self.user_lookups.append(user_ref)
        return self.user

    async def get_current_classification(self, user_ref):
        self.classification_lookups.append(user_ref)
        return self.classification

    async def get_current_verification(self, user_ref):
        self.predecessor_lookups.append(user_ref)
        return self.predecessor

    async def add_verification_decision(self, evidence):
        self.verification_adds.append(evidence)
        if self.fail_on == "verification":
            raise RuntimeError("verification write failed")

    async def add_eligibility_decision(self, evidence):
        self.eligibility_adds.append(evidence)
        if self.fail_on == "eligibility":
            raise RuntimeError("eligibility write failed")

    async def add_outbox(self, outbox):
        self.outbox_adds.append(outbox)
        if self.fail_on == "outbox":
            raise RuntimeError("outbox write failed")

    async def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error
        if self.fail_on == "commit":
            raise RuntimeError(
                "secret sql params phone=13800138000 password=hunter2"
            )


class UnitOfWorkFactoryStub:
    def __init__(self, *units_of_work):
        self.units_of_work = list(units_of_work)
        self.calls = 0

    def __call__(self):
        unit_of_work = self.units_of_work[self.calls]
        self.calls += 1
        return unit_of_work


class UuidGeneratorStub:
    def __init__(self):
        self.values = iter(
            (
                VERIFICATION_DECISION_REF,
                REGISTRATION_EVENT_ID,
                ELIGIBILITY_DECISION_REF,
                OUTBOX_RECORD_ID,
            )
        )
        self.calls = 0

    def generate(self):
        self.calls += 1
        return next(self.values)


def _command(command_type, **changes):
    values = {
        "authority": "P1_IDENTITY_REVIEW",
        "case_ref": "review-case-73-v4",
        "decision_version": 4,
        "target_facts_version": TARGET_FACTS_VERSION,
        "user_ref": USER_REF,
        "verification_epoch": 4,
        "outcome": "verified",
        "evidence_digest": "verification-evidence-digest-v4",
        "actor_type": "platform_reviewer",
        "actor_ref": "reviewer-ref-17",
        "decided_at": DECIDED_AT,
    }
    values.update(changes)
    return command_type(**values)


def _execute(writer_type, factory, uuid_generator, command):
    return asyncio.run(
        writer_type(
            unit_of_work_factory=factory,
            uuid_generator=uuid_generator,
        ).execute(command)
    )


def _trusted_facts(verification_ref=VERIFICATION_DECISION_REF):
    classification = ClassificationEvidenceStub()
    return TrustedRegistrationEligibilityFacts(
        user_ref=USER_REF,
        exists=True,
        facts_version=TARGET_FACTS_VERSION,
        role=RegistrationUserRole.MEMBER,
        status=RegistrationAccountStatus.ACTIVE,
        verify_status=VerificationStatus.VERIFIED,
        verification_decision_ref=verification_ref,
        verification_subject_user_ref=USER_REF,
        verification_outcome=VerificationOutcome.VERIFIED,
        verification_epoch=4,
        verification_is_current=True,
        account_class=AccountClass.NATURAL_PERSON,
        classification_decision_ref=classification.decision_ref,
        classification_subject_user_ref=USER_REF,
        classification_version=classification.classification_version,
        classification_is_current=True,
    )


def _facts_digest(verification_ref=VERIFICATION_DECISION_REF):
    return sha256(
        repr(_trusted_facts(verification_ref)).encode("utf-8")
    ).hexdigest()


def _projection_digest():
    return build_p1_projection_digest(
        user_ref=USER_REF,
        role="member",
        status="active",
        verify_status="verified",
        updated_at=DECIDED_AT,
    )


def _semantic_key():
    return (
        "identity.registration.verification_verified:v1:"
        f"p1_user:{USER_REF}:authority:{_authority_decision_key()}"
    )


def _complete_snapshot(module, **changes):
    verification = module.P1VerificationDecision(
        decision_ref=VERIFICATION_DECISION_REF,
        user_ref=USER_REF,
        facts_version=TARGET_FACTS_VERSION,
        verification_epoch=4,
        outcome="verified",
        evidence_digest="verification-evidence-digest-v4",
        actor_type="platform_reviewer",
        actor_ref="reviewer-ref-17",
        decided_at=DECIDED_AT,
        authority_decision_key=_authority_decision_key(),
        registration_event_id=REGISTRATION_EVENT_ID,
        supersedes_ref=None,
    )
    classification = ClassificationEvidenceStub()
    eligibility = module.P1RegistrationEligibilityDecision(
        decision_ref=ELIGIBILITY_DECISION_REF,
        user_ref=USER_REF,
        facts_version=TARGET_FACTS_VERSION,
        verification_decision_ref=VERIFICATION_DECISION_REF,
        classification_decision_ref=classification.decision_ref,
        policy_version="registration-eligibility-v1",
        facts_digest=_facts_digest(),
        p1_projection_digest=_projection_digest(),
        decision="eligible",
        reason="eligible",
        decided_at=DECIDED_AT,
    )
    outbox = module.P1RegistrationOutboxRecord(
        outbox_record_id=OUTBOX_RECORD_ID,
        event_id=REGISTRATION_EVENT_ID,
        semantic_idempotency_key=_semantic_key(),
        event_type="identity.registration.verification_verified",
        event_schema_version=1,
        source_system="P1_USER",
        source_ref=USER_REF,
        verification_decision_ref=VERIFICATION_DECISION_REF,
        authority_decision_key=_authority_decision_key(),
        facts_version=TARGET_FACTS_VERSION,
        occurred_at=DECIDED_AT,
    )
    result = module.P1VerificationTransitionResult(
        user_ref=USER_REF,
        verification_decision_ref=VERIFICATION_DECISION_REF,
        registration_event_id=REGISTRATION_EVENT_ID,
        authority_decision_key=_authority_decision_key(),
        transition_digest=_transition_digest(),
        facts_version=TARGET_FACTS_VERSION,
        status="verified",
        replayed=False,
    )
    values = {
        "result": result,
        "user_role": "member",
        "user_status": "active",
        "user_verify_status": "verified",
        "user_updated_at": DECIDED_AT,
        "verification": verification,
        "classification": classification,
        "eligibility": eligibility,
        "outbox": outbox,
    }
    values.update(changes)
    return module.P1VerificationTransitionSnapshot(**values)


def test_P1权威验证转换与持久发件箱尚未实现():
    command_type, writer_type = _api()
    unit_of_work = RegistrationOutboxUnitOfWorkStub()
    factory = UnitOfWorkFactoryStub(unit_of_work)
    uuid_generator = UuidGeneratorStub()
    result = _execute(
        writer_type,
        factory,
        uuid_generator,
        _command(command_type),
    )

    authority_key = _authority_decision_key()
    assert result.user_ref == USER_REF
    assert result.verification_decision_ref == VERIFICATION_DECISION_REF
    assert result.registration_event_id == REGISTRATION_EVENT_ID
    assert result.authority_decision_key == authority_key
    assert result.transition_digest == _transition_digest()
    assert result.facts_version == TARGET_FACTS_VERSION
    assert result.status == "verified"
    assert result.replayed is False
    assert unit_of_work.authority_key_lookups == [authority_key]
    assert unit_of_work.user.verify_status == "verified"
    assert len(unit_of_work.verification_adds) == 1
    assert len(unit_of_work.eligibility_adds) == 1
    assert len(unit_of_work.outbox_adds) == 1
    verification = unit_of_work.verification_adds[0]
    eligibility = unit_of_work.eligibility_adds[0]
    outbox = unit_of_work.outbox_adds[0]
    assert verification.authority_decision_key == authority_key
    assert verification.registration_event_id == REGISTRATION_EVENT_ID
    assert verification.facts_version == TARGET_FACTS_VERSION
    assert verification.supersedes_ref is None
    assert eligibility.verification_decision_ref == VERIFICATION_DECISION_REF
    assert eligibility.facts_version == TARGET_FACTS_VERSION
    assert eligibility.facts_digest == _facts_digest()
    assert eligibility.p1_projection_digest == _projection_digest()
    assert outbox.verification_decision_ref == VERIFICATION_DECISION_REF
    assert outbox.event_id == REGISTRATION_EVENT_ID
    assert outbox.authority_decision_key == authority_key
    assert outbox.facts_version == TARGET_FACTS_VERSION
    assert outbox.semantic_idempotency_key == (
        "identity.registration.verification_verified:v1:"
        f"p1_user:{USER_REF}:authority:{authority_key}"
    )
    assert unit_of_work.commit_calls == 1
    assert factory.calls == 1
    assert uuid_generator.calls == 4


def test_重复Authority命令稳定重放且零UUID零写入零commit():
    module = _module()
    authority_key = _authority_decision_key()
    existing = _complete_snapshot(module)
    unit_of_work = RegistrationOutboxUnitOfWorkStub(existing=existing)
    factory = UnitOfWorkFactoryStub(unit_of_work)
    uuid_generator = UuidGeneratorStub()

    result = _execute(
        module.P1VerificationTransitionWriter,
        factory,
        uuid_generator,
        _command(module.P1VerificationTransitionCommand),
    )

    assert result == module.P1VerificationTransitionResult(
        user_ref=USER_REF,
        verification_decision_ref=VERIFICATION_DECISION_REF,
        registration_event_id=REGISTRATION_EVENT_ID,
        authority_decision_key=authority_key,
        transition_digest=_transition_digest(),
        facts_version=TARGET_FACTS_VERSION,
        status="verified",
        replayed=True,
    )
    assert unit_of_work.user_lookups == []
    assert unit_of_work.classification_lookups == []
    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []
    assert unit_of_work.commit_calls == 0
    assert uuid_generator.calls == 0


@pytest.mark.parametrize(
    "delivery_status",
    [
        "pending",
        "processing",
        "retry",
        "delivered",
        "review_required",
        "dead_letter",
    ],
)
def test_合法投递生命周期不改变权威转换的稳定重放(delivery_status):
    module = _module()
    snapshot = _complete_snapshot(module)
    existing = replace(
        snapshot,
        outbox=replace(snapshot.outbox, status=delivery_status),
    )
    unit_of_work = RegistrationOutboxUnitOfWorkStub(existing=existing)
    generator = UuidGeneratorStub()

    result = _execute(
        module.P1VerificationTransitionWriter,
        UnitOfWorkFactoryStub(unit_of_work),
        generator,
        _command(module.P1VerificationTransitionCommand),
    )

    assert result.replayed is True
    assert result.registration_event_id == REGISTRATION_EVENT_ID
    assert unit_of_work.commit_calls == 0
    assert generator.calls == 0


def test_commit结果未知时已投递事件仍可由新UoW确认():
    module = _module()
    snapshot = _complete_snapshot(module)
    delivered = replace(
        snapshot,
        outbox=replace(snapshot.outbox, status="delivered"),
    )
    first = RegistrationOutboxUnitOfWorkStub(
        commit_error=module.P1VerificationTransitionCommitOutcomeUnknown(
            "secret vendor sql"
        )
    )
    confirmation = RegistrationOutboxUnitOfWorkStub(
        existing=delivered
    )
    generator = UuidGeneratorStub()

    result = _execute(
        module.P1VerificationTransitionWriter,
        UnitOfWorkFactoryStub(first, confirmation),
        generator,
        _command(module.P1VerificationTransitionCommand),
    )

    assert result.replayed is True
    assert result.registration_event_id == REGISTRATION_EVENT_ID
    assert confirmation.commit_calls == 0
    assert generator.calls == 4


def test_未知投递状态拒绝稳定重放():
    module = _module()
    snapshot = _complete_snapshot(module)
    existing = replace(
        snapshot,
        outbox=replace(snapshot.outbox, status="invented"),
    )

    with pytest.raises(module.P1VerificationTransitionInconsistent):
        _execute(
            module.P1VerificationTransitionWriter,
            UnitOfWorkFactoryStub(
                RegistrationOutboxUnitOfWorkStub(existing=existing)
            ),
            UuidGeneratorStub(),
            _command(module.P1VerificationTransitionCommand),
        )


@pytest.mark.parametrize(
    "classification",
    [
        None,
        ClassificationEvidenceStub(facts_version=TARGET_FACTS_VERSION - 1),
        ClassificationEvidenceStub(is_current=False),
        ClassificationEvidenceStub(account_class="staff"),
    ],
)
def test_Classification未就绪时FailClosed且零副作用(classification):
    module = _module()
    unit_of_work = RegistrationOutboxUnitOfWorkStub(
        classification=classification
    )
    factory = UnitOfWorkFactoryStub(unit_of_work)
    uuid_generator = UuidGeneratorStub()

    with pytest.raises(module.P1VerificationTransitionNotReady) as caught:
        _execute(
            module.P1VerificationTransitionWriter,
            factory,
            uuid_generator,
            _command(module.P1VerificationTransitionCommand),
        )

    assert str(caught.value) == NOT_READY
    assert unit_of_work.user.verify_status == "pending"
    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []
    assert unit_of_work.commit_calls == 0
    assert uuid_generator.calls == 0


def test_已存在Authority结果与命令漂移时FailClosed且零副作用():
    module = _module()
    existing = _complete_snapshot(
        module,
        result=replace(
            _complete_snapshot(module).result,
            user_ref=USER_REF + 1,
        ),
    )
    unit_of_work = RegistrationOutboxUnitOfWorkStub(existing=existing)
    factory = UnitOfWorkFactoryStub(unit_of_work)
    uuid_generator = UuidGeneratorStub()

    with pytest.raises(module.P1VerificationTransitionInconsistent) as caught:
        _execute(
            module.P1VerificationTransitionWriter,
            factory,
            uuid_generator,
            _command(module.P1VerificationTransitionCommand),
        )

    assert str(caught.value) == INCONSISTENT
    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []
    assert unit_of_work.commit_calls == 0
    assert uuid_generator.calls == 0


@pytest.mark.parametrize(
    "command_changes",
    [
        {"verification_epoch": 5},
        {"evidence_digest": "different-evidence-digest"},
    ],
)
def test_同一AuthorityKey的审核载荷漂移时拒绝重放(command_changes):
    module = _module()
    existing = _complete_snapshot(module)
    unit_of_work = RegistrationOutboxUnitOfWorkStub(existing=existing)
    factory = UnitOfWorkFactoryStub(unit_of_work)
    uuid_generator = UuidGeneratorStub()

    with pytest.raises(module.P1VerificationTransitionInconsistent) as caught:
        _execute(
            module.P1VerificationTransitionWriter,
            factory,
            uuid_generator,
            _command(
                module.P1VerificationTransitionCommand,
                **command_changes,
            ),
        )

    assert str(caught.value) == INCONSISTENT
    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []
    assert unit_of_work.commit_calls == 0
    assert uuid_generator.calls == 0


def test_缺少匹配记录时已verified投影不得创建新事件():
    module = _module()
    unit_of_work = RegistrationOutboxUnitOfWorkStub(
        user=UserProjectionStub(verify_status="verified")
    )
    factory = UnitOfWorkFactoryStub(unit_of_work)
    uuid_generator = UuidGeneratorStub()

    with pytest.raises(module.P1VerificationTransitionNotReady) as caught:
        _execute(
            module.P1VerificationTransitionWriter,
            factory,
            uuid_generator,
            _command(module.P1VerificationTransitionCommand),
        )

    assert str(caught.value) == NOT_READY
    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []
    assert unit_of_work.commit_calls == 0
    assert uuid_generator.calls == 0


@pytest.mark.parametrize(
    "fail_on",
    ["verification", "eligibility", "outbox", "commit"],
)
def test_任一写入或commit失败由同一UoW回滚且不留半成品(fail_on):
    module = _module()
    unit_of_work = RegistrationOutboxUnitOfWorkStub(fail_on=fail_on)
    factory = UnitOfWorkFactoryStub(unit_of_work)

    with pytest.raises(module.P1VerificationTransitionUnavailable) as caught:
        _execute(
            module.P1VerificationTransitionWriter,
            factory,
            UuidGeneratorStub(),
            _command(module.P1VerificationTransitionCommand),
        )

    rendered = "".join(
        traceback.format_exception(
            type(caught.value), caught.value, caught.value.__traceback__
        )
    )
    assert str(caught.value) == "P1 verification transition is unavailable"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "secret sql" not in rendered
    assert "13800138000" not in rendered
    assert "hunter2" not in rendered
    assert unit_of_work.exit_calls == [RuntimeError]
    assert unit_of_work.user.verify_status == "pending"
    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []


def test_前序Verification决定被新决定精确supersede():
    module = _module()
    predecessor = VerificationEvidenceStub()
    unit_of_work = RegistrationOutboxUnitOfWorkStub(
        predecessor=predecessor
    )

    _execute(
        module.P1VerificationTransitionWriter,
        UnitOfWorkFactoryStub(unit_of_work),
        UuidGeneratorStub(),
        _command(module.P1VerificationTransitionCommand),
    )

    assert unit_of_work.verification_adds[0].supersedes_ref == (
        predecessor.decision_ref
    )


@pytest.mark.parametrize(
    "predecessor",
    [
        VerificationEvidenceStub(facts_version=TARGET_FACTS_VERSION),
        VerificationEvidenceStub(verification_epoch=4),
    ],
)
def test_前序Verification版本不递增时FailClosed(predecessor):
    module = _module()
    unit_of_work = RegistrationOutboxUnitOfWorkStub(
        predecessor=predecessor
    )
    uuid_generator = UuidGeneratorStub()

    with pytest.raises(module.P1VerificationTransitionNotReady):
        _execute(
            module.P1VerificationTransitionWriter,
            UnitOfWorkFactoryStub(unit_of_work),
            uuid_generator,
            _command(module.P1VerificationTransitionCommand),
        )

    assert unit_of_work.verification_adds == []
    assert unit_of_work.eligibility_adds == []
    assert unit_of_work.outbox_adds == []
    assert unit_of_work.commit_calls == 0
    assert uuid_generator.calls == 0


def test_commit结果未知时新UoW确认完整五对象并返回稳定重放():
    module = _module()
    first = RegistrationOutboxUnitOfWorkStub(
        commit_error=module.P1VerificationTransitionCommitOutcomeUnknown(
            "secret vendor sql"
        )
    )
    confirmation = RegistrationOutboxUnitOfWorkStub(
        existing=_complete_snapshot(module)
    )
    factory = UnitOfWorkFactoryStub(first, confirmation)
    generator = UuidGeneratorStub()

    result = _execute(
        module.P1VerificationTransitionWriter,
        factory,
        generator,
        _command(module.P1VerificationTransitionCommand),
    )

    assert result.replayed is True
    assert result.registration_event_id == REGISTRATION_EVENT_ID
    assert factory.calls == 2
    assert generator.calls == 4
    assert confirmation.commit_calls == 0


def test_commit结果未知且确认全无时复用原UUID安全重试一次():
    module = _module()
    first = RegistrationOutboxUnitOfWorkStub(
        commit_error=module.P1VerificationTransitionCommitOutcomeUnknown(
            "secret vendor sql"
        )
    )
    retry = RegistrationOutboxUnitOfWorkStub()
    factory = UnitOfWorkFactoryStub(first, retry)
    generator = UuidGeneratorStub()

    result = _execute(
        module.P1VerificationTransitionWriter,
        factory,
        generator,
        _command(module.P1VerificationTransitionCommand),
    )

    assert result.verification_decision_ref == VERIFICATION_DECISION_REF
    assert result.registration_event_id == REGISTRATION_EVENT_ID
    assert retry.verification_adds[0].decision_ref == (
        VERIFICATION_DECISION_REF
    )
    assert retry.outbox_adds[0].event_id == REGISTRATION_EVENT_ID
    assert retry.commit_calls == 1
    assert factory.calls == 2
    assert generator.calls == 4


def test_commit结果未知且确认五对象部分缺失时FailClosed():
    module = _module()
    first = RegistrationOutboxUnitOfWorkStub(
        commit_error=module.P1VerificationTransitionCommitOutcomeUnknown(
            "secret vendor sql"
        )
    )
    partial = RegistrationOutboxUnitOfWorkStub(
        existing=replace(_complete_snapshot(module), outbox=None)
    )

    with pytest.raises(module.P1VerificationTransitionInconsistent):
        _execute(
            module.P1VerificationTransitionWriter,
            UnitOfWorkFactoryStub(first, partial),
            UuidGeneratorStub(),
            _command(module.P1VerificationTransitionCommand),
        )

    assert partial.commit_calls == 0
    assert partial.verification_adds == []
    assert partial.eligibility_adds == []
    assert partial.outbox_adds == []
