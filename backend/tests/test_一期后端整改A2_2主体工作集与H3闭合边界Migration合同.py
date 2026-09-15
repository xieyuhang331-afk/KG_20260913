import importlib.util
import inspect
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND_ROOT / "app" / "migrations" / "versions"
REVISION_FILE = (
    VERSIONS
    / "20260903_0036_a2_remediation_subject_h3_closed_boundary.py"
)
HISTORICAL_0034 = (
    VERSIONS / "20260901_0034_a2_identity_inventory_closed_read_boundary.py"
)
HISTORICAL_0035 = VERSIONS / "20260902_0035_a2_identity_remediation_ledger.py"

WORKSET_FUNCTION = (
    "identity.a2_identity_remediation_subject_workset_v1("
    "uuid,bigint,varchar,varchar,varchar)"
)
MATERIAL_FUNCTION = (
    "identity.a2_identity_remediation_h3_material_v1("
    "uuid,uuid,bigint,varchar)"
)
MUTATION_FUNCTION = (
    "identity.a2_identity_remediation_h3_clear_legacy_v1("
    "uuid,uuid,bigint,varchar,varchar,varchar,varchar)"
)


def _load_revision(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _revision_heads() -> set[str]:
    revisions: dict[str, object] = {}
    parents: set[str] = set()
    for path in sorted(VERSIONS.glob("*.py")):
        module = _load_revision(path)
        assert module.revision not in revisions
        revisions[module.revision] = module
        if module.down_revision is not None:
            assert isinstance(module.down_revision, str)
            parents.add(module.down_revision)
    return set(revisions) - parents


def _normalized_source() -> str:
    return " ".join(REVISION_FILE.read_text(encoding="utf-8").split()).lower()


def test_A2_2_RP_0036从0035派生且保持单一Head() -> None:
    assert REVISION_FILE.is_file(), "A2.2-RP 0036 migration is missing"
    module = _load_revision(REVISION_FILE)
    assert module.revision == "20260903_0036"
    assert module.down_revision == "20260902_0035"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert _revision_heads() == {"20260915_0048"}
    assert HISTORICAL_0034.is_file()
    assert HISTORICAL_0035.is_file()


def test_A2_2_RP_仅有三个强类型闭合函数且无任意载荷入口() -> None:
    source = _normalized_source()
    module = _load_revision(REVISION_FILE)
    assert module._WORKSET_FUNCTION == WORKSET_FUNCTION
    assert module._MATERIAL_FUNCTION == MATERIAL_FUNCTION
    assert module._MUTATION_FUNCTION == MUTATION_FUNCTION
    headers = re.findall(
        r"create function identity\.a2_identity_remediation_[^(]+\((.*?)\) returns",
        source,
    )
    assert len(headers) == 3
    assert all("json" not in header for header in headers)
    assert all("[]" not in header for header in headers)
    assert source.count("security definer") == 3
    assert source.count("set search_path = pg_catalog, pg_temp") == 3
    for forbidden in (
        "dynamic sql",
        "execute format",
        "payload json",
        "metadata json",
        "subject_filter",
        "where_sql",
    ):
        assert forbidden not in source


def test_A2_2_RP_Workset闭合分类manifest和快照前像() -> None:
    source = _normalized_source()
    module = _load_revision(REVISION_FILE)
    from app.modules.auth.identity_remediation import _classification_rule_hash

    assert _classification_rule_hash().lower() == (
        "9801dbb22c45cb679fdae43dd72dbf804e7f5cb4b0095d28f9d572f3d6757bc5"
    )
    for token in (
        "9801dbb22c45cb679fdae43dd72dbf804e7f5cb4b0095d28f9d572f3d6757bc5",
        "expected_batch_version",
        "expected_batch_state_digest",
        "expected_classification_rule_hash",
        "expected_snapshot_ref_hash",
        "primary_class",
        "secondary_flags",
        "preimage_digest",
        "manifest",
        "h7",
        "h2",
        "h3",
        "h1",
        "h6",
        "h4",
        "h5",
        "h0",
    ):
        assert token in source
    assert "batch.status<>'planned'" in source
    assert "a2_remediation_manifest_mismatch" in source
    assert "a2_remediation_stale_version" in source
    workset_body = source.split(
        "create function identity.a2_identity_remediation_subject_workset_v1", 1
    )[1].split(
        "create function identity.a2_identity_remediation_h3_material_v1", 1
    )[0]
    assert workset_body.count("with {_facts_cte()}") == 1
    assert "manifest_valid" in workset_body
    assert "emitted_count" in workset_body
    preimage_expression = module._workset_preimage_expression().lower()
    assert "a2_rp_workset_preimage_v1;" in preimage_expression
    assert preimage_expression.count("octet_length(convert_to") == 14
    assert "subject_user_ref=v" in preimage_expression
    assert "secondary_flags=v" in preimage_expression
    assert "=n;" in preimage_expression
    assert "concat_ws" not in preimage_expression
    assert "{_workset_preimage_expression()}" in workset_body


def test_A2_2_RP_H3Material锁定唯一正式链且只读() -> None:
    source = _normalized_source()
    for token in (
        "identity_remediation_item",
        "identity_verification_submission",
        "identity_verification_decision",
        "identity_subject_claim_registry",
        "for update",
        "primary_class<>'h3'",
        "source_kind='p1'",
        "outcome='verified'",
        "a2_remediation_h3_chain_invalid",
        "chain_digest",
        "consent_version",
        "a2_rp_h3_chain_v1",
        "real_name_ciphertext",
        "real_name_nonce",
        "id_card_ciphertext",
        "id_card_nonce",
        "encryption_key_id",
        "identity_fingerprint",
        "fingerprint_key_id",
    ):
        assert token in source
    assert "claim.identity_fingerprint is distinct from submission.id_card_digest" in source
    assert "claim.fingerprint_key_id is distinct from submission.encryption_key_id" in source
    material_body = source.split(
        "create function identity.a2_identity_remediation_h3_material_v1", 1
    )[1].split(
        "create function identity.a2_identity_remediation_h3_clear_legacy_v1", 1
    )[0]
    for dml in ("insert into", "update public.", "delete from", "truncate "):
        assert dml not in material_body


def test_A2_2_RP_H3Mutation只清理legacy两列并重验完整前像() -> None:
    source = _normalized_source()
    for token in (
        "a2_canonical_match_approved",
        "expected_chain_digest",
        "exact_match_gate_digest",
        "eligibility_action_gate_digest",
        "status<>'processing'",
        "set real_name=null, id_card=null",
        "mutation_digest",
        "postimage_digest",
        "a2_remediation_h3_chain_invalid",
    ):
        assert token in source
    mutation_body = source.split(
        "create function identity.a2_identity_remediation_h3_clear_legacy_v1", 1
    )[1]
    assert mutation_body.count("update public.\"user\"") == 1
    for forbidden_target in (
        "update public.identity_verification_submission",
        "update public.identity_verification_decision",
        "update identity.identity_subject_claim_registry",
        "update public.service_enrollment",
    ):
        assert forbidden_target not in mutation_body


def test_A2_2_RP_权限只授予既有Writer且其他身份保持拒绝() -> None:
    source = _normalized_source()
    module = _load_revision(REVISION_FILE)
    upgrade_source = inspect.getsource(module.upgrade).lower()
    assert module._WRITER_ROLE_ENV == "KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE"
    assert (
        module._WORKSET_FUNCTION,
        module._MATERIAL_FUNCTION,
        module._MUTATION_FUNCTION,
    ) == (WORKSET_FUNCTION, MATERIAL_FUNCTION, MUTATION_FUNCTION)
    assert "for function in (_workset_function, _material_function, _mutation_function)" in upgrade_source
    assert "revoke all on function {function} from public, {quoted_role}" in upgrade_source
    assert "grant execute on function {function} to {quoted_role}" in upgrade_source
    assert "session_user" in source
    assert "a2_remediation_role_mismatch" in source
    for forbidden_grant in (
        "grant select on public.\"user\"",
        "grant update on public.\"user\"",
        "grant select on public.identity_verification_submission",
        "grant select on identity.identity_subject_claim_registry",
        "grant create on schema",
    ):
        assert forbidden_grant not in source


def test_A2_2_RP_downgrade仅删除0036函数且不修改业务数据() -> None:
    source = _normalized_source()
    module = _load_revision(REVISION_FILE)
    downgrade_source = inspect.getsource(module.downgrade).lower()
    assert "for function in (_mutation_function, _material_function, _workset_function)" in downgrade_source
    assert 'op.execute(f"drop function {function}")' in downgrade_source
    assert "drop table" not in source
    assert "alter table" not in source
    assert "delete from" not in source
    assert "truncate" not in source
    assert "drop function identity.a2_identity_inventory_snapshot_v1" not in source
    assert "drop function identity.a2_identity_remediation_ledger_v1" not in source
    assert "drop function identity.a2_identity_remediation_confirm_v1" not in source
