"""Phase 1 Slice 2 therapist qualification and SERVICE_READY.

Revision ID: 20260817_0021
Revises: 20260816_0020
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260817_0021"
down_revision = "20260816_0020"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212012
_ROLE_VARS = (
    "KG_THERAPIST_ONBOARDING_WRITER_ROLE",
    "KG_THERAPIST_REVIEW_WRITER_ROLE",
    "KG_THERAPIST_READINESS_WORKER_ROLE",
    "KG_THERAPIST_READER_ROLE",
)
_RUNTIME_IDENTITIES = (
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
    ("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_BUILDER_ROLE", "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_PROJECTION_CONFIRMATION_ROLE", "KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_READY_GATE_DATABASE_URL"),
    ("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_READER_ROLE", "KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_WRITER_ROLE", "KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_REVIEW_WRITER_ROLE", "KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL"),
    ("KG_PRIVATE_FILE_WRITER_ROLE", "KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_READER_ROLE", "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"),
    ("KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_THERAPIST_REVIEW_WRITER_ROLE", "KG_THERAPIST_REVIEW_WRITER_DATABASE_URL"),
    ("KG_THERAPIST_READINESS_WORKER_ROLE", "KG_THERAPIST_READINESS_WORKER_DATABASE_URL"),
    ("KG_THERAPIST_READER_ROLE", "KG_THERAPIST_READER_DATABASE_URL"),
)
_TABLES = (
    "therapist_invitation", "therapist_profile", "therapist_profile_revision",
    "therapist_qualification_version", "therapist_profile_revision_qualification",
    "therapist_qualification_attachment", "therapist_review_item",
    "therapist_review_decision", "therapist_status_decision",
    "institution_service_readiness", "readiness_evidence",
    "therapist_workflow_idempotency", "therapist_workflow_audit",
    "therapist_workflow_outbox", "therapist_workflow_delivery",
)
_EVENT_TYPES = (
    "THERAPIST_INVITED", "THERAPIST_INVITATION_REVOKED", "THERAPIST_INVITATION_EXPIRED",
    "THERAPIST_ACTIVATED", "THERAPIST_SUBMITTED", "THERAPIST_CORRECTION_REQUESTED",
    "THERAPIST_RESUBMITTED", "THERAPIST_REJECTED", "THERAPIST_APPROVED_ACTIVE",
    "THERAPIST_SUSPENDED", "THERAPIST_RESUMED", "THERAPIST_EXITED",
    "THERAPIST_QUALIFICATION_RENEWAL_SUBMITTED", "THERAPIST_QUALIFICATION_REVIEWED",
    "THERAPIST_QUALIFICATION_RENEWAL_RESUBMITTED", "SERVICE_READINESS_RECOMPUTED",
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 2 database role configuration is invalid") from None


def _membership_is_unsafe(connection, roles: tuple[str, ...]) -> bool:
    rows = dict(connection.execute(sa.text("SELECT rolname,oid FROM pg_roles WHERE rolname=ANY(:roles)"), {"roles": list(roles)}).all())
    if set(rows) != set(roles):
        _configuration_error()
    return bool(connection.execute(sa.text(
        "WITH RECURSIVE paths(source_oid,target_oid,path) AS ("
        "SELECT member,roleid,ARRAY[member,roleid] FROM pg_auth_members UNION ALL "
        "SELECT paths.source_oid,m.roleid,paths.path||m.roleid FROM paths "
        "JOIN pg_auth_members m ON m.member=paths.target_oid WHERE NOT m.roleid=ANY(paths.path)) "
        "SELECT EXISTS(SELECT 1 FROM paths WHERE source_oid=ANY(:oids) OR target_oid=ANY(:oids))"
    ), {"oids": list(rows.values())}).scalar_one())


def _roles() -> tuple[str, str, str, str]:
    configured: dict[str, str] = {}
    targets = set()
    for role_var, url_var in _RUNTIME_IDENTITIES:
        role = os.getenv(role_var, "").strip()
        raw = os.getenv(url_var, "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw:
            _configuration_error()
        try:
            url = make_url(raw)
        except Exception:
            _configuration_error()
        if url.username != role or url.drivername != "postgresql+asyncpg":
            _configuration_error()
        configured[role_var] = role
        targets.add((url.host, url.port, url.database))
    if len(set(configured.values())) != len(configured) or len(targets) != 1:
        _configuration_error()
    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    values = tuple(configured[name] for name in _ROLE_VARS)
    if current_user in configured.values() or _membership_is_unsafe(
        connection, tuple(configured.values())
    ):
        _configuration_error()
    rows = connection.execute(sa.text(
        "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolinherit,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=ANY(:roles)"
    ), {"roles": sorted(configured.values())}).mappings().all()
    if len(rows) != len(configured) or any(row[flag] for row in rows for flag in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolinherit", "rolreplication", "rolbypassrls")):
        _configuration_error()
    return values  # type: ignore[return-value]


def _uuid(name: str, *, nullable: bool = False):
    return sa.Column(name, postgresql.UUID(as_uuid=False), nullable=nullable)


def _grant(role: str, privilege: str, table: str, columns: tuple[str, ...]) -> None:
    rendered = ",".join(f'"{column}"' for column in columns)
    op.execute(f'GRANT {privilege} ({rendered}) ON TABLE public."{table}" TO "{role}"')


def _create_tables() -> None:
    op.create_table("therapist_invitation",
        _uuid("invitation_id"), sa.Column("tenant_id", sa.BigInteger(), nullable=False), sa.Column("phone_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("phone_encryption_key_id", sa.String(64), nullable=False), sa.Column("phone_digest", sa.CHAR(64), nullable=False), sa.Column("phone_digest_key_id", sa.String(64), nullable=False), sa.Column("phone_masked", sa.String(16), nullable=False), sa.Column("code_digest", sa.CHAR(64), nullable=False), sa.Column("code_digest_key_id", sa.String(64), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("failed_attempts", sa.SmallInteger(), nullable=False, server_default="0"), sa.Column("issued_by", sa.BigInteger(), nullable=False), sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False), sa.Column("activated_at", sa.DateTime(timezone=True)), sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("invitation_id", name="pk_therapist_invitation"), sa.UniqueConstraint("tenant_id", "invitation_id", name="uq_therapist_invitation_tenant_id"), sa.CheckConstraint("failed_attempts BETWEEN 0 AND 5 AND version>=1", name="ck_therapist_invitation_attempts"), sa.CheckConstraint("(status='INVITED' AND activated_at IS NULL AND revoked_at IS NULL) OR (status='ACTIVATED' AND activated_at IS NOT NULL AND revoked_at IS NULL) OR (status='EXPIRED' AND activated_at IS NULL AND revoked_at IS NULL) OR (status='REVOKED' AND activated_at IS NULL AND revoked_at IS NOT NULL)", name="ck_therapist_invitation_state"), schema="public")
    op.create_table("therapist_profile",
        _uuid("therapist_id"), sa.Column("user_id", sa.BigInteger(), nullable=False), sa.Column("tenant_id", sa.BigInteger(), nullable=False), _uuid("invitation_id"), sa.Column("real_name_ciphertext", sa.LargeBinary()), sa.Column("real_name_encryption_key_id", sa.String(64)), sa.Column("real_name_digest", sa.CHAR(64)), sa.Column("real_name_digest_key_id", sa.String(64)), sa.Column("display_name", sa.String(50)), sa.Column("practice_summary", sa.String(500)), sa.Column("service_tags", postgresql.JSONB()), sa.Column("status", sa.String(24), nullable=False), sa.Column("capacity_limit", sa.SmallInteger(), nullable=False, server_default="30"), sa.Column("active_case_count", sa.SmallInteger(), nullable=False, server_default="0"), _uuid("current_qualification_version_id", nullable=True), sa.Column("current_revision_no", sa.Integer(), nullable=False, server_default="0"), sa.Column("qualification_valid_until", sa.Date()), sa.Column("suspension_reason_code", sa.String(64)), sa.Column("totp_secret_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("totp_encryption_key_id", sa.String(64), nullable=False), sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("submitted_at", sa.DateTime(timezone=True)), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("suspended_at", sa.DateTime(timezone=True)), sa.Column("resumed_at", sa.DateTime(timezone=True)), sa.Column("exited_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("therapist_id", name="pk_therapist_profile"), sa.UniqueConstraint("user_id", name="uq_therapist_profile_user"), sa.UniqueConstraint("invitation_id", name="uq_therapist_profile_invitation"), sa.CheckConstraint("capacity_limit=30 AND active_case_count BETWEEN 0 AND 30", name="ck_therapist_profile_capacity"), sa.CheckConstraint("status IN ('ACTIVATED','DRAFT','SUBMITTED','UNDER_REVIEW','NEEDS_CORRECTION','RESUBMITTED','APPROVED_ACTIVE','SUSPENDED','EXITED','REJECTED') AND version>=1 AND current_revision_no>=0 AND activated_at IS NOT NULL AND ((real_name_ciphertext IS NULL AND real_name_encryption_key_id IS NULL AND real_name_digest IS NULL AND real_name_digest_key_id IS NULL) OR (real_name_ciphertext IS NOT NULL AND real_name_encryption_key_id IS NOT NULL AND real_name_digest IS NOT NULL AND real_name_digest_key_id IS NOT NULL)) AND ((status IN ('ACTIVATED','DRAFT') AND current_revision_no=0 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NULL AND reviewed_at IS NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL) OR (status IN ('SUBMITTED','UNDER_REVIEW','RESUBMITTED') AND current_revision_no>=1 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NOT NULL AND reviewed_at IS NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL) OR (status='NEEDS_CORRECTION' AND current_revision_no>=1 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL) OR (status='APPROVED_ACTIVE' AND current_revision_no>=1 AND current_qualification_version_id IS NOT NULL AND qualification_valid_until IS NOT NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND exited_at IS NULL AND suspension_reason_code IS NULL AND ((suspended_at IS NULL AND resumed_at IS NULL) OR (suspended_at IS NOT NULL AND resumed_at IS NOT NULL AND resumed_at>suspended_at))) OR (status='SUSPENDED' AND current_revision_no>=1 AND current_qualification_version_id IS NOT NULL AND qualification_valid_until IS NOT NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND suspended_at IS NOT NULL AND suspension_reason_code IS NOT NULL AND exited_at IS NULL) OR (status='EXITED' AND current_revision_no>=1 AND current_qualification_version_id IS NOT NULL AND qualification_valid_until IS NOT NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND exited_at IS NOT NULL) OR (status='REJECTED' AND current_revision_no>=1 AND current_qualification_version_id IS NULL AND qualification_valid_until IS NULL AND submitted_at IS NOT NULL AND reviewed_at IS NOT NULL AND suspended_at IS NULL AND resumed_at IS NULL AND exited_at IS NULL AND suspension_reason_code IS NULL)) AND (status IN ('ACTIVATED','DRAFT') OR (real_name_ciphertext IS NOT NULL AND display_name IS NOT NULL AND practice_summary IS NOT NULL AND service_tags IS NOT NULL))", name="ck_therapist_profile_state"), sa.CheckConstraint("service_tags IS NULL OR (jsonb_typeof(service_tags)='array' AND jsonb_array_length(service_tags) BETWEEN 1 AND 4 AND service_tags <@ '[\"HYPERTENSION\",\"GLUCOSE_METABOLISM\",\"DYSLIPIDEMIA\",\"OBESITY\"]'::jsonb)", name="ck_therapist_profile_service_tags"), schema="public")
    op.create_table("therapist_profile_revision",
        _uuid("revision_id"), _uuid("therapist_id"), sa.Column("revision_no", sa.Integer(), nullable=False), sa.Column("profile_snapshot", postgresql.JSONB(), nullable=False), sa.Column("input_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("revision_id", name="pk_therapist_profile_revision"), sa.UniqueConstraint("therapist_id", "revision_no", name="uq_therapist_profile_revision_no"), sa.UniqueConstraint("therapist_id", "revision_id", name="uq_therapist_revision_identity"), sa.CheckConstraint("revision_no>=1", name="ck_therapist_revision_no"), schema="public")
    op.create_table("therapist_qualification_version",
        _uuid("qualification_version_id"), _uuid("therapist_id"), _uuid("profile_revision_id"), _uuid("previous_version_id", nullable=True), sa.Column("qualification_type", sa.String(48), nullable=False), sa.Column("certificate_no_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("certificate_encryption_key_id", sa.String(64), nullable=False), sa.Column("certificate_no_digest", sa.CHAR(64), nullable=False), sa.Column("certificate_digest_key_id", sa.String(64), nullable=False), sa.Column("certificate_no_masked", sa.String(16), nullable=False), sa.Column("issuer_name", sa.String(100), nullable=False), sa.Column("valid_from", sa.Date(), nullable=False), sa.Column("valid_until", sa.Date(), nullable=False), sa.Column("attachment_count", sa.SmallInteger(), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("qualification_version_id", name="pk_therapist_qualification_version"), sa.UniqueConstraint("therapist_id", "qualification_version_id", name="uq_therapist_qualification_identity"), sa.UniqueConstraint("therapist_id", "qualification_type", "version_no", name="uq_therapist_qualification_version_no"), sa.CheckConstraint("qualification_type='METABOLIC_HEALTH_PRACTICE'", name="ck_therapist_qualification_type"), sa.CheckConstraint("valid_from<=valid_until AND version_no>=1", name="ck_therapist_qualification_dates"), sa.CheckConstraint("attachment_count BETWEEN 1 AND 3", name="ck_therapist_qualification_attachment_count"), schema="public")
    op.create_table("therapist_profile_revision_qualification",
        _uuid("therapist_id"), _uuid("revision_id"), _uuid("qualification_version_id"), sa.Column("position", sa.SmallInteger(), nullable=False), sa.PrimaryKeyConstraint("revision_id", "qualification_version_id", name="pk_therapist_revision_qualification"), sa.UniqueConstraint("revision_id", "position", name="uq_therapist_revision_qualification_position"), sa.CheckConstraint("position=1", name="ck_therapist_revision_qualification_position"), schema="public")
    op.create_table("therapist_qualification_attachment",
        _uuid("qualification_version_id"), sa.Column("slot", sa.SmallInteger(), nullable=False), _uuid("private_file_id"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("qualification_version_id", "slot", name="pk_therapist_qualification_attachment"), sa.UniqueConstraint("private_file_id", name="uq_therapist_qualification_attachment_file"), sa.CheckConstraint("slot BETWEEN 1 AND 3", name="ck_therapist_qualification_attachment_slot"), schema="public")
    op.create_table("therapist_review_item",
        _uuid("review_item_id"), _uuid("therapist_id"), _uuid("revision_id"), _uuid("qualification_version_id", nullable=True), sa.Column("review_kind", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("reviewer_user_id", sa.BigInteger()), _uuid("previous_review_item_id", nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("claimed_at", sa.DateTime(timezone=True)), sa.Column("decided_at", sa.DateTime(timezone=True)), sa.Column("version", sa.BigInteger(), nullable=False), sa.PrimaryKeyConstraint("review_item_id", name="pk_therapist_review_item"), sa.UniqueConstraint("therapist_id", "review_item_id", name="uq_therapist_review_item_identity"), sa.CheckConstraint("review_kind IN ('INITIAL','RENEWAL') AND status IN ('QUEUED','UNDER_REVIEW','DECIDED') AND version>=1 AND ((review_kind='INITIAL' AND qualification_version_id IS NULL) OR (review_kind='RENEWAL' AND qualification_version_id IS NOT NULL)) AND ((status='QUEUED' AND reviewer_user_id IS NULL AND claimed_at IS NULL AND decided_at IS NULL) OR (status='UNDER_REVIEW' AND reviewer_user_id IS NOT NULL AND claimed_at IS NOT NULL AND decided_at IS NULL) OR (status='DECIDED' AND reviewer_user_id IS NOT NULL AND claimed_at IS NOT NULL AND decided_at IS NOT NULL))", name="ck_therapist_review_item_contract"), schema="public")
    op.create_table("therapist_review_decision",
        _uuid("decision_id"), _uuid("review_item_id"), _uuid("therapist_id"), _uuid("revision_id"), sa.Column("reviewer_user_id", sa.BigInteger(), nullable=False), sa.Column("decision", sa.String(24), nullable=False), sa.Column("qualification_outcomes", postgresql.JSONB(), nullable=False), sa.Column("reason_code", sa.String(64)), sa.Column("correction_fields", postgresql.JSONB()), sa.Column("request_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("decision_id", name="pk_therapist_review_decision"), sa.UniqueConstraint("review_item_id", name="uq_therapist_review_item_decision"), sa.CheckConstraint("decision IN ('NEEDS_CORRECTION','REJECTED','APPROVED') AND jsonb_typeof(qualification_outcomes)='object' AND ((decision='NEEDS_CORRECTION' AND reason_code IS NOT NULL AND qualification_outcomes='{}'::jsonb AND jsonb_typeof(correction_fields)='array' AND jsonb_array_length(correction_fields)>0) OR (decision='REJECTED' AND reason_code IS NOT NULL AND correction_fields IS NULL AND qualification_outcomes<>'{}'::jsonb) OR (decision='APPROVED' AND reason_code IS NULL AND correction_fields IS NULL AND qualification_outcomes<>'{}'::jsonb))", name="ck_therapist_review_decision_contract"), schema="public")
    op.create_table("therapist_status_decision",
        _uuid("status_decision_id"), _uuid("therapist_id"), sa.Column("actor_kind", sa.String(16), nullable=False), sa.Column("actor_user_id", sa.BigInteger()), sa.Column("worker_identity", sa.String(64)), sa.Column("decision", sa.String(16), nullable=False), sa.Column("reason_code", sa.String(64), nullable=False), sa.Column("expected_profile_version", sa.BigInteger(), nullable=False), sa.Column("request_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("status_decision_id", name="pk_therapist_status_decision"), sa.UniqueConstraint("therapist_id", "expected_profile_version", name="uq_therapist_status_decision_profile_version"), sa.CheckConstraint("decision IN ('SUSPENDED','RESUMED','EXITED') AND expected_profile_version>=1 AND ((actor_kind='USER' AND actor_user_id IS NOT NULL AND worker_identity IS NULL) OR (actor_kind='EXPIRY_WORKER' AND actor_user_id IS NULL AND worker_identity='THERAPIST_EXPIRY_WORKER_V1' AND decision='SUSPENDED')) AND (decision<>'RESUMED' OR reason_code='QUALIFICATION_RENEWED')", name="ck_therapist_status_decision_contract"), schema="public")
    op.create_table("institution_service_readiness",
        sa.Column("tenant_id", sa.BigInteger(), nullable=False), sa.Column("readiness_status", sa.String(16), nullable=False), sa.Column("reason_codes", postgresql.ARRAY(sa.Text()), nullable=False), sa.Column("qualified_therapist_count", sa.Integer(), nullable=False), sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("evidence_version", sa.BigInteger(), nullable=False), sa.Column("input_digest", sa.CHAR(64), nullable=False), sa.Column("result_digest", sa.CHAR(64), nullable=False), sa.Column("source_versions", postgresql.JSONB(), nullable=False), sa.Column("next_expiry_at", sa.Date()), sa.Column("version", sa.BigInteger(), nullable=False), sa.PrimaryKeyConstraint("tenant_id", name="pk_institution_service_readiness"), sa.CheckConstraint("version>=1 AND evidence_version>=1 AND qualified_therapist_count>=0 AND jsonb_typeof(source_versions)='object' AND ((readiness_status='SERVICE_READY' AND cardinality(reason_codes)=0 AND qualified_therapist_count>=1 AND next_expiry_at IS NOT NULL) OR (readiness_status='NOT_READY' AND cardinality(reason_codes)>0))", name="ck_institution_service_readiness_truth"), sa.CheckConstraint("reason_codes <@ ARRAY['TENANT_NOT_ACTIVE','INSTITUTION_LICENSE_INVALID','NO_APPROVED_ACTIVE_THERAPIST','METABOLIC_SCOPE_MISSING','COMPLIANCE_SUSPENDED','INSTITUTION_APPROVAL_SOURCE_INVALID']::text[]", name="ck_institution_service_readiness_reasons"), schema="public")
    op.create_table("readiness_evidence",
        _uuid("evidence_id"), sa.Column("tenant_id", sa.BigInteger(), nullable=False), sa.Column("evidence_version", sa.BigInteger(), nullable=False), sa.Column("readiness_status", sa.String(16), nullable=False), sa.Column("reason_codes", postgresql.ARRAY(sa.Text()), nullable=False), sa.Column("qualified_therapist_count", sa.Integer(), nullable=False), sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("tenant_status", sa.String(16), nullable=False), sa.Column("institution_license_digest", sa.CHAR(64), nullable=False), sa.Column("therapist_set_digest", sa.CHAR(64), nullable=False), sa.Column("service_scope_digest", sa.CHAR(64), nullable=False), sa.Column("source_versions", postgresql.JSONB(), nullable=False), sa.Column("next_expiry_at", sa.Date()), _uuid("trigger_event_id"), sa.Column("input_digest", sa.CHAR(64), nullable=False), sa.Column("result_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("evidence_id", name="pk_readiness_evidence"), sa.UniqueConstraint("tenant_id", "evidence_version", name="uq_readiness_evidence_version"), sa.UniqueConstraint("trigger_event_id", name="uq_readiness_evidence_trigger"), sa.CheckConstraint("evidence_version>=1 AND qualified_therapist_count>=0 AND jsonb_typeof(source_versions)='object'", name="ck_readiness_evidence_truth"), schema="public")
    op.create_table("therapist_workflow_idempotency",
        sa.Column("actor_scope", sa.String(160), nullable=False), sa.Column("operation", sa.String(64), nullable=False), sa.Column("idempotency_key", sa.String(128), nullable=False), sa.Column("request_digest", sa.CHAR(64), nullable=False), sa.Column("response_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("response_encryption_key_id", sa.String(64), nullable=False), sa.Column("postimage_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("actor_scope", "operation", "idempotency_key", name="pk_therapist_workflow_idempotency"), schema="public")
    op.create_table("therapist_workflow_audit",
        sa.Column("audit_id", sa.BigInteger(), autoincrement=True, nullable=False), sa.Column("actor_scope", sa.String(160), nullable=False), sa.Column("action", sa.String(64), nullable=False), _uuid("object_id"), sa.Column("result", sa.String(16), nullable=False), sa.Column("reason_code", sa.String(64)), _uuid("request_id"), sa.Column("preimage_digest", sa.CHAR(64), nullable=False), sa.Column("postimage_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("audit_id", name="pk_therapist_workflow_audit"), schema="public")
    event_list = ",".join(f"'{value}'" for value in _EVENT_TYPES)
    op.create_table("therapist_workflow_outbox",
        _uuid("event_id"), sa.Column("event_type", sa.String(64), nullable=False), _uuid("aggregate_id"), sa.Column("tenant_id", sa.BigInteger(), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("payload_digest", sa.CHAR(64), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default="0"), sa.Column("processing_at", sa.DateTime(timezone=True)), _uuid("lease_owner", nullable=True), sa.Column("delivered_at", sa.DateTime(timezone=True)), sa.Column("failed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("version", sa.BigInteger(), nullable=False), sa.PrimaryKeyConstraint("event_id", name="pk_therapist_workflow_outbox"), sa.CheckConstraint("attempts BETWEEN 0 AND 3 AND version>=1 AND ((status='PENDING' AND attempts BETWEEN 0 AND 2 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NULL) OR (status='PROCESSING' AND attempts BETWEEN 1 AND 3 AND processing_at IS NOT NULL AND lease_owner IS NOT NULL AND delivered_at IS NULL AND failed_at IS NULL) OR (status='DELIVERED' AND attempts BETWEEN 1 AND 3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NOT NULL AND failed_at IS NULL) OR (status='FAILED' AND attempts=3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NOT NULL))", name="ck_therapist_workflow_outbox_state"), sa.CheckConstraint(f"event_type IN ({event_list})", name="ck_therapist_workflow_outbox_event_type"), schema="public")
    op.create_table("therapist_workflow_delivery",
        _uuid("delivery_id"), _uuid("event_id"), sa.Column("recipient_scope", sa.String(32), nullable=False), sa.Column("recipient_user_id", sa.BigInteger()), sa.Column("recipient_tenant_id", sa.BigInteger()), sa.Column("recipient_platform_code", sa.String(32)), sa.Column("target_digest", sa.CHAR(64), nullable=False), sa.Column("target_digest_key_id", sa.String(64), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("payload_digest", sa.CHAR(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.PrimaryKeyConstraint("delivery_id", name="pk_therapist_workflow_delivery"), sa.UniqueConstraint("event_id", "target_digest_key_id", "target_digest", name="uq_therapist_workflow_delivery_target"), sa.CheckConstraint("jsonb_typeof(payload)='object' AND ((recipient_scope='THERAPIST' AND recipient_user_id IS NOT NULL AND recipient_tenant_id IS NULL AND recipient_platform_code IS NULL) OR (recipient_scope='TENANT_ADMIN' AND recipient_user_id IS NOT NULL AND recipient_tenant_id IS NOT NULL AND recipient_platform_code IS NULL) OR (recipient_scope='PLATFORM' AND recipient_user_id IS NULL AND recipient_tenant_id IS NULL AND recipient_platform_code='THERAPIST_REVIEW_QUEUE'))", name="ck_therapist_workflow_delivery_contract"), schema="public")


def _create_indexes_and_fks() -> None:
    op.create_index("uq_therapist_invitation_open_phone", "therapist_invitation", ["tenant_id", "phone_digest_key_id", "phone_digest"], unique=True, schema="public", postgresql_where=sa.text("status='INVITED'"))
    op.create_index("ix_therapist_invitation_phone_digest_lookup", "therapist_invitation", ["tenant_id", "phone_digest_key_id", "phone_digest", "status"], schema="public")
    op.create_index("ix_therapist_profile_tenant_status", "therapist_profile", ["tenant_id", "status", "therapist_id"], schema="public")
    op.create_index("ix_therapist_qualification_expiry", "therapist_qualification_version", ["valid_until", "therapist_id", "qualification_version_id"], schema="public")
    op.create_index("ix_therapist_qualification_certificate_digest_lookup", "therapist_qualification_version", ["therapist_id", "certificate_digest_key_id", "certificate_no_digest"], schema="public")
    op.create_index("ix_therapist_review_queue", "therapist_review_item", ["status", "created_at", "review_item_id"], schema="public")
    op.create_index("ix_therapist_workflow_outbox_claim", "therapist_workflow_outbox", ["status", "processing_at", "created_at", "event_id"], schema="public")
    fks = (
        ("fk_therapist_invitation_tenant", "therapist_invitation", "tenant", ["tenant_id"], ["id"]),
        ("fk_therapist_invitation_issued_by", "therapist_invitation", "user", ["issued_by"], ["id"]),
        ("fk_therapist_profile_user", "therapist_profile", "user", ["user_id"], ["id"]),
        ("fk_therapist_profile_tenant", "therapist_profile", "tenant", ["tenant_id"], ["id"]),
        ("fk_therapist_profile_invitation_tenant", "therapist_profile", "therapist_invitation", ["tenant_id", "invitation_id"], ["tenant_id", "invitation_id"]),
        ("fk_therapist_profile_current_qualification", "therapist_profile", "therapist_qualification_version", ["therapist_id", "current_qualification_version_id"], ["therapist_id", "qualification_version_id"]),
        ("fk_therapist_revision_profile", "therapist_profile_revision", "therapist_profile", ["therapist_id"], ["therapist_id"]),
        ("fk_therapist_qualification_profile", "therapist_qualification_version", "therapist_profile", ["therapist_id"], ["therapist_id"]),
        ("fk_therapist_qualification_revision", "therapist_qualification_version", "therapist_profile_revision", ["therapist_id", "profile_revision_id"], ["therapist_id", "revision_id"]),
        ("fk_therapist_qualification_previous", "therapist_qualification_version", "therapist_qualification_version", ["therapist_id", "previous_version_id"], ["therapist_id", "qualification_version_id"]),
        ("fk_therapist_revision_qualification_revision", "therapist_profile_revision_qualification", "therapist_profile_revision", ["therapist_id", "revision_id"], ["therapist_id", "revision_id"]),
        ("fk_therapist_revision_qualification_version", "therapist_profile_revision_qualification", "therapist_qualification_version", ["therapist_id", "qualification_version_id"], ["therapist_id", "qualification_version_id"]),
        ("fk_therapist_attachment_qualification", "therapist_qualification_attachment", "therapist_qualification_version", ["qualification_version_id"], ["qualification_version_id"]),
        ("fk_therapist_attachment_private_file", "therapist_qualification_attachment", "private_file", ["private_file_id"], ["file_id"]),
        ("fk_therapist_review_item_profile", "therapist_review_item", "therapist_profile", ["therapist_id"], ["therapist_id"]),
        ("fk_therapist_review_item_revision", "therapist_review_item", "therapist_profile_revision", ["therapist_id", "revision_id"], ["therapist_id", "revision_id"]),
        ("fk_therapist_review_item_qualification", "therapist_review_item", "therapist_qualification_version", ["therapist_id", "qualification_version_id"], ["therapist_id", "qualification_version_id"]),
        ("fk_therapist_review_item_previous", "therapist_review_item", "therapist_review_item", ["therapist_id", "previous_review_item_id"], ["therapist_id", "review_item_id"]),
        ("fk_therapist_review_item_reviewer", "therapist_review_item", "user", ["reviewer_user_id"], ["id"]),
        ("fk_therapist_review_decision_item", "therapist_review_decision", "therapist_review_item", ["review_item_id"], ["review_item_id"]),
        ("fk_therapist_review_decision_item_identity", "therapist_review_decision", "therapist_review_item", ["therapist_id", "review_item_id"], ["therapist_id", "review_item_id"]),
        ("fk_therapist_review_decision_revision", "therapist_review_decision", "therapist_profile_revision", ["therapist_id", "revision_id"], ["therapist_id", "revision_id"]),
        ("fk_therapist_review_decision_user", "therapist_review_decision", "user", ["reviewer_user_id"], ["id"]),
        ("fk_therapist_status_decision_profile", "therapist_status_decision", "therapist_profile", ["therapist_id"], ["therapist_id"]),
        ("fk_therapist_status_decision_user", "therapist_status_decision", "user", ["actor_user_id"], ["id"]),
        ("fk_institution_service_readiness_tenant", "institution_service_readiness", "tenant", ["tenant_id"], ["id"]),
        ("fk_readiness_evidence_tenant", "readiness_evidence", "tenant", ["tenant_id"], ["id"]),
        ("fk_therapist_workflow_outbox_tenant", "therapist_workflow_outbox", "tenant", ["tenant_id"], ["id"]),
        ("fk_therapist_workflow_delivery_event", "therapist_workflow_delivery", "therapist_workflow_outbox", ["event_id"], ["event_id"]),
        ("fk_therapist_workflow_delivery_user", "therapist_workflow_delivery", "user", ["recipient_user_id"], ["id"]),
        ("fk_therapist_workflow_delivery_tenant", "therapist_workflow_delivery", "tenant", ["recipient_tenant_id"], ["id"]),
    )
    for name, source, target, source_columns, target_columns in fks:
        op.create_foreign_key(name, source, target, source_columns, target_columns, source_schema="public", referent_schema="public")


def _create_safe_interfaces() -> None:
    op.execute("""CREATE VIEW public.institution_readiness_source_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT t.id AS tenant_id,a.tenant_public_id,a.application_id,a.version AS application_version,
