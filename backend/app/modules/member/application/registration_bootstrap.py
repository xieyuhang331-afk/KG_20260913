from dataclasses import dataclass
from typing import Callable
from uuid import RFC_4122, UUID

from app.core.uuid_generator import UuidGenerator

from ..entities import Member
from ..value_objects import CreationSource, MemberNo, MemberStatus
from .registration_bootstrap_ports import (
    RegistrationBootstrapCommitOutcomeUnknown,
    RegistrationBootstrapConflict,
    RegistrationBootstrapNotFound,
    RegistrationBootstrapPersistenceError,
    RegistrationBootstrapRecord,
    RegistrationBootstrapUnitOfWork,
    RegistrationEligibilityProof,
    RegistrationEligibilityProofReader,
    RegistrationMemberNoAllocationProofReader,
    UserMemberSelfLink,
)


class RegistrationIdentityBootstrapError(RuntimeError):
    pass


class InvalidRegistrationIdentityBootstrapCommand(
    RegistrationIdentityBootstrapError
):
    pass


class RegistrationIdentityBootstrapInconsistent(
    RegistrationIdentityBootstrapError
):
    pass


class RegistrationIdentityBootstrapConflict(
    RegistrationIdentityBootstrapError
):
    pass


class RegistrationIdentityBootstrapOutcomeUnknown(
    RegistrationIdentityBootstrapError
):
    pass


class RegistrationIdentityBootstrapFailed(RegistrationIdentityBootstrapError):
    pass


@dataclass(frozen=True, slots=True)
class RegistrationIdentityBootstrapCommand:
    user_ref: int
    registration_event_id: UUID
    eligibility_decision_ref: UUID
    member_no_allocation_ref: UUID


@dataclass(frozen=True, slots=True)
class RegistrationIdentityBootstrapResult:
    user_ref: int
    member_id: UUID
    member_no: str
    self_link_id: UUID
    bootstrap_record_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class _CommitOutcomeUncertain(Exception):
    candidate: RegistrationBootstrapRecord


