import asyncio
from collections import deque
from importlib import import_module
from uuid import UUID

import pytest

from app.modules.member.entities import Member
from app.modules.member.value_objects import CreationSource, MemberNo


EXPECTED_RED = "Identity atomic registration bootstrap is not implemented"
USER_REF = 73
EVENT_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20001")
ELIGIBILITY_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20002")
ALLOCATION_REF = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20003")
MEMBER_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20004")
LINK_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20005")
RECORD_ID = UUID("01890f3e-7b7d-7cc3-88c8-2f5a12d20006")


def _api():
    try:
        module = import_module(
            "app.modules.member.application.registration_bootstrap"
        )
        ports = import_module(
            "app.modules.member.application.registration_bootstrap_ports"
        )
        return module, ports
    except (ImportError, AttributeError):
        pytest.fail(EXPECTED_RED)


class EligibilityReaderStub:
    def __init__(self, proof):
        self.proof = proof
        self.calls = []

    async def get_current_eligible(self, *, decision_ref, user_ref):
        self.calls.append((decision_ref, user_ref))
        return self.proof


class AllocationReaderStub:
    def __init__(self, proof):
        self.proof = proof
        self.calls = []

    async def get_allocated(self, *, allocation_ref, user_ref):
        self.calls.append((allocation_ref, user_ref))
        return self.proof


class RepositoryStub:
    def __init__(self, missing_error):
        self.missing_error = missing_error
        self.values = {}
        self.add_calls = []
        self.add_error = None

    async def get(self, key):
        if key not in self.values:
            raise self.missing_error()
        return self.values[key]

    async def add(self, value):
        self.add_calls.append(value)
        if self.add_error is not None:
            raise self.add_error


class UnitOfWorkStub:
    def __init__(self, ports):
        self.members = RepositoryStub(ports.RegistrationBootstrapNotFound)
        self.self_links = RepositoryStub(ports.RegistrationBootstrapNotFound)
        self.bootstrap_records = RepositoryStub(
            ports.RegistrationBootstrapNotFound
        )
        self.commit_calls = 0
        self.commit_error = None
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error


class UnitOfWorkFactoryStub:
    def __init__(self, *values):
        self.values = deque(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.values.popleft()


class UuidGeneratorStub:
    def __init__(self):
        self.values = deque((MEMBER_ID, LINK_ID, RECORD_ID))
        self.calls = 0

    def generate(self):
        self.calls += 1
        return self.values.popleft()


def _fixtures():
    module, ports = _api()
    eligibility = ports.RegistrationEligibilityProof(
        decision_ref=ELIGIBILITY_REF,
        user_ref=USER_REF,
        decision="APPROVED",
        policy_version="v1",
    )
    allocation = ports.RegistrationMemberNoAllocationProof(
        allocation_ref=ALLOCATION_REF,
        user_ref=USER_REF,
        member_no="M0123456789ABCDEFGHJK",
    )
    return module, ports, eligibility, allocation


def _command(module, **changes):
    values = {
        "user_ref": USER_REF,
        "registration_event_id": EVENT_REF,
        "eligibility_decision_ref": ELIGIBILITY_REF,
        "member_no_allocation_ref": ALLOCATION_REF,
    }
    values.update(changes)
    return module.RegistrationIdentityBootstrapCommand(**values)


def _execute(service, command):
    return asyncio.run(service.execute(command))


def _record(ports, **changes):
    values = {
        "record_id": RECORD_ID,
        "user_ref": USER_REF,
        "member_id": MEMBER_ID,
        "self_link_id": LINK_ID,
        "registration_event_id": EVENT_REF,
        "eligibility_decision_ref": ELIGIBILITY_REF,
        "member_no_allocation_ref": ALLOCATION_REF,
        "member_no": "M0123456789ABCDEFGHJK",
        "bootstrap_scope": "REGISTRATION_VERIFIED",
        "source_system": "P1_USER",
        "source_ref": USER_REF,
        "decision": "APPROVED",
        "policy_version": "v1",
    }
    values.update(changes)
    return ports.RegistrationBootstrapRecord(**values)


def _seed_complete(unit_of_work, ports, *, record=None):
    record = record or _record(ports)
    unit_of_work.bootstrap_records.values[USER_REF] = record
    unit_of_work.members.values[record.member_no] = Member.create(
        member_id=record.member_id,
        member_no=MemberNo(record.member_no),
        creation_source=CreationSource.REGISTRATION,
    )
    unit_of_work.self_links.values[USER_REF] = ports.UserMemberSelfLink(
        link_id=record.self_link_id,
        user_ref=record.user_ref,
        member_id=record.member_id,
        eligibility_decision_ref=record.eligibility_decision_ref,
        establishment_basis="REGISTRATION_VERIFIED_BOOTSTRAP",
        establishment_record_ref=record.record_id,
    )
    return record


def test_身份原子注册Bootstrap尚未实现():
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=UuidGeneratorStub(),
    )

    result = _execute(service, _command(module))

    assert result.user_ref == USER_REF
    assert result.member_id == MEMBER_ID
    assert result.member_no == "M0123456789ABCDEFGHJK"
    assert result.self_link_id == LINK_ID
    assert result.bootstrap_record_id == RECORD_ID
    assert result.replayed is False
    assert unit_of_work.commit_calls == 1
    assert len(unit_of_work.members.add_calls) == 1
    assert len(unit_of_work.self_links.add_calls) == 1
    assert len(unit_of_work.bootstrap_records.add_calls) == 1