a.institution_type,a.draft_payload->'service_tags' AS service_tags,l.license_id,l.license_type,l.valid_from,l.valid_until
FROM public.tenant t JOIN public.institution_application a ON a.tenant_public_id IS NOT NULL
JOIN public.institution_license l ON l.application_id=a.application_id
WHERE a.status='APPROVED' AND t.id=a.tenant_internal_id""")
    op.execute("""CREATE FUNCTION public.therapist_totp_for_login_v1(p_user_id BIGINT)
RETURNS TABLE(therapist_id UUID,tenant_public_id UUID,totp_secret_ciphertext BYTEA,totp_encryption_key_id VARCHAR,totp_enabled BOOLEAN,therapist_status VARCHAR,tenant_id BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
SELECT p.therapist_id,a.tenant_public_id,p.totp_secret_ciphertext,p.totp_encryption_key_id,p.totp_enabled,p.status,p.tenant_id
FROM public.therapist_profile p JOIN public.institution_application a ON a.tenant_internal_id=p.tenant_id AND a.status='APPROVED'
WHERE p.user_id=p_user_id$$""")
    op.execute("""CREATE FUNCTION public.therapist_qualification_file_relation_v1(p_file_id UUID)
RETURNS TABLE(qualification_bound BOOLEAN,reviewer_access BOOLEAN)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
SELECT EXISTS(
         SELECT 1 FROM public.therapist_qualification_attachment a
         WHERE a.private_file_id=p_file_id
       ),
       EXISTS(
         SELECT 1
         FROM public.therapist_qualification_attachment a
         JOIN public.therapist_qualification_version q
           ON q.qualification_version_id=a.qualification_version_id
         JOIN public.therapist_review_item i
           ON i.therapist_id=q.therapist_id
          AND i.revision_id=q.profile_revision_id
          AND i.status IN ('QUEUED','UNDER_REVIEW')
         WHERE a.private_file_id=p_file_id
       )$$""")
    op.execute("""CREATE FUNCTION public.lock_therapist_qualification_files_v1(
 p_file_ids UUID[],p_owner_user_id BIGINT)
RETURNS TABLE(file_id UUID,bound_application_id UUID)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
BEGIN
 IF p_file_ids IS NULL OR cardinality(p_file_ids) NOT BETWEEN 1 AND 3
    OR EXISTS(SELECT 1 FROM unnest(p_file_ids) AS source(item) WHERE item IS NULL)
    OR (SELECT count(DISTINCT item) FROM unnest(p_file_ids) AS source(item))<>cardinality(p_file_ids) THEN
   RETURN;
 END IF;
 PERFORM 1
 FROM public.private_file f
 WHERE f.file_id=ANY(p_file_ids)
   AND f.owner_user_id=p_owner_user_id
   AND f.purpose='THERAPIST_QUALIFICATION'
   AND f.status='CLEAN'
   AND f.bound_application_id IS NULL
 ORDER BY f.file_id
 FOR UPDATE OF f;
 RETURN QUERY
 SELECT f.file_id,f.bound_application_id
 FROM public.private_file f
 WHERE f.file_id=ANY(p_file_ids)
   AND f.owner_user_id=p_owner_user_id
   AND f.purpose='THERAPIST_QUALIFICATION'
   AND f.status='CLEAN'
   AND f.bound_application_id IS NULL
   AND NOT EXISTS(
     SELECT 1 FROM public.therapist_qualification_attachment a
     WHERE a.private_file_id=f.file_id
   )
 ORDER BY f.file_id;
END$$""")
    op.execute("""CREATE FUNCTION public.lock_therapist_qualification_review_files_v1(
 p_qualification_version_id UUID,p_expected_count INTEGER)
RETURNS BOOLEAN
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_count INTEGER; v_valid BOOLEAN;
BEGIN
 IF p_expected_count NOT BETWEEN 1 AND 3 THEN RETURN FALSE; END IF;
 PERFORM 1
 FROM public.therapist_qualification_attachment a
 JOIN public.private_file f ON f.file_id=a.private_file_id
 WHERE a.qualification_version_id=p_qualification_version_id
 ORDER BY a.slot
 FOR SHARE OF a,f;
 SELECT count(*),COALESCE(bool_and(
   f.purpose='THERAPIST_QUALIFICATION' AND f.status='CLEAN'
 ),FALSE)
 INTO v_count,v_valid
 FROM public.therapist_qualification_attachment a
 JOIN public.private_file f ON f.file_id=a.private_file_id
 WHERE a.qualification_version_id=p_qualification_version_id;
 RETURN v_count=p_expected_count AND v_valid;
END$$""")
    op.execute("""CREATE VIEW public.institution_readiness_guard_v1 WITH (security_barrier=true,security_invoker=false) AS
SELECT t.id AS tenant_id,a.tenant_public_id,t.status AS tenant_status,
       a.application_id,a.version AS application_version,a.institution_type,
       a.draft_payload->'service_tags' AS service_tags,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('license_id',l.license_id,'license_type',l.license_type,'valid_from',l.valid_from,'valid_until',l.valid_until) ORDER BY l.license_type COLLATE "C",l.license_id)
                FROM public.institution_license l WHERE l.application_id=a.application_id),'[]'::jsonb) AS license_versions,
       COALESCE((SELECT jsonb_agg(jsonb_build_object('therapist_id',p.therapist_id,'status',p.status,'version',p.version,'qualification_version_id',p.current_qualification_version_id,'valid_until',p.qualification_valid_until) ORDER BY p.therapist_id)
                FROM public.therapist_profile p WHERE p.tenant_id=t.id),'[]'::jsonb) AS current_therapist_versions,
       LEAST(
         (SELECT min(p.qualification_valid_until) FROM public.therapist_profile p WHERE p.tenant_id=t.id AND p.status='APPROVED_ACTIVE'),
         (SELECT min(l.valid_until) FROM public.institution_license l WHERE l.application_id=a.application_id)
       ) AS next_expiry_at
