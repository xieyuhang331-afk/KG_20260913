from typing import Protocol, Sequence

from .domain import HealthCurrentFact, HealthProjectionFactRow, HealthWindowSelection


class HealthProjectionCorePort(Protocol):
    def project(
        self, *, facts: Sequence[HealthCurrentFact], digest_key: bytes
    ) -> tuple[list[HealthProjectionFactRow], list[HealthWindowSelection]]: ...
