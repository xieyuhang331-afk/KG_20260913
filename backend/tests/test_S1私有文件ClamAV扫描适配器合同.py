from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.modules.private_file.clamav_scanner import (
    ClamAVScanner,
    ClamAVScannerConfig,
    build_clamav_scanner,
)
from app.modules.private_file.ports import PrivateFileScannerUnavailable


class _ScriptedClamd:
    def __init__(self, responses: list[bytes], *, block_scan: bool = False) -> None:
        self.responses = responses.copy()
        self.block_scan = block_scan
        self.commands: list[bytes] = []
        self.streams: list[bytes] = []
        self.stream_received = asyncio.Event()
        self.server: asyncio.Server | None = None

    async def __aenter__(self) -> _ScriptedClamd:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_: object) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    @property
    def port(self) -> int:
        assert self.server is not None
        return int(self.server.sockets[0].getsockname()[1])

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            command = await reader.readuntil(b"\0")
            self.commands.append(command)
            if command == b"zINSTREAM\0":
                content = bytearray()
                while True:
                    size = int.from_bytes(await reader.readexactly(4), "big")
                    if size == 0:
                        break
                    content.extend(await reader.readexactly(size))
                self.streams.append(bytes(content))
                self.stream_received.set()
                if self.block_scan:
                    await reader.read()
                    return
            response = self.responses.pop(0)
            writer.write(response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()


def _version_response(*, age_hours: int = 0) -> bytes:
    from datetime import timedelta

    stamp = datetime.now(UTC) - timedelta(hours=age_hours)
    rendered = stamp.strftime("%a %b %d %H:%M:%S %Y")
    return (
        f"ClamAV 1.4.6/28000/{rendered}| COMMANDS: PING VERSION "
        "VERSIONCOMMANDS INSTREAM\0"
    ).encode("ascii")


def _config(root: Path, port: int, *, timeout: float = 1.0) -> ClamAVScannerConfig:
    return ClamAVScannerConfig(
        host="127.0.0.1",
        port=port,
        storage_root=root,
        engine_version="1.4.6",
        scan_timeout_seconds=timeout,
        max_signature_age_hours=48,
    )


def test_S1_R01_工厂闭合配置且无需新增Python依赖(monkeypatch, tmp_path) -> None:
    values = {
        "KG_PRIVATE_FILE_CLAMD_HOST": "127.0.0.1",
        "KG_PRIVATE_FILE_CLAMD_PORT": "3310",
        "KG_PRIVATE_FILE_STORAGE_ROOT": str(tmp_path),
        "KG_PRIVATE_FILE_SCANNER_ENGINE_VERSION": "1.4.6",
        "KG_PRIVATE_FILE_SCANNER_TIMEOUT_SECONDS": "30",
        "KG_PRIVATE_FILE_SCANNER_MAX_SIGNATURE_AGE_HOURS": "48",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    scanner = build_clamav_scanner()
    assert isinstance(scanner, ClamAVScanner)
    for invalid in ("0.0.0.0", "localhost", "clamd", "192.0.2.1"):
        monkeypatch.setenv("KG_PRIVATE_FILE_CLAMD_HOST", invalid)
        with pytest.raises(RuntimeError, match="PRIVATE_FILE_SCANNER_CONFIGURATION_INVALID"):
            build_clamav_scanner()


def test_S1_R01_Port显式包含异步health合同() -> None:
    from app.modules.private_file.ports import PrivateFileScanner

    source = inspect.getsource(PrivateFileScanner)
    assert "async def health(self) -> bool" in source
    assert "async def scan(self, path: Path, *, mime_type: str) -> str" in source


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scan_response", "expected"),
    [
        (b"stream: OK\0", "CLEAN"),
        (b"stream: Synthetic-Test-Signature FOUND\0", "REJECTED"),
    ],
    ids=["exact-clean", "single-detection"],
)
async def test_S1_R02_R03_仅精确OK或单一FOUND形成领域结果(
    tmp_path, scan_response, expected
) -> None:
    content = b"synthetic-private-file"
    path = tmp_path / "object.bin"
    path.write_bytes(content)
    async with _ScriptedClamd(
        [b"PONG\0", _version_response(), scan_response]
    ) as server:
        scanner = ClamAVScanner(_config(tmp_path, server.port))
        assert await scanner.scan(path, mime_type="application/pdf") == expected
    assert server.streams == [content]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"stream: ERROR\0",
        b"stream: UNKNOWN\0",
        b"stream: OK\n\0",
        b"stream: One FOUND\0stream: OK\0",
        b"other: OK\0",
        b"stream: \xff FOUND\0",
    ],
    ids=["error", "unknown", "extra-line", "multiple", "wrong-target", "non-ascii"],
)
async def test_S1_R04_未知畸形或多结果绝不映射CLEAN(tmp_path, response) -> None:
    path = tmp_path / "object.bin"
    path.write_bytes(b"synthetic")
    async with _ScriptedClamd(
        [b"PONG\0", _version_response(), response]
    ) as server:
        scanner = ClamAVScanner(_config(tmp_path, server.port))
        with pytest.raises(
            PrivateFileScannerUnavailable,
            match="PRIVATE_FILE_SCANNER_UNAVAILABLE",
        ):
            await scanner.scan(path, mime_type="application/pdf")


