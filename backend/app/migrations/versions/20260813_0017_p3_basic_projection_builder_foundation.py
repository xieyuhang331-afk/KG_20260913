"""Add P3 Basic Projection Module B builder foundation.

Revision ID: 20260813_0017
Revises: 20260812_0016
"""
import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260813_0017"
down_revision = "20260812_0016"
branch_labels = None
depends_on = None

_FAILURES = "'PROJECTION_SOURCE_INVALID','PROJECTION_DIGEST_KEY_UNAVAILABLE','PROJECTION_CHECKPOINT_CONFLICT','PROJECTION_LEASE_CONFLICT','PROJECTION_UNAVAILABLE'"
_TABLES = (
    "organization_projection_generation", "organization_projection_checkpoint", "organization_projection",
    "health_projection_generation", "health_projection_checkpoint", "health_projection_fact", "health_projection_window_selection",
)
_ROLES = ("kg_organization_projection_builder", "kg_health_projection_builder", "kg_projection_confirmation")


def _runtime_roles(connection):
    names = {
        "organization_projection_builder_role": os.environ.get("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", ""),
        "health_projection_builder_role": os.environ.get("KG_HEALTH_PROJECTION_BUILDER_ROLE", ""),
        "projection_confirmation_role": os.environ.get("KG_PROJECTION_CONFIRMATION_ROLE", ""),
    }
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) for value in names.values()) or len(set(names.values())) != 3:
        raise RuntimeError("projection runtime role configuration is invalid")
    existing = set(connection.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:roles)"), {"roles": list(names.values())}).scalars())
    if existing != set(names.values()):
        raise RuntimeError("projection runtime role configuration is invalid")
    return names


def _q(role):
    return '"' + role + '"'


