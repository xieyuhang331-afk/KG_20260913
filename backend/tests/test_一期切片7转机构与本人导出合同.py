from datetime import datetime, timedelta, timezone
from inspect import signature
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pydantic import ValidationError

from app.modules.service_fulfillment.domain import (
    ExportStatus,
    TransferStatus,
    build_personal_data_export_archive,
    next_transfer_status,
)
from app.modules.service_fulfillment.schemas import (
    DataExportCreateRequest,
    OneTimeDownloadDTO,
    TransferCreateRequest,
    TransferScopeConfirmRequest,
)
from app.modules.private_file.domain import (
    PrivateFileConflict,
    validate_generated_export_archive,
)
from app.modules.private_file.schemas import UploadInitiateRequest
from app.modules.private_file.service import issue_access_token, verify_access_token
from app.core.security import CurrentUser, create_access_token
from app.modules.service_fulfillment.api import _require_current_step_up, create_export
from app.modules.service_fulfillment.service import ServiceFulfillmentError
from app.tasks.slice7_service_fulfillment_tasks import _export_failure_code


UUID7 = UUID("0198f1c0-0000-7000-8000-000000000001")


def test_D22_D30_转机构状态机只允许冻结跃迁() -> None:
    assert next_transfer_status(
        TransferStatus.REQUESTED_BY_USER, TransferStatus.NEW_INSTITUTION_REVIEWING
    ) is TransferStatus.NEW_INSTITUTION_REVIEWING
    assert next_transfer_status(
        TransferStatus.NEW_INSTITUTION_REVIEWING, TransferStatus.ACCEPTED
    ) is TransferStatus.ACCEPTED
    assert next_transfer_status(
        TransferStatus.ACCEPTED, TransferStatus.OLD_INSTITUTION_CLOSING
    ) is TransferStatus.OLD_INSTITUTION_CLOSING
    assert next_transfer_status(
        TransferStatus.OLD_INSTITUTION_CLOSING, TransferStatus.USER_SCOPE_CONFIRMED
    ) is TransferStatus.USER_SCOPE_CONFIRMED
    assert next_transfer_status(
        TransferStatus.USER_SCOPE_CONFIRMED, TransferStatus.TRANSFERRED
    ) is TransferStatus.TRANSFERRED
    with pytest.raises(ValueError, match="TRANSFER_STATE_CONFLICT"):
        next_transfer_status(TransferStatus.REQUESTED_BY_USER, TransferStatus.TRANSFERRED)


def test_D25_转移范围只允许冻结业务目录() -> None:
    request = TransferCreateRequest(
        target_tenant_id=UUID7,
        requested_scope=("PROFILE", "REPORT", "CANONICAL_FACT", "ASSESSMENT", "APPROVED_PLAN", "MILESTONE", "SERVICE_SUMMARY"),
        expected_version=1,
    )
    assert request.requested_scope[0] == "PROFILE"
    with pytest.raises(ValidationError):
        TransferScopeConfirmRequest(
            expected_version=1,
            exact_scope=("PROFILE", "IDENTITY_CIPHERTEXT"),
        )


def test_D31_D40_导出范围和一次性下载DTO不泄漏内部位置() -> None:
    request = DataExportCreateRequest(
        requested_scope=("PROFILE", "REPORT", "CANONICAL_FACT"),
        reason="PERSONAL_ARCHIVE",
    )
    assert request.requested_scope == ("PROFILE", "REPORT", "CANONICAL_FACT")
    with pytest.raises(ValidationError):
        DataExportCreateRequest(
            requested_scope=("PROFILE", "CREDENTIAL"),
            reason="PERSONAL_ARCHIVE",
        )
    fields = set(OneTimeDownloadDTO.model_fields)
    assert fields == {"access_token", "expires_at", "filename", "content_type"}
    assert {item.value for item in ExportStatus} == {
        "REQUESTED", "GENERATING", "READY", "DOWNLOADED", "EXPIRED", "FAILED", "CANCELLED"
    }


def _archive(payload: bytes = b"{}") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", payload)
    return buffer.getvalue()


def test_D33_普通上传仍拒绝ZIP且内部ZIP用途固定() -> None:
    with pytest.raises(ValidationError):
        UploadInitiateRequest(
            purpose="PERSONAL_DATA_EXPORT",
            size=128,
            mime_type="application/zip",
            sha256="ab" * 32,
        )

    archive = _archive()
    evidence = validate_generated_export_archive(
        purpose="PERSONAL_DATA_EXPORT",
        data=archive,
    )
    assert evidence.size == len(archive)
    assert len(evidence.sha256) == 64
    assert evidence.mime_type == "application/zip"

    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_EXPORT_PURPOSE_REQUIRED"):
        validate_generated_export_archive(purpose="DETECTION_REPORT", data=archive)


def test_D33_D37_内部ZIP拒绝伪装空包及超限内容() -> None:
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_EXPORT_ARCHIVE_INVALID"):
        validate_generated_export_archive(
            purpose="PERSONAL_DATA_EXPORT",
            data=b"%PDF-not-a-zip",
        )
    with pytest.raises(PrivateFileConflict, match="PRIVATE_FILE_EXPORT_ARCHIVE_INVALID"):
        validate_generated_export_archive(
            purpose="PERSONAL_DATA_EXPORT",
            data=_archive(b"x" * (10 * 1024 * 1024 + 1)),
        )
    assert _export_failure_code(RuntimeError("EXPORT_ARCHIVE_TOO_LARGE")) == (
        "EXPORT_ARCHIVE_TOO_LARGE"
    )
    assert _export_failure_code(RuntimeError("database unavailable")) is None