FROM public.tenant t JOIN public.institution_application a
  ON a.tenant_internal_id=t.id AND a.status='APPROVED' AND a.tenant_public_id IS NOT NULL""")
    op.execute("""CREATE FUNCTION public.is_current_slice2_recovery_actor_v1(p_user_id BIGINT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
SELECT EXISTS(
  SELECT 1 FROM public."user" u
  WHERE u.id=p_user_id AND u.role='super_admin' AND u.status='active'
    AND u.tenant_id IS NULL
)$$""")
    op.execute("""CREATE FUNCTION public.record_therapist_expiry_suspension_v1(
 p_decision_id UUID,p_therapist_id UUID,p_expected_version BIGINT,p_request_digest CHAR(64))
RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_profile public.therapist_profile%ROWTYPE;
BEGIN
 SELECT * INTO v_profile FROM public.therapist_profile WHERE therapist_id=p_therapist_id FOR UPDATE;
 IF NOT FOUND OR v_profile.status<>'APPROVED_ACTIVE' OR v_profile.version<>p_expected_version
    OR v_profile.qualification_valid_until >= (transaction_timestamp() AT TIME ZONE 'Asia/Shanghai')::date THEN
   RETURN FALSE;
 END IF;
 INSERT INTO public.therapist_status_decision(status_decision_id,therapist_id,actor_kind,actor_user_id,worker_identity,decision,reason_code,expected_profile_version,request_digest,created_at)
 VALUES(p_decision_id,p_therapist_id,'EXPIRY_WORKER',NULL,'THERAPIST_EXPIRY_WORKER_V1','SUSPENDED','QUALIFICATION_EXPIRED',p_expected_version,p_request_digest,transaction_timestamp());
 RETURN TRUE;
END$$""")


def _create_integrity_contracts() -> None:
    op.execute("""CREATE FUNCTION public.enforce_therapist_service_tags_canonical_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE rebuilt jsonb;
BEGIN
 IF NEW.service_tags IS NULL THEN RETURN NEW; END IF;
 IF jsonb_typeof(NEW.service_tags)<>'array' OR EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.service_tags) v WHERE jsonb_typeof(v)<>'string') THEN
   RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist service tags are invalid';
 END IF;
 SELECT jsonb_agg(value ORDER BY value COLLATE "C") INTO rebuilt FROM (SELECT DISTINCT jsonb_array_elements_text(NEW.service_tags) AS value) s;
 IF rebuilt IS DISTINCT FROM NEW.service_tags THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist service tags are not canonical'; END IF;
 RETURN NEW;
