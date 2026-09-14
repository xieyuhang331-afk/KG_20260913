from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.modules.private_file.ports import PrivateFileScannerUnavailable

_ALLOWED_MIME_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})
_MAX_FILE_SIZE = 10 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
_MAX_RESPONSE_SIZE = 4096
_HEALTH_TIMEOUT_SECONDS = 0.6
_CLOSE_TIMEOUT_SECONDS = 0.2
_CONFIGURATION_ERROR = "PRIVATE_FILE_SCANNER_CONFIGURATION_INVALID"
_UNAVAILABLE = "PRIVATE_FILE_SCANNER_UNAVAILABLE"
_VERSION = re.compile(
    rb"^ClamAV (?P<engine>[0-9]+\.[0-9]+\.[0-9]+)/(?P<database>[0-9]+)/"
    rb"(?P<weekday>Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    rb"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    rb"(?P<day>[ 0-9][0-9]) (?P<clock>[0-9]{2}:[0-9]{2}:[0-9]{2}) "
    rb"(?P<year>[0-9]{4})\| COMMANDS: (?P<commands>[A-Z ]+)$"
)
_MONTHS = {
    name: index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
_FOUND = re.compile(rb"^stream: [\x21-\x7e]{1,256} FOUND$")


def _configuration_invalid() -> RuntimeError:
    return RuntimeError(_CONFIGURATION_ERROR)


def _scanner_unavailable() -> PrivateFileScannerUnavailable:
    return PrivateFileScannerUnavailable(_UNAVAILABLE)


@dataclass(frozen=True, slots=True)
class ClamAVScannerConfig:
    host: str
    port: int
    storage_root: Path = field(repr=False)
    engine_version: str
    scan_timeout_seconds: float
    max_signature_age_hours: int

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "::1"}:
            raise _configuration_invalid()
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise _configuration_invalid()
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.engine_version):
            raise _configuration_invalid()
        if not 1 <= self.scan_timeout_seconds <= 60:
            raise _configuration_invalid()
        if type(self.max_signature_age_hours) is not int or not (
            1 <= self.max_signature_age_hours <= 72
        ):
            raise _configuration_invalid()
        try:
            root = self.storage_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise _configuration_invalid() from None
        if not root.is_dir() or not root.is_absolute():
            raise _configuration_invalid()
        object.__setattr__(self, "storage_root", root)


