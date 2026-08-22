from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "app" / "modules" / "member_enrollment" / "api.py"
MIGRATION = (
    ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260822_0026_phase1_slice3_reviewer_claim_authority.py"
)


def test_0026线性迁移只新增受限审核领取权威() -> None:
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260822_0026"' in source
    assert 'down_revision = "20260822_0025"' in source
    assert "slice3_reviewer_claim_authority_v1" in source
    assert "RETURNS BOOLEAN" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path=pg_catalog,pg_temp" in source
    assert "session_user" in source
    assert "IDENTITY_REVIEW_CLAIMED" in source
    assert "PLATFORM_REVIEWING" in source
    assert "FOR SHARE" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert source.count("GRANT EXECUTE ON FUNCTION") == 1
    assert source.count("REVOKE EXECUTE ON FUNCTION") == 1
    assert "GRANT SELECT" not in source
    assert "GRANT UPDATE" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT USAGE ON SEQUENCE" not in source
    assert "EXECUTE IMMEDIATE" not in source


def test_PII访问只经受限领取权威且不直接读取Audit基础表() -> None:
    repository = (
        ROOT / "app" / "modules" / "member_enrollment" / "repository.py"
    ).read_text(encoding="utf-8")
    api = (
        ROOT / "app" / "modules" / "member_enrollment" / "api.py"
    ).read_text(encoding="utf-8")

    assert "slice3_reviewer_claim_authority_v1" in repository
    assert "reviewer_claim_is_current" in repository
    assert "reviewer_claim_is_current(review_id,actor.id)" in api
    assert "await session.rollback()" in api


def test_PII访问与平台审核决定共享完整审核员Currentness摘要及403错误目录() -> None:
    source = API.read_text(encoding="utf-8")

    assert "def _reviewer_currentness_payload(" in source
    assert source.count(
        "audit_digest(_reviewer_currentness_payload(reviewer))"
    ) == 2
    assert (
        '("POST","/api/v1/platform/member-identity-reviews/{review_id}/decision"):'
        '_route_errors("INVALID_REQUEST","REVIEWER_CURRENTNESS_FORBIDDEN",'
        '"STEP_UP_FORBIDDEN"'
    ) in source
