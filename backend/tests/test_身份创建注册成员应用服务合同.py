import asyncio
from uuid import UUID

import pytest

from app.modules.member.application.create_registration_member import (
    CreateRegistrationMemberConflict,
    CreateRegistrationMemberFailed,
    CreateRegistrationMemberOutcomeUnknown,
    CreateRegistrationMemberService,
    CreateRegistrationMemberUnavailable,
    CreateRegistrationMemberCommand,
    InvalidCreateRegistrationMemberCommand,
)
from app.modules.member.application.unit_of_work import (
    IdentityUnitOfWorkError,
    IdentityUnitOfWorkUnavailableError,
)
from app.modules.member.entities import Member
from app.modules.member.repository import (
    MemberNotFoundError,
    MemberPersistenceUnavailableError,
    MemberUniquenessConflictError,
)
from app.modules.member.value_objects import CreationSource, MemberNo


MEMBER_ID = UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d21234")
WINNER_ID = UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d25678")


def _member(member_id=MEMBER_ID, member_no="M001"):
    return Member.create(
        member_id=member_id,
        member_no=MemberNo(member_no),
        creation_source=CreationSource.REGISTRATION,
    )


class RepositoryStub:
    def __init__(self, *, get_result=None, add_error=None):
        self.get_result = get_result
        self.add_error = add_error
        self.get_calls = []
        self.add_calls = []

    async def get_by_member_no(self, member_no):
        self.get_calls.append(member_no)
        if isinstance(self.get_result, BaseException):
            raise self.get_result
        return self.get_result

    async def add(self, member):
        self.add_calls.append(member)
        if self.add_error is not None:
            raise self.add_error


class UnitOfWorkStub:
    def __init__(self, repository, *, enter_error=None, commit_error=None):
        self.repository = repository
        self.enter_error = enter_error
        self.commit_error = commit_error
        self.enter_calls = 0
        self.exit_calls = []
        self.commit_calls = 0

    @property
    def members(self):
        return self.repository

    async def __aenter__(self):
        self.enter_calls += 1
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.exit_calls.append(exc_type)
        return False

    async def commit(self):
        self.commit_calls += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self):
        raise AssertionError("application service must not call rollback directly")


class UnitOfWorkFactoryStub:
    def __init__(self, *units_of_work):
        self.units_of_work = list(units_of_work)
        self.calls = 0

    def __call__(self):
        unit_of_work = self.units_of_work[self.calls]
        self.calls += 1
        return unit_of_work


