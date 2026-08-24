from __future__ import annotations

import inspect

from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID

from app.core.database import Base
from app.modules.health_assessment import models
from app.modules.health_assessment import repository, service


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

UUID_COLUMNS = {
    "assessment_rule_set_version": {"rule_set_version_id"},
    "health_assessment": {
        "assessment_id",
        "service_case_id",
        "subject_member_id",
        "snapshot_id",
        "rule_set_version_id",
        "supersedes_assessment_id",
    },
    "assessment_input_snapshot": {
        "snapshot_id",
        "assessment_id",
        "service_case_id",
        "subject_member_id",
        "assembly_id",
        "profile_revision_id",
        "rule_set_version_id",
    },
    "assessment_module_result": {"module_result_id", "assessment_id"},
    "high_risk_task": {
        "task_id",
        "assessment_id",
        "service_case_id",
        "subject_member_id",
    },
    "high_risk_task_action": {"action_id", "task_id"},
    "assessment_dispute": {
        "dispute_id",
        "assessment_id",
        "superseding_assessment_id",
    },
    "slice5_idempotency": {"receipt_id", "target_id"},
    "slice5_audit": {"audit_id", "target_id"},
    "slice5_outbox": {"event_id", "aggregate_ref"},
    "slice5_delivery": {"delivery_id", "event_id", "target_ref"},
}


def test_slice5ORM目录与Migration对象一一对应且UUID不降级为字符串() -> None:
    actual = {
        table.name
        for table in Base.metadata.tables.values()
        if table.schema == "public" and table.name in TABLES
    }
    assert actual == TABLES
    for table_name in TABLES:
        table = Base.metadata.tables[f"public.{table_name}"]
        for column_name in UUID_COLUMNS[table_name]:
            column = table.columns[column_name]
            assert isinstance(column.type, PostgreSQLUUID), (table_name, column.name)
            assert column.type.as_uuid is True


def test_slice5架构不导入AI或旧健康展示模块并只经受限边界取输入() -> None:
    combined = "\n".join(
        inspect.getsource(module) for module in (repository, service)
    )
    assert "health_analysis" not in combined
    assert "canonical_health_fact" not in combined
    assert "health_projection_fact" not in combined
    assert "assessment_input_assembly_fact" not in inspect.getsource(repository)
    assert "slice5_assessment_input_v1" in inspect.getsource(repository)
    assert "evaluate_cn_adult_baseline_v1" in inspect.getsource(service)


def test_slice5Repository无字符串拼接SQL和管理员身份旁路() -> None:
    source = inspect.getsource(repository)
    assert "admin_session" not in source.lower()
    assert "migration_session" not in source.lower()
    assert "superuser" not in source.lower()
    assert "f\"SELECT" not in source
    assert "f'SELECT" not in source
    assert "bindparam" in source
    assert 'bindparam("tenant_id", type_=BigInteger())' in source
    assert 'bindparam("status", type_=String())' in source
