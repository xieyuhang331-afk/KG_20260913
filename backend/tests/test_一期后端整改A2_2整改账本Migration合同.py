import importlib.util
import inspect
import re
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
VERSIONS = BACKEND_ROOT / "app" / "migrations" / "versions"
REVISION_FILE = VERSIONS / "20260902_0035_a2_identity_remediation_ledger.py"

TABLES = (
    "identity.identity_remediation_batch",
    "identity.identity_remediation_item",
    "identity.identity_remediation_receipt",
    "identity.identity_remediation_audit",
)
WRITER_FUNCTION = (
    "identity.a2_identity_remediation_ledger_v1("
    "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,"
    "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
    "timestamp with time zone)"
)
CONFIRMATION_FUNCTION = (
    "identity.a2_identity_remediation_confirm_v1("
    "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,"
    "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
    "timestamp with time zone,varchar,bigint,varchar)"
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


def test_A2_2_L_0035从0034派生且保持单一Head() -> None:
    assert REVISION_FILE.is_file(), "A2.2-L 0035 migration is missing"
    module = _load_revision(REVISION_FILE)
    assert module.revision == "20260902_0035"
    assert module.down_revision == "20260901_0034"
    assert module.branch_labels is None
    assert module.depends_on is None
    assert _revision_heads() == {"20260902_0035"}


def test_A2_2_L_四类账本与状态版本摘要约束闭合() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    for table in TABLES:
        assert table in normalized
    for state in (
        "PLANNED",
        "RUNNING",
        "PAUSED",
        "COMPLETED",
        "FAILED",
        "DISCOVERED",
        "READY",
        "PROCESSING",
        "REMEDIATED",
        "REMEDIATION_REQUIRED",
        "FAILED_TERMINAL",
        "EXCLUDED",
    ):
        assert state.lower() in normalized
    for contract in (
        "uq_identity_remediation_single_running_batch",
        "uq_identity_remediation_batch_snapshot",
        "uq_identity_remediation_item_subject",
        "uq_identity_remediation_receipt_idempotency",
        "ck_identity_remediation_batch_version",
        "ck_identity_remediation_item_version",
        "ck_identity_remediation_item_attempt",
        "ck_identity_remediation_digest_lengths",
    ):
        assert contract in normalized


def test_A2_2_L_闭合Writer与Confirmation实现幂等并发及三态确认() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    module = _load_revision(REVISION_FILE)
    assert "create function identity.a2_identity_remediation_ledger_v1(" in normalized
    assert "create function identity.a2_identity_remediation_confirm_v1(" in normalized
    assert normalized.count("create function identity.a2_identity_remediation_ledger_v1(") == 1
    assert normalized.count("create function identity.a2_identity_remediation_confirm_v1(") == 1
    assert module._WRITER_FUNCTION == WRITER_FUNCTION
    assert module._CONFIRMATION_FUNCTION == CONFIRMATION_FUNCTION
    assert "value_request_digest" not in normalized
    assert normalized.count("security definer") == 2
    assert normalized.count("set search_path = pg_catalog, pg_temp") >= 2
    headers = re.findall(
        r"create function identity\.a2_identity_remediation_[^(]+\((.*?)\) returns",
        normalized,
    )
    assert len(headers) == 2
    assert all("json" not in header for header in headers)
    assert all("[]" not in header for header in headers)
    assert "pg_advisory_xact_lock" in normalized
    assert "value_actor_scope || ':' || value_operation || ':' || value_idempotency_key_digest" in normalized
    assert "value_request_digest" not in normalized
    assert "a2_identity_remediation_request_digest_v2" in normalized
    assert "a2_identity_remediation_batch_row_digest_v1" in normalized
    assert "a2_identity_remediation_item_row_digest_v1" in normalized
    assert "a2_remediation_idempotency_conflict" in normalized
    assert "a2_remediation_snapshot_conflict" in normalized
    assert "a2_remediation_stale_version" in normalized
    assert "'committed'" in normalized
    assert "'not_committed'" in normalized
    assert "'unknown'" in normalized
    assert "for update" in normalized
    assert "identity_remediation_receipt" in normalized
    assert "identity_remediation_audit" in normalized


def test_A2_2_L_V2只存在29参数Writer和32参数Confirmation() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    module = _load_revision(REVISION_FILE)
    typed_parameters = [
        line.strip().rstrip(",")
        for line in module._typed_parameters().splitlines()
        if line.strip()
    ]
    assert len(typed_parameters) == 29
    assert module._WRITER_FUNCTION == WRITER_FUNCTION
    assert module._CONFIRMATION_FUNCTION == CONFIRMATION_FUNCTION
    assert "value_request_digest" not in normalized


def test_A2_2_L_强类型入口没有任意载荷或敏感字段通道() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    module = _load_revision(REVISION_FILE)
    assert "a2_remediation_input_invalid" in normalized
    assert module._DIGEST_PATTERN == "^[0-9a-f]{64}$"
    assert module._ACTOR_PATTERN == "^[a-z][a-z0-9_-]{0,63}$"
    assert "value_secondary_flags between 0 and 15" in normalized
    for forbidden_parameter in (
        "value jsonb",
        "payload jsonb",
        "metadata jsonb",
        "response jsonb",
        "varchar[]",
        "jsonb[]",
    ):
        assert forbidden_parameter not in normalized
    for sensitive_name in (
        "real_name",
        "id_card",
        "phone",
        "ciphertext",
        "nonce",
        "fingerprint",
        "key_id",
    ):
        assert sensitive_name not in normalized


def test_A2_2_L_数据库函数强制合法迁移矩阵和终态不可重开() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    for operation in (
        "PLAN_BATCH",
        "START_BATCH",
        "PAUSE_BATCH",
        "RESUME_BATCH",
        "COMPLETE_BATCH",
        "FAIL_BATCH",
        "REGISTER_ITEM",
        "MARK_ITEM_READY",
        "START_ITEM",
        "RECOVER_ITEM",
        "COMPLETE_ITEM",
        "REQUIRE_ITEM",
        "FAIL_ITEM",
        "EXCLUDE_ITEM",
    ):
        assert operation.lower() in normalized
    for pair in (
        "batch.status='planned' and value_operation='start_batch'",
        "batch.status='running' and value_operation='pause_batch'",
        "batch.status='paused' and value_operation='resume_batch'",
        "batch.status='running' and value_operation='complete_batch'",
        "item.status='discovered' and value_operation='mark_item_ready'",
        "item.status='ready' and value_operation='start_item'",
        "item.status='processing' and value_operation='recover_item'",
        "item.status='processing' and value_operation='complete_item'",
    ):
        assert pair in normalized
    assert "value_operation='recover_item' and value_reason_code='a2_controlled_recovery'" in normalized
    assert "a2_canonical_match_approved" in normalized
    assert "a2_token_window_proved" in normalized
    assert "batch.status<>'running'" in normalized
    assert "manifest_count<>batch.input_count" in normalized
    assert "terminal_mismatch_count<>0" in normalized
    assert "a2_remediation_illegal_transition" in normalized
    assert "a2_remediation_stale_version" in normalized
    assert "version=target.version+1" in normalized


def test_A2_2_L_V2完整后像与前像三态边界闭合() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    for receipt_field in (
        "receipt.result_code=value_expected_result_code",
        "receipt.ledger_row_digest=current_row_digest",
    ):
        assert receipt_field in normalized
    for audit_field in (
        "audit.reason_code=value_reason_code",
        "audit.result_code=value_expected_result_code",
        "audit.ledger_row_digest=current_row_digest",
    ):
        assert audit_field in normalized
    assert "batch.version=value_expected_version" in normalized
    assert "batch.state_digest=value_expected_target_state_digest" in normalized
    assert "item.version=value_expected_version" in normalized
    assert "item.state_digest=value_expected_target_state_digest" in normalized
    assert "return 'not_committed'" in normalized
    assert "return 'unknown'" in normalized


def test_A2_2_L_V2分类动作与snapshot防绕过由数据库强制() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    assert "existing_batch.snapshot_ref_hash=value_snapshot_ref_hash" in normalized
    assert "a2_remediation_snapshot_conflict" in normalized
    for expression in (
        "primary_class in ('h0','h7')",
        "primary_class in ('h1','h2','h6')",
        "primary_class in ('h3','h4','h5')",
        "status='failed_terminal'",
        "batch.status='planned'",
        "batch.status='running'",
    ):
        assert expression in normalized


def test_A2_2_L_专用角色仅有闭合函数权限且底表无授权() -> None:
    source = REVISION_FILE.read_text(encoding="utf-8")
    normalized = " ".join(source.split()).lower()
    module = _load_revision(REVISION_FILE)
    for name in (
        "KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE",
        "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL",
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE",
        "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL",
    ):
        assert name in source
    for attribute in (
        "rolcanlogin",
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
        "pg_auth_members",
    ):
        assert attribute in normalized
    assert "grant execute on function" in normalized
    assert "grant usage on schema identity" in normalized
    assert "revoke create on schema public, identity" in normalized
    assert module._TABLES == TABLES
    assert "revoke all privileges on table {table}" in normalized
    assert "grant select on table" not in normalized
    assert "grant insert on table" not in normalized
    assert "grant update on table" not in normalized
    assert "grant delete on table" not in normalized


def test_A2_2_L_降级只删除0035对象且不触碰业务数据() -> None:
    module = _load_revision(REVISION_FILE)
    downgrade = re.sub(r"\s+", " ", inspect.getsource(module.downgrade)).lower()
    for table in reversed(TABLES):
        assert table.split(".", 1)[1] in downgrade
    assert "drop function" in downgrade
    for forbidden in (
        'public."user"',
        "public.service_enrollment",
        "public.identity_verification_submission",
        "update public",
        "delete from public",
        "truncate",
    ):
        assert forbidden not in downgrade
