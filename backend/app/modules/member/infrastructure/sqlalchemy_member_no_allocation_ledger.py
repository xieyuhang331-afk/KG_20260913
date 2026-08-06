import re
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import RFC_4122, UUID

from sqlalchemy import select
from sqlalchemy.exc import (
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    TimeoutError as SqlAlchemyTimeoutError,
)

from ..application.member_no_allocator import (
    AllocationScope,
    AllocationSourceSystem,
    MemberNoAllocation,
    MemberNoAllocationKey,
    MemberNoAllocationKeyConflictError,
    MemberNoAllocationNotFoundError,
    MemberNoAllocationPortError,
    MemberNoAllocationPortUnavailableError,
    MemberNoAllocationState,
    MemberNoValueConflictError,
)
from ..value_objects import MemberNo
from .models import MemberNoAllocationOrmModel


_SOURCE_UNIQUE_CONSTRAINT = "uq_member_no_allocation_source"
_MEMBER_NO_UNIQUE_CONSTRAINT = "uq_member_no_allocation_member_no"
_UNIQUE_VIOLATION_SQLSTATE = "23505"
_MEMBER_NO_PATTERN = re.compile(
    r"^M[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{20}$"
)
_UNAVAILABLE_ERRORS = (
    DisconnectionError,
    InterfaceError,
    OperationalError,
    SqlAlchemyTimeoutError,
)
_GENERIC_ERROR = "member number allocation persistence operation failed"


class SqlAlchemyMemberNoAllocationLedger:
    def __init__(self, session, clock) -> None:
        self._session = session
        self._clock = clock

    async def get_by_key(
        self, key: MemberNoAllocationKey
    ) -> MemberNoAllocation:
        self._validate_key(key)
        statement = select(MemberNoAllocationOrmModel).where(
            MemberNoAllocationOrmModel.allocation_scope
            == key.allocation_scope.value,
            MemberNoAllocationOrmModel.source_system
            == key.source_system.value,
            MemberNoAllocationOrmModel.source_ref == key.source_ref,
        )
        try:
            result = await self._session.execute(statement)
            model = result.scalar_one_or_none()
        except MemberNoAllocationPortError:
            raise
        except Exception as exc:
            self._raise_translated(exc)
        if model is None:
            raise MemberNoAllocationNotFoundError(
                "member number allocation was not found"
            )
        return self._restore(model)

    async def add(self, allocation: MemberNoAllocation) -> None:
        self._validate_allocation(allocation)
        try:
            now = self._current_time()
            model = MemberNoAllocationOrmModel(
                allocation_id=allocation.allocation_id,
                allocation_scope=allocation.key.allocation_scope.value,
                source_system=allocation.key.source_system.value,
                source_ref=allocation.key.source_ref,
                member_no=allocation.member_no.value,
                state=allocation.state.value,
                request_ref=allocation.request_ref,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self._session.add(model)
            await self._session.flush()
        except MemberNoAllocationPortError:
            raise
        except Exception as exc:
            self._raise_translated(exc)

    @classmethod
    def _restore(cls, model) -> MemberNoAllocation:
        try:
            allocation_id = cls._validate_allocation_id(model.allocation_id)
            key = MemberNoAllocationKey(
                allocation_scope=AllocationScope(model.allocation_scope),
                source_system=AllocationSourceSystem(model.source_system),
                source_ref=model.source_ref,
            )
            cls._validate_key(key)
            if (
                type(model.member_no) is not str
                or _MEMBER_NO_PATTERN.fullmatch(model.member_no) is None
                or type(model.request_ref) is not UUID
                or MemberNoAllocationState(model.state)
                is not MemberNoAllocationState.ALLOCATED
                or type(model.version) is not int
                or model.version != 1
            ):
                raise ValueError
            cls._validate_timestamps(model.created_at, model.updated_at)
            return MemberNoAllocation(
                allocation_id=allocation_id,
                key=key,
                member_no=MemberNo(model.member_no),
                request_ref=model.request_ref,
                state=MemberNoAllocationState.ALLOCATED,
            )
        except MemberNoAllocationPortError:
            raise
        except Exception as exc:
            raise MemberNoAllocationPortError(
                "stored member number allocation is invalid"
            ) from exc

    @classmethod
    def _validate_allocation(cls, allocation: MemberNoAllocation) -> None:
        try:
            if type(allocation) is not MemberNoAllocation:
                raise ValueError
            cls._validate_allocation_id(allocation.allocation_id)
            cls._validate_key(allocation.key)
            if (
                type(allocation.member_no) is not MemberNo
                or _MEMBER_NO_PATTERN.fullmatch(allocation.member_no.value)
                is None
                or type(allocation.request_ref) is not UUID
                or allocation.state is not MemberNoAllocationState.ALLOCATED
            ):
                raise ValueError
        except MemberNoAllocationPortError:
            raise
        except Exception as exc:
            raise MemberNoAllocationPortError(_GENERIC_ERROR) from exc

    @staticmethod
    def _validate_allocation_id(value: object) -> UUID:
        if (
            type(value) is not UUID
            or value.version != 7
            or value.variant != RFC_4122
        ):
            raise ValueError
        return value

    @staticmethod
    def _validate_key(key: MemberNoAllocationKey) -> None:
        if (
            type(key) is not MemberNoAllocationKey
            or key.allocation_scope is not AllocationScope.REGISTRATION_BOOTSTRAP
            or key.source_system is not AllocationSourceSystem.P1_USER
            or type(key.source_ref) is not int
            or key.source_ref <= 0
        ):
            raise MemberNoAllocationPortError(_GENERIC_ERROR)

    @staticmethod
    def _validate_timestamps(created_at: object, updated_at: object) -> None:
        for value in (created_at, updated_at):
            if (
                type(value) is not datetime
                or value.tzinfo is None
                or value.utcoffset() != timedelta(0)
            ):
                raise ValueError
        if updated_at != created_at:
            raise ValueError

    def _current_time(self) -> datetime:
        value = self._clock()
        self._validate_timestamps(value, value)
        return value

    @staticmethod
    def _raise_translated(exc: Exception) -> NoReturn:
        constraint = SqlAlchemyMemberNoAllocationLedger._unique_constraint(exc)
        if constraint == _SOURCE_UNIQUE_CONSTRAINT:
            error = MemberNoAllocationKeyConflictError(
                "member number allocation key conflict"
            )
        elif constraint == _MEMBER_NO_UNIQUE_CONSTRAINT:
            error = MemberNoValueConflictError(
                "member number allocation value conflict"
            )
        elif SqlAlchemyMemberNoAllocationLedger._is_unavailable(exc):
            error = MemberNoAllocationPortUnavailableError(
                "member number allocation persistence is unavailable"
            )
        else:
            error = MemberNoAllocationPortError(_GENERIC_ERROR)
        raise error from exc

    @staticmethod
    def _unique_constraint(exc: Exception) -> str | None:
        if not isinstance(exc, IntegrityError):
            return None
        original = exc.orig
        driver_error = getattr(original, "__cause__", None)
        sqlstate = getattr(original, "sqlstate", None) or getattr(
            driver_error, "sqlstate", None
        )
        if sqlstate != _UNIQUE_VIOLATION_SQLSTATE:
            return None
        return getattr(original, "constraint_name", None) or getattr(
            driver_error, "constraint_name", None
        )

    @staticmethod
    def _is_unavailable(exc: Exception) -> bool:
        return isinstance(exc, _UNAVAILABLE_ERRORS) or (
            isinstance(exc, DBAPIError) and exc.connection_invalidated
        )
