from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError


def _u(n):
    return UUID(f"00000000-0000-7000-8000-{n:012d}")


def test_D11_G01_检测报告文件purpose与附件数量去重():
    from app.modules.private_file.schemas import UploadInitiateRequest
    from app.modules.user_health.schemas import FormalDetectionReportCreateRequest

    UploadInitiateRequest.model_validate({"purpose": "DETECTION_REPORT", "size": 512, "mime_type": "application/pdf", "sha256": "a" * 64})
    base = {"report_type": "LAB_REPORT", "measured_at": "2026-08-20T09:00:00+08:00", "source_type": "APP"}
    assert FormalDetectionReportCreateRequest.model_validate({**base, "file_ids": [_u(1)]}).file_ids == [_u(1)]
    for file_ids in ([], [_u(1), _u(1)], [_u(n) for n in range(1, 12)]):
        with pytest.raises(ValidationError):
            FormalDetectionReportCreateRequest.model_validate({**base, "file_ids": file_ids})


def test_D11_绑定计划要求CLEAN同主体同case同tenant并稳定排序():
    from app.modules.user_health.service import build_report_attachment_plan

    scope = {"subject_member_id": _u(10), "service_case_id": _u(11), "tenant_id": 4}
    rows = [{"file_id": _u(n), "status": "CLEAN", "purpose": "DETECTION_REPORT", **scope, "bound": False} for n in (3, 2)]
    assert [item.file_id for item in build_report_attachment_plan(files=rows, **scope)] == [_u(2), _u(3)]
    for key, value in (("status", "PENDING_SCAN"), ("purpose", "BUSINESS_LICENSE"), ("tenant_id", 9), ("bound", True)):
        changed = [dict(row) for row in rows]; changed[0][key] = value
        with pytest.raises(ValueError, match="^PRIVATE_FILE_BIND_CONFLICT$"):
            build_report_attachment_plan(files=changed, **scope)


def test_D12_D15_报告原件读取每次复核currentness与permission且证据不含对象路径():
    from app.modules.private_file.domain import DetectionReportAccessContext, build_detection_report_access_evidence

    assert DetectionReportAccessContext("SELF", True, True, True, True, ()).may_read_original()
    assert DetectionReportAccessContext("PROXY", True, True, True, True, ("DAILY_VIEW",)).may_read_original()
    assert not DetectionReportAccessContext("PROXY", True, True, True, True, ("REPORT_UPLOAD",)).may_read_original()
    assert not DetectionReportAccessContext("INSTITUTION", True, True, True, True, ()).may_read_original()
    assert not DetectionReportAccessContext("THERAPIST", False, True, True, True, ()).may_read_original()
    evidence = build_detection_report_access_evidence(file_id=_u(20), subject_member_id=_u(21), service_case_id=_u(22), actor_user_id=1, actor_version=2, grant_or_assignment_version=3, expires_at=datetime(2026, 8, 20, 1, tzinfo=timezone.utc))
    assert "object_key" not in evidence


def test_PrivateFile生产访问必须调用Slice4受限权威并写访问审计():
    import inspect
    from app.modules.private_file import service

    authorize = inspect.getsource(service.authorize_file_access)
    content = inspect.getsource(service.read_authorized_content)
    for source in (authorize, content):
        assert "report_file_authority" in source
    assert "REPORT_ORIGINAL_ACCESSED" in content
