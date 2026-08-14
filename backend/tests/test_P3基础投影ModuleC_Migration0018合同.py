from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "app/migrations/versions/20260814_0018_p3_basic_projection_shadow_ready_gate.py"


def test_0018仅增加Module_C且禁止CASCADE_ACTIVE_API():
    text = MIGRATION.read_text("utf-8")
    assert 'revision = "20260814_0018"' in text
    assert 'down_revision = "20260813_0017"' in text
    assert " CASCADE" not in text.upper()
    assert "ACTIVE" not in text
    assert "cutover" not in text.lower()
    assert "api" not in text.lower()


def test_0018四角色与Confirmation零DML设计存在():
    text = MIGRATION.read_text("utf-8")
    for value in ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_ROLE"):
        assert value in text


def test_0018_adds_only_one_path_version_baseline_field_and_drops_it_symmetrically():
    text = MIGRATION.read_text("utf-8")
    assert '"path_versions"' in text
    assert "jsonb_array_length(path_versions)=4" in text
    assert 'op.drop_column("organization_projection", "path_versions"' in text


def test_0018_canonical_postimage_generation_select_is_symmetric():
    text = MIGRATION.read_text("utf-8")
    expected = "id,projection_version,status,high_watermark,digest_key_id,input_digest,updated_at,completed_at,version,current_shadow_run_id,shadow_success_count,ready_at,ready_operation_id"
    assert f"GRANT SELECT ({expected})" in text
    assert f"REVOKE SELECT ({expected})" in text


def test_0018_downgrade_locks_and_rejects_nonempty_projection_before_revoke_or_drop():
    text = MIGRATION.read_text("utf-8")
    downgrade = text.split("def downgrade():", 1)[1]
    lock = 'LOCK TABLE public.organization_projection IN ACCESS EXCLUSIVE MODE'
    nonempty = 'SELECT EXISTS (SELECT 1 FROM public.organization_projection)'
    assert lock in downgrade
    assert nonempty in downgrade
    assert downgrade.index(lock) < downgrade.index("REVOKE")
    assert downgrade.index(nonempty) < downgrade.index("REVOKE")


def test_0018_path_versions_constraint_requires_exact_positive_integers():
    text = MIGRATION.read_text("utf-8")
    assert text.count('op.f("ck_organization_projection_compatibility")') == 2
    assert 'op.f("ck_organization_projection_ck_organization_projection_c_41f6")' in text
    assert 'if len(compatibility_names) != 1:' in text
    assert 'create_check_constraint(\n        "ck_organization_projection_compatibility"' not in text
    assert "@ % 1 != 0" in text
    assert '@ < 1' in text
    assert 'f"{domain}_projection_shadow_run"' in text


def test_0018_column_grants_and_downgrade_revoke_source_privileges():
    text = MIGRATION.read_text("utf-8")
    assert "GRANT SELECT ON TABLE public.{run}" not in text
    assert "GRANT INSERT ({_columns(run_insert)})" in text
    assert "_revoke_module_c_source_privileges(roles)" in text
    assert "REVOKE SELECT (generation_id,organization_id" in text
    assert "REVOKE SELECT (generation_id,fact_id" in text


def test_0018_downgrade精确恢复0017failure_enum且Shadow_truth_table完整():
    text = MIGRATION.read_text("utf-8")
    assert "failure_code IN ({_FAILURES})" in text
    assert "failure_code IS NOT NULL" not in text
    assert "category_counts - ARRAY" in text
    assert "coverage_numerator=coverage_denominator" in text
    assert "fact_coverage_numerator=fact_coverage_denominator" in text
    assert "selection_coverage_numerator=selection_coverage_denominator" in text


def test_0018_category_truth_table分别绑定三类计数():
    text = MIGRATION.read_text("utf-8")
    assert "=blocker_count" in text
    assert "=review_required_count" in text
    assert "=informational_count" in text
    assert "=blocker_count+review_required_count+informational_count" not in text


def test_0018角色预检覆盖既有运行身份隔离():
    text = MIGRATION.read_text("utf-8")
    for value in (
        "KG_ORGANIZATION_PROJECTION_BUILDER_ROLE",
        "KG_HEALTH_PROJECTION_BUILDER_ROLE",
        "KG_PROJECTION_CONFIRMATION_ROLE",
        "KG_DATABASE_USER",
        "KG_TEST_MIGRATION_ROLE",
        "KG_TEST_READONLY_ROLE",
    ):
        assert value in text
