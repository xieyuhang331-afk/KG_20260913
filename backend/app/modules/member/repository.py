from typing import Protocol, runtime_checkable
from uuid import UUID

from .entities import Member
from .value_objects import MemberNo


class MemberRepositoryError(RuntimeError):
    pass


class MemberNotFoundError(MemberRepositoryError):
    pass


class MemberUniquenessConflictError(MemberRepositoryError):
    pass


class MemberVersionConflictError(MemberRepositoryError):
    pass


class MemberPersistenceUnavailableError(MemberRepositoryError):
    pass


@runtime_checkable
class MemberRepository(Protocol):
    async def get_by_id(self, member_id: UUID) -> Member: ...

    async def get_by_member_no(self, member_no: MemberNo) -> Member: ...

    async def is_member_no_available(self, member_no: MemberNo) -> bool: ...

    async def add(self, member: Member) -> None: ...

    async def save(
        self, member: Member, expected_version: object
    ) -> None: ...
