from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from app.core.uuid_generator import UuidGenerator

from ..entities import Member
from ..errors import InvalidMemberNoError
from ..repository import (
    MemberNotFoundError,
    MemberPersistenceUnavailableError,
    MemberRepositoryError,
    MemberUniquenessConflictError,
)
from ..value_objects import CreationSource, MemberNo
from .unit_of_work import (
    IdentityUnitOfWork,
    IdentityUnitOfWorkError,
    IdentityUnitOfWorkUnavailableError,
)


class CreateRegistrationMemberError(RuntimeError):
    pass


class InvalidCreateRegistrationMemberCommand(CreateRegistrationMemberError):
    pass


class CreateRegistrationMemberConflict(CreateRegistrationMemberError):
    pass


class CreateRegistrationMemberUnavailable(CreateRegistrationMemberError):
    pass


class CreateRegistrationMemberOutcomeUnknown(CreateRegistrationMemberError):
    pass


class CreateRegistrationMemberFailed(CreateRegistrationMemberError):
    pass


@dataclass(frozen=True, slots=True)
class CreateRegistrationMemberCommand:
    member_no: str


@dataclass(frozen=True, slots=True)
class CreateRegistrationMemberResult:
    member_id: UUID
    member_no: str
    creation_source: str
    status: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class _CommitOutcomeUncertain(Exception):
    candidate: Member


class CreateRegistrationMemberService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], IdentityUnitOfWork],
        uuid_generator: UuidGenerator,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._uuid_generator = uuid_generator

    async def execute(
        self, command: CreateRegistrationMemberCommand
    ) -> CreateRegistrationMemberResult:
        member_no = self._validate_member_no(command)

        try:
            return await self._create_or_replay(member_no)
        except _CommitOutcomeUncertain as uncertainty:
            return await self._confirm_commit(member_no, uncertainty.candidate)
        except MemberUniquenessConflictError:
            return await self._resolve_uniqueness_conflict(member_no)
        except CreateRegistrationMemberError:
            raise
        except (
            MemberPersistenceUnavailableError,
            IdentityUnitOfWorkUnavailableError,
        ):
            raise CreateRegistrationMemberUnavailable(
                "identity persistence is unavailable"
            ) from None
        except (MemberRepositoryError, IdentityUnitOfWorkError):
            raise CreateRegistrationMemberFailed(
                "registration member creation failed"
            ) from None
        except Exception:
            raise CreateRegistrationMemberFailed(
                "registration member creation failed"
            ) from None

    async def _create_or_replay(
        self, member_no: MemberNo
    ) -> CreateRegistrationMemberResult:
        async with self._unit_of_work_factory() as unit_of_work:
            try:
                existing = await unit_of_work.members.get_by_member_no(member_no)
            except MemberNotFoundError:
                pass
            else:
                return self._result(existing, replayed=True)

            candidate = Member.create(
                member_id=self._uuid_generator.generate(),
                member_no=member_no,
                creation_source=CreationSource.REGISTRATION,
            )
            await unit_of_work.members.add(candidate)
            try:
                await unit_of_work.commit()
            except IdentityUnitOfWorkError:
                raise _CommitOutcomeUncertain(candidate) from None
            return self._result(candidate, replayed=False)

    async def _resolve_uniqueness_conflict(
        self, member_no: MemberNo
    ) -> CreateRegistrationMemberResult:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                winner = await unit_of_work.members.get_by_member_no(member_no)
            return self._result(winner, replayed=True)
        except MemberNotFoundError:
            raise CreateRegistrationMemberConflict(
                "registration member creation conflicts with persisted state"
            ) from None
        except (
            MemberPersistenceUnavailableError,
            IdentityUnitOfWorkUnavailableError,
        ):
            raise CreateRegistrationMemberUnavailable(
                "identity persistence is unavailable"
            ) from None
        except Exception:
            raise CreateRegistrationMemberFailed(
                "registration member creation failed"
            ) from None

    async def _confirm_commit(
        self, member_no: MemberNo, candidate: Member
    ) -> CreateRegistrationMemberResult:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                persisted = await unit_of_work.members.get_by_member_no(member_no)
            return self._result(
                persisted,
                replayed=persisted.member_id != candidate.member_id,
            )
        except Exception:
            raise CreateRegistrationMemberOutcomeUnknown(
                "registration member creation outcome is unknown"
            ) from None

    @staticmethod
    def _validate_member_no(command: CreateRegistrationMemberCommand) -> MemberNo:
        if type(command) is not CreateRegistrationMemberCommand:
            raise InvalidCreateRegistrationMemberCommand(
                "create registration member command is invalid"
            )
        raw_member_no = command.member_no
        if (
            type(raw_member_no) is not str
            or not raw_member_no
            or raw_member_no != raw_member_no.strip()
            or len(raw_member_no) > 64
        ):
            raise InvalidCreateRegistrationMemberCommand(
                "create registration member command is invalid"
            )
        try:
            return MemberNo(raw_member_no)
        except InvalidMemberNoError:
            raise InvalidCreateRegistrationMemberCommand(
                "create registration member command is invalid"
            ) from None

    @staticmethod
    def _result(member: Member, *, replayed: bool) -> CreateRegistrationMemberResult:
        return CreateRegistrationMemberResult(
            member_id=member.member_id,
            member_no=member.member_no.value,
            creation_source=member.creation_source.value,
            status=member.status.value,
            replayed=replayed,
        )