def test_ProofReaders位于Identity事务之外且主体与引用精确绑定():
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    eligibility_reader = EligibilityReaderStub(eligibility)
    allocation_reader = AllocationReaderStub(allocation)
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=eligibility_reader,
        allocation_reader=allocation_reader,
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=UuidGeneratorStub(),
    )

    _execute(service, _command(module))

    assert eligibility_reader.calls == [(ELIGIBILITY_REF, USER_REF)]
    assert allocation_reader.calls == [(ALLOCATION_REF, USER_REF)]
    assert unit_of_work.entered is True


@pytest.mark.parametrize("proof_name", ["eligibility", "allocation"])
def test_Proof主体漂移必须在进入UoW前FailClosed且零写入(proof_name):
    module, ports, eligibility, allocation = _fixtures()
    if proof_name == "eligibility":
        eligibility = ports.RegistrationEligibilityProof(
            decision_ref=ELIGIBILITY_REF,
            user_ref=USER_REF + 1,
            decision="APPROVED",
            policy_version="v1",
        )
    else:
        allocation = ports.RegistrationMemberNoAllocationProof(
            allocation_ref=ALLOCATION_REF,
            user_ref=USER_REF + 1,
            member_no="M0123456789ABCDEFGHJK",
        )
    unit_of_work = UnitOfWorkStub(ports)
    factory = UnitOfWorkFactoryStub(unit_of_work)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=factory,
        uuid_generator=generator,
    )

    with pytest.raises(module.RegistrationIdentityBootstrapInconsistent):
        _execute(service, _command(module))

    assert factory.calls == 0
    assert generator.calls == 0


def test_同一CanonicalSource精确重放零新增零提交():
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    record = _seed_complete(unit_of_work, ports)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=generator,
    )

    result = _execute(service, _command(module))

    assert result.replayed is True
    assert result.member_id == MEMBER_ID
    assert generator.calls == 0
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.members.add_calls == []
    assert unit_of_work.self_links.add_calls == []
    assert unit_of_work.bootstrap_records.add_calls == []


def test_同一P1User的新事件引用仍按CanonicalSource稳定重放():
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    _seed_complete(unit_of_work, ports)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=generator,
    )

    result = _execute(
        service,
        _command(
            module,
            registration_event_id=UUID(
                "01890f3e-7b7d-7cc3-a8c8-2f5a12d29998"
            ),
        ),
    )

    assert result.replayed is True
    assert result.bootstrap_record_id == RECORD_ID
    assert generator.calls == 0
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.members.add_calls == []
    assert unit_of_work.self_links.add_calls == []
    assert unit_of_work.bootstrap_records.add_calls == []


def test_CanonicalSource已有不同Proof时冲突且零写入():
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    _seed_complete(
        unit_of_work,
        ports,
        record=_record(
            ports,
            eligibility_decision_ref=UUID(
                "01890f3e-7b7d-7cc3-98c8-2f5a12d29999"
            ),
        ),
    )
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=generator,
    )

    with pytest.raises(module.RegistrationIdentityBootstrapConflict):
        _execute(service, _command(module))

    assert generator.calls == 0
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.members.add_calls == []