END$$""")
    op.execute("""CREATE TRIGGER trg_therapist_profile_service_tags_canonical
BEFORE INSERT OR UPDATE OF service_tags ON public.therapist_profile
FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_service_tags_canonical_v1()""")
    op.execute("""CREATE FUNCTION public.enforce_readiness_reasons_canonical_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
DECLARE rebuilt text[];
BEGIN
 SELECT array_agg(value ORDER BY value COLLATE "C") INTO rebuilt FROM (SELECT DISTINCT unnest(NEW.reason_codes) AS value) s;
 rebuilt := COALESCE(rebuilt,ARRAY[]::text[]);
 IF rebuilt IS DISTINCT FROM NEW.reason_codes THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='readiness reasons are not canonical'; END IF;
 RETURN NEW;
END$$""")
    op.execute("""CREATE TRIGGER trg_institution_service_readiness_reasons_canonical BEFORE INSERT OR UPDATE OF reason_codes ON public.institution_service_readiness FOR EACH ROW EXECUTE FUNCTION public.enforce_readiness_reasons_canonical_v1()""")
    op.execute("""CREATE TRIGGER trg_readiness_evidence_reasons_canonical BEFORE INSERT OR UPDATE OF reason_codes ON public.readiness_evidence FOR EACH ROW EXECUTE FUNCTION public.enforce_readiness_reasons_canonical_v1()""")
    op.execute("""CREATE FUNCTION public.enforce_therapist_revision_qualification_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_revision UUID; v_count integer;
