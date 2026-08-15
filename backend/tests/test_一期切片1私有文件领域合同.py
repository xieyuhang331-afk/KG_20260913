from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.modules.private_file.domain import (
    PrivateFile,
    PrivateFileConflict,
    PrivateFileStatus,
)
from app.modules.private_file.service import (
    cleanup_orphan_private_file,
    issue_access_token,
    verify_access_token,
)


NOW = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
FILE_ID = UUID("0198a58f-4900-7000-8000-000000000010")


def upload(*, purpose: str = "BUSINESS_LICENSE") -> PrivateFile:
    return PrivateFile.initiate(
        file_id=FILE_ID,
        purpose=purpose,
        owner_user_id=81,
        declared_size=4,
        declared_mime_type="application/pdf",
        declared_sha256="a" * 64,
        object_key="slice1/01/file",
        expires_at=NOW + timedelta(minutes=15),
        now=NOW,
    )


@pytest.mark.parametrize("mime", ["text/plain", "image/svg+xml", "application/zip"])
def test_仅允许冻结的文件类型(mime: str):
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_TYPE_NOT_ALLOWED"):
        PrivateFile.initiate(
            file_id=FILE_ID,
            purpose="BUSINESS_LICENSE",
            owner_user_id=81,
            declared_size=4,
            declared_mime_type=mime,
            declared_sha256="a" * 64,
            object_key="slice1/01/file",
            expires_at=NOW + timedelta(minutes=15),
            now=NOW,
        )


def test_单文件十兆限制():
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_SIZE_INVALID"):
        PrivateFile.initiate(
            file_id=FILE_ID,
            purpose="BUSINESS_LICENSE",
            owner_user_id=81,
            declared_size=10 * 1024 * 1024 + 1,
            declared_mime_type="application/pdf",
            declared_sha256="a" * 64,
            object_key="slice1/01/file",
            expires_at=NOW + timedelta(minutes=15),
            now=NOW,
        )


def test_大小类型哈希必须逐项匹配():
    value = upload()
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_EVIDENCE_MISMATCH"):
        value.complete_upload(
            actual_size=4,
            actual_mime_type="application/pdf",
            actual_sha256="b" * 64,
            now=NOW,
        )
    assert value.status is PrivateFileStatus.UPLOAD_INITIATED


def test_只有clean文件可绑定业务():
    value = upload()
    value.complete_upload(
        actual_size=4,
        actual_mime_type="application/pdf",
        actual_sha256="a" * 64,
        now=NOW,
    )
    assert value.status is PrivateFileStatus.PENDING_SCAN
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_NOT_CLEAN"):
        value.bind(application_id=UUID("0198a58f-4900-7000-8000-000000000002"), now=NOW)
    value.record_scan("CLEAN", now=NOW)
    value.bind(application_id=UUID("0198a58f-4900-7000-8000-000000000002"), now=NOW)
    assert value.bound_at == NOW


@pytest.mark.parametrize("result", ["REJECTED", "SCAN_FAILED"])
def test_恶意或失败扫描永久阻止绑定(result: str):
    value = upload()
    value.complete_upload(
        actual_size=4,
        actual_mime_type="application/pdf",
        actual_sha256="a" * 64,
        now=NOW,
    )
    value.record_scan(result, now=NOW)
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_NOT_CLEAN"):
        value.bind(application_id=UUID("0198a58f-4900-7000-8000-000000000002"), now=NOW)


def test_过期上传不可完成且未绑定clean文件才可孤儿清理():
    value = upload()
    assert value.is_orphan_expired(NOW + timedelta(minutes=16))
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_UPLOAD_EXPIRED"):
        value.complete_upload(
            actual_size=4,
            actual_mime_type="application/pdf",
            actual_sha256="a" * 64,
            now=NOW + timedelta(minutes=16),
        )


def test_短时访问令牌使用稳定双段编码且所有签名字节均可验签(monkeypatch):
    monkeypatch.setenv(
        "KG_PRIVATE_FILE_ACCESS_SIGNING_KEY",
        "slice1-test-only-signing-key-32-bytes",
    )
    for index in range(512):
        file_id = f"file-{index}"
        token = issue_access_token(
            file_id=file_id,
            user_id=81,
            reason_code="REVIEW",
            expires_at=2_000_000_000,
            evidence_digest=f"{index:064x}",
        )
        assert token.count(".") == 1
        values = verify_access_token(token=token, file_id=file_id, user_id=81)
        assert values["evidence_digest"] == f"{index:064x}"


@pytest.mark.asyncio
async def test_已标记删除但物理unlink失败的孤儿可在下一周期恢复清理(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("KG_PRIVATE_FILE_STORAGE_ROOT", str(tmp_path))
    object_key = "slice1/2026/08/retry-file"
    path = tmp_path / object_key
    path.parent.mkdir(parents=True)
    path.write_bytes(b"safe-test-content")
    row = SimpleNamespace(status="DELETED", object_key=object_key)
    monkeypatch.setattr(
        "app.modules.private_file.service.PrivateFileRepository.get",
        AsyncMock(return_value=row),
    )
    assert await cleanup_orphan_private_file(object(), "retry-file") is True
    assert not path.exists()
