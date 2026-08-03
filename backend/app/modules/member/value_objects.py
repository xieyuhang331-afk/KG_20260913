from dataclasses import dataclass
from enum import Enum

from .errors import (
    InvalidCreationSourceError,
    InvalidMemberNoError,
    InvalidMemberStatusError,
)


@dataclass(frozen=True, slots=True)
class MemberNo:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value.strip():
            raise InvalidMemberNoError("member number must be a non-blank string")


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
