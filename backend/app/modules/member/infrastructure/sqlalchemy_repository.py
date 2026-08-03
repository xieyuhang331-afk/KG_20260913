from typing import NoReturn
from uuid import RFC_4122, UUID

from ..entities import Member
from ..errors import InvalidMemberIdError, MemberDomainError
from ..repository import (
    MemberNotFoundError,
    MemberPersistenceUnavailableError,
    MemberRepositoryError,
    MemberUniquenessConflictError,
    MemberVersionConflictError,
)
from ..value_objects import MemberNo


class SqlAlchemyMemberRepository:
    """Async Member repository adapter over injected persistence collaborators."""

    def __init__(self, session, mapper) -> None:
        self._session = session
        self._mapper = mapper

    async def get_by_id(self, member_id: UUID) -> Member:
        self._validate_member_id(member_id)
        result = await self._execute("get_by_id", member_id)
        state = result.scalar_one_or_none()
        if state is None:
            raise MemberNotFoundError("member was not found")
        return self._restore_member(state)

    async def get_by_member_no(self, member_no: MemberNo) -> Member:
        result = await self._execute("get_by_member_no", member_no)
        state = result.scalar_one_or_none()
        if state is None:
            raise MemberNotFoundError("member was not found")
        return self._restore_member(state)

    async def is_member_no_available(self, member_no: MemberNo) -> bool:
        result = await self._execute("is_member_no_available", member_no)
        return result.scalar_one_or_none() is None

    async def add(self, member: Member) -> None:
        self._validate_member_id(member.member_id)
        try:
            state = self._mapper.to_persistence(member, None)
            self._session.add(state)
            await self._session.flush()
        except MemberRepositoryError:
            raise
        except Exception as exc:
            self._raise_translated(exc)

    async def save(self, member: Member, expected_version: object) -> None:
        self._validate_member_id(member.member_id)
        try:
            state = self._mapper.to_persistence(member, expected_version)
            result = await self._session.execute(
                "save", state, expected_version
            )
            if getattr(result, "rowcount", None) != 1:
                raise MemberRepositoryError(
                    "member persistence operation failed"
                )
            await self._session.flush()
        except MemberRepositoryError:
            raise
        except Exception as exc:
            self._raise_translated(exc)

    async def _execute(self, *args):
        try:
            return await self._session.execute(*args)
        except MemberRepositoryError:
            raise
        except Exception as exc:
            self._raise_translated(exc)

    def _restore_member(self, state) -> Member:
        try:
            member = self._mapper.to_domain(state)
        except MemberDomainError as exc:
            raise MemberRepositoryError(
                "stored member state is invalid"
            ) from exc
        except MemberRepositoryError:
            raise
        except Exception as exc:
            self._raise_translated(exc)

        if type(member) is not Member:
            raise MemberRepositoryError("stored member state is invalid")
        return member

    @staticmethod
    def _validate_member_id(member_id: UUID) -> None:
        if type(member_id) is not UUID:
            raise InvalidMemberIdError(
                "member ID must be a standard uuid.UUID"
            )
        if member_id.version != 7 or member_id.variant != RFC_4122:
            raise InvalidMemberIdError(
                "member ID must be an RFC UUID version 7"
            )

    @staticmethod
    def _raise_translated(exc: Exception) -> NoReturn:
        category = getattr(exc, "category", None)
        if category == "not_found":
            error = MemberNotFoundError("member was not found")
        elif category == "unique_conflict":
            error = MemberUniquenessConflictError(
                "member uniqueness conflict"
            )
        elif category == "version_conflict":
            error = MemberVersionConflictError("member version conflict")
        elif category == "unavailable":
            error = MemberPersistenceUnavailableError(
                "member persistence is unavailable"
            )
        else:
            error = MemberRepositoryError(
                "member persistence operation failed"
            )
        raise error from exc
