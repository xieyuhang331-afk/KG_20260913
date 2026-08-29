from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260827_0032_phase1_slice7_service_fulfillment_closure_transfer_export.py"

TABLES = {
    "service_cycle_schedule",
    "service_milestone",
    "service_milestone_revision",
    "service_case_lifecycle_event",
    "service_closing_assessment",
    "service_summary",
    "service_summary_acknowledgement",
    "service_transfer_request",
    "service_transfer_scope_revision",
    "service_transfer_continuation_handoff",
    "proxy_major_authorization",
    "personal_data_export_request",
    "personal_data_export_artifact",
    "personal_data_export_download_access",
    "service_fulfillment_receipt",
    "service_fulfillment_audit",
    "service_fulfillment_outbox",
    "service_fulfillment_delivery",
}


def test_0032线性继承0031且对象目录闭合() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"revision", "down_revision", "branch_labels", "depends_on"}
    }
    assert assignments == {
        "revision": "20260827_0032",
        "down_revision": "20260826_0031",
        "branch_labels": None,
        "depends_on": None,
    }
    assert all(f'"{name}"' in source for name in TABLES)


def test_0032六身份受限函数和最小权限() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for purpose in (
        "MILESTONE_WRITER",
        "CASE_WRITER",
        "TRANSFER_WRITER",
        "EXPORT_WORKER",
        "FAMILY_READER",
        "OVERSIGHT_READER",
    ):
        assert f"KG_SLICE7_{purpose}_ROLE" in source
        assert f"KG_SLICE7_{purpose}_DATABASE_URL" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog, pg_temp" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT ALL" not in source
    assert "GRANT UPDATE ON" not in source
    assert "GRANT INSERT ON" not in source
    assert "GRANT USAGE, SELECT ON" not in source
    assert "CREATE SEQUENCE" not in source
    assert "EXECUTE format(" not in source


def test_0032业务权威回滚和历史保护() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        "slice7_authority_v1",
        "slice7_mutation_v1",
        "slice7_mutation_replay_v1",
        "slice7_mutation_confirm_v1",
        "slice7_read_one_v1",
        "slice7_read_many_v1",
        "slice7_worker_claim_v1",
        "slice7_export_claim_v1",
        "slice7_export_snapshot_v1",
        "slice7_export_source_file_v1",
        "slice7_export_private_file_register_v1",
        "slice7_export_private_file_snapshot_v1",
        "slice7_export_artifact_bind_v1",
        "slice7_export_ready_confirm_v1",
        "slice7_export_download_consume_v1",
        "slice7_export_download_confirm_v1",
        "slice7_export_fail_v1",
        "slice7_export_recover_v1",
        "slice7_export_cleanup_claim_v1",
        "slice7_export_cleanup_complete_v1",
        "slice7_outbox_claim_v1",
        "slice7_outbox_consume_v1",
        "slice7_outbox_recover_v1",
        "SERVICE_READY",
        "high_risk_task",
        "consent",
        "primary_therapist",
        "pg_advisory_xact_lock",
    ):
        assert token in source
    downgrade = source[source.index("def downgrade()") :]
    first_drop = min(
        position
        for token in ("op.drop_table", "DROP FUNCTION")
        if (position := downgrade.find(token)) >= 0
    )
    assert "Slice 7 downgrade requires empty module tables" in downgrade
    assert 0 <= downgrade.find("SELECT EXISTS") < first_drop
    assert "CASCADE" not in downgrade
    assert "ALTER TABLE public.service_case" not in source
    assert "ALTER TABLE public.health_plan" not in source


def test_D_下载凭证过期后可重新授权且历史记录不被删除() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "uq_personal_data_export_access_current" not in source
    mutation = source[
        source.index("ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS'") :
        source.index("ELSE RAISE EXCEPTION 'INVALID_REQUEST'", source.index("ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS'"))
    ]
    assert "INSERT INTO public.personal_data_export_download_access" in mutation
    assert "DELETE FROM public.personal_data_export_download_access" not in mutation