@pytest.mark.asyncio
async def test_S1_R05_R06_health要求版本命令与签名新鲜且在Worker预算内(tmp_path) -> None:
    async with _ScriptedClamd([b"PONG\0", _version_response()]) as healthy:
        assert await ClamAVScanner(_config(tmp_path, healthy.port)).health() is True
    async with _ScriptedClamd([b"PONG\0", _version_response(age_hours=72)]) as old:
        assert await ClamAVScanner(_config(tmp_path, old.port)).health() is False
    async with _ScriptedClamd(
        [b"PONG\0", b"ClamAV 1.4.5/28000/Thu Jan 01 00:00:00 2026| COMMANDS: INSTREAM\0"]
    ) as wrong:
        assert await ClamAVScanner(_config(tmp_path, wrong.port)).health() is False


@pytest.mark.asyncio
async def test_S1_R07_R08_timeout与取消关闭所属连接但不终止服务(tmp_path) -> None:
    path = tmp_path / "object.bin"
    path.write_bytes(b"synthetic")
    async with _ScriptedClamd(
        [b"PONG\0", _version_response(), b"stream: OK\0"], block_scan=True
    ) as server:
        scanner = ClamAVScanner(_config(tmp_path, server.port))
        scanner._scan_timeout_seconds = 0.05
        with pytest.raises(PrivateFileScannerUnavailable):
            await scanner.scan(path, mime_type="application/pdf")
        assert server.server is not None and server.server.is_serving()

    async with _ScriptedClamd(
        [b"PONG\0", _version_response(), b"stream: OK\0"], block_scan=True
    ) as server:
        scanner = ClamAVScanner(_config(tmp_path, server.port, timeout=1.0))
        task = asyncio.create_task(scanner.scan(path, mime_type="application/pdf"))
        await asyncio.wait_for(server.stream_received.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert server.server is not None and server.server.is_serving()


@pytest.mark.asyncio
async def test_S1_R08_连接关闭取消优先于既有普通异常() -> None:
    cancellation = asyncio.CancelledError("S1_CONNECTION_CLOSE_CANCELLED")

    class Writer:
        def __init__(self) -> None:
            self.close_calls = 0
            self.wait_closed_calls = 0

        def close(self) -> None:
            self.close_calls += 1

        async def wait_closed(self) -> None:
            self.wait_closed_calls += 1
            raise cancellation

    writer = Writer()
    with pytest.raises(asyncio.CancelledError) as raised:
        await ClamAVScanner._close_writer(
            writer, primary=RuntimeError("S1_PRIMARY_SCAN_FAILURE")
        )

    assert raised.value is cancellation
    assert writer.close_calls == 1
    assert writer.wait_closed_calls == 1


@pytest.mark.asyncio
async def test_S1_R09_路径symlink逃逸与不支持MIME在联网前拒绝(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"synthetic")
    link = root / "link.bin"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("S1_TEST_PLATFORM_CANNOT_CREATE_SYMLINK")
    scanner = ClamAVScanner(_config(root, 1))
    with pytest.raises(PrivateFileScannerUnavailable):
        await scanner.scan(link, mime_type="application/pdf")
    inside = root / "inside.bin"
    inside.write_bytes(b"synthetic")
    with pytest.raises(PrivateFileScannerUnavailable):
        await scanner.scan(inside, mime_type="application/zip")


@pytest.mark.asyncio
async def test_S1_R10_打开句柄后路径被替换绝不形成CLEAN(tmp_path, monkeypatch) -> None:
    path = tmp_path / "object.bin"
    original = b"synthetic-original"
    path.write_bytes(original)
    scanner = ClamAVScanner(_config(tmp_path, 1))
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"x" * len(original))
    original_stat = Path.stat
    switched = False

    def current_path_stat(target: Path, *args, **kwargs):
        if switched and target == path:
            return original_stat(replacement, *args, **kwargs)
        return original_stat(target, *args, **kwargs)

    async def replace_after_stream(descriptor: int, expected_size: int):
        import hashlib

        nonlocal switched
        assert expected_size == len(original)
        switched = True
        return b"stream: OK", hashlib.sha256(original).hexdigest()

    monkeypatch.setattr(Path, "stat", current_path_stat)
    monkeypatch.setattr(scanner, "_instream", replace_after_stream)
    with pytest.raises(PrivateFileScannerUnavailable):
        await scanner._scan_open_file(path, mime_type="application/pdf")


