from app.modules.private_file.schemas import UploadInitiateRequest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_资质附件只复用既有扫描读取恢复边界():
    import inspect

    from app.modules.private_file import service as private_file_service

    value = UploadInitiateRequest(purpose="THERAPIST_QUALIFICATION", size=1, mime_type="application/pdf", sha256="a" * 64)
    assert value.purpose == "THERAPIST_QUALIFICATION"
    repository = (ROOT / "app/modules/private_file/repository.py").read_text(encoding="utf-8")
    service = (ROOT / "app/modules/private_file/service.py").read_text(encoding="utf-8")
    assert "therapist_qualification_file_relation_v1" in repository
    assert "TherapistQualificationAttachmentModel" not in repository
    assert "async def closed_access_snapshot(" in repository
    assert '"qualification_bound,reviewer_access "' in repository
    for operation in (
        private_file_service.authorize_file_access,
        private_file_service.read_authorized_content,
    ):
        assert "_closed_access_snapshot(" in inspect.getsource(operation)
    assert 'snapshot["qualification_bound"]' in service