def test_整改C_导出READY未知提交使用精确只读后像确认() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    start = source.index('    _function(\n        "slice7_export_ready_confirm_v1"')
    function = source[start : source.index("    _function(", start + 20)]
    for token in (
        "session_user",
        "personal_data_export_request",
        "personal_data_export_artifact",
        "private_file",
        "service_fulfillment_audit",
        "service_fulfillment_outbox",
        "EXPORT_READY",
        "artifact_id",
        "private_file_id",
        "manifest_digest",
        "artifact_digest",
        "artifact_size",
        "audit_id",
        "event_id",
        "created_at",
        "expires_at",
    ):
        assert token in function
    assert "UPDATE " not in function
    assert "INSERT " not in function
    assert "DELETE " not in function


def test_整改D_通用提交确认必须核验核心证据和操作后像并返回三态() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    start = source.index('    _function(\n        "slice7_mutation_confirm_v1"')
    function = source[start : source.index("    _function(", start + 20)]
    for token in (
        "COMMITTED",
        "NOT_COMMITTED",
        "UNKNOWN",
        "service_fulfillment_receipt",
        "service_fulfillment_audit",
        "service_fulfillment_outbox",
        "service_cycle_schedule",
        "service_milestone_revision",
        "service_case_lifecycle_event",
        "service_closing_assessment",
        "service_summary",
        "service_summary_acknowledgement",
        "service_transfer_request",
        "service_transfer_continuation_handoff",
        "proxy_major_authorization",
        "personal_data_export_request",
        "personal_data_export_download_access",
        "request_digest",
        "postimage_digest",
        "response_json",
        "receipt_id",
        "audit_id",
        "event_id",
        "operation_id",
    ):
        assert token in function
    assert "UPDATE " not in function
    assert "INSERT " not in function
    assert "DELETE " not in function


def test_0032仅向前扩展PrivateFile内部ZIP且降级预检先于DDL() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "ck_private_file_mime_type" in source
    assert "application/zip" in source
    assert "PERSONAL_DATA_EXPORT" in source
    assert "EXPORT_ARCHIVE_TOO_LARGE" in source
    assert "EXPORT_RECOVERY_REQUESTED" in source
    assert "EXPORT_RETRY_REQUESTED" in source
    assert "EXPORT_FILE_CLEANED" in source
    assert "NOT EXISTS" in source
    downgrade = source[source.index("def downgrade()") :]
    assert "Slice 7 downgrade requires no generated export private files" in downgrade
    assert downgrade.index("PERSONAL_DATA_EXPORT") < downgrade.index("DROP FUNCTION")


def test_整改A_方案接受在0032内原子建立唯一履约周期() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "slice7_plan_activation_v1" in source
    assert "trg_slice7_plan_activation_v1" in source
    assert "AFTER UPDATE OF status ON public.health_plan_version" in source
    assert "OLD.status IS DISTINCT FROM 'ACTIVE'" in source
    assert "NEW.status='ACTIVE'" in source
    assert "ON CONFLICT (service_case_id) WHERE is_current DO NOTHING" in source
    assert "PLAN_ACCEPTED" in source


def test_整改A_MARK_MISSED具有真实幂等Mutation分支() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "operation_name='MARK_MISSED' THEN UPDATE" in source
    assert "status IN ('PENDING','DUE')" in source
    assert "status='MISSED'" in source
    assert "MILESTONE_MISSED" in source


def test_整改A_读模型不把无周期PREPARING误报ACTIVE并计算真实关闭水位() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "'PLAN_PENDING'" in source
    assert "'closing_readiness',public.slice7_closing_readiness_v1" in source
    assert "'closing_readiness','NOT_READY'" not in source


def test_整改A_关闭材料与ACK均在数据库比较expected_version() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for operation in ("CREATE_CLOSING_ASSESSMENT", "CREATE_SUMMARY", "ACK_SUMMARY"):
        branch = source[source.index(f"operation_name='{operation}'") :]
        branch = branch[: branch.index("ELSIF", 1) if "ELSIF" in branch[1:] else len(branch)]
        assert "expected_version" in branch
    assert "ACK_SUMMARY" in source
    assert "'COMPLETED'" in source