def _grant_runtime_permissions(connection):
    roles = _runtime_roles(connection)
    org = _q(roles["organization_projection_builder_role"]); health = _q(roles["health_projection_builder_role"]); confirmation = _q(roles["projection_confirmation_role"])
    all_roles = f"{org}, {health}, {confirmation}"
    op.execute(f"REVOKE CREATE ON SCHEMA public FROM {all_roles}")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {all_roles}")
    op.execute(f"GRANT SELECT (id,parent_id,org_name,org_code,org_type,status,sort_order,version) ON TABLE public.platform_org TO {org}")
    op.execute(f"GRANT SELECT (id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,supersedes_fact_id) ON TABLE public.canonical_health_fact TO {health}")
    op.execute(f"GRANT SELECT (id,projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,created_at,updated_at,completed_at,failure_code,version) ON TABLE public.organization_projection_generation TO {org}")
    op.execute(f"GRANT SELECT (generation_id,last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version,updated_at) ON TABLE public.organization_projection_checkpoint TO {org}")
    op.execute(f"GRANT SELECT (generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,compatibility_mode,scope_eligible,row_digest,digest_key_id,created_at) ON TABLE public.organization_projection TO {org}")
    op.execute(f"GRANT INSERT (projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at) ON TABLE public.organization_projection_generation TO {org}")
    op.execute(f"GRANT INSERT (generation_id,last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version) ON TABLE public.organization_projection_checkpoint TO {org}")
    op.execute(f"GRANT INSERT (generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,compatibility_mode,scope_eligible,row_digest,digest_key_id) ON TABLE public.organization_projection TO {org}")
    op.execute(f"GRANT UPDATE (status,builder_id,lease_epoch,lease_expires_at,updated_at,completed_at,failure_code,version) ON TABLE public.organization_projection_generation TO {org}")
    op.execute(f"GRANT UPDATE (last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version,updated_at) ON TABLE public.organization_projection_checkpoint TO {org}")
    op.execute(f"GRANT SELECT (id,projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,created_at,updated_at,completed_at,failure_code,version) ON TABLE public.health_projection_generation TO {health}")
    op.execute(f"GRANT SELECT (generation_id,last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version,updated_at) ON TABLE public.health_projection_checkpoint TO {health}")
    op.execute(f"GRANT SELECT (generation_id,fact_id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,business_day,window_start_utc,window_end_utc,row_digest,digest_key_id,created_at) ON TABLE public.health_projection_fact TO {health}")
    op.execute(f"GRANT SELECT (generation_id,subject_user_id,indicator_code,business_day,winner_fact_id,rule_version,selection_digest,digest_key_id,created_at) ON TABLE public.health_projection_window_selection TO {health}")
    op.execute(f"GRANT INSERT (projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at) ON TABLE public.health_projection_generation TO {health}")
    op.execute(f"GRANT INSERT (generation_id,last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version) ON TABLE public.health_projection_checkpoint TO {health}")
    op.execute(f"GRANT INSERT (generation_id,fact_id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,business_day,window_start_utc,window_end_utc,row_digest,digest_key_id) ON TABLE public.health_projection_fact TO {health}")
    op.execute(f"GRANT INSERT (generation_id,subject_user_id,indicator_code,business_day,winner_fact_id,rule_version,selection_digest,digest_key_id) ON TABLE public.health_projection_window_selection TO {health}")
    op.execute(f"GRANT UPDATE (status,builder_id,lease_epoch,lease_expires_at,updated_at,completed_at,failure_code,version) ON TABLE public.health_projection_generation TO {health}")
    op.execute(f"GRANT UPDATE (last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version,updated_at) ON TABLE public.health_projection_checkpoint TO {health}")
    op.execute(f"GRANT INSERT (operator_id,module,object_type,object_id,action,payload) ON TABLE public.operation_log TO {org}, {health}")
    op.execute(f"GRANT SELECT ON TABLE public.organization_projection_operation_audit TO {org}, {confirmation}")
    op.execute(f"GRANT SELECT ON TABLE public.health_projection_operation_audit TO {health}, {confirmation}")
    op.execute(f"GRANT SELECT ON TABLE public.health_projection_source_visibility_v1 TO {health}")
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.health_projection_source_visibility_v1 FROM {org}, {confirmation}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE public.organization_projection_generation_id_seq TO {org}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE public.health_projection_generation_id_seq TO {health}")
    op.execute(f"GRANT USAGE, SELECT ON SEQUENCE public.operation_log_id_seq TO {org}, {health}")
    op.execute(f"GRANT SELECT (id,projection_version,generation_no,status,high_watermark,digest_key_id,input_digest,start_operation_id,builder_id,lease_epoch,lease_expires_at,created_at,updated_at,completed_at,failure_code,version) ON TABLE public.organization_projection_generation, public.health_projection_generation TO {confirmation}")
    op.execute(f"GRANT SELECT (generation_id,last_source_id,processed_count,projected_count,skipped_count,remaining_count,last_operation_id,checkpoint_digest,version,updated_at) ON TABLE public.organization_projection_checkpoint, public.health_projection_checkpoint TO {confirmation}")
    op.execute(f"GRANT SELECT (generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,compatibility_mode,scope_eligible,row_digest,digest_key_id,created_at) ON TABLE public.organization_projection TO {confirmation}")
    op.execute(f"GRANT SELECT (generation_id,fact_id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,business_day,window_start_utc,window_end_utc,row_digest,digest_key_id,created_at) ON TABLE public.health_projection_fact TO {confirmation}")
    op.execute(f"GRANT SELECT (generation_id,subject_user_id,indicator_code,business_day,winner_fact_id,rule_version,selection_digest,digest_key_id,created_at) ON TABLE public.health_projection_window_selection TO {confirmation}")
    for role in (org, health, confirmation):
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM {role}")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.health_projection_window_selection FROM {health}")
    return roles


def _revoke_runtime_permissions(connection):
    roles = _runtime_roles(connection)
    org = _q(roles["organization_projection_builder_role"]); health = _q(roles["health_projection_builder_role"]); confirmation = _q(roles["projection_confirmation_role"])
    all_roles = f"{org}, {health}, {confirmation}"
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.platform_org, public.canonical_health_fact, public.operation_log, public.organization_projection_operation_audit, public.health_projection_operation_audit, public.health_projection_source_visibility_v1 FROM {all_roles}")
    op.execute(f"REVOKE ALL PRIVILEGES ON SEQUENCE public.operation_log_id_seq FROM {all_roles}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {all_roles}")