def test_D35_D39_导出Token绑定一次性凭据标识且篡改过期拒绝(
    monkeypatch,
) -> None:
    monkeypatch.setenv("KG_PRIVATE_FILE_ACCESS_SIGNING_KEY", "slice7-test-signing-key")
    token = issue_access_token(
        file_id=str(UUID7),
        user_id=7,
        reason_code="PERSONAL_ARCHIVE",
        expires_at=4_102_444_800,
        evidence_digest="ab" * 32,
        access_scope="EXPORT",
        token_id="0198f1c0-0000-7000-8000-000000000002",
    )
    values = verify_access_token(
        token=token,
        file_id=str(UUID7),
        user_id=7,
        access_scope="EXPORT",
    )
    assert values["token_id"] == "0198f1c0-0000-7000-8000-000000000002"

    with pytest.raises(Exception, match="PRIVATE_FILE_ACCESS_INVALID"):
        verify_access_token(
            token=token[:-1] + ("A" if token[-1] != "A" else "B"),
            file_id=str(UUID7),
            user_id=7,
            access_scope="EXPORT",
        )
    expired = issue_access_token(
        file_id=str(UUID7),
        user_id=7,
        reason_code="PERSONAL_ARCHIVE",
        expires_at=1,
        evidence_digest="ab" * 32,
        access_scope="EXPORT",
        token_id="0198f1c0-0000-7000-8000-000000000002",
    )
    with pytest.raises(Exception, match="PRIVATE_FILE_ACCESS_INVALID"):
        verify_access_token(
            token=expired,
            file_id=str(UUID7),
            user_id=7,
            access_scope="EXPORT",
        )


def test_D34_D37_导出Manifest与ZIP确定且排除内部字段() -> None:
    snapshot = {
        "export_id": str(UUID7),
        "subject_member_id": "0198f1c0-0000-7000-8000-000000000002",
        "requested_scope": ["PROFILE", "MILESTONE"],
        "source_versions": {"profile": 3, "milestone": 5},
        "data": {
            "PROFILE": {"height_cm": "168.0", "version": 3},
            "MILESTONE": [{"code": "D0", "status": "COMPLETED", "version": 5}],
        },
    }
    first = build_personal_data_export_archive(snapshot)
    second = build_personal_data_export_archive(snapshot)
    assert first.data == second.data
    assert first.manifest_digest == second.manifest_digest
    assert first.artifact_digest == second.artifact_digest

    with ZipFile(BytesIO(first.data)) as archive:
        assert archive.namelist() == [
            "manifest.json",
            "data/milestone.json",
            "data/profile.json",
        ]
        assert b"credential" not in archive.read("manifest.json").lower()

    with pytest.raises(ValueError, match="EXPORT_SNAPSHOT_FORBIDDEN_FIELD"):
        build_personal_data_export_archive(
            {
                **snapshot,
                "data": {
                    **snapshot["data"],
                    "PROFILE": {"identity_ciphertext": "forbidden"},
                },
            }
        )


def test_D34_导出Manifest拒绝请求范围与快照不一致() -> None:
    with pytest.raises(ValueError, match="EXPORT_SNAPSHOT_SCOPE_MISMATCH"):
        build_personal_data_export_archive(
            {
                "export_id": str(UUID7),
                "subject_member_id": "0198f1c0-0000-7000-8000-000000000002",
                "requested_scope": ["PROFILE", "REPORT"],
                "source_versions": {},
                "data": {"PROFILE": {}},
            }
        )


def test_D31_导出StepUp必须绑定当前会员且十分钟内签发(monkeypatch) -> None:
    assert "step_up" in signature(create_export).parameters

    monkeypatch.setattr(
        "app.core.security.get_settings",
        lambda: SimpleNamespace(
            jwt_algorithm="HS256",
            jwt_secret_key="slice7-step-up-contract-secret",
            jwt_access_token_expire_minutes=15,
        ),
    )
    actor = CurrentUser(id=7, role="member", tenant_id=None)
    now = datetime.now(timezone.utc)
    current = create_access_token(
        {
            "sub": "7",
            "role": "member",
            "tenant_id": None,
            "iat": now,
            "exp": now + timedelta(minutes=15),
        }
    )
    _require_current_step_up(current, actor)

    stale = create_access_token(
        {
            "sub": "7",
            "role": "member",
            "tenant_id": None,
            "iat": now - timedelta(seconds=601),
            "exp": now + timedelta(minutes=4),
        }
    )
    other_member = create_access_token(
        {
            "sub": "8",
            "role": "member",
            "tenant_id": None,
            "iat": now,
            "exp": now + timedelta(minutes=15),
        }
    )
    for token in (stale, other_member, current[:-1] + "x"):
        with pytest.raises(ServiceFulfillmentError, match="STEP_UP_FORBIDDEN"):
            _require_current_step_up(token, actor)
