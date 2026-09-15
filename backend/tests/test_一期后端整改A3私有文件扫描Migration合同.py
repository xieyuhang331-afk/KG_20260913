from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "app" / "migrations" / "versions"
MIGRATION = MIGRATIONS / "20260904_0037_a3_private_file_scan_convergence.py"


def _heads() -> set[str]:
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in MIGRATIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        values: dict[str, str | tuple[str, ...] | None] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
        revision = values.get("revision")
        parent = values.get("down_revision")
        if isinstance(revision, str):
            revisions.add(revision)
        if isinstance(parent, str):
            parents.add(parent)
        elif isinstance(parent, tuple):
            parents.update(parent)
    return revisions - parents


def _load_migration():
    spec = importlib.util.spec_from_file_location("a3_migration_0037", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_A3_0037从0036派生且保持单一Head():
    assert MIGRATION.exists(), "A3_0037_MIGRATION_MISSING"
    module = _load_migration()
    assert module.revision == "20260904_0037"
    assert module.down_revision == "20260903_0036"
    assert _heads() == {"20260914_0047"}


def test_A3_0037字段约束历史回填与索引合同闭合():
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        "scan_attempt_count",
        "scan_last_error_code",
        "scan_next_retry_at",
        "scan_lease_token",
        "scan_lease_until",
        "scan_operation_ref_digest",
        "scan_version",
        "BETWEEN 0 AND 4",
        "PENDING_SCAN",
        "20260904_0037",
    ):
        assert token in source
    assert "KG_PRIVATE_FILE_STORAGE_ROOT" not in source
    assert "read_bytes" not in source
    assert (
        "SET scan_attempt_count=1 WHERE status IN ('CLEAN','REJECTED')" in source
    ), "A3_0037_HISTORICAL_TERMINAL_ATTEMPT_BACKFILL_MISSING"
    assert (
        "SET scan_attempt_count=4, "
        "scan_last_error_code='LEGACY_SCAN_FAILED' WHERE status='SCAN_FAILED'"
        in source
    ), "A3_0037_HISTORICAL_FAILED_ATTEMPT_BACKFILL_MISSING"


def test_A3_0037引用authority静态SQL及最小ACL():
    source = MIGRATION.read_text(encoding="utf-8")
    assert "a3_private_file_referenced_v1" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog" in source
    for table in (
        "institution_license",
        "therapist_qualification_attachment",
        "detection_report_attachment",
        "personal_data_export_artifact",
        "personal_data_export_download_access",
    ):
        assert table in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert "EXECUTE format" not in source


def test_A3_0037_downgrade只撤销本Revision对象():
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade") :]
    assert "DROP FUNCTION" in downgrade
    expected_columns = (
        "scan_attempt_count",
        "scan_last_error_code",
        "scan_next_retry_at",
        "scan_lease_token",
        "scan_lease_until",
        "scan_operation_ref_digest",
        "scan_version",
    )
    module = _load_migration()
    assert expected_columns == module._SCAN_COLUMNS
    assert "reversed(_SCAN_COLUMNS)" in downgrade
    assert "DELETE FROM public.private_file" not in downgrade
    assert "UPDATE public.private_file" not in downgrade


def test_A3_I2_0037_downgrade任何DDL前必须完成quiescent预检():
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade") :]
    preflight = downgrade.index("A3_PRIVATE_FILE_DOWNGRADE_NOT_QUIESCENT")
    first_revoke = downgrade.index("REVOKE")
    first_drop = downgrade.index("DROP")
    assert "LOCK TABLE public.private_file IN ACCESS EXCLUSIVE MODE" in downgrade
    assert "status='PENDING_SCAN'" in downgrade
    assert "scan_next_retry_at IS NOT NULL" in downgrade
    assert "scan_lease_until>clock_timestamp()" in downgrade
    assert preflight < first_revoke
    assert preflight < first_drop
