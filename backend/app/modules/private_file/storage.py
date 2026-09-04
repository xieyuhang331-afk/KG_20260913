from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.modules.private_file.domain import PrivateFileConflict
from app.modules.private_file.ports import PrivateObjectEvidence

_MAX_SIZE = 10 * 1024 * 1024
_LEASE = re.compile(r"^[0-9a-f-]{36}$")


class LocalFilesystemAdapter:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise RuntimeError("PRIVATE_FILE_STORAGE_ROOT_INVALID")
        self.root = root.resolve()
        repository_root = Path(__file__).resolve().parents[4]
        if self.root.is_relative_to(repository_root) or any(
            part.casefold() == "synologydrive" for part in self.root.parts
        ):
            raise RuntimeError("PRIVATE_FILE_STORAGE_ROOT_INVALID")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or not os.access(self.root, os.R_OK | os.W_OK):
            raise RuntimeError("PRIVATE_FILE_STORAGE_ROOT_INVALID")

    def _final(self, object_key: str) -> Path:
        key = PurePosixPath(object_key)
        if key.is_absolute() or not key.parts or any(
            part in {"", ".", ".."} for part in key.parts
        ):
            raise PrivateFileConflict("PRIVATE_FILE_OBJECT_KEY_INVALID")
        value = self.root.joinpath(*key.parts).resolve()
        if not value.is_relative_to(self.root) or value == self.root:
            raise PrivateFileConflict("PRIVATE_FILE_OBJECT_KEY_INVALID")
        return value

    def _temporary(self, object_key: str, lease_token: str) -> Path:
        if not _LEASE.fullmatch(lease_token):
            raise PrivateFileConflict("PRIVATE_FILE_UPLOAD_LEASE_INVALID")
        final = self._final(object_key)
        return final.with_name(f".{final.name}.{lease_token}.part")

    async def create_temporary(self, object_key: str, lease_token: str) -> None:
        temporary = self._temporary(object_key, lease_token)
        await asyncio.to_thread(temporary.parent.mkdir, parents=True, exist_ok=True)

        def create() -> None:
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            os.close(descriptor)

        try:
            await asyncio.to_thread(create)
        except FileExistsError:
            raise PrivateFileConflict("PRIVATE_FILE_UPLOAD_IN_PROGRESS") from None

    async def write_chunk(
        self, object_key: str, lease_token: str, chunk: bytes
    ) -> None:
        if type(chunk) is not bytes or not chunk:
            return
        temporary = self._temporary(object_key, lease_token)

        def append() -> None:
            with temporary.open("ab", buffering=0) as stream:
                stream.write(chunk)

        await asyncio.to_thread(append)

    async def flush(
        self, object_key: str, lease_token: str, *, mime_type: str
    ) -> PrivateObjectEvidence:
        temporary = self._temporary(object_key, lease_token)

        def inspect() -> PrivateObjectEvidence:
            digest = hashlib.sha256()
            size = 0
            magic = b""
            with temporary.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if size > _MAX_SIZE:
                        raise PrivateFileConflict(
                            "PRIVATE_FILE_SIZE_LIMIT_EXCEEDED"
                        )
                    if len(magic) < 16:
                        magic += chunk[: 16 - len(magic)]
                    digest.update(chunk)
            if size < 1:
                raise PrivateFileConflict("PRIVATE_FILE_EVIDENCE_MISMATCH")
            with temporary.open("rb+") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            return PrivateObjectEvidence(size, mime_type, digest.hexdigest(), magic)

        return await asyncio.to_thread(inspect)

    async def commit(self, object_key: str, lease_token: str) -> None:
        temporary = self._temporary(object_key, lease_token)
        final = self._final(object_key)

        def replace() -> None:
            os.replace(temporary, final)
            if os.name != "nt":
                descriptor = os.open(final.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

        await asyncio.to_thread(replace)

    async def abort_temporary(self, object_key: str, lease_token: str) -> None:
        temporary = self._temporary(object_key, lease_token)
        try:
            await asyncio.to_thread(temporary.unlink)
        except FileNotFoundError:
            return

    async def stat(self, object_key: str) -> PrivateObjectEvidence:
        final = self._final(object_key)

        def inspect() -> PrivateObjectEvidence:
            digest = hashlib.sha256()
            size = 0
            magic = b""
            with final.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    size += len(chunk)
                    if len(magic) < 16:
                        magic += chunk[: 16 - len(magic)]
                    digest.update(chunk)
            return PrivateObjectEvidence(size, "", digest.hexdigest(), magic)

        try:
            return await asyncio.to_thread(inspect)
        except FileNotFoundError:
            raise PrivateFileConflict("PRIVATE_FILE_OBJECT_MISSING") from None

    async def iter_read(
        self, object_key: str, *, chunk_size: int = 64 * 1024
    ) -> AsyncIterator[bytes]:
        final = self._final(object_key)
        try:
            stream = await asyncio.to_thread(final.open, "rb")
        except FileNotFoundError:
            raise PrivateFileConflict("PRIVATE_FILE_OBJECT_MISSING") from None
        try:
            while True:
                chunk = await asyncio.to_thread(stream.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(stream.close)

    async def materialize_for_scan(self, object_key: str) -> Path:
        final = self._final(object_key)
        if not await asyncio.to_thread(final.is_file):
            raise PrivateFileConflict("PRIVATE_FILE_OBJECT_MISSING")
        return final

    async def delete(self, object_key: str) -> None:
        final = self._final(object_key)
        try:
            await asyncio.to_thread(final.unlink)
        except FileNotFoundError:
            return

    async def cleanup_temporary(
        self, *, active_leases: set[str], older_than: datetime
    ) -> int:
        if older_than.tzinfo is None:
            raise PrivateFileConflict("PRIVATE_FILE_CLEANUP_INVALID")
        removed = 0
        for candidate in self.root.rglob(".*.part"):
            if not candidate.is_file() or not candidate.resolve().is_relative_to(self.root):
                continue
            parts = candidate.name.rsplit(".", 2)
            if len(parts) != 3 or not _LEASE.fullmatch(parts[1]):
                continue
            if parts[1] in active_leases:
                continue
            changed = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
            if changed >= older_than:
                continue
            try:
                await asyncio.to_thread(candidate.unlink)
                removed += 1
            except FileNotFoundError:
                continue
        return removed


def build_private_object_store(
    *, backend: str, root: str | None
) -> LocalFilesystemAdapter:
    if backend == "minio":
        raise RuntimeError("PRIVATE_FILE_STORAGE_BACKEND_UNAVAILABLE")
    if backend != "local_filesystem" or not root:
        raise RuntimeError("PRIVATE_FILE_STORAGE_CONFIGURATION_INVALID")
    return LocalFilesystemAdapter(Path(root))
