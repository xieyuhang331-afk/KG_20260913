from typing import Protocol, Sequence

from .domain import OrganizationProjectionRow, OrganizationSourceNode


class OrganizationProjectionCorePort(Protocol):
    def project(
        self, *, chain: Sequence[OrganizationSourceNode], digest_key: bytes
    ) -> OrganizationProjectionRow: ...