@pytest.mark.parametrize("missing", ["member", "self_link"])
def test_重放时三对象任一缺失必须FailClosed且零副作用(missing):
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    _seed_complete(unit_of_work, ports)
    if missing == "member":
        unit_of_work.members.values.clear()
    else:
        unit_of_work.self_links.values.clear()
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=generator,
    )

    with pytest.raises(module.RegistrationIdentityBootstrapInconsistent):
        _execute(service, _command(module))

    assert generator.calls == 0
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.members.add_calls == []
    assert unit_of_work.self_links.add_calls == []
    assert unit_of_work.bootstrap_records.add_calls == []


@pytest.mark.parametrize(
    "mismatch",
    [
        "member_id",
        "link_member_id",
        "link_id",
        "link_proof",
        "link_record",
    ],
)
def test_重放时三对象任一矛盾必须FailClosed且零副作用(mismatch):
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    record = _seed_complete(unit_of_work, ports)
    different = UUID("01890f3e-7b7d-7cc3-b8c8-2f5a12d29997")
    if mismatch == "member_id":
        unit_of_work.members.values[record.member_no] = Member.create(
            member_id=different,
            member_no=MemberNo(record.member_no),
            creation_source=CreationSource.REGISTRATION,
        )
    else:
        unit_of_work.self_links.values[USER_REF] = ports.UserMemberSelfLink(
            link_id=different if mismatch == "link_id" else LINK_ID,
            user_ref=USER_REF,
            member_id=different if mismatch == "link_member_id" else MEMBER_ID,
            eligibility_decision_ref=(
                different if mismatch == "link_proof" else ELIGIBILITY_REF
            ),
            establishment_basis="REGISTRATION_VERIFIED_BOOTSTRAP",
            establishment_record_ref=(
                different if mismatch == "link_record" else RECORD_ID
            ),
        )
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=generator,
    )

    with pytest.raises(module.RegistrationIdentityBootstrapInconsistent):
        _execute(service, _command(module))

    assert generator.calls == 0
    assert unit_of_work.commit_calls == 0
    assert unit_of_work.members.add_calls == []
    assert unit_of_work.self_links.add_calls == []
    assert unit_of_work.bootstrap_records.add_calls == []


@pytest.mark.parametrize("repository_name", ["members", "self_links", "bootstrap_records"])
def test_任一写入失败不提交并由同一UoW回滚(repository_name):
    module, ports, eligibility, allocation = _fixtures()
    unit_of_work = UnitOfWorkStub(ports)
    getattr(unit_of_work, repository_name).add_error = (
        ports.RegistrationBootstrapPersistenceError("secret sql")
    )
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=UnitOfWorkFactoryStub(unit_of_work),
        uuid_generator=UuidGeneratorStub(),
    )

    with pytest.raises(module.RegistrationIdentityBootstrapFailed) as caught:
        _execute(service, _command(module))

    assert str(caught.value) == "identity registration bootstrap failed"
    assert "secret" not in str(caught.value)
    assert unit_of_work.commit_calls == 0


def test_CommitOutcomeUnknown只用新UoW确认且不二次写入():
    module, ports, eligibility, allocation = _fixtures()
    first = UnitOfWorkStub(ports)
    first.commit_error = ports.RegistrationBootstrapCommitOutcomeUnknown()
    confirmation = UnitOfWorkStub(ports)
    _seed_complete(confirmation, ports)
    factory = UnitOfWorkFactoryStub(first, confirmation)
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=factory,
        uuid_generator=UuidGeneratorStub(),
    )

    result = _execute(service, _command(module))

    assert result.replayed is False
    assert factory.calls == 2
    assert first.commit_calls == 1
    assert confirmation.commit_calls == 0
    assert confirmation.members.add_calls == []
    assert confirmation.self_links.add_calls == []
    assert confirmation.bootstrap_records.add_calls == []


