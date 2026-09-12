import hashlib
import importlib.util
import inspect
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND_ROOT / "app" / "migrations" / "versions"
REVISION_FILE = (
    VERSIONS
    / "20260901_0034_a2_identity_inventory_closed_read_boundary.py"
)
HISTORICAL_0033 = (
    VERSIONS
    / "20260830_0033_phase1_slice5_web_api_contract_hotfix.py"
)
HISTORICAL_0033_SHA256 = (
    "811BAD2D1EA620A776BFD6E9451BEC2BDE704244BD72CEE2585481A85E3835D6"
)
FUNCTION_SIGNATURE = "identity.a2_identity_inventory_snapshot_v1()"
RETURN_FIELDS = (
    "role",
    "legacy_pii_present",
    "identity_authority_signal",
    "formal_chain_complete",
    "tenant_present",
    "tenant_relation_known",
    "self_link_count",
    "enrollment_count",
    "current_enrollment_count",
    "tenant_matches_unique_current",
    "enrollment_scope_complete",
)


def _load_revision(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _normalized_lf_sha256(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper()


def _revision_heads() -> set[str]:
    revisions = {}
    parents = set()
    for path in sorted(VERSIONS.glob("*.py")):
        module = _load_revision(path)
        assert module.revision not in revisions
        revisions[module.revision] = module
        if module.down_revision is not None:
            assert isinstance(module.down_revision, str)
            parents.add(module.down_revision)
    return set(revisions) - parents


def test_A2_2_P_0034修订与历史0033保持单一Head():
    assert REVISION_FILE.is_file(), "A2.2-P 0034 migration is missing"
    module = _load_revision(REVISION_FILE)
    assert module.revision == "20260901_0034"
    assert module.down_revision == "20260830_0033"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert _revision_heads() == {"20260913_0044"}
    assert _normalized_lf_sha256(HISTORICAL_0033) == HISTORICAL_0033_SHA256


def test_A2_2_P函数输出字段闭合且无标识或敏感值():
    source = REVISION_FILE.read_text(encoding="utf-8")
    match = re.search(
        r"RETURNS\s+TABLE\s*\((?P<fields>.*?)\)\s*LANGUAGE",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match is not None
    fields = tuple(
        part.strip().split()[0].strip('"')
        for part in match.group("fields").split(",")
    )
    assert fields == RETURN_FIELDS
    forbidden_output = {
        "user_id",
        "user_ref",
        "member_id",
        "tenant_id",
        "enrollment_id",
        "submission_id",
        "decision_ref",
        "claim_id",
        "real_name",
        "id_card",
        "phone",
        "ciphertext",
        "nonce",
        "digest",
        "fingerprint",
        "key_id",
    }
    assert forbidden_output.isdisjoint(fields)


def test_A2_2_P函数为无参数静态只读安全定义者边界():
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    assert f"create function {FUNCTION_SIGNATURE}" in normalized
    assert "security definer" in normalized
    assert "set search_path = pg_catalog, pg_temp" in normalized
    assert "session_user" in normalized
    assert "a2_identity_inventory_reader_forbidden" in normalized
    assert "format(" not in normalized
    assert "create temp" not in normalized
    function_body = re.search(
        r"AS \$fn\$(?P<body>.*?)\$fn\$",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert function_body is not None
    body = function_body.group("body").lower()
    assert "execute " not in body
    for forbidden in (
        " insert ",
        " update ",
        " delete ",
        " truncate ",
        " alter ",
        " create ",
        " drop ",
    ):
        assert forbidden not in f" {body} "
    assert "_base_tables" in normalized
    for relation in (
        'public."user"',
        "public.identity_verification_submission",
        "public.identity_verification_decision",
        "identity.identity_subject_claim_registry",
        "identity.user_member_self_link",
        "public.service_enrollment",
    ):
        assert relation in body


def test_A2_2_P专用角色配置与最小ACL合同():
    source = REVISION_FILE.read_text(encoding="utf-8")
    for name in (
        "KG_A2_IDENTITY_INVENTORY_ROLE",
        "KG_A2_IDENTITY_INVENTORY_DATABASE_URL",
    ):
        assert name in source
    normalized = " ".join(source.split()).lower()
    for attribute in (
        "rolcanlogin",
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
    ):
        assert attribute in normalized
    assert "pg_auth_members" in normalized
    assert "revoke all on function" in normalized
    assert "from public" in normalized
    assert "grant execute on function" in normalized
    assert "grant usage on schema identity" in normalized
    assert "revoke create on schema public, identity" in normalized
    for relation in (
        'public."user"',
        "public.identity_verification_submission",
        "public.identity_verification_decision",
        "identity.identity_subject_claim_registry",
        "identity.user_member_self_link",
        "public.service_enrollment",
    ):
        assert relation in normalized
    assert "revoke all privileges on table {relation}" in normalized


def test_A2_2_P降级只撤销边界且不触碰业务数据():
    source = REVISION_FILE.read_text(encoding="utf-8")
    module = _load_revision(REVISION_FILE)
    downgrade_source = re.sub(
        r"\s+", " ", inspect.getsource(module.downgrade)
    ).lower()
    assert "def downgrade" in downgrade_source
    assert 'op.execute(f"drop function {_function}")' in downgrade_source
    assert "revoke execute on function" in downgrade_source
    assert "revoke usage on schema identity" in downgrade_source
    for forbidden in (
        "insert into public",
        "update public",
        "delete from public",
        "truncate",
        "drop table",
        "alter table",
    ):
        assert forbidden not in downgrade_source
    assert "20260830_0033" in source
