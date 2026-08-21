from __future__ import annotations

import inspect
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    BACKEND_ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260821_0024_phase1_slice3_member_currentness_authority.py"
)


def test_0024线性HotfixMigration与受限函数合同() -> None:
    assert MIGRATION.is_file()
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "20260821_0024"' in source
    assert 'down_revision = "20260821_0023"' in source
    assert "slice3_member_currentness_authority_v1" in source
    assert "RETURNS UUID" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path=pg_catalog,pg_temp" in source
    assert "session_user" in source
    assert "KG_DATABASE_USER" in source
    assert "KG_IDENTITY_APPLICATION_DATABASE_URL" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "DROP FUNCTION" in source
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "CREATE SEQUENCE" not in source
    assert "GRANT SELECT" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT UPDATE" not in source


def test_MemberCurrentness只通过受限Authority取得Member() -> None:
    from app.modules.member_enrollment import api

    source = inspect.getsource(api._member_for_actor)
    assert "slice3_member_currentness_authority_v1" in source
    assert "identity.user_member_self_link" not in source
    assert "identity.member" not in source
    assert 'public.\"user\"' not in source
    assert "expected_phone" in source
    assert "ACTOR_CURRENTNESS_FORBIDDEN" in source


def test_Family公开API合同不因Authority改变() -> None:
    from app.modules.member_enrollment import api

    route = next(
        route
        for route in api.family_router.routes
        if route.path == "/api/v1/family/member-enrollments/accept"
    )
    assert route.methods == {"POST"}
    assert route.status_code == 201
    assert route.response_model.__name__ == "EnrollmentDTO"
    assert (
        "ACTOR_CURRENTNESS_FORBIDDEN"
        in api.SLICE3_ROUTE_ERROR_CODES[
            ("POST", "/api/v1/family/member-enrollments/accept")
        ][403]
    )