@pytest.mark.parametrize(
    "failure_mode", ["member_missing", "link_missing", "link_record_mismatch"]
)
def test_CommitOutcomeUnknown确认时三对象不完整保持OutcomeUnknown(failure_mode):
    module, ports, eligibility, allocation = _fixtures()
    first = UnitOfWorkStub(ports)
    first.commit_error = ports.RegistrationBootstrapCommitOutcomeUnknown()
    confirmation = UnitOfWorkStub(ports)
    record = _seed_complete(confirmation, ports)
    if failure_mode == "member_missing":
        confirmation.members.values.clear()
    elif failure_mode == "link_missing":
        confirmation.self_links.values.clear()
    else:
        confirmation.self_links.values[USER_REF] = ports.UserMemberSelfLink(
            link_id=record.self_link_id,
            user_ref=record.user_ref,
            member_id=record.member_id,
            eligibility_decision_ref=record.eligibility_decision_ref,
            establishment_basis="REGISTRATION_VERIFIED_BOOTSTRAP",
            establishment_record_ref=UUID(
                "01890f3e-7b7d-7cc3-b8c8-2f5a12d29997"
            ),
        )
    factory = UnitOfWorkFactoryStub(first, confirmation)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=factory,
        uuid_generator=generator,
    )

    with pytest.raises(module.RegistrationIdentityBootstrapOutcomeUnknown):
        _execute(service, _command(module))

    assert factory.calls == 2
    assert generator.calls == 3
    assert confirmation.members.add_calls == []
    assert confirmation.self_links.add_calls == []
    assert confirmation.bootstrap_records.add_calls == []
    assert confirmation.commit_calls == 0


def test_唯一约束竞争后使用新UoW确认完整赢家且不二次写入():
    module, ports, eligibility, allocation = _fixtures()
    failed = UnitOfWorkStub(ports)
    failed.members.add_error = ports.RegistrationBootstrapConflict(
        "concurrent winner"
    )
    confirmation = UnitOfWorkStub(ports)
    _seed_complete(confirmation, ports)
    factory = UnitOfWorkFactoryStub(failed, confirmation)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=factory,
        uuid_generator=generator,
    )

    result = _execute(service, _command(module))

    assert result.replayed is True
    assert result.member_id == MEMBER_ID
    assert factory.calls == 2
    assert failed.commit_calls == 0
    assert len(failed.members.add_calls) == 1
    assert confirmation.members.add_calls == []
    assert confirmation.self_links.add_calls == []
    assert confirmation.bootstrap_records.add_calls == []
    assert confirmation.commit_calls == 0


@pytest.mark.parametrize("late_visible", ["member", "self_link"])
def test_并发赢家在同一事务后续查询可见时使用新UoW确认稳定重放(late_visible):
    module, ports, eligibility, allocation = _fixtures()
    failed = UnitOfWorkStub(ports)
    winner = _seed_complete(failed, ports)
    failed.bootstrap_records.values.clear()
    if late_visible == "member":
        failed.self_links.values.clear()
    else:
        failed.members.values.clear()
    confirmation = UnitOfWorkStub(ports)
    _seed_complete(confirmation, ports)
    factory = UnitOfWorkFactoryStub(failed, confirmation)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=factory,
        uuid_generator=generator,
    )

    result = _execute(service, _command(module))

    assert result.replayed is True
    assert result.member_id == winner.member_id
    assert factory.calls == 2
    assert generator.calls == 0
    assert failed.members.add_calls == []
    assert failed.self_links.add_calls == []
    assert failed.bootstrap_records.add_calls == []
    assert failed.commit_calls == 0
    assert confirmation.members.add_calls == []
    assert confirmation.self_links.add_calls == []
    assert confirmation.bootstrap_records.add_calls == []
    assert confirmation.commit_calls == 0


@pytest.mark.parametrize("failure_mode", ["member_missing", "link_missing"])
def test_唯一约束竞争后新UoW找不到完整赢家仍为冲突(failure_mode):
    module, ports, eligibility, allocation = _fixtures()
    failed = UnitOfWorkStub(ports)
    failed.members.add_error = ports.RegistrationBootstrapConflict(
        "concurrent winner"
    )
    confirmation = UnitOfWorkStub(ports)
    _seed_complete(confirmation, ports)
    if failure_mode == "member_missing":
        confirmation.members.values.clear()
    else:
        confirmation.self_links.values.clear()
    factory = UnitOfWorkFactoryStub(failed, confirmation)
    generator = UuidGeneratorStub()
    service = module.RegistrationIdentityBootstrapService(
        eligibility_reader=EligibilityReaderStub(eligibility),
        allocation_reader=AllocationReaderStub(allocation),
        unit_of_work_factory=factory,
        uuid_generator=generator,
    )

    with pytest.raises(module.RegistrationIdentityBootstrapConflict):
        _execute(service, _command(module))

    assert factory.calls == 2
    assert generator.calls == 3
    assert confirmation.members.add_calls == []
    assert confirmation.self_links.add_calls == []
    assert confirmation.bootstrap_records.add_calls == []
    assert confirmation.commit_calls == 0
