from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PrivateObjectEvidence:
    size: int
    mime_type: str
    sha256: str
    magic: bytes = b""


class PrivateObjectStorePort(Protocol):
    async def create_temporary(self, object_key: str, lease_token: str) -> None: ...
    async def write_chunk(
        self, object_key: str, lease_token: str, chunk: bytes
    ) -> None: ...
    async def flush(
        self, object_key: str, lease_token: str, *, mime_type: str
    ) -> PrivateObjectEvidence: ...
    async def commit(self, object_key: str, lease_token: str) -> None: ...
    async def abort_temporary(self, object_key: str, lease_token: str) -> None: ...
    async def stat(self, object_key: str) -> PrivateObjectEvidence: ...
    def iter_read(
        self, object_key: str, *, chunk_size: int = 64 * 1024
    ) -> AsyncIterator[bytes]: ...
    async def materialize_for_scan(self, object_key: str) -> Path: ...
    async def delete(self, object_key: str) -> None: ...
    async def cleanup_temporary(
        self, *, active_leases: set[str], older_than: datetime
    ) -> int: ...


class PrivateFileScanner(Protocol):
    async def health(self) -> bool: ...
    async def scan(self, path: Path, *, mime_type: str) -> str: ...


class PrivateFileScannerUnavailable(RuntimeError):
    pass
