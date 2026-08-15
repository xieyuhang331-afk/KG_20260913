from __future__ import annotations

from pathlib import Path
from typing import Protocol


class PrivateObjectStorePort(Protocol):
    async def write(self, object_key: str, data: bytes) -> None: ...
    async def read(self, object_key: str) -> bytes: ...
    async def delete(self, object_key: str) -> None: ...


class PrivateFileScanner(Protocol):
    async def scan(self, path: Path, *, mime_type: str) -> str: ...


class PrivateFileScannerUnavailable(RuntimeError):
    pass
