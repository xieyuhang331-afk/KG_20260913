from typing import Protocol
from uuid import UUID

from uuid_utils.compat import uuid7


class UuidGenerator(Protocol):
    def generate(self) -> UUID: ...


class Uuid7Generator:
    def generate(self) -> UUID:
        return UUID(int=uuid7().int)
