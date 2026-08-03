from types import TracebackType
from typing import Protocol, Self

from ..repository import MemberRepository


class IdentityUnitOfWorkError(RuntimeError):
    pass


class IdentityUnitOfWorkUnavailableError(IdentityUnitOfWorkError):
    pass


class IdentityUnitOfWorkStateError(IdentityUnitOfWorkError):
    pass


class IdentityUnitOfWork(Protocol):
    @property
    def members(self) -> MemberRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