BEGIN
 v_revision:=COALESCE(NEW.revision_id,OLD.revision_id);
 IF EXISTS(SELECT 1 FROM public.therapist_profile_revision r WHERE r.revision_id=v_revision) THEN
   SELECT count(*) INTO v_count FROM public.therapist_profile_revision_qualification q WHERE q.revision_id=v_revision AND q.position=1;
   IF v_count<>1 THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist revision qualification is incomplete'; END IF;
 END IF;
 RETURN NULL;
END$$""")
    op.execute("""CREATE CONSTRAINT TRIGGER cktrg_therapist_revision_qualification_integrity AFTER INSERT OR UPDATE OR DELETE ON public.therapist_profile_revision_qualification DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_revision_qualification_v1()""")
    op.execute("""CREATE CONSTRAINT TRIGGER cktrg_therapist_revision_qualification_revision AFTER INSERT OR UPDATE ON public.therapist_profile_revision DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_revision_qualification_v1()""")
    op.execute("""CREATE FUNCTION public.enforce_therapist_qualification_attachments_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_id UUID; v_expected integer; v_actual integer;
BEGIN
 v_id:=COALESCE(NEW.qualification_version_id,OLD.qualification_version_id);
 SELECT attachment_count INTO v_expected FROM public.therapist_qualification_version WHERE qualification_version_id=v_id;
 IF FOUND THEN
   SELECT count(*) INTO v_actual FROM public.therapist_qualification_attachment WHERE qualification_version_id=v_id;
   IF v_actual<>v_expected OR v_actual NOT BETWEEN 1 AND 3 THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist qualification attachments are incomplete'; END IF;
 END IF;
 RETURN NULL;
END$$""")
    op.execute("""CREATE CONSTRAINT TRIGGER cktrg_therapist_qualification_attachment_count AFTER INSERT OR UPDATE OR DELETE ON public.therapist_qualification_attachment DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_qualification_attachments_v1()""")
    op.execute("""CREATE CONSTRAINT TRIGGER cktrg_therapist_qualification_attachment_version AFTER INSERT OR UPDATE ON public.therapist_qualification_version DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_qualification_attachments_v1()""")
    op.execute("""CREATE FUNCTION public.enforce_therapist_review_decision_v1()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_item UUID; v_status varchar; v_kind varchar; v_q UUID; v_previous UUID; v_count integer; v_decision public.therapist_review_decision%ROWTYPE;
BEGIN
 v_item:=COALESCE(NEW.review_item_id,OLD.review_item_id);
 SELECT i.status,i.review_kind,q.qualification_version_id,q.previous_version_id INTO v_status,v_kind,v_q,v_previous
 FROM public.therapist_review_item i
 JOIN public.therapist_profile_revision_qualification rq ON rq.therapist_id=i.therapist_id AND rq.revision_id=i.revision_id AND rq.position=1
 JOIN public.therapist_qualification_version q ON q.therapist_id=rq.therapist_id AND q.qualification_version_id=rq.qualification_version_id
 WHERE i.review_item_id=v_item;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT count(*) INTO v_count FROM public.therapist_review_decision d WHERE d.review_item_id=v_item;
 IF (v_status='DECIDED' AND v_count<>1) OR (v_status<>'DECIDED' AND v_count<>0) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist review decision state is invalid'; END IF;
 IF v_count=1 THEN
   SELECT * INTO v_decision FROM public.therapist_review_decision d WHERE d.review_item_id=v_item;
   IF v_decision.decision='REJECTED' AND v_decision.qualification_outcomes<>jsonb_build_object(v_q::text,'REJECTED') THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist review outcome is invalid'; END IF;
   IF v_decision.decision='APPROVED' AND ((v_kind='INITIAL' AND v_decision.qualification_outcomes<>jsonb_build_object(v_q::text,'APPROVED')) OR (v_kind='RENEWAL' AND (v_previous IS NULL OR v_decision.qualification_outcomes<>jsonb_build_object(v_q::text,'APPROVED',v_previous::text,'SUPERSEDED')))) THEN RAISE EXCEPTION USING ERRCODE='23514',MESSAGE='therapist review outcome is invalid'; END IF;
 END IF;
 RETURN NULL;
