from datetime import datetime, timedelta
from typing import NoReturn
from uuid import RFC_4122, UUID

from sqlalchemy import select, update

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
from .models import MemberOrmModel


class SqlAlchemyMemberRepository:
    """Async Member repository adapter over injected persistence collaborators."""

    def __init__(self, session, mapper, orm_mapper, clock) -> None:
        self._session = session
        self._mapper = mapper
        self._orm_mapper = orm_mapper
        self._clock = clock

    async def get_by_id(self, member_id: UUID) -> Member:
        self._validate_member_id(member_id)
        statement = select(MemberOrmModel).where(
            MemberOrmModel.member_id == member_id
        )
        result = await self._execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise MemberNotFoundError("member was not found")
        return self._restore_member(model)

    async def get_by_member_no(self, member_no: MemberNo) -> Member:
        statement = select(MemberOrmModel).where(
            MemberOrmModel.member_no == member_no.value
        )
        result = await self._execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            raise MemberNotFoundError("member was not found")
        return self._restore_member(model)

    async def is_member_no_available(self, member_no: MemberNo) -> bool:
        statement = select(MemberOrmModel.member_id).where(
            MemberOrmModel.member_no == member_no.value
        )
        result = await self._execute(statement)
        return result.scalar_one_or_none() is None

    async def add(self, member: Member) -> None:
        self._validate_member_id(member.member_id)
        try:
            state = self._mapper.to_persistence(member, None)
            now = self._current_time()
            model = self._orm_mapper.to_new_model(
                state,
                initial_version=1,
                created_at=now,
                updated_at=now,
            )
            self._session.add(model)
            await self._session.flush()
        except MemberRepositoryError:
            raise
        except Exception as exc:
            self._raise_translated(exc)

    async def save(self, member: Member, expected_version: object) -> None:
        self._validate_member_id(member.member_id)
        if type(expected_version) is not int or expected_version < 1:
            raise MemberVersionConflictError("member version conflict")
        try:
            state = self._mapper.to_persistence(member, expected_version)
            statement = (
                update(MemberOrmModel)
                .where(MemberOrmModel.member_id == state.member_id)
                .where(MemberOrmModel.version == expected_version)
                .values(
                    member_no=state.member_no,
                    creation_source=state.creation_source,
                    status=state.status,
                    version=expected_version + 1,
                    updated_at=self._current_time(),
                )
            )
            result = await self._session.execute(statement)
            rowcount = getattr(result, "rowcount", None)
            if rowcount == 0:
                raise MemberVersionConflictError("member version conflict")
            if rowcount != 1:
                raise MemberRepositoryError(
                    "member persistence operation failed"
                )
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

    def _restore_member(self, model) -> Member:
        try:
            state = self._orm_mapper.to_state(model)
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

    def _current_time(self) -> datetime:
        value = self._clock()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() != timedelta(0)
        ):
            raise MemberRepositoryError(
                "member persistence operation failed"
            )
        return value

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