class RegistrationIdentityBootstrapService:
    def __init__(
        self,
        *,
        eligibility_reader: RegistrationEligibilityProofReader,
        allocation_reader: RegistrationMemberNoAllocationProofReader,
        unit_of_work_factory: Callable[[], RegistrationBootstrapUnitOfWork],
        uuid_generator: UuidGenerator,
    ) -> None:
        self._eligibility_reader = eligibility_reader
        self._allocation_reader = allocation_reader
        self._unit_of_work_factory = unit_of_work_factory
        self._uuid_generator = uuid_generator

    async def execute(
        self, command: RegistrationIdentityBootstrapCommand
    ) -> RegistrationIdentityBootstrapResult:
        self._validate_command(command)
        try:
            eligibility = await self._eligibility_reader.get_current_eligible(
                decision_ref=command.eligibility_decision_ref,
                user_ref=command.user_ref,
            )
            allocation = await self._allocation_reader.get_allocated(
                allocation_ref=command.member_no_allocation_ref,
                user_ref=command.user_ref,
            )
            if (
                eligibility.user_ref != command.user_ref
                or eligibility.decision_ref
                != command.eligibility_decision_ref
                or allocation.user_ref != command.user_ref
                or allocation.allocation_ref
                != command.member_no_allocation_ref
            ):
                raise RegistrationIdentityBootstrapInconsistent(
                    "registration bootstrap proofs do not match the subject"
                )
            return await self._bootstrap(
                command, allocation.member_no, eligibility
            )
        except _CommitOutcomeUncertain as uncertainty:
            return await self._confirm_outcome(
                command, uncertainty.candidate, eligibility
            )
        except RegistrationIdentityBootstrapError:
            raise
        except RegistrationBootstrapConflict:
            return await self._confirm_conflict_winner(
                command, allocation.member_no, eligibility
            )
        except Exception:
            raise RegistrationIdentityBootstrapFailed(
                "identity registration bootstrap failed"
            ) from None

    async def _bootstrap(
        self,
        command: RegistrationIdentityBootstrapCommand,
        member_no_value: str,
        eligibility: RegistrationEligibilityProof,
    ) -> RegistrationIdentityBootstrapResult:
        member_no = MemberNo(member_no_value)
        async with self._unit_of_work_factory() as unit_of_work:
            existing = await self._optional_get(
                unit_of_work.bootstrap_records, command.user_ref
            )
            if existing is not None:
                member = await self._optional_get(
                    unit_of_work.members, existing.member_no
                )
                link = await self._optional_get(
                    unit_of_work.self_links, command.user_ref
                )
                if not self._trio_consistent(existing, member, link):
                    raise RegistrationIdentityBootstrapInconsistent(
                        "persisted identity bootstrap is incomplete or inconsistent"
                    )
                if not self._matches_complete(
                    existing,
                    member,
                    link,
                    command,
                    member_no.value,
                    eligibility,
                ):
                    raise RegistrationIdentityBootstrapConflict(
                        "canonical registration bootstrap already exists"
                    )
                return self._result(existing, replayed=True)

            if await self._optional_get(unit_of_work.members, member_no.value):
                raise RegistrationBootstrapConflict(
                    "member number already belongs to another member"
                )
            if await self._optional_get(
                unit_of_work.self_links, command.user_ref
            ):
                raise RegistrationBootstrapConflict(
                    "registration user already has a self link"
                )

            member_id = self._uuid_generator.generate()
            link_id = self._uuid_generator.generate()
            record_id = self._uuid_generator.generate()
            member = Member.create(
                member_id=member_id,
                member_no=member_no,
                creation_source=CreationSource.REGISTRATION,
            )
            link = UserMemberSelfLink(
                link_id=link_id,
                user_ref=command.user_ref,
                member_id=member.member_id,
                eligibility_decision_ref=command.eligibility_decision_ref,
                establishment_basis="REGISTRATION_VERIFIED_BOOTSTRAP",
                establishment_record_ref=record_id,
            )
            record = RegistrationBootstrapRecord(
                record_id=record_id,
                user_ref=command.user_ref,
                member_id=member.member_id,
                self_link_id=link.link_id,
                registration_event_id=command.registration_event_id,
                eligibility_decision_ref=command.eligibility_decision_ref,
                member_no_allocation_ref=command.member_no_allocation_ref,
                member_no=member_no.value,
                bootstrap_scope="REGISTRATION_VERIFIED",
                source_system="P1_USER",
                source_ref=command.user_ref,
                decision=eligibility.decision,
                policy_version=eligibility.policy_version,
            )
            await unit_of_work.members.add(member)
            await unit_of_work.self_links.add(link)
            await unit_of_work.bootstrap_records.add(record)
            try:
                await unit_of_work.commit()
            except RegistrationBootstrapCommitOutcomeUnknown:
                raise _CommitOutcomeUncertain(record) from None
            return self._result(record, replayed=False)

    async def _confirm_outcome(
        self,
        command: RegistrationIdentityBootstrapCommand,
        candidate: RegistrationBootstrapRecord,
        eligibility: RegistrationEligibilityProof,
    ) -> RegistrationIdentityBootstrapResult:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                persisted, member, link = await self._read_complete(
                    unit_of_work, command.user_ref
                )
            if not self._matches_complete(
                persisted,
                member,
                link,
                command,
                candidate.member_no,
                eligibility,
            ):
                raise RegistrationIdentityBootstrapOutcomeUnknown(
                    "identity registration bootstrap outcome is unknown"
                )
            return self._result(
                persisted, replayed=persisted.record_id != candidate.record_id
            )
        except RegistrationIdentityBootstrapOutcomeUnknown:
            raise
        except Exception:
            raise RegistrationIdentityBootstrapOutcomeUnknown(
                "identity registration bootstrap outcome is unknown"
            ) from None

    async def _confirm_conflict_winner(
        self,
        command: RegistrationIdentityBootstrapCommand,
        member_no: str,
        eligibility: RegistrationEligibilityProof,
    ) -> RegistrationIdentityBootstrapResult:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                persisted, member, link = await self._read_complete(
                    unit_of_work, command.user_ref
                )
            if not self._matches_complete(
                persisted,
                member,
                link,
                command,
                member_no,
                eligibility,
            ):
                raise RegistrationIdentityBootstrapConflict
            return self._result(persisted, replayed=True)
        except Exception:
            raise RegistrationIdentityBootstrapConflict(
                "identity registration bootstrap conflicts with persisted state"
            ) from None

    async def _read_complete(self, unit_of_work, user_ref: int):
        record = await unit_of_work.bootstrap_records.get(user_ref)
        member = await unit_of_work.members.get(record.member_no)
        link = await unit_of_work.self_links.get(user_ref)
        return record, member, link

    @staticmethod
    async def _optional_get(repository, key):
        try:
            return await repository.get(key)
        except RegistrationBootstrapNotFound:
            return None

    @staticmethod
    def _trio_consistent(
        record: RegistrationBootstrapRecord,
        member: Member | None,
        link: UserMemberSelfLink | None,
    ) -> bool:
        return (
            type(record) is RegistrationBootstrapRecord
            and type(member) is Member
            and type(link) is UserMemberSelfLink
            and member.member_id == record.member_id
            and member.member_no.value == record.member_no
            and member.creation_source is CreationSource.REGISTRATION
            and member.status is MemberStatus.CREATED
            and link.link_id == record.self_link_id
            and link.user_ref == record.user_ref
            and link.member_id == record.member_id
            and link.eligibility_decision_ref
            == record.eligibility_decision_ref
            and link.source == "REGISTRATION_VERIFIED"
            and link.establishment_basis
            == "REGISTRATION_VERIFIED_BOOTSTRAP"
            and link.establishment_record_ref == record.record_id
        )

    @classmethod
    def _matches_complete(
        cls,
        record: RegistrationBootstrapRecord,
        member: Member | None,
        link: UserMemberSelfLink | None,
        command: RegistrationIdentityBootstrapCommand,
        member_no: str,
        eligibility: RegistrationEligibilityProof,
    ) -> bool:
        return (
            cls._trio_consistent(record, member, link)
            and record.user_ref == command.user_ref
            and record.eligibility_decision_ref
            == command.eligibility_decision_ref
            and record.member_no_allocation_ref
            == command.member_no_allocation_ref
            and record.member_no == member_no
            and record.bootstrap_scope == "REGISTRATION_VERIFIED"
            and record.source_system == "P1_USER"
            and record.source_ref == command.user_ref
            and record.decision == eligibility.decision
            and record.policy_version == eligibility.policy_version
            and record.source == "REGISTRATION_VERIFIED"
        )

    @staticmethod
    def _result(
        record: RegistrationBootstrapRecord, *, replayed: bool
    ) -> RegistrationIdentityBootstrapResult:
        return RegistrationIdentityBootstrapResult(
            user_ref=record.user_ref,
            member_id=record.member_id,
            member_no=record.member_no,
            self_link_id=record.self_link_id,
            bootstrap_record_id=record.record_id,
            replayed=replayed,
        )

    @staticmethod
    def _validate_command(command: object) -> None:
        if type(command) is not RegistrationIdentityBootstrapCommand:
            raise InvalidRegistrationIdentityBootstrapCommand(
                "registration bootstrap command is invalid"
            )
        if type(command.user_ref) is not int or command.user_ref <= 0:
            raise InvalidRegistrationIdentityBootstrapCommand(
                "registration bootstrap command is invalid"
            )
        for value in (
            command.registration_event_id,
            command.eligibility_decision_ref,
            command.member_no_allocation_ref,
        ):
            if (
                type(value) is not UUID
                or value.version != 7
                or value.variant != RFC_4122
            ):
                raise InvalidRegistrationIdentityBootstrapCommand(
                    "registration bootstrap command is invalid"
                )
