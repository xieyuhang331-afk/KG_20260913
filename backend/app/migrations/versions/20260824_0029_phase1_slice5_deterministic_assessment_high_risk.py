"""Phase 1 Slice 5 deterministic health assessment and high-risk workflow.

Revision ID: 20260824_0029
Revises: 20260823_0028
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260824_0029"
down_revision = "20260823_0028"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212025
_SLICE4_INDICATOR_CHECK = (
    "(catalog_version=1 AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR "
    "(catalog_version=2 AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','hba1c','weight','height','waist'))"
)
_SLICE5_INDICATOR_CHECK = (
    "(catalog_version=1 AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR "
    "(catalog_version=2 AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','height','waist'))"
)
_SLICE4_PROJECTION_INDICATOR_CHECK = (
    "(subject_member_id IS NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR "
    "(subject_member_id IS NOT NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','hba1c','weight','height','waist'))"
)
_SLICE5_PROJECTION_INDICATOR_CHECK = (
    "(subject_member_id IS NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR "
    "(subject_member_id IS NOT NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate',"
    "'fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride',"
    "'hdl_c','ldl_c','weight','height','waist'))"
)
_NEW_IDENTITIES = (
    ("KG_SLICE5_ASSESSMENT_WRITER_ROLE", "KG_SLICE5_ASSESSMENT_WRITER_DATABASE_URL"),
    ("KG_SLICE5_RISK_WORKFLOW_WRITER_ROLE", "KG_SLICE5_RISK_WORKFLOW_WRITER_DATABASE_URL"),
    ("KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE", "KG_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL"),
    ("KG_SLICE5_WORKFLOW_WORKER_ROLE", "KG_SLICE5_WORKFLOW_WORKER_DATABASE_URL"),
    ("KG_SLICE5_CLINICAL_READER_ROLE", "KG_SLICE5_CLINICAL_READER_DATABASE_URL"),
    ("KG_SLICE5_OVERSIGHT_READER_ROLE", "KG_SLICE5_OVERSIGHT_READER_DATABASE_URL"),
)
_MODULE_TABLES = (
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
)
_FUNCTIONS = (
    ("slice5_measurement_context_projection_v1", "UUID[]"),
    ("slice5_assembly_measurement_context_bind_v1", "UUID,JSONB"),
    ("slice5_assessment_start_authority_v1", "UUID,BIGINT"),
    ("slice5_assessment_start_replay_v1", "BIGINT,VARCHAR,BYTEA"),
    ("slice5_assessment_snapshot_write_v1", "JSONB"),
    ("slice5_assessment_worker_v1", "VARCHAR,JSONB"),
    ("slice5_assessment_input_v1", "UUID"),
    ("slice5_assessment_complete_v1", "JSONB"),
    ("slice5_assessment_confirm_v1", "UUID"),
    ("slice5_actor_read_authority_v1", "BIGINT,VARCHAR,UUID,UUID,BIGINT"),
    ("slice5_family_subject_authority_v1", "BIGINT,UUID"),
    ("slice5_assessment_dispute_v1", "JSONB"),
    ("slice5_high_risk_transition_v1", "JSONB"),
    ("slice5_rule_governance_v1", "VARCHAR,JSONB"),
    ("slice5_ordinary_plan_authority_v1", "UUID"),
    ("slice5_outbox_claim_v1", "UUID,BIGINT"),
    ("slice5_outbox_consume_v1", "JSONB"),
    ("slice5_outbox_recover_v1", "TIMESTAMP WITH TIME ZONE"),
    ("slice5_outbox_reopen_v1", "UUID,BIGINT"),
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 5 database role configuration is invalid") from None


def _roles() -> tuple[str, str, str, str, str, str]:
    configured: list[str] = []
    targets: set[tuple[str | None, int | None, str | None]] = set()
    for role_var, url_var in _NEW_IDENTITIES:
        role = os.getenv(role_var, "").strip()
        raw_url = os.getenv(url_var, "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
            _configuration_error()
        try:
            url = make_url(raw_url)
        except Exception:
            _configuration_error()
        if url.drivername != "postgresql+asyncpg" or url.username != role or not url.password:
            _configuration_error()
        configured.append(role)
        targets.add((url.host, url.port, url.database))
    if len(set(configured)) != 6 or len(targets) != 1:
        _configuration_error()
    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    if current_user in configured:
        _configuration_error()
    rows = connection.execute(
        sa.text(
            "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(:roles)"
        ),
        {"roles": configured},
    ).mappings().all()
    unsafe = ("rolsuper", "rolcreaterole", "rolcreatedb", "rolinherit", "rolreplication", "rolbypassrls")
    if len(rows) != 6 or any(row[name] for row in rows for name in unsafe):
        _configuration_error()
    memberships = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles a ON a.oid=m.member "
            "JOIN pg_roles b ON b.oid=m.roleid WHERE a.rolname=ANY(:roles) OR b.rolname=ANY(:roles))"
        ),
        {"roles": configured},
    ).scalar_one()
    if memberships:
        _configuration_error()
    return tuple(configured)  # type: ignore[return-value]


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=False), nullable=nullable)


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _create_tables() -> None:
    op.create_table(
        "assessment_rule_set_version",
        _uuid("rule_set_version_id"),
        sa.Column("rule_set_code", sa.String(64), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("typed_rule_payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        sa.Column("author_user_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewer_user_id", sa.BigInteger(), nullable=True),
        sa.Column("approval_evidence_ref", sa.String(256), nullable=True),
        _ts("effective_from", nullable=True),
        _ts("suspended_at", nullable=True),
        _ts("retired_at", nullable=True),
        _ts("created_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("rule_set_version_id", name="pk_assessment_rule_set_version"),
        sa.UniqueConstraint("version_no", name="uq_assessment_rule_set_version_no"),
        sa.UniqueConstraint("rule_set_code", "content_digest", name="uq_assessment_rule_set_content"),
        sa.CheckConstraint(
            "rule_set_code='CN_ADULT_BASELINE_V1' AND version>=1 AND "
            "status IN ('DRAFT','IN_REVIEW','NEEDS_CORRECTION','PUBLISHED','SUSPENDED','RETIRED') AND "
            "(reviewer_user_id IS NULL OR author_user_id <> reviewer_user_id)",
            name="ck_assessment_rule_set_version_truth",
        ),
        schema="public",
    )
    op.create_index(
        "uq_assessment_rule_set_published",
        "assessment_rule_set_version",
        ["rule_set_code"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status='PUBLISHED'"),
    )
    op.create_table(
        "health_assessment",
        _uuid("assessment_id"),
        _uuid("service_case_id"),
        _uuid("subject_member_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        _uuid("snapshot_id"),
        _uuid("rule_set_version_id"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("overall_risk", sa.String(24), nullable=True),
        _uuid("supersedes_assessment_id", nullable=True),
        sa.Column("initiated_by", sa.BigInteger(), nullable=False),
        _ts("initiated_at"),
        _ts("started_at", nullable=True),
        _ts("completed_at", nullable=True),
        _ts("failed_at", nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("assessment_id", name="pk_health_assessment"),
        sa.UniqueConstraint("service_case_id", "sequence_no", name="uq_health_assessment_case_sequence"),
        sa.UniqueConstraint("assessment_id", "service_case_id", name="uq_health_assessment_case"),
        sa.ForeignKeyConstraint(["service_case_id"], ["public.service_case.case_id"], name="fk_health_assessment_case"),
        sa.ForeignKeyConstraint(["subject_member_id"], ["identity.member.member_id"], name="fk_health_assessment_member"),
        sa.ForeignKeyConstraint(["rule_set_version_id"], ["public.assessment_rule_set_version.rule_set_version_id"], name="fk_health_assessment_rule_set"),
        sa.ForeignKeyConstraint(["supersedes_assessment_id"], ["public.health_assessment.assessment_id"], name="fk_health_assessment_supersedes"),
        sa.CheckConstraint(
            "sequence_no>=1 AND version>=1 AND status IN "
            "('DRAFT_SNAPSHOT','RUNNING','COMPLETED','FAILED','UNDER_REVIEW','SUPERSEDED') AND "
            "(overall_risk IS NULL OR overall_risk IN ('NOT_ASSESSED','WITHIN_RANGE','ATTENTION','HIGH_RISK'))",
            name="ck_health_assessment_truth",
        ),
        schema="public",
    )
    op.create_table(
        "assessment_input_snapshot",
        _uuid("snapshot_id"),
        _uuid("assessment_id"),
        _uuid("service_case_id"),
        _uuid("subject_member_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        _uuid("assembly_id"),
        sa.Column("assembly_digest", sa.LargeBinary(), nullable=False),
        sa.Column("source_vector_digest", sa.LargeBinary(), nullable=False),
        _uuid("profile_revision_id"),
        sa.Column("consent_version_ids", postgresql.JSONB(), nullable=False),
        _uuid("rule_set_version_id"),
        sa.Column("projection_version", sa.BigInteger(), nullable=False),
        sa.Column("projection_rule_version", sa.String(64), nullable=False),
        sa.Column("required_max_fact_id", sa.BigInteger(), nullable=False),
        sa.Column("required_max_status_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("source_snapshot", sa.String(512), nullable=False),
        sa.Column("fact_ref_manifest_digest", sa.LargeBinary(), nullable=False),
        sa.Column("snapshot_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_assessment_input_snapshot"),
        sa.UniqueConstraint("assessment_id", name="uq_assessment_input_snapshot_assessment"),
        sa.ForeignKeyConstraint(
            ["assessment_id", "service_case_id"],
            ["public.health_assessment.assessment_id", "public.health_assessment.service_case_id"],
            name="fk_assessment_input_snapshot_assessment",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["assembly_id", "service_case_id"], ["public.assessment_input_assembly.assembly_id", "public.assessment_input_assembly.service_case_id"], name="fk_assessment_input_snapshot_assembly"),
        sa.ForeignKeyConstraint(["profile_revision_id"], ["public.health_profile_revision.profile_revision_id"], name="fk_assessment_input_snapshot_profile_revision"),
        sa.ForeignKeyConstraint(["rule_set_version_id"], ["public.assessment_rule_set_version.rule_set_version_id"], name="fk_assessment_input_snapshot_rule_set"),
        sa.CheckConstraint("required_max_fact_id>=0 AND required_max_status_event_seq>=0", name="ck_assessment_input_snapshot_hwm"),
        schema="public",
    )
    op.create_foreign_key(
        "fk_health_assessment_snapshot",
        "health_assessment",
        "assessment_input_snapshot",
        ["snapshot_id"],
        ["snapshot_id"],
        source_schema="public",
        referent_schema="public",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "assessment_module_result",
        _uuid("module_result_id"),
        _uuid("assessment_id"),
        sa.Column("module_code", sa.String(48), nullable=False),
        sa.Column("risk_level", sa.String(24), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("message_codes", postgresql.JSONB(), nullable=False),
        sa.Column("rule_fragment_digest", sa.LargeBinary(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("module_result_id", name="pk_assessment_module_result"),
        sa.UniqueConstraint("assessment_id", "module_code", name="uq_assessment_module_result_module"),
        sa.ForeignKeyConstraint(["assessment_id"], ["public.health_assessment.assessment_id"], name="fk_assessment_module_result_assessment"),
        sa.CheckConstraint(
            "module_code IN ('BLOOD_PRESSURE_CARDIOVASCULAR','GLUCOSE_METABOLISM','LIPID_METABOLISM','WEIGHT_ABDOMINAL_OBESITY') "
            "AND risk_level IN ('NOT_ASSESSED','WITHIN_RANGE','ATTENTION','HIGH_RISK')",
            name="ck_assessment_module_result_truth",
        ),
        schema="public",
    )
    op.create_table(
        "high_risk_task",
        _uuid("task_id"),
        _uuid("assessment_id"),
        _uuid("service_case_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        _uuid("subject_member_id"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason_module_codes", postgresql.JSONB(), nullable=False),
        sa.Column("trigger_codes", postgresql.JSONB(), nullable=False),
        sa.Column("assigned_actor_id", sa.BigInteger(), nullable=True),
        _ts("due_at"),
        _ts("claimed_at", nullable=True),
        _ts("closed_at", nullable=True),
        sa.Column("close_reason_code", sa.String(64), nullable=True),
        _ts("created_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("task_id", name="pk_high_risk_task"),
        sa.UniqueConstraint("assessment_id", name="uq_high_risk_task_assessment"),
        sa.ForeignKeyConstraint(["assessment_id", "service_case_id"], ["public.health_assessment.assessment_id", "public.health_assessment.service_case_id"], name="fk_high_risk_task_assessment"),
        sa.ForeignKeyConstraint(["subject_member_id"], ["identity.member.member_id"], name="fk_high_risk_task_member"),
        sa.CheckConstraint("status IN ('OPEN','CLAIMED','ESCALATED','REFERRED','RESOLVED') AND version>=1", name="ck_high_risk_task_truth"),
        schema="public",
    )
    op.create_table(
        "high_risk_task_action",
        _uuid("action_id"),
        _uuid("task_id"),
        sa.Column("from_status", sa.String(24), nullable=False),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("action_code", sa.String(64), nullable=False),
        sa.Column("contact_outcome_code", sa.String(64), nullable=True),
        sa.Column("advice_code", sa.String(64), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        _ts("occurred_at"),
        sa.Column("evidence_digest", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("action_id", name="pk_high_risk_task_action"),
        sa.ForeignKeyConstraint(["task_id"], ["public.high_risk_task.task_id"], name="fk_high_risk_task_action_task"),
        schema="public",
    )
    op.create_table(
        "assessment_dispute",
        _uuid("dispute_id"),
        _uuid("assessment_id"),
        sa.Column("raised_by", sa.BigInteger(), nullable=False),
        sa.Column("actor_context", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("statement_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("statement_key_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("resolution_code", sa.String(64), nullable=True),
        _uuid("superseding_assessment_id", nullable=True),
        _ts("created_at"),
        _ts("resolved_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("dispute_id", name="pk_assessment_dispute"),
        sa.ForeignKeyConstraint(["assessment_id"], ["public.health_assessment.assessment_id"], name="fk_assessment_dispute_assessment"),
        sa.ForeignKeyConstraint(["superseding_assessment_id"], ["public.health_assessment.assessment_id"], name="fk_assessment_dispute_superseding"),
        sa.CheckConstraint("status IN ('OPEN','RESOLVED','SUPERSEDED') AND version>=1", name="ck_assessment_dispute_truth"),
        schema="public",
    )
    op.create_table(
        "slice5_idempotency",
        _uuid("receipt_id"),
        sa.Column("actor_scope", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        _uuid("target_id"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.LargeBinary(), nullable=False),
        sa.Column("response_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("response_key_id", sa.String(64), nullable=False),
        sa.Column("postimage_digest", sa.LargeBinary(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_slice5_idempotency"),
        sa.UniqueConstraint("actor_scope", "operation", "target_id", "idempotency_key", name="uq_slice5_idempotency_key"),
        schema="public",
    )
    op.create_table(
        "slice5_audit",
        _uuid("audit_id"),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        _uuid("target_id"),
        sa.Column("evidence_digest", sa.LargeBinary(), nullable=False),
        _ts("occurred_at"),
        sa.PrimaryKeyConstraint("audit_id", name="pk_slice5_audit"),
        schema="public",
    )
    op.create_table(
        "slice5_outbox",
        _uuid("event_id"),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        _uuid("aggregate_ref"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.LargeBinary(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        _ts("lease_until", nullable=True),
        _ts("created_at"),
        _ts("delivered_at", nullable=True),
        sa.PrimaryKeyConstraint("event_id", name="pk_slice5_outbox"),
        sa.CheckConstraint("status IN ('PENDING','CLAIMED','RETRY','DELIVERED','FAILED') AND attempts>=0", name="ck_slice5_outbox_truth"),
        schema="public",
    )
    op.create_table(
        "slice5_delivery",
        _uuid("delivery_id"),
        _uuid("event_id"),
        sa.Column("target_type", sa.String(32), nullable=False),
        _uuid("target_ref"),
        sa.Column("target_digest", sa.LargeBinary(), nullable=False),
        _ts("delivered_at"),
        sa.PrimaryKeyConstraint("delivery_id", name="pk_slice5_delivery"),
        sa.UniqueConstraint("event_id", "target_type", "target_ref", name="uq_slice5_delivery_target"),
        sa.ForeignKeyConstraint(["event_id"], ["public.slice5_outbox.event_id"], name="fk_slice5_delivery_event"),
        schema="public",
    )


def _function(
    name: str,
    arguments: str,
    returns: str,
    body: str,
    roles: tuple[str, ...],
    *,
    declarations: str = "",
) -> None:
    owner = str(op.get_bind().execute(sa.text("SELECT current_user")).scalar_one())
    op.execute(
        f"CREATE FUNCTION public.{name}({arguments}) RETURNS {returns} "
        f"LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $fn$ "
        f"{('DECLARE ' + declarations) if declarations else ''} BEGIN {body} END $fn$"
    )
    op.execute(f'ALTER FUNCTION public.{name}({arguments}) OWNER TO "{owner}"')
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({arguments}) FROM PUBLIC")
    for role in roles:
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{name}({arguments}) TO "{role}"')


def _create_functions(
    assessment: str,
    risk: str,
    rule: str,
    worker: str,
    clinical: str,
    oversight: str,
) -> None:
    health_builder = os.environ["KG_HEALTH_PROJECTION_BUILDER_ROLE"]
    health_shadow = os.environ["KG_HEALTH_PROJECTION_SHADOW_ROLE"]
    readiness = os.environ["KG_ASSESSMENT_READINESS_WRITER_ROLE"]
    _function(
        "slice5_measurement_context_projection_v1",
        "value_fact_refs UUID[]",
        "TABLE(fact_ref UUID,measurement_context VARCHAR)",
        f"IF session_user NOT IN ('{health_builder}','{health_shadow}') "
        "THEN RAISE EXCEPTION 'SLICE5_MEASUREMENT_CONTEXT_FORBIDDEN'; END IF; "
        "IF value_fact_refs IS NULL OR cardinality(value_fact_refs)>500 THEN RAISE EXCEPTION 'SLICE5_MEASUREMENT_CONTEXT_SCOPE_INVALID'; END IF; "
        "RETURN QUERY SELECT f.fact_ref,f.measurement_context FROM public.canonical_health_fact f "
        "WHERE f.fact_ref=ANY(value_fact_refs) ORDER BY f.fact_ref FOR SHARE;",
        (health_builder, health_shadow),
    )
    _function(
        "slice5_assembly_measurement_context_bind_v1",
        "value_assembly_id UUID,value_facts JSONB",
        "BOOLEAN",
        f"IF session_user<>'{readiness}' THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_FORBIDDEN'; END IF; "
        "IF jsonb_typeof(value_facts)<>'array' THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_INVALID'; END IF; "
        "PERFORM 1 FROM public.assessment_input_assembly a WHERE a.assembly_id=value_assembly_id FOR UPDATE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_INVALID'; END IF; "
        "IF EXISTS(SELECT 1 FROM jsonb_array_elements(value_facts) x WHERE jsonb_typeof(x)<>'object' "
        "OR NOT (x ? 'indicator_code') "
        "OR NOT (x ? 'fact_ref') OR NOT (x ? 'measurement_context')) THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_INVALID'; END IF; "
        "IF (SELECT count(*) FROM jsonb_array_elements(value_facts)) <> "
        "(SELECT count(DISTINCT (x->>'indicator_code',x->>'fact_ref')) FROM jsonb_array_elements(value_facts) x) "
        "THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_INVALID'; END IF; "
        "UPDATE public.assessment_input_assembly_fact f SET measurement_context=x.measurement_context "
        "FROM (SELECT e->>'indicator_code' indicator_code,(e->>'fact_ref')::uuid fact_ref," 
        "e->>'measurement_context' measurement_context FROM jsonb_array_elements(value_facts) e) x "
        "WHERE f.assembly_id=value_assembly_id AND f.indicator_code=x.indicator_code AND f.fact_ref=x.fact_ref; "
        "GET DIAGNOSTICS value_updated = ROW_COUNT; "
        "IF value_updated <> jsonb_array_length(value_facts) THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_INVALID'; END IF; "
        "IF (SELECT count(*) FROM public.assessment_input_assembly_fact f WHERE f.assembly_id=value_assembly_id) "
        "<> jsonb_array_length(value_facts) THEN RAISE EXCEPTION 'SLICE5_ASSEMBLY_CONTEXT_INVALID'; END IF; RETURN TRUE;",
        (readiness,),
        declarations="value_updated BIGINT;",
    )
    _function(
        "slice5_assessment_start_authority_v1",
        "value_case_id UUID,value_actor_user_id BIGINT",
        "JSONB",
        f"IF session_user<>'{assessment}' THEN RAISE EXCEPTION 'SLICE5_ASSESSMENT_WRITER_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.\"user\" u WHERE u.id=value_actor_user_id AND u.role='therapist' "
        "AND u.status='active' FOR SHARE; IF NOT FOUND THEN RETURN NULL; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_case_id::text,0)); "
        "PERFORM 1 FROM public.service_case c JOIN public.primary_therapist_assignment a ON a.assignment_id=c.assignment_id "
        "JOIN public.therapist_profile t ON t.therapist_id=c.primary_therapist_id "
        "JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id "
        "WHERE c.case_id=value_case_id AND c.status='PREPARING' AND a.status='ACCEPTED' "
        "AND a.service_case_id=c.case_id AND t.user_id=value_actor_user_id AND t.status='APPROVED_ACTIVE' "
        "AND t.current_qualification_version_id IS NOT NULL "
        "AND t.qualification_valid_until >= (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date "
        "AND t.service_tags @> c.service_scope_tags AND sr.readiness_status='SERVICE_READY' "
        "AND sr.evidence_version=c.readiness_evidence_version AND sr.result_digest=c.readiness_result_digest "
        "FOR SHARE OF c,a,t,sr; IF NOT FOUND THEN RETURN NULL; END IF; "
        "RETURN (SELECT jsonb_build_object('case_id',c.case_id,'case_version',c.version,'subject_member_id',c.subject_member_id," 
        "'tenant_id',c.tenant_id,'transaction_time',transaction_timestamp(),'assembly_id',a.assembly_id," 
        "'assembly_status',a.status,'assembly_digest',encode(a.assembly_digest,'hex')," 
        "'source_vector_digest',encode(a.source_vector_digest,'hex'),'profile_revision_id',a.profile_revision_id," 
        "'consent_version_ids',a.consent_version_ids,'projection_version',a.projection_version," 
        "'projection_rule_version',a.rule_version,'required_max_fact_id',a.required_max_fact_id," 
        "'required_max_status_event_seq',a.required_max_status_event_seq,'source_snapshot',a.source_snapshot," 
        "'fact_ref_manifest_digest',encode(sha256(convert_to(COALESCE((SELECT string_agg(f.fact_ref::text,',' ORDER BY f.fact_ref) "
        "FROM public.assessment_input_assembly_fact f WHERE f.assembly_id=a.assembly_id),''),'UTF8')),'hex')," 
        "'rule_set_version_id',r.rule_set_version_id,'rule_set_code',r.rule_set_code," 
        "'rule_version',r.version_no,'next_sequence_no',COALESCE((SELECT max(n.sequence_no)+1 "
        "FROM public.health_assessment n WHERE n.service_case_id=c.case_id),1),"
        "'supersedes_assessment_id',(SELECT old.assessment_id FROM public.health_assessment old "
        "JOIN public.assessment_input_snapshot old_snapshot ON old_snapshot.assessment_id=old.assessment_id "
        "WHERE old.service_case_id=c.case_id AND old.status='UNDER_REVIEW' "
        "AND old_snapshot.assembly_id<>a.assembly_id ORDER BY old.sequence_no DESC LIMIT 1)) "
        "FROM public.service_case c JOIN public.assessment_readiness_case_pointer p ON p.service_case_id=c.case_id "
        "JOIN public.assessment_input_assembly a ON a.assembly_id=p.current_assembly_id AND a.service_case_id=p.service_case_id "
        "JOIN LATERAL (SELECT x.* FROM public.assessment_rule_set_version x WHERE x.status='PUBLISHED' "
        "AND x.effective_from<=clock_timestamp() ORDER BY x.effective_from DESC,x.version_no DESC LIMIT 1) r ON true "
        "WHERE c.case_id=value_case_id AND a.status='ASSESSMENT_READY' FOR SHARE OF p,a,r);",
        (assessment,),
    )
    _function(
        "slice5_assessment_start_replay_v1",
        "value_actor_user_id BIGINT,value_idempotency_key VARCHAR,value_request_digest BYTEA",
        "JSONB",
        f"IF session_user<>'{assessment}' THEN RAISE EXCEPTION 'SLICE5_ASSESSMENT_WRITER_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.\"user\" u WHERE u.id=value_actor_user_id AND u.role='therapist' "
        "AND u.status='active' FOR SHARE; IF NOT FOUND THEN RAISE EXCEPTION 'ACTOR_CURRENTNESS_FORBIDDEN'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_actor_user_id::text "
        "AND i.operation='ASSESSMENT_START' AND i.idempotency_key=value_idempotency_key) THEN RETURN NULL; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_actor_user_id::text "
        "AND i.operation='ASSESSMENT_START' AND i.idempotency_key=value_idempotency_key "
        "AND i.request_digest=value_request_digest) THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; "
        "PERFORM 1 FROM public.health_assessment h WHERE h.assessment_id=(SELECT i.target_id FROM public.slice5_idempotency i "
        "WHERE i.actor_scope=value_actor_user_id::text AND i.operation='ASSESSMENT_START' "
        "AND i.idempotency_key=value_idempotency_key) FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "PERFORM 1 FROM public.assessment_input_snapshot s WHERE s.assessment_id=(SELECT i.target_id FROM public.slice5_idempotency i "
        "WHERE i.actor_scope=value_actor_user_id::text AND i.operation='ASSESSMENT_START' "
        "AND i.idempotency_key=value_idempotency_key) FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "PERFORM 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_actor_user_id::text "
        "AND i.operation='ASSESSMENT_START' AND i.idempotency_key=value_idempotency_key FOR SHARE; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i JOIN public.health_assessment h ON h.assessment_id=i.target_id "
        "JOIN public.assessment_input_snapshot s ON s.assessment_id=h.assessment_id "
        "WHERE i.actor_scope=value_actor_user_id::text AND i.operation='ASSESSMENT_START' "
        "AND i.idempotency_key=value_idempotency_key AND i.request_digest=value_request_digest "
        "AND octet_length(i.postimage_digest)=32 AND (convert_from(i.response_ciphertext,'UTF8')::jsonb->>'assessment_id')::uuid=h.assessment_id "
        "AND (convert_from(i.response_ciphertext,'UTF8')::jsonb->>'service_case_id')::uuid=h.service_case_id "
        "AND (convert_from(i.response_ciphertext,'UTF8')::jsonb->>'input_snapshot_ref')::uuid=s.snapshot_id "
        "AND (convert_from(i.response_ciphertext,'UTF8')::jsonb->>'sequence_no')::bigint=h.sequence_no "
        "AND (convert_from(i.response_ciphertext,'UTF8')::jsonb->>'initiated_at')::timestamptz=h.initiated_at "
        "AND (SELECT count(*) FROM public.slice5_audit a WHERE a.target_id=h.assessment_id "
        "AND a.action='ASSESSMENT_STARTED' AND octet_length(a.evidence_digest)=32)=1 "
        "AND (SELECT count(*) FROM public.slice5_outbox o WHERE o.aggregate_ref=h.assessment_id "
        "AND o.event_type='ASSESSMENT_RUN_REQUESTED' AND octet_length(o.payload_digest)=32)=1) "
        "THEN RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "RETURN (SELECT convert_from(i.response_ciphertext,'UTF8')::jsonb FROM public.slice5_idempotency i "
        "WHERE i.actor_scope=value_actor_user_id::text AND i.operation='ASSESSMENT_START' "
        "AND i.idempotency_key=value_idempotency_key);",
        (assessment,),
    )
    _function(
        "slice5_assessment_snapshot_write_v1",
        "value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{assessment}' THEN RAISE EXCEPTION 'SLICE5_ASSESSMENT_WRITER_FORBIDDEN'; END IF; "
        "IF jsonb_typeof(value_payload)<>'object' THEN RAISE EXCEPTION 'SLICE5_INVALID_PAYLOAD'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'service_case_id',0)); "
        "IF public.slice5_assessment_start_authority_v1((value_payload->>'service_case_id')::uuid," 
        "(value_payload->>'actor_user_id')::bigint) IS NULL "
        "OR (public.slice5_assessment_start_authority_v1((value_payload->>'service_case_id')::uuid," 
        "(value_payload->>'actor_user_id')::bigint)->>'case_version')::bigint<>(value_payload->>'case_version')::bigint "
        "OR public.slice5_assessment_start_authority_v1((value_payload->>'service_case_id')::uuid," 
        "(value_payload->>'actor_user_id')::bigint)->>'assembly_id'<>value_payload->>'assembly_id' "
        "OR public.slice5_assessment_start_authority_v1((value_payload->>'service_case_id')::uuid," 
        "(value_payload->>'actor_user_id')::bigint)->>'rule_set_version_id'<>value_payload->>'rule_set_version_id' "
        "OR public.slice5_assessment_start_authority_v1((value_payload->>'service_case_id')::uuid,"
        "(value_payload->>'actor_user_id')::bigint)->>'supersedes_assessment_id' "
        "IS DISTINCT FROM NULLIF(value_payload->>'supersedes_assessment_id','') "
        "THEN RAISE EXCEPTION 'ASSEMBLY_STALE'; END IF; "
        "IF EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='ASSESSMENT_START' "
        "AND i.idempotency_key=value_payload->>'idempotency_key') THEN "
        "RETURN public.slice5_assessment_start_replay_v1((value_payload->>'actor_user_id')::bigint,"
        "value_payload->>'idempotency_key',decode(value_payload->>'request_digest','hex')); END IF; "
        "INSERT INTO public.health_assessment(assessment_id,service_case_id,subject_member_id,tenant_id,sequence_no,snapshot_id," 
        "rule_set_version_id,status,supersedes_assessment_id,initiated_by,initiated_at,version) VALUES((value_payload->>'assessment_id')::uuid," 
        "(value_payload->>'service_case_id')::uuid,(value_payload->>'subject_member_id')::uuid,(value_payload->>'tenant_id')::bigint," 
        "(value_payload->>'sequence_no')::bigint," 
        "(value_payload->>'snapshot_id')::uuid,(value_payload->>'rule_set_version_id')::uuid,'DRAFT_SNAPSHOT',"
        "NULLIF(value_payload->>'supersedes_assessment_id','')::uuid," 
        "(value_payload->>'actor_user_id')::bigint,(value_payload->>'created_at')::timestamptz,1); "
        "INSERT INTO public.assessment_input_snapshot(snapshot_id,assessment_id,service_case_id,subject_member_id,tenant_id,assembly_id," 
        "assembly_digest,source_vector_digest,profile_revision_id,consent_version_ids,rule_set_version_id,projection_version," 
        "projection_rule_version,required_max_fact_id,required_max_status_event_seq,source_snapshot,fact_ref_manifest_digest," 
        "snapshot_digest,digest_key_id,created_at) VALUES((value_payload->>'snapshot_id')::uuid,(value_payload->>'assessment_id')::uuid," 
        "(value_payload->>'service_case_id')::uuid,(value_payload->>'subject_member_id')::uuid,(value_payload->>'tenant_id')::bigint," 
        "(value_payload->>'assembly_id')::uuid,decode(value_payload->>'assembly_digest','hex'),decode(value_payload->>'source_vector_digest','hex')," 
        "(value_payload->>'profile_revision_id')::uuid,value_payload->'consent_version_ids',(value_payload->>'rule_set_version_id')::uuid," 
        "(value_payload->>'projection_version')::bigint,value_payload->>'projection_rule_version'," 
        "(value_payload->>'required_max_fact_id')::bigint,(value_payload->>'required_max_status_event_seq')::bigint," 
        "value_payload->>'source_snapshot',decode(value_payload->>'fact_ref_manifest_digest','hex'),decode(value_payload->>'snapshot_digest','hex')," 
        "value_payload->>'digest_key_id',(value_payload->>'created_at')::timestamptz); "
        "INSERT INTO public.slice5_audit VALUES((value_payload->>'audit_id')::uuid,'ASSESSMENT_STARTED'," 
        "(value_payload->>'actor_user_id')::bigint,'therapist','ASSESSMENT',(value_payload->>'assessment_id')::uuid," 
        "decode(value_payload->>'audit_digest','hex'),(value_payload->>'created_at')::timestamptz); "
        "INSERT INTO public.slice5_outbox VALUES((value_payload->>'event_id')::uuid,'ASSESSMENT',(value_payload->>'assessment_id')::uuid," 
        "'ASSESSMENT_RUN_REQUESTED',decode(value_payload->>'outbox_digest','hex'),jsonb_build_object('assessment_id',value_payload->>'assessment_id')," 
        "'PENDING',0,NULL,NULL,(value_payload->>'created_at')::timestamptz,NULL); "
        "INSERT INTO public.slice5_idempotency VALUES((value_payload->>'receipt_id')::uuid,value_payload->>'actor_user_id'," 
        "'ASSESSMENT_START',(value_payload->>'assessment_id')::uuid,value_payload->>'idempotency_key'," 
        "decode(value_payload->>'request_digest','hex'),convert_to((value_payload->'response')::text,'UTF8'),value_payload->>'digest_key_id'," 
        "decode(value_payload->>'postimage_digest','hex'),(value_payload->>'created_at')::timestamptz); "
        "RETURN value_payload->'response';",
        (assessment,),
    )
    _function(
        "slice5_assessment_worker_v1",
        "value_operation VARCHAR,value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'assessment_id',0)); "
        "IF value_operation='CLAIM' THEN UPDATE public.health_assessment SET status='RUNNING',started_at=clock_timestamp(),version=version+1 "
        "WHERE assessment_id=(value_payload->>'assessment_id')::uuid AND status='DRAFT_SNAPSHOT' RETURNING "
        "jsonb_build_object('assessment_id',assessment_id,'version',version) INTO value_payload; RETURN value_payload; "
        "ELSIF value_operation='FAIL' THEN "
        "IF EXISTS(SELECT 1 FROM public.health_assessment h WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND h.status='FAILED') THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.health_assessment h WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND h.failure_code=value_payload->>'failure_code' "
        "AND (SELECT count(*) FROM public.slice5_audit a WHERE a.target_id=h.assessment_id AND a.action='ASSESSMENT_FAILED' "
        "AND a.evidence_digest=decode(value_payload->>'audit_digest','hex'))=1 "
        "AND (SELECT count(*) FROM public.slice5_outbox o WHERE o.aggregate_ref=h.assessment_id AND o.event_type='ASSESSMENT_FAILED' "
        "AND o.payload_digest=decode(value_payload->>'outbox_digest','hex'))=1 "
        "AND (SELECT count(*) FROM public.slice5_idempotency i WHERE i.target_id=h.assessment_id "
        "AND i.operation='ASSESSMENT_FAIL' AND i.request_digest=decode(value_payload->>'request_digest','hex') "
        "AND i.postimage_digest=decode(value_payload->>'postimage_digest','hex'))=1) "
        "THEN RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "RETURN (SELECT convert_from(i.response_ciphertext,'UTF8')::jsonb FROM public.slice5_idempotency i "
        "WHERE i.target_id=(value_payload->>'assessment_id')::uuid AND i.operation='ASSESSMENT_FAIL' LIMIT 1); END IF; "
        "UPDATE public.health_assessment SET status='FAILED',failed_at=(value_payload->>'failed_at')::timestamptz,"
        "failure_code=value_payload->>'failure_code',version=version+1 WHERE assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND status='RUNNING' AND version=(value_payload->>'expected_version')::bigint; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ASSESSMENT_STALE'; END IF; "
        "INSERT INTO public.slice5_audit VALUES((value_payload->>'audit_id')::uuid,'ASSESSMENT_FAILED',0,'workflow_worker',"
        "'ASSESSMENT',(value_payload->>'assessment_id')::uuid,decode(value_payload->>'audit_digest','hex'),"
        "(value_payload->>'failed_at')::timestamptz); "
        "INSERT INTO public.slice5_outbox VALUES((value_payload->>'event_id')::uuid,'ASSESSMENT',"
        "(value_payload->>'assessment_id')::uuid,'ASSESSMENT_FAILED',decode(value_payload->>'outbox_digest','hex'),"
        "jsonb_build_object('assessment_id',value_payload->>'assessment_id','failure_code',value_payload->>'failure_code'),"
        "'PENDING',0,NULL,NULL,(value_payload->>'failed_at')::timestamptz,NULL); "
        "INSERT INTO public.slice5_idempotency VALUES((value_payload->>'receipt_id')::uuid,'workflow_worker','ASSESSMENT_FAIL',"
        "(value_payload->>'assessment_id')::uuid,value_payload->>'assessment_id',decode(value_payload->>'request_digest','hex'),"
        "convert_to((value_payload->'response')::text,'UTF8'),value_payload->>'digest_key_id',"
        "decode(value_payload->>'postimage_digest','hex'),(value_payload->>'failed_at')::timestamptz); "
        "RETURN value_payload->'response'; ELSE RAISE EXCEPTION 'SLICE5_WORKER_OPERATION_INVALID'; END IF;",
        (worker,),
    )
    _function(
        "slice5_assessment_input_v1",
        "value_assessment_id UUID",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "RETURN (SELECT jsonb_build_object('assessment_id',h.assessment_id,'service_case_id',h.service_case_id," 
        "'subject_member_id',h.subject_member_id,'tenant_id',h.tenant_id,'tenant_public_id',ia.tenant_public_id,'assembly_id',s.assembly_id," 
        "'profile_revision_id',s.profile_revision_id,'identity_revision_ref',pr.identity_revision_ref," 
        "'identity_source_version',pr.identity_source_version,'snapshot_created_at',s.created_at," 
        "'profile_ciphertext',encode(pr.payload_ciphertext,'hex'),'profile_key_id',pr.payload_key_id," 
        "'rule_set_code',r.rule_set_code,'supersedes_assessment_id',h.supersedes_assessment_id,"
        "'superseded_assessment_version',old.version,'superseded_dispute_id',old_dispute.dispute_id,"
        "'superseded_dispute_version',old_dispute.version,'facts',COALESCE((SELECT jsonb_agg(jsonb_build_object(" 
        "'indicator_code',f.indicator_code,'fact_ref',f.fact_ref,'value_ciphertext',encode(f.value_ciphertext,'hex')," 
        "'value_key_id',f.value_key_id,'unit',f.unit,'source_type',f.source_type," 
        "'measurement_context',f.measurement_context,'measured_at',f.measured_at) ORDER BY f.indicator_code) "
        "FROM public.assessment_input_assembly_fact f WHERE f.assembly_id=s.assembly_id),'[]'::jsonb)) "
        "FROM public.health_assessment h JOIN public.assessment_input_snapshot s ON s.assessment_id=h.assessment_id "
        "JOIN public.health_profile_revision pr ON pr.profile_revision_id=s.profile_revision_id "
        "JOIN public.assessment_rule_set_version r ON r.rule_set_version_id=h.rule_set_version_id "
        "JOIN public.institution_application ia ON ia.tenant_internal_id=h.tenant_id "
        "LEFT JOIN public.health_assessment old ON old.assessment_id=h.supersedes_assessment_id "
        "LEFT JOIN LATERAL (SELECT d.dispute_id,d.version FROM public.assessment_dispute d "
        "WHERE d.assessment_id=old.assessment_id AND d.status='OPEN' ORDER BY d.created_at DESC LIMIT 1) old_dispute ON true "
        "WHERE h.assessment_id=value_assessment_id AND h.status='RUNNING' FOR SHARE OF h,s,pr,r,ia);",
        (worker,),
    )
    _function(
        "slice5_assessment_complete_v1",
        "value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "IF jsonb_typeof(value_payload->'module_results')<>'array' OR jsonb_array_length(value_payload->'module_results')<>4 "
        "THEN RAISE EXCEPTION 'SLICE5_FOUR_MODULES_REQUIRED'; END IF; "
        "IF (SELECT count(DISTINCT x->>'module_code')=4 AND bool_and(x->>'module_code' IN "
        "('BLOOD_PRESSURE_CARDIOVASCULAR','GLUCOSE_METABOLISM','LIPID_METABOLISM','WEIGHT_ABDOMINAL_OBESITY')) "
        "AND bool_and(x->>'risk_level' IN ('NOT_ASSESSED','WITHIN_RANGE','ATTENTION','HIGH_RISK')) "
        "FROM jsonb_array_elements(value_payload->'module_results') x) IS NOT TRUE "
        "THEN RAISE EXCEPTION 'SLICE5_FOUR_MODULES_REQUIRED'; END IF; "
        "value_payload := value_payload || jsonb_build_object('computed_overall_risk',(SELECT CASE max(CASE x->>'risk_level' "
        "WHEN 'HIGH_RISK' THEN 3 WHEN 'ATTENTION' THEN 2 WHEN 'WITHIN_RANGE' THEN 1 ELSE 0 END) "
        "WHEN 3 THEN 'HIGH_RISK' WHEN 2 THEN 'ATTENTION' WHEN 1 THEN 'WITHIN_RANGE' ELSE 'NOT_ASSESSED' END "
        "FROM jsonb_array_elements(value_payload->'module_results') x)); "
        "IF value_payload ? 'overall_risk' AND value_payload->>'overall_risk'<>value_payload->>'computed_overall_risk' "
        "THEN RAISE EXCEPTION 'SLICE5_OVERALL_RISK_MISMATCH'; END IF; "
        "value_payload := value_payload || jsonb_build_object('overall_risk',value_payload->>'computed_overall_risk'); "
        "IF value_payload->'response' IS DISTINCT FROM jsonb_build_object('assessment_id',value_payload->>'assessment_id',"
        "'status','COMPLETED','overall_risk',value_payload->>'computed_overall_risk',"
        "'version',(value_payload->>'expected_version')::bigint+1) THEN RAISE EXCEPTION 'SLICE5_RESPONSE_MISMATCH'; END IF; "
        "IF ((value_payload->>'overall_risk'='HIGH_RISK')<>(jsonb_array_length(COALESCE(value_payload->'trigger_codes','[]'::jsonb))>0)) "
        "THEN RAISE EXCEPTION 'SLICE5_TRIGGER_SET_MISMATCH'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'assessment_id',0)); "
        "PERFORM 1 FROM public.health_assessment h JOIN public.assessment_input_snapshot s ON s.assessment_id=h.assessment_id "
        "JOIN public.assessment_readiness_case_pointer p ON p.service_case_id=h.service_case_id AND p.current_assembly_id=s.assembly_id "
        "JOIN public.assessment_rule_set_version r ON r.rule_set_version_id=h.rule_set_version_id AND r.status='PUBLISHED' "
        "WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid AND h.status='RUNNING' "
        "AND h.version=(value_payload->>'expected_version')::bigint FOR UPDATE OF h; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ASSESSMENT_STALE'; END IF; "
        "IF EXISTS(SELECT 1 FROM public.health_assessment h WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND h.supersedes_assessment_id IS NOT NULL) THEN "
        "PERFORM 1 FROM public.health_assessment current_assessment "
        "JOIN public.health_assessment old ON old.assessment_id=current_assessment.supersedes_assessment_id "
        "JOIN public.assessment_dispute d ON d.assessment_id=old.assessment_id AND d.status='OPEN' "
        "WHERE current_assessment.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND old.status='UNDER_REVIEW' AND old.version=(value_payload->>'superseded_assessment_version')::bigint "
        "AND d.dispute_id=(value_payload->>'superseded_dispute_id')::uuid "
        "AND d.version=(value_payload->>'superseded_dispute_version')::bigint FOR UPDATE OF old,d; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ASSESSMENT_STALE'; END IF; END IF; "
        "INSERT INTO public.assessment_module_result(module_result_id,assessment_id,module_code,risk_level,reason_codes,evidence_manifest," 
        "message_codes,rule_fragment_digest,created_at) SELECT (x->>'module_result_id')::uuid,(value_payload->>'assessment_id')::uuid," 
        "x->>'module_code',x->>'risk_level',x->'reason_codes',x->'evidence_manifest',x->'message_codes'," 
        "decode(x->>'rule_fragment_digest','hex'),(value_payload->>'completed_at')::timestamptz FROM jsonb_array_elements(value_payload->'module_results') x; "
        "UPDATE public.health_assessment SET status='COMPLETED',overall_risk=value_payload->>'overall_risk'," 
        "completed_at=(value_payload->>'completed_at')::timestamptz,version=version+1 "
        "WHERE assessment_id=(value_payload->>'assessment_id')::uuid; "
        "UPDATE public.health_assessment old SET status='SUPERSEDED',version=old.version+1 "
        "FROM public.health_assessment current_assessment WHERE current_assessment.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND old.assessment_id=current_assessment.supersedes_assessment_id AND old.status='UNDER_REVIEW'; "
        "UPDATE public.assessment_dispute d SET status='SUPERSEDED',superseding_assessment_id=(value_payload->>'assessment_id')::uuid,"
        "resolved_at=(value_payload->>'completed_at')::timestamptz,version=d.version+1 "
        "FROM public.health_assessment current_assessment WHERE current_assessment.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND d.assessment_id=current_assessment.supersedes_assessment_id AND d.status='OPEN'; "
        "IF value_payload->>'overall_risk'='HIGH_RISK' THEN INSERT INTO public.high_risk_task(task_id,assessment_id,service_case_id," 
        "tenant_id,subject_member_id,status,reason_module_codes,trigger_codes,due_at,created_at,version) "
        "SELECT (value_payload->>'task_id')::uuid,h.assessment_id,h.service_case_id,h.tenant_id,h.subject_member_id,'OPEN'," 
        "(SELECT jsonb_agg(x->>'module_code' ORDER BY x->>'module_code') FROM jsonb_array_elements(value_payload->'module_results') x WHERE x->>'risk_level'='HIGH_RISK')," 
        "value_payload->'trigger_codes',(value_payload->>'completed_at')::timestamptz,(value_payload->>'completed_at')::timestamptz,1 "
        "FROM public.health_assessment h WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid; END IF; "
        "INSERT INTO public.slice5_audit VALUES((value_payload->>'audit_id')::uuid,'ASSESSMENT_COMPLETED',0,'workflow_worker','ASSESSMENT'," 
        "(value_payload->>'assessment_id')::uuid,decode(value_payload->>'audit_digest','hex'),(value_payload->>'completed_at')::timestamptz); "
        "INSERT INTO public.slice5_outbox VALUES((value_payload->>'event_id')::uuid,'ASSESSMENT',(value_payload->>'assessment_id')::uuid," 
        "'ASSESSMENT_COMPLETED',decode(value_payload->>'outbox_digest','hex'),jsonb_build_object('assessment_id',value_payload->>'assessment_id'," 
        "'overall_risk',value_payload->>'overall_risk'),'PENDING',0,NULL,NULL,(value_payload->>'completed_at')::timestamptz,NULL); "
        "INSERT INTO public.slice5_idempotency VALUES((value_payload->>'receipt_id')::uuid,'workflow_worker','ASSESSMENT_COMPLETE'," 
        "(value_payload->>'assessment_id')::uuid,value_payload->>'assessment_id',decode(value_payload->>'request_digest','hex')," 
        "convert_to((value_payload->'response')::text,'UTF8'),value_payload->>'digest_key_id',decode(value_payload->>'postimage_digest','hex')," 
        "(value_payload->>'completed_at')::timestamptz); RETURN value_payload->'response';",
        (worker,),
    )
    _function(
        "slice5_assessment_confirm_v1",
        "value_assessment_id UUID",
        "JSONB",
        f"IF session_user NOT IN ('{assessment}','{worker}') THEN RAISE EXCEPTION 'SLICE5_CONFIRM_FORBIDDEN'; END IF; "
        "IF EXISTS(SELECT 1 FROM public.health_assessment h WHERE h.assessment_id=value_assessment_id AND h.status='UNDER_REVIEW') THEN "
        "RETURN (SELECT jsonb_build_object('assessment',jsonb_build_object('assessment_id',h.assessment_id,'status',h.status,'version',h.version),"
        "'dispute',(SELECT jsonb_build_object('dispute_id',d.dispute_id,'assessment_id',d.assessment_id,'raised_by',d.raised_by,"
        "'actor_context',d.actor_context,'reason_code',d.reason_code,'status',d.status,"
        "'created_at_us',(extract(epoch from d.created_at)*1000000)::bigint,'version',d.version) FROM public.assessment_dispute d "
        "WHERE d.assessment_id=h.assessment_id AND d.status='OPEN' ORDER BY d.created_at DESC LIMIT 1),"
        "'audit',(SELECT jsonb_build_object('audit_id',a.audit_id,'action',a.action,'actor_user_id',a.actor_user_id,"
        "'actor_role',a.actor_role,'target_type',a.target_type,'target_id',a.target_id,'evidence_digest',encode(a.evidence_digest,'hex'),"
        "'occurred_at_us',(extract(epoch from a.occurred_at)*1000000)::bigint) FROM public.slice5_audit a "
        "WHERE a.target_id=h.assessment_id AND a.action='ASSESSMENT_DISPUTED'),"
        "'outbox',(SELECT jsonb_build_object('event_id',o.event_id,'aggregate_type',o.aggregate_type,'aggregate_ref',o.aggregate_ref,"
        "'event_type',o.event_type,'payload_digest',encode(o.payload_digest,'hex'),'status',o.status,'attempts',o.attempts,"
        "'created_at_us',(extract(epoch from o.created_at)*1000000)::bigint) FROM public.slice5_outbox o "
        "WHERE o.aggregate_ref=h.assessment_id AND o.event_type='ASSESSMENT_DISPUTED'),"
        "'receipt',(SELECT jsonb_build_object('receipt_id',i.receipt_id,'actor_scope',i.actor_scope,'operation',i.operation,"
        "'target_id',i.target_id,'idempotency_key',i.idempotency_key,'request_digest',encode(i.request_digest,'hex'),"
        "'response',convert_from(i.response_ciphertext,'UTF8')::jsonb,'response_key_id',i.response_key_id,"
        "'postimage_digest',encode(i.postimage_digest,'hex'),'created_at_us',(extract(epoch from i.created_at)*1000000)::bigint) "
        "FROM public.slice5_idempotency i WHERE i.target_id=h.assessment_id AND i.operation='ASSESSMENT_DISPUTE')) "
        "FROM public.health_assessment h WHERE h.assessment_id=value_assessment_id); END IF; "
        "IF EXISTS(SELECT 1 FROM public.health_assessment h WHERE h.assessment_id=value_assessment_id AND h.status='FAILED') THEN "
        "RETURN (SELECT jsonb_build_object('assessment',jsonb_build_object('assessment_id',h.assessment_id,'status',h.status,"
        "'failure_code',h.failure_code,'failed_at_us',(extract(epoch from h.failed_at)*1000000)::bigint,'version',h.version),"
        "'audit',(SELECT jsonb_build_object('audit_id',a.audit_id,'action',a.action,'actor_user_id',a.actor_user_id,"
        "'actor_role',a.actor_role,'target_type',a.target_type,'target_id',a.target_id,'evidence_digest',encode(a.evidence_digest,'hex'),"
        "'occurred_at_us',(extract(epoch from a.occurred_at)*1000000)::bigint) FROM public.slice5_audit a "
        "WHERE a.target_id=h.assessment_id AND a.action='ASSESSMENT_FAILED'),"
        "'outbox',(SELECT jsonb_build_object('event_id',o.event_id,'aggregate_type',o.aggregate_type,'aggregate_ref',o.aggregate_ref,"
        "'event_type',o.event_type,'payload_digest',encode(o.payload_digest,'hex'),'status',o.status,'attempts',o.attempts,"
        "'created_at_us',(extract(epoch from o.created_at)*1000000)::bigint) FROM public.slice5_outbox o "
        "WHERE o.aggregate_ref=h.assessment_id AND o.event_type='ASSESSMENT_FAILED'),"
        "'receipt',(SELECT jsonb_build_object('receipt_id',i.receipt_id,'actor_scope',i.actor_scope,'operation',i.operation,"
        "'target_id',i.target_id,'idempotency_key',i.idempotency_key,'request_digest',encode(i.request_digest,'hex'),"
        "'response',convert_from(i.response_ciphertext,'UTF8')::jsonb,'response_key_id',i.response_key_id,"
        "'postimage_digest',encode(i.postimage_digest,'hex'),'created_at_us',(extract(epoch from i.created_at)*1000000)::bigint) "
        "FROM public.slice5_idempotency i WHERE i.target_id=h.assessment_id AND i.operation='ASSESSMENT_FAIL')) "
        "FROM public.health_assessment h WHERE h.assessment_id=value_assessment_id); END IF; "
        "IF EXISTS(SELECT 1 FROM public.health_assessment h WHERE h.assessment_id=value_assessment_id AND h.status='COMPLETED') THEN "
        "RETURN (SELECT jsonb_build_object('assessment',jsonb_build_object('assessment_id',h.assessment_id,'status',h.status,"
        "'overall_risk',h.overall_risk,'completed_at_us',(extract(epoch from h.completed_at)*1000000)::bigint,'version',h.version),"
        "'module_results',COALESCE((SELECT jsonb_agg(jsonb_build_object('module_result_id',m.module_result_id,"
        "'assessment_id',m.assessment_id,'module_code',m.module_code,'risk_level',m.risk_level,'reason_codes',m.reason_codes,"
        "'evidence_manifest',m.evidence_manifest,'message_codes',m.message_codes,'rule_fragment_digest',encode(m.rule_fragment_digest,'hex'),"
        "'created_at_us',(extract(epoch from m.created_at)*1000000)::bigint) ORDER BY m.module_code) "
        "FROM public.assessment_module_result m WHERE m.assessment_id=h.assessment_id),'[]'::jsonb),"
        "'high_risk_task',(SELECT jsonb_build_object('task_id',t.task_id,'assessment_id',t.assessment_id,"
        "'service_case_id',t.service_case_id,'tenant_id',t.tenant_id,'subject_member_id',t.subject_member_id,'status',t.status,"
        "'reason_module_codes',t.reason_module_codes,'trigger_codes',t.trigger_codes,'assigned_actor_id',t.assigned_actor_id,"
        "'due_at_us',(extract(epoch from t.due_at)*1000000)::bigint,'claimed_at_us',CASE WHEN t.claimed_at IS NULL THEN NULL ELSE "
        "(extract(epoch from t.claimed_at)*1000000)::bigint END,'closed_at_us',CASE WHEN t.closed_at IS NULL THEN NULL ELSE "
        "(extract(epoch from t.closed_at)*1000000)::bigint END,'close_reason_code',t.close_reason_code,"
        "'created_at_us',(extract(epoch from t.created_at)*1000000)::bigint,'version',t.version) "
        "FROM public.high_risk_task t WHERE t.assessment_id=h.assessment_id),"
        "'superseded_assessment',(SELECT jsonb_build_object('assessment_id',old.assessment_id,'status',old.status,'version',old.version) "
        "FROM public.health_assessment old WHERE old.assessment_id=h.supersedes_assessment_id),"
        "'superseded_dispute',(SELECT jsonb_build_object('dispute_id',d.dispute_id,'assessment_id',d.assessment_id,"
        "'status',d.status,'superseding_assessment_id',d.superseding_assessment_id,"
        "'resolved_at_us',(extract(epoch from d.resolved_at)*1000000)::bigint,'version',d.version) "
        "FROM public.assessment_dispute d WHERE d.assessment_id=h.supersedes_assessment_id AND d.status='SUPERSEDED' "
        "ORDER BY d.resolved_at DESC LIMIT 1),"
        "'audit',(SELECT jsonb_build_object('audit_id',a.audit_id,'action',a.action,'actor_user_id',a.actor_user_id,"
        "'actor_role',a.actor_role,'target_type',a.target_type,'target_id',a.target_id,'evidence_digest',encode(a.evidence_digest,'hex'),"
        "'occurred_at_us',(extract(epoch from a.occurred_at)*1000000)::bigint) FROM public.slice5_audit a "
        "WHERE a.target_id=h.assessment_id AND a.action='ASSESSMENT_COMPLETED'),"
        "'outbox',(SELECT jsonb_build_object('event_id',o.event_id,'aggregate_type',o.aggregate_type,'aggregate_ref',o.aggregate_ref,"
        "'event_type',o.event_type,'payload_digest',encode(o.payload_digest,'hex'),'status',o.status,'attempts',o.attempts,"
        "'created_at_us',(extract(epoch from o.created_at)*1000000)::bigint) FROM public.slice5_outbox o "
        "WHERE o.aggregate_ref=h.assessment_id AND o.event_type='ASSESSMENT_COMPLETED'),"
        "'receipt',(SELECT jsonb_build_object('receipt_id',i.receipt_id,'actor_scope',i.actor_scope,'operation',i.operation,"
        "'target_id',i.target_id,'idempotency_key',i.idempotency_key,'request_digest',encode(i.request_digest,'hex'),"
        "'response',convert_from(i.response_ciphertext,'UTF8')::jsonb,'response_key_id',i.response_key_id,"
        "'postimage_digest',encode(i.postimage_digest,'hex'),'created_at_us',(extract(epoch from i.created_at)*1000000)::bigint) "
        "FROM public.slice5_idempotency i WHERE i.target_id=h.assessment_id AND i.operation='ASSESSMENT_COMPLETE')) "
        "FROM public.health_assessment h WHERE h.assessment_id=value_assessment_id); END IF; "
        "RETURN (SELECT jsonb_build_object(" 
        "'assessment',jsonb_build_object('assessment_id',h.assessment_id,'service_case_id',h.service_case_id," 
        "'subject_member_id',h.subject_member_id,'tenant_id',h.tenant_id,'sequence_no',h.sequence_no," 
        "'snapshot_id',h.snapshot_id,'rule_set_version_id',h.rule_set_version_id,'status',h.status," 
        "'supersedes_assessment_id',h.supersedes_assessment_id,'initiated_by',h.initiated_by,"
        "'initiated_at_us',(extract(epoch from h.initiated_at)*1000000)::bigint,'version',h.version)," 
        "'snapshot',jsonb_build_object('snapshot_id',s.snapshot_id,'assessment_id',s.assessment_id," 
        "'service_case_id',s.service_case_id,'subject_member_id',s.subject_member_id,'tenant_id',s.tenant_id," 
        "'assembly_id',s.assembly_id,'assembly_digest',encode(s.assembly_digest,'hex')," 
        "'source_vector_digest',encode(s.source_vector_digest,'hex'),'profile_revision_id',s.profile_revision_id," 
        "'consent_version_ids',s.consent_version_ids,'rule_set_version_id',s.rule_set_version_id," 
        "'projection_version',s.projection_version,'projection_rule_version',s.projection_rule_version," 
        "'required_max_fact_id',s.required_max_fact_id,'required_max_status_event_seq',s.required_max_status_event_seq," 
        "'source_snapshot',s.source_snapshot,'fact_ref_manifest_digest',encode(s.fact_ref_manifest_digest,'hex')," 
        "'snapshot_digest',encode(s.snapshot_digest,'hex'),'digest_key_id',s.digest_key_id," 
        "'created_at_us',(extract(epoch from s.created_at)*1000000)::bigint)," 
        "'module_results',COALESCE((SELECT jsonb_agg(to_jsonb(m) ORDER BY m.module_code) FROM public.assessment_module_result m "
        "WHERE m.assessment_id=h.assessment_id),'[]'::jsonb)," 
        "'high_risk_task',(SELECT to_jsonb(t) FROM public.high_risk_task t WHERE t.assessment_id=h.assessment_id)," 
        "'audit',(SELECT jsonb_build_object('audit_id',a.audit_id,'action',a.action,'actor_user_id',a.actor_user_id," 
        "'actor_role',a.actor_role,'target_type',a.target_type,'target_id',a.target_id," 
        "'evidence_digest',encode(a.evidence_digest,'hex'),'occurred_at_us',(extract(epoch from a.occurred_at)*1000000)::bigint) "
        "FROM public.slice5_audit a WHERE a.target_id=h.assessment_id AND a.action='ASSESSMENT_STARTED')," 
        "'outbox',(SELECT jsonb_build_object('event_id',o.event_id,'aggregate_type',o.aggregate_type," 
        "'aggregate_ref',o.aggregate_ref,'event_type',o.event_type,'payload_digest',encode(o.payload_digest,'hex')," 
        "'status',o.status,'attempts',o.attempts,'created_at_us',(extract(epoch from o.created_at)*1000000)::bigint) "
        "FROM public.slice5_outbox o WHERE o.aggregate_ref=h.assessment_id AND o.event_type='ASSESSMENT_RUN_REQUESTED')," 
        "'receipt',(SELECT jsonb_build_object('receipt_id',i.receipt_id,'actor_scope',i.actor_scope," 
        "'operation',i.operation,'target_id',i.target_id,'idempotency_key',i.idempotency_key," 
        "'request_digest',encode(i.request_digest,'hex'),'response',convert_from(i.response_ciphertext,'UTF8')::jsonb," 
        "'response_key_id',i.response_key_id,'postimage_digest',encode(i.postimage_digest,'hex')," 
        "'created_at_us',(extract(epoch from i.created_at)*1000000)::bigint) FROM public.slice5_idempotency i "
        "WHERE i.target_id=h.assessment_id AND i.operation='ASSESSMENT_START')) "
        "FROM public.health_assessment h JOIN public.assessment_input_snapshot s ON s.assessment_id=h.assessment_id "
        "WHERE h.assessment_id=value_assessment_id);",
        (assessment, worker),
    )
    _function(
        "slice5_actor_read_authority_v1",
        "value_actor_user_id BIGINT,value_actor_role VARCHAR,value_service_case_id UUID,value_subject_member_id UUID,value_tenant_id BIGINT",
        "BOOLEAN",
        f"IF session_user NOT IN ('{clinical}','{oversight}') "
        "THEN RAISE EXCEPTION 'SLICE5_READ_AUTHORITY_FORBIDDEN'; END IF; "
        "IF value_actor_role='therapist' THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u "
        "JOIN public.therapist_profile t ON t.user_id=u.id JOIN public.service_case c ON c.primary_therapist_id=t.therapist_id "
        "WHERE u.id=value_actor_user_id AND u.status='active' AND u.role='therapist' AND c.case_id=value_service_case_id "
        "AND c.subject_member_id=value_subject_member_id AND c.tenant_id=value_tenant_id AND c.status='PREPARING' "
        "AND t.status='APPROVED_ACTIVE'); "
        "ELSIF value_actor_role IN ('org_admin','org_operator') THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u "
        "JOIN public.service_case c ON c.tenant_id=u.tenant_id WHERE u.id=value_actor_user_id AND u.status='active' "
        "AND u.role::text=value_actor_role AND c.case_id=value_service_case_id AND c.subject_member_id=value_subject_member_id "
        "AND c.tenant_id=value_tenant_id AND c.status='PREPARING'); "
        "ELSIF value_actor_role='member' THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u "
        "JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN public.service_case c ON c.subject_member_id=value_subject_member_id "
        "WHERE u.id=value_actor_user_id AND u.status='active' AND u.role='member' AND u.tenant_id IS NULL "
        "AND c.case_id=value_service_case_id AND c.tenant_id=value_tenant_id AND (l.member_id=value_subject_member_id OR EXISTS(" 
        "SELECT 1 FROM public.proxy_grant g JOIN public.service_enrollment e ON e.enrollment_id=g.enrollment_id "
        "WHERE g.proxy_member_id=l.member_id AND e.subject_member_id=value_subject_member_id AND e.service_case_id=value_service_case_id "
        "AND g.status='ACTIVE' AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) "
        "AND g.permission_codes ? 'DAILY_VIEW'))); "
        "ELSIF value_actor_role IN ('super_admin','sys_admin','expert') THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u "
        "WHERE u.id=value_actor_user_id AND u.status='active' AND u.role::text=value_actor_role); "
        "END IF; RETURN FALSE;",
        (clinical, oversight),
    )
    _function(
        "slice5_family_subject_authority_v1",
        "value_actor_user_id BIGINT,value_enrollment_id UUID",
        "JSONB",
        f"IF session_user<>'{clinical}' THEN "
        "RAISE EXCEPTION 'SLICE5_FAMILY_SUBJECT_AUTHORITY_FORBIDDEN'; END IF; "
        "IF value_enrollment_id IS NULL THEN "
        "RETURN (SELECT jsonb_build_object('subject_member_id',l.member_id) "
        "FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id "
        "JOIN identity.member m ON m.member_id=l.member_id "
        "WHERE u.id=value_actor_user_id AND u.status='active' AND u.role='member' "
        "AND u.tenant_id IS NULL AND m.status='created' FOR SHARE OF u,l,m); "
        "END IF; "
        "RETURN (SELECT jsonb_build_object('subject_member_id',e.subject_member_id) "
        "FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id "
        "JOIN identity.member m ON m.member_id=l.member_id "
        "JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id "
        "JOIN public.service_enrollment e ON e.enrollment_id=g.enrollment_id "
        "JOIN public.service_case c ON c.enrollment_id=e.enrollment_id "
        "AND c.subject_member_id=e.subject_member_id "
        "WHERE u.id=value_actor_user_id AND u.status='active' AND u.role='member' "
        "AND u.tenant_id IS NULL AND m.status='created' "
        "AND e.enrollment_id=value_enrollment_id AND e.status='CASE_CREATED' "
        "AND g.status='ACTIVE' AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) "
        "AND g.permission_codes ? 'DAILY_VIEW' AND c.status='PREPARING' "
        "FOR SHARE OF u,l,m,g,e,c);",
        (clinical,),
    )
    _function(
        "slice5_assessment_dispute_v1",
        "value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{assessment}' THEN RAISE EXCEPTION 'SLICE5_ASSESSMENT_WRITER_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.\"user\" u WHERE u.id=(value_payload->>'actor_user_id')::bigint AND u.status='active' FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ACTOR_CURRENTNESS_FORBIDDEN'; END IF; "
        "IF value_payload->>'actor_context'='THERAPIST' THEN PERFORM 1 FROM public.health_assessment h "
        "JOIN public.service_case c ON c.case_id=h.service_case_id JOIN public.therapist_profile p ON p.therapist_id=c.primary_therapist_id "
        "JOIN public.\"user\" u ON u.id=p.user_id WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND u.id=(value_payload->>'actor_user_id')::bigint AND u.role='therapist' AND p.status='APPROVED_ACTIVE' FOR SHARE OF c,p; "
        "ELSIF value_payload->>'actor_context'='SELF' THEN PERFORM 1 FROM public.health_assessment h "
        "JOIN identity.user_member_self_link l ON l.member_id=h.subject_member_id JOIN public.\"user\" u ON u.id=l.user_ref "
        "WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid AND u.id=(value_payload->>'actor_user_id')::bigint "
        "AND u.role='member' FOR SHARE OF l; ELSE RAISE EXCEPTION 'ACTOR_CURRENTNESS_FORBIDDEN'; END IF; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ACTOR_CURRENTNESS_FORBIDDEN'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'assessment_id',0)); "
        "IF EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='ASSESSMENT_DISPUTE' AND i.target_id=(value_payload->>'assessment_id')::uuid "
        "AND i.idempotency_key=value_payload->>'idempotency_key') THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='ASSESSMENT_DISPUTE' AND i.target_id=(value_payload->>'assessment_id')::uuid "
        "AND i.idempotency_key=value_payload->>'idempotency_key' AND i.request_digest=decode(value_payload->>'request_digest','hex')) "
        "THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i JOIN public.assessment_dispute d ON d.dispute_id=(value_payload->>'dispute_id')::uuid "
        "JOIN public.health_assessment h ON h.assessment_id=d.assessment_id WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='ASSESSMENT_DISPUTE' AND i.target_id=h.assessment_id AND i.idempotency_key=value_payload->>'idempotency_key' "
        "AND i.postimage_digest=decode(value_payload->>'postimage_digest','hex') AND h.status='UNDER_REVIEW' "
        "AND (SELECT count(*) FROM public.slice5_audit a WHERE a.target_id=h.assessment_id AND a.action='ASSESSMENT_DISPUTED' "
        "AND a.evidence_digest=decode(value_payload->>'evidence_digest','hex'))=1 "
        "AND (SELECT count(*) FROM public.slice5_outbox o WHERE o.aggregate_ref=h.assessment_id AND o.event_type='ASSESSMENT_DISPUTED' "
        "AND o.payload_digest=decode(value_payload->>'outbox_digest','hex'))=1) "
        "THEN RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "RETURN (SELECT convert_from(i.response_ciphertext,'UTF8')::jsonb FROM public.slice5_idempotency i "
        "WHERE i.actor_scope=value_payload->>'actor_user_id' AND i.operation='ASSESSMENT_DISPUTE' "
        "AND i.target_id=(value_payload->>'assessment_id')::uuid AND i.idempotency_key=value_payload->>'idempotency_key'); END IF; "
        "PERFORM 1 FROM public.health_assessment h WHERE h.assessment_id=(value_payload->>'assessment_id')::uuid "
        "AND h.status='COMPLETED' AND h.version=(value_payload->>'expected_version')::bigint FOR UPDATE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'VERSION_CONFLICT'; END IF; "
        "INSERT INTO public.assessment_dispute VALUES((value_payload->>'dispute_id')::uuid,(value_payload->>'assessment_id')::uuid," 
        "(value_payload->>'actor_user_id')::bigint,value_payload->>'actor_context',value_payload->>'reason_code'," 
        "CASE WHEN value_payload ? 'statement_ciphertext' THEN decode(value_payload->>'statement_ciphertext','hex') ELSE NULL END," 
        "value_payload->>'statement_key_id','OPEN',NULL,NULL,(value_payload->>'created_at')::timestamptz,NULL,1); "
        "UPDATE public.health_assessment SET status='UNDER_REVIEW',version=version+1 "
        "WHERE assessment_id=(value_payload->>'assessment_id')::uuid; "
        "INSERT INTO public.slice5_audit VALUES((value_payload->>'audit_id')::uuid,'ASSESSMENT_DISPUTED'," 
        "(value_payload->>'actor_user_id')::bigint,value_payload->>'actor_role','ASSESSMENT'," 
        "(value_payload->>'assessment_id')::uuid,decode(value_payload->>'evidence_digest','hex'),(value_payload->>'created_at')::timestamptz); "
        "INSERT INTO public.slice5_outbox VALUES((value_payload->>'event_id')::uuid,'ASSESSMENT',(value_payload->>'assessment_id')::uuid," 
        "'ASSESSMENT_DISPUTED',decode(value_payload->>'outbox_digest','hex'),jsonb_build_object('assessment_id',value_payload->>'assessment_id'," 
        "'reason_code',value_payload->>'reason_code'),'PENDING',0,NULL,NULL,(value_payload->>'created_at')::timestamptz,NULL); "
        "INSERT INTO public.slice5_idempotency VALUES((value_payload->>'receipt_id')::uuid,value_payload->>'actor_user_id','ASSESSMENT_DISPUTE'," 
        "(value_payload->>'assessment_id')::uuid,value_payload->>'idempotency_key',decode(value_payload->>'request_digest','hex')," 
        "convert_to((value_payload->'response')::text,'UTF8'),value_payload->>'digest_key_id',decode(value_payload->>'postimage_digest','hex')," 
        "(value_payload->>'created_at')::timestamptz); "
        "RETURN value_payload->'response';",
        (assessment,),
    )
    _function(
        "slice5_high_risk_transition_v1",
        "value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{risk}' THEN RAISE EXCEPTION 'SLICE5_RISK_WRITER_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.\"user\" u WHERE u.id=(value_payload->>'actor_user_id')::bigint AND u.status='active' FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ACTOR_CURRENTNESS_FORBIDDEN'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'task_id',0)); "
        "PERFORM 1 FROM public.high_risk_task t WHERE t.task_id=(value_payload->>'task_id')::uuid "
        "FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'HIGH_RISK_TASK_NOT_FOUND'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.high_risk_task t JOIN public.health_assessment h ON h.assessment_id=t.assessment_id "
        "JOIN public.service_case c ON c.case_id=t.service_case_id JOIN public.\"user\" u ON u.id=(value_payload->>'actor_user_id')::bigint "
        "LEFT JOIN public.therapist_profile p ON p.user_id=u.id WHERE t.task_id=(value_payload->>'task_id')::uuid AND ("
        "(value_payload->>'actor_role'='therapist' AND u.role='therapist' AND c.primary_therapist_id=p.therapist_id AND p.status='APPROVED_ACTIVE') OR "
        "(value_payload->>'actor_role' IN ('org_admin','org_operator') AND u.role::text=value_payload->>'actor_role' AND u.tenant_id=t.tenant_id))) "
        "THEN RAISE EXCEPTION 'ACTOR_CURRENTNESS_FORBIDDEN'; END IF; "
        "IF EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='HIGH_RISK_ACTION' AND i.target_id=(value_payload->>'task_id')::uuid "
        "AND i.idempotency_key=value_payload->>'idempotency_key') THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i JOIN public.high_risk_task_action a ON a.action_id=(value_payload->>'action_id')::uuid "
        "WHERE i.actor_scope=value_payload->>'actor_user_id' AND i.operation='HIGH_RISK_ACTION' "
        "AND i.target_id=(value_payload->>'task_id')::uuid AND i.idempotency_key=value_payload->>'idempotency_key' "
        "AND i.request_digest=decode(value_payload->>'request_digest','hex') "
        "AND i.postimage_digest=decode(value_payload->>'postimage_digest','hex') "
        "AND a.task_id=i.target_id AND a.evidence_digest=decode(value_payload->>'evidence_digest','hex') "
        "AND (SELECT count(*) FROM public.slice5_audit z WHERE z.audit_id=(value_payload->>'audit_id')::uuid "
        "AND z.target_id=i.target_id AND z.evidence_digest=decode(value_payload->>'evidence_digest','hex'))=1 "
        "AND (SELECT count(*) FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "AND o.aggregate_ref=i.target_id AND o.payload_digest=decode(value_payload->>'outbox_digest','hex'))=1) "
        "THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; "
        "RETURN (SELECT convert_from(i.response_ciphertext,'UTF8')::jsonb FROM public.slice5_idempotency i "
        "WHERE i.actor_scope=value_payload->>'actor_user_id' AND i.operation='HIGH_RISK_ACTION' "
        "AND i.target_id=(value_payload->>'task_id')::uuid AND i.idempotency_key=value_payload->>'idempotency_key'); END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.high_risk_task t WHERE t.task_id=(value_payload->>'task_id')::uuid "
        "AND t.version=(value_payload->>'expected_version')::bigint) THEN RAISE EXCEPTION 'VERSION_CONFLICT'; END IF; "
        "value_payload := value_payload || jsonb_build_object(" 
        "'from_status',(SELECT status FROM public.high_risk_task WHERE task_id=(value_payload->>'task_id')::uuid)," 
        "'to_status',CASE value_payload->>'action_code' WHEN 'CLAIM' THEN 'CLAIMED' WHEN 'ESCALATE' THEN 'ESCALATED' "
        "WHEN 'REFER' THEN 'REFERRED' WHEN 'RESOLVE' THEN 'RESOLVED' ELSE NULL END); "
        "IF NOT ((value_payload->>'from_status'='OPEN' AND value_payload->>'to_status' IN ('CLAIMED','REFERRED','RESOLVED')) "
        "OR (value_payload->>'from_status'='CLAIMED' AND value_payload->>'to_status' IN ('ESCALATED','REFERRED','RESOLVED')) "
        "OR (value_payload->>'from_status'='ESCALATED' AND value_payload->>'to_status' IN ('REFERRED','RESOLVED'))) "
        "THEN RAISE EXCEPTION 'INVALID_TASK_TRANSITION'; END IF; "
        "IF value_payload->>'to_status' IN ('REFERRED','RESOLVED') AND (value_payload->>'contact_outcome_code' IS NULL "
        "OR value_payload->>'advice_code' IS NULL OR value_payload->>'reason_code' IS NULL) "
        "THEN RAISE EXCEPTION 'HIGH_RISK_ACTION_INVALID'; END IF; "
        "INSERT INTO public.high_risk_task_action VALUES((value_payload->>'action_id')::uuid,(value_payload->>'task_id')::uuid," 
        "value_payload->>'from_status',value_payload->>'to_status',(value_payload->>'actor_user_id')::bigint,value_payload->>'actor_role'," 
        "value_payload->>'action_code',value_payload->>'contact_outcome_code',value_payload->>'advice_code',value_payload->>'reason_code'," 
        "(value_payload->>'occurred_at')::timestamptz,decode(value_payload->>'evidence_digest','hex')); "
        "UPDATE public.high_risk_task SET status=value_payload->>'to_status',assigned_actor_id=CASE WHEN value_payload->>'to_status'='CLAIMED' "
        "THEN (value_payload->>'actor_user_id')::bigint ELSE assigned_actor_id END,claimed_at=CASE WHEN value_payload->>'to_status'='CLAIMED' "
        "THEN (value_payload->>'occurred_at')::timestamptz ELSE claimed_at END,closed_at=CASE WHEN value_payload->>'to_status' IN ('REFERRED','RESOLVED') "
        "THEN (value_payload->>'occurred_at')::timestamptz ELSE NULL END,close_reason_code=CASE WHEN value_payload->>'to_status' IN ('REFERRED','RESOLVED') "
        "THEN value_payload->>'reason_code' ELSE NULL END,version=version+1 WHERE task_id=(value_payload->>'task_id')::uuid; "
        "INSERT INTO public.slice5_audit VALUES((value_payload->>'audit_id')::uuid,'HIGH_RISK_TASK_TRANSITIONED',"
        "(value_payload->>'actor_user_id')::bigint,value_payload->>'actor_role','HIGH_RISK_TASK',(value_payload->>'task_id')::uuid,"
        "decode(value_payload->>'evidence_digest','hex'),(value_payload->>'occurred_at')::timestamptz); "
        "INSERT INTO public.slice5_outbox VALUES((value_payload->>'event_id')::uuid,'HIGH_RISK_TASK',(value_payload->>'task_id')::uuid,"
        "'HIGH_RISK_TASK_TRANSITIONED',decode(value_payload->>'outbox_digest','hex'),jsonb_build_object('task_id',value_payload->>'task_id',"
        "'status',value_payload->>'to_status'),'PENDING',0,NULL,NULL,(value_payload->>'occurred_at')::timestamptz,NULL); "
        "INSERT INTO public.slice5_idempotency VALUES((value_payload->>'receipt_id')::uuid,value_payload->>'actor_user_id','HIGH_RISK_ACTION',"
        "(value_payload->>'task_id')::uuid,value_payload->>'idempotency_key',decode(value_payload->>'request_digest','hex'),"
        "convert_to((value_payload->'response')::text,'UTF8'),value_payload->>'digest_key_id',"
        "decode(value_payload->>'postimage_digest','hex'),(value_payload->>'occurred_at')::timestamptz); RETURN value_payload->'response';",
        (risk,),
    )
    _function(
        "slice5_rule_governance_v1",
        "value_operation VARCHAR,value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{rule}' THEN RAISE EXCEPTION 'SLICE5_RULE_WRITER_FORBIDDEN'; END IF; "
        "IF value_payload->>'rule_set_code'<>'CN_ADULT_BASELINE_V1' THEN RAISE EXCEPTION 'RULE_SET_INVALID'; END IF; "
        "PERFORM 1 FROM public.\"user\" u WHERE u.id=(value_payload->>'actor_user_id')::bigint AND u.status='active' "
        "AND ((value_operation IN ('CREATE','SUBMIT','REVIEW_APPROVE','REVIEW_CORRECTION') AND u.role='expert') "
        "OR (value_operation IN ('PUBLISH','SUSPEND','RESUME','RETIRE') AND u.role IN ('sys_admin','super_admin'))) FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'RULE_GOVERNANCE_FORBIDDEN'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'rule_set_version_id',0)); "
        "IF EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='RULE_'||value_operation AND i.target_id=(value_payload->>'rule_set_version_id')::uuid "
        "AND i.idempotency_key=value_payload->>'idempotency_key') THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_idempotency i WHERE i.actor_scope=value_payload->>'actor_user_id' "
        "AND i.operation='RULE_'||value_operation AND i.target_id=(value_payload->>'rule_set_version_id')::uuid "
        "AND i.idempotency_key=value_payload->>'idempotency_key' AND i.request_digest=decode(value_payload->>'request_digest','hex') "
        "AND (SELECT count(*) FROM public.slice5_audit a WHERE a.audit_id=(value_payload->>'audit_id')::uuid "
        "AND a.target_id=i.target_id AND a.evidence_digest=decode(value_payload->>'evidence_digest','hex'))=1 "
        "AND (SELECT count(*) FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "AND o.aggregate_ref=i.target_id AND o.payload_digest=decode(value_payload->>'outbox_digest','hex'))=1) "
        "THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; "
        "RETURN (SELECT convert_from(i.response_ciphertext,'UTF8')::jsonb FROM public.slice5_idempotency i "
        "WHERE i.actor_scope=value_payload->>'actor_user_id' AND i.operation='RULE_'||value_operation "
        "AND i.target_id=(value_payload->>'rule_set_version_id')::uuid AND i.idempotency_key=value_payload->>'idempotency_key'); END IF; "
        "IF value_operation='CREATE' THEN INSERT INTO public.assessment_rule_set_version VALUES(" 
        "(value_payload->>'rule_set_version_id')::uuid,'CN_ADULT_BASELINE_V1',(value_payload->>'version_no')::bigint,'DRAFT'," 
        "value_payload->'typed_rule_payload',decode(value_payload->>'content_digest','hex'),'SHA256_V1'," 
        "(value_payload->>'actor_user_id')::bigint,NULL,value_payload->>'approval_evidence_ref',NULL,NULL,NULL," 
        "(value_payload->>'created_at')::timestamptz,1); "
        "ELSIF value_operation='SUBMIT' THEN UPDATE public.assessment_rule_set_version SET status='IN_REVIEW',version=version+1 "
        "WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid AND status IN ('DRAFT','NEEDS_CORRECTION') "
        "AND author_user_id=(value_payload->>'actor_user_id')::bigint AND version=(value_payload->>'expected_version')::bigint; "
        "ELSIF value_operation='REVIEW_APPROVE' THEN UPDATE public.assessment_rule_set_version SET reviewer_user_id=(value_payload->>'actor_user_id')::bigint," 
        "version=version+1 WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid "
        "AND status='IN_REVIEW' AND author_user_id <> (value_payload->>'actor_user_id')::bigint "
        "AND approval_evidence_ref IS NOT NULL AND version=(value_payload->>'expected_version')::bigint; "
        "ELSIF value_operation='REVIEW_CORRECTION' THEN UPDATE public.assessment_rule_set_version SET status='NEEDS_CORRECTION',"
        "reviewer_user_id=(value_payload->>'actor_user_id')::bigint,version=version+1 "
        "WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid AND status='IN_REVIEW' "
        "AND author_user_id<>(value_payload->>'actor_user_id')::bigint AND version=(value_payload->>'expected_version')::bigint; "
        "ELSIF value_operation='PUBLISH' THEN UPDATE public.assessment_rule_set_version SET status='PUBLISHED'," 
        "effective_from=(value_payload->>'effective_from')::timestamptz,version=version+1 WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid "
        "AND status='IN_REVIEW' AND reviewer_user_id IS NOT NULL AND approval_evidence_ref IS NOT NULL "
        "AND version=(value_payload->>'expected_version')::bigint; "
        "ELSIF value_operation='SUSPEND' THEN UPDATE public.assessment_rule_set_version SET status='SUSPENDED',suspended_at=(value_payload->>'created_at')::timestamptz,version=version+1 "
        "WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid AND status='PUBLISHED' "
        "AND version=(value_payload->>'expected_version')::bigint; "
        "ELSIF value_operation='RESUME' THEN UPDATE public.assessment_rule_set_version SET status='PUBLISHED',suspended_at=NULL,version=version+1 "
        "WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid AND status='SUSPENDED' "
        "AND version=(value_payload->>'expected_version')::bigint; "
        "ELSIF value_operation='RETIRE' THEN UPDATE public.assessment_rule_set_version SET status='RETIRED',retired_at=(value_payload->>'created_at')::timestamptz,version=version+1 "
        "WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid AND status IN ('PUBLISHED','SUSPENDED') "
        "AND version=(value_payload->>'expected_version')::bigint; "
        "ELSE RAISE EXCEPTION 'RULE_OPERATION_INVALID'; END IF; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'RULE_STATE_CONFLICT'; END IF; "
        "INSERT INTO public.slice5_audit VALUES((value_payload->>'audit_id')::uuid,'RULE_'||value_operation," 
        "(value_payload->>'actor_user_id')::bigint,value_payload->>'actor_role','RULE_SET',(value_payload->>'rule_set_version_id')::uuid," 
        "decode(value_payload->>'evidence_digest','hex'),(value_payload->>'created_at')::timestamptz); "
        "INSERT INTO public.slice5_outbox VALUES((value_payload->>'event_id')::uuid,'RULE_SET',(value_payload->>'rule_set_version_id')::uuid," 
        "'RULE_'||value_operation,decode(value_payload->>'outbox_digest','hex'),jsonb_build_object('rule_set_version_id'," 
        "value_payload->>'rule_set_version_id','operation',value_operation),'PENDING',0,NULL,NULL,(value_payload->>'created_at')::timestamptz,NULL); "
        "INSERT INTO public.slice5_idempotency VALUES((value_payload->>'receipt_id')::uuid,value_payload->>'actor_user_id'," 
        "'RULE_'||value_operation,(value_payload->>'rule_set_version_id')::uuid,value_payload->>'idempotency_key'," 
        "decode(value_payload->>'request_digest','hex'),convert_to((value_payload->'response')::text,'UTF8')," 
        "value_payload->>'digest_key_id',decode(value_payload->>'postimage_digest','hex'),(value_payload->>'created_at')::timestamptz); "
        "RETURN value_payload->'response';",
        (rule,),
    )
    _function(
        "slice5_outbox_claim_v1",
        "value_lease_owner UUID,value_limit BIGINT",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "IF value_limit<1 OR value_limit>100 THEN RAISE EXCEPTION 'SLICE5_OUTBOX_LIMIT_INVALID'; END IF; "
        "WITH candidates AS (SELECT event_id FROM public.slice5_outbox "
        "WHERE status IN ('PENDING','RETRY') AND (lease_until IS NULL OR lease_until<clock_timestamp()) "
        "ORDER BY created_at,event_id FOR UPDATE SKIP LOCKED LIMIT value_limit), updated AS ("
        "UPDATE public.slice5_outbox o SET status='CLAIMED',lease_owner=value_lease_owner::text,"
        "lease_until=clock_timestamp()+interval '60 seconds',attempts=o.attempts+1 FROM candidates c "
        "WHERE o.event_id=c.event_id RETURNING o.event_id,o.aggregate_type,o.aggregate_ref,o.event_type,o.attempts) "
        "SELECT jsonb_agg(jsonb_build_object('event_id',event_id,'aggregate_type',aggregate_type,"
        "'aggregate_ref',aggregate_ref,'event_type',event_type,'attempts',attempts) ORDER BY event_id) INTO value_result FROM updated; "
        "RETURN COALESCE(value_result,'[]'::jsonb);",
        (worker,),
        declarations="value_result JSONB;",
    )
    _function(
        "slice5_outbox_consume_v1",
        "value_payload JSONB",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "IF jsonb_typeof(value_payload)<>'object' OR NOT value_payload ?& ARRAY['event_id','delivery_id','target_digest','delivered_at'] "
        "THEN RAISE EXCEPTION 'SLICE5_INVALID_PAYLOAD'; END IF; "
        "IF EXISTS(SELECT 1 FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "AND o.aggregate_type='ASSESSMENT') THEN "
        "PERFORM 1 FROM public.service_case c WHERE c.case_id=(SELECT h.service_case_id FROM public.health_assessment h "
        "WHERE h.assessment_id=(SELECT o.aggregate_ref FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid)) "
        "FOR SHARE; IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.health_assessment h WHERE h.assessment_id=(SELECT o.aggregate_ref FROM public.slice5_outbox o "
        "WHERE o.event_id=(value_payload->>'event_id')::uuid) FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN'; END IF; "
        "ELSIF EXISTS(SELECT 1 FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "AND o.aggregate_type='HIGH_RISK_TASK') THEN "
        "PERFORM 1 FROM public.service_case c WHERE c.case_id=(SELECT t.service_case_id FROM public.high_risk_task t "
        "WHERE t.task_id=(SELECT o.aggregate_ref FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid)) "
        "FOR SHARE; IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.health_assessment h WHERE h.assessment_id=(SELECT t.assessment_id FROM public.high_risk_task t "
        "WHERE t.task_id=(SELECT o.aggregate_ref FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid)) "
        "FOR SHARE; IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.high_risk_task t WHERE t.task_id=(SELECT o.aggregate_ref FROM public.slice5_outbox o "
        "WHERE o.event_id=(value_payload->>'event_id')::uuid) FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN'; END IF; "
        "ELSIF EXISTS(SELECT 1 FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "AND o.aggregate_type='RULE_SET') THEN "
        "PERFORM 1 FROM public.assessment_rule_set_version r WHERE r.rule_set_version_id=(SELECT o.aggregate_ref "
        "FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid) FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_RECIPIENT_CURRENTNESS_FORBIDDEN'; END IF; END IF; "
        "PERFORM 1 FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid FOR UPDATE; "
        "IF NOT FOUND THEN RETURN jsonb_build_object('status','NOT_FOUND'); END IF; "
        "IF EXISTS(SELECT 1 FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid AND o.status='DELIVERED') THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_delivery d WHERE d.event_id=(value_payload->>'event_id')::uuid "
        "AND d.delivery_id=(value_payload->>'delivery_id')::uuid AND d.target_type='INTERNAL_EVENT' "
        "AND d.target_digest=decode(value_payload->>'target_digest','hex')) THEN "
        "RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "RETURN (SELECT jsonb_build_object('status','DELIVERED','delivery_id',value_payload->>'delivery_id',"
        "'aggregate_ref',o.aggregate_ref,'event_type',o.event_type,'new_delivery',false) FROM public.slice5_outbox o "
        "WHERE o.event_id=(value_payload->>'event_id')::uuid); END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "AND o.status='CLAIMED') THEN RAISE EXCEPTION 'SLICE5_WORKFLOW_STATE_CONFLICT'; END IF; "
        "INSERT INTO public.slice5_delivery(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at) "
        "SELECT (value_payload->>'delivery_id')::uuid,o.event_id,'INTERNAL_EVENT',o.aggregate_ref,"
        "decode(value_payload->>'target_digest','hex'),(value_payload->>'delivered_at')::timestamptz "
        "FROM public.slice5_outbox o WHERE o.event_id=(value_payload->>'event_id')::uuid "
        "ON CONFLICT(event_id,target_type,target_ref) DO NOTHING; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice5_delivery d WHERE d.event_id=(value_payload->>'event_id')::uuid "
        "AND d.delivery_id=(value_payload->>'delivery_id')::uuid AND d.target_digest=decode(value_payload->>'target_digest','hex')) "
        "THEN RAISE EXCEPTION 'SLICE5_COMMIT_OUTCOME_UNKNOWN'; END IF; "
        "UPDATE public.slice5_outbox SET status='DELIVERED',lease_owner=NULL,lease_until=NULL,"
        "delivered_at=(value_payload->>'delivered_at')::timestamptz WHERE event_id=(value_payload->>'event_id')::uuid; "
        "RETURN (SELECT jsonb_build_object('status','DELIVERED','delivery_id',value_payload->>'delivery_id',"
        "'aggregate_ref',o.aggregate_ref,'event_type',o.event_type,'new_delivery',true) FROM public.slice5_outbox o "
        "WHERE o.event_id=(value_payload->>'event_id')::uuid);",
        (worker,),
    )
    _function(
        "slice5_outbox_recover_v1",
        "value_now TIMESTAMP WITH TIME ZONE",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "WITH recovered AS (UPDATE public.slice5_outbox SET status=CASE WHEN attempts>=3 THEN 'FAILED' ELSE 'RETRY' END,"
        "lease_owner=NULL,lease_until=NULL WHERE status='CLAIMED' AND lease_until<value_now RETURNING status) "
        "SELECT jsonb_build_object('recovered',count(*),'retry',count(*) FILTER (WHERE status='RETRY'),"
        "'failed',count(*) FILTER (WHERE status='FAILED')) INTO value_result FROM recovered; RETURN value_result;",
        (worker,),
        declarations="value_result JSONB;",
    )
    _function(
        "slice5_outbox_reopen_v1",
        "value_event_id UUID,value_expected_attempts BIGINT",
        "JSONB",
        f"IF session_user<>'{worker}' THEN RAISE EXCEPTION 'SLICE5_WORKER_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.slice5_outbox WHERE event_id=value_event_id FOR UPDATE; "
        "IF NOT FOUND THEN RETURN jsonb_build_object('status','NOT_FOUND'); END IF; "
        "IF EXISTS(SELECT 1 FROM public.slice5_outbox WHERE event_id=value_event_id AND status='RETRY' "
        "AND attempts=value_expected_attempts) THEN RETURN jsonb_build_object('status','RETRY','attempts',value_expected_attempts); END IF; "
        "UPDATE public.slice5_outbox SET status='RETRY',lease_owner=NULL,lease_until=NULL WHERE event_id=value_event_id "
        "AND status='FAILED' AND attempts=value_expected_attempts; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE5_WORKFLOW_STATE_CONFLICT'; END IF; "
        "RETURN jsonb_build_object('status','RETRY','attempts',value_expected_attempts);",
        (worker,),
    )
    _function(
        "slice5_ordinary_plan_authority_v1",
        "value_service_case_id UUID",
        "BOOLEAN",
        f"IF session_user NOT IN ('{assessment}','{risk}','{worker}') THEN RAISE EXCEPTION 'SLICE5_PLAN_AUTHORITY_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.health_assessment newer JOIN public.assessment_input_snapshot s ON s.assessment_id=newer.assessment_id "
        "JOIN public.assessment_readiness_case_pointer p ON p.service_case_id=newer.service_case_id AND p.current_assembly_id=s.assembly_id "
        "WHERE newer.service_case_id=value_service_case_id AND newer.sequence_no=(SELECT max(x.sequence_no) FROM public.health_assessment x "
        "WHERE x.service_case_id=value_service_case_id) AND newer.status='COMPLETED' "
        "AND newer.overall_risk IN ('WITHIN_RANGE','ATTENTION') AND EXISTS(SELECT 1 FROM public.assessment_module_result m "
        "WHERE m.assessment_id=newer.assessment_id AND m.risk_level<>'NOT_ASSESSED') "
        "AND NOT EXISTS(SELECT 1 FROM public.high_risk_task t WHERE t.service_case_id=value_service_case_id "
        "AND t.status IN ('OPEN','CLAIMED','ESCALATED')) "
        "AND NOT EXISTS(SELECT 1 FROM public.health_assessment older WHERE older.service_case_id=value_service_case_id "
        "AND older.overall_risk='HIGH_RISK' AND NOT EXISTS(SELECT 1 FROM public.health_assessment newer "
        "WHERE newer.service_case_id=older.service_case_id AND newer.sequence_no>older.sequence_no "
        "AND newer.status='COMPLETED' AND newer.overall_risk IN ('WITHIN_RANGE','ATTENTION'))) FOR SHARE OF newer,s,p; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'ordinary plan blocked by current high risk assessment'; END IF; RETURN TRUE;",
        (assessment, risk, worker),
    )


def _create_views(clinical: str, oversight: str) -> None:
    health_reader = os.environ["KG_HEALTH_PROJECTION_READER_ROLE"]
    op.execute(
        "CREATE VIEW public.slice5_ready_projection_measurement_context_v1 WITH (security_barrier=true) AS "
        "SELECT f.generation_id,f.fact_ref,f.measurement_context FROM public.health_projection_fact f "
        "JOIN public.health_projection_generation g ON g.id=f.generation_id WHERE g.status='READY'"
    )
    op.execute("REVOKE ALL ON public.slice5_ready_projection_measurement_context_v1 FROM PUBLIC")
    op.execute(f'GRANT SELECT ON public.slice5_ready_projection_measurement_context_v1 TO "{health_reader}"')
    op.execute(
        "CREATE VIEW public.slice5_assessment_read_v1 WITH (security_barrier=true) AS "
        "SELECT h.assessment_id,h.service_case_id,h.subject_member_id,h.tenant_id,h.sequence_no,h.status,h.overall_risk," 
        "r.version_no AS rule_version,h.snapshot_id AS input_snapshot_ref,h.supersedes_assessment_id,h.initiated_at,h.completed_at,h.version," 
        "jsonb_build_object('assembly_ref',s.assembly_id,'profile_revision_ref',s.profile_revision_id," 
        "'data_as_of',s.created_at,'source_types',COALESCE((SELECT jsonb_agg(DISTINCT f.source_type) "
        "FROM public.assessment_input_assembly_fact f WHERE f.assembly_id=s.assembly_id),'[]'::jsonb)," 
        "'watermark_status',CASE WHEN p.current_assembly_id=s.assembly_id THEN 'CURRENT' ELSE 'STALE' END) AS input_evidence," 
        "COALESCE((SELECT jsonb_agg(jsonb_build_object('module_code',m.module_code,'risk_level',m.risk_level," 
        "'reason_codes',m.reason_codes,'evidence_items',m.evidence_manifest,'message_codes',m.message_codes) ORDER BY m.module_code) "
        "FROM public.assessment_module_result m WHERE m.assessment_id=h.assessment_id),'[]'::jsonb) AS module_results," 
        "(SELECT d.status FROM public.assessment_dispute d WHERE d.assessment_id=h.assessment_id ORDER BY d.created_at DESC LIMIT 1) AS dispute_status," 
        "(SELECT t.task_id FROM public.high_risk_task t WHERE t.assessment_id=h.assessment_id) AS high_risk_task_ref "
        "FROM public.health_assessment h JOIN public.assessment_rule_set_version r ON r.rule_set_version_id=h.rule_set_version_id "
        "JOIN public.assessment_input_snapshot s ON s.assessment_id=h.assessment_id "
        "LEFT JOIN public.assessment_readiness_case_pointer p ON p.service_case_id=h.service_case_id"
    )
    op.execute(
        "CREATE VIEW public.slice5_high_risk_task_read_v1 WITH (security_barrier=true) AS "
        "SELECT t.task_id,t.assessment_id,t.service_case_id,t.tenant_id,t.status,t.reason_module_codes," 
        "t.assigned_actor_id AS assignee,t.due_at,(SELECT max(a.occurred_at) FROM public.high_risk_task_action a WHERE a.task_id=t.task_id) "
        "AS last_action_at,jsonb_build_object('ordinary_plan',true,'case_completion',true) AS blocking,t.version,t.created_at,t.closed_at "
        "FROM public.high_risk_task t"
    )
    for view in ("slice5_assessment_read_v1", "slice5_high_risk_task_read_v1"):
        op.execute(f"REVOKE ALL ON public.{view} FROM PUBLIC")
        op.execute(f'GRANT SELECT ON public.{view} TO "{clinical}"')
        op.execute(f'GRANT SELECT ON public.{view} TO "{oversight}"')
    op.execute(
        "CREATE VIEW public.slice5_rule_set_governance_read_v1 WITH (security_barrier=true) AS "
        "SELECT rule_set_version_id,rule_set_code,version_no,status,typed_rule_payload,author_user_id," 
        "reviewer_user_id,approval_evidence_ref,effective_from,suspended_at,retired_at,created_at,version "
        "FROM public.assessment_rule_set_version"
    )
    op.execute("REVOKE ALL ON public.slice5_rule_set_governance_read_v1 FROM PUBLIC")
    rule_role = os.getenv("KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE", "").strip()
    op.execute(f'GRANT SELECT ON public.slice5_rule_set_governance_read_v1 TO "{rule_role}"')


def _acl(roles: tuple[str, str, str, str, str, str]) -> None:
    assessment, risk, rule, worker, clinical, oversight = roles
    for role in roles:
        op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    for table in _MODULE_TABLES:
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
        for role in roles:
            op.execute(f'REVOKE ALL ON public.{table} FROM "{role}"')


def _measurement_context_upgrade() -> None:
    allowed = (
        "measurement_context IS NULL OR "
        "(indicator_code IN ('systolic_bp','diastolic_bp') AND measurement_context IN "
        "('OFFICE','HOME_AVERAGE','ABPM_24H_AVERAGE','ABPM_DAY_AVERAGE','ABPM_NIGHT_AVERAGE')) OR "
        "(indicator_code='fasting_glucose' AND measurement_context='FASTING_VENOUS') OR "
        "(indicator_code='postprandial_glucose_2h' AND measurement_context='OGTT_2H_VENOUS') OR "
        "(indicator_code='hba1c' AND measurement_context='LAB') OR "
        "(indicator_code IN ('total_cholesterol','triglyceride','hdl_c','ldl_c') AND measurement_context='FASTING_LAB')"
    )
    for table in ("canonical_health_fact", "health_projection_fact", "assessment_input_assembly_fact"):
        op.add_column(table, sa.Column("measurement_context", sa.String(32), nullable=True), schema="public")
        op.create_check_constraint(f"ck_{table}_measurement_context_v1", table, allowed, schema="public")
    op.drop_constraint(
        "ck_canonical_health_fact_indicator_v1_v2",
        "canonical_health_fact",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_canonical_health_fact_indicator_v1_v2",
        "canonical_health_fact",
        _SLICE5_INDICATOR_CHECK,
        schema="public",
    )
    op.drop_constraint(
        "ck_health_projection_fact_indicator_v1_v2",
        "health_projection_fact",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_health_projection_fact_indicator_v1_v2",
        "health_projection_fact",
        _SLICE5_PROJECTION_INDICATOR_CHECK,
        schema="public",
    )
    health_writer = os.environ["KG_HEALTH_FACT_WRITER_ROLE"]
    health_builder = os.environ["KG_HEALTH_PROJECTION_BUILDER_ROLE"]
    health_shadow = os.environ["KG_HEALTH_PROJECTION_SHADOW_ROLE"]
    op.execute(f'GRANT INSERT(measurement_context) ON public.canonical_health_fact TO "{health_writer}"')
    op.execute(f'GRANT SELECT(measurement_context),INSERT(measurement_context) ON public.health_projection_fact TO "{health_builder}"')
    op.execute(f'GRANT SELECT(measurement_context) ON public.health_projection_fact TO "{health_shadow}"')


def upgrade() -> None:
    roles = _roles()
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )
    _measurement_context_upgrade()
    _create_tables()
    _create_functions(*roles)
    _create_views(roles[4], roles[5])
    _acl(roles)


def downgrade() -> None:
    roles = _roles()
    connection = op.get_bind()
    connection.execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )
    nonempty = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM public.assessment_rule_set_version "
            "UNION ALL SELECT 1 FROM public.health_assessment "
            "UNION ALL SELECT 1 FROM public.assessment_input_snapshot "
            "UNION ALL SELECT 1 FROM public.assessment_module_result "
            "UNION ALL SELECT 1 FROM public.high_risk_task "
            "UNION ALL SELECT 1 FROM public.high_risk_task_action "
            "UNION ALL SELECT 1 FROM public.assessment_dispute "
            "UNION ALL SELECT 1 FROM public.slice5_idempotency "
            "UNION ALL SELECT 1 FROM public.slice5_audit "
            "UNION ALL SELECT 1 FROM public.slice5_outbox "
            "UNION ALL SELECT 1 FROM public.slice5_delivery "
            "UNION ALL SELECT 1 FROM public.canonical_health_fact WHERE catalog_version=2 AND indicator_code IN "
            "('postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') "
            "UNION ALL SELECT 1 FROM public.canonical_health_fact WHERE measurement_context IS NOT NULL "
            "UNION ALL SELECT 1 FROM public.health_projection_fact WHERE measurement_context IS NOT NULL "
            "UNION ALL SELECT 1 FROM public.assessment_input_assembly_fact WHERE measurement_context IS NOT NULL)"
        )
    ).scalar_one()
    if nonempty:
        raise RuntimeError("Slice 5 downgrade requires empty module tables") from None
    for role in roles:
        op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
    op.execute("DROP VIEW public.slice5_ready_projection_measurement_context_v1")
    for name, signature in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{name}({signature})")
    op.execute("DROP VIEW public.slice5_rule_set_governance_read_v1")
    op.execute("DROP VIEW public.slice5_high_risk_task_read_v1")
    op.execute("DROP VIEW public.slice5_assessment_read_v1")
    op.drop_constraint(
        "fk_health_assessment_snapshot",
        "health_assessment",
        schema="public",
        type_="foreignkey",
    )
    for table in reversed(_MODULE_TABLES):
        op.drop_table(table, schema="public")
    op.drop_constraint(
        "ck_canonical_health_fact_indicator_v1_v2",
        "canonical_health_fact",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_canonical_health_fact_indicator_v1_v2",
        "canonical_health_fact",
        _SLICE4_INDICATOR_CHECK,
        schema="public",
    )
    op.drop_constraint(
        "ck_health_projection_fact_indicator_v1_v2",
        "health_projection_fact",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_health_projection_fact_indicator_v1_v2",
        "health_projection_fact",
        _SLICE4_PROJECTION_INDICATOR_CHECK,
        schema="public",
    )
    for table in reversed(("canonical_health_fact", "health_projection_fact", "assessment_input_assembly_fact")):
        op.drop_constraint(f"ck_{table}_measurement_context_v1", table, schema="public", type_="check")
        op.drop_column(table, "measurement_context", schema="public")
