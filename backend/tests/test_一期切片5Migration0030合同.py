from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260825_0030_phase1_slice5_deterministic_assessment_high_risk.py"

TABLES = {
    "assessment_rule_set_version",
    "health_assessment",
    "assessment_input_snapshot",
    "assessment_module_result",
    "high_risk_task",
    "high_risk_task_action",
    "assessment_dispute",
    "slice5_idempotency",
    "slice5_audit",
    "slice5_outbox",
    "slice5_delivery",
}

FUNCTIONS = {
    "slice5_assessment_start_authority_v1",
    "slice5_assessment_start_replay_v1",
    "slice5_assessment_snapshot_write_v1",
    "slice5_assessment_worker_v1",
    "slice5_assessment_input_v1",
    "slice5_assessment_complete_v1",
    "slice5_assessment_confirm_v1",
    "slice5_actor_read_authority_v1",
    "slice5_family_subject_authority_v1",
    "slice5_assessment_dispute_v1",
    "slice5_high_risk_transition_v1",
    "slice5_rule_governance_v1",
    "slice5_ordinary_plan_authority_v1",
    "slice5_outbox_claim_v1",
    "slice5_outbox_consume_v1",
    "slice5_outbox_recover_v1",
    "slice5_outbox_reopen_v1",
}


def test_0030是Hotfix0029后的唯一线性候选且对象目录闭合() -> None:
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
        "revision": "20260825_0030",
        "down_revision": "20260824_0029",
        "branch_labels": None,
        "depends_on": None,
    }
    assert all(f'"{name}"' in source for name in TABLES | FUNCTIONS)
    assert "20260823_0028_phase1_slice4" not in source


def test_0030六身份最小权限和受限函数安全边界() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for purpose in (
        "ASSESSMENT_WRITER",
        "RISK_WORKFLOW_WRITER",
        "RULE_GOVERNANCE_WRITER",
        "WORKFLOW_WORKER",
        "CLINICAL_READER",
        "OVERSIGHT_READER",
    ):
        assert f"KG_SLICE5_{purpose}_ROLE" in source
        assert f"KG_SLICE5_{purpose}_DATABASE_URL" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog, pg_temp" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT USAGE ON SCHEMA public" in source
    assert "GRANT USAGE, SELECT ON" not in source
    assert "GRANT SELECT (event_id" not in source
    assert "INSERT (delivery_id" not in source
    assert "GRANT ALL" not in source
    assert "EXECUTE format(" not in source
    assert "CREATE SEQUENCE" not in source


def test_0030回滚先做非空预检且不修改历史对象() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]
    assert "Slice 5 downgrade requires empty module tables" in downgrade
    first_drop = min(
        position
        for token in ("op.drop_table", "DROP FUNCTION", "DROP VIEW")
        if (position := downgrade.find(token)) >= 0
    )
    assert 0 <= downgrade.find("SELECT EXISTS") < first_drop
    assert "ALTER TABLE public.service_case" not in source
    assert "ALTER TABLE public.assessment_input_assembly" not in source
    assert "DROP TABLE public." not in source
    assert downgrade.find('"fk_health_assessment_snapshot"') < downgrade.find(
        "for table in reversed(_MODULE_TABLES)"
    )
    assert "CASCADE" not in downgrade


def test_0030发布规则与完成评估都由数据库权威收口() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert "author_user_id <> reviewer_user_id" in source
    assert "PUBLISHED" in source
    assert "CN_ADULT_BASELINE_V1" in source
    assert "jsonb_array_length" in source
    assert "HIGH_RISK" in source
    assert "uq_high_risk_task_assessment" in source
    assert "ordinary plan blocked by current high risk assessment" in source


def test_0030测量场景列为空兼容且完整传播到Worker快照() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    compact = "".join(source.split())
    assert '("canonical_health_fact","health_projection_fact","assessment_input_assembly_fact")' in compact
    assert 'op.add_column(table,sa.Column("measurement_context"' in compact
    assert "nullable=True" in compact
    assert "UPDATEpublic.canonical_health_factSETmeasurement_context" not in compact
    assert "'measurement_context',f.measurement_context" in compact
    assert "slice5_measurement_context_projection_v1" in source
    assert 'os.environ["KG_HEALTH_PROJECTION_SHADOW_ROLE"]' in source
    assert "(health_builder, health_shadow)" in source
    assert "_SLICE5_INDICATOR_CHECK" in source
    assert "_SLICE4_INDICATOR_CHECK" in source
    assert "_SLICE5_PROJECTION_INDICATOR_CHECK" in source
    assert "_SLICE4_PROJECTION_INDICATOR_CHECK" in source
    assert "ck_canonical_health_fact_indicator_v1_v2" in source
    assert "ck_health_projection_fact_indicator_v1_v2" in source
    assert "count(DISTINCT (x->>'indicator_code',x->>'fact_ref'))" in source
    assert "GET DIAGNOSTICS value_updated = ROW_COUNT" in source
    assert "FROM PUBLIC" in source