END$$""")
    op.execute("""CREATE CONSTRAINT TRIGGER cktrg_therapist_review_decision_item AFTER INSERT OR UPDATE ON public.therapist_review_item DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_review_decision_v1()""")
    op.execute("""CREATE CONSTRAINT TRIGGER cktrg_therapist_review_decision_fact AFTER INSERT OR UPDATE OR DELETE ON public.therapist_review_decision DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_therapist_review_decision_v1()""")
    op.execute("""CREATE FUNCTION public.resolve_tenant_admin_delivery_targets_v1(p_event_id UUID)
RETURNS TABLE(recipient_user_id BIGINT,recipient_tenant_id BIGINT)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event public.therapist_workflow_outbox%ROWTYPE; v_valid BOOLEAN := FALSE;
BEGIN
 SELECT * INTO v_event FROM public.therapist_workflow_outbox WHERE event_id=p_event_id FOR UPDATE;
 IF NOT FOUND OR v_event.status<>'PROCESSING' THEN RETURN; END IF;
 IF v_event.event_type IN ('THERAPIST_INVITED','THERAPIST_INVITATION_REVOKED','THERAPIST_INVITATION_EXPIRED') THEN
   SELECT EXISTS(SELECT 1 FROM public.therapist_invitation i WHERE i.invitation_id=v_event.aggregate_id AND i.tenant_id=v_event.tenant_id) INTO v_valid;
 ELSIF v_event.event_type IN ('THERAPIST_ACTIVATED','THERAPIST_EXITED') THEN
   SELECT EXISTS(SELECT 1 FROM public.therapist_profile p WHERE p.therapist_id=v_event.aggregate_id AND p.tenant_id=v_event.tenant_id) INTO v_valid;
 ELSIF v_event.event_type='SERVICE_READINESS_RECOMPUTED' THEN
   SELECT EXISTS(SELECT 1 FROM public.institution_application a WHERE a.tenant_public_id=v_event.aggregate_id AND a.tenant_internal_id=v_event.tenant_id AND a.status='APPROVED') INTO v_valid;
 END IF;
 IF NOT v_valid THEN RETURN; END IF;
 PERFORM 1 FROM public.tenant t WHERE t.id=v_event.tenant_id AND t.status='active' FOR SHARE;
 IF NOT FOUND THEN RETURN; END IF;
 RETURN QUERY SELECT u.id,v_event.tenant_id FROM public."user" u
  WHERE u.tenant_id=v_event.tenant_id AND u.status='active' AND u.role IN ('org_admin','org_operator')
  ORDER BY u.id FOR SHARE OF u;
END$$""")
    op.execute("""CREATE FUNCTION public.lock_delivery_recipient_currentness_v1(
 p_event_id UUID,p_scope VARCHAR(32),p_user_id BIGINT,p_recipient_tenant_id BIGINT,p_platform_code VARCHAR(32))
RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE v_event public.therapist_workflow_outbox%ROWTYPE; v_user BIGINT; v_valid BOOLEAN := FALSE;
BEGIN
 SELECT * INTO v_event FROM public.therapist_workflow_outbox WHERE event_id=p_event_id FOR UPDATE;
 IF NOT FOUND OR v_event.status<>'PROCESSING' THEN RETURN FALSE; END IF;
 IF p_scope='PLATFORM' THEN
   RETURN p_user_id IS NULL AND p_recipient_tenant_id IS NULL AND p_platform_code='THERAPIST_REVIEW_QUEUE'
      AND v_event.event_type IN ('THERAPIST_SUBMITTED','THERAPIST_RESUBMITTED','THERAPIST_QUALIFICATION_RENEWAL_SUBMITTED','THERAPIST_QUALIFICATION_RENEWAL_RESUBMITTED')
      AND EXISTS(SELECT 1 FROM public.therapist_review_item i JOIN public.therapist_profile p ON p.therapist_id=i.therapist_id WHERE i.review_item_id=v_event.aggregate_id AND p.tenant_id=v_event.tenant_id);
 ELSIF p_scope='TENANT_ADMIN' THEN
   IF p_platform_code IS NOT NULL OR p_user_id IS NULL OR p_recipient_tenant_id<>v_event.tenant_id THEN RETURN FALSE; END IF;
   PERFORM 1 FROM public."user" u JOIN public.tenant t ON t.id=u.tenant_id
    WHERE u.id=p_user_id AND u.tenant_id=v_event.tenant_id AND u.status='active' AND u.role IN ('org_admin','org_operator') AND t.status='active'
    FOR SHARE OF u,t;
   RETURN FOUND;
 ELSIF p_scope='THERAPIST' THEN
   IF p_platform_code IS NOT NULL OR p_recipient_tenant_id IS NOT NULL OR p_user_id IS NULL THEN RETURN FALSE; END IF;
   IF v_event.event_type IN ('THERAPIST_CORRECTION_REQUESTED','THERAPIST_QUALIFICATION_REVIEWED') THEN
     SELECT p.user_id INTO v_user FROM public.therapist_review_item i JOIN public.therapist_profile p ON p.therapist_id=i.therapist_id WHERE i.review_item_id=v_event.aggregate_id AND p.tenant_id=v_event.tenant_id FOR SHARE OF p;
   ELSIF v_event.event_type IN ('THERAPIST_REJECTED','THERAPIST_APPROVED_ACTIVE','THERAPIST_SUSPENDED','THERAPIST_RESUMED') THEN
     SELECT p.user_id INTO v_user FROM public.therapist_profile p WHERE p.therapist_id=v_event.aggregate_id AND p.tenant_id=v_event.tenant_id FOR SHARE OF p;
   ELSE RETURN FALSE; END IF;
   IF v_user IS DISTINCT FROM p_user_id THEN RETURN FALSE; END IF;
   PERFORM 1 FROM public."user" u JOIN public.tenant t ON t.id=u.tenant_id WHERE u.id=p_user_id AND u.status='active' AND u.role='therapist' AND u.tenant_id=v_event.tenant_id AND t.status='active' FOR SHARE OF u,t;
   RETURN FOUND;
 END IF;
 RETURN FALSE;