class UuidGeneratorStub:
    def __init__(self, value=MEMBER_ID, *, error=None):
        self.value = value
        self.error = error
        self.calls = 0

    def generate(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.value


def _execute(service, member_no="M001"):
    return asyncio.run(
        service.execute(CreateRegistrationMemberCommand(member_no=member_no))
    )


def test_创建注册成员应用服务尚未实现():
    repository = RepositoryStub(get_result=MemberNotFoundError("missing"))
    unit_of_work = UnitOfWorkStub(repository)
    factory = UnitOfWorkFactoryStub(unit_of_work)
    generator = UuidGeneratorStub()

    result = _execute(CreateRegistrationMemberService(factory, generator))

    assert result.member_id == MEMBER_ID
    assert type(result.member_id) is UUID
    assert result.member_id.version == 7
    assert result.member_no == "M001"
    assert result.creation_source == "registration"
    assert result.status == "created"
    assert result.replayed is False
    assert generator.calls == 1
    assert len(repository.add_calls) == 1
    assert repository.add_calls[0].member_id == MEMBER_ID
    assert unit_of_work.commit_calls == 1
    assert factory.calls == 1


def test_相同MemberNo顺序重放不生成UUID不写入不提交():
    repository = RepositoryStub(get_result=_member())
    unit_of_work = UnitOfWorkStub(repository)
    factory = UnitOfWorkFactoryStub(unit_of_work)
    generator = UuidGeneratorStub()

    result = _execute(CreateRegistrationMemberService(factory, generator))

    assert result.member_id == MEMBER_ID
    assert result.replayed is True
    assert generator.calls == 0
    assert repository.add_calls == []
    assert unit_of_work.commit_calls == 0


@pytest.mark.parametrize(
    "member_no",
    [None, 7, "", "   ", " M001", "M001 ", "M" * 65],
)
def test_无效MemberNo在UUID和UoW前失败(member_no):
    factory = UnitOfWorkFactoryStub()
    generator = UuidGeneratorStub()
    service = CreateRegistrationMemberService(factory, generator)

    with pytest.raises(InvalidCreateRegistrationMemberCommand) as caught:
        _execute(service, member_no)

    assert str(caught.value) == "create registration member command is invalid"
    assert factory.calls == 0
    assert generator.calls == 0


def test_非命令对象在UUID和UoW前失败():
    factory = UnitOfWorkFactoryStub()
    generator = UuidGeneratorStub()
    service = CreateRegistrationMemberService(factory, generator)

    with pytest.raises(InvalidCreateRegistrationMemberCommand) as caught:
        asyncio.run(service.execute(object()))

    assert str(caught.value) == "create registration member command is invalid"
    assert factory.calls == 0
    assert generator.calls == 0


def test_唯一约束竞争返回数据库赢家且不二次写入():
    first_repository = RepositoryStub(
        get_result=MemberNotFoundError("missing"),
        add_error=MemberUniquenessConflictError("secret sql"),
    )
    winner = _member(WINNER_ID)
    confirmation_repository = RepositoryStub(get_result=winner)
    first_uow = UnitOfWorkStub(first_repository)
    confirmation_uow = UnitOfWorkStub(confirmation_repository)
    factory = UnitOfWorkFactoryStub(first_uow, confirmation_uow)

    result = _execute(
        CreateRegistrationMemberService(factory, UuidGeneratorStub())
    )

    assert result.member_id == WINNER_ID
    assert result.replayed is True
    assert len(first_repository.add_calls) == 1
    assert confirmation_repository.add_calls == []
    assert first_uow.commit_calls == 0
    assert confirmation_uow.commit_calls == 0
    assert factory.calls == 2


@pytest.mark.parametrize(
    ("confirmation_error", "expected_error"),
    [
        (MemberNotFoundError("missing"), CreateRegistrationMemberConflict),
        (
            MemberPersistenceUnavailableError("secret dsn"),
            CreateRegistrationMemberUnavailable,
        ),
    ],
)
def test_唯一约束确认失败不会二次写入(
    confirmation_error, expected_error
):
    first_repository = RepositoryStub(
        get_result=MemberNotFoundError("missing"),
        add_error=MemberUniquenessConflictError("secret sql"),
    )
    confirmation_repository = RepositoryStub(get_result=confirmation_error)
    factory = UnitOfWorkFactoryStub(
        UnitOfWorkStub(first_repository),
        UnitOfWorkStub(confirmation_repository),
    )
    service = CreateRegistrationMemberService(factory, UuidGeneratorStub())

    with pytest.raises(expected_error) as caught:
        _execute(service)

    assert "secret" not in str(caught.value)
    assert len(first_repository.add_calls) == 1
    assert confirmation_repository.add_calls == []
    assert factory.calls == 2


def test_唯一约束确认返回非法对象时映射Failed():
    first_repository = RepositoryStub(
        get_result=MemberNotFoundError("missing"),
        add_error=MemberUniquenessConflictError("secret sql"),
    )
    confirmation_repository = RepositoryStub(get_result=object())
    factory = UnitOfWorkFactoryStub(
        UnitOfWorkStub(first_repository),
        UnitOfWorkStub(confirmation_repository),
    )

    with pytest.raises(CreateRegistrationMemberFailed) as caught:
        _execute(
            CreateRegistrationMemberService(factory, UuidGeneratorStub())
        )

    assert str(caught.value) == "registration member creation failed"
    assert confirmation_repository.add_calls == []


@pytest.mark.parametrize(
    ("confirmed_member", "expected_replayed"),
    [(_member(MEMBER_ID), False), (_member(WINNER_ID), True)],
)
def test_commit结果不确定时确认已持久化结果(
    confirmed_member, expected_replayed
):
    first_repository = RepositoryStub(get_result=MemberNotFoundError("missing"))
    confirmation_repository = RepositoryStub(get_result=confirmed_member)
    first_uow = UnitOfWorkStub(
        first_repository,
        commit_error=IdentityUnitOfWorkUnavailableError("secret dsn"),
    )
    confirmation_uow = UnitOfWorkStub(confirmation_repository)
    factory = UnitOfWorkFactoryStub(first_uow, confirmation_uow)
    generator = UuidGeneratorStub()

    result = _execute(CreateRegistrationMemberService(factory, generator))

    assert result.member_id == confirmed_member.member_id
    assert result.replayed is expected_replayed
    assert generator.calls == 1
    assert len(first_repository.add_calls) == 1
    assert confirmation_repository.add_calls == []
    assert first_uow.commit_calls == 1
    assert confirmation_uow.commit_calls == 0
    assert factory.calls == 2


@pytest.mark.parametrize(
    "confirmation_error",
    [
        MemberNotFoundError("missing"),
        MemberPersistenceUnavailableError("secret dsn"),
    ],
)
def test_commit结果确认失败映射OutcomeUnknown且不二次写入(
    confirmation_error,
):
    first_repository = RepositoryStub(get_result=MemberNotFoundError("missing"))
    confirmation_repository = RepositoryStub(get_result=confirmation_error)
    first_uow = UnitOfWorkStub(
        first_repository,
        commit_error=IdentityUnitOfWorkError("secret transaction"),
    )
    factory = UnitOfWorkFactoryStub(
        first_uow,
        UnitOfWorkStub(confirmation_repository),
    )
    generator = UuidGeneratorStub()
    service = CreateRegistrationMemberService(factory, generator)

    with pytest.raises(CreateRegistrationMemberOutcomeUnknown) as caught:
        _execute(service)

    assert str(caught.value) == "registration member creation outcome is unknown"
    assert "secret" not in str(caught.value)
    assert generator.calls == 1
    assert len(first_repository.add_calls) == 1
    assert confirmation_repository.add_calls == []
    assert first_uow.commit_calls == 1
    assert factory.calls == 2


def test_commit结果确认返回非法对象时映射OutcomeUnknown():
    first_repository = RepositoryStub(get_result=MemberNotFoundError("missing"))
    first_uow = UnitOfWorkStub(
        first_repository,
        commit_error=IdentityUnitOfWorkError("secret transaction"),
    )
    confirmation_repository = RepositoryStub(get_result=object())
    factory = UnitOfWorkFactoryStub(
        first_uow,
        UnitOfWorkStub(confirmation_repository),
    )

    with pytest.raises(CreateRegistrationMemberOutcomeUnknown) as caught:
        _execute(
            CreateRegistrationMemberService(factory, UuidGeneratorStub())
        )

    assert str(caught.value) == "registration member creation outcome is unknown"
    assert confirmation_repository.add_calls == []
    assert first_uow.commit_calls == 1


@pytest.mark.parametrize(
    ("repository_error", "expected_error", "expected_message"),
    [
        (
            MemberPersistenceUnavailableError("secret dsn"),
            CreateRegistrationMemberUnavailable,
            "identity persistence is unavailable",
        ),
        (
            RuntimeError("secret sql"),
            CreateRegistrationMemberFailed,
            "registration member creation failed",
        ),
    ],
)
def test_初始查询异常采用白名单映射且不泄漏底层信息(
    repository_error, expected_error, expected_message
):
    repository = RepositoryStub(get_result=repository_error)
    factory = UnitOfWorkFactoryStub(UnitOfWorkStub(repository))
    generator = UuidGeneratorStub()
    service = CreateRegistrationMemberService(factory, generator)

    with pytest.raises(expected_error) as caught:
        _execute(service)

    assert str(caught.value) == expected_message
    assert "secret" not in str(caught.value)
    assert generator.calls == 0
    assert repository.add_calls == []


def test_UoW进入不可用映射Unavailable且不生成UUID():
    repository = RepositoryStub()
    unit_of_work = UnitOfWorkStub(
        repository,
        enter_error=IdentityUnitOfWorkUnavailableError("secret dsn"),
    )
    factory = UnitOfWorkFactoryStub(unit_of_work)
    generator = UuidGeneratorStub()

    with pytest.raises(CreateRegistrationMemberUnavailable) as caught:
        _execute(CreateRegistrationMemberService(factory, generator))

    assert str(caught.value) == "identity persistence is unavailable"
    assert "secret" not in str(caught.value)
    assert generator.calls == 0
    assert repository.add_calls == []
    assert unit_of_work.commit_calls == 0


@pytest.mark.parametrize(
    ("add_error", "expected_error", "expected_message"),
    [
        (
            MemberPersistenceUnavailableError("secret dsn"),
            CreateRegistrationMemberUnavailable,
            "identity persistence is unavailable",
        ),
        (
            RuntimeError("secret sql"),
            CreateRegistrationMemberFailed,
            "registration member creation failed",
        ),
    ],
)
def test_写入异常采用白名单映射且不提交(
    add_error, expected_error, expected_message
):
    repository = RepositoryStub(
        get_result=MemberNotFoundError("missing"),
        add_error=add_error,
    )
    unit_of_work = UnitOfWorkStub(repository)
    service = CreateRegistrationMemberService(
        UnitOfWorkFactoryStub(unit_of_work),
        UuidGeneratorStub(),
    )

    with pytest.raises(expected_error) as caught:
        _execute(service)

    assert str(caught.value) == expected_message
    assert "secret" not in str(caught.value)
    assert len(repository.add_calls) == 1
    assert unit_of_work.commit_calls == 0


def test_UUID生成异常映射Failed且不写入不提交():
    repository = RepositoryStub(get_result=MemberNotFoundError("missing"))
    unit_of_work = UnitOfWorkStub(repository)
    generator = UuidGeneratorStub(error=RuntimeError("secret generator"))
    service = CreateRegistrationMemberService(
        UnitOfWorkFactoryStub(unit_of_work),
        generator,
    )

    with pytest.raises(CreateRegistrationMemberFailed) as caught:
        _execute(service)

    assert str(caught.value) == "registration member creation failed"
    assert "secret" not in str(caught.value)
    assert generator.calls == 1
    assert repository.add_calls == []
    assert unit_of_work.commit_calls == 0