class ClamAVScanner:
    def __init__(self, config: ClamAVScannerConfig) -> None:
        self._host = config.host
        self._port = config.port
        self._storage_root = config.storage_root
        self._engine_version = config.engine_version
        self._scan_timeout_seconds = config.scan_timeout_seconds
        self._max_signature_age_hours = config.max_signature_age_hours
        self._scan_slots = asyncio.Semaphore(2)

    async def health(self) -> bool:
        try:
            async with asyncio.timeout(_HEALTH_TIMEOUT_SECONDS):
                if await self._command(b"zPING\0") != b"PONG":
                    return False
                version = await self._command(b"zVERSIONCOMMANDS\0")
                return self._version_is_acceptable(version)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False

    async def scan(self, path: Path, *, mime_type: str) -> str:
        try:
            async with self._scan_slots:
                async with asyncio.timeout(self._scan_timeout_seconds):
                    if not await self.health():
                        raise _scanner_unavailable()
                    return await self._scan_open_file(path, mime_type=mime_type)
        except asyncio.CancelledError:
            raise
        except PrivateFileScannerUnavailable:
            raise
        except Exception:
            raise _scanner_unavailable() from None

    def _version_is_acceptable(self, response: bytes) -> bool:
        match = _VERSION.fullmatch(response)
        if match is None:
            return False
        try:
            engine = match.group("engine").decode("ascii")
            commands = frozenset(match.group("commands").decode("ascii").split())
            month = _MONTHS[match.group("month").decode("ascii")]
            day = int(match.group("day"))
            hour, minute, second = (
                int(value) for value in match.group("clock").split(b":")
            )
            signature_time = datetime(
                int(match.group("year")),
                month,
                day,
                hour,
                minute,
                second,
                tzinfo=UTC,
            )
        except (KeyError, UnicodeDecodeError, ValueError):
            return False
        if engine != self._engine_version or "INSTREAM" not in commands:
            return False
        age_seconds = (datetime.now(UTC) - signature_time).total_seconds()
        return -300 <= age_seconds <= self._max_signature_age_hours * 3600

    async def _scan_open_file(self, path: Path, *, mime_type: str) -> str:
        if mime_type not in _ALLOWED_MIME_TYPES:
            raise _scanner_unavailable()
        resolved = self._resolve_regular_path(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(resolved, flags)
        except OSError:
            raise _scanner_unavailable() from None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= _MAX_FILE_SIZE:
                raise _scanner_unavailable()
            response, streamed_digest = await self._instream(descriptor, before.st_size)
            stable_digest = await asyncio.to_thread(self._hash_descriptor, descriptor)
            after = os.fstat(descriptor)
            current = resolved.stat()
            if (
                streamed_digest != stable_digest
                or not self._same_file(before, after)
                or not self._same_file(after, current)
            ):
                raise _scanner_unavailable()
            if response == b"stream: OK":
                return "CLEAN"
            if _FOUND.fullmatch(response):
                return "REJECTED"
            raise _scanner_unavailable()
        finally:
            os.close(descriptor)

    def _resolve_regular_path(self, path: Path) -> Path:
        try:
            unresolved = Path(path)
            link_state = os.lstat(unresolved)
            file_attributes = getattr(link_state, "st_file_attributes", 0)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if stat.S_ISLNK(link_state.st_mode) or (file_attributes & reparse_flag):
                raise _scanner_unavailable()
            resolved = unresolved.resolve(strict=True)
            resolved.relative_to(self._storage_root)
        except PrivateFileScannerUnavailable:
            raise
        except (OSError, RuntimeError, ValueError):
            raise _scanner_unavailable() from None
        return resolved

    async def _instream(self, descriptor: int, expected_size: int) -> tuple[bytes, str]:
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        primary: BaseException | None = None
        digest = hashlib.sha256()
        sent = 0
        try:
            reader, writer = await asyncio.open_connection(
                self._host, self._port, limit=_MAX_RESPONSE_SIZE
            )
            writer.write(b"zINSTREAM\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                chunk = await asyncio.to_thread(os.read, descriptor, _CHUNK_SIZE)
                if not chunk:
                    break
                sent += len(chunk)
                if sent > _MAX_FILE_SIZE or sent > expected_size:
                    raise _scanner_unavailable()
                digest.update(chunk)
                writer.write(len(chunk).to_bytes(4, "big"))
                writer.write(chunk)
                await writer.drain()
            if sent != expected_size:
                raise _scanner_unavailable()
            writer.write((0).to_bytes(4, "big"))
            await writer.drain()
            response = await self._read_single_record(reader)
            return response, digest.hexdigest()
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if writer is not None:
                await self._close_writer(writer, primary=primary)

    async def _command(self, command: bytes) -> bytes:
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        primary: BaseException | None = None
        try:
            reader, writer = await asyncio.open_connection(
                self._host, self._port, limit=_MAX_RESPONSE_SIZE
            )
            writer.write(command)
            await writer.drain()
            return await self._read_single_record(reader)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            if writer is not None:
                await self._close_writer(writer, primary=primary)

    @staticmethod
    async def _read_single_record(reader: asyncio.StreamReader) -> bytes:
        record = await reader.readuntil(b"\0")
        if not 1 < len(record) <= _MAX_RESPONSE_SIZE:
            raise _scanner_unavailable()
        # clamd closes a single-command connection after its one NUL-terminated
        # response.  Only EOF proves that the response is complete; a short
        # period without bytes does not rule out a delayed second record.
        extra = await reader.read(_MAX_RESPONSE_SIZE + 1)
        if extra:
            raise _scanner_unavailable()
        return record[:-1]

    @staticmethod
    async def _close_writer(
        writer: asyncio.StreamWriter, *, primary: BaseException | None
    ) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), _CLOSE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            if primary is None:
                raise _scanner_unavailable() from None

    @staticmethod
    def _hash_descriptor(descriptor: int) -> str:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, _CHUNK_SIZE):
            total += len(chunk)
            if total > _MAX_FILE_SIZE:
                raise _scanner_unavailable()
            digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
        identity_matches = (
            left.st_dev == right.st_dev and left.st_ino == right.st_ino
            if left.st_ino and right.st_ino
            else True
        )
        return (
            identity_matches
            and left.st_size == right.st_size
            and left.st_mtime_ns == right.st_mtime_ns
        )


def build_clamav_scanner() -> ClamAVScanner:
    try:
        config = ClamAVScannerConfig(
            host=os.environ["KG_PRIVATE_FILE_CLAMD_HOST"].strip(),
            port=int(os.environ["KG_PRIVATE_FILE_CLAMD_PORT"]),
            storage_root=Path(os.environ["KG_PRIVATE_FILE_STORAGE_ROOT"]),
            engine_version=os.environ["KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION"].strip(),
            scan_timeout_seconds=float(
                os.environ.get("KG_PRIVATE_FILE_SCANNER_TIMEOUT_SECONDS", "30")
            ),
            max_signature_age_hours=int(
                os.environ.get(
                    "KG_PRIVATE_FILE_SCANNER_MAX_SIGNATURE_AGE_HOURS", "48"
                )
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise _configuration_invalid() from None
    return ClamAVScanner(config)
