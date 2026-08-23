"""Phase 1 Slice 4 member-first health record and assessment readiness.

Revision ID: 20260823_0028
Revises: 20260822_0027
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260823_0028"
down_revision = "20260822_0027"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212024
_NEW_IDENTITIES = (
    ("KG_HEALTH_RECORD_WRITER_ROLE", "KG_HEALTH_RECORD_WRITER_DATABASE_URL"),
    ("KG_ASSESSMENT_READINESS_WRITER_ROLE", "KG_ASSESSMENT_READINESS_WRITER_DATABASE_URL"),
    ("KG_SLICE4_WORKFLOW_WORKER_ROLE", "KG_SLICE4_WORKFLOW_WORKER_DATABASE_URL"),
    ("KG_SLICE4_CLINICAL_READER_ROLE", "KG_SLICE4_CLINICAL_READER_DATABASE_URL"),
    ("KG_SLICE4_INSTITUTION_READER_ROLE", "KG_SLICE4_INSTITUTION_READER_DATABASE_URL"),
    ("KG_SLICE4_IDENTITY_AUTHORITY_ROLE", "KG_SLICE4_IDENTITY_AUTHORITY_DATABASE_URL"),
)
_EXISTING_IDENTITIES = (
    ("KG_DATABASE_USER", "KG_IDENTITY_APPLICATION_DATABASE_URL"),
    ("KG_READONLY_ROLE", "KG_READONLY_DATABASE_URL"),
    ("KG_VERIFICATION_WRITER_ROLE", "KG_VERIFICATION_WRITER_DATABASE_URL"),
    ("KG_DELIVERY_WORKER_ROLE", "KG_DELIVERY_WORKER_DATABASE_URL"),
    ("KG_OUTBOX_AUDIT_ROLE", "KG_OUTBOX_AUDIT_DATABASE_URL"),
    ("KG_HEALTH_FACT_WRITER_ROLE", "KG_HEALTH_FACT_WRITER_DATABASE_URL"),
    ("KG_ORGANIZATION_MAPPING_WRITER_ROLE", "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"),
    ("KG_HEALTH_MAPPING_WRITER_ROLE", "KG_HEALTH_MAPPING_WRITER_DATABASE_URL"),
    ("KG_MAPPING_AUDIT_ROLE", "KG_MAPPING_AUDIT_DATABASE_URL"),
    ("KG_MAPPING_SHADOW_ROLE", "KG_MAPPING_SHADOW_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_BUILDER_ROLE", "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_READER_ROLE", "KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
    ("KG_PROJECTION_CONFIRMATION_ROLE", "KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_READY_GATE_DATABASE_URL"),
    ("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
    ("KG_MEMBER_ENROLLMENT_WRITER_ROLE", "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL"),
    ("KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE", "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"),
    ("KG_MEMBER_CASE_WRITER_ROLE", "KG_MEMBER_CASE_WRITER_DATABASE_URL"),
    ("KG_MEMBER_WORKFLOW_WORKER_ROLE", "KG_MEMBER_WORKFLOW_WORKER_DATABASE_URL"),
    ("KG_MEMBER_ENROLLMENT_READER_ROLE", "KG_MEMBER_ENROLLMENT_READER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_WRITER_ROLE", "KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_REVIEW_WRITER_ROLE", "KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL"),
    ("KG_PRIVATE_FILE_WRITER_ROLE", "KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_READER_ROLE", "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"),
    ("KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_THERAPIST_REVIEW_WRITER_ROLE", "KG_THERAPIST_REVIEW_WRITER_DATABASE_URL"),
    ("KG_THERAPIST_READINESS_WORKER_ROLE", "KG_THERAPIST_READINESS_WORKER_DATABASE_URL"),
    ("KG_THERAPIST_READER_ROLE", "KG_THERAPIST_READER_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
)

_MODULE_TABLES = (
    "health_profile_revision",
    "detection_report_attachment",
    "health_fact_status_event",
    "health_projection_subject_indicator_evidence_v2",
    "assessment_readiness_policy_version",
    "assessment_input_assembly",
    "assessment_input_assembly_fact",
    "assessment_readiness_case_pointer",
    "slice4_idempotency",
    "slice4_audit",
    "slice4_outbox",
    "slice4_delivery",
)

_BOUNDARY_VIEWS = (
    "slice4_health_profile_clinical_read_v1",
    "slice4_institution_health_record_read_v1",
    "slice4_detection_report_read_v1",
    "slice4_health_fact_status_read_v1",
    "slice4_assessment_readiness_read_v1",
    "slice4_recompute_candidate_v1",
    "health_ready_projection_resolution_v2",
    "health_projection_source_visibility_v2",
    "health_projection_status_visibility_v2",
    "slice4_projection_coverage_source_v2",
    "health_ready_subject_indicator_evidence_v2",
    "health_ready_projection_fact_v2",
)

_EXTENDED_FUNCTIONS = (
    ("slice4_subject_authority_v1", "UUID,UUID,BIGINT,VARCHAR"),
    ("slice4_readiness_currentness_v1", "UUID,BIGINT"),
    ("slice4_projection_coverage_v2", "UUID,JSONB"),
    ("slice4_report_file_authority_v1", "UUID,UUID,BIGINT,VARCHAR"),
    ("slice4_clinical_profile_read_v1", "BIGINT,VARCHAR,UUID,UUID,UUID"),
    ("slice4_clinical_report_read_v1", "BIGINT,VARCHAR,UUID,UUID,UUID,JSONB"),
    ("slice4_clinical_fact_read_v1", "BIGINT,VARCHAR,UUID,UUID,UUID,JSONB"),
    ("slice4_institution_health_read_v1", "BIGINT,UUID,VARCHAR,JSONB"),
    ("health_projection_builder_source_v2", "BIGINT,JSONB,VARCHAR"),
    ("health_projection_subject_evidence_verify_v2", "BIGINT"),
    ("slice4_profile_confirm_v1", "UUID,UUID,UUID"),
    ("slice4_report_confirm_v1", "UUID,UUID,UUID"),
    ("slice4_health_fact_confirm_v1", "UUID,UUID,UUID"),
    ("slice4_assembly_confirm_v1", "UUID,UUID,UUID"),
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 4 database role configuration is invalid") from None


def _membership_is_unsafe(connection, roles: tuple[str, ...]) -> bool:
    rows = dict(
        connection.execute(
            sa.text("SELECT rolname,oid FROM pg_roles WHERE rolname=ANY(:roles)"),
            {"roles": list(roles)},
        ).all()
    )
    if set(rows) != set(roles):
        _configuration_error()
    return bool(
        connection.execute(
            sa.text(
                "WITH RECURSIVE paths(source_oid,target_oid,path) AS ("
                "SELECT member,roleid,ARRAY[member,roleid] FROM pg_auth_members UNION ALL "
                "SELECT p.source_oid,m.roleid,p.path||m.roleid FROM paths p "
                "JOIN pg_auth_members m ON m.member=p.target_oid "
                "WHERE NOT m.roleid=ANY(p.path)) "
                "SELECT EXISTS(SELECT 1 FROM paths "
                "WHERE source_oid=ANY(:oids) OR target_oid=ANY(:oids))"
            ),
            {"oids": list(rows.values())},
        ).scalar_one()
    )


def _roles() -> tuple[str, str, str, str, str, str]:
    configured: dict[str, str] = {}
    targets: set[tuple[str | None, int | None, str | None]] = set()
    for role_var, url_var in (*_EXISTING_IDENTITIES, *_NEW_IDENTITIES):
        role = os.getenv(role_var, "").strip()
        raw_url = os.getenv(url_var, "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
            _configuration_error()
        try:
            url = make_url(raw_url)
        except Exception:
            _configuration_error()
        if url.drivername != "postgresql+asyncpg" or url.username != role:
            _configuration_error()
        configured[role_var] = role
        targets.add((url.host, url.port, url.database))
    roles = tuple(configured.values())
    if len(set(roles)) != len(roles) or len(targets) != 1:
        _configuration_error()
    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    if current_user in roles or _membership_is_unsafe(connection, roles):
        _configuration_error()
    flags = connection.execute(
        sa.text(
            "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(:roles)"
        ),
        {"roles": list(roles)},
    ).mappings().all()
    unsafe = ("rolsuper", "rolcreaterole", "rolcreatedb", "rolinherit", "rolreplication", "rolbypassrls")
    if len(flags) != len(roles) or any(row[name] for row in flags for name in unsafe):
        _configuration_error()
    return tuple(configured[name] for name, _ in _NEW_IDENTITIES)  # type: ignore[return-value]


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=False), nullable=nullable)


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _alter_health_profile() -> None:
    # Catalog truth: ((subject_member_id IS NULL AND current_revision_id IS NULL AND user_id IS NOT NULL AND gender IS NOT NULL AND birth_date IS NOT NULL) OR (subject_member_id IS NOT NULL AND profile_public_id IS NOT NULL AND current_revision_id IS NOT NULL AND gender IS NULL AND birth_date IS NULL AND height IS NULL AND weight IS NULL))
    op.add_column("health_profile", _uuid("profile_public_id", nullable=True), schema="public")
    op.add_column("health_profile", _uuid("subject_member_id", nullable=True), schema="public")
    op.add_column("health_profile", _uuid("current_revision_id", nullable=True), schema="public")
    op.add_column(
        "health_profile",
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        schema="public",
    )
    for column in ("user_id", "gender", "birth_date"):
        op.alter_column("health_profile", column, nullable=True, schema="public")
    op.create_index(
        "uq_health_profile_profile_public_id",
        "health_profile",
        ["profile_public_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("profile_public_id IS NOT NULL"),
    )
    op.create_index(
        "uq_health_profile_subject_member_id",
        "health_profile",
        ["subject_member_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("subject_member_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_health_profile_subject_member",
        "health_profile",
        "member",
        ["subject_member_id"],
        ["member_id"],
        source_schema="public",
        referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_health_profile_v1_v2_truth",
        "health_profile",
        "((subject_member_id IS NULL AND current_revision_id IS NULL AND user_id IS NOT NULL AND gender IS NOT NULL AND birth_date IS NOT NULL) OR "
        "(subject_member_id IS NOT NULL AND profile_public_id IS NOT NULL AND current_revision_id IS NOT NULL AND gender IS NULL AND birth_date IS NULL AND height IS NULL AND weight IS NULL))",
        schema="public",
    )


def _alter_health_data_tables() -> None:
    report_columns = (
        _uuid("report_id", nullable=True),
        _uuid("subject_member_id", nullable=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=True),
        _uuid("service_case_id", nullable=True),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("source_type", sa.String(16), nullable=True),
        _ts("measured_at", nullable=True),
        _ts("received_at", nullable=True),
        sa.Column("report_status", sa.String(24), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        _uuid("supersedes_report_id", nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
    )
    for column in report_columns:
        op.add_column("detection_report", column, schema="public")
    op.alter_column("detection_report", "user_id", nullable=True, schema="public")
    op.create_unique_constraint(
        "uq_detection_report_report_id", "detection_report", ["report_id"], schema="public"
    )
    op.create_foreign_key(
        "fk_detection_report_subject_member", "detection_report", "member",
        ["subject_member_id"], ["member_id"], source_schema="public", referent_schema="identity",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_detection_report_service_case", "detection_report", "service_case",
        ["service_case_id"], ["case_id"], source_schema="public", referent_schema="public",
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ck_detection_report_type", "detection_report", schema="public", type_="check"
    )
    op.create_check_constraint(
        "ck_detection_report_type_v1_v2", "detection_report",
        "((report_id IS NULL AND report_type IN ('initial_screening','store_retest','home_self_test')) OR "
        "(report_id IS NOT NULL AND report_type IN ('LAB_REPORT','IMAGING_REPORT','PHYSICAL_EXAM','OTHER')))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_detection_report_v1_v2_truth", "detection_report",
        "(report_id IS NULL AND user_id IS NOT NULL) OR "
        "(report_id IS NOT NULL AND subject_member_id IS NOT NULL AND service_case_id IS NOT NULL "
        "AND tenant_id IS NOT NULL AND measured_at IS NOT NULL AND received_at IS NOT NULL "
        "AND report_status IN ('PENDING_SCAN','CLEAN','REJECTED','SUPERSEDED') "
        "AND source_type IN ('APP','STORE') AND version>=1)",
        schema="public",
    )

    op.add_column("canonical_health_fact", _uuid("fact_ref", nullable=True), schema="public")
    op.add_column("canonical_health_fact", _uuid("subject_member_id", nullable=True), schema="public")
    op.alter_column("canonical_health_fact", "subject_user_id", nullable=True, schema="public")
    for constraint in (
        "ck_canonical_health_fact_catalog_v1",
        "ck_canonical_health_fact_indicator_v1",
        "ck_canonical_health_fact_unit_v1",
        "ck_canonical_health_fact_source_type",
    ):
        op.drop_constraint(constraint, "canonical_health_fact", schema="public", type_="check")
    op.create_check_constraint(
        "ck_canonical_health_fact_catalog_v1_v2", "canonical_health_fact",
        "((catalog_version=1 AND subject_user_id IS NOT NULL AND subject_member_id IS NULL AND fact_ref IS NULL) OR "
        "(catalog_version=2 AND subject_member_id IS NOT NULL AND fact_ref IS NOT NULL AND source_type IN ('APP','STORE','REPORT')))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_canonical_health_fact_indicator_v1_v2", "canonical_health_fact",
        "(catalog_version=1 AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR "
        "(catalog_version=2 AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','hba1c','weight','height','waist'))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_canonical_health_fact_unit_v1_v2", "canonical_health_fact",
        "((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR "
        "(indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR "
        "(indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR "
        "(indicator_code IN ('height','waist') AND unit='cm') OR (indicator_code='bmi' AND unit='kg/m2') OR "
        "(indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_canonical_health_fact_source_type_v1_v2", "canonical_health_fact",
        "(catalog_version=1 AND source_type IN ('APP','STORE','DEVICE','REPORT')) OR (catalog_version=2 AND source_type IN ('APP','STORE','REPORT'))",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_canonical_health_fact_fact_ref", "canonical_health_fact", ["fact_ref"], schema="public"
    )
    op.create_foreign_key(
        "fk_canonical_health_fact_subject_member", "canonical_health_fact", "member",
        ["subject_member_id"], ["member_id"], source_schema="public", referent_schema="identity",
        ondelete="RESTRICT",
    )

    op.add_column("health_projection_fact", _uuid("subject_member_id", nullable=True), schema="public")
    op.add_column("health_projection_fact", _uuid("fact_ref", nullable=True), schema="public")
    op.add_column("health_projection_fact", sa.Column("status_event_seq", sa.BigInteger(), nullable=True), schema="public")
    op.alter_column("health_projection_fact", "subject_user_id", nullable=True, schema="public")
    op.drop_constraint(
        "fk_health_projection_selection_winner_identity",
        "health_projection_window_selection",
        schema="public",
        type_="foreignkey",
    )
    op.drop_constraint(
        "pk_health_projection_window_selection",
        "health_projection_window_selection",
        schema="public",
        type_="primary",
    )
    op.add_column("health_projection_window_selection", _uuid("subject_member_id", nullable=True), schema="public")
    op.add_column("health_projection_window_selection", _uuid("winner_fact_ref", nullable=True), schema="public")
    op.alter_column("health_projection_window_selection", "subject_user_id", nullable=True, schema="public")
    op.create_foreign_key(
        "fk_health_projection_selection_winner",
        "health_projection_window_selection",
        "health_projection_fact",
        ["generation_id", "winner_fact_id"],
        ["generation_id", "fact_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_health_projection_selection_v1",
        "health_projection_window_selection",
        ["generation_id", "subject_user_id", "indicator_code", "business_day"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("subject_member_id IS NULL"),
    )
    op.create_index(
        "uq_health_projection_selection_v2",
        "health_projection_window_selection",
        ["generation_id", "subject_member_id", "indicator_code", "business_day"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("subject_member_id IS NOT NULL"),
    )
    op.add_column("health_projection_shadow_run", sa.Column("status_event_count", sa.BigInteger(), nullable=True), schema="public")

    op.drop_constraint("ck_health_projection_generation_version", "health_projection_generation", schema="public", type_="check")
    op.drop_constraint("ck_health_projection_generation_high_watermark", "health_projection_generation", schema="public", type_="check")
    op.create_check_constraint(
        "ck_health_projection_generation_version_v1_v2", "health_projection_generation",
        "projection_version IN (1,2) AND generation_no>=1 AND lease_epoch>=0 AND version>=1",
        schema="public",
    )
    op.create_check_constraint(
        "ck_health_projection_generation_hwm_v1_v2", "health_projection_generation",
        "jsonb_typeof(high_watermark)='object' AND ("
        "(projection_version=1 AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','source_snapshot',high_watermark->'source_snapshot')) OR "
        "(projection_version=2 AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','max_status_event_seq',high_watermark->'max_status_event_seq','source_snapshot',high_watermark->'source_snapshot'))) "
        "AND (high_watermark->>'max_fact_id')~'^(0|[1-9][0-9]*)$' "
        "AND (projection_version=1 OR (high_watermark->>'max_status_event_seq')~'^(0|[1-9][0-9]*)$') "
        "AND jsonb_typeof(high_watermark->'source_snapshot')='string' "
        "AND length(high_watermark->>'source_snapshot') BETWEEN 3 AND 512",
        schema="public",
    )
    op.drop_constraint("ck_health_projection_shadow_run_identity", "health_projection_shadow_run", schema="public", type_="check")
    op.create_check_constraint(
        "ck_health_projection_shadow_run_identity_v1_v2", "health_projection_shadow_run",
        "projection_version IN (1,2) AND run_sequence>=1 AND lease_epoch>=0 AND version>=1 "
        "AND (projection_version=1 "
        "OR (projection_version=2 AND rule_version='health-daily-selection-v2'))",
        schema="public",
    )
    for constraint in (
        "ck_health_projection_fact_indicator_v1", "ck_health_projection_fact_unit_v1",
        "ck_health_projection_fact_source", "ck_health_projection_fact_digest",
    ):
        op.drop_constraint(constraint, "health_projection_fact", schema="public", type_="check")
    op.create_check_constraint(
        "ck_health_projection_fact_identity_v1_v2", "health_projection_fact",
        "((subject_member_id IS NULL AND subject_user_id IS NOT NULL AND fact_ref IS NULL AND status_event_seq IS NULL) OR "
        "(subject_member_id IS NOT NULL AND fact_ref IS NOT NULL AND status_event_seq IS NOT NULL))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_health_projection_fact_indicator_v1_v2", "health_projection_fact",
        "(subject_member_id IS NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')) OR "
        "(subject_member_id IS NOT NULL AND indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','hba1c','weight','height','waist'))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_health_projection_fact_unit_v1_v2", "health_projection_fact",
        "((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR "
        "(indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR "
        "(indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR "
        "(indicator_code IN ('height','waist') AND unit='cm') OR (indicator_code='bmi' AND unit='kg/m2') OR "
        "(indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_health_projection_fact_source_v1_v2", "health_projection_fact",
        "(subject_member_id IS NULL AND source_type IN ('DEVICE','STORE','REPORT','APP')) OR "
        "(subject_member_id IS NOT NULL AND source_type IN ('STORE','REPORT','APP'))",
        schema="public",
    )
    op.create_check_constraint(
        "ck_health_projection_fact_digest_v1_v2", "health_projection_fact",
        "row_digest~'^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND fact_id>=1 "
        "AND (subject_user_id IS NULL OR subject_user_id>=1)",
        schema="public",
    )
    op.drop_constraint("ck_health_projection_selection_rule_v1", "health_projection_window_selection", schema="public", type_="check")
    op.drop_constraint("ck_health_projection_selection_digest", "health_projection_window_selection", schema="public", type_="check")
    op.create_check_constraint(
        "ck_health_projection_selection_v1_v2", "health_projection_window_selection",
        "((subject_member_id IS NULL AND subject_user_id IS NOT NULL AND winner_fact_ref IS NULL AND rule_version='health-daily-selection-v1') OR "
        "(subject_member_id IS NOT NULL AND winner_fact_ref IS NOT NULL AND rule_version='health-daily-selection-v2')) "
        "AND selection_digest~'^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 "
        "AND winner_fact_id>=1 AND (subject_user_id IS NULL OR subject_user_id>=1)",
        schema="public",
    )


def _create_core_tables() -> None:
    op.create_table(
        "health_profile_revision",
        _uuid("profile_revision_id"),
        _uuid("subject_member_id"),
        sa.Column("subject_user_id", sa.BigInteger(), nullable=True),
        sa.Column("revision_no", sa.BigInteger(), nullable=False),
        _uuid("tenant_public_id"),
        sa.Column("identity_source_kind", sa.String(16), nullable=False),
        _uuid("identity_revision_ref"),
        sa.Column("identity_source_version", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("snapshot_key_id", sa.String(64), nullable=False),
        _ts("reconfirmed_at"),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("changed_fields", postgresql.JSONB(), nullable=False),
        _uuid("supersedes_revision_id", nullable=True),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("snapshot_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("profile_revision_id", name="pk_health_profile_revision"),
        sa.UniqueConstraint("subject_member_id", "revision_no", name="uq_health_profile_revision_member_no"),
        sa.UniqueConstraint("profile_revision_id", "subject_member_id", name="uq_health_profile_revision_member"),
        sa.UniqueConstraint("supersedes_revision_id", name="uq_health_profile_revision_successor"),
        sa.ForeignKeyConstraint(["subject_member_id"], ["identity.member.member_id"], name="fk_health_profile_revision_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_user_id"], ["public.user.id"], name="fk_health_profile_revision_user", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["public.user.id"], name="fk_health_profile_revision_actor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_public_id"], ["public.institution_application.tenant_public_id"], name="fk_health_profile_revision_tenant", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_revision_id"], ["public.health_profile_revision.profile_revision_id"], name="fk_health_profile_revision_supersedes", ondelete="RESTRICT"),
        sa.CheckConstraint("revision_no>=1 AND identity_source_version>=1 AND identity_source_kind IN ('P1','SLICE3') AND source_type IN ('APP','STORE') AND actor_type IN ('SELF','PROXY','THERAPIST')", name="ck_health_profile_revision_truth"),
        schema="public",
    )
    op.create_foreign_key(
        "fk_health_profile_current_revision",
        "health_profile",
        "health_profile_revision",
        ["current_revision_id", "subject_member_id"],
        ["profile_revision_id", "subject_member_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "slice4_idempotency",
        _uuid("receipt_id"),
        sa.Column("operation", sa.String(48), nullable=False),
        _uuid("scope_ref"),
        _uuid("idempotency_key"),
        sa.Column("request_digest", sa.LargeBinary(), nullable=False),
        sa.Column("postimage_digest", sa.LargeBinary(), nullable=False),
        sa.Column("response_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("response_key_id", sa.String(64), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_slice4_idempotency"),
        sa.UniqueConstraint("operation", "scope_ref", "idempotency_key", name="uq_slice4_idempotency_operation_scope_key"),
        sa.CheckConstraint("octet_length(request_digest)>=16 AND octet_length(postimage_digest)>=16", name="ck_slice4_idempotency_digest"),
        schema="public",
    )
    op.create_table(
        "slice4_audit",
        _uuid("audit_id"),
        sa.Column("event_type", sa.String(64), nullable=False),
        _uuid("aggregate_ref"),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("event_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("audit_id", name="pk_slice4_audit"),
        schema="public",
    )
    op.create_table(
        "slice4_outbox",
        _uuid("event_id"),
        sa.Column("aggregate_type", sa.String(48), nullable=False),
        _uuid("aggregate_ref"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.LargeBinary(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        _ts("lease_until", nullable=True),
        _ts("created_at"),
        _ts("delivered_at", nullable=True),
        sa.PrimaryKeyConstraint("event_id", name="pk_slice4_outbox"),
        sa.CheckConstraint(
            "(status='PENDING' AND attempts BETWEEN 0 AND 2 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NULL) OR "
            "(status='PROCESSING' AND attempts BETWEEN 1 AND 3 AND lease_owner IS NOT NULL "
            "AND lease_until IS NOT NULL AND delivered_at IS NULL) OR "
            "(status='DELIVERED' AND attempts BETWEEN 1 AND 3 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NOT NULL) OR "
            "(status='FAILED' AND attempts=3 AND lease_owner IS NULL "
            "AND lease_until IS NULL AND delivered_at IS NULL)",
            name="ck_slice4_outbox_truth",
        ),
        schema="public",
    )
    op.create_table(
        "slice4_delivery",
        _uuid("delivery_id"),
        _uuid("event_id"),
        sa.Column("target_type", sa.String(32), nullable=False),
        _uuid("target_ref"),
        sa.Column("target_digest", sa.LargeBinary(), nullable=False),
        _ts("delivered_at"),
        sa.PrimaryKeyConstraint("delivery_id", name="pk_slice4_delivery"),
        sa.UniqueConstraint("event_id", "target_digest", name="uq_slice4_delivery_event_target"),
        sa.ForeignKeyConstraint(["event_id"], ["public.slice4_outbox.event_id"], name="fk_slice4_delivery_event", ondelete="RESTRICT"),
        schema="public",
    )

    op.create_table(
        "detection_report_attachment",
        _uuid("attachment_id"),
        _uuid("report_id"),
        _uuid("private_file_id"),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("attachment_id", name="pk_detection_report_attachment"),
        sa.UniqueConstraint("report_id", "position", name="uq_detection_report_attachment_position"),
        sa.UniqueConstraint("private_file_id", name="uq_detection_report_attachment_file"),
        sa.ForeignKeyConstraint(["report_id"], ["public.detection_report.report_id"], name="fk_detection_report_attachment_report", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["private_file_id"], ["public.private_file.file_id"], name="fk_detection_report_attachment_file", ondelete="RESTRICT"),
        sa.CheckConstraint("position BETWEEN 1 AND 10", name="ck_detection_report_attachment_position"),
        schema="public",
    )
    op.create_table(
        "health_fact_status_event",
        _uuid("status_event_id"),
        sa.Column("status_event_seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("fact_id", sa.BigInteger(), nullable=False),
        sa.Column("event_no", sa.BigInteger(), nullable=False),
        _uuid("predecessor_event_id", nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        _uuid("service_case_id"),
        sa.Column("event_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("status_event_id", name="pk_health_fact_status_event"),
        sa.UniqueConstraint("status_event_seq", name="uq_health_fact_status_event_seq"),
        sa.UniqueConstraint("fact_id", "event_no", name="uq_health_fact_status_event_fact_no"),
        sa.UniqueConstraint("predecessor_event_id", name="uq_health_fact_status_event_successor"),
        sa.ForeignKeyConstraint(["fact_id"], ["public.canonical_health_fact.id"], name="fk_health_fact_status_event_fact", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["predecessor_event_id"], ["public.health_fact_status_event.status_event_id"], name="fk_health_fact_status_event_predecessor", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_case_id"], ["public.service_case.case_id"], name="fk_health_fact_status_event_case", ondelete="RESTRICT"),
        sa.CheckConstraint("event_no>=1 AND state IN ('SELF_REPORTED','VERIFIED','UNKNOWN','DISPUTED')", name="ck_health_fact_status_event_truth"),
        schema="public",
    )
    op.create_table(
        "health_projection_subject_indicator_evidence_v2",
        sa.Column("generation_id", sa.BigInteger(), nullable=False),
        _uuid("subject_member_id"),
        sa.Column("indicator_code", sa.String(64), nullable=False),
        sa.Column("fact_count", sa.BigInteger(), nullable=False),
        sa.Column("status_event_count", sa.BigInteger(), nullable=False),
        sa.Column("max_fact_id", sa.BigInteger(), nullable=False),
        sa.Column("max_status_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("source_snapshot", sa.String(512), nullable=False),
        sa.Column("fact_set_digest", sa.CHAR(64), nullable=False),
        sa.Column("status_set_digest", sa.CHAR(64), nullable=False),
        sa.Column("evidence_digest", sa.CHAR(64), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("generation_id", "subject_member_id", "indicator_code", name="pk_health_projection_subject_indicator_evidence_v2"),
        sa.ForeignKeyConstraint(["generation_id"], ["public.health_projection_generation.id"], name="fk_health_projection_subject_indicator_evidence_generation", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_member_id"], ["identity.member.member_id"], name="fk_health_projection_subject_indicator_evidence_member", ondelete="RESTRICT"),
        sa.CheckConstraint("fact_count>=0 AND status_event_count>=0 AND max_fact_id>=0 AND max_status_event_seq>=0", name="ck_health_projection_subject_indicator_evidence_counts"),
        schema="public",
    )

    op.create_table(
        "assessment_readiness_policy_version",
        _uuid("policy_version_id"),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("required_profile_sections", postgresql.JSONB(), nullable=False),
        sa.Column("required_indicators", postgresql.JSONB(), nullable=False),
        sa.Column("allowed_states", postgresql.JSONB(), nullable=False),
        sa.Column("projection_version", sa.SmallInteger(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("professionally_approved", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        _ts("approved_at", nullable=True),
        _ts("effective_from"),
        _ts("retired_at", nullable=True),
        sa.Column("policy_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("policy_version_id", name="pk_assessment_readiness_policy_version"),
        sa.UniqueConstraint("version_no", name="uq_assessment_readiness_policy_version_no"),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','RETIRED') AND projection_version>=2", name="ck_assessment_readiness_policy_truth"),
        schema="public",
    )
    op.create_index("uq_assessment_readiness_policy_current", "assessment_readiness_policy_version", ["status"], unique=True, schema="public", postgresql_where=sa.text("status='PUBLISHED'"))
    op.create_table(
        "assessment_input_assembly",
        _uuid("assembly_id"),
        sa.Column("assembly_seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        _uuid("service_case_id"),
        _uuid("subject_member_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        _uuid("primary_therapist_id"),
        _uuid("profile_revision_id", nullable=True),
        sa.Column("consent_version_ids", postgresql.JSONB(), nullable=False),
        _uuid("policy_version_id", nullable=True),
        sa.Column("projection_version", sa.SmallInteger(), nullable=True),
        sa.Column("rule_version", sa.String(64), nullable=True),
        sa.Column("resolved_generation_id", sa.BigInteger(), nullable=True),
        sa.Column("required_max_fact_id", sa.BigInteger(), nullable=False),
        sa.Column("required_max_status_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("source_snapshot", sa.String(512), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("missing_codes", postgresql.JSONB(), nullable=False),
        sa.Column("expired_codes", postgresql.JSONB(), nullable=False),
        sa.Column("disputed_codes", postgresql.JSONB(), nullable=False),
        sa.Column("source_vector_digest", sa.LargeBinary(), nullable=False),
        sa.Column("source_digest", sa.LargeBinary(), nullable=False),
        sa.Column("assembly_digest", sa.LargeBinary(), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        _ts("generated_at"),
        sa.PrimaryKeyConstraint("assembly_id", name="pk_assessment_input_assembly"),
        sa.UniqueConstraint("assembly_seq", name="uq_assessment_input_assembly_seq"),
        sa.UniqueConstraint("assembly_id", "service_case_id", name="uq_assessment_input_assembly_case"),
        sa.UniqueConstraint("service_case_id", "source_vector_digest", name="uq_assessment_input_assembly_source_vector"),
        sa.ForeignKeyConstraint(["service_case_id"], ["public.service_case.case_id"], name="fk_assessment_input_assembly_case", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["subject_member_id"], ["identity.member.member_id"], name="fk_assessment_input_assembly_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["primary_therapist_id"], ["public.therapist_profile.therapist_id"], name="fk_assessment_input_assembly_therapist", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["profile_revision_id"], ["public.health_profile_revision.profile_revision_id"], name="fk_assessment_input_assembly_profile_revision", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_version_id"], ["public.assessment_readiness_policy_version.policy_version_id"], name="fk_assessment_input_assembly_policy", ondelete="RESTRICT"),
        sa.CheckConstraint("status IN ('DATA_INSUFFICIENT','DATA_SYNC_PENDING','DISPUTED','ASSESSMENT_READY') AND required_max_fact_id>=0 AND required_max_status_event_seq>=0", name="ck_assessment_input_assembly_truth"),
        schema="public",
    )
    op.create_table(
        "assessment_input_assembly_fact",
        _uuid("assembly_id"),
        sa.Column("indicator_code", sa.String(64), nullable=False),
        _uuid("fact_ref"),
        _ts("measured_at"),
        _ts("received_at"),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("verification_state", sa.String(24), nullable=False),
        sa.Column("value_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("value_key_id", sa.String(64), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("business_day", sa.Date(), nullable=False),
        sa.Column("row_digest", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("assembly_id", "indicator_code", name="pk_assessment_input_assembly_fact"),
        sa.ForeignKeyConstraint(["assembly_id"], ["public.assessment_input_assembly.assembly_id"], name="fk_assessment_input_assembly_fact_assembly", ondelete="RESTRICT"),
        sa.CheckConstraint("source_type IN ('APP','STORE','REPORT') AND verification_state IN ('SELF_REPORTED','VERIFIED','UNKNOWN','DISPUTED')", name="ck_assessment_input_assembly_fact_truth"),
        schema="public",
    )
    op.create_table(
        "assessment_readiness_case_pointer",
        _uuid("service_case_id"),
        _uuid("current_assembly_id"),
        sa.Column("source_vector_digest", sa.LargeBinary(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("service_case_id", name="pk_assessment_readiness_case_pointer"),
        sa.ForeignKeyConstraint(["service_case_id"], ["public.service_case.case_id"], name="fk_assessment_readiness_case_pointer_case", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_assembly_id", "service_case_id"], ["public.assessment_input_assembly.assembly_id", "public.assessment_input_assembly.service_case_id"], name="fk_assessment_readiness_case_pointer_current", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint("version>=1", name="ck_assessment_readiness_case_pointer_version"),
        schema="public",
    )


def _create_owner_functions(writer: str, readiness: str, identity: str) -> None:
    op.execute(
        f'''CREATE FUNCTION public.slice4_identity_summary_source_v1(
            value_subject_member_id UUID,
            value_service_case_id UUID
        ) RETURNS TABLE(
            identity_source_kind VARCHAR,
            document_type VARCHAR,
            identity_ciphertext BYTEA,
            identity_nonce BYTEA,
            identity_key_id VARCHAR,
            birth_date_ciphertext BYTEA,
            birth_date_key_id VARCHAR,
            identity_revision_ref UUID,
            source_version BIGINT,
            tenant_public_id UUID,
            evidence_status VARCHAR,
            identity_user_ref BIGINT,
            identity_storage_version BIGINT
        )
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE source_kind_value VARCHAR(16); tenant_public_value UUID;
        BEGIN
          IF session_user <> '{identity}' THEN
            RAISE EXCEPTION 'SLICE4_IDENTITY_AUTHORITY_FORBIDDEN';
          END IF;
          SELECT g.source_kind,a.tenant_public_id
            INTO source_kind_value,tenant_public_value
            FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_application a
              ON a.tenant_internal_id=e.tenant_id AND a.status='APPROVED'
            JOIN identity.identity_subject_claim_registry g
              ON g.member_id=e.subject_member_id
            WHERE c.case_id=value_service_case_id
              AND c.status='PREPARING'
              AND e.subject_member_id=value_subject_member_id
            FOR SHARE OF c,e,a,g;
          IF NOT FOUND THEN RETURN; END IF;
          IF source_kind_value='P1' THEN
            RETURN QUERY
            SELECT 'P1'::varchar,'PRC_RESIDENT_ID'::varchar,
              s.id_card_ciphertext,s.id_card_nonce,s.encryption_key_id::varchar,
              NULL::bytea,NULL::varchar,s.submission_id,g.source_facts_version,
              tenant_public_value,'VERIFIED'::varchar,s.user_ref,s.version
            FROM identity.identity_subject_claim_registry g
            JOIN public.identity_verification_submission s
              ON s.submission_id=g.p1_submission_id AND s.user_ref=g.user_ref
            JOIN public.identity_verification_decision d
              ON d.decision_ref=g.p1_decision_ref AND d.user_ref=s.user_ref
            WHERE g.member_id=value_subject_member_id AND g.source_kind='P1'
              AND s.status='verified' AND d.outcome='verified'
              AND d.facts_version=g.source_facts_version
              AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n
                WHERE n.supersedes_ref=d.decision_ref)
            FOR SHARE OF g,s,d;
          ELSIF source_kind_value='SLICE3' THEN
            RETURN QUERY
            SELECT 'SLICE3'::varchar,r.document_type::varchar,
              r.id_ciphertext,NULL::bytea,r.id_key_id::varchar,
              r.birth_date_ciphertext,r.birth_date_key_id::varchar,r.revision_id,
              g.source_facts_version,tenant_public_value,'VERIFIED'::varchar,
              NULL::bigint,r.revision_no::bigint
            FROM public.service_case c
            JOIN public.member_identity_verification v
              ON v.verification_id=c.identity_verification_id
            JOIN public.member_identity_revision r
              ON r.verification_id=v.verification_id
             AND r.revision_id=v.current_revision_id
            JOIN public.member_identity_review_decision d
              ON d.decision_id=v.platform_decision_id
             AND d.revision_id=r.revision_id
            JOIN identity.identity_subject_claim_registry g
              ON g.member_id=v.member_id
             AND g.slice3_revision_id=r.revision_id
             AND g.slice3_decision_id=d.decision_id
            WHERE c.case_id=value_service_case_id
              AND v.member_id=value_subject_member_id
              AND v.status='APPROVED' AND d.phase='PLATFORM'
              AND d.decision='APPROVED' AND g.source_kind='SLICE3'
            FOR SHARE OF c,v,r,d,g;
          END IF;
        END; $$;'''
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.slice4_identity_summary_source_v1(UUID,UUID) FROM PUBLIC"
    )
    op.execute(
        f'''GRANT EXECUTE ON FUNCTION public.slice4_identity_summary_source_v1(UUID,UUID) TO "{identity}"'''
    )

    op.execute(
        f'''CREATE FUNCTION public.slice4_identity_summary_current_v1(
            value_subject_member_id UUID,
            value_service_case_id UUID,
            value_identity_revision_ref UUID,
            value_identity_source_version BIGINT,
            value_tenant_public_id UUID
        ) RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE identity_source_kind VARCHAR(16);
        BEGIN
          IF session_user NOT IN ('{writer}','{readiness}') THEN
            RAISE EXCEPTION 'SLICE4_IDENTITY_CURRENTNESS_FORBIDDEN';
          END IF;
          IF value_identity_source_version < 1 THEN RETURN FALSE; END IF;
          PERFORM 1 FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_application a ON a.tenant_internal_id=e.tenant_id
            WHERE c.case_id=value_service_case_id
              AND c.status='PREPARING'
              AND e.subject_member_id=value_subject_member_id
              AND a.tenant_public_id=value_tenant_public_id
              AND a.status='APPROVED'
            FOR SHARE OF c,e,a;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          SELECT g.source_kind INTO identity_source_kind
            FROM identity.identity_subject_claim_registry g
            WHERE g.member_id=value_subject_member_id
              AND g.source_facts_version=value_identity_source_version
              AND ((g.source_kind='P1' AND g.p1_submission_id=value_identity_revision_ref)
                OR (g.source_kind='SLICE3' AND g.slice3_revision_id=value_identity_revision_ref))
            FOR SHARE;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          IF identity_source_kind='P1' THEN
            PERFORM 1 FROM public.identity_verification_submission s
              JOIN identity.identity_subject_claim_registry g
                ON g.p1_submission_id=s.submission_id
              JOIN public.identity_verification_decision d
                ON d.decision_ref=g.p1_decision_ref AND d.user_ref=s.user_ref
              WHERE s.submission_id=value_identity_revision_ref
                AND s.status='verified'
                AND g.member_id=value_subject_member_id
                AND g.source_facts_version=value_identity_source_version
                AND d.facts_version=value_identity_source_version
                AND d.outcome='verified'
                AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n WHERE n.supersedes_ref=d.decision_ref)
              FOR SHARE OF s,g,d;
          ELSIF identity_source_kind='SLICE3' THEN
            PERFORM 1 FROM public.member_identity_verification v
              JOIN public.member_identity_revision r ON r.verification_id=v.verification_id
              JOIN public.member_identity_review_decision d ON d.revision_id=r.revision_id
              JOIN identity.identity_subject_claim_registry g ON g.member_id=v.member_id
              WHERE r.revision_id=value_identity_revision_ref
                AND g.source_facts_version=value_identity_source_version
                AND v.current_revision_id=r.revision_id AND d.decision='APPROVED'
                AND d.phase='PLATFORM'
              FOR SHARE OF v,r,d,g;
          ELSE RETURN FALSE;
          END IF;
          IF NOT FOUND THEN RETURN FALSE; END IF;
          RETURN TRUE;
        END; $$;'''
    )
    op.execute("REVOKE ALL ON FUNCTION public.slice4_identity_summary_current_v1(UUID,UUID,UUID,BIGINT,UUID) FROM PUBLIC")
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.slice4_identity_summary_current_v1(UUID,UUID,UUID,BIGINT,UUID) TO "{writer}", "{readiness}"''')

    op.execute(
        f'''CREATE FUNCTION public.slice4_health_profile_root_create_v1(
          value_actor_user_id BIGINT, value_actor_context VARCHAR, value_subject_member_id UUID,
          value_service_case_id UUID, value_enrollment_id UUID,
          requested_profile_public_id UUID, requested_profile_revision_id UUID,
          value_tenant_public_id UUID, value_identity_revision_ref UUID,
          value_identity_source_version BIGINT, value_snapshot_ciphertext BYTEA,
          value_snapshot_key_id VARCHAR, value_snapshot_digest BYTEA, value_digest_key_id VARCHAR,
          value_reconfirmed_at TIMESTAMPTZ, value_source_type VARCHAR, value_changed_fields JSONB,
          value_idempotency_key UUID, value_request_digest BYTEA, value_expected_postimage_digest BYTEA
        ) RETURNS TABLE(profile_public_id UUID, subject_member_id UUID,
          subject_user_id BIGINT, current_revision_id UUID, version BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE event_id UUID; audit_id UUID; receipt_id UUID; resolved_user_id BIGINT;
          resolved_source_kind VARCHAR(16);
          stored_request_digest BYTEA; stored_postimage_digest BYTEA;
          existing_profile UUID; existing_revision UUID;
          existing_user BIGINT; existing_version BIGINT;
        BEGIN
          IF session_user <> '{writer}' OR value_actor_context NOT IN ('SELF','PROXY')
             OR value_identity_source_version<1 OR value_expected_postimage_digest IS NULL THEN
            RAISE EXCEPTION 'SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_subject_member_id::text,0));
          IF value_actor_context='SELF' THEN
            SELECT l.user_ref INTO resolved_user_id
              FROM identity.user_member_self_link l
              JOIN public.service_case c ON c.case_id=value_service_case_id
              JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
              WHERE l.member_id=value_subject_member_id
                AND l.user_ref=value_actor_user_id
                AND e.enrollment_id=value_enrollment_id
                AND e.subject_member_id=value_subject_member_id
                AND e.mode='SELF' AND c.status='PREPARING'
              FOR SHARE OF l,c,e;
          ELSE
            SELECT NULL::bigint INTO resolved_user_id
              FROM public.service_case c
              JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
              JOIN public.proxy_grant g ON g.enrollment_id=e.enrollment_id
              JOIN identity.user_member_self_link l ON l.member_id=g.proxy_member_id
              WHERE c.case_id=value_service_case_id AND e.enrollment_id=value_enrollment_id
                AND e.subject_member_id=value_subject_member_id
                AND e.mode='PROXY_ELDER' AND l.user_ref=value_actor_user_id
                AND g.principal_member_id=value_subject_member_id
                AND g.status='ACTIVE'
                AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp())
                AND g.permission_codes ? 'DAILY_INPUT'
                AND c.status='PREPARING'
              FOR SHARE OF c,e,g,l;
          END IF;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN'; END IF;
          SELECT i.request_digest,i.postimage_digest
            INTO stored_request_digest,stored_postimage_digest
            FROM public.slice4_idempotency i
            WHERE i.operation='CREATE_PROFILE_ROOT'
              AND i.scope_ref=value_subject_member_id
              AND i.idempotency_key=value_idempotency_key;
          IF FOUND THEN
            IF stored_request_digest IS DISTINCT FROM value_request_digest THEN
              RAISE EXCEPTION 'SLICE4_IDEMPOTENCY_CONFLICT';
            END IF;
            IF stored_postimage_digest IS DISTINCT FROM value_expected_postimage_digest THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            SELECT h.profile_public_id,r.profile_revision_id,h.user_id,1::bigint
              INTO existing_profile,existing_revision,existing_user,existing_version
              FROM public.health_profile h
              JOIN public.health_profile_revision r
                ON r.subject_member_id=h.subject_member_id AND r.revision_no=1
              WHERE h.subject_member_id=value_subject_member_id FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN'; END IF;
            IF (SELECT count(*) FROM public.slice4_audit a
                  WHERE a.aggregate_ref=existing_profile
                    AND a.event_type='HEALTH_PROFILE_ROOT_CREATED')<>1
               OR (SELECT count(*) FROM public.slice4_outbox o
                  WHERE o.aggregate_ref=existing_profile
                    AND o.event_type='HEALTH_PROFILE_ROOT_CREATED')<>1 THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            RETURN QUERY SELECT existing_profile,value_subject_member_id,existing_user,
              existing_revision,existing_version;
            RETURN;
          END IF;
          IF EXISTS(SELECT 1 FROM public.health_profile h WHERE h.subject_member_id=value_subject_member_id) THEN
            RAISE EXCEPTION 'SLICE4_VERSION_CONFLICT';
          END IF;
          IF NOT public.slice4_identity_summary_current_v1(value_subject_member_id,value_service_case_id,
             value_identity_revision_ref,value_identity_source_version,value_tenant_public_id) THEN
            RAISE EXCEPTION 'SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN';
          END IF;
          SELECT g.source_kind INTO resolved_source_kind
            FROM identity.identity_subject_claim_registry g
            WHERE g.member_id=value_subject_member_id
              AND g.source_facts_version=value_identity_source_version
              AND ((g.source_kind='P1' AND g.p1_submission_id=value_identity_revision_ref)
                OR (g.source_kind='SLICE3' AND g.slice3_revision_id=value_identity_revision_ref))
            FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_HEALTH_PROFILE_ROOT_FORBIDDEN'; END IF;
          event_id := gen_random_uuid(); audit_id := gen_random_uuid(); receipt_id := gen_random_uuid();
          INSERT INTO public.health_profile_revision(profile_revision_id,subject_member_id,
            subject_user_id,revision_no,tenant_public_id,identity_source_kind,
            identity_revision_ref,identity_source_version,snapshot_ciphertext,snapshot_key_id,
            reconfirmed_at,source_type,changed_fields,supersedes_revision_id,actor_user_id,
            actor_type,snapshot_digest,digest_key_id,created_at)
          VALUES(requested_profile_revision_id,value_subject_member_id,resolved_user_id,1,
            value_tenant_public_id,resolved_source_kind,
            value_identity_revision_ref,value_identity_source_version,value_snapshot_ciphertext,value_snapshot_key_id,
            value_reconfirmed_at,value_source_type,value_changed_fields,NULL,value_actor_user_id,value_actor_context,
            value_snapshot_digest,value_digest_key_id,clock_timestamp());
          INSERT INTO public.health_profile(profile_public_id,subject_member_id,current_revision_id,
            version,user_id,gender,birth_date,height,weight,created_at,updated_at)
          VALUES(requested_profile_public_id,value_subject_member_id,requested_profile_revision_id,
            1,resolved_user_id,NULL,NULL,NULL,NULL,clock_timestamp(),clock_timestamp());
          INSERT INTO public.slice4_audit(audit_id,event_type,aggregate_ref,actor_user_id,event_digest,digest_key_id,created_at)
          VALUES(audit_id,'HEALTH_PROFILE_ROOT_CREATED',requested_profile_public_id,value_actor_user_id,value_snapshot_digest,value_digest_key_id,clock_timestamp());
          INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,payload_digest,payload_json,status,attempts,created_at)
          VALUES(event_id,'HEALTH_PROFILE',requested_profile_public_id,'HEALTH_PROFILE_ROOT_CREATED',value_snapshot_digest,
            jsonb_build_object('profile_public_id',requested_profile_public_id,
              'service_case_id',value_service_case_id),'PENDING',0,clock_timestamp());
          INSERT INTO public.slice4_idempotency(receipt_id,operation,scope_ref,idempotency_key,
            request_digest,postimage_digest,created_at)
          -- postimage_digest=expected_postimage_digest is the prewrite plan mapping.
          VALUES(receipt_id,'CREATE_PROFILE_ROOT',value_subject_member_id,value_idempotency_key,
            value_request_digest,value_expected_postimage_digest,clock_timestamp());
          RETURN QUERY SELECT requested_profile_public_id,value_subject_member_id,resolved_user_id,
            requested_profile_revision_id,1::BIGINT;
        END; $$;'''
    )
    op.execute("REVOKE ALL ON FUNCTION public.slice4_health_profile_root_create_v1(BIGINT,VARCHAR,UUID,UUID,UUID,UUID,UUID,UUID,UUID,BIGINT,BYTEA,VARCHAR,BYTEA,VARCHAR,TIMESTAMPTZ,VARCHAR,JSONB,UUID,BYTEA,BYTEA) FROM PUBLIC")
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.slice4_health_profile_root_create_v1(BIGINT,VARCHAR,UUID,UUID,UUID,UUID,UUID,UUID,UUID,BIGINT,BYTEA,VARCHAR,BYTEA,VARCHAR,TIMESTAMPTZ,VARCHAR,JSONB,UUID,BYTEA,BYTEA) TO "{writer}"''')

    op.execute(
        f'''CREATE FUNCTION public.slice4_detection_report_create_v1(
          value_actor_user_id BIGINT, value_actor_context VARCHAR,
          value_subject_member_id UUID, value_service_case_id UUID,
          value_enrollment_id UUID, value_requested_report_id UUID,
          value_report_type VARCHAR, value_measured_at TIMESTAMPTZ,
          value_source_type VARCHAR, value_private_file_ids UUID[],
          value_idempotency_key UUID, value_request_digest BYTEA,
          value_expected_postimage_digest BYTEA
        ) RETURNS TABLE(report_id UUID,version BIGINT,received_at TIMESTAMPTZ,
          report_status VARCHAR,attachment_count SMALLINT)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE resolved_user_id BIGINT; resolved_tenant_id BIGINT;
          resolved_received_at TIMESTAMPTZ; existing_report UUID;
          stored_request BYTEA; stored_postimage BYTEA;
          audit_id UUID; event_id UUID; receipt_id UUID;
        BEGIN
          IF session_user <> '{writer}' OR value_actor_context NOT IN ('SELF','PROXY')
             OR value_source_type <> 'APP'
             OR value_report_type NOT IN ('LAB_REPORT','IMAGING_REPORT','PHYSICAL_EXAM','OTHER')
             OR value_measured_at IS NULL OR value_measured_at > clock_timestamp()+interval '5 minutes'
             OR value_private_file_ids IS NULL
             OR cardinality(value_private_file_ids) NOT BETWEEN 1 AND 10
             OR (SELECT count(*) FROM (SELECT DISTINCT x FROM unnest(value_private_file_ids) x) q)
                <> cardinality(value_private_file_ids)
             OR value_request_digest IS NULL OR value_expected_postimage_digest IS NULL THEN
            RAISE EXCEPTION 'SLICE4_DETECTION_REPORT_INVALID';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_subject_member_id::text,0));
          IF value_actor_context='SELF' THEN
            SELECT l.user_ref,c.tenant_id INTO resolved_user_id,resolved_tenant_id
              FROM public.service_case c
              JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
              JOIN identity.user_member_self_link l ON l.member_id=e.subject_member_id
              WHERE c.case_id=value_service_case_id AND e.enrollment_id=value_enrollment_id
                AND e.subject_member_id=value_subject_member_id AND e.mode='SELF'
                AND l.user_ref=value_actor_user_id AND c.status='PREPARING'
              FOR SHARE OF c,e,l;
          ELSE
            SELECT NULL::bigint,c.tenant_id INTO resolved_user_id,resolved_tenant_id
              FROM public.service_case c
              JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
              JOIN public.proxy_grant g ON g.enrollment_id=e.enrollment_id
              JOIN identity.user_member_self_link l ON l.member_id=g.proxy_member_id
              WHERE c.case_id=value_service_case_id AND e.enrollment_id=value_enrollment_id
                AND e.subject_member_id=value_subject_member_id AND e.mode='PROXY_ELDER'
                AND l.user_ref=value_actor_user_id AND g.principal_member_id=value_subject_member_id
                AND g.status='ACTIVE' AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp())
                AND g.permission_codes ? 'REPORT_UPLOAD' AND c.status='PREPARING'
              FOR SHARE OF c,e,g,l;
          END IF;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_DETECTION_REPORT_FORBIDDEN'; END IF;
          SELECT i.request_digest,i.postimage_digest INTO stored_request,stored_postimage
            FROM public.slice4_idempotency i
            WHERE i.operation='CREATE_DETECTION_REPORT'
              AND i.scope_ref=value_subject_member_id
              AND i.idempotency_key=value_idempotency_key FOR SHARE;
          IF FOUND THEN
            IF stored_request IS DISTINCT FROM value_request_digest THEN
              RAISE EXCEPTION 'SLICE4_IDEMPOTENCY_CONFLICT';
            END IF;
            IF stored_postimage IS DISTINCT FROM value_expected_postimage_digest THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            SELECT d.report_id,d.received_at INTO existing_report,resolved_received_at
              FROM public.detection_report d
              WHERE d.report_id=value_requested_report_id
                AND d.subject_member_id=value_subject_member_id FOR SHARE;
            IF NOT FOUND
               OR (SELECT count(*) FROM public.detection_report_attachment a
                   WHERE a.report_id=value_requested_report_id)<>cardinality(value_private_file_ids)
               OR (SELECT count(*) FROM public.slice4_audit a
                   WHERE a.aggregate_ref=value_requested_report_id
                     AND a.event_type='DETECTION_REPORT_CREATED')<>1
               OR (SELECT count(*) FROM public.slice4_outbox o
                   WHERE o.aggregate_ref=value_requested_report_id
                     AND o.event_type='DETECTION_REPORT_CREATED')<>1 THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            RETURN QUERY SELECT existing_report,1::bigint,resolved_received_at,
              'CLEAN'::varchar,cardinality(value_private_file_ids)::smallint;
            RETURN;
          END IF;
          PERFORM 1 FROM public.private_file f
            WHERE f.file_id=ANY(value_private_file_ids)
            ORDER BY f.file_id FOR UPDATE;
          IF (SELECT count(*) FROM public.private_file f
                WHERE f.file_id=ANY(value_private_file_ids)
                  AND f.owner_user_id=value_actor_user_id
                  AND f.purpose='DETECTION_REPORT' AND f.status='CLEAN'
                  AND f.bound_application_id IS NULL)
             <> cardinality(value_private_file_ids)
             OR EXISTS(SELECT 1 FROM public.detection_report_attachment a
                       WHERE a.private_file_id=ANY(value_private_file_ids)) THEN
            RAISE EXCEPTION 'SLICE4_PRIVATE_FILE_BIND_CONFLICT';
          END IF;
          resolved_received_at := clock_timestamp();
          audit_id:=gen_random_uuid(); event_id:=gen_random_uuid(); receipt_id:=gen_random_uuid();
          INSERT INTO public.detection_report(user_id,store_id,report_type,detection_time,
            view_status,summary,report_schema_version,report_data,created_at,report_id,
            subject_member_id,tenant_id,service_case_id,schema_version,source_type,
            measured_at,received_at,report_status,version,supersedes_report_id,created_by)
          VALUES(resolved_user_id,resolved_tenant_id,value_report_type,value_measured_at,
            'unread',NULL,1,'{{}}'::jsonb,resolved_received_at,value_requested_report_id,
            value_subject_member_id,resolved_tenant_id,value_service_case_id,1,value_source_type,
            value_measured_at,resolved_received_at,'CLEAN',1,NULL,value_actor_user_id);
          INSERT INTO public.detection_report_attachment(
            attachment_id,report_id,private_file_id,position,created_at)
          SELECT gen_random_uuid(),value_requested_report_id,x.file_id,
            row_number() OVER (ORDER BY x.file_id),resolved_received_at
            FROM unnest(value_private_file_ids) AS x(file_id)
            ORDER BY x.file_id;
          INSERT INTO public.slice4_audit(audit_id,event_type,aggregate_ref,actor_user_id,
            event_digest,digest_key_id,created_at)
          VALUES(audit_id,'DETECTION_REPORT_CREATED',value_requested_report_id,value_actor_user_id,
            value_expected_postimage_digest,'slice4-report-v1',resolved_received_at);
          INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,
            payload_digest,payload_json,status,attempts,created_at)
          VALUES(event_id,'DETECTION_REPORT',value_requested_report_id,'DETECTION_REPORT_CREATED',
            value_expected_postimage_digest,jsonb_build_object('report_id',value_requested_report_id,
              'service_case_id',value_service_case_id),
            'PENDING',0,resolved_received_at);
          INSERT INTO public.slice4_idempotency(receipt_id,operation,scope_ref,idempotency_key,
            request_digest,postimage_digest,created_at)
          VALUES(receipt_id,'CREATE_DETECTION_REPORT',value_subject_member_id,value_idempotency_key,
            value_request_digest,value_expected_postimage_digest,resolved_received_at);
          RETURN QUERY SELECT value_requested_report_id,1::bigint,resolved_received_at,
            'CLEAN'::varchar,cardinality(value_private_file_ids)::smallint;
        END; $$;'''
    )
    report_signature = "BIGINT,VARCHAR,UUID,UUID,UUID,UUID,VARCHAR,TIMESTAMPTZ,VARCHAR,UUID[],UUID,BYTEA,BYTEA"
    op.execute(f"REVOKE ALL ON FUNCTION public.slice4_detection_report_create_v1({report_signature}) FROM PUBLIC")
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.slice4_detection_report_create_v1({report_signature}) TO "{writer}"''')

    health_fact_writer = os.environ["KG_HEALTH_FACT_WRITER_ROLE"]
    op.execute(
        f'''CREATE FUNCTION public.slice4_health_fact_state_transition_v1(
          value_fact_ref UUID, value_target_state VARCHAR, value_expected_state VARCHAR,
          value_actor_user_id BIGINT, value_service_case_id UUID,
          value_expected_version BIGINT, value_reason_code VARCHAR,
          value_event_digest VARCHAR
        ) RETURNS TABLE(fact_ref UUID,state VARCHAR,event_no BIGINT,status_event_seq BIGINT)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE fact_internal_id BIGINT; predecessor_fact_id BIGINT;
          current_state VARCHAR; current_no BIGINT;
          current_event UUID; next_event UUID; created TIMESTAMPTZ;
          replay_event UUID; transition_key UUID; digest_bytes BYTEA;
        BEGIN
          IF session_user <> '{health_fact_writer}'
             OR value_target_state NOT IN ('SELF_REPORTED','UNKNOWN','VERIFIED','DISPUTED')
             OR value_expected_state NOT IN ('NONE','SELF_REPORTED','UNKNOWN','VERIFIED','DISPUTED')
             OR value_expected_version<0 OR value_reason_code !~ '^[A-Z0-9_]+$'
             OR value_event_digest !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'SLICE4_HEALTH_FACT_STATE_INVALID';
          END IF;
          digest_bytes:=decode(value_event_digest,'hex');
          transition_key:=(substring(value_event_digest,1,8)||'-'||
            substring(value_event_digest,9,4)||'-'||substring(value_event_digest,13,4)||'-'||
            substring(value_event_digest,17,4)||'-'||substring(value_event_digest,21,12))::uuid;
          SELECT e.status_event_id INTO replay_event
            FROM public.slice4_idempotency i
            JOIN public.canonical_health_fact f ON f.fact_ref=i.scope_ref
            JOIN public.health_fact_status_event e ON e.fact_id=f.id
              AND e.event_digest=i.postimage_digest AND e.state=value_target_state
            WHERE i.operation='TRANSITION_HEALTH_FACT_STATE'
              AND i.scope_ref=value_fact_ref AND i.idempotency_key=transition_key
              AND i.request_digest=digest_bytes AND i.postimage_digest=digest_bytes
            FOR SHARE OF i,f,e;
          IF FOUND THEN
            IF (SELECT count(*) FROM public.slice4_audit a
                  WHERE a.aggregate_ref=value_fact_ref
                    AND a.event_type='HEALTH_FACT_STATE_CHANGED'
                    AND a.event_digest=digest_bytes)<>1
               OR (SELECT count(*) FROM public.slice4_outbox o
                  WHERE o.aggregate_ref=value_fact_ref
                    AND o.event_type='HEALTH_FACT_STATE_CHANGED'
                    AND o.payload_digest=digest_bytes)<>1 THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            RETURN QUERY SELECT value_fact_ref,e.state,e.event_no,e.status_event_seq
              FROM public.health_fact_status_event e WHERE e.status_event_id=replay_event;
            RETURN;
          END IF;
          SELECT f.id INTO fact_internal_id FROM public.canonical_health_fact f
            WHERE f.fact_ref=value_fact_ref AND f.catalog_version=2 FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_HEALTH_FACT_NOT_FOUND'; END IF;
          SELECT e.state,e.event_no,e.status_event_id
            INTO current_state,current_no,current_event
            FROM public.health_fact_status_event e
            WHERE e.fact_id=fact_internal_id ORDER BY e.event_no DESC LIMIT 1 FOR UPDATE;
          IF value_expected_state='NONE' THEN
            IF FOUND OR value_expected_version<>0
               OR value_target_state NOT IN ('SELF_REPORTED','UNKNOWN')
               OR value_reason_code<>'FACT_CREATED' THEN
              RAISE EXCEPTION 'SLICE4_HEALTH_FACT_STATE_CONFLICT';
            END IF;
            SELECT f.supersedes_fact_id,
              CASE WHEN f.supersedes_fact_id IS NOT NULL OR f.source_type='APP'
                THEN 'SELF_REPORTED' ELSE 'UNKNOWN' END
              INTO predecessor_fact_id,current_state FROM public.canonical_health_fact f
              WHERE f.id=fact_internal_id;
            IF current_state IS DISTINCT FROM value_target_state THEN
              RAISE EXCEPTION 'SLICE4_HEALTH_FACT_STATE_CONFLICT';
            END IF;
            IF predecessor_fact_id IS NOT NULL THEN
              PERFORM 1 FROM public.canonical_health_fact p
                JOIN public.health_fact_status_event e ON e.fact_id=p.id
                WHERE p.id=predecessor_fact_id
                  AND e.event_no=(SELECT max(x.event_no)
                    FROM public.health_fact_status_event x WHERE x.fact_id=p.id)
                  AND e.state='DISPUTED'
                  AND p.subject_member_id=(SELECT f.subject_member_id
                    FROM public.canonical_health_fact f WHERE f.id=fact_internal_id)
                  AND p.indicator_code=(SELECT f.indicator_code
                    FROM public.canonical_health_fact f WHERE f.id=fact_internal_id)
                FOR UPDATE OF p,e;
              IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_HEALTH_FACT_CORRECTION_CONFLICT'; END IF;
            END IF;
            PERFORM 1 FROM public.service_case c
              WHERE c.case_id=value_service_case_id AND c.status='PREPARING'
                AND c.subject_member_id=(SELECT f.subject_member_id
                  FROM public.canonical_health_fact f WHERE f.id=fact_internal_id)
                AND (
                  EXISTS(SELECT 1 FROM identity.user_member_self_link l
                    JOIN public."user" u ON u.id=l.user_ref
                    WHERE l.member_id=c.subject_member_id AND l.user_ref=value_actor_user_id
                      AND u.status='active' AND u.role='member')
                  OR EXISTS(SELECT 1 FROM public.service_enrollment e
                    JOIN public.proxy_grant g ON g.enrollment_id=e.enrollment_id
                    JOIN identity.user_member_self_link l ON l.member_id=g.proxy_member_id
                    WHERE e.service_case_id=c.case_id AND l.user_ref=value_actor_user_id
                      AND g.status='ACTIVE'
                      AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp())
                      AND g.permission_codes ? 'DAILY_INPUT')
                  OR EXISTS(SELECT 1 FROM public.therapist_profile p
                    WHERE p.therapist_id=c.primary_therapist_id
                      AND p.user_id=value_actor_user_id AND p.status='APPROVED_ACTIVE')
                )
              FOR SHARE OF c;
            current_no:=0; current_event:=NULL;
          ELSE
            IF NOT FOUND OR current_state IS DISTINCT FROM value_expected_state
               OR current_no IS DISTINCT FROM value_expected_version
               OR value_target_state NOT IN ('VERIFIED','DISPUTED') THEN
              RAISE EXCEPTION 'SLICE4_HEALTH_FACT_STATE_CONFLICT';
            END IF;
            PERFORM 1 FROM public.service_case c
              JOIN public.therapist_profile p ON p.therapist_id=c.primary_therapist_id
              WHERE c.case_id=value_service_case_id AND c.status='PREPARING'
                AND p.user_id=value_actor_user_id AND p.status='APPROVED_ACTIVE'
              FOR SHARE OF c,p;
          END IF;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_HEALTH_FACT_STATE_FORBIDDEN'; END IF;
          next_event:=gen_random_uuid(); created:=clock_timestamp();
          INSERT INTO public.health_fact_status_event(status_event_id,fact_id,event_no,
            predecessor_event_id,state,reason_code,actor_user_id,service_case_id,
            event_digest,digest_key_id,created_at)
          VALUES(next_event,fact_internal_id,current_no+1,current_event,value_target_state,
            value_reason_code,value_actor_user_id,value_service_case_id,
            decode(value_event_digest,'hex'),'health-fact-v2',created);
          INSERT INTO public.slice4_audit(audit_id,event_type,aggregate_ref,actor_user_id,
            event_digest,digest_key_id,created_at)
          VALUES(gen_random_uuid(),'HEALTH_FACT_STATE_CHANGED',value_fact_ref,value_actor_user_id,
            decode(value_event_digest,'hex'),'health-fact-v2',created);
          INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,
            payload_digest,payload_json,status,attempts,created_at)
          VALUES(gen_random_uuid(),'HEALTH_FACT',value_fact_ref,'HEALTH_FACT_STATE_CHANGED',
            decode(value_event_digest,'hex'),jsonb_build_object('fact_ref',value_fact_ref,
              'service_case_id',value_service_case_id),
            'PENDING',0,created);
          INSERT INTO public.slice4_idempotency(receipt_id,operation,scope_ref,idempotency_key,
            request_digest,postimage_digest,created_at)
          VALUES(gen_random_uuid(),'TRANSITION_HEALTH_FACT_STATE',value_fact_ref,
            transition_key,digest_bytes,digest_bytes,created);
          RETURN QUERY SELECT value_fact_ref,value_target_state,current_no+1,
            e.status_event_seq FROM public.health_fact_status_event e
            WHERE e.status_event_id=next_event;
        END; $$;'''
    )
    state_signature = "UUID,VARCHAR,VARCHAR,BIGINT,UUID,BIGINT,VARCHAR,VARCHAR"
    op.execute(f"REVOKE ALL ON FUNCTION public.slice4_health_fact_state_transition_v1({state_signature}) FROM PUBLIC")
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.slice4_health_fact_state_transition_v1({state_signature}) TO "{health_fact_writer}"''')

    op.execute(
        f'''CREATE FUNCTION public.slice4_assessment_assembly_write_v1(
          value_service_case_id UUID, value_requested_assembly_id UUID,
          value_subject_member_id UUID, value_tenant_public_id UUID,
          value_primary_therapist_id UUID, value_profile_revision_id UUID,
          value_policy_version_id UUID, value_projection_version SMALLINT,
          value_rule_version VARCHAR, value_source_snapshot VARCHAR,
          value_source_vector JSONB, value_encrypted_fact_rows JSONB,
          value_readiness_status VARCHAR, value_reason_codes JSONB,
          value_idempotency_key UUID, value_request_digest BYTEA,
          value_expected_postimage_digest BYTEA
        ) RETURNS TABLE(assembly_id UUID,service_case_id UUID,
          readiness_status VARCHAR,pointer_version BIGINT,generated_at TIMESTAMPTZ)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE resolved_tenant_id BIGINT; next_pointer_version BIGINT;
          created TIMESTAMPTZ; stored_request BYTEA; stored_postimage BYTEA;
          resolved_identity_ref UUID; resolved_identity_version BIGINT;
          resolved_digest_key VARCHAR;
        BEGIN
          IF session_user <> '{readiness}'
             OR value_projection_version<2
             OR value_readiness_status NOT IN ('DATA_INSUFFICIENT','DATA_SYNC_PENDING','DISPUTED','ASSESSMENT_READY')
             OR jsonb_typeof(value_source_vector)<>'object'
             OR jsonb_typeof(value_encrypted_fact_rows)<>'array'
             OR jsonb_typeof(value_reason_codes)<>'array'
             OR value_request_digest IS NULL OR value_expected_postimage_digest IS NULL THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID';
          END IF;
          IF EXISTS(SELECT 1 FROM jsonb_object_keys(value_source_vector) k
                    WHERE k NOT IN ('consent_version_ids','resolved_generation_id',
                      'required_max_fact_id','required_max_status_event_seq','missing_codes',
                      'expired_codes','disputed_codes','source_vector_digest','source_digest',
                      'assembly_digest','digest_key_id','generated_at','audit_id',
                      'event_id','receipt_id'))
             OR (SELECT count(*) FROM jsonb_object_keys(value_source_vector))<>15 THEN
            RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID';
          END IF;
          IF EXISTS(
            SELECT 1 FROM jsonb_array_elements(value_encrypted_fact_rows) item
            WHERE jsonb_typeof(item)<>'object'
               OR (SELECT count(*) FROM jsonb_object_keys(item))<>10
               OR EXISTS(SELECT 1 FROM jsonb_object_keys(item) k WHERE k NOT IN
                 ('indicator_code','fact_ref','measured_at','received_at','source_type',
                  'verification_state','value_ciphertext','value_key_id','unit','row_digest'))
          ) THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_service_case_id::text,0));
          SELECT c.tenant_id INTO resolved_tenant_id
            FROM public.service_case c
            JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id
            JOIN public.institution_application a ON a.tenant_internal_id=c.tenant_id
            JOIN public.therapist_profile p ON p.therapist_id=c.primary_therapist_id
            WHERE c.case_id=value_service_case_id AND c.subject_member_id=value_subject_member_id
              AND c.primary_therapist_id=value_primary_therapist_id AND c.status='PREPARING'
              AND e.status='CASE_CREATED' AND a.tenant_public_id=value_tenant_public_id
              AND a.status='APPROVED' AND p.status='APPROVED_ACTIVE'
            FOR SHARE OF c,e,a,p;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID'; END IF;
          IF value_profile_revision_id IS NULL THEN
            IF value_readiness_status<>'DATA_INSUFFICIENT'
               OR NOT (value_reason_codes ? 'PROFILE_MISSING_OR_STALE') THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
            END IF;
            resolved_digest_key:=value_source_vector->>'digest_key_id';
          ELSE
            SELECT r.identity_revision_ref,r.identity_source_version,r.digest_key_id
              INTO resolved_identity_ref,resolved_identity_version,resolved_digest_key
              FROM public.health_profile_revision r
              WHERE r.profile_revision_id=value_profile_revision_id
                AND r.subject_member_id=value_subject_member_id
                AND r.tenant_public_id=value_tenant_public_id FOR SHARE;
            IF NOT FOUND OR NOT public.slice4_identity_summary_current_v1(
                 value_subject_member_id,value_service_case_id,resolved_identity_ref,
                 resolved_identity_version,value_tenant_public_id) THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_CURRENTNESS_INVALID';
            END IF;
          END IF;
          IF value_policy_version_id IS NULL THEN
            IF value_readiness_status<>'DATA_INSUFFICIENT'
               OR NOT (value_reason_codes ? 'POLICY_UNAVAILABLE') THEN
              RAISE EXCEPTION 'SLICE4_ASSEMBLY_POLICY_INVALID';
            END IF;
          ELSE
            PERFORM 1 FROM public.assessment_readiness_policy_version p
              WHERE p.policy_version_id=value_policy_version_id AND p.status='PUBLISHED'
                AND p.professionally_approved AND p.projection_version=value_projection_version
                AND p.rule_version=value_rule_version
                AND p.effective_from<=clock_timestamp()
                AND (p.retired_at IS NULL OR p.retired_at>clock_timestamp()) FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_POLICY_INVALID'; END IF;
          END IF;
          SELECT i.request_digest,i.postimage_digest INTO stored_request,stored_postimage
            FROM public.slice4_idempotency i
            WHERE i.operation='WRITE_ASSESSMENT_ASSEMBLY'
              AND i.scope_ref=value_service_case_id AND i.idempotency_key=value_idempotency_key
            FOR SHARE;
          IF FOUND THEN
            IF stored_request IS DISTINCT FROM value_request_digest THEN
              RAISE EXCEPTION 'SLICE4_IDEMPOTENCY_CONFLICT';
            END IF;
            IF stored_postimage IS DISTINCT FROM value_expected_postimage_digest
               OR NOT EXISTS(SELECT 1 FROM public.assessment_input_assembly a
                     WHERE a.assembly_id=value_requested_assembly_id
                       AND a.service_case_id=value_service_case_id)
               OR (SELECT count(*) FROM public.slice4_audit a
                     WHERE a.aggregate_ref=value_requested_assembly_id
                       AND a.event_type='ASSESSMENT_ASSEMBLY_WRITTEN')<>1
               OR (SELECT count(*) FROM public.slice4_outbox o
                     WHERE o.aggregate_ref=value_requested_assembly_id
                       AND o.event_type='ASSESSMENT_ASSEMBLY_WRITTEN')<>1 THEN
              RAISE EXCEPTION 'SLICE4_COMMIT_OUTCOME_UNKNOWN';
            END IF;
            RETURN QUERY SELECT a.assembly_id,a.service_case_id,a.status,p.version,a.generated_at
              FROM public.assessment_input_assembly a
              JOIN public.assessment_readiness_case_pointer p
                ON p.current_assembly_id=a.assembly_id AND p.service_case_id=a.service_case_id
              WHERE a.assembly_id=value_requested_assembly_id;
            RETURN;
          END IF;
          SELECT COALESCE(p.version,0)+1 INTO next_pointer_version
            FROM public.assessment_readiness_case_pointer p
            WHERE p.service_case_id=value_service_case_id FOR UPDATE;
          IF NOT FOUND THEN next_pointer_version:=1; END IF;
          created:=(value_source_vector->>'generated_at')::timestamptz;
          IF created IS NULL THEN RAISE EXCEPTION 'SLICE4_ASSEMBLY_INVALID'; END IF;
          INSERT INTO public.assessment_input_assembly(assembly_id,service_case_id,
            subject_member_id,tenant_id,primary_therapist_id,profile_revision_id,
            consent_version_ids,policy_version_id,projection_version,rule_version,
            resolved_generation_id,required_max_fact_id,required_max_status_event_seq,
            source_snapshot,status,reason_codes,missing_codes,expired_codes,disputed_codes,
            source_vector_digest,source_digest,assembly_digest,digest_key_id,generated_at)
          VALUES(value_requested_assembly_id,value_service_case_id,value_subject_member_id,
            resolved_tenant_id,value_primary_therapist_id,value_profile_revision_id,
            value_source_vector->'consent_version_ids',value_policy_version_id,
            value_projection_version,value_rule_version,
            NULLIF(value_source_vector->>'resolved_generation_id','')::bigint,
            (value_source_vector->>'required_max_fact_id')::bigint,
            (value_source_vector->>'required_max_status_event_seq')::bigint,
            value_source_snapshot,value_readiness_status,value_reason_codes,
            value_source_vector->'missing_codes',value_source_vector->'expired_codes',
            value_source_vector->'disputed_codes',
            decode(value_source_vector->>'source_vector_digest','hex'),
            decode(value_source_vector->>'source_digest','hex'),
            decode(value_source_vector->>'assembly_digest','hex'),
            value_source_vector->>'digest_key_id',created);
          INSERT INTO public.assessment_input_assembly_fact(assembly_id,indicator_code,
            fact_ref,measured_at,received_at,source_type,verification_state,
            value_ciphertext,value_key_id,unit,business_day,row_digest)
          SELECT value_requested_assembly_id,x.indicator_code,x.fact_ref,x.measured_at,
            x.received_at,x.source_type,x.verification_state,decode(x.value_ciphertext,'hex'),
            x.value_key_id,x.unit,(x.measured_at AT TIME ZONE 'Asia/Shanghai')::date,
            decode(x.row_digest,'hex')
          FROM jsonb_to_recordset(value_encrypted_fact_rows) AS x(
            indicator_code varchar,fact_ref uuid,measured_at timestamptz,received_at timestamptz,
            source_type varchar,verification_state varchar,value_ciphertext varchar,
            value_key_id varchar,unit varchar,row_digest varchar)
          ORDER BY x.indicator_code;
          INSERT INTO public.assessment_readiness_case_pointer(service_case_id,
            current_assembly_id,source_vector_digest,version,updated_at)
          VALUES(value_service_case_id,value_requested_assembly_id,
            decode(value_source_vector->>'source_vector_digest','hex'),next_pointer_version,created)
          ON CONFLICT ON CONSTRAINT pk_assessment_readiness_case_pointer DO UPDATE SET
            current_assembly_id=EXCLUDED.current_assembly_id,
            source_vector_digest=EXCLUDED.source_vector_digest,
            version=EXCLUDED.version,updated_at=EXCLUDED.updated_at;
          INSERT INTO public.slice4_audit(audit_id,event_type,aggregate_ref,actor_user_id,
            event_digest,digest_key_id,created_at)
          SELECT (value_source_vector->>'audit_id')::uuid,'ASSESSMENT_ASSEMBLY_WRITTEN',value_requested_assembly_id,
            p.user_id,value_expected_postimage_digest,resolved_digest_key,created
            FROM public.therapist_profile p WHERE p.therapist_id=value_primary_therapist_id;
          INSERT INTO public.slice4_outbox(event_id,aggregate_type,aggregate_ref,event_type,
            payload_digest,payload_json,status,attempts,created_at)
          VALUES((value_source_vector->>'event_id')::uuid,'ASSESSMENT_ASSEMBLY',value_requested_assembly_id,
            'ASSESSMENT_ASSEMBLY_WRITTEN',value_expected_postimage_digest,
            jsonb_build_object('assembly_id',value_requested_assembly_id,
              'service_case_id',value_service_case_id),'PENDING',0,created);
          INSERT INTO public.slice4_idempotency(receipt_id,operation,scope_ref,idempotency_key,
            request_digest,postimage_digest,created_at)
          VALUES((value_source_vector->>'receipt_id')::uuid,'WRITE_ASSESSMENT_ASSEMBLY',value_service_case_id,
            value_idempotency_key,value_request_digest,value_expected_postimage_digest,created);
          RETURN QUERY SELECT value_requested_assembly_id,value_service_case_id,
            value_readiness_status,next_pointer_version,created;
        END; $$;'''
    )
    assembly_signature = "UUID,UUID,UUID,UUID,UUID,UUID,UUID,SMALLINT,VARCHAR,VARCHAR,JSONB,JSONB,VARCHAR,JSONB,UUID,BYTEA,BYTEA"
    op.execute(f"REVOKE ALL ON FUNCTION public.slice4_assessment_assembly_write_v1({assembly_signature}) FROM PUBLIC")
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.slice4_assessment_assembly_write_v1({assembly_signature}) TO "{readiness}"''')


def _create_read_boundary(clinical: str, institution: str) -> None:
    op.execute(
        "CREATE VIEW public.slice4_assessment_readiness_read_v1 WITH (security_barrier=true) AS "
        "SELECT p.service_case_id,a.assembly_id,a.status,a.reason_codes,a.missing_codes,"
        "a.expired_codes,a.disputed_codes,a.profile_revision_id,"
        "a.rule_version AS policy_version,"
        "CASE WHEN a.status='DATA_SYNC_PENDING' THEN 'SYNC_PENDING' "
        "WHEN a.resolved_generation_id IS NULL THEN 'UNAVAILABLE' ELSE 'CURRENT' END AS projection_status,"
        "a.generated_at AS data_as_of,a.generated_at "
        "FROM public.assessment_readiness_case_pointer p "
        "JOIN public.assessment_input_assembly a "
        "ON a.assembly_id=p.current_assembly_id AND a.service_case_id=p.service_case_id"
    )
    op.execute("REVOKE ALL ON TABLE public.slice4_assessment_readiness_read_v1 FROM PUBLIC")
    op.execute(
        f'''CREATE FUNCTION public.slice4_assessment_readiness_read_v1(value_service_case_id UUID)
        RETURNS SETOF public.slice4_assessment_readiness_read_v1
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF session_user NOT IN ('{clinical}','{institution}') THEN
            RAISE EXCEPTION 'SLICE4_READINESS_READ_FORBIDDEN';
          END IF;
          RETURN QUERY SELECT * FROM public.slice4_assessment_readiness_read_v1 r
            WHERE r.service_case_id=value_service_case_id;
        END; $$;'''
    )
    op.execute("REVOKE ALL ON FUNCTION public.slice4_assessment_readiness_read_v1(UUID) FROM PUBLIC")
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.slice4_assessment_readiness_read_v1(UUID) TO "{clinical}", "{institution}"''')


def _create_boundary_view(name: str, query: str) -> None:
    op.execute(f"CREATE VIEW public.{name} WITH (security_barrier=true) AS {query}")
    op.execute(f"REVOKE ALL ON TABLE public.{name} FROM PUBLIC")


def _create_boundary_function(
    name: str,
    arguments: str,
    returns: str,
    body: str,
) -> None:
    op.execute(
        f"CREATE FUNCTION public.{name}({arguments}) RETURNS {returns} "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ "
        f"BEGIN {body} END; $$"
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({arguments}) FROM PUBLIC")


def _create_extended_boundaries(
    roles: tuple[str, str, str, str, str, str]
) -> None:
    writer, readiness, worker, clinical, institution, _identity = roles
    health_fact_writer = os.environ["KG_HEALTH_FACT_WRITER_ROLE"]
    health_reader = os.environ["KG_HEALTH_PROJECTION_READER_ROLE"]
    health_builder = os.environ["KG_HEALTH_PROJECTION_BUILDER_ROLE"]
    evidence_roles = (
        os.environ["KG_HEALTH_PROJECTION_SHADOW_ROLE"],
        os.environ["KG_PROJECTION_READY_GATE_ROLE"],
        os.environ["KG_PROJECTION_CONFIRMATION_ROLE"],
        os.environ["KG_PROJECTION_SHADOW_CONFIRMATION_ROLE"],
    )

    _create_boundary_view(
        "slice4_health_profile_clinical_read_v1",
        "SELECT h.profile_public_id,h.subject_member_id AS subject_ref,"
        "h.current_revision_id AS revision_id,r.revision_no,h.version,"
        "r.tenant_public_id,r.identity_revision_ref,r.identity_source_version,"
        "r.snapshot_ciphertext,r.snapshot_key_id,r.reconfirmed_at,h.updated_at "
        "FROM public.health_profile h JOIN public.health_profile_revision r "
        "ON r.profile_revision_id=h.current_revision_id "
        "AND r.subject_member_id=h.subject_member_id "
        "WHERE h.subject_member_id IS NOT NULL",
    )
    _create_boundary_view(
        "slice4_institution_health_record_read_v1",
        "SELECT 'HEALTH_RECORD'::varchar AS resource,c.case_id,c.tenant_id,"
        "c.subject_member_id AS subject_ref,"
        "(h.current_revision_id IS NOT NULL) AS profile_complete,"
        "COALESCE(h.updated_at,c.updated_at) AS profile_updated_at,"
        "COALESCE((SELECT jsonb_agg(DISTINCT f.indicator_code ORDER BY f.indicator_code) "
        "FROM public.canonical_health_fact f WHERE f.catalog_version=2 "
        "AND f.subject_member_id=c.subject_member_id),'[]'::jsonb) AS indicator_codes,"
        "COALESCE((SELECT jsonb_object_agg(x.indicator_code,x.state) FROM ("
        "SELECT DISTINCT ON (f.indicator_code) f.indicator_code,"
        "COALESCE(e.state,CASE WHEN f.source_type='APP' THEN 'SELF_REPORTED' ELSE 'UNKNOWN' END) AS state "
        "FROM public.canonical_health_fact f LEFT JOIN LATERAL (SELECT s.state "
        "FROM public.health_fact_status_event s WHERE s.fact_id=f.id "
        "ORDER BY s.event_no DESC LIMIT 1) e ON true WHERE f.catalog_version=2 "
        "AND f.subject_member_id=c.subject_member_id "
        "ORDER BY f.indicator_code,f.measured_at DESC,f.fact_ref DESC) x),'{}'::jsonb) "
        "AS indicator_states,"
        "(SELECT count(*) FROM public.detection_report d WHERE d.report_id IS NOT NULL "
        "AND d.subject_member_id=c.subject_member_id AND d.service_case_id=c.case_id)::bigint AS report_count,"
        "COALESCE(a.status,'DATA_SYNC_PENDING')::varchar AS readiness_status,"
        "NULL::uuid AS report_id,NULL::varchar AS report_type,NULL::timestamptz AS measured_at,"
        "NULL::timestamptz AS received_at,NULL::varchar AS report_status,"
        "NULL::varchar AS source_type,NULL::bigint AS report_version,"
        "NULL::uuid AS supersedes_report_id,NULL::timestamptz AS report_created_at,"
        "NULL::smallint AS attachment_count "
        "FROM public.service_case c LEFT JOIN public.health_profile h "
        "ON h.subject_member_id=c.subject_member_id "
        "LEFT JOIN public.assessment_readiness_case_pointer p ON p.service_case_id=c.case_id "
        "LEFT JOIN public.assessment_input_assembly a ON a.assembly_id=p.current_assembly_id "
        "AND a.service_case_id=p.service_case_id WHERE c.status='PREPARING' "
        "UNION ALL SELECT 'DETECTION_REPORT'::varchar,c.case_id,c.tenant_id,"
        "d.subject_member_id,NULL::boolean,c.updated_at,'[]'::jsonb,'{}'::jsonb,"
        "NULL::bigint,NULL::varchar,d.report_id,d.report_type,d.measured_at,d.received_at,"
        "d.report_status,d.source_type,d.version,d.supersedes_report_id,d.created_at,"
        "(SELECT count(*)::smallint FROM public.detection_report_attachment x "
        "WHERE x.report_id=d.report_id) "
        "FROM public.service_case c JOIN public.detection_report d "
        "ON d.service_case_id=c.case_id AND d.subject_member_id=c.subject_member_id "
        "WHERE c.status='PREPARING' AND d.report_id IS NOT NULL",
    )
    _create_boundary_view(
        "slice4_detection_report_read_v1",
        "SELECT d.report_id,d.subject_member_id AS subject_ref,d.service_case_id,d.tenant_id,"
        "d.report_type,d.measured_at,d.received_at,d.report_status AS status,"
        "d.source_type,d.version,d.supersedes_report_id,d.created_at,"
        "count(a.attachment_id)::smallint AS attachment_count,"
        "COALESCE(jsonb_agg(jsonb_build_object('file_id',a.private_file_id,"
        "'mime_type',COALESCE(f.actual_mime_type,f.declared_mime_type),"
        "'size',COALESCE(f.actual_size,f.declared_size),'status',f.status) "
        "ORDER BY a.position) FILTER (WHERE a.attachment_id IS NOT NULL),'[]'::jsonb) "
        "AS attachments "
        "FROM public.detection_report d LEFT JOIN public.detection_report_attachment a "
        "ON a.report_id=d.report_id LEFT JOIN public.private_file f "
        "ON f.file_id=a.private_file_id WHERE d.report_id IS NOT NULL "
        "GROUP BY d.id,d.report_id,d.subject_member_id,d.service_case_id,d.tenant_id,"
        "d.report_type,d.measured_at,d.received_at,d.report_status,d.source_type,"
        "d.version,d.supersedes_report_id,d.created_at",
    )
    _create_boundary_view(
        "slice4_health_fact_status_read_v1",
        "SELECT f.fact_ref,f.subject_member_id AS subject_ref,f.indicator_code,"
        "f.numeric_value,f.unit,f.measured_at,f.received_at,f.source_type,"
        "(f.measured_at AT TIME ZONE 'Asia/Shanghai')::date AS business_day,"
        "COALESCE(e.state,CASE WHEN f.source_type='APP' "
        "THEN 'SELF_REPORTED' ELSE 'UNKNOWN' END) AS verification_state,"
        "e.event_no,e.status_event_seq "
        "FROM public.canonical_health_fact f LEFT JOIN LATERAL "
        "(SELECT x.state,x.event_no,x.status_event_seq FROM public.health_fact_status_event x "
        "WHERE x.fact_id=f.id ORDER BY x.event_no DESC LIMIT 1) e ON true "
        "WHERE f.catalog_version=2",
    )
    _create_boundary_view(
        "slice4_recompute_candidate_v1",
        "SELECT c.case_id,c.subject_member_id,c.tenant_id,c.version AS case_version,"
        "p.current_assembly_id,p.source_vector_digest,p.version AS pointer_version "
        "FROM public.service_case c LEFT JOIN public.assessment_readiness_case_pointer p "
        "ON p.service_case_id=c.case_id WHERE c.status='PREPARING'",
    )
    _create_boundary_view(
        "health_ready_projection_resolution_v2",
        "SELECT g.id AS generation_id,g.generation_no,g.projection_version,"
        "r.rule_version,g.high_watermark,g.ready_at "
        "FROM public.health_projection_generation g "
        "JOIN public.health_projection_shadow_run r ON r.run_id=g.current_shadow_run_id "
        "WHERE g.status='READY' AND g.projection_version=2",
    )
    _create_boundary_view(
        "health_projection_source_visibility_v2",
        "SELECT f.id AS fact_id,f.fact_ref,f.subject_member_id,f.indicator_code,"
        "f.numeric_value,f.unit,f.measured_at,f.received_at,f.source_type,"
        "(f.measured_at AT TIME ZONE 'Asia/Shanghai')::date AS business_day,"
        "f.supersedes_fact_id,f.xmin::text AS source_xmin "
        "FROM public.canonical_health_fact f WHERE f.catalog_version=2",
    )
    _create_boundary_view(
        "health_projection_status_visibility_v2",
        "SELECT e.status_event_seq,e.fact_id,f.fact_ref,f.subject_member_id,"
        "f.indicator_code,e.event_no,e.state,e.created_at,e.xmin::text AS source_xmin "
        "FROM public.health_fact_status_event e JOIN public.canonical_health_fact f "
        "ON f.id=e.fact_id WHERE f.catalog_version=2",
    )
    _create_boundary_view(
        "slice4_projection_coverage_source_v2",
        "SELECT f.subject_member_id,f.indicator_code,count(DISTINCT f.id)::bigint AS fact_count,"
        "count(e.status_event_seq)::bigint AS status_event_count,max(f.id)::bigint AS max_fact_id,"
        "COALESCE(max(e.status_event_seq),0)::bigint AS max_status_event_seq,"
        "encode(sha256(convert_to(COALESCE(string_agg(DISTINCT f.payload_digest::text,',' "
        "ORDER BY f.payload_digest::text),''),'UTF8')),'hex') AS fact_set_digest,"
        "encode(sha256(convert_to(COALESCE(string_agg(encode(e.event_digest,'hex'),',' "
        "ORDER BY encode(e.event_digest,'hex')) FILTER (WHERE e.status_event_seq IS NOT NULL),''),"
        "'UTF8')),'hex') AS status_set_digest "
        "FROM public.canonical_health_fact f LEFT JOIN public.health_fact_status_event e "
        "ON e.fact_id=f.id WHERE f.catalog_version=2 "
        "GROUP BY f.subject_member_id,f.indicator_code",
    )
    _create_boundary_view(
        "health_ready_subject_indicator_evidence_v2",
        "SELECT e.generation_id,g.generation_no,g.projection_version,r.rule_version,g.ready_at,"
        "e.subject_member_id,e.indicator_code,e.fact_count,e.status_event_count,"
        "e.max_fact_id,e.max_status_event_seq,e.source_snapshot,e.fact_set_digest,"
        "e.status_set_digest,e.evidence_digest,e.digest_key_id,e.created_at "
        "FROM public.health_projection_subject_indicator_evidence_v2 e "
        "JOIN public.health_projection_generation g ON g.id=e.generation_id "
        "JOIN public.health_projection_shadow_run r ON r.run_id=g.current_shadow_run_id "
        "WHERE g.status='READY' AND g.projection_version=2",
    )
    _create_boundary_view(
        "health_ready_projection_fact_v2",
        "SELECT g.id AS generation_id,g.generation_no,g.projection_version,r.rule_version,g.ready_at,"
        "f.fact_ref,f.subject_member_id,f.subject_user_id,f.indicator_code,f.numeric_value,"
        "f.unit,f.measured_at,f.received_at,f.source_type,f.business_day,"
        "e.state AS verification_state,f.status_event_seq "
        "FROM public.health_projection_generation g "
        "JOIN public.health_projection_shadow_run r ON r.run_id=g.current_shadow_run_id "
        "JOIN public.health_projection_fact f ON f.generation_id=g.id "
        "JOIN public.health_fact_status_event e ON e.status_event_seq=f.status_event_seq "
        "AND e.fact_id=f.fact_id WHERE g.status='READY' AND g.projection_version=2 "
        "AND f.subject_member_id IS NOT NULL AND f.fact_ref IS NOT NULL",
    )

    _create_boundary_function(
        "slice4_subject_authority_v1",
        "value_enrollment_id UUID,value_service_case_id UUID,value_actor_user_id BIGINT,value_context VARCHAR",
        "TABLE(subject_member_id UUID,subject_user_id BIGINT)",
        f"IF session_user NOT IN ('{writer}','{readiness}','{clinical}') THEN "
        "RAISE EXCEPTION 'SLICE4_SUBJECT_AUTHORITY_FORBIDDEN'; END IF; "
        "IF value_context='SELF' THEN "
        "RETURN QUERY SELECT l.member_id,l.user_ref FROM identity.user_member_self_link l "
        "JOIN identity.member m ON m.member_id=l.member_id "
        "JOIN public.\"user\" u ON u.id=l.user_ref "
        "WHERE l.user_ref=value_actor_user_id AND u.status='active' AND u.role='member' "
        "AND u.tenant_id IS NULL AND m.status='created' FOR SHARE OF l,m,u; "
        "ELSIF value_context LIKE 'PROXY_%' THEN "
        "RETURN QUERY SELECT e.subject_member_id,s.user_ref FROM public.service_enrollment e "
        "JOIN public.proxy_grant g ON g.enrollment_id=e.enrollment_id "
        "JOIN identity.user_member_self_link p ON p.member_id=g.proxy_member_id "
        "JOIN public.\"user\" u ON u.id=p.user_ref LEFT JOIN identity.user_member_self_link s "
        "ON s.member_id=e.subject_member_id WHERE e.enrollment_id=value_enrollment_id "
        "AND p.user_ref=value_actor_user_id AND u.status='active' AND g.status='ACTIVE' "
        "AND (g.valid_until IS NULL OR g.valid_until>now()) "
        "AND g.permission_codes ? CASE WHEN value_context='PROXY_REPORT_UPLOAD' "
        "THEN 'REPORT_UPLOAD' WHEN value_context='PROXY_DAILY_INPUT' THEN 'DAILY_INPUT' "
        "ELSE 'DAILY_VIEW' END FOR SHARE OF e,g,p,u; "
        "ELSIF value_context='THERAPIST' THEN "
        "RETURN QUERY SELECT c.subject_member_id,s.user_ref FROM public.service_case c "
        "JOIN public.therapist_profile t ON t.therapist_id=c.primary_therapist_id "
        "JOIN public.\"user\" u ON u.id=value_actor_user_id "
        "LEFT JOIN identity.user_member_self_link s ON s.member_id=c.subject_member_id "
        "WHERE c.case_id=value_service_case_id AND c.status='PREPARING' "
        "AND t.user_id=value_actor_user_id AND t.status='APPROVED_ACTIVE' "
        "AND u.status='active' AND u.role='therapist' AND u.tenant_id=c.tenant_id "
        "FOR SHARE OF c,t,u; ELSE RAISE EXCEPTION 'SLICE4_SUBJECT_AUTHORITY_INVALID'; END IF;",
    )
    _create_boundary_function(
        "slice4_readiness_currentness_v1",
        "value_service_case_id UUID,value_actor_user_id BIGINT",
        "JSONB",
        f"IF session_user NOT IN ('{readiness}','{worker}') THEN "
        "RAISE EXCEPTION 'SLICE4_READINESS_CURRENTNESS_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.service_case c JOIN public.primary_therapist_assignment a "
        "ON a.assignment_id=c.assignment_id JOIN public.therapist_profile t "
        "ON t.therapist_id=c.primary_therapist_id JOIN public.service_enrollment e "
        "ON e.enrollment_id=c.enrollment_id JOIN public.institution_application ia "
        "ON ia.tenant_internal_id=c.tenant_id WHERE c.case_id=value_service_case_id "
        "AND c.status='PREPARING' AND a.status='ACCEPTED' AND t.status='APPROVED_ACTIVE' "
        "AND e.status='CASE_CREATED' AND ia.status='APPROVED' "
        "FOR SHARE OF c,a,t,e,ia; IF NOT FOUND THEN RETURN NULL; END IF; "
        "PERFORM 1 FROM public.health_profile h JOIN public.health_profile_revision r "
        "ON r.profile_revision_id=h.current_revision_id AND r.subject_member_id=h.subject_member_id "
        "JOIN public.service_case c ON c.subject_member_id=h.subject_member_id "
        "WHERE c.case_id=value_service_case_id FOR SHARE OF h,r; "
        "PERFORM 1 FROM public.assessment_readiness_policy_version p "
        "WHERE p.status='PUBLISHED' AND p.professionally_approved "
        "AND p.effective_from<=clock_timestamp() "
        "AND (p.retired_at IS NULL OR p.retired_at>clock_timestamp()) FOR SHARE OF p; "
        "RETURN (SELECT jsonb_build_object('case_id',c.case_id,"
        "'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,"
        "'tenant_public_id',ia.tenant_public_id,'case_version',c.version,"
        "'assignment_id',c.assignment_id,'primary_therapist_id',c.primary_therapist_id,"
        "'primary_therapist_user_id',t.user_id,'transaction_time',transaction_timestamp(),"
        "'identity_revision_id',c.identity_revision_id,"
        "'consent_set_digest',c.consent_set_digest,"
        "'consent_version_ids',COALESCE((SELECT jsonb_agg(cr.document_version_id ORDER BY cr.document_version_id) "
        "FROM public.consent_record cr WHERE cr.enrollment_id=c.enrollment_id "
        "AND cr.status='ACCEPTED'),'[]'::jsonb),"
        "'authorization_complete',EXISTS(SELECT 1 FROM public.consent_record cr "
        "WHERE cr.enrollment_id=c.enrollment_id AND cr.status='ACCEPTED'),"
        "'readiness_evidence_version',c.readiness_evidence_version,"
        "'profile_revision_id',h.current_revision_id,"
        "'profile_reconfirmed_at',r.reconfirmed_at,"
        "'policy',CASE WHEN p.policy_version_id IS NULL THEN NULL ELSE jsonb_build_object("
        "'policy_version_id',p.policy_version_id,'version_no',p.version_no,"
        "'required_profile_sections',p.required_profile_sections,"
        "'required_indicators',p.required_indicators,'allowed_states',p.allowed_states,"
        "'projection_version',p.projection_version,'rule_version',p.rule_version,"
        "'policy_digest',encode(p.policy_digest,'hex'),'digest_key_id',p.digest_key_id) END) "
        "FROM public.service_case c JOIN public.institution_application ia "
        "ON ia.tenant_internal_id=c.tenant_id JOIN public.therapist_profile t "
        "ON t.therapist_id=c.primary_therapist_id LEFT JOIN public.health_profile h "
        "ON h.subject_member_id=c.subject_member_id LEFT JOIN public.health_profile_revision r "
        "ON r.profile_revision_id=h.current_revision_id LEFT JOIN LATERAL ("
        "SELECT x.* FROM public.assessment_readiness_policy_version x "
        "WHERE x.status='PUBLISHED' AND x.professionally_approved "
        "AND x.effective_from<=clock_timestamp() "
        "AND (x.retired_at IS NULL OR x.retired_at>clock_timestamp()) LIMIT 1) p ON true "
        "WHERE c.case_id=value_service_case_id);",
    )
    _create_boundary_function(
        "slice4_projection_coverage_v2",
        "value_subject_member_id UUID,value_indicator_codes JSONB",
        "JSONB",
        f"IF session_user<>'{health_reader}' THEN RAISE EXCEPTION 'SLICE4_COVERAGE_FORBIDDEN'; END IF; "
        "IF jsonb_typeof(value_indicator_codes)<>'array' THEN RAISE EXCEPTION 'SLICE4_COVERAGE_INVALID'; END IF; "
        "RETURN (SELECT jsonb_build_object('subject_member_id',value_subject_member_id,"
        "'source_snapshot',txid_current_snapshot()::text,'items',"
        "COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.indicator_code),'[]'::jsonb)) FROM ("
        "SELECT requested.indicator_code,COALESCE(s.fact_count,0)::bigint AS fact_count,"
        "COALESCE(s.status_event_count,0)::bigint AS status_event_count,"
        "COALESCE(s.max_fact_id,0)::bigint AS max_fact_id,"
        "COALESCE(s.max_status_event_seq,0)::bigint AS max_status_event_seq,"
        "COALESCE(s.fact_set_digest,encode(sha256(convert_to('','UTF8')),'hex')) AS fact_set_digest,"
        "COALESCE(s.status_set_digest,encode(sha256(convert_to('','UTF8')),'hex')) AS status_set_digest "
        "FROM (SELECT DISTINCT jsonb_array_elements_text(value_indicator_codes) AS indicator_code) requested "
        "LEFT JOIN public.slice4_projection_coverage_source_v2 s "
        "ON s.subject_member_id=value_subject_member_id "
        "AND s.indicator_code=requested.indicator_code) x);",
    )
    _create_boundary_function(
        "slice4_report_file_authority_v1",
        "value_file_id UUID,value_report_id UUID,value_actor_user_id BIGINT,value_context VARCHAR",
        "BOOLEAN",
        f"IF session_user NOT IN ('{writer}','{clinical}') THEN "
        "RAISE EXCEPTION 'SLICE4_REPORT_AUTHORITY_FORBIDDEN'; END IF; "
        "RETURN EXISTS(SELECT 1 FROM public.detection_report_attachment a "
        "JOIN public.detection_report d ON d.report_id=a.report_id "
        "WHERE a.private_file_id=value_file_id AND a.report_id=value_report_id);",
    )
    _create_boundary_function(
        "slice4_clinical_profile_read_v1",
        "value_actor_user_id BIGINT,value_context VARCHAR,value_subject_member_id UUID,value_service_case_id UUID,value_enrollment_id UUID",
        "SETOF public.slice4_health_profile_clinical_read_v1",
        f"IF session_user<>'{clinical}' THEN RAISE EXCEPTION 'SLICE4_CLINICAL_READ_FORBIDDEN'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice4_subject_authority_v1(value_enrollment_id,"
        "value_service_case_id,value_actor_user_id,value_context) s "
        "WHERE s.subject_member_id=value_subject_member_id) THEN "
        "RAISE EXCEPTION 'SLICE4_CLINICAL_SCOPE_FORBIDDEN'; END IF; "
        "RETURN QUERY SELECT v.* FROM public.slice4_health_profile_clinical_read_v1 v "
        "WHERE v.subject_ref=value_subject_member_id;",
    )
    _create_boundary_function(
        "slice4_clinical_report_read_v1",
        "value_actor_user_id BIGINT,value_context VARCHAR,value_subject_member_id UUID,value_service_case_id UUID,value_enrollment_id UUID,value_page JSONB",
        "SETOF public.slice4_detection_report_read_v1",
        f"IF session_user<>'{clinical}' THEN RAISE EXCEPTION 'SLICE4_CLINICAL_READ_FORBIDDEN'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice4_subject_authority_v1(value_enrollment_id,"
        "value_service_case_id,value_actor_user_id,value_context) s "
        "WHERE s.subject_member_id=value_subject_member_id) THEN "
        "RAISE EXCEPTION 'SLICE4_CLINICAL_SCOPE_FORBIDDEN'; END IF; "
        "RETURN QUERY SELECT v.* FROM public.slice4_detection_report_read_v1 v "
        "WHERE v.subject_ref=value_subject_member_id "
        "AND (value_service_case_id IS NULL OR v.service_case_id=value_service_case_id) "
        "AND (value_page->>'report_id' IS NULL OR v.report_id=(value_page->>'report_id')::uuid) "
        "AND (value_page->>'cursor_measured_at' IS NULL OR (v.measured_at,v.report_id)<"
        "((value_page->>'cursor_measured_at')::timestamptz,(value_page->>'cursor_report_id')::uuid)) "
        "ORDER BY v.measured_at DESC,v.report_id DESC LIMIT LEAST(COALESCE((value_page->>'limit')::int,20),100);",
    )
    _create_boundary_function(
        "slice4_clinical_fact_read_v1",
        "value_actor_user_id BIGINT,value_context VARCHAR,value_subject_member_id UUID,value_service_case_id UUID,value_enrollment_id UUID,value_page JSONB",
        "SETOF public.slice4_health_fact_status_read_v1",
        f"IF session_user<>'{clinical}' THEN RAISE EXCEPTION 'SLICE4_CLINICAL_READ_FORBIDDEN'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.slice4_subject_authority_v1(value_enrollment_id,"
        "value_service_case_id,value_actor_user_id,value_context) s "
        "WHERE s.subject_member_id=value_subject_member_id) THEN "
        "RAISE EXCEPTION 'SLICE4_CLINICAL_SCOPE_FORBIDDEN'; END IF; "
        "RETURN QUERY SELECT v.* FROM public.slice4_health_fact_status_read_v1 v "
        "WHERE v.subject_ref=value_subject_member_id "
        "AND (value_page->>'fact_ref' IS NULL OR v.fact_ref=(value_page->>'fact_ref')::uuid) "
        "AND (value_page->>'indicator_code' IS NULL OR v.indicator_code=value_page->>'indicator_code') "
        "AND (value_page->>'cursor_measured_at' IS NULL OR (v.measured_at,v.fact_ref)<"
        "((value_page->>'cursor_measured_at')::timestamptz,(value_page->>'cursor_fact_ref')::uuid)) "
        "ORDER BY v.measured_at DESC,v.fact_ref DESC "
        "LIMIT LEAST(COALESCE((value_page->>'limit')::int,20),100);",
    )
    _create_boundary_function(
        "slice4_institution_health_read_v1",
        "value_actor_user_id BIGINT,value_service_case_id UUID,value_resource VARCHAR,value_page JSONB",
        "SETOF public.slice4_institution_health_record_read_v1",
        f"IF session_user<>'{institution}' THEN RAISE EXCEPTION 'SLICE4_INSTITUTION_READ_FORBIDDEN'; END IF; "
        "IF value_resource NOT IN ('HEALTH_RECORD','DETECTION_REPORTS') "
        "OR jsonb_typeof(value_page)<>'object' THEN "
        "RAISE EXCEPTION 'SLICE4_INSTITUTION_READ_INVALID'; END IF; "
        "PERFORM 1 FROM public.\"user\" u JOIN public.service_case c ON c.case_id=value_service_case_id "
        "WHERE u.id=value_actor_user_id AND u.status='active' AND u.role IN ('org_admin','org_operator') "
        "AND u.tenant_id=c.tenant_id AND c.status='PREPARING' FOR SHARE OF u,c; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'SLICE4_INSTITUTION_SCOPE_FORBIDDEN'; END IF; "
        "RETURN QUERY SELECT v.* FROM public.slice4_institution_health_record_read_v1 v "
        "WHERE v.case_id=value_service_case_id "
        "AND ((value_resource='HEALTH_RECORD' AND v.resource='HEALTH_RECORD') "
        "OR (value_resource='DETECTION_REPORTS' AND v.resource='DETECTION_REPORT' "
        "AND (value_page->>'cursor_measured_at' IS NULL OR (v.measured_at,v.report_id)<"
        "((value_page->>'cursor_measured_at')::timestamptz,"
        "(value_page->>'cursor_report_id')::uuid)))) "
        "ORDER BY v.measured_at DESC NULLS LAST,v.report_id DESC NULLS LAST "
        "LIMIT CASE WHEN value_resource='HEALTH_RECORD' THEN 1 "
        "ELSE LEAST(COALESCE((value_page->>'limit')::int,20),100) END;",
    )
    _create_boundary_function(
        "health_projection_builder_source_v2",
        "value_max_fact_id BIGINT,value_page JSONB,value_source_snapshot VARCHAR",
        "JSONB",
        f"IF session_user<>'{health_builder}' THEN RAISE EXCEPTION 'SLICE4_BUILDER_SOURCE_FORBIDDEN'; END IF; "
        "IF value_max_fact_id<0 OR value_source_snapshot IS NULL OR jsonb_typeof(value_page)<>'object' "
        "THEN RAISE EXCEPTION 'SLICE4_BUILDER_SOURCE_INVALID'; END IF; "
        "RETURN (SELECT jsonb_build_object('facts',COALESCE(jsonb_agg(to_jsonb(v) ORDER BY v.fact_id),'[]'::jsonb),"
        "'source_snapshot',value_source_snapshot) FROM (SELECT * FROM public.health_projection_source_visibility_v2 "
        "WHERE fact_id<=value_max_fact_id ORDER BY fact_id "
        "LIMIT LEAST(COALESCE((value_page->>'limit')::int,100),500)) v);",
    )
    evidence_role_sql = ",".join(f"'{role}'" for role in evidence_roles)
    _create_boundary_function(
        "health_projection_subject_evidence_verify_v2",
        "value_generation_id BIGINT",
        "BOOLEAN",
        f"IF session_user NOT IN ({evidence_role_sql}) THEN RAISE EXCEPTION 'SLICE4_EVIDENCE_VERIFY_FORBIDDEN'; END IF; "
        "RETURN EXISTS(SELECT 1 FROM public.health_projection_generation g "
        "WHERE g.id=value_generation_id AND g.projection_version=2 AND g.status IN ('SHADOW_RUNNING','SHADOW_COMPLETE','READY')) "
        "AND NOT EXISTS(SELECT 1 FROM public.health_projection_window_selection s "
        "WHERE s.generation_id=value_generation_id AND s.subject_member_id IS NOT NULL "
        "AND NOT EXISTS(SELECT 1 FROM public.health_projection_subject_indicator_evidence_v2 e "
        "WHERE e.generation_id=s.generation_id AND e.subject_member_id=s.subject_member_id "
        "AND e.indicator_code=s.indicator_code));",
    )
    _create_boundary_function(
        "slice4_profile_confirm_v1",
        "value_aggregate_ref UUID,value_audit_id UUID,value_event_id UUID",
        "JSONB",
        f"IF session_user<>'{writer}' THEN RAISE EXCEPTION 'SLICE4_CONFIRM_FORBIDDEN'; END IF; "
        "RETURN (SELECT jsonb_build_object('aggregate',to_jsonb(h),'revision',to_jsonb(r),"
        "'audit',to_jsonb(a),'outbox',to_jsonb(o),'receipt',to_jsonb(i)) "
        "FROM public.health_profile h JOIN public.health_profile_revision r "
        "ON r.profile_revision_id=h.current_revision_id LEFT JOIN public.slice4_audit a ON a.audit_id=value_audit_id "
        "LEFT JOIN public.slice4_outbox o ON o.event_id=value_event_id "
        "LEFT JOIN LATERAL (SELECT x.* FROM public.slice4_idempotency x "
        "WHERE x.scope_ref=h.subject_member_id ORDER BY x.created_at DESC LIMIT 1) i ON true "
        "WHERE h.profile_public_id=value_aggregate_ref LIMIT 1);",
    )
    _create_boundary_function(
        "slice4_report_confirm_v1",
        "value_aggregate_ref UUID,value_audit_id UUID,value_event_id UUID",
        "JSONB",
        f"IF session_user<>'{writer}' THEN RAISE EXCEPTION 'SLICE4_CONFIRM_FORBIDDEN'; END IF; "
        "RETURN (SELECT jsonb_build_object('aggregate',to_jsonb(d),'attachments',"
        "COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.position) FILTER (WHERE x.attachment_id IS NOT NULL),'[]'::jsonb),"
        "'audit',to_jsonb(a),'outbox',to_jsonb(o),'receipt',to_jsonb(receipt)) FROM public.detection_report d "
        "LEFT JOIN public.detection_report_attachment x ON x.report_id=d.report_id "
        "LEFT JOIN public.slice4_audit a ON a.audit_id=value_audit_id "
        "LEFT JOIN public.slice4_outbox o ON o.event_id=value_event_id "
        "LEFT JOIN LATERAL (SELECT i.* FROM public.slice4_idempotency i "
        "WHERE i.scope_ref=d.subject_member_id ORDER BY i.created_at DESC LIMIT 1) receipt ON true "
        "WHERE d.report_id=value_aggregate_ref GROUP BY d.id,a.audit_id,o.event_id,receipt.receipt_id);",
    )
    _create_boundary_function(
        "slice4_health_fact_confirm_v1",
        "value_aggregate_ref UUID,value_audit_id UUID,value_event_id UUID",
        "JSONB",
        f"IF session_user<>'{health_fact_writer}' THEN RAISE EXCEPTION 'SLICE4_CONFIRM_FORBIDDEN'; END IF; "
        "RETURN (SELECT jsonb_build_object('aggregate',to_jsonb(f),'status_events',"
        "COALESCE(jsonb_agg(to_jsonb(e) ORDER BY e.event_no) FILTER (WHERE e.status_event_id IS NOT NULL),'[]'::jsonb),"
        "'audit',to_jsonb(a),'outbox',to_jsonb(o),'receipt',to_jsonb(receipt)) FROM public.canonical_health_fact f "
        "LEFT JOIN public.health_fact_status_event e ON e.fact_id=f.id "
        "LEFT JOIN public.slice4_audit a ON a.audit_id=value_audit_id "
        "LEFT JOIN public.slice4_outbox o ON o.event_id=value_event_id "
        "LEFT JOIN LATERAL (SELECT i.* FROM public.slice4_idempotency i "
        "WHERE i.scope_ref=f.fact_ref ORDER BY i.created_at DESC LIMIT 1) receipt ON true "
        "WHERE f.fact_ref=value_aggregate_ref GROUP BY f.id,a.audit_id,o.event_id,receipt.receipt_id);",
    )
    _create_boundary_function(
        "slice4_assembly_confirm_v1",
        "value_aggregate_ref UUID,value_audit_id UUID,value_event_id UUID",
        "JSONB",
        f"IF session_user<>'{readiness}' THEN RAISE EXCEPTION 'SLICE4_CONFIRM_FORBIDDEN'; END IF; "
        "RETURN (SELECT jsonb_build_object('aggregate',to_jsonb(a),'facts',"
        "COALESCE(jsonb_agg(to_jsonb(f) ORDER BY f.indicator_code) FILTER (WHERE f.indicator_code IS NOT NULL),'[]'::jsonb),"
        "'pointer',to_jsonb(p),'audit',to_jsonb(u),'outbox',to_jsonb(o),'receipt',to_jsonb(receipt)) "
        "FROM public.assessment_input_assembly a LEFT JOIN public.assessment_input_assembly_fact f "
        "ON f.assembly_id=a.assembly_id LEFT JOIN public.assessment_readiness_case_pointer p "
        "ON p.current_assembly_id=a.assembly_id LEFT JOIN public.slice4_audit u ON u.audit_id=value_audit_id "
        "LEFT JOIN public.slice4_outbox o ON o.event_id=value_event_id "
        "LEFT JOIN LATERAL (SELECT i.* FROM public.slice4_idempotency i "
        "WHERE i.scope_ref=a.service_case_id ORDER BY i.created_at DESC LIMIT 1) receipt ON true "
        "WHERE a.assembly_id=value_aggregate_ref "
        "GROUP BY a.assembly_id,p.service_case_id,u.audit_id,o.event_id,receipt.receipt_id);",
    )

    grants = {
        writer: ("slice4_subject_authority_v1(UUID,UUID,BIGINT,VARCHAR)",
                 "slice4_report_file_authority_v1(UUID,UUID,BIGINT,VARCHAR)",
                 "slice4_profile_confirm_v1(UUID,UUID,UUID)",
                 "slice4_report_confirm_v1(UUID,UUID,UUID)"),
        readiness: ("slice4_subject_authority_v1(UUID,UUID,BIGINT,VARCHAR)",
                    "slice4_readiness_currentness_v1(UUID,BIGINT)",
                    "slice4_assembly_confirm_v1(UUID,UUID,UUID)"),
        worker: ("slice4_readiness_currentness_v1(UUID,BIGINT)",),
        clinical: ("slice4_subject_authority_v1(UUID,UUID,BIGINT,VARCHAR)",
                   "slice4_report_file_authority_v1(UUID,UUID,BIGINT,VARCHAR)",
                   "slice4_clinical_profile_read_v1(BIGINT,VARCHAR,UUID,UUID,UUID)",
                   "slice4_clinical_report_read_v1(BIGINT,VARCHAR,UUID,UUID,UUID,JSONB)",
                   "slice4_clinical_fact_read_v1(BIGINT,VARCHAR,UUID,UUID,UUID,JSONB)"),
        institution: ("slice4_institution_health_read_v1(BIGINT,UUID,VARCHAR,JSONB)",),
        health_fact_writer: ("slice4_health_fact_confirm_v1(UUID,UUID,UUID)",),
        health_reader: ("slice4_projection_coverage_v2(UUID,JSONB)",),
        health_builder: ("health_projection_builder_source_v2(BIGINT,JSONB,VARCHAR)",),
    }
    for role, signatures in grants.items():
        for signature in signatures:
            op.execute(f'''GRANT EXECUTE ON FUNCTION public.{signature} TO "{role}"''')
    for role in evidence_roles:
        op.execute(
            f'''GRANT EXECUTE ON FUNCTION public.health_projection_subject_evidence_verify_v2(BIGINT) TO "{role}"'''
        )
    op.execute(
        f'''GRANT SELECT ON TABLE public.health_ready_projection_resolution_v2,
        public.health_ready_subject_indicator_evidence_v2,
        public.health_ready_projection_fact_v2 TO "{health_reader}"'''
    )
    op.execute(f'''GRANT SELECT ON TABLE public.slice4_recompute_candidate_v1 TO "{worker}"''')
    op.execute(
        f'''GRANT SELECT(event_id,aggregate_type,aggregate_ref,event_type,payload_digest,
        payload_json,status,attempts,lease_owner,lease_until,created_at,delivered_at),
        UPDATE(status,attempts,lease_owner,lease_until,delivered_at)
        ON TABLE public.slice4_outbox TO "{worker}"'''
    )
    op.execute(
        f'''GRANT SELECT(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at),
        INSERT(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at)
        ON TABLE public.slice4_delivery TO "{worker}"'''
    )


def _grant_acl(roles: tuple[str, str, str, str, str, str]) -> None:
    writer, readiness, worker, clinical, institution, identity = roles
    for role in roles:
        op.execute(f'''GRANT USAGE ON SCHEMA public TO "{role}"''')
        op.execute(f'''REVOKE CREATE ON SCHEMA public FROM "{role}"''')
    op.execute(f'''GRANT SELECT(profile_public_id,subject_member_id,current_revision_id,version), UPDATE(current_revision_id,version) ON TABLE public.health_profile TO "{writer}"''')
    op.execute(f'''GRANT SELECT(profile_revision_id,subject_member_id,revision_no,tenant_public_id,identity_revision_ref,identity_source_version,snapshot_digest,created_at), INSERT(profile_revision_id,subject_member_id,subject_user_id,revision_no,tenant_public_id,identity_source_kind,identity_revision_ref,identity_source_version,snapshot_ciphertext,snapshot_key_id,reconfirmed_at,source_type,changed_fields,supersedes_revision_id,actor_user_id,actor_type,snapshot_digest,digest_key_id,created_at) ON TABLE public.health_profile_revision TO "{writer}"''')
    # Preserve and explicitly verify the PR #53 invitation hotfix ACL while 0024 is active.
    for column in ("code_digest", "code_key_id", "expires_at", "issued_at"):
        op.get_bind().execute(
            sa.text("SELECT has_column_privilege(:role,'public.member_service_invitation',:column,'UPDATE')"),
            {"role": os.environ["KG_MEMBER_ENROLLMENT_WRITER_ROLE"], "column": column},
        ).scalar_one()


def upgrade() -> None:
    roles = _roles()
    writer, readiness, _worker, clinical, institution, _identity = roles
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    _alter_health_profile()
    _alter_health_data_tables()
    _create_core_tables()
    _create_owner_functions(writer, readiness, _identity)
    _create_read_boundary(clinical, institution)
    _create_extended_boundaries(roles)
    _grant_acl(roles)


def downgrade() -> None:
    writer, readiness, _worker, clinical, institution, _identity = _roles()
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    populated = any(
        connection.execute(sa.text(f"SELECT EXISTS(SELECT 1 FROM public.{table} LIMIT 1)")).scalar_one()
        for table in _MODULE_TABLES
    )
    populated = populated or bool(
        connection.execute(sa.text(
            "SELECT EXISTS(SELECT 1 FROM public.health_profile WHERE subject_member_id IS NOT NULL) "
            "OR EXISTS(SELECT 1 FROM public.detection_report WHERE report_id IS NOT NULL) "
            "OR EXISTS(SELECT 1 FROM public.canonical_health_fact WHERE catalog_version=2) "
            "OR EXISTS(SELECT 1 FROM public.health_projection_fact WHERE subject_member_id IS NOT NULL)"
        )).scalar_one()
    )
    if populated:
        raise RuntimeError("Slice 4 downgrade requires empty module tables") from None

    health_reader = os.environ["KG_HEALTH_PROJECTION_READER_ROLE"]
    worker = os.environ["KG_SLICE4_WORKFLOW_WORKER_ROLE"]
    op.execute(
        f'''REVOKE SELECT ON TABLE public.health_ready_projection_resolution_v2,
        public.health_ready_subject_indicator_evidence_v2,
        public.health_ready_projection_fact_v2 FROM "{health_reader}"'''
    )
    op.execute(f'''REVOKE SELECT ON TABLE public.slice4_recompute_candidate_v1 FROM "{worker}"''')
    op.execute(
        f'''REVOKE SELECT(event_id,aggregate_type,aggregate_ref,event_type,payload_digest,
        payload_json,status,attempts,lease_owner,lease_until,created_at,delivered_at),
        UPDATE(status,attempts,lease_owner,lease_until,delivered_at)
        ON TABLE public.slice4_outbox FROM "{worker}"'''
    )
    op.execute(
        f'''REVOKE SELECT(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at),
        INSERT(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at)
        ON TABLE public.slice4_delivery FROM "{worker}"'''
    )

    extended_grantees = tuple(dict.fromkeys((
        writer, readiness, worker, clinical, institution, _identity,
        os.environ["KG_HEALTH_FACT_WRITER_ROLE"],
        os.environ["KG_HEALTH_PROJECTION_READER_ROLE"],
        os.environ["KG_HEALTH_PROJECTION_BUILDER_ROLE"],
        os.environ["KG_HEALTH_PROJECTION_SHADOW_ROLE"],
        os.environ["KG_PROJECTION_READY_GATE_ROLE"],
        os.environ["KG_PROJECTION_CONFIRMATION_ROLE"],
        os.environ["KG_PROJECTION_SHADOW_CONFIRMATION_ROLE"],
    )))
    for name, signature in reversed(_EXTENDED_FUNCTIONS):
        for role in extended_grantees:
            op.execute(f'''REVOKE EXECUTE ON FUNCTION public.{name}({signature}) FROM "{role}"''')
        op.execute(f"DROP FUNCTION public.{name}({signature})")

    for role in (clinical, institution):
        op.execute(f'''REVOKE EXECUTE ON FUNCTION public.slice4_assessment_readiness_read_v1(UUID) FROM "{role}"''')
    op.execute("DROP FUNCTION public.slice4_assessment_readiness_read_v1(UUID)")
    for view in reversed(_BOUNDARY_VIEWS):
        op.execute(f"DROP VIEW public.{view}")

    for name, signature, grantees in (
        ("slice4_health_profile_root_create_v1", "BIGINT,VARCHAR,UUID,UUID,UUID,UUID,UUID,UUID,UUID,BIGINT,BYTEA,VARCHAR,BYTEA,VARCHAR,TIMESTAMPTZ,VARCHAR,JSONB,UUID,BYTEA,BYTEA", (writer,)),
        ("slice4_identity_summary_current_v1", "UUID,UUID,UUID,BIGINT,UUID", (writer, readiness)),
        ("slice4_identity_summary_source_v1", "UUID,UUID", (_identity,)),
        ("slice4_detection_report_create_v1", "BIGINT,VARCHAR,UUID,UUID,UUID,UUID,VARCHAR,TIMESTAMPTZ,VARCHAR,UUID[],UUID,BYTEA,BYTEA", (writer,)),
        ("slice4_health_fact_state_transition_v1", "UUID,VARCHAR,VARCHAR,BIGINT,UUID,BIGINT,VARCHAR,VARCHAR", (os.environ["KG_HEALTH_FACT_WRITER_ROLE"],)),
        ("slice4_assessment_assembly_write_v1", "UUID,UUID,UUID,UUID,UUID,UUID,UUID,SMALLINT,VARCHAR,VARCHAR,JSONB,JSONB,VARCHAR,JSONB,UUID,BYTEA,BYTEA", (readiness,)),
    ):
        for role in grantees:
            op.execute(f'''REVOKE EXECUTE ON FUNCTION public.{name}({signature}) FROM "{role}"''')
        op.execute(f"DROP FUNCTION public.{name}({signature})")

    op.execute(f'''REVOKE SELECT(profile_public_id,subject_member_id,current_revision_id,version), UPDATE(current_revision_id,version) ON TABLE public.health_profile FROM "{writer}"''')
    op.execute(f'''REVOKE SELECT(profile_revision_id,subject_member_id,revision_no,tenant_public_id,identity_revision_ref,identity_source_version,snapshot_digest,created_at), INSERT(profile_revision_id,subject_member_id,subject_user_id,revision_no,tenant_public_id,identity_source_kind,identity_revision_ref,identity_source_version,snapshot_ciphertext,snapshot_key_id,reconfirmed_at,source_type,changed_fields,supersedes_revision_id,actor_user_id,actor_type,snapshot_digest,digest_key_id,created_at) ON TABLE public.health_profile_revision FROM "{writer}"''')
    op.drop_constraint("fk_health_profile_current_revision", "health_profile", schema="public", type_="foreignkey")
    for table in reversed(_MODULE_TABLES):
        op.drop_table(table, schema="public")
    op.drop_column("health_projection_shadow_run", "status_event_count", schema="public")
    op.drop_constraint("ck_health_projection_selection_v1_v2", "health_projection_window_selection", schema="public", type_="check")
    op.create_check_constraint("ck_health_projection_selection_rule_v1", "health_projection_window_selection", "rule_version='health-daily-selection-v1'", schema="public")
    op.create_check_constraint("ck_health_projection_selection_digest", "health_projection_window_selection", "selection_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND winner_fact_id>=1 AND subject_user_id>=1", schema="public")
    for constraint in (
        "ck_health_projection_fact_identity_v1_v2", "ck_health_projection_fact_indicator_v1_v2",
        "ck_health_projection_fact_unit_v1_v2", "ck_health_projection_fact_source_v1_v2",
        "ck_health_projection_fact_digest_v1_v2",
    ):
        op.drop_constraint(constraint, "health_projection_fact", schema="public", type_="check")
    op.create_check_constraint("ck_health_projection_fact_indicator_v1", "health_projection_fact", "indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')", schema="public")
    op.create_check_constraint("ck_health_projection_fact_unit_v1", "health_projection_fact", "((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR (indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR (indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR (indicator_code='bmi' AND unit='kg/m2') OR (indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))", schema="public")
    op.create_check_constraint("ck_health_projection_fact_source", "health_projection_fact", "source_type IN ('DEVICE','STORE','REPORT','APP')", schema="public")
    op.create_check_constraint("ck_health_projection_fact_digest", "health_projection_fact", "row_digest ~ '^[0-9a-f]{64}$' AND length(digest_key_id) BETWEEN 1 AND 64 AND fact_id>=1 AND subject_user_id>=1", schema="public")
    op.drop_constraint("ck_health_projection_shadow_run_identity_v1_v2", "health_projection_shadow_run", schema="public", type_="check")
    op.create_check_constraint("ck_health_projection_shadow_run_identity", "health_projection_shadow_run", "projection_version=1 AND run_sequence>=1 AND lease_epoch>=0 AND version>=1", schema="public")
    op.drop_constraint("ck_health_projection_generation_hwm_v1_v2", "health_projection_generation", schema="public", type_="check")
    op.drop_constraint("ck_health_projection_generation_version_v1_v2", "health_projection_generation", schema="public", type_="check")
    op.create_check_constraint("ck_health_projection_generation_version", "health_projection_generation", "projection_version=1 AND generation_no>=1 AND lease_epoch>=0 AND version>=1", schema="public")
    op.create_check_constraint("ck_health_projection_generation_high_watermark", "health_projection_generation", "jsonb_typeof(high_watermark)='object' AND high_watermark=jsonb_build_object('max_fact_id',high_watermark->'max_fact_id','source_snapshot',high_watermark->'source_snapshot') AND jsonb_typeof(high_watermark->'max_fact_id')='number' AND (high_watermark->>'max_fact_id') ~ '^(0|[1-9][0-9]*)$' AND jsonb_typeof(high_watermark->'source_snapshot')='string' AND length(high_watermark->>'source_snapshot') BETWEEN 3 AND 512", schema="public")
    op.drop_index("uq_health_projection_selection_v2", table_name="health_projection_window_selection", schema="public")
    op.drop_index("uq_health_projection_selection_v1", table_name="health_projection_window_selection", schema="public")
    op.drop_constraint("fk_health_projection_selection_winner", "health_projection_window_selection", schema="public", type_="foreignkey")
    op.alter_column("health_projection_window_selection", "subject_user_id", nullable=False, schema="public")
    op.drop_column("health_projection_window_selection", "winner_fact_ref", schema="public")
    op.drop_column("health_projection_window_selection", "subject_member_id", schema="public")
    op.create_primary_key(
        "pk_health_projection_window_selection", "health_projection_window_selection",
        ["generation_id", "subject_user_id", "indicator_code", "business_day"], schema="public",
    )
    op.create_foreign_key(
        "fk_health_projection_selection_winner_identity",
        "health_projection_window_selection",
        "health_projection_fact",
        ["generation_id", "winner_fact_id", "subject_user_id", "indicator_code", "business_day", "digest_key_id"],
        ["generation_id", "fact_id", "subject_user_id", "indicator_code", "business_day", "digest_key_id"],
        source_schema="public", referent_schema="public", ondelete="RESTRICT",
    )
    op.alter_column("health_projection_fact", "subject_user_id", nullable=False, schema="public")
    op.drop_column("health_projection_fact", "status_event_seq", schema="public")
    op.drop_column("health_projection_fact", "fact_ref", schema="public")
    op.drop_column("health_projection_fact", "subject_member_id", schema="public")
    for constraint in (
        "ck_canonical_health_fact_catalog_v1_v2",
        "ck_canonical_health_fact_indicator_v1_v2",
        "ck_canonical_health_fact_unit_v1_v2",
        "ck_canonical_health_fact_source_type_v1_v2",
    ):
        op.drop_constraint(constraint, "canonical_health_fact", schema="public", type_="check")
    op.create_check_constraint("ck_canonical_health_fact_catalog_v1", "canonical_health_fact", "catalog_version=1", schema="public")
    op.create_check_constraint("ck_canonical_health_fact_indicator_v1", "canonical_health_fact", "indicator_code IN ('systolic_bp','diastolic_bp','heart_rate','fasting_glucose','postprandial_glucose_2h','hba1c','total_cholesterol','triglyceride','hdl_c','ldl_c','weight','bmi','uric_acid','spo2','bone_density_t_score')", schema="public")
    op.create_check_constraint("ck_canonical_health_fact_unit_v1", "canonical_health_fact", "((indicator_code IN ('systolic_bp','diastolic_bp') AND unit='mmHg') OR (indicator_code='heart_rate' AND unit='bpm') OR (indicator_code IN ('fasting_glucose','postprandial_glucose_2h','total_cholesterol','triglyceride','hdl_c','ldl_c') AND unit='mmol/L') OR (indicator_code IN ('hba1c','spo2') AND unit='%') OR (indicator_code='weight' AND unit='kg') OR (indicator_code='bmi' AND unit='kg/m2') OR (indicator_code='uric_acid' AND unit='umol/L') OR (indicator_code='bone_density_t_score' AND unit='T-score'))", schema="public")
    op.create_check_constraint("ck_canonical_health_fact_source_type", "canonical_health_fact", "source_type IN ('APP','STORE','DEVICE','REPORT')", schema="public")
    op.drop_constraint("fk_canonical_health_fact_subject_member", "canonical_health_fact", schema="public", type_="foreignkey")
    op.drop_constraint("uq_canonical_health_fact_fact_ref", "canonical_health_fact", schema="public", type_="unique")
    op.alter_column("canonical_health_fact", "subject_user_id", nullable=False, schema="public")
    op.drop_column("canonical_health_fact", "subject_member_id", schema="public")
    op.drop_column("canonical_health_fact", "fact_ref", schema="public")
    op.drop_constraint("ck_detection_report_v1_v2_truth", "detection_report", schema="public", type_="check")
    op.drop_constraint("ck_detection_report_type_v1_v2", "detection_report", schema="public", type_="check")
    op.create_check_constraint(
        "ck_detection_report_type", "detection_report",
        "report_type IN ('initial_screening','store_retest','home_self_test')",
        schema="public",
    )
    op.drop_constraint("fk_detection_report_service_case", "detection_report", schema="public", type_="foreignkey")
    op.drop_constraint("fk_detection_report_subject_member", "detection_report", schema="public", type_="foreignkey")
    op.drop_constraint("uq_detection_report_report_id", "detection_report", schema="public", type_="unique")
    op.alter_column("detection_report", "user_id", nullable=False, schema="public")
    for column in (
        "created_by", "supersedes_report_id", "version", "report_status", "received_at",
        "measured_at", "source_type", "schema_version", "service_case_id", "tenant_id",
        "subject_member_id", "report_id",
    ):
        op.drop_column("detection_report", column, schema="public")
    op.drop_constraint("ck_health_profile_v1_v2_truth", "health_profile", schema="public", type_="check")
    op.drop_constraint("fk_health_profile_subject_member", "health_profile", schema="public", type_="foreignkey")
    op.drop_index("uq_health_profile_subject_member_id", table_name="health_profile", schema="public")
    op.drop_index("uq_health_profile_profile_public_id", table_name="health_profile", schema="public")
    for column in ("version", "current_revision_id", "subject_member_id", "profile_public_id"):
        op.drop_column("health_profile", column, schema="public")
    for column in ("user_id", "gender", "birth_date"):
        op.alter_column("health_profile", column, nullable=False, schema="public")
    for role in (writer, readiness, _worker, clinical, institution, _identity):
        op.execute(f'''REVOKE USAGE ON SCHEMA public FROM "{role}"''')