def _generation(name: str, hwm: str) -> None:
    op.create_table(
        name,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("projection_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("generation_no", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("high_watermark", postgresql.JSONB(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        sa.Column("input_digest", sa.CHAR(64), nullable=False),
        sa.Column("start_operation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("builder_id", postgresql.UUID(as_uuid=False)),
        sa.Column("lease_epoch", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{name}"),
        sa.UniqueConstraint("projection_version", "generation_no", name=f"uq_{name}_version_no"),
        sa.UniqueConstraint("start_operation_id", name=f"uq_{name}_start_operation"),
        sa.UniqueConstraint("id", "digest_key_id", name=f"uq_{name}_id_digest_key"),
        sa.CheckConstraint("projection_version=1 AND generation_no>=1 AND lease_epoch>=0 AND version>=1", name=f"ck_{name}_version"),
        sa.CheckConstraint("input_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name=f"ck_{name}_digest"),
        sa.CheckConstraint((f"jsonb_typeof(high_watermark)='object' AND high_watermark=jsonb_build_object('{hwm}',high_watermark->'{hwm}') AND jsonb_typeof(high_watermark->'{hwm}')='number' AND (high_watermark->>'{hwm}') ~ '^(0|[1-9][0-9]*)$'" if hwm != "max_fact_id" else "jsonb_typeof(high_watermark)='object' AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','source_snapshot',high_watermark->'source_snapshot') AND jsonb_typeof(high_watermark->'max_fact_id')='number' AND (high_watermark->>'max_fact_id') ~ '^(0|[1-9][0-9]*)$' AND jsonb_typeof(high_watermark->'source_snapshot')='string' AND length(high_watermark->>'source_snapshot') BETWEEN 3 AND 512"), name=f"ck_{name}_high_watermark"),
        sa.CheckConstraint(f"(status='BUILDING' AND completed_at IS NULL AND failure_code IS NULL AND builder_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status='BUILD_COMPLETE' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='FAILED' AND completed_at IS NOT NULL AND failure_code IN ({_FAILURES}) AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='SUPERSEDED' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL)", name=f"ck_{name}_state"),
        schema="public",
    )
    op.create_index(f"idx_{name}_status", name, ("status", "projection_version", "generation_no"), schema="public")


def _checkpoint(name: str, generation: str) -> None:
    op.create_table(
        name,
        sa.Column("generation_id", sa.BigInteger(), nullable=False),
        sa.Column("last_source_id", sa.BigInteger()),
        sa.Column("processed_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("projected_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("skipped_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("remaining_count", sa.BigInteger(), nullable=False),
        sa.Column("last_operation_id", postgresql.UUID(as_uuid=False)),
        sa.Column("checkpoint_digest", sa.CHAR(64), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(("generation_id",), (f"public.{generation}.id",), name=f"fk_{name}_generation", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("generation_id", name=f"pk_{name}"),
        sa.CheckConstraint("processed_count>=0 AND projected_count>=0 AND skipped_count>=0 AND remaining_count>=0 AND projected_count+skipped_count=processed_count", name=f"ck_{name}_counts"),
        sa.CheckConstraint("last_source_id IS NULL OR last_source_id>=1", name=f"ck_{name}_cursor"),
        sa.CheckConstraint("checkpoint_digest ~ '^[0-9a-f]{64}$' AND version>=1", name=f"ck_{name}_digest"),
        schema="public",
    )


def upgrade() -> None:
    _generation("organization_projection_generation", "max_organization_id")
    _generation("health_projection_generation", "max_fact_id")
    _checkpoint("organization_projection_checkpoint", "organization_projection_generation")
    _checkpoint("health_projection_checkpoint", "health_projection_generation")
    op.create_table(
        "organization_projection",
        sa.Column("generation_id", sa.BigInteger(), nullable=False), sa.Column("organization_id", sa.BigInteger(), nullable=False), sa.Column("parent_id", sa.BigInteger()),
        sa.Column("org_code", sa.String(50), nullable=False), sa.Column("org_name", sa.String(100), nullable=False), sa.Column("org_type", sa.String(20), nullable=False), sa.Column("status", sa.String(10), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False), sa.Column("source_version", sa.BigInteger(), nullable=False), sa.Column("path_ids", postgresql.JSONB(), nullable=False), sa.Column("path_codes", postgresql.JSONB(), nullable=False),
        sa.Column("compatibility_mode", sa.String(16), server_default=sa.text("'canonical'"), nullable=False), sa.Column("scope_eligible", sa.Boolean(), nullable=False), sa.Column("row_digest", sa.CHAR(64), nullable=False), sa.Column("digest_key_id", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(("generation_id", "digest_key_id"), ("public.organization_projection_generation.id", "public.organization_projection_generation.digest_key_id"), name="fk_organization_projection_generation_key", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("generation_id", "organization_id", name="pk_organization_projection"), sa.UniqueConstraint("generation_id", "org_code", name="uq_organization_projection_generation_code"),
        sa.CheckConstraint("org_type IN ('headquarter','province','city','county')", name="ck_organization_projection_type"), sa.CheckConstraint("status IN ('active','inactive','archived')", name="ck_organization_projection_status"),
        sa.CheckConstraint("source_version>=1 AND organization_id>=1 AND sort_order>=0", name="ck_organization_projection_source_version"), sa.CheckConstraint("status='active' OR scope_eligible=false", name="ck_organization_projection_scope"),
        sa.CheckConstraint("row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64", name="ck_organization_projection_digest"), sa.CheckConstraint("compatibility_mode='canonical' AND jsonb_typeof(path_ids)='array' AND jsonb_typeof(path_codes)='array' AND jsonb_array_length(path_ids)=4 AND jsonb_array_length(path_codes)=4", name="ck_organization_projection_compatibility"), schema="public",
    )
    op.create_index("idx_organization_projection_parent_order", "organization_projection", ("generation_id", "parent_id", "sort_order", "organization_id"), schema="public")
    op.create_index("idx_organization_projection_type_status", "organization_projection", ("generation_id", "org_type", "status"), schema="public")
    op.create_table(
        "health_projection_fact",
        sa.Column("generation_id", sa.BigInteger(), nullable=False), sa.Column("fact_id", sa.BigInteger(), nullable=False), sa.Column("subject_user_id", sa.BigInteger(), nullable=False), sa.Column("indicator_code", sa.String(64), nullable=False), sa.Column("numeric_value", sa.Numeric(10, 2), nullable=False), sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False), sa.Column("received_at", sa.DateTime(timezone=True), nullable=False), sa.Column("source_type", sa.String(16), nullable=False), sa.Column("business_day", sa.Date(), nullable=False), sa.Column("window_start_utc", sa.DateTime(timezone=True), nullable=False), sa.Column("window_end_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_digest", sa.CHAR(64), nullable=False), sa.Column("digest_key_id", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(("generation_id", "digest_key_id"), ("public.health_projection_generation.id", "public.health_projection_generation.digest_key_id"), name="fk_health_projection_fact_generation_key", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("generation_id", "fact_id", name="pk_health_projection_fact"), sa.UniqueConstraint("generation_id", "fact_id", "subject_user_id", "indicator_code", "business_day", "digest_key_id", name="uq_health_projection_fact_winner_identity"),
        sa.CheckConstraint("indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')", name="ck_health_projection_fact_indicator_v1"),
        sa.CheckConstraint("((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR (indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR (indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR (indicator_code='bmi' AND unit='kg/m2') OR (indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))", name="ck_health_projection_fact_unit_v1"),
        sa.CheckConstraint("numeric_value::text NOT IN ('NaN','Infinity','-Infinity')", name="ck_health_projection_fact_numeric"), sa.CheckConstraint("source_type IN ('DEVICE','STORE','REPORT','APP')", name="ck_health_projection_fact_source"),
        sa.CheckConstraint("business_day=(measured_at AT TIME ZONE 'Asia/Shanghai')::date AND window_start_utc=(business_day::timestamp AT TIME ZONE 'Asia/Shanghai') AND window_end_utc=((business_day+1)::timestamp AT TIME ZONE 'Asia/Shanghai') AND measured_at>=window_start_utc AND measured_at<window_end_utc", name="ck_health_projection_fact_window"),
        sa.CheckConstraint("row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND fact_id>=1 AND subject_user_id>=1", name="ck_health_projection_fact_digest"), schema="public",
    )
    op.create_index("idx_health_projection_fact_subject_indicator_time", "health_projection_fact", ("generation_id", "subject_user_id", "indicator_code", "measured_at", "fact_id"), schema="public")
    op.create_index("idx_health_projection_fact_window", "health_projection_fact", ("generation_id", "subject_user_id", "indicator_code", "business_day"), schema="public")
    op.create_table(
        "health_projection_window_selection",
        sa.Column("generation_id", sa.BigInteger(), nullable=False), sa.Column("subject_user_id", sa.BigInteger(), nullable=False), sa.Column("indicator_code", sa.String(64), nullable=False), sa.Column("business_day", sa.Date(), nullable=False), sa.Column("winner_fact_id", sa.BigInteger(), nullable=False), sa.Column("rule_version", sa.String(64), nullable=False), sa.Column("selection_digest", sa.CHAR(64), nullable=False), sa.Column("digest_key_id", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(("generation_id", "winner_fact_id", "subject_user_id", "indicator_code", "business_day", "digest_key_id"), ("public.health_projection_fact.generation_id", "public.health_projection_fact.fact_id", "public.health_projection_fact.subject_user_id", "public.health_projection_fact.indicator_code", "public.health_projection_fact.business_day", "public.health_projection_fact.digest_key_id"), name="fk_health_projection_selection_winner_identity", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("generation_id", "subject_user_id", "indicator_code", "business_day", name="pk_health_projection_window_selection"), sa.CheckConstraint("rule_version='health-daily-selection-v1'", name="ck_health_projection_selection_rule_v1"), sa.CheckConstraint("selection_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND winner_fact_id>=1 AND subject_user_id>=1", name="ck_health_projection_selection_digest"), schema="public",
    )
    op.create_index("idx_health_projection_selection_subject_day", "health_projection_window_selection", ("generation_id", "subject_user_id", "business_day", "indicator_code"), schema="public")
    op.create_index("uq_operation_log_basic_projection_builder_operation_id", "operation_log", ("module", sa.text("((payload->>'operation_id')::uuid)")), unique=True, schema="public", postgresql_where=sa.text("module='basic_projection_builder' AND payload ? 'operation_id'"))
    op.execute("CREATE VIEW public.organization_projection_operation_audit WITH (security_barrier=true) AS SELECT object_id,payload FROM public.operation_log WHERE module='basic_projection_builder' AND object_type='organization_projection_generation'")
    op.execute("CREATE VIEW public.health_projection_operation_audit WITH (security_barrier=true) AS SELECT object_id,payload FROM public.operation_log WHERE module='basic_projection_builder' AND object_type='health_projection_generation'")
    op.execute("CREATE VIEW public.health_projection_source_visibility_v1 WITH (security_barrier=true, security_invoker=false) AS SELECT id,supersedes_fact_id,(xmin::text)::bigint AS inserting_xid FROM public.canonical_health_fact")
    op.execute("REVOKE ALL ON TABLE public.health_projection_source_visibility_v1 FROM PUBLIC")
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
    op.execute("REVOKE ALL ON SEQUENCE public.organization_projection_generation_id_seq, public.health_projection_generation_id_seq FROM PUBLIC")
    _grant_runtime_permissions(op.get_bind())


def _assert_downgrade_safe(connection) -> None:
    for table in _TABLES:
        connection.execute(sa.text(f"LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE public.operation_log IN SHARE ROW EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM public.organization_projection_generation UNION ALL SELECT 1 FROM public.health_projection_generation UNION ALL SELECT 1 FROM public.operation_log WHERE module='basic_projection_builder')")).scalar_one():
        raise RuntimeError("refusing to downgrade: projection builder facts exist")


def downgrade() -> None:
    _assert_downgrade_safe(op.get_bind())
    _revoke_runtime_permissions(op.get_bind())
    op.execute("DROP VIEW public.health_projection_source_visibility_v1")
    op.execute("DROP VIEW public.health_projection_operation_audit")
    op.execute("DROP VIEW public.organization_projection_operation_audit")
    op.drop_index("uq_operation_log_basic_projection_builder_operation_id", table_name="operation_log", schema="public")
    op.drop_index("idx_health_projection_selection_subject_day", table_name="health_projection_window_selection", schema="public")
    op.drop_index("idx_health_projection_fact_window", table_name="health_projection_fact", schema="public")
    op.drop_index("idx_health_projection_fact_subject_indicator_time", table_name="health_projection_fact", schema="public")
    op.drop_index("idx_organization_projection_type_status", table_name="organization_projection", schema="public")
    op.drop_index("idx_organization_projection_parent_order", table_name="organization_projection", schema="public")
    op.drop_index("idx_health_projection_generation_status", table_name="health_projection_generation", schema="public")
    op.drop_index("idx_organization_projection_generation_status", table_name="organization_projection_generation", schema="public")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="public")
