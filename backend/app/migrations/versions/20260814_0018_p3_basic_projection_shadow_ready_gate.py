"""Add P3 Basic Projection Module C shadow and READY gate.

Revision ID: 20260814_0018
Revises: 20260813_0017
"""
import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260814_0018"
down_revision = "20260813_0017"
branch_labels = None
depends_on = None

_DOMAINS = ("organization", "health")
_FAILURES = "'PROJECTION_SOURCE_INVALID','PROJECTION_DIGEST_KEY_UNAVAILABLE','PROJECTION_CHECKPOINT_CONFLICT','PROJECTION_LEASE_CONFLICT','PROJECTION_UNAVAILABLE'"
_CATEGORIES = {
    "organization": (
        "ORG_SOURCE_MISSING", "ORG_SOURCE_DUPLICATE", "ORG_CHAIN_INVALID",
        "ORG_ROOT_INVALID", "ORG_JSONB_INVALID", "ORG_ROW_MISMATCH",
        "ORG_ROW_DIGEST_MISMATCH", "ORG_MAPPING_CORRUPT",
        "ORG_MAPPING_TARGET_INVALID", "ORG_MAPPING_SCOPE_MISMATCH",
        "ORG_COVERAGE_MISMATCH", "ORG_GENERATION_IDENTITY_MISMATCH",
        "ORG_UNPROVEN_SOURCE_DRIFT", "ORG_MAPPING_REVIEW_REQUIRED",
        "ORG_MAPPING_CONFLICT_UNRESOLVED", "ORG_POST_HWM_NEW_SOURCE",
        "ORG_POST_BUILD_VALID_VERSION_ADVANCE",
    ),
    "health": (
        "HEALTH_FACT_MISSING", "HEALTH_FACT_DUPLICATE",
        "HEALTH_FACT_FIELD_MISMATCH", "HEALTH_FACT_DIGEST_MISMATCH",
        "HEALTH_WINDOW_INVALID", "HEALTH_SELECTION_MISSING",
        "HEALTH_SELECTION_DUPLICATE", "HEALTH_WINNER_MISMATCH",
        "HEALTH_RULE_VERSION_MISMATCH", "HEALTH_MAPPING_TARGET_MISMATCH",
        "HEALTH_COVERAGE_MISMATCH", "HEALTH_GENERATION_IDENTITY_MISMATCH",
        "HEALTH_SOURCE_UNKNOWN", "HEALTH_CURRENTNESS_UNPROVEN",
        "HEALTH_MAPPING_REVIEW_REQUIRED", "HEALTH_P2_PRIORITY_EXPECTED_DIFFERENCE",
        "HEALTH_POST_HWM_NEW_FACT",
    ),
}
_CATEGORY_CLASSES = {
    "organization": (
        _CATEGORIES["organization"][:13],
        _CATEGORIES["organization"][13:15],
        _CATEGORIES["organization"][15:],
    ),
    "health": (
        _CATEGORIES["health"][:12],
        _CATEGORIES["health"][12:16],
        _CATEGORIES["health"][16:],
    ),
}


def _category_truth(domain):
    keys = _CATEGORIES[domain]
    allowed = ",".join(f"'{key}'" for key in keys)
    values = " AND ".join(
        f"(NOT category_counts ? '{key}' OR (jsonb_typeof(category_counts->'{key}')='number' AND (category_counts->>'{key}') ~ '^(0|[1-9][0-9]*)$'))"
        for key in keys
    )
    sums = [
        "+".join(f"COALESCE((category_counts->>'{key}')::bigint,0)" for key in group)
        for group in _CATEGORY_CLASSES[domain]
    ]
    return f"category_counts IS NULL OR (jsonb_typeof(category_counts)='object' AND category_counts - ARRAY[{allowed}] = '{{}}'::jsonb AND {values} AND ({sums[0]})=blocker_count AND ({sums[1]})=review_required_count AND ({sums[2]})=informational_count)"


