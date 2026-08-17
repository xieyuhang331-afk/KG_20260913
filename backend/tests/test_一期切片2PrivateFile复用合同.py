from app.modules.private_file.schemas import UploadInitiateRequest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_资质附件只复用既有扫描读取恢复边界():
    value = UploadInitiateRequest(purpose="THERAPIST_QUALIFICATION", size=1, mime_type="application/pdf", sha256="a" * 64)
    assert value.purpose == "THERAPIST_QUALIFICATION"
    repository = (ROOT / "app/modules/private_file/repository.py").read_text(encoding="utf-8")
    service = (ROOT / "app/modules/private_file/service.py").read_text(encoding="utf-8")
    assert "therapist_qualification_file_relation_v1" in repository
    assert "TherapistQualificationAttachmentModel" not in repository
    assert 'row["reviewer_access"]' in service
    assert 'snapshot["qualification_bound"]' in service
