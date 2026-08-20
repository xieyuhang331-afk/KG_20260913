import secrets
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Callable, Protocol, Self
from uuid import UUID

from app.core.uuid_generator import UuidGenerator

from ..value_objects import MemberNo


_MEMBER_NO_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_MEMBER_NO_RANDOM_BITS = 100
_MEMBER_NO_PAYLOAD_LENGTH = 20
_MAX_CANDIDATE_INSERT_ATTEMPTS = 3


def generate_member_no_candidate(
    random_bits: Callable[[int], int] = secrets.randbits,
) -> MemberNo:
    value = random_bits(_MEMBER_NO_RANDOM_BITS)
    if type(value) is not int or value < 0 or value >= 1 << _MEMBER_NO_RANDOM_BITS:
        raise MemberNoAllocationFailed("member number generation failed")
    encoded = ["0"] * _MEMBER_NO_PAYLOAD_LENGTH
    for index in range(_MEMBER_NO_PAYLOAD_LENGTH - 1, -1, -1):
        encoded[index] = _MEMBER_NO_ALPHABET[value & 31]
        value >>= 5
    return MemberNo("M" + "".join(encoded))


class MemberNoAllocationError(RuntimeError):
    pass


class InvalidMemberNoAllocationCommand(MemberNoAllocationError):
    pass


class MemberNoAllocationConflict(MemberNoAllocationError):
    pass


class MemberNoAllocationOutcomeUnknown(MemberNoAllocationError):
    pass


class MemberNoAllocationUnavailable(MemberNoAllocationError):
    pass


class MemberNoAllocationFailed(MemberNoAllocationError):
    pass


class MemberNoAllocationPortError(RuntimeError):
    pass


class MemberNoAllocationNotFoundError(MemberNoAllocationPortError):
    pass


class MemberNoAllocationKeyConflictError(MemberNoAllocationPortError):
    pass


class MemberNoValueConflictError(MemberNoAllocationPortError):
    pass


class MemberNoAllocationPortUnavailableError(MemberNoAllocationPortError):
    pass


class MemberNoAllocationTransactionError(RuntimeError):
    pass


class MemberNoAllocationCommitOutcomeUnknownError(
    MemberNoAllocationTransactionError
):
    pass


class MemberNoAllocationTransactionUnavailableError(
    MemberNoAllocationTransactionError
):
    pass


class AllocationScope(str, Enum):
    REGISTRATION_BOOTSTRAP = "registration_bootstrap"


class AllocationSourceSystem(str, Enum):
    P1_USER = "p1_user"


class MemberNoAllocationState(str, Enum):
    ALLOCATED = "allocated"


@dataclass(frozen=True, slots=True)
class MemberNoAllocationKey:
    allocation_scope: AllocationScope
    source_system: AllocationSourceSystem
    source_ref: int


@dataclass(frozen=True, slots=True)
class MemberNoAllocation:
    allocation_id: UUID
    key: MemberNoAllocationKey
    member_no: MemberNo
    request_ref: UUID
    state: MemberNoAllocationState = MemberNoAllocationState.ALLOCATED


@dataclass(frozen=True, slots=True)
class AllocateRegistrationMemberNoCommand:
    user_ref: int
    request_ref: UUID


@dataclass(frozen=True, slots=True)
class MemberNoAllocationResult:
    allocation_id: UUID
    member_no: MemberNo
    replayed: bool


class MemberNoAllocationLedger(Protocol):
    async def get_by_key(
        self, key: MemberNoAllocationKey
    ) -> MemberNoAllocation: ...

    async def add(self, allocation: MemberNoAllocation) -> None: ...


class MemberNoAllocationUnitOfWork(Protocol):
    @property
    def allocations(self) -> MemberNoAllocationLedger: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool: ...

    async def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _CommitOutcomeUncertain(Exception):
    candidate: MemberNoAllocation


