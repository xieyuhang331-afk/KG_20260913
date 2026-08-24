from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "app/migrations/versions/20260826_0031_phase1_slice6_health_plan_generation_review.py"

TABLES = {
    "health_plan_template_version",
    "health_plan_generation_request",
    "health_plan_version",
    "health_plan_review",
    "health_plan_explanation",
    "health_plan_user_decision",
    "health_plan_receipt",
    "health_plan_audit",
    "health_plan_outbox",
    "health_plan_delivery",
}

FUNCTIONS = {
    "slice6_generation_authority_v1",
    "slice6_mutation_replay_v1",
    "slice6_generation_request_v1",
    "slice6_generation_worker_v1",
    "slice6_generation_input_v1",
    "slice6_generation_complete_v1",
    "slice6_review_transition_v1",
    "slice6_plan_explanation_v1",
    "slice6_user_decision_v1",
    "slice6_template_governance_v1",
    "slice6_case_read_authority_v1",
    "slice6_actor_read_authority_v1",
    "slice6_outbox_claim_v1",
    "slice6_outbox_consume_v1",
    "slice6_outbox_recover_v1",
}


def test_0031线性继承0030且对象目录闭合() -> None:
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
        "revision": "20260826_0031",
        "down_revision": "20260825_0030",
        "branch_labels": None,
        "depends_on": None,
    }
    assert all(f'"{name}"' in source for name in TABLES | FUNCTIONS)


def test_0031六身份最小权限和受限函数安全边界() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for purpose in (
        "INSTITUTION_WRITER",
        "TEMPLATE_WRITER",
        "REVIEW_WRITER",
        "WORKFLOW_WORKER",
        "CLINICAL_READER",
        "FAMILY_READER",
    ):
        assert f"KG_SLICE6_{purpose}_ROLE" in source
        assert f"KG_SLICE6_{purpose}_DATABASE_URL" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog, pg_temp" in source
    assert "REVOKE ALL ON FUNCTION" in source
    assert "FROM PUBLIC" in source
    assert "GRANT ALL" not in source
    assert "CREATE SEQUENCE" not in source
    assert "GRANT USAGE, SELECT ON" not in source
    assert "EXECUTE format(" not in source


def test_0031资格高风险和模板权威在数据库收口() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        "institution_service_readiness",
        "assessment_module_result",
        "high_risk_task",
        "health_assessment blocked",
        "ASSESSMENT_READY",
        "SERVICE_READY",
        "PUBLISHED",
        "HIGH_RISK_BLOCKING",
        "CONSENT_NOT_CURRENT",
        "PRIMARY_THERAPIST_NOT_CURRENT",
    ):
        assert token in source
    assert "public.slice5_ordinary_plan_authority_v1(c.case_id)" not in source
    assert "pg_advisory_xact_lock" in source
    assert "uq_health_plan_generation_active_case" in source
    assert "uq_health_plan_active_case" in source


def test_0031回滚先做非空预检且不修改历史Migration对象() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade = source[source.index("def downgrade()") :]
    assert "Slice 6 downgrade requires empty module tables" in downgrade
    first_drop = min(
        position
        for token in ("op.drop_table", "DROP FUNCTION", "DROP VIEW")
        if (position := downgrade.find(token)) >= 0
    )
    assert 0 <= downgrade.find("SELECT EXISTS") < first_drop
    assert "CASCADE" not in downgrade
    assert "ALTER TABLE public.service_case" not in source
    assert "ALTER TABLE public.health_assessment" not in source