END$$""")


def _acl(roles: tuple[str, str, str, str]) -> None:
    onboarding, reviewer, worker, reader = roles
    all_roles = ",".join(f'"{role}"' for role in roles)
    for table in _TABLES:
        op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public."{table}" FROM PUBLIC,{all_roles}')
    op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.institution_readiness_source_v1 FROM PUBLIC,{all_roles}')
    op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.institution_readiness_guard_v1 FROM PUBLIC,{all_roles}')
    op.execute("REVOKE ALL ON FUNCTION public.therapist_totp_for_login_v1(BIGINT) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.record_therapist_expiry_suspension_v1(UUID,UUID,BIGINT,CHAR) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.resolve_tenant_admin_delivery_targets_v1(UUID) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.lock_delivery_recipient_currentness_v1(UUID,VARCHAR,BIGINT,BIGINT,VARCHAR) FROM PUBLIC")
    # Every allow-list is column-explicit. Table-level DML is intentionally absent.
    invitation_select = ("invitation_id","tenant_id","phone_digest","phone_digest_key_id","phone_masked","code_digest","code_digest_key_id","expires_at","status","failed_attempts","issued_by","issued_at","activated_at","revoked_at","version")
    invitation_insert = ("invitation_id","tenant_id","phone_ciphertext","phone_encryption_key_id","phone_digest","phone_digest_key_id","phone_masked","code_digest","code_digest_key_id","expires_at","status","failed_attempts","issued_by","issued_at","version")
    profile_business = ("therapist_id","user_id","tenant_id","invitation_id","real_name_ciphertext","real_name_encryption_key_id","real_name_digest","real_name_digest_key_id","display_name","practice_summary","service_tags","status","capacity_limit","active_case_count","current_revision_no","current_qualification_version_id","qualification_valid_until","suspension_reason_code","activated_at","submitted_at","reviewed_at","suspended_at","resumed_at","exited_at","created_at","updated_at","version")
    profile_insert = ("therapist_id","user_id","tenant_id","invitation_id","status","capacity_limit","active_case_count","current_revision_no","totp_secret_ciphertext","totp_encryption_key_id","totp_enabled","activated_at","created_at","updated_at","version")
    profile_public = ("therapist_id","user_id","tenant_id","invitation_id","display_name","practice_summary","service_tags","status","capacity_limit","active_case_count","current_revision_no","current_qualification_version_id","qualification_valid_until","activated_at","submitted_at","reviewed_at","suspended_at","resumed_at","exited_at","created_at","updated_at","version")
    revision = ("revision_id","therapist_id","revision_no","profile_snapshot","input_digest","created_at")
    revision_qualification = ("therapist_id","revision_id","qualification_version_id","position")
    qualification = ("qualification_version_id","therapist_id","profile_revision_id","previous_version_id","qualification_type","certificate_no_ciphertext","certificate_encryption_key_id","certificate_no_digest","certificate_digest_key_id","certificate_no_masked","issuer_name","valid_from","valid_until","attachment_count","version_no","created_at")
    qualification_public = ("qualification_version_id","therapist_id","profile_revision_id","previous_version_id","qualification_type","certificate_no_masked","issuer_name","valid_from","valid_until","attachment_count","version_no","created_at")
    attachment = ("qualification_version_id","slot","private_file_id","created_at")
    review_item = ("review_item_id","therapist_id","revision_id","qualification_version_id","review_kind","status","reviewer_user_id","previous_review_item_id","created_at","claimed_at","decided_at","version")
    review_decision = ("decision_id","review_item_id","therapist_id","revision_id","reviewer_user_id","decision","qualification_outcomes","reason_code","correction_fields","request_digest","created_at")
    status_decision = ("status_decision_id","therapist_id","actor_kind","actor_user_id","worker_identity","decision","reason_code","expected_profile_version","request_digest","created_at")
    readiness = ("tenant_id","readiness_status","reason_codes","qualified_therapist_count","computed_at","evidence_version","input_digest","result_digest","source_versions","next_expiry_at","version")
    evidence = ("evidence_id","tenant_id","evidence_version","readiness_status","reason_codes","qualified_therapist_count","computed_at","tenant_status","institution_license_digest","therapist_set_digest","service_scope_digest","source_versions","next_expiry_at","trigger_event_id","input_digest","result_digest","created_at")
    evidence_public = ("tenant_id","evidence_version","readiness_status","reason_codes","qualified_therapist_count","computed_at","input_digest","result_digest")
    idempotency = ("actor_scope","operation","idempotency_key","request_digest","response_ciphertext","response_encryption_key_id","postimage_digest","created_at")
    audit = ("actor_scope","action","object_id","result","reason_code","request_id","preimage_digest","postimage_digest","created_at")
    outbox = ("event_id","event_type","aggregate_id","tenant_id","payload","payload_digest","status","attempts","processing_at","lease_owner","delivered_at","failed_at","created_at","version")
    delivery = ("delivery_id","event_id","recipient_scope","recipient_user_id","recipient_tenant_id","recipient_platform_code","target_digest","target_digest_key_id","payload","payload_digest","created_at")

    _grant(onboarding, "SELECT", "therapist_invitation", invitation_select)
    _grant(onboarding, "INSERT", "therapist_invitation", invitation_insert)
    _grant(onboarding, "UPDATE", "therapist_invitation", ("status","failed_attempts","activated_at","revoked_at","version"))
    _grant(onboarding, "SELECT", "therapist_profile", profile_business)
    _grant(onboarding, "INSERT", "therapist_profile", profile_insert)
    for table, columns in (("therapist_profile_revision",revision),("therapist_qualification_version",qualification),("therapist_profile_revision_qualification",revision_qualification),("therapist_qualification_attachment",attachment),("therapist_review_item",review_item),("therapist_workflow_idempotency",idempotency),("therapist_workflow_audit",audit),("therapist_workflow_outbox",outbox)):
        _grant(onboarding, "SELECT", table, columns)
        _grant(onboarding, "INSERT", table, columns)
    _grant(onboarding, "UPDATE", "therapist_profile", ("real_name_ciphertext","real_name_encryption_key_id","real_name_digest","real_name_digest_key_id","display_name","practice_summary","service_tags","status","current_revision_no","submitted_at","reviewed_at","updated_at","version"))
    _grant(onboarding, "INSERT", "user", ("phone","password_hash","role","status","tenant_id"))
    _grant(onboarding, "SELECT", "user", ("id",))
    _grant(onboarding, "SELECT", "private_file", ("file_id","purpose","owner_user_id","status","actual_size","actual_sha256","bound_application_id","created_at"))
    for table, columns in (("therapist_invitation",("invitation_id","tenant_id","status","version")),("therapist_profile",profile_business),("therapist_profile_revision",revision),("therapist_qualification_version",qualification),("therapist_profile_revision_qualification",revision_qualification),("therapist_qualification_attachment",attachment),("therapist_review_item",review_item),("therapist_review_decision",review_decision),("therapist_status_decision",status_decision),("institution_service_readiness",readiness),("readiness_evidence",evidence),("therapist_workflow_idempotency",idempotency),("therapist_workflow_audit",audit),("therapist_workflow_outbox",outbox)):
        _grant(reviewer, "SELECT", table, columns)
    for table, columns in (("therapist_review_item",review_item),("therapist_review_decision",review_decision),("therapist_status_decision",status_decision),("institution_service_readiness",readiness),("readiness_evidence",evidence),("therapist_workflow_idempotency",idempotency),("therapist_workflow_audit",audit),("therapist_workflow_outbox",outbox)):
        _grant(reviewer, "INSERT", table, columns)
    _grant(reviewer, "UPDATE", "therapist_profile", ("status","current_qualification_version_id","qualification_valid_until","suspension_reason_code","reviewed_at","suspended_at","resumed_at","exited_at","updated_at","version"))
    _grant(reviewer, "UPDATE", "therapist_review_item", ("status","reviewer_user_id","claimed_at","decided_at","version"))
    _grant(reviewer, "UPDATE", "institution_service_readiness", tuple(value for value in readiness if value != "tenant_id"))
    _grant(reviewer, "SELECT", "private_file", ("file_id","purpose","owner_user_id","status","actual_size","actual_sha256","bound_application_id","created_at"))
    for table, columns in (("therapist_invitation",("invitation_id","tenant_id","status","expires_at","version")),("therapist_profile",("therapist_id","user_id","tenant_id","status","service_tags","current_qualification_version_id","qualification_valid_until","active_case_count","capacity_limit","version")),("therapist_profile_revision",("revision_id","therapist_id","input_digest")),("therapist_profile_revision_qualification",revision_qualification),("therapist_qualification_version",qualification_public),("therapist_review_item",("review_item_id","therapist_id","revision_id","qualification_version_id","review_kind","status","version")),("therapist_review_decision",("review_item_id","qualification_outcomes","reason_code","created_at")),("therapist_status_decision",status_decision),("institution_service_readiness",readiness),("readiness_evidence",evidence),("therapist_workflow_idempotency",idempotency),("therapist_workflow_outbox",outbox),("therapist_workflow_delivery",delivery),("therapist_workflow_audit",audit)):
        _grant(worker, "SELECT", table, columns)
    _grant(worker, "UPDATE", "therapist_invitation", ("status","version"))
    _grant(worker, "UPDATE", "therapist_profile", ("status","suspension_reason_code","suspended_at","updated_at","version"))
    _grant(worker, "UPDATE", "therapist_workflow_outbox", ("status","attempts","processing_at","lease_owner","delivered_at","failed_at","version"))
    _grant(worker, "INSERT", "therapist_workflow_delivery", delivery)
    _grant(worker, "INSERT", "therapist_workflow_idempotency", idempotency)
    _grant(worker, "INSERT", "therapist_workflow_audit", audit)
    _grant(worker, "INSERT", "therapist_workflow_outbox", outbox)
    _grant(worker, "INSERT", "readiness_evidence", evidence)
    _grant(worker, "INSERT", "institution_service_readiness", readiness)
    _grant(worker, "UPDATE", "institution_service_readiness", tuple(value for value in readiness if value != "tenant_id"))
    op.execute(f'GRANT SELECT ON TABLE public.institution_readiness_source_v1 TO "{reviewer}","{worker}"')
    op.execute(f'GRANT SELECT ON TABLE public.institution_readiness_guard_v1 TO "{reviewer}","{worker}","{reader}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.record_therapist_expiry_suspension_v1(UUID,UUID,BIGINT,CHAR) TO "{worker}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.resolve_tenant_admin_delivery_targets_v1(UUID) TO "{worker}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.lock_delivery_recipient_currentness_v1(UUID,VARCHAR,BIGINT,BIGINT,VARCHAR) TO "{worker}"')
    for table, columns in (("therapist_invitation",("invitation_id","tenant_id","phone_masked","expires_at","status","failed_attempts","issued_at","activated_at","revoked_at","version")),("therapist_profile",profile_public),("therapist_profile_revision",("revision_id","therapist_id","revision_no","input_digest","created_at")),("therapist_profile_revision_qualification",revision_qualification),("therapist_qualification_version",qualification_public),("therapist_qualification_attachment",attachment),("therapist_review_item",("review_item_id","therapist_id","revision_id","qualification_version_id","review_kind","status","created_at","claimed_at","decided_at","version")),("therapist_review_decision",("decision_id","review_item_id","therapist_id","revision_id","decision","qualification_outcomes","reason_code","correction_fields","created_at")),("therapist_status_decision",("status_decision_id","therapist_id","actor_kind","decision","reason_code","created_at")),("institution_service_readiness",readiness),("readiness_evidence",evidence_public),("therapist_workflow_idempotency",("actor_scope","operation","idempotency_key","request_digest","postimage_digest","created_at")),("therapist_workflow_audit",audit),("therapist_workflow_outbox",("event_id","event_type","aggregate_id","tenant_id","payload_digest","status","attempts","processing_at","lease_owner","delivered_at","failed_at","created_at","version")),("therapist_workflow_delivery",("delivery_id","event_id","recipient_scope","recipient_user_id","recipient_tenant_id","recipient_platform_code","target_digest","target_digest_key_id","payload_digest","created_at"))):
        _grant(reader, "SELECT", table, columns)
    for role in roles:
        op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    op.execute(f'GRANT USAGE,SELECT ON SEQUENCE public.therapist_workflow_audit_audit_id_seq TO "{onboarding}","{reviewer}","{worker}"')
    op.execute(f'GRANT USAGE,SELECT ON SEQUENCE public.user_id_seq TO "{onboarding}"')
    application = os.environ["KG_DATABASE_USER"]
    op.execute(f'GRANT EXECUTE ON FUNCTION public.therapist_totp_for_login_v1(BIGINT) TO "{application}"')
    slice1_onboarding = os.environ["KG_INSTITUTION_ONBOARDING_WRITER_ROLE"]
    slice1_review = os.environ["KG_INSTITUTION_REVIEW_WRITER_ROLE"]
    slice1_reader = os.environ["KG_INSTITUTION_ONBOARDING_READER_ROLE"]
    file_writer = os.environ["KG_PRIVATE_FILE_WRITER_ROLE"]
    op.execute("REVOKE ALL ON FUNCTION public.therapist_qualification_file_relation_v1(UUID) FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION public.therapist_qualification_file_relation_v1(UUID) TO "{file_writer}","{slice1_reader}"')
    op.execute("REVOKE ALL ON FUNCTION public.lock_therapist_qualification_files_v1(UUID[],BIGINT) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.lock_therapist_qualification_review_files_v1(UUID,INTEGER) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.is_current_slice2_recovery_actor_v1(BIGINT) FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION public.lock_therapist_qualification_files_v1(UUID[],BIGINT) TO "{onboarding}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.lock_therapist_qualification_review_files_v1(UUID,INTEGER) TO "{reviewer}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.is_current_slice2_recovery_actor_v1(BIGINT) TO "{worker}"')
    _grant(slice1_onboarding, "SELECT", "institution_license", ("valid_from","valid_until"))
    _grant(slice1_onboarding, "INSERT", "institution_license", ("valid_from","valid_until"))
    _grant(slice1_onboarding, "UPDATE", "institution_license", ("valid_from","valid_until"))
    _grant(slice1_review, "SELECT", "institution_license", ("valid_from","valid_until"))
    _grant(slice1_reader, "SELECT", "institution_license", ("valid_from","valid_until"))


def upgrade() -> None:
    roles = _roles()
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    op.add_column("institution_license", sa.Column("valid_from", sa.Date()), schema="public")
    op.add_column("institution_license", sa.Column("valid_until", sa.Date()), schema="public")
    op.create_check_constraint("ck_institution_license_validity", "institution_license", "(valid_from IS NULL AND valid_until IS NULL) OR (valid_from IS NOT NULL AND valid_until IS NOT NULL AND valid_from<=valid_until)", schema="public")
    _create_tables()
    _create_indexes_and_fks()
    _create_safe_interfaces()
    _create_integrity_contracts()
    _acl(roles)


def downgrade() -> None:
    roles = _roles()
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    for table in _TABLES:
        connection.execute(sa.text(f'LOCK TABLE public."{table}" IN ACCESS EXCLUSIVE MODE'))
        if connection.execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM public."{table}")')).scalar_one():
            raise RuntimeError("Slice 2 downgrade requires empty module tables") from None
    connection.execute(sa.text('LOCK TABLE public."institution_license" IN ACCESS EXCLUSIVE MODE'))
    if connection.execute(sa.text(
        'SELECT EXISTS(SELECT 1 FROM public."institution_license" '
        'WHERE valid_from IS NOT NULL OR valid_until IS NOT NULL)'
    )).scalar_one():
        raise RuntimeError("Slice 2 downgrade requires empty license validity data") from None
    all_roles = ",".join(f'"{role}"' for role in roles)
    onboarding, reviewer, worker, _ = roles
    op.execute(f'REVOKE INSERT (phone,password_hash,role,status,tenant_id), SELECT (id) ON TABLE public."user" FROM "{onboarding}"')
    op.execute(f'REVOKE SELECT (file_id,purpose,owner_user_id,status,actual_size,actual_sha256,bound_application_id,created_at) ON TABLE public.private_file FROM "{onboarding}","{reviewer}"')
    op.execute(f'REVOKE USAGE,SELECT ON SEQUENCE public.user_id_seq FROM "{onboarding}"')
    op.execute(f'REVOKE USAGE ON SCHEMA public FROM {all_roles}')
    for table in _TABLES:
        op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public."{table}" FROM PUBLIC,{all_roles}')
    op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.institution_readiness_source_v1 FROM PUBLIC,{all_roles}')
    op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.institution_readiness_guard_v1 FROM PUBLIC,{all_roles}')
    op.execute("REVOKE ALL ON FUNCTION public.therapist_totp_for_login_v1(BIGINT) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.therapist_qualification_file_relation_v1(UUID) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.lock_therapist_qualification_files_v1(UUID[],BIGINT) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.lock_therapist_qualification_review_files_v1(UUID,INTEGER) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.record_therapist_expiry_suspension_v1(UUID,UUID,BIGINT,CHAR) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.resolve_tenant_admin_delivery_targets_v1(UUID) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.lock_delivery_recipient_currentness_v1(UUID,VARCHAR,BIGINT,BIGINT,VARCHAR) FROM PUBLIC")
    application = os.environ["KG_DATABASE_USER"]
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.therapist_totp_for_login_v1(BIGINT) FROM "{application}"')
    slice1_onboarding = os.environ["KG_INSTITUTION_ONBOARDING_WRITER_ROLE"]
    slice1_review = os.environ["KG_INSTITUTION_REVIEW_WRITER_ROLE"]
    slice1_reader = os.environ["KG_INSTITUTION_ONBOARDING_READER_ROLE"]
    file_writer = os.environ["KG_PRIVATE_FILE_WRITER_ROLE"]
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.therapist_qualification_file_relation_v1(UUID) FROM "{file_writer}","{slice1_reader}"')
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.lock_therapist_qualification_files_v1(UUID[],BIGINT) FROM "{onboarding}"')
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.lock_therapist_qualification_review_files_v1(UUID,INTEGER) FROM "{reviewer}"')
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.is_current_slice2_recovery_actor_v1(BIGINT) FROM "{worker}"')
    op.execute(f'REVOKE SELECT (valid_from,valid_until), INSERT (valid_from,valid_until), UPDATE (valid_from,valid_until) ON TABLE public.institution_license FROM "{slice1_onboarding}"')
    op.execute(f'REVOKE SELECT (valid_from,valid_until) ON TABLE public.institution_license FROM "{slice1_review}","{slice1_reader}"')
    op.execute("DROP TRIGGER cktrg_therapist_review_decision_fact ON public.therapist_review_decision")
    op.execute("DROP TRIGGER cktrg_therapist_review_decision_item ON public.therapist_review_item")
    op.execute("DROP TRIGGER cktrg_therapist_qualification_attachment_version ON public.therapist_qualification_version")
    op.execute("DROP TRIGGER cktrg_therapist_qualification_attachment_count ON public.therapist_qualification_attachment")
    op.execute("DROP TRIGGER cktrg_therapist_revision_qualification_revision ON public.therapist_profile_revision")
    op.execute("DROP TRIGGER cktrg_therapist_revision_qualification_integrity ON public.therapist_profile_revision_qualification")
    op.execute("DROP TRIGGER trg_readiness_evidence_reasons_canonical ON public.readiness_evidence")
    op.execute("DROP TRIGGER trg_institution_service_readiness_reasons_canonical ON public.institution_service_readiness")
    op.execute("DROP TRIGGER trg_therapist_profile_service_tags_canonical ON public.therapist_profile")
    op.execute("DROP FUNCTION public.enforce_therapist_review_decision_v1()")
    op.execute("DROP FUNCTION public.enforce_therapist_qualification_attachments_v1()")
    op.execute("DROP FUNCTION public.enforce_therapist_revision_qualification_v1()")
    op.execute("DROP FUNCTION public.enforce_readiness_reasons_canonical_v1()")
    op.execute("DROP FUNCTION public.enforce_therapist_service_tags_canonical_v1()")
    op.execute("DROP FUNCTION public.lock_delivery_recipient_currentness_v1(UUID,VARCHAR,BIGINT,BIGINT,VARCHAR)")
    op.execute("DROP FUNCTION public.resolve_tenant_admin_delivery_targets_v1(UUID)")
    op.execute("DROP FUNCTION public.record_therapist_expiry_suspension_v1(UUID,UUID,BIGINT,CHAR)")
    op.execute("DROP FUNCTION public.therapist_totp_for_login_v1(BIGINT)")
    op.execute("DROP FUNCTION public.lock_therapist_qualification_review_files_v1(UUID,INTEGER)")
    op.execute("DROP FUNCTION public.lock_therapist_qualification_files_v1(UUID[],BIGINT)")
    op.execute("DROP FUNCTION public.therapist_qualification_file_relation_v1(UUID)")
    op.execute("DROP FUNCTION public.is_current_slice2_recovery_actor_v1(BIGINT)")
    op.execute("DROP VIEW public.institution_readiness_guard_v1")
    op.execute("DROP VIEW public.institution_readiness_source_v1")
    op.drop_constraint(
        "fk_therapist_profile_current_qualification",
        "therapist_profile",
        schema="public",
        type_="foreignkey",
    )
    for table in reversed(_TABLES):
        op.drop_table(table, schema="public")
    op.drop_constraint("ck_institution_license_validity", "institution_license", schema="public", type_="check")
    op.drop_column("institution_license", "valid_until", schema="public")
    op.drop_column("institution_license", "valid_from", schema="public")