class RegistrationMemberNoAllocator:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], MemberNoAllocationUnitOfWork],
        uuid_generator: UuidGenerator,
        random_bits: Callable[[int], int] = secrets.randbits,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._uuid_generator = uuid_generator
        self._random_bits = random_bits

    async def allocate(
        self, command: AllocateRegistrationMemberNoCommand
    ) -> MemberNoAllocationResult:
        key = self._validate_command(command)

        try:
            for attempt in range(_MAX_CANDIDATE_INSERT_ATTEMPTS):
                try:
                    return await self._allocate_once(key, command.request_ref)
                except MemberNoAllocationKeyConflictError:
                    return await self._resolve_key_conflict(key)
                except MemberNoValueConflictError:
                    winner = await self._find_winner(key)
                    if winner is not None:
                        return self._result(winner, replayed=True)
                    if attempt == _MAX_CANDIDATE_INSERT_ATTEMPTS - 1:
                        raise MemberNoAllocationConflict(
                            "member number allocation conflict"
                        ) from None
                except _CommitOutcomeUncertain as uncertainty:
                    return await self._confirm_commit(key, uncertainty.candidate)
        except MemberNoAllocationError:
            raise
        except (
            MemberNoAllocationPortUnavailableError,
            MemberNoAllocationTransactionUnavailableError,
        ):
            raise MemberNoAllocationUnavailable(
                "member number allocation is unavailable"
            ) from None
        except (
            MemberNoAllocationPortError,
            MemberNoAllocationTransactionError,
        ):
            raise MemberNoAllocationFailed(
                "member number allocation failed"
            ) from None
        except Exception:
            raise MemberNoAllocationFailed(
                "member number allocation failed"
            ) from None

        raise MemberNoAllocationFailed(
            "member number allocation failed"
        )

    async def _allocate_once(
        self,
        key: MemberNoAllocationKey,
        request_ref: UUID,
    ) -> MemberNoAllocationResult:
        async with self._unit_of_work_factory() as unit_of_work:
            try:
                existing = await unit_of_work.allocations.get_by_key(key)
            except MemberNoAllocationNotFoundError:
                pass
            else:
                return self._result(existing, replayed=True)

            candidate = MemberNoAllocation(
                allocation_id=self._generate_allocation_id(),
                key=key,
                member_no=self._generate_member_no(),
                request_ref=request_ref,
            )
            await unit_of_work.allocations.add(candidate)
            try:
                await unit_of_work.commit()
            except MemberNoAllocationCommitOutcomeUnknownError:
                raise _CommitOutcomeUncertain(candidate) from None
            return self._result(candidate, replayed=False)

    async def _resolve_key_conflict(
        self, key: MemberNoAllocationKey
    ) -> MemberNoAllocationResult:
        winner = await self._find_winner(key)
        if winner is None:
            raise MemberNoAllocationConflict(
                "member number allocation key conflicts with persisted state"
            )
        return self._result(winner, replayed=True)

    async def _confirm_commit(
        self,
        key: MemberNoAllocationKey,
        candidate: MemberNoAllocation,
    ) -> MemberNoAllocationResult:
        try:
            winner = await self._read_by_key(key)
        except Exception:
            raise MemberNoAllocationOutcomeUnknown(
                "member number allocation outcome is unknown"
            ) from None
        if winner is None:
            raise MemberNoAllocationOutcomeUnknown(
                "member number allocation outcome is unknown"
            )
        return self._result(
            winner,
            replayed=winner.allocation_id != candidate.allocation_id,
        )

    async def _find_winner(
        self, key: MemberNoAllocationKey
    ) -> MemberNoAllocation | None:
        return await self._read_by_key(key)

    async def _read_by_key(
        self, key: MemberNoAllocationKey
    ) -> MemberNoAllocation | None:
        async with self._unit_of_work_factory() as unit_of_work:
            try:
                return await unit_of_work.allocations.get_by_key(key)
            except MemberNoAllocationNotFoundError:
                return None

    def _generate_allocation_id(self) -> UUID:
        value = self._uuid_generator.generate()
        if type(value) is not UUID or value.version != 7:
            raise MemberNoAllocationFailed(
                "member number allocation id generation failed"
            )
        return value

    def _generate_member_no(self) -> MemberNo:
        return generate_member_no_candidate(self._random_bits)

    @staticmethod
    def _validate_command(
        command: AllocateRegistrationMemberNoCommand,
    ) -> MemberNoAllocationKey:
        if (
            type(command) is not AllocateRegistrationMemberNoCommand
            or type(command.user_ref) is not int
            or command.user_ref <= 0
            or type(command.request_ref) is not UUID
        ):
            raise InvalidMemberNoAllocationCommand(
                "member number allocation command is invalid"
            )
        return MemberNoAllocationKey(
            allocation_scope=AllocationScope.REGISTRATION_BOOTSTRAP,
            source_system=AllocationSourceSystem.P1_USER,
            source_ref=command.user_ref,
        )

    @staticmethod
    def _result(
        allocation: MemberNoAllocation,
        *,
        replayed: bool,
    ) -> MemberNoAllocationResult:
        return MemberNoAllocationResult(
            allocation_id=allocation.allocation_id,
            member_no=allocation.member_no,
            replayed=replayed,
        )