@pytest.mark.asyncio
async def test_S1_R11_进程内扫描并发固定为二且不暴露路径(tmp_path, monkeypatch) -> None:
    scanner = ClamAVScanner(_config(tmp_path, 1))
    active = 0
    peak = 0

    async def healthy() -> bool:
        return True

    async def bounded_scan(path: Path, *, mime_type: str) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "CLEAN"

    monkeypatch.setattr(scanner, "health", healthy)
    monkeypatch.setattr(scanner, "_scan_open_file", bounded_scan)
    results = await asyncio.gather(
        *(scanner.scan(tmp_path / f"object-{index}", mime_type="application/pdf") for index in range(5))
    )
    assert results == ["CLEAN"] * 5
    assert peak == 2


@pytest.mark.asyncio
async def test_S1_R12_超长引擎响应稳定fail_closed(tmp_path) -> None:
    path = tmp_path / "object.bin"
    path.write_bytes(b"synthetic")
    async with _ScriptedClamd(
        [b"PONG\0", _version_response(), b"x" * 4097 + b"\0"]
    ) as server:
        scanner = ClamAVScanner(_config(tmp_path, server.port))
        with pytest.raises(
            PrivateFileScannerUnavailable,
            match="PRIVATE_FILE_SCANNER_UNAVAILABLE",
        ):
            await scanner.scan(path, mime_type="application/pdf")


@pytest.mark.asyncio
async def test_S1_R04_OK后连接未结束且延迟第二记录绝不接受() -> None:
    reader = asyncio.StreamReader(limit=4096)
    reader.feed_data(b"stream: OK\0")
    pending = asyncio.create_task(ClamAVScanner._read_single_record(reader))
    await asyncio.sleep(0.03)
    assert pending.done() is False

    reader.feed_data(b"stream: Synthetic-Delayed FOUND\0")
    reader.feed_eof()
    with pytest.raises(
        PrivateFileScannerUnavailable,
        match="PRIVATE_FILE_SCANNER_UNAVAILABLE",
    ):
        await pending


@pytest.mark.asyncio
async def test_S1_R04_OK后连接不结束由调用方总预算超时() -> None:
    reader = asyncio.StreamReader(limit=4096)
    reader.feed_data(b"stream: OK\0")
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.03):
            await ClamAVScanner._read_single_record(reader)


@pytest.mark.asyncio
async def test_S1_R04_单一记录必须以EOF完成() -> None:
    reader = asyncio.StreamReader(limit=4096)
    reader.feed_data(b"stream: OK\0")
    reader.feed_eof()
    assert await ClamAVScanner._read_single_record(reader) == b"stream: OK"


@pytest.mark.asyncio
async def test_S1_R08_等待响应结束期间取消仍优先传播() -> None:
    reader = asyncio.StreamReader(limit=4096)
    reader.feed_data(b"stream: OK\0")
    pending = asyncio.create_task(ClamAVScanner._read_single_record(reader))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending


def test_S1_R18_clamd唯一配置拒绝部分扫描与敏感日志() -> None:
    config_path = Path(__file__).resolve().parents[1] / "clamd私有文件扫描_V1.conf"
    text = config_path.read_text(encoding="utf-8")
    required = {
        "StreamMaxLength 11M",
        "MaxFileSize 11M",
        "MaxScanSize 12M",
        "MaxRecursion 16",
        "MaxFiles 1000",
        "MaxScanTime 25000",
        "ScanArchive yes",
        "ScanPDF yes",
        "AlertEncrypted yes",
        "AlertExceedsMax yes",
        "LogClean no",
        "LogVerbose no",
        "ExtendedDetectionInfo no",
        "LeaveTemporaryFiles no",
        "GenerateMetadataJson no",
        "FollowFileSymlinks no",
        "FollowDirectorySymlinks no",
    }
    assert required <= set(text.splitlines())
    assert "VirusEvent" not in text
    assert "DatabaseMirror" not in text
    assert "OnAccess" not in text


def test_S1_R16_DTO与异常repr不包含路径或响应() -> None:
    import app.modules.private_file.clamav_scanner as module

    fields = ClamAVScannerConfig.__dataclass_fields__
    assert fields["storage_root"].repr is False
    source = inspect.getsource(module)
    assert 'PrivateFileScannerUnavailable(_UNAVAILABLE)' in source
    assert "raise PrivateFileScannerUnavailable(str(" not in source
