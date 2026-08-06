import asyncio
from collections import deque
from uuid import UUID

import pytest

from app.modules.member.application.member_no_allocator import (
    AllocateRegistrationMemberNoCommand,
    AllocationScope,
    AllocationSourceSystem,
    InvalidMemberNoAllocationCommand,
    MemberNoAllocation,
    MemberNoAllocationCommitOutcomeUnknownError,
    MemberNoAllocationConflict,
    MemberNoAllocationFailed,
    MemberNoAllocationKey,
    MemberNoAllocationKeyConflictError,
    MemberNoAllocationNotFoundError,
    MemberNoAllocationOutcomeUnknown,
    MemberNoAllocationPortUnavailableError,
    MemberNoAllocationState,
    MemberNoAllocationUnavailable,
    MemberNoValueConflictError,
    RegistrationMemberNoAllocator,
)
from app.modules.member.value_objects import MemberNo


ALLOCATION_IDS = (
    UUID("01890f3e-7b7d-7cc3-98c8-2f5a12d21234"),
    UUID("01890f3e-7b7d-7cc3-a8c8-2f5a12d25678"),
    UUID("01890f3e-7b7d-7cc3-b8c8-2f5a12d29012"),
)
REQUEST_REF = UUID("12345678-1234-4234-8234-123456789abc")
SECOND_REQUEST_REF = UUID("87654321-4321-4321-8321-cba987654321")


class UuidGeneratorStub:
    def __init__(self, *values):
        self.values = deque(values or ALLOCATION_IDS)
        self.calls = 0

    def generate(self):
        self.calls += 1
        return self.values.popleft()


class RandomBitsStub:
    def __init__(self, *values):
        self.values = deque(values or (0,))
        self.calls = []

    def __call__(self, bit_count):
        self.calls.append(bit_count)
        return self.values.popleft()


class MemoryAllocationStore:
    def __init__(self):
        self.by_key = {}
        self.by_member_no = {}
        self.add_effects = deque()
        self.commit_effects = deque()
        self.insert_attempts = 0

    def persist(self, allocation):
        self.by_key[allocation.key] = allocation
        self.by_member_no[allocation.member_no.value] = allocation


class MemoryAllocationLedger:
    def __init__(self, store, unit_of_work):
        self.store = store
        self.unit_of_work = unit_of_work

    async def get_by_key(self, key):
        try:
            return self.store.by_key[key]
        except KeyError:
            raise MemberNoAllocationNotFoundError from None

    async def add(self, allocation):
        self.store.insert_attempts += 1
        if self.store.add_effects:
            self.store.add_effects.popleft()(self.store, allocation)
        if allocation.key in self.store.by_key:
            raise MemberNoAllocationKeyConflictError
        if allocation.member_no.value in self.store.by_member_no:
            raise MemberNoValueConflictError
        self.unit_of_work.pending = allocation


