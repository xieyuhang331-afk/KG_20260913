from sqlalchemy import inspect
from pathlib import Path

from app.modules.therapist_qualification.models import (
    TherapistProfileModel,
    TherapistReviewDecisionModel,
    TherapistWorkflowOutboxModel,
)
from app.modules.therapist_qualification.repository import TherapistQualificationRepository


def test_显式列投影锁序和禁止lazy_load():
    assert inspect(TherapistProfileModel).primary_key[0].name == "therapist_id"
    assert "lock_operation" in TherapistQualificationRepository.__dict__
    assert "lock_clean_files" in TherapistQualificationRepository.__dict__
    assert {c.name for c in TherapistWorkflowOutboxModel.__table__.columns} >= {"status", "attempts", "lease_owner", "version"}


def test_初始写入只使用批准列且用户ID显式返回():
    source = (
        Path(__file__).parents[1]
        / "app/modules/therapist_qualification/repository.py"
    ).read_text(encoding="utf-8")
    assert "async def add_invitation" in source
    assert "async def add_therapist_user" in source
    assert "async def add_profile" in source
    assert 'RETURNING id' in source
    assert "insert(TherapistInvitationModel).inline()" in source
    assert "insert(TherapistProfileModel).inline()" in source


def test_资质文件锁只经由最小权限函数边界():
    source = (
        Path(__file__).parents[1]
        / "app/modules/therapist_qualification/repository.py"
    ).read_text(encoding="utf-8")
    assert "lock_therapist_qualification_files_v1" in source
    assert "lock_therapist_qualification_review_files_v1" in source
    assert "PrivateFileModel" not in source


def test_修订与资质及附件在同一Repository边界顺序持久():
    repository_source = (
        Path(__file__).parents[1]
        / "app/modules/therapist_qualification/repository.py"
    ).read_text(encoding="utf-8")
    service_source = (
        Path(__file__).parents[1]
        / "app/modules/therapist_qualification/service.py"
    ).read_text(encoding="utf-8")
    method = repository_source.split("async def add_revision_bundle", 1)[1]
    assert method.index("self.session.add(revision)") < method.index("self.session.add(qualification)")
    assert method.index("self.session.add(qualification)") < method.index("self.session.add_all([link, *attachments])")
    assert service_source.count("add_revision_bundle(revision, q, link, attachments)") == 1


def test_审核决定可空JSON字段持久为SQL_NULL():
    column = TherapistReviewDecisionModel.__table__.c.correction_fields
    assert column.type.none_as_null is True
