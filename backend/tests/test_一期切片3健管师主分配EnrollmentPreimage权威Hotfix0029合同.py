from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "app" / "migrations" / "versions"
MIGRATION = VERSIONS / (
    "20260824_0029_phase1_slice3_case_enrollment_preimage_authority.py"
)
REPOSITORY = ROOT / "app" / "modules" / "member_enrollment" / "repository.py"
SERVICE = ROOT / "app" / "modules" / "member_enrollment" / "service.py"


def _source() -> str:
    assert MIGRATION.is_file(), "Expected RED: Hotfix Migration 0029 does not exist"
    return MIGRATION.read_text(encoding="utf-8")


def _assigned(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        target.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
        and target.id in {"revision", "down_revision"}
    }


def test_0029迁移尚不存在时Expected_RED且实现后线性唯一() -> None:
    source = _source()

    assert _assigned(MIGRATION) == {
        "revision": "20260824_0029",
        "down_revision": "20260823_0028",
    }
    revisions = [
        _assigned(path).get("revision")
        for path in VERSIONS.glob("*.py")
        if path.name != "__init__.py"
    ]
    assert revisions.count("20260824_0029") == 1
    assert source.count("CREATE FUNCTION") == 1
    assert source.count(
        "slice3_case_enrollment_preimage_authority_v1"
    ) >= 1


def test_0029只创建受限完整Enrollment前像函数() -> None:
    source = _source()
    compact = "".join(source.split())

    assert (
        "slice3_case_enrollment_preimage_authority_v1("
        "value_assignmentUUID,value_enrollmentUUID,value_therapistUUID,"
        "value_actor_userBIGINT)RETURNSJSONB"
    ) in compact
    for token in (
        "LANGUAGE plpgsql",
        "SECURITY DEFINER",
        "SET search_path=pg_catalog,pg_temp",
        "session_user",
        "role='therapist'",
        "u.status='active'",
        "p.user_id=value_actor_user",
        "p.therapist_id=value_therapist",
        "p.status='APPROVED_ACTIVE'",
        "a.assignment_id=value_assignment",
        "a.enrollment_id=value_enrollment",
        "a.therapist_id=value_therapist",
        "a.status='PENDING_ACCEPTANCE'",
        "e.enrollment_id=value_enrollment",
        "e.current_assignment_id=value_assignment",
        "e.status='THERAPIST_PENDING'",
        "e.service_case_id IS NULL",
        "to_jsonb(e)",
        "FOR UPDATE OF e",
    ):
        assert "".join(token.split()) in compact
    assert "RETURN NULL" in source
    assert "RETURN preimage" in source
    assert "LIMIT" not in source
    assert "EXECUTE format(" not in source


def test_0029严格验证CaseWriter角色且不扩大表权限() -> None:
    source = _source()

    for token in (
        "KG_MEMBER_CASE_WRITER_ROLE",
        "KG_MEMBER_CASE_WRITER_DATABASE_URL",
        "rolsuper",
        "rolcreaterole",
        "rolcreatedb",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
        "pg_auth_members",
    ):
        assert token in source
    assert source.count("GRANT EXECUTE ON FUNCTION") == 1
    assert source.count("REVOKE EXECUTE ON FUNCTION") == 1
    assert source.count("REVOKE ALL ON FUNCTION") == 2
    assert "FROM PUBLIC" in source
    for privilege in (
        "GRANT SELECT",
        "GRANT INSERT",
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT ALL ON TABLE",
        "ALTER DEFAULT PRIVILEGES",
    ):
        assert privilege not in source


def test_0029_downgrade精确撤销并删除函数且无业务数据() -> None:
    source = _source()
    downgrade = source[source.index("def downgrade()") :]

    assert downgrade.count("REVOKE EXECUTE ON FUNCTION") == 1
    assert downgrade.count("REVOKE ALL ON FUNCTION") == 1
    assert downgrade.count("DROP FUNCTION") == 1
    for token in (
        "INSERT INTO",
        "UPDATE public.",
        "DELETE FROM",
        "COPY ",
        "test data",
        "fixture",
    ):
        assert token not in source


def test_CaseWriter只经受限权威取得完整前像并供Mutation复用() -> None:
    repository = REPOSITORY.read_text(encoding="utf-8")
    service = SERVICE.read_text(encoding="utf-8")

    assert "case_enrollment_preimage_for_update" in repository
    assert "slice3_case_enrollment_preimage_authority_v1" in repository
    assert "set(preimage) != expected_columns" in repository
    assert "case_enrollment_preimage_for_update(" in service
    assert "assignment_id=assignment_id" in service
    assert 'enrollment_id=assignment["enrollment_id"]' in service
    assert 'therapist_id=assignment["therapist_id"]' in service
    assert "actor_user_id=context.actor.id" in service
