from dataclasses import dataclass
from uuid import RFC_4122, UUID

from .errors import (
    InvalidCreationSourceError,
    InvalidMemberIdError,
    InvalidMemberNoError,
    InvalidMemberStatusError,
)
from .value_objects import CreationSource, MemberNo, MemberStatus


@dataclass(frozen=True, slots=True)
class Member:
    member_id: UUID
    member_no: MemberNo
    creation_source: CreationSource
    status: MemberStatus

    def __post_init__(self) -> None:
        if type(self.member_id) is not UUID:
            raise InvalidMemberIdError("member ID must be a standard uuid.UUID")
        if self.member_id.version != 7 or self.member_id.variant != RFC_4122:
            raise InvalidMemberIdError("member ID must be an RFC UUID version 7")
        if not isinstance(self.member_no, MemberNo):
            raise InvalidMemberNoError("member_no must be a MemberNo")
        if not isinstance(self.creation_source, CreationSource):
            raise InvalidCreationSourceError(
                "creation_source must be a CreationSource"
            )
        if not isinstance(self.status, MemberStatus):
            raise InvalidMemberStatusError("status must be a MemberStatus")

    @classmethod
    def create(
        cls,
        *,
        member_id: UUID,
        member_no: MemberNo,
        creation_source: CreationSource,
    ) -> "Member":
        return cls(
            member_id=member_id,
            member_no=member_no,
            creation_source=creation_source,
            status=MemberStatus.CREATED,
        )