def _roles(connection):
    values = {
        "organization": os.environ.get("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", ""),
        "health": os.environ.get("KG_HEALTH_PROJECTION_SHADOW_ROLE", ""),
        "ready": os.environ.get("KG_PROJECTION_READY_GATE_ROLE", ""),
        "confirmation": os.environ.get("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", ""),
    }
    if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value) for value in values.values()) or len(set(values.values())) != 4:
        raise RuntimeError("projection shadow runtime role configuration is invalid")
    protected_names = (
        "KG_ORGANIZATION_PROJECTION_BUILDER_ROLE",
        "KG_HEALTH_PROJECTION_BUILDER_ROLE",
        "KG_PROJECTION_CONFIRMATION_ROLE",
        "KG_DATABASE_USER",
        "KG_TEST_MIGRATION_ROLE",
        "KG_TEST_READONLY_ROLE",
    )
    protected = {os.environ.get(name, "") for name in protected_names}
    protected.discard("")
    protected.add(str(connection.execute(sa.text("SELECT current_user")).scalar_one()))
    if set(values.values()) & protected:
        raise RuntimeError("projection shadow runtime role configuration is invalid")
    existing = set(connection.execute(sa.text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:roles)"), {"roles": list(values.values())}).scalars())
    if existing != set(values.values()):
        raise RuntimeError("projection shadow runtime role configuration is invalid")
    return {key: '"' + value + '"' for key, value in values.items()}


def _organization_builder_role(connection):
    value = os.environ.get("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise RuntimeError("projection builder runtime role configuration is invalid")
    exists = connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=:role)"), {"role": value}).scalar_one()
    if not exists:
        raise RuntimeError("projection builder runtime role configuration is invalid")
    return '"' + value + '"'


def _run_table(domain):
    name = f"{domain}_projection_shadow_run"
    generation = f"{domain}_projection_generation"
    common = [
        sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("generation_id", sa.BigInteger(), nullable=False),
        sa.Column("run_sequence", sa.BigInteger(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("projection_version", sa.SmallInteger(), nullable=False), sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("high_watermark", postgresql.JSONB(), nullable=False), sa.Column("high_watermark_digest", sa.CHAR(64), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False), sa.Column("generation_input_digest", sa.CHAR(64), nullable=False),
        sa.Column("source_digest", sa.CHAR(64)), sa.Column("mapping_digest", sa.CHAR(64)), sa.Column("projection_digest", sa.CHAR(64)),
        sa.Column("coverage_digest", sa.CHAR(64)), sa.Column("evidence_digest", sa.CHAR(64)),
    ]
    if domain == "health":
        common += [sa.Column("currentness_digest", sa.CHAR(64)), sa.Column("selection_digest", sa.CHAR(64))]
    common += [
        sa.Column("blocker_count", sa.BigInteger()), sa.Column("review_required_count", sa.BigInteger()), sa.Column("informational_count", sa.BigInteger()),
        sa.Column("category_counts", postgresql.JSONB()), sa.Column("source_count", sa.BigInteger()),
    ]
    if domain == "organization":
        common += [sa.Column(x, sa.BigInteger()) for x in ("eligible_count", "projection_count", "coverage_numerator", "coverage_denominator")]
    else:
        common += [sa.Column(x, sa.BigInteger()) for x in ("current_fact_count", "projection_fact_count", "expected_selection_count", "actual_selection_count", "fact_coverage_numerator", "fact_coverage_denominator", "selection_coverage_numerator", "selection_coverage_denominator")]
    common += [
        sa.Column("start_operation_id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("complete_operation_id", postgresql.UUID(as_uuid=False)),
        sa.Column("validator_id", postgresql.UUID(as_uuid=False), nullable=False), sa.Column("lease_epoch", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(("generation_id",), (f"public.{generation}.id",), name=f"fk_{name}_generation", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("run_id", name=f"pk_{name}"), sa.UniqueConstraint("generation_id", "run_sequence", name=f"uq_{name}_generation_sequence"),
        sa.UniqueConstraint("start_operation_id", name=f"uq_{name}_start_operation"),
        sa.CheckConstraint("status IN ('RUNNING','PASSED','FAILED')", name=f"ck_{name}_status"),
        sa.CheckConstraint("projection_version=1 AND run_sequence>=1 AND lease_epoch>=0 AND version>=1", name=f"ck_{name}_identity"),
        sa.CheckConstraint("high_watermark_digest ~ '^[0-9A-F]{64}$' AND generation_input_digest ~ '^[0-9a-f]{64}$'", name=f"ck_{name}_digest"),
    ]
    result_columns = ["source_digest", "mapping_digest", "projection_digest", "coverage_digest", "evidence_digest", "blocker_count", "review_required_count", "informational_count", "category_counts", "source_count"]
    result_columns += (["eligible_count", "projection_count", "coverage_numerator", "coverage_denominator"] if domain == "organization" else ["currentness_digest", "selection_digest", "current_fact_count", "projection_fact_count", "expected_selection_count", "actual_selection_count", "fact_coverage_numerator", "fact_coverage_denominator", "selection_coverage_numerator", "selection_coverage_denominator"])
    running_null = " AND ".join(f"{column} IS NULL" for column in result_columns + ["complete_operation_id", "completed_at"])
    final_not_null = " AND ".join(f"{column} IS NOT NULL" for column in result_columns + ["complete_operation_id", "completed_at"])
    count_columns = [column for column in result_columns if column.endswith("count") or column.endswith("numerator") or column.endswith("denominator")]
    digest_columns = ["source_digest", "mapping_digest", "projection_digest", "coverage_digest", "evidence_digest"]
    if domain == "health":
        digest_columns += ["currentness_digest", "selection_digest"]
    digest_truth = " AND ".join(f"{column} IS NULL OR {column} ~ '^[0-9A-F]{{64}}$'" for column in digest_columns)
    coverage_truth = (
        "(status<>'PASSED') OR (blocker_count=0 AND review_required_count=0 AND coverage_numerator=coverage_denominator)"
        if domain == "organization" else
        "(status<>'PASSED') OR (blocker_count=0 AND review_required_count=0 AND fact_coverage_numerator=fact_coverage_denominator AND selection_coverage_numerator=selection_coverage_denominator)"
    )
    bounds = ("fact_coverage_numerator IS NULL OR (fact_coverage_numerator<=fact_coverage_denominator AND selection_coverage_numerator<=selection_coverage_denominator)" if domain == "health" else "coverage_numerator IS NULL OR coverage_numerator<=coverage_denominator")
    common += [
        sa.CheckConstraint(" AND ".join(f"{column} IS NULL OR {column}>=0" for column in count_columns) + f" AND ({bounds})", name=f"ck_{name}_counts"),
        sa.CheckConstraint(_category_truth(domain), name=f"ck_{name}_category_json"),
        sa.CheckConstraint(digest_truth, name=f"ck_{name}_result_digest"),
        sa.CheckConstraint(coverage_truth, name=f"ck_{name}_coverage"),
        sa.CheckConstraint(f"(status='RUNNING' AND {running_null} AND lease_expires_at IS NOT NULL) OR (status IN ('PASSED','FAILED') AND {final_not_null} AND lease_expires_at IS NULL)", name=f"ck_{name}_state"),
    ]
    op.create_table(name, *common, schema="public")
    op.create_index(f"idx_{name}_generation_status", name, ("generation_id", "status", "run_sequence"), schema="public")
    op.create_index(f"idx_{name}_generation_completed", name, ("generation_id", "completed_at"), schema="public")
    op.create_index(f"uq_{name}_complete_operation", name, ("complete_operation_id",), unique=True, schema="public", postgresql_where=sa.text("complete_operation_id IS NOT NULL"))


def _audit_table(domain):
    name = f"{domain}_projection_shadow_audit"; run = f"{domain}_projection_shadow_run"; generation = f"{domain}_projection_generation"
    op.create_table(name,
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False), sa.Column("run_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("generation_id", sa.BigInteger(), nullable=False), sa.Column("operation_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("action", sa.String(32), nullable=False), sa.Column("preimage_digest", sa.CHAR(64), nullable=False),
        sa.Column("postimage_digest", sa.CHAR(64), nullable=False), sa.Column("evidence_digest", sa.CHAR(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(("run_id",), (f"public.{run}.run_id",), name=f"fk_{name}_run", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(("generation_id",), (f"public.{generation}.id",), name=f"fk_{name}_generation", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=f"pk_{name}"), sa.UniqueConstraint("operation_id", name=f"uq_{name}_operation"),
        sa.CheckConstraint("action IN ('SHADOW_START','SHADOW_HEARTBEAT','SHADOW_TAKEOVER','SHADOW_COMPLETE','SHADOW_FAIL','GENERATION_READY')", name=f"ck_{name}_action"),
        schema="public")
    op.create_index(f"idx_{name}_generation_created", name, ("generation_id", "created_at"), schema="public")


def _module_c_state(domain):
    terminal = "completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL"
    return f"(status='BUILDING' AND completed_at IS NULL AND failure_code IS NULL AND builder_id IS NOT NULL AND lease_expires_at IS NOT NULL AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='BUILD_COMPLETE' AND {terminal} AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='FAILED' AND completed_at IS NOT NULL AND failure_code IN ({_FAILURES}) AND builder_id IS NULL AND lease_expires_at IS NULL AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SUPERSEDED' AND {terminal} AND current_shadow_run_id IS NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SHADOW_RUNNING' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count IN (0,1) AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SHADOW_PASSED' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count IN (1,2) AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='SHADOW_FAILED' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count=0 AND ready_at IS NULL AND ready_operation_id IS NULL) OR (status='READY' AND {terminal} AND current_shadow_run_id IS NOT NULL AND shadow_success_count=2 AND ready_at IS NOT NULL AND ready_operation_id IS NOT NULL)"


def _columns(values):
    return ",".join(values)


def _grant_select(role, relation, columns):
    op.execute(f"GRANT SELECT ({_columns(columns)}) ON TABLE public.{relation} TO {role}")


def _run_columns(domain):
    values = [
        "run_id", "generation_id", "run_sequence", "status",
        "projection_version", "rule_version", "high_watermark",
        "high_watermark_digest", "digest_key_id", "generation_input_digest",
        "source_digest", "mapping_digest", "projection_digest",
        "coverage_digest", "evidence_digest", "blocker_count",
        "review_required_count", "informational_count", "category_counts",
        "source_count", "start_operation_id", "complete_operation_id",
        "validator_id", "lease_epoch", "lease_expires_at", "started_at",
        "completed_at", "version",
    ]
    values += (["eligible_count", "projection_count", "coverage_numerator", "coverage_denominator"] if domain == "organization" else ["currentness_digest", "selection_digest", "current_fact_count", "projection_fact_count", "expected_selection_count", "actual_selection_count", "fact_coverage_numerator", "fact_coverage_denominator", "selection_coverage_numerator", "selection_coverage_denominator"])
    return values


def _revoke_module_c_source_privileges(roles):
    org, health = roles["organization"], roles["health"]
    op.execute(f"REVOKE SELECT (id,parent_id,org_code,org_name,org_type,status,sort_order,version,updated_at) ON TABLE public.platform_org FROM {org}")
    op.execute(f"REVOKE SELECT (id,legacy_tenant_id,legacy_org_id,canonical_organization_id,mapping_version,source_fingerprint,digest_key_id,disposition,reason_code,created_at) ON TABLE public.organization_legacy_mapping FROM {org}")
    op.execute(f"REVOKE SELECT (id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,supersedes_fact_id) ON TABLE public.canonical_health_fact FROM {health}")
    op.execute(f"REVOKE SELECT ON TABLE public.health_projection_source_visibility_v1 FROM {health}")
    op.execute(f"REVOKE SELECT (id,legacy_indicator_id,legacy_recorded_at,canonical_fact_id,mapping_version,source_fingerprint,digest_key_id,disposition,reason_code,created_at) ON TABLE public.health_indicator_legacy_mapping FROM {health}")


def upgrade():
    connection = op.get_bind()
    roles = _roles(connection)
    organization_builder = _organization_builder_role(connection)
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM public.organization_projection)")).scalar_one():
        raise RuntimeError("refusing to add organization projection path version baseline to populated rows")
    op.add_column("organization_projection", sa.Column("path_versions", postgresql.JSONB(), nullable=False), schema="public")
    op.execute(f"GRANT SELECT (path_versions), INSERT (path_versions) ON TABLE public.organization_projection TO {organization_builder}")
    compatibility_constraint = op.f("ck_organization_projection_compatibility")
    legacy_compatibility_constraint = op.f("ck_organization_projection_ck_organization_projection_c_41f6")
    compatibility_names = tuple(op.get_bind().execute(sa.text(
        "SELECT conname FROM pg_catalog.pg_constraint "
        "WHERE conrelid='public.organization_projection'::regclass AND contype='c' "
        "AND conname IN ('ck_organization_projection_compatibility',"
        "'ck_organization_projection_ck_organization_projection_c_41f6')"
    )).scalars())
    if len(compatibility_names) != 1:
        raise RuntimeError("organization projection compatibility constraint is ambiguous")
    existing_compatibility_constraint = (
        compatibility_constraint
        if compatibility_names[0] == "ck_organization_projection_compatibility"
        else legacy_compatibility_constraint
    )
    op.drop_constraint(existing_compatibility_constraint, "organization_projection", type_="check", schema="public")
    op.create_check_constraint(
        compatibility_constraint, "organization_projection",
        "compatibility_mode='canonical' AND jsonb_typeof(path_ids)='array' AND jsonb_typeof(path_codes)='array' AND jsonb_typeof(path_versions)='array' AND jsonb_array_length(path_ids)=4 AND jsonb_array_length(path_codes)=4 AND jsonb_array_length(path_versions)=4 AND NOT jsonb_path_exists(path_versions, '$[*] ? (@.type() != \"number\" || @ < 1 || @ % 1 != 0)')",
        schema="public",
    )
    for domain in _DOMAINS:
        _run_table(domain); _audit_table(domain)
        generation=f"{domain}_projection_generation"; run=f"{domain}_projection_shadow_run"
        op.add_column(generation, sa.Column("current_shadow_run_id", postgresql.UUID(as_uuid=False)), schema="public")
        op.add_column(generation, sa.Column("shadow_success_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False), schema="public")
        op.add_column(generation, sa.Column("ready_at", sa.DateTime(timezone=True)), schema="public")
        op.add_column(generation, sa.Column("ready_operation_id", postgresql.UUID(as_uuid=False)), schema="public")
        op.drop_constraint(f"ck_{generation}_state", generation, type_="check", schema="public")
        op.create_check_constraint(f"ck_{generation}_state", generation, _module_c_state(domain), schema="public")
        op.create_foreign_key(f"fk_{generation}_current_shadow_run", generation, run, ("current_shadow_run_id",), ("run_id",), source_schema="public", referent_schema="public", ondelete="RESTRICT", deferrable=True, initially="DEFERRED")
        op.create_index(f"uq_{generation}_ready_operation", generation, ("ready_operation_id",), unique=True, schema="public", postgresql_where=sa.text("ready_operation_id IS NOT NULL"))
    all_roles=", ".join(roles.values()); op.execute(f"REVOKE CREATE ON SCHEMA public FROM {all_roles}"); op.execute(f"GRANT USAGE ON SCHEMA public TO {all_roles}")
    for domain in _DOMAINS:
        shadow=roles[domain]; ready=roles["ready"]; confirm=roles["confirmation"]; generation=f"{domain}_projection_generation"; run=f"{domain}_projection_shadow_run"; audit=f"{domain}_projection_shadow_audit"
        op.execute(f"REVOKE ALL ON TABLE public.{run}, public.{audit} FROM PUBLIC")
        run_insert = ["run_id","generation_id","run_sequence","status","projection_version","rule_version","high_watermark","high_watermark_digest","digest_key_id","generation_input_digest","start_operation_id","validator_id","lease_epoch","lease_expires_at","version"]
        run_update = ["status","source_digest","mapping_digest","projection_digest","coverage_digest","evidence_digest","blocker_count","review_required_count","informational_count","category_counts","source_count","complete_operation_id","validator_id","lease_epoch","lease_expires_at","completed_at","version"]
        run_update += (["eligible_count","projection_count","coverage_numerator","coverage_denominator"] if domain == "organization" else ["currentness_digest","selection_digest","current_fact_count","projection_fact_count","expected_selection_count","actual_selection_count","fact_coverage_numerator","fact_coverage_denominator","selection_coverage_numerator","selection_coverage_denominator"])
        _grant_select(shadow, run, _run_columns(domain))
        _grant_select(shadow, audit, ("id","run_id","generation_id","operation_id","action","preimage_digest","postimage_digest","evidence_digest","created_at"))
        op.execute(f"GRANT INSERT ({_columns(run_insert)}) ON TABLE public.{run} TO {shadow}")
        op.execute(f"GRANT INSERT (run_id,generation_id,operation_id,action,preimage_digest,postimage_digest,evidence_digest) ON TABLE public.{audit} TO {shadow}")
        op.execute(f"GRANT UPDATE ({_columns(run_update)}) ON TABLE public.{run} TO {shadow}")
        for role in (ready, confirm):
            _grant_select(role, run, _run_columns(domain))
            _grant_select(role, audit, ("id","run_id","generation_id","operation_id","action","preimage_digest","postimage_digest","evidence_digest","created_at"))
        op.execute(f"GRANT SELECT (id,projection_version,status,high_watermark,digest_key_id,input_digest,updated_at,completed_at,version,current_shadow_run_id,shadow_success_count,ready_at,ready_operation_id) ON TABLE public.{generation} TO {shadow}, {ready}, {confirm}")
        op.execute(f"GRANT UPDATE (status,current_shadow_run_id,shadow_success_count,updated_at,version) ON TABLE public.{generation} TO {shadow}")
        op.execute(f"GRANT UPDATE (status,ready_at,ready_operation_id,updated_at,version) ON TABLE public.{generation} TO {ready}")
        op.execute(f"GRANT INSERT (run_id,generation_id,operation_id,action,preimage_digest,postimage_digest,evidence_digest) ON TABLE public.{audit} TO {ready}")
        op.execute(f"GRANT USAGE ON SEQUENCE public.{audit}_id_seq TO {shadow}, {ready}")
    org=roles["organization"]; health=roles["health"]
    op.execute(f"GRANT SELECT (id,parent_id,org_code,org_name,org_type,status,sort_order,version,updated_at) ON TABLE public.platform_org TO {org}")
    op.execute(f"GRANT SELECT (id,legacy_tenant_id,legacy_org_id,canonical_organization_id,mapping_version,source_fingerprint,digest_key_id,disposition,reason_code,created_at) ON TABLE public.organization_legacy_mapping TO {org}")
    op.execute(f"GRANT SELECT (id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,supersedes_fact_id) ON TABLE public.canonical_health_fact TO {health}")
    op.execute(f"GRANT SELECT ON TABLE public.health_projection_source_visibility_v1 TO {health}")
    op.execute(f"GRANT SELECT (id,legacy_indicator_id,legacy_recorded_at,canonical_fact_id,mapping_version,source_fingerprint,digest_key_id,disposition,reason_code,created_at) ON TABLE public.health_indicator_legacy_mapping TO {health}")
    _grant_select(org, "organization_projection", ("generation_id","organization_id","parent_id","org_code","org_name","org_type","status","sort_order","source_version","path_ids","path_codes","path_versions","compatibility_mode","scope_eligible","row_digest","digest_key_id"))
    _grant_select(health, "health_projection_fact", ("generation_id","fact_id","subject_user_id","indicator_code","numeric_value","unit","measured_at","received_at","source_type","business_day","window_start_utc","window_end_utc","row_digest","digest_key_id"))
    _grant_select(health, "health_projection_window_selection", ("generation_id","subject_user_id","indicator_code","business_day","winner_fact_id","rule_version","selection_digest","digest_key_id"))


def downgrade():
    connection = op.get_bind()
    roles=_roles(connection)
    organization_builder = _organization_builder_role(connection)
    op.execute(sa.text("LOCK TABLE public.organization_projection IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM public.organization_projection)")).scalar_one():
        raise RuntimeError("refusing to downgrade: organization projection rows exist")
    for domain in _DOMAINS:
        generation=f"{domain}_projection_generation"; run=f"{domain}_projection_shadow_run"; audit=f"{domain}_projection_shadow_audit"
        op.execute(sa.text(f"LOCK TABLE public.{generation}, public.{run}, public.{audit} IN ACCESS EXCLUSIVE MODE"))
        if op.get_bind().execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{run} UNION ALL SELECT 1 FROM public.{audit} UNION ALL SELECT 1 FROM public.{generation} WHERE current_shadow_run_id IS NOT NULL OR shadow_success_count<>0 OR ready_at IS NOT NULL OR ready_operation_id IS NOT NULL)")).scalar_one():
            raise RuntimeError("refusing to downgrade: projection shadow facts exist")
    all_roles=", ".join(roles.values())
    op.execute(f"REVOKE SELECT (path_versions), INSERT (path_versions) ON TABLE public.organization_projection FROM {organization_builder}")
    _revoke_module_c_source_privileges(roles)
    op.execute(f"REVOKE SELECT (generation_id,organization_id,parent_id,org_code,org_name,org_type,status,sort_order,source_version,path_ids,path_codes,path_versions,compatibility_mode,scope_eligible,row_digest,digest_key_id) ON TABLE public.organization_projection FROM {roles['organization']}")
    op.execute(f"REVOKE SELECT (generation_id,fact_id,subject_user_id,indicator_code,numeric_value,unit,measured_at,received_at,source_type,business_day,window_start_utc,window_end_utc,row_digest,digest_key_id) ON TABLE public.health_projection_fact FROM {roles['health']}")
    op.execute(f"REVOKE SELECT (generation_id,subject_user_id,indicator_code,business_day,winner_fact_id,rule_version,selection_digest,digest_key_id) ON TABLE public.health_projection_window_selection FROM {roles['health']}")
    for domain in _DOMAINS:
        generation=f"{domain}_projection_generation"; run=f"{domain}_projection_shadow_run"; audit=f"{domain}_projection_shadow_audit"
        shadow, ready, confirm = roles[domain], roles["ready"], roles["confirmation"]
        op.execute(f"REVOKE SELECT (id,projection_version,status,high_watermark,digest_key_id,input_digest,updated_at,completed_at,version,current_shadow_run_id,shadow_success_count,ready_at,ready_operation_id) ON TABLE public.{generation} FROM {shadow}, {ready}, {confirm}")
        op.execute(f"REVOKE UPDATE (status,current_shadow_run_id,shadow_success_count,updated_at,version) ON TABLE public.{generation} FROM {shadow}")
        op.execute(f"REVOKE UPDATE (status,ready_at,ready_operation_id,updated_at,version) ON TABLE public.{generation} FROM {ready}")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{run}, public.{audit} FROM {all_roles}")
        op.execute(f"REVOKE ALL PRIVILEGES ON SEQUENCE public.{audit}_id_seq FROM {all_roles}")
        op.drop_index(f"uq_{generation}_ready_operation", table_name=generation, schema="public")
        op.drop_constraint(f"fk_{generation}_current_shadow_run", generation, type_="foreignkey", schema="public")
        op.drop_constraint(f"ck_{generation}_state", generation, type_="check", schema="public")
        old = f"(status='BUILDING' AND completed_at IS NULL AND failure_code IS NULL AND builder_id IS NOT NULL AND lease_expires_at IS NOT NULL) OR (status='BUILD_COMPLETE' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='FAILED' AND completed_at IS NOT NULL AND failure_code IN ({_FAILURES}) AND builder_id IS NULL AND lease_expires_at IS NULL) OR (status='SUPERSEDED' AND completed_at IS NOT NULL AND failure_code IS NULL AND builder_id IS NULL AND lease_expires_at IS NULL)"
        op.create_check_constraint(f"ck_{generation}_state", generation, old, schema="public")
        for column in ("ready_operation_id", "ready_at", "shadow_success_count", "current_shadow_run_id"):
            op.drop_column(generation, column, schema="public")
        op.drop_index(f"idx_{audit}_generation_created", table_name=audit, schema="public")
        op.drop_table(audit, schema="public")
        op.drop_index(f"uq_{run}_complete_operation", table_name=run, schema="public")
        op.drop_index(f"idx_{run}_generation_completed", table_name=run, schema="public")
        op.drop_index(f"idx_{run}_generation_status", table_name=run, schema="public")
        op.drop_table(run, schema="public")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {all_roles}")
    compatibility_constraint = op.f("ck_organization_projection_compatibility")
    op.drop_constraint(compatibility_constraint, "organization_projection", type_="check", schema="public")
    op.create_check_constraint(compatibility_constraint, "organization_projection", "compatibility_mode='canonical' AND jsonb_typeof(path_ids)='array' AND jsonb_typeof(path_codes)='array' AND jsonb_array_length(path_ids)=4 AND jsonb_array_length(path_codes)=4", schema="public")
    op.drop_column("organization_projection", "path_versions", schema="public")
