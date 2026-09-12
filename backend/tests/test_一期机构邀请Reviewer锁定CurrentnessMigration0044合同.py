from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = BACKEND_ROOT / "app" / "migrations" / "versions"
MIGRATION = MIGRATIONS / "20260913_0044_机构邀请Reviewer锁定当前性.py"


def _source() -> str:
    if not MIGRATION.exists():
        raise AssertionError("INSTITUTION_REVIEWER_LOCKED_CURRENTNESS_0044_MISSING")
    return MIGRATION.read_text(encoding="utf-8")


def test_0044从0043派生且历史Migration不被改写():
    source = _source()
    tree = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision"}
    }
    assert assignments == {
        "revision": "20260913_0044",
        "down_revision": "20260912_0043",
    }


def test_0044专用函数强类型最小返回并真实锁定User底表():
    source = _source()
    assert "institution_onboarding_reviewer_currentness_v1(p_user_id BIGINT)" in source
    for fragment in (
        "id BIGINT",
        "role public.user_role",
        "status public.user_status",
        "tenant_id BIGINT",
        "LANGUAGE plpgsql SECURITY DEFINER VOLATILE",
        "SET search_path=pg_catalog,pg_temp",
        'FROM public."user" reviewer_user',
        "FOR SHARE OF reviewer_user",
        "session_user",
        "INSTITUTION_REVIEWER_CURRENTNESS_INPUT_INVALID",
    ):
        assert fragment in source
    for forbidden in (
        "password_hash",
        "phone",
        "real_name",
        "id_card",
        "EXECUTE IMMEDIATE",
        "auth_user_currentness_v1",
    ):
        assert forbidden not in source


def test_0044仅Application身份获得Execute且downgrade只撤权删除函数():
    source = _source()
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "KG_DATABASE_USER" in source
    assert "KG_IDENTITY_APPLICATION_DATABASE_URL" in source
    downgrade = source.split("def downgrade()", 1)[1]
    assert "REVOKE EXECUTE ON FUNCTION" in downgrade
    assert "DROP FUNCTION" in downgrade
    for forbidden in ("INSERT INTO", "UPDATE public.", "DELETE FROM", "TRUNCATE"):
        assert forbidden not in downgrade
