from dataclasses import dataclass
from enum import Enum

from .errors import (
    InvalidCreationSourceError,
    InvalidMemberNoError,
    InvalidMemberStatusError,
)


_MEMBER_NO_MAX_LENGTH = 64


@dataclass(frozen=True, slots=True)
class MemberNo:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise InvalidMemberNoError("member number must be a non-blank string")
        if len(self.value) > _MEMBER_NO_MAX_LENGTH:
            raise InvalidMemberNoError(
                "member number must be at most 64 characters"
            )


class MemberStatus(str, Enum):
    CREATED = "created"

    @classmethod
    def _missing_(cls, value):
        raise InvalidMemberStatusError(f"unsupported member status: {value!r}")


class CreationSource(str, Enum):
    REGISTRATION = "registration"

    @classmethod
    def _missing_(cls, value):
        raise InvalidCreationSourceError(f"unsupported creation source: {value!r}")