class MemoryAllocationUnitOfWork:
    def __init__(self, store):
        self.store = store
        self.pending = None
        self.allocations = MemoryAllocationLedger(store, self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.pending = None
        return False

    async def commit(self):
        effect = (
            self.store.commit_effects.popleft()
            if self.store.commit_effects
            else "success"
        )
        if effect == "unknown_missing":
            raise MemberNoAllocationCommitOutcomeUnknownError
        if effect == "unknown_persisted":
            self.store.persist(self.pending)
            raise MemberNoAllocationCommitOutcomeUnknownError
        if callable(effect):
            effect(self.store, self.pending)
        else:
            self.store.persist(self.pending)


def _allocator(store=None, *, ids=ALLOCATION_IDS, random_values=(0,)):
    store = store or MemoryAllocationStore()
    uuid_generator = UuidGeneratorStub(*ids)
    random_bits = RandomBitsStub(*random_values)
    service = RegistrationMemberNoAllocator(
        lambda: MemoryAllocationUnitOfWork(store),
        uuid_generator,
        random_bits,
    )
    return service, store, uuid_generator, random_bits


def _command(user_ref=7, request_ref=REQUEST_REF):
    return AllocateRegistrationMemberNoCommand(
        user_ref=user_ref,
        request_ref=request_ref,
    )


def _allocate(service, command):
    return asyncio.run(service.allocate(command))


def _persisted_allocation(user_ref=7, *, allocation_id=ALLOCATION_IDS[0]):
    return MemberNoAllocation(
        allocation_id=allocation_id,
        key=MemberNoAllocationKey(
            allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
            source_system=AllocationSourceSystem.P1_USER,
            source_ref=user_ref,
        ),
        member_no=MemberNo("M0123456789ABCDEFGHJK"),
        request_ref=REQUEST_REF,
    )


def test_MemberNo生产分配器尚未实现():
    service, _, _, _ = _allocator()

    result = _allocate(service, _command())

    assert result.member_no == MemberNo("M00000000000000000000")


def test_编号格式使用固定前缀二十位Crockford和一百位随机载荷():
    service, _, _, random_bits = _allocator(
        random_values=((1 << 100) - 1,)
    )

    result = _allocate(service, _command())

    assert result.member_no.value == "MZZZZZZZZZZZZZZZZZZZZ"
    assert len(result.member_no.value) == 21
    assert random_bits.calls == [100]


def test_同一用户不同请求引用稳定重放且不生成第二编号():
    service, store, uuid_generator, random_bits = _allocator()

    first = _allocate(service, _command())
    replay = _allocate(service, _command(request_ref=SECOND_REQUEST_REF))

    assert first.member_no == replay.member_no
    assert first.allocation_id == replay.allocation_id
    assert first.replayed is False
    assert replay.replayed is True
    assert store.insert_attempts == 1
    assert uuid_generator.calls == 1
    assert random_bits.calls == [100]


def test_稳定分配键固定为注册引导和P1用户正整数引用():
    service, store, _, _ = _allocator()

    _allocate(service, _command(user_ref=73))

    (key,) = store.by_key
    assert key == MemberNoAllocationKey(
        allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
        source_system=AllocationSourceSystem.P1_USER,
        source_ref=73,
    )
    assert store.by_key[key].state is MemberNoAllocationState.ALLOCATED


@pytest.mark.parametrize("user_ref", [True, 0, -1, "7", 7.0])
def test_用户引用只接受精确正整数(user_ref):
    service, store, _, _ = _allocator()

    with pytest.raises(InvalidMemberNoAllocationCommand):
        _allocate(service, _command(user_ref=user_ref))

    assert store.insert_attempts == 0


@pytest.mark.parametrize("request_ref", [None, str(REQUEST_REF), 7])
def test_请求引用只接受标准库UUID对象(request_ref):
    service, store, _, _ = _allocator()

    with pytest.raises(InvalidMemberNoAllocationCommand):
        _allocate(service, _command(request_ref=request_ref))

    assert store.insert_attempts == 0


def test_allocation_key冲突后在新UoW读取唯一赢家():
    store = MemoryAllocationStore()
    winner = _persisted_allocation()

    def publish_winner_then_conflict(shared, allocation):
        shared.persist(winner)
        raise MemberNoAllocationKeyConflictError

    store.add_effects.append(publish_winner_then_conflict)
    service, _, _, _ = _allocator(store)

    result = _allocate(service, _command())

    assert result.allocation_id == winner.allocation_id
    assert result.member_no == winner.member_no
    assert result.replayed is True
    assert store.insert_attempts == 1


def test_MemberNo冲突最多三次候选写入且第三次成功():
    store = MemoryAllocationStore()

    def collide(shared, allocation):
        raise MemberNoValueConflictError

    store.add_effects.extend((collide, collide))
    service, _, _, random_bits = _allocator(
        store,
        random_values=(0, 1, 2),
    )

    result = _allocate(service, _command())

    assert result.member_no.value == "M00000000000000000002"
    assert result.replayed is False
    assert store.insert_attempts == 3
    assert random_bits.calls == [100, 100, 100]


def test_MemberNo连续三次冲突后停止且不无限重试():
    store = MemoryAllocationStore()

    def collide(shared, allocation):
        raise MemberNoValueConflictError

    store.add_effects.extend((collide, collide, collide))
    service, _, _, random_bits = _allocator(
        store,
        random_values=(0, 1, 2),
    )

    with pytest.raises(MemberNoAllocationConflict):
        _allocate(service, _command())

    assert store.insert_attempts == 3
    assert random_bits.calls == [100, 100, 100]
    assert store.by_key == {}


def test_MemberNo冲突后先确认allocation_key赢家而不盲目重试():
    store = MemoryAllocationStore()
    winner = _persisted_allocation()

    def publish_winner_then_value_conflict(shared, allocation):
        shared.persist(winner)
        raise MemberNoValueConflictError

    store.add_effects.append(publish_winner_then_value_conflict)
    service, _, _, random_bits = _allocator(store)

    result = _allocate(service, _command())

    assert result.allocation_id == winner.allocation_id
    assert result.replayed is True
    assert store.insert_attempts == 1
    assert random_bits.calls == [100]


def test_commit结果未知时只确认已落库候选而不重新分配():
    store = MemoryAllocationStore()
    store.commit_effects.append("unknown_persisted")
    service, _, uuid_generator, random_bits = _allocator(store)

    result = _allocate(service, _command())

    assert result.allocation_id == ALLOCATION_IDS[0]
    assert result.replayed is False
    assert store.insert_attempts == 1
    assert uuid_generator.calls == 1
    assert random_bits.calls == [100]


def test_commit结果未知且查不到事实时返回未知而不重新分配():
    store = MemoryAllocationStore()
    store.commit_effects.append("unknown_missing")
    service, _, uuid_generator, random_bits = _allocator(store)

    with pytest.raises(MemberNoAllocationOutcomeUnknown):
        _allocate(service, _command())

    assert store.insert_attempts == 1
    assert uuid_generator.calls == 1
    assert random_bits.calls == [100]
    assert store.by_key == {}


def test_commit结果未知时确认并返回并发赢家():
    store = MemoryAllocationStore()
    winner = _persisted_allocation(allocation_id=ALLOCATION_IDS[1])

    def replace_with_winner_then_unknown(shared, allocation):
        shared.persist(winner)
        raise MemberNoAllocationCommitOutcomeUnknownError

    store.commit_effects.append(replace_with_winner_then_unknown)
    service, _, _, _ = _allocator(store)

    result = _allocate(service, _command())

    assert result.allocation_id == winner.allocation_id
    assert result.replayed is True
    assert store.insert_attempts == 1


def test_持久化不可用映射为稳定服务异常():
    store = MemoryAllocationStore()

    def unavailable(shared, allocation):
        raise MemberNoAllocationPortUnavailableError

    store.add_effects.append(unavailable)
    service, _, _, _ = _allocator(store)

    with pytest.raises(MemberNoAllocationUnavailable):
        _allocate(service, _command())


@pytest.mark.parametrize("random_value", [True, -1, 1 << 100, "0"])
def test_随机源必须返回一百位范围内的精确整数(random_value):
    service, store, _, _ = _allocator(random_values=(random_value,))

    with pytest.raises(MemberNoAllocationFailed):
        _allocate(service, _command())

    assert store.insert_attempts == 0


def test_allocation_id必须是标准库UUIDv7():
    service, store, _, _ = _allocator(
        ids=(UUID("12345678-1234-4234-8234-123456789abc"),)
    )

    with pytest.raises(MemberNoAllocationFailed):
        _allocate(service, _command())

    assert store.insert_attempts == 0
