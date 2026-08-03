from dataclasses import dataclass
from uuid import UUID

from ..entities import Member
from ..value_objects import CreationSource, MemberNo, MemberStatus


class MemberMappingError(ValueError):
    """Raised when persistence mapping input violates the mapper contract."""


@dataclass(frozen=True, slots=True)
class MemberPersistenceState:
    member_id: UUID
    member_no: str
    creation_source: str
    status: str
    version: int | None


class MemberMapper:
    @staticmethod
    def _validate_version(version: int | None) -> None:
        if version is not None and (type(version) is not int or version < 0):
            raise MemberMappingError("member persistence version is invalid")

    def to_persistence(
        self, member: Member, version: int | None
    ) -> MemberPersistenceState:
        if type(member) is not Member:
            raise MemberMappingError("member mapping input is invalid")
        self._validate_version(version)

        return MemberPersistenceState(
            member_id=member.member_id,
            member_no=member.member_no.value,
            creation_source=member.creation_source.value,
            status=member.status.value,
            version=version,
        )

    def to_domain(self, state: MemberPersistenceState) -> Member:
        if type(state) is not MemberPersistenceState:
            raise MemberMappingError("member persistence state is invalid")
        self._validate_version(state.version)

        return Member(
            member_id=state.member_id,
            member_no=MemberNo(state.member_no),
            creation_source=CreationSource(state.creation_source),
            status=MemberStatus(state.status),
        )
