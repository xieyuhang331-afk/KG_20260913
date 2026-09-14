from __future__ import annotations

import asyncio
import binascii
import io
import os
import struct
import zipfile

import pytest

from app.modules.private_file.clamav_scanner import build_clamav_scanner
from app.modules.private_file.ports import PrivateFileScannerUnavailable

pytestmark = pytest.mark.skipif(
    os.getenv("KG_RUN_S1_CLAMAV_INTEGRATION") != "1",
    reason="S1_REAL_CLAMD_NOT_REQUESTED",
)


def _eicar_in_memory_only() -> bytes:
    # Deliberately assembled at runtime: the harmless standard sample never lands
    # as a contiguous host-worktree file.
    pieces = (
        b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$",
        b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!",
        b"$H+H*",
    )
    return b"".join(pieces)


def _zip_in_memory(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()


def _encrypted_zip_in_memory() -> bytes:
    name = b"synthetic.txt"
    content = b"synthetic encrypted container"
    password = b"synthetic-password"
    checksum = binascii.crc32(content) & 0xFFFFFFFF
    import zlib

    compressor = zlib.compressobj(level=9, wbits=-15)
    compressed = compressor.compress(content) + compressor.flush()
    keys = [0x12345678, 0x23456789, 0x34567890]

    def crc32_byte(value: int, byte: int) -> int:
        return binascii.crc32(bytes((byte,)), value) & 0xFFFFFFFF

    def update(byte: int) -> None:
        keys[0] = crc32_byte(keys[0], byte)
        keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        keys[2] = crc32_byte(keys[2], (keys[1] >> 24) & 0xFF)

    def encrypt(payload: bytes) -> bytes:
        result = bytearray()
        for byte in payload:
            temporary = (keys[2] | 2) & 0xFFFFFFFF
            result.append(byte ^ (((temporary * (temporary ^ 1)) >> 8) & 0xFF))
            update(byte)
        return bytes(result)

    for byte in password:
        update(byte)
    header = bytes(range(11)) + bytes(((checksum >> 24) & 0xFF,))
    encrypted = encrypt(header + compressed)
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        1,
        8,
        0,
        0,
        checksum,
        len(encrypted),
        len(content),
        len(name),
        0,
    ) + name + encrypted
    central = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        1,
        8,
        0,
        0,
        checksum,
        len(encrypted),
        len(content),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    ) + name
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        1,
        1,
        len(central),
        len(local),
        0,
    )
    return local + central + end


async def _raw_instream(host: str, port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection(host, port, limit=4096)
    try:
        writer.write(b"zINSTREAM\0")
        writer.write(len(payload).to_bytes(4, "big"))
        writer.write(payload)
        writer.write((0).to_bytes(4, "big"))
        await writer.drain()
        return await asyncio.wait_for(reader.readuntil(b"\0"), timeout=30)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_S1_F01_真实引擎health与良性PDF(tmp_path, monkeypatch) -> None:
    root = tmp_path.resolve()
    path = root / "synthetic.pdf"
    path.write_bytes(b"%PDF-1.4\n% synthetic clean document\n%%EOF\n")
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(root))
    scanner = build_clamav_scanner()
    assert await scanner.health() is True
    assert await scanner.scan(path, mime_type="application/pdf") == "CLEAN"
    assert path.read_bytes().startswith(b"%PDF-1.4")


@pytest.mark.asyncio
async def test_S1_F02_标准无害检测样本只经内存流并真实FOUND() -> None:
    host = os.environ["KG_PRIVATE_FILE_CLAMD_HOST"]
    port = int(os.environ["KG_PRIVATE_FILE_CLAMD_PORT"])
    result = await _raw_instream(host, port, _eicar_in_memory_only())
    assert result.startswith(b"stream: ") and result.endswith(b" FOUND\0")


@pytest.mark.asyncio
async def test_S1_F03_超stream限额绝不形成CLEAN() -> None:
    host = os.environ["KG_PRIVATE_FILE_CLAMD_HOST"]
    port = int(os.environ["KG_PRIVATE_FILE_CLAMD_PORT"])
    payload = b"0" * (11 * 1024 * 1024 + 1)
    try:
        result = await _raw_instream(host, port, payload)
    except (ConnectionError, TimeoutError):
        return
    assert result != b"stream: OK\0"
    assert result.endswith((b" ERROR\0", b" FOUND\0"))


@pytest.mark.asyncio
async def test_S1_F04_scan时重新校验签名状态(tmp_path, monkeypatch) -> None:
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    scanner = build_clamav_scanner()
    assert await scanner.scan(path, mime_type="application/pdf") == "CLEAN"
    scanner._max_signature_age_hours = -1
    with pytest.raises(PrivateFileScannerUnavailable):
        await scanner.scan(path, mime_type="application/pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda: _zip_in_memory([("large.bin", b"0" * (65 * 1024 * 1024))]),
        lambda: _zip_in_memory([(f"entry-{index}", b"") for index in range(1001)]),
        lambda: _encrypted_zip_in_memory(),
    ],
    ids=["max-scan-size", "max-files", "encrypted-archive"],
)
async def test_S1_F05_解压文件数或加密边界绝不形成CLEAN(payload_factory) -> None:
    host = os.environ["KG_PRIVATE_FILE_CLAMD_HOST"]
    port = int(os.environ["KG_PRIVATE_FILE_CLAMD_PORT"])
    result = await _raw_instream(host, port, payload_factory())
    assert result != b"stream: OK\0"
    assert result.endswith((b" ERROR\0", b" FOUND\0"))


@pytest.mark.asyncio
async def test_S1_F06_递归边界绝不形成CLEAN() -> None:
    payload = b"synthetic"
    for depth in range(18):
        payload = _zip_in_memory([(f"level-{depth}.zip", payload)])
    result = await _raw_instream(
        os.environ["KG_PRIVATE_FILE_CLAMD_HOST"],
        int(os.environ["KG_PRIVATE_FILE_CLAMD_PORT"]),
        payload,
    )
    assert result != b"stream: OK\0"
    assert result.endswith((b" ERROR\0", b" FOUND\0"))
