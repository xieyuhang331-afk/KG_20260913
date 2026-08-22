from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260822_0027_phase1_slice3_review_enrollment_preimage_authority.py"
)
REPOSITORY = ROOT / "app" / "modules" / "member_enrollment" / "repository.py"
SERVICE = ROOT / "app" / "modules" / "member_enrollment" / "service.py"


def test_0027线性迁移只新增审核Enrollment受限Preimage权威() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260822_0027"' in source
    assert 'down_revision = "20260822_0026"' in source
    assert "slice3_review_enrollment_preimage_authority_v1" in source
    assert "RETURNS JSONB" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path=pg_catalog,pg_temp" in source
    assert "session_user" in source
    assert "value_verification" in source
    assert "value_enrollment" in source
    assert "value_reviewer" in source
    assert "PLATFORM_REVIEWING" in source
    assert "INSTITUTION_CHECKED" in source
    assert "IDENTITY_REVIEW_CLAIMED" in source
    assert "FOR UPDATE" in source
    assert "to_jsonb" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert source.count("GRANT EXECUTE ON FUNCTION") == 1
    assert source.count("REVOKE EXECUTE ON FUNCTION") == 1
    assert "GRANT SELECT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT USAGE ON SEQUENCE" not in source
    assert "EXECUTE IMMEDIATE" not in source


def test_平台审核决定只经受限权威取得EnrollmentPreimage() -> None:
    repository = REPOSITORY.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")

    assert "review_enrollment_preimage_for_update" in repository
    assert "slice3_review_enrollment_preimage_authority_v1" in repository
    assert "review_enrollment_preimage_for_update(" in service
    assert "verification_id=verification_id" in service
    assert 'enrollment_id=enrollment["enrollment_id"]' in service
    assert "reviewer_user_id=context.actor.id" in service


def test_受限Preimage不以宽权限或SQL拼接取得GREEN() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    repository = REPOSITORY.read_text(encoding="utf-8")

    assert "dynamic SQL" not in source
    assert "EXECUTE format(" not in source
    assert "EXECUTE value_" not in source
    assert "SELECT * FROM public.service_enrollment" not in repository
    assert "review_enrollment_preimage_for_update" in repository
