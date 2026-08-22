import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_0022 = (
    ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260818_0022_phase1_slice3_member_proxy_consent_service_case.py"
)
MIGRATION_0023 = (
    ROOT
    / "app"
    / "migrations"
    / "versions"
    / "20260821_0023_phase1_slice3_invitation_resend_runtime_acl.py"
)
EXPECTED_COLUMNS = (
    "code_digest",
    "code_key_id",
    "expires_at",
    "issued_at",
)


def _assignments(path: Path) -> dict[str, object]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for statement in module.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if isinstance(target, ast.Name):
            values[target.id] = ast.literal_eval(statement.value)
    return values


def test_0022保持冻结且未提前包含重发四列权限() -> None:
    source = MIGRATION_0022.read_text(encoding="utf-8")
    assert _assignments(MIGRATION_0022)["revision"] == "20260818_0022"
    assert (
        '_grant(enrollment,"UPDATE","member_service_invitation",'
        '("status","failed_attempts","accepted_at","revoked_at","version"))'
        in source
    )
    assert (
        '"code_digest","code_key_id","expires_at","issued_at"'
        not in source.split(
            '_grant(enrollment,"UPDATE","member_service_invitation"', 1
        )[1].split("\n", 1)[0]
    )


def test_0023是0022唯一线性后继且只含四列ACL增量() -> None:
    assert MIGRATION_0023.is_file(), "Slice 3 invitation resend ACL hotfix migration is missing"
    source = MIGRATION_0023.read_text(encoding="utf-8")
    assignments = _assignments(MIGRATION_0023)
    assert assignments["revision"] == "20260821_0023"
    assert assignments["down_revision"] == "20260818_0022"
    assert assignments["branch_labels"] is None
    assert assignments["depends_on"] is None
    assert assignments["_COLUMNS"] == EXPECTED_COLUMNS
    assert "KG_MEMBER_ENROLLMENT_WRITER_ROLE" in source
    assert "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL" in source
    assert "GRANT UPDATE ({rendered})" in source
    assert "REVOKE UPDATE ({rendered})" in source
    assert "member_service_invitation" in source
    assert "PUBLIC" not in source
    for forbidden in (
        "CREATE TABLE",
        "ALTER TABLE",
        "DROP TABLE",
        "CREATE FUNCTION",
        "CREATE VIEW",
        "CREATE SEQUENCE",
        "GRANT UPDATE ON",
    ):
        assert forbidden not in source.upper()


def test_当前Head传播只更新Head合同而不改0022历史合同() -> None:
    conftest = (ROOT / "tests" / "integration" / "conftest.py").read_text(
        encoding="utf-8"
    )
    ci_contract = (ROOT / "tests" / "test_身份成员集成持续集成合同.py").read_text(
        encoding="utf-8"
    )
    migration_0022_contract = (
        ROOT / "tests" / "test_一期切片3Migration0022合同.py"
    ).read_text(encoding="utf-8")
    assert 'REQUIRED_HEAD_REVISION = "20260822_0027"' in conftest
    assert 'EXPECTED_HEAD = "20260822_0027"' in ci_contract
    assert "20260818_0022_phase1_slice3_member_proxy_consent_service_case.py" in migration_0022_contract
    assert '"revision": "20260818_0022"' in migration_0022_contract
