"""Phase 1 Slice 7 service fulfillment, closure, transfer and export.

Revision ID: 20260827_0032
Revises: 20260826_0031
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260827_0032"
down_revision = "20260826_0031"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052317115572212032
_IDENTITIES = (
    ("KG_SLICE7_MILESTONE_WRITER_ROLE", "KG_SLICE7_MILESTONE_WRITER_DATABASE_URL"),
    ("KG_SLICE7_CASE_WRITER_ROLE", "KG_SLICE7_CASE_WRITER_DATABASE_URL"),
    ("KG_SLICE7_TRANSFER_WRITER_ROLE", "KG_SLICE7_TRANSFER_WRITER_DATABASE_URL"),
    ("KG_SLICE7_EXPORT_WORKER_ROLE", "KG_SLICE7_EXPORT_WORKER_DATABASE_URL"),
    ("KG_SLICE7_FAMILY_READER_ROLE", "KG_SLICE7_FAMILY_READER_DATABASE_URL"),
    ("KG_SLICE7_OVERSIGHT_READER_ROLE", "KG_SLICE7_OVERSIGHT_READER_DATABASE_URL"),
    (
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL",
    ),
)
_TABLES = (
    "service_cycle_schedule",
    "service_milestone",
    "service_milestone_revision",
    "service_case_lifecycle_event",
    "service_closing_assessment",
    "service_summary",
    "service_summary_acknowledgement",
    "service_transfer_request",
    "service_transfer_scope_revision",
    "service_transfer_continuation_handoff",
    "proxy_major_authorization",
    "personal_data_export_request",
    "personal_data_export_artifact",
    "personal_data_export_download_access",
    "service_fulfillment_receipt",
    "service_fulfillment_audit",
    "service_fulfillment_outbox",
    "service_fulfillment_delivery",
)
_FUNCTIONS = (
    ("slice7_derived_uuid7_v1", "UUID,INTEGER"),
    ("slice7_closing_readiness_v1", "UUID"),
    ("slice7_plan_activation_v1", ""),
    ("slice7_service_case_current_guard_v1", ""),
    ("slice7_proxy_major_current_v1", "BIGINT,UUID,VARCHAR"),
    ("slice7_proxy_plan_decision_guard_v1", ""),
    ("slice7_authority_v1", "VARCHAR,UUID,BIGINT,VARCHAR,BIGINT"),
    ("slice7_mutation_replay_v1", "BIGINT,VARCHAR,VARCHAR,BYTEA"),
    ("slice7_mutation_v1", "JSONB"),
    ("slice7_mutation_confirm_v1", "JSONB"),
    ("slice7_read_one_v1", "VARCHAR,UUID,BIGINT,VARCHAR"),
    ("slice7_read_many_v1", "VARCHAR,UUID,BIGINT,VARCHAR,UUID,UUID,BIGINT,VARCHAR,VARCHAR"),
    ("slice7_worker_claim_v1", "VARCHAR,VARCHAR,BIGINT"),
    ("slice7_export_claim_v1", "UUID,VARCHAR"),
    ("slice7_export_snapshot_v1", "UUID"),
    ("slice7_export_source_file_v1", "UUID,UUID"),
    ("slice7_export_private_file_register_v1", "JSONB"),
    ("slice7_export_private_file_snapshot_v1", "UUID,UUID"),
    ("slice7_export_artifact_bind_v1", "JSONB"),
    ("slice7_export_ready_confirm_v1", "JSONB"),
    ("slice7_export_download_consume_v1", "JSONB"),
    ("slice7_export_download_confirm_v1", "JSONB"),
    ("slice7_export_fail_v1", "JSONB"),
    ("slice7_export_recover_v1", "JSONB"),
    ("slice7_export_cleanup_claim_v1", "TIMESTAMP WITH TIME ZONE,BIGINT"),
    ("slice7_export_cleanup_complete_v1", "JSONB"),
    ("slice7_outbox_claim_v1", "UUID,BIGINT"),
    ("slice7_outbox_consume_v1", "JSONB"),
    ("slice7_outbox_recover_v1", "TIMESTAMP WITH TIME ZONE"),
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 7 database role configuration is invalid") from None


def _roles() -> tuple[str, str, str, str, str, str, str]:
    configured: list[str] = []
    targets: set[tuple[str | None, int | None, str | None]] = set()
    for role_var, url_var in _IDENTITIES:
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
    if len(set(configured)) != 7 or len(targets) != 1:
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
    if len(rows) != 7 or any(row[name] for row in rows for name in unsafe):
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
        "service_cycle_schedule",
        _uuid("schedule_id"), _uuid("service_case_id"), _uuid("subject_member_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False), _uuid("active_plan_id"),
        sa.Column("version_no", sa.BigInteger(), nullable=False), _ts("cycle_anchor_at"),
        sa.Column("is_current", sa.Boolean(), nullable=False), _ts("created_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("schedule_id", name="pk_service_cycle_schedule"),
        sa.UniqueConstraint("service_case_id", "version_no", name="uq_service_cycle_schedule_version"),
        sa.UniqueConstraint("active_plan_id", name="uq_service_cycle_schedule_plan"),
        sa.CheckConstraint("version_no>=1 AND version>=1", name="ck_service_cycle_schedule_version"),
        schema="public",
    )
    op.create_index("uq_service_cycle_schedule_current", "service_cycle_schedule", ["service_case_id"], unique=True, schema="public", postgresql_where=sa.text("is_current"))
    op.create_table(
        "service_milestone", _uuid("milestone_id"), _uuid("schedule_id"), _uuid("service_case_id"),
        sa.Column("code", sa.String(4), nullable=False), sa.Column("window_start", sa.Date(), nullable=False),
        sa.Column("window_end", sa.Date(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        _ts("completed_at", nullable=True), sa.Column("record_summary", postgresql.JSONB(), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("milestone_id", name="pk_service_milestone"),
        sa.UniqueConstraint("schedule_id", "code", name="uq_service_milestone_code"),
        sa.ForeignKeyConstraint(["schedule_id"], ["public.service_cycle_schedule.schedule_id"], name="fk_service_milestone_schedule"),
        sa.CheckConstraint("code IN ('D0','D7','D14','D21','D28')", name="ck_service_milestone_code"),
        sa.CheckConstraint("status IN ('PENDING','DUE','COMPLETED','MISSED','INVALIDATED') AND version>=1", name="ck_service_milestone_status"), schema="public",
    )
    op.create_table(
        "service_milestone_revision", _uuid("revision_id"), _uuid("milestone_id"),
        sa.Column("version_no", sa.BigInteger(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("body", postgresql.JSONB(), nullable=False), sa.Column("body_digest", sa.LargeBinary(), nullable=False),
        _ts("created_at"), sa.PrimaryKeyConstraint("revision_id", name="pk_service_milestone_revision"),
        sa.UniqueConstraint("milestone_id", "version_no", name="uq_service_milestone_revision"), schema="public",
    )
    op.create_table(
        "service_case_lifecycle_event", _uuid("lifecycle_event_id"), _uuid("service_case_id"),
        sa.Column("from_status", sa.String(32), nullable=False), sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False), _ts("occurred_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("lifecycle_event_id", name="pk_service_case_lifecycle_event"), schema="public",
    )
    op.create_table(
        "service_closing_assessment", _uuid("closing_assessment_id"), _uuid("service_case_id"), _uuid("assessment_id"),
        sa.Column("version_no", sa.BigInteger(), nullable=False), sa.Column("final_retest_evidence", postgresql.JSONB(), nullable=False),
        _ts("created_at"), sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("closing_assessment_id", name="pk_service_closing_assessment"),
        sa.UniqueConstraint("service_case_id", "version_no", name="uq_service_closing_assessment"), schema="public",
    )
    op.create_table(
        "service_summary", _uuid("summary_id"), _uuid("service_case_id"), _uuid("assessment_id"),
        sa.Column("version_no", sa.BigInteger(), nullable=False), sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False), _ts("created_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("summary_id", name="pk_service_summary"),
        sa.UniqueConstraint("service_case_id", "version_no", name="uq_service_summary_version"), schema="public",
    )
    op.create_table(
        "service_summary_acknowledgement", _uuid("acknowledgement_id"), _uuid("summary_id"), _uuid("subject_member_id"),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False), _uuid("proxy_grant_id", nullable=True),
        _ts("viewed_at"), sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("acknowledgement_id", name="pk_service_summary_acknowledgement"),
        sa.UniqueConstraint("summary_id", "subject_member_id", name="uq_service_summary_ack"), schema="public",
    )
    op.create_table(
        "service_transfer_request", _uuid("transfer_id"), _uuid("source_service_case_id"),
        _uuid("source_tenant_id"), _uuid("target_tenant_id"),
        _uuid("subject_member_id"), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requested_scope", postgresql.JSONB(), nullable=False), sa.Column("target_decision", sa.String(64), nullable=True),
        sa.Column("source_closure_status", sa.String(32), nullable=True), _ts("scope_confirmed_at", nullable=True),
        _ts("transferred_at", nullable=True), _ts("created_at"), sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("transfer_id", name="pk_service_transfer_request"),
        sa.CheckConstraint("source_tenant_id<>target_tenant_id", name="ck_service_transfer_distinct_tenants"),
        sa.CheckConstraint("status IN ('REQUESTED_BY_USER','NEW_INSTITUTION_REVIEWING','ACCEPTED','OLD_INSTITUTION_CLOSING','USER_SCOPE_CONFIRMED','TRANSFERRED','REJECTED_BY_NEW_INSTITUTION','CANCELLED_BY_USER') AND version>=1", name="ck_service_transfer_status"), schema="public",
    )
    op.create_index("uq_service_transfer_open", "service_transfer_request", ["source_service_case_id"], unique=True, schema="public", postgresql_where=sa.text("status NOT IN ('TRANSFERRED','REJECTED_BY_NEW_INSTITUTION','CANCELLED_BY_USER')"))
    op.create_table(
        "service_transfer_scope_revision", _uuid("scope_revision_id"), _uuid("transfer_id"),
        sa.Column("version_no", sa.BigInteger(), nullable=False), sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("scope_digest", sa.LargeBinary(), nullable=False), _ts("created_at"),
        sa.PrimaryKeyConstraint("scope_revision_id", name="pk_service_transfer_scope_revision"),
        sa.UniqueConstraint("transfer_id", "version_no", name="uq_service_transfer_scope_revision"), schema="public",
    )
    op.create_table(
        "service_transfer_continuation_handoff", _uuid("handoff_id"), _uuid("transfer_id"),
        _uuid("source_service_case_id"), _uuid("source_tenant_id"), _uuid("target_tenant_id"),
        _uuid("subject_member_id"), sa.Column("authorized_scope", postgresql.JSONB(), nullable=False),
        sa.Column("scope_digest", sa.LargeBinary(), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        _ts("created_at"), _uuid("linked_enrollment_id", nullable=True),
        _uuid("linked_service_case_id", nullable=True), _ts("linked_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("handoff_id", name="pk_service_transfer_continuation_handoff"),
        sa.UniqueConstraint("transfer_id", name="uq_service_transfer_continuation_handoff_transfer"),
        sa.ForeignKeyConstraint(["transfer_id"], ["public.service_transfer_request.transfer_id"], name="fk_service_transfer_continuation_handoff_transfer"),
        sa.ForeignKeyConstraint(["source_service_case_id"], ["public.service_case.case_id"], name="fk_service_transfer_continuation_handoff_source_case"),
        sa.ForeignKeyConstraint(["linked_enrollment_id"], ["public.service_enrollment.enrollment_id"], name="fk_service_transfer_continuation_handoff_enrollment"),
        sa.ForeignKeyConstraint(["linked_service_case_id"], ["public.service_case.case_id"], name="fk_service_transfer_continuation_handoff_case"),
        sa.CheckConstraint("status IN ('PENDING_TARGET_ENROLLMENT','ENROLLMENT_CREATED','ASSIGNMENT_PENDING','CONTINUATION_CASE_LINKED') AND version>=1", name="ck_service_transfer_continuation_handoff_status"),
        sa.CheckConstraint("(status='CONTINUATION_CASE_LINKED' AND linked_enrollment_id IS NOT NULL AND linked_service_case_id IS NOT NULL AND linked_at IS NOT NULL) OR (status<>'CONTINUATION_CASE_LINKED' AND linked_service_case_id IS NULL AND linked_at IS NULL)", name="ck_service_transfer_continuation_handoff_link"),
        schema="public",
    )
    op.create_table(
        "proxy_major_authorization", _uuid("authorization_revision_id"), _uuid("authorization_id"),
        _uuid("proxy_grant_id"), _uuid("principal_member_id"), _uuid("proxy_member_id"),
        _uuid("authorization_document_version_id"), _uuid("witness_decision_id"),
        sa.Column("permission_codes", postgresql.JSONB(), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=False), _ts("valid_from"),
        _ts("valid_until", nullable=True), _ts("revoked_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("authorization_revision_id", name="pk_proxy_major_authorization"),
        sa.UniqueConstraint("authorization_id", "version", name="uq_proxy_major_authorization_version"),
        sa.ForeignKeyConstraint(["proxy_grant_id"], ["public.proxy_grant.grant_id"], name="fk_proxy_major_authorization_grant"),
        sa.ForeignKeyConstraint(["principal_member_id"], ["identity.member.member_id"], name="fk_proxy_major_authorization_principal"),
        sa.ForeignKeyConstraint(["proxy_member_id"], ["identity.member.member_id"], name="fk_proxy_major_authorization_proxy"),
        sa.ForeignKeyConstraint(["authorization_document_version_id"], ["public.consent_document_version.document_version_id"], name="fk_proxy_major_authorization_document"),
        sa.ForeignKeyConstraint(["witness_decision_id"], ["public.member_identity_review_decision.decision_id"], name="fk_proxy_major_authorization_witness"),
        sa.ForeignKeyConstraint(["granted_by"], ["public.user.id"], name="fk_proxy_major_authorization_granted_by"),
        sa.CheckConstraint("version>=1 AND ((version=1 AND revoked_at IS NULL) OR (version>1 AND revoked_at IS NOT NULL))", name="ck_proxy_major_authorization_append_only"),
        sa.CheckConstraint("jsonb_typeof(permission_codes)='array' AND jsonb_array_length(permission_codes) BETWEEN 1 AND 4 AND permission_codes <@ '[\"PLAN_DECISION\",\"SERVICE_WITHDRAW\",\"SERVICE_TRANSFER\",\"PERSONAL_DATA_EXPORT\"]'::jsonb", name="ck_proxy_major_authorization_permissions"),
        sa.CheckConstraint("valid_until IS NULL OR valid_until>valid_from", name="ck_proxy_major_authorization_validity"),
        schema="public",
    )
    op.create_table(
        "personal_data_export_request", _uuid("export_id"), _uuid("subject_member_id"),
        sa.Column("requested_scope", postgresql.JSONB(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("requested_by", sa.BigInteger(), nullable=False), _ts("requested_at"), _ts("ready_at", nullable=True),
        _ts("expires_at", nullable=True), _ts("downloaded_at", nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True), _ts("lease_until", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("export_id", name="pk_personal_data_export_request"),
        sa.CheckConstraint("status IN ('REQUESTED','GENERATING','READY','DOWNLOADED','EXPIRED','FAILED','CANCELLED') AND version>=1 AND ((status='GENERATING' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR (status<>'GENERATING' AND lease_owner IS NULL AND lease_until IS NULL))", name="ck_personal_data_export_status"), schema="public",
    )
    op.create_table(
        "personal_data_export_artifact", _uuid("artifact_id"), _uuid("export_id"), _uuid("private_file_id"),
        sa.Column("manifest_digest", sa.LargeBinary(), nullable=False), sa.Column("artifact_digest", sa.LargeBinary(), nullable=False),
        _ts("created_at"), sa.PrimaryKeyConstraint("artifact_id", name="pk_personal_data_export_artifact"),
        sa.UniqueConstraint("export_id", name="uq_personal_data_export_artifact"),
        sa.ForeignKeyConstraint(["export_id"], ["public.personal_data_export_request.export_id"], name="fk_personal_data_export_artifact_export"),
        sa.ForeignKeyConstraint(["private_file_id"], ["public.private_file.file_id"], name="fk_personal_data_export_artifact_file"),
        schema="public",
    )
    op.create_table(
        "personal_data_export_download_access", _uuid("access_id"), _uuid("export_id"), _uuid("private_file_id"),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False), sa.Column("reason_code", sa.String(64), nullable=False),
        _ts("expires_at"), _ts("consumed_at", nullable=True), _ts("created_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("access_id", name="pk_personal_data_export_download_access"),
        sa.ForeignKeyConstraint(["export_id"], ["public.personal_data_export_request.export_id"], name="fk_personal_data_export_access_export"),
        sa.ForeignKeyConstraint(["private_file_id"], ["public.private_file.file_id"], name="fk_personal_data_export_access_file"),
        sa.CheckConstraint("expires_at>created_at AND version>=1", name="ck_personal_data_export_access"),
        schema="public",
    )
    op.create_table(
        "service_fulfillment_receipt", _uuid("receipt_id"), sa.Column("actor_scope", sa.String(160), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False), _uuid("target_id"),
        sa.Column("idempotency_key", sa.String(128), nullable=False), sa.Column("request_digest", sa.LargeBinary(), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False), sa.Column("postimage_digest", sa.LargeBinary(), nullable=False),
        _ts("created_at"), sa.PrimaryKeyConstraint("receipt_id", name="pk_service_fulfillment_receipt"),
        sa.UniqueConstraint("actor_scope", "operation", "idempotency_key", name="uq_service_fulfillment_receipt"), schema="public",
    )
    op.create_table(
        "service_fulfillment_audit", _uuid("audit_id"), sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False), sa.Column("actor_role", sa.String(32), nullable=False),
        _uuid("target_id"), sa.Column("evidence_digest", sa.LargeBinary(), nullable=False), _ts("occurred_at"),
        sa.PrimaryKeyConstraint("audit_id", name="pk_service_fulfillment_audit"), schema="public",
    )
    op.create_table(
        "service_fulfillment_outbox", _uuid("event_id"), _uuid("aggregate_ref"),
        sa.Column("event_type", sa.String(64), nullable=False), sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("payload_digest", sa.LargeBinary(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False), sa.Column("lease_owner", sa.String(128), nullable=True),
        _ts("lease_until", nullable=True), _ts("created_at"), _ts("delivered_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False), sa.PrimaryKeyConstraint("event_id", name="pk_service_fulfillment_outbox"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','DELIVERED','FAILED') AND attempts>=0 AND version>=1", name="ck_service_fulfillment_outbox"), schema="public",
    )
    op.create_table(
        "service_fulfillment_delivery", _uuid("delivery_id"), _uuid("event_id"),
        sa.Column("target_type", sa.String(32), nullable=False), _uuid("target_ref"),
        sa.Column("target_digest", sa.LargeBinary(), nullable=False), _ts("delivered_at"),
        sa.PrimaryKeyConstraint("delivery_id", name="pk_service_fulfillment_delivery"),
        sa.UniqueConstraint("event_id", "target_type", "target_ref", name="uq_service_fulfillment_delivery"), schema="public",
    )


def _function(name: str, args: str, returns: str, body: str, roles: tuple[str, ...], declarations: str = "") -> None:
    op.execute(
        f"CREATE FUNCTION public.{name}({args}) RETURNS {returns} LANGUAGE plpgsql SECURITY DEFINER "
        f"SET search_path = pg_catalog, pg_temp AS $$ DECLARE {declarations} BEGIN {body} END $$"
    )
    signature = ",".join(part.strip().split(" ", 1)[1] for part in args.split(","))
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")
    for role in roles:
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{name}({signature}) TO "{role}"')


def _create_functions(
    milestone: str,
    case: str,
    transfer: str,
    export: str,
    family: str,
    oversight: str,
    identity_review: str,
) -> None:
    writers = (milestone, case, transfer)
    mutation_runtime = writers + (export,)
    all_runtime = writers + (export, family, oversight)
    readers = all_runtime
    _function(
        "slice7_derived_uuid7_v1",
        "value_source UUID,value_discriminator INTEGER",
        "UUID",
        "source_hex:=replace(value_source::text,'-',''); hash_hex:=md5(value_source::text||':'||value_discriminator::text); result_value:=(substring(source_hex,1,12)||'7'||substring(hash_hex,1,3)||'8'||substring(hash_hex,4,15))::uuid; RETURN result_value;",
        (),
        declarations="source_hex TEXT; hash_hex TEXT; result_value UUID;",
    )
    _function(
        "slice7_closing_readiness_v1",
        "value_case UUID",
        "VARCHAR",
        f"IF session_user NOT IN {all_runtime!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.service_cycle_schedule s WHERE s.service_case_id=value_case AND s.is_current) THEN RETURN 'NOT_READY'; END IF; "
        "IF (SELECT count(*) FROM public.service_milestone m JOIN public.service_cycle_schedule s ON s.schedule_id=m.schedule_id AND s.is_current WHERE m.service_case_id=value_case AND m.status='COMPLETED')<>5 THEN RETURN 'BLOCKED_BY_MISSING_MILESTONE'; END IF; "
        "IF EXISTS(SELECT 1 FROM public.high_risk_task h WHERE h.service_case_id=value_case AND h.status IN ('OPEN','CLAIMED','ESCALATED')) THEN RETURN 'BLOCKED_BY_HIGH_RISK'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.service_closing_assessment a WHERE a.service_case_id=value_case) OR NOT EXISTS(SELECT 1 FROM public.service_summary s WHERE s.service_case_id=value_case) THEN RETURN 'NOT_READY'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.service_summary s JOIN public.service_summary_acknowledgement a ON a.summary_id=s.summary_id WHERE s.service_case_id=value_case) THEN RETURN 'BLOCKED_BY_USER_ACK'; END IF; RETURN 'READY_TO_CLOSE';",
        all_runtime,
    )
    op.execute(
        "CREATE FUNCTION public.slice7_plan_activation_v1() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$ "
        "DECLARE schedule_ref UUID; lifecycle_ref UUID; audit_ref UUID; outbox_ref UUID; "
        "actor_ref BIGINT; lifecycle_version BIGINT; anchor_date DATE; inserted_count BIGINT; "
        "BEGIN "
        "IF NEW.status<>'ACTIVE' OR OLD.status IS NOT DISTINCT FROM 'ACTIVE' THEN RETURN NEW; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(NEW.service_case_id::text,7)); "
        "SELECT d.actor_user_id INTO actor_ref FROM public.health_plan_user_decision d "
        "WHERE d.plan_id=NEW.plan_id AND d.service_case_id=NEW.service_case_id AND d.decision='ACCEPT' "
        "ORDER BY d.created_at DESC,d.decision_id DESC LIMIT 1; "
        "IF actor_ref IS NULL THEN RAISE EXCEPTION 'PLAN_ACCEPT_DECISION_REQUIRED'; END IF; "
        "schedule_ref:=public.slice7_derived_uuid7_v1(NEW.plan_id,1); "
        "anchor_date:=(NEW.updated_at AT TIME ZONE 'Asia/Shanghai')::date; "
        "INSERT INTO public.service_cycle_schedule(schedule_id,service_case_id,subject_member_id,tenant_id,active_plan_id,version_no,cycle_anchor_at,is_current,created_at,version) "
        "VALUES (schedule_ref,NEW.service_case_id,NEW.subject_member_id,NEW.tenant_id,NEW.plan_id,1,NEW.updated_at,true,NEW.updated_at,1) "
        "ON CONFLICT (service_case_id) WHERE is_current DO NOTHING; "
        "GET DIAGNOSTICS inserted_count=ROW_COUNT; "
        "IF inserted_count=0 THEN "
        "IF EXISTS(SELECT 1 FROM public.service_cycle_schedule s WHERE s.service_case_id=NEW.service_case_id AND s.is_current AND s.active_plan_id=NEW.plan_id) THEN RETURN NEW; END IF; "
        "RAISE EXCEPTION 'ACTIVE_CYCLE_CONFLICT'; END IF; "
        "INSERT INTO public.service_milestone(milestone_id,schedule_id,service_case_id,code,window_start,window_end,status,completed_at,record_summary,version) "
        "SELECT public.slice7_derived_uuid7_v1(NEW.plan_id,10+v.ordinality::integer),schedule_ref,NEW.service_case_id,v.code,"
        "anchor_date+v.start_offset,anchor_date+v.end_offset,CASE WHEN v.code='D0' THEN 'DUE' ELSE 'PENDING' END,NULL,NULL,1 "
        "FROM (VALUES ('D0',0,0,1),('D7',4,8,2),('D14',12,16,3),('D21',19,23,4),('D28',25,31,5)) "
        "AS v(code,start_offset,end_offset,ordinality); "
        "SELECT COALESCE(max(e.version),0)+1 INTO lifecycle_version FROM public.service_case_lifecycle_event e WHERE e.service_case_id=NEW.service_case_id; "
        "lifecycle_ref:=public.slice7_derived_uuid7_v1(NEW.plan_id,21); "
        "INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) "
        "VALUES (lifecycle_ref,NEW.service_case_id,'PLAN_PENDING','ACTIVE','PLAN_ACCEPTED',NEW.updated_at,lifecycle_version); "
        "audit_ref:=public.slice7_derived_uuid7_v1(NEW.plan_id,22); "
        "INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) "
        "VALUES (audit_ref,'PLAN_ACCEPTED',actor_ref,'member',NEW.service_case_id,NEW.content_digest,NEW.updated_at); "
        "outbox_ref:=public.slice7_derived_uuid7_v1(NEW.plan_id,23); "
        "INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) "
        "VALUES (outbox_ref,NEW.service_case_id,'PLAN_ACCEPTED',jsonb_build_object('service_case_id',NEW.service_case_id,'plan_id',NEW.plan_id,'schedule_id',schedule_ref),NEW.content_digest,'PENDING',0,NULL,NULL,NEW.updated_at,NULL,1); "
        "RETURN NEW; END $$"
    )
    op.execute("REVOKE ALL ON FUNCTION public.slice7_plan_activation_v1() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_slice7_plan_activation_v1 AFTER UPDATE OF status ON public.health_plan_version "
        "FOR EACH ROW WHEN (NEW.status='ACTIVE' AND OLD.status IS DISTINCT FROM 'ACTIVE') "
        "EXECUTE FUNCTION public.slice7_plan_activation_v1()"
    )
    op.execute(
        "CREATE FUNCTION public.slice7_service_case_current_guard_v1() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$ "
        "BEGIN "
        "IF NEW.status<>'PREPARING' THEN RETURN NEW; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(NEW.subject_member_id::text,32)); "
        "IF EXISTS(SELECT 1 FROM public.service_case c WHERE c.subject_member_id=NEW.subject_member_id "
        "AND c.status='PREPARING' AND c.case_id<>NEW.case_id "
        "AND COALESCE((SELECT e.to_status FROM public.service_case_lifecycle_event e "
        "WHERE e.service_case_id=c.case_id ORDER BY e.version DESC LIMIT 1),'PLAN_PENDING') "
        "NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED')) "
        "THEN RAISE EXCEPTION 'ACTIVE_SERVICE_CASE_EXISTS' USING ERRCODE='23505'; END IF; "
        "RETURN NEW; END $$"
    )
    op.execute("REVOKE ALL ON FUNCTION public.slice7_service_case_current_guard_v1() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_slice7_service_case_current_guard_v1 "
        "BEFORE INSERT OR UPDATE OF subject_member_id,status ON public.service_case "
        "FOR EACH ROW EXECUTE FUNCTION public.slice7_service_case_current_guard_v1()"
    )
    op.execute(
        "CREATE FUNCTION identity.slice7_identity_claim_reuse_v2("
        "value_claim UUID,value_member UUID,value_fingerprint CHAR(64),value_key VARCHAR,"
        "value_slice3_revision UUID,value_slice3_decision UUID,value_facts_version BIGINT,"
        "value_evidence CHAR(64),value_elder BOOLEAN,value_claimed_at TIMESTAMPTZ) "
        "RETURNS TABLE(outcome VARCHAR,resolved_claim_id UUID,resolved_claim JSONB) "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$ "
        "DECLARE existing_by_member identity.identity_subject_claim_registry%ROWTYPE; "
        "existing_by_fingerprint identity.identity_subject_claim_registry%ROWTYPE; "
        "existing identity.identity_subject_claim_registry%ROWTYPE; algorithm_key VARCHAR(64); "
        "BEGIN "
        f"IF session_user<>'{identity_review}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT fingerprint_key_id INTO algorithm_key FROM identity.identity_claim_algorithm_state "
        "WHERE singleton=1 FOR SHARE; "
        "IF value_member IS NULL OR value_fingerprint IS NULL OR value_key IS NULL OR "
        "value_slice3_revision IS NULL OR value_slice3_decision IS NULL OR value_facts_version<1 OR "
        "value_evidence IS NULL OR algorithm_key IS NULL OR value_key<>algorithm_key THEN "
        "outcome:='IDENTITY_REUSE_SOURCE_NOT_CURRENT'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.member_identity_revision r "
        "JOIN public.member_identity_verification v ON v.verification_id=r.verification_id "
        "AND v.current_revision_id=r.revision_id "
        "JOIN public.member_identity_review_decision d ON d.verification_id=v.verification_id "
        "AND d.revision_id=r.revision_id "
        "JOIN public.service_enrollment se ON se.enrollment_id=v.enrollment_id "
        "AND se.subject_member_id=v.member_id "
        "AND se.current_identity_verification_id=v.verification_id "
        "AND se.status='IDENTITY_VERIFIED' "
        "JOIN identity.member m ON m.member_id=v.member_id AND m.status='created' "
        "WHERE r.revision_id=value_slice3_revision AND r.identity_fingerprint=value_fingerprint "
        "AND r.fingerprint_key_id=value_key AND v.member_id=value_member "
        "AND v.platform_decision_id=value_slice3_decision AND v.version=value_facts_version "
        "AND v.status='VERIFIED' AND d.decision_id=value_slice3_decision "
        "AND d.phase='PLATFORM' AND d.decision='APPROVED' AND d.evidence_digest=value_evidence) THEN "
        "outcome:='IDENTITY_REUSE_SOURCE_NOT_CURRENT'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_member::text,3201)); "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_fingerprint,3202)); "
        "SELECT * INTO existing_by_member FROM identity.identity_subject_claim_registry "
        "WHERE member_id=value_member FOR UPDATE; "
        "SELECT * INTO existing_by_fingerprint FROM identity.identity_subject_claim_registry "
        "WHERE identity_fingerprint=value_fingerprint FOR UPDATE; "
        "IF existing_by_fingerprint.claim_id IS NOT NULL AND "
        "existing_by_fingerprint.member_id IS DISTINCT FROM value_member THEN "
        "outcome:='IDENTITY_REUSE_MEMBER_MISMATCH'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "IF existing_by_member.claim_id IS NOT NULL AND "
        "(existing_by_member.identity_fingerprint<>value_fingerprint OR existing_by_member.fingerprint_key_id<>value_key) THEN "
        "outcome:='IDENTITY_REUSE_FINGERPRINT_MISMATCH'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "IF existing_by_member.claim_id IS NULL AND existing_by_fingerprint.claim_id IS NULL THEN "
        "outcome:='CREATE_ALLOWED'; resolved_claim_id:=value_claim; RETURN NEXT; RETURN; END IF; "
        "IF existing_by_member.claim_id IS NOT NULL THEN existing:=existing_by_member; "
        "ELSE existing:=existing_by_fingerprint; END IF; "
        "IF existing.claim_id IS NULL OR existing.claim_id IS DISTINCT FROM existing_by_fingerprint.claim_id THEN "
        "outcome:='IDENTITY_REUSE_SOURCE_NOT_CURRENT'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "IF existing.source_kind='P1' THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.identity_verification_submission s "
        "JOIN public.identity_verification_decision d ON d.decision_ref=existing.p1_decision_ref "
        "AND d.user_ref=s.user_ref "
        "JOIN identity.user_member_self_link l ON l.user_ref=s.user_ref AND l.member_id=existing.member_id "
        "JOIN identity.member m ON m.member_id=l.member_id AND m.status='created' "
        "WHERE s.submission_id=existing.p1_submission_id AND s.status='verified' "
        "AND s.id_card_digest=existing.identity_fingerprint AND s.encryption_key_id=existing.fingerprint_key_id "
        "AND d.outcome='verified' AND d.facts_version=existing.source_facts_version "
        "AND d.evidence_digest=existing.source_evidence_digest "
        "AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision newer "
        "WHERE newer.supersedes_ref=d.decision_ref)) THEN "
        "outcome:='IDENTITY_REUSE_SOURCE_NOT_CURRENT'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "ELSIF existing.source_kind='SLICE3' THEN "
        "IF NOT EXISTS(SELECT 1 FROM public.member_identity_revision r "
        "JOIN public.member_identity_verification v ON v.verification_id=r.verification_id "
        "AND v.current_revision_id=r.revision_id "
        "JOIN public.member_identity_review_decision d ON d.verification_id=v.verification_id "
        "AND d.revision_id=r.revision_id "
        "JOIN identity.member m ON m.member_id=v.member_id AND m.status='created' "
        "WHERE r.revision_id=existing.slice3_revision_id "
        "AND r.identity_fingerprint=existing.identity_fingerprint "
        "AND r.fingerprint_key_id=existing.fingerprint_key_id "
        "AND v.member_id=existing.member_id AND v.status='VERIFIED' "
        "AND v.platform_decision_id=existing.slice3_decision_id "
        "AND v.version=existing.source_facts_version "
        "AND d.decision_id=existing.slice3_decision_id AND d.phase='PLATFORM' "
        "AND d.decision='APPROVED' AND d.evidence_digest=existing.source_evidence_digest) THEN "
        "outcome:='IDENTITY_REUSE_SOURCE_NOT_CURRENT'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "ELSE outcome:='IDENTITY_REUSE_SOURCE_NOT_CURRENT'; resolved_claim_id:=NULL; RETURN NEXT; RETURN; END IF; "
        "outcome:='REUSED'; resolved_claim_id:=existing.claim_id; "
        "resolved_claim:=to_jsonb(existing); RETURN NEXT; RETURN; END $$"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION identity.slice7_identity_claim_reuse_v2("
        "UUID,UUID,CHAR,VARCHAR,UUID,UUID,BIGINT,CHAR,BOOLEAN,TIMESTAMPTZ) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION identity.slice7_identity_claim_reuse_v2("
        f"UUID,UUID,CHAR,VARCHAR,UUID,UUID,BIGINT,CHAR,BOOLEAN,TIMESTAMPTZ) TO \"{identity_review}\""
    )
    _function(
        "slice7_proxy_major_current_v1",
        "value_actor BIGINT,value_principal UUID,value_permission VARCHAR",
        "UUID",
        "IF value_permission NOT IN ('PLAN_DECISION','SERVICE_WITHDRAW','SERVICE_TRANSFER','PERSONAL_DATA_EXPORT') THEN RETURN NULL; END IF; "
        "SELECT a.authorization_id INTO result FROM public.\"user\" u "
        "JOIN identity.user_member_self_link l ON l.user_ref=u.id "
        "JOIN identity.member proxy_member ON proxy_member.member_id=l.member_id AND proxy_member.status='created' "
        "JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id AND g.principal_member_id=value_principal "
        "JOIN identity.member principal ON principal.member_id=g.principal_member_id AND principal.status='created' "
        "JOIN public.proxy_major_authorization a ON a.proxy_grant_id=g.grant_id "
        "JOIN public.consent_record cr ON cr.enrollment_id=g.enrollment_id AND cr.document_type='PROXY_AUTHORIZATION' AND cr.status='ACCEPTED' "
        "JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' "
        "WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL "
        "AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) "
        "AND g.authorization_document_version_id=cr.document_version_id AND g.witness_decision_id=a.witness_decision_id "
        "AND a.authorization_document_version_id=g.authorization_document_version_id "
        "AND a.principal_member_id=g.principal_member_id AND a.proxy_member_id=g.proxy_member_id "
        "AND a.valid_from<=clock_timestamp() AND (a.valid_until IS NULL OR a.valid_until>clock_timestamp()) "
        "AND a.permission_codes ? value_permission "
        "AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) "
        "AND a.revoked_at IS NULL ORDER BY a.valid_from DESC,a.authorization_id DESC LIMIT 1; RETURN result;",
        all_runtime,
        declarations="result UUID;",
    )
    op.execute(
        "CREATE FUNCTION public.slice7_proxy_plan_decision_guard_v1() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$ BEGIN "
        "IF NEW.actor_context='PROXY' AND public.slice7_proxy_major_current_v1(NEW.actor_user_id,NEW.subject_member_id,'PLAN_DECISION') IS NULL THEN "
        "RAISE EXCEPTION 'PROXY_PERMISSION_FORBIDDEN' USING ERRCODE='42501'; END IF; RETURN NEW; END $$"
    )
    op.execute("REVOKE ALL ON FUNCTION public.slice7_proxy_plan_decision_guard_v1() FROM PUBLIC")
    op.execute(
        "CREATE TRIGGER trg_slice7_proxy_plan_decision_guard_v1 BEFORE INSERT ON public.health_plan_user_decision "
        "FOR EACH ROW EXECUTE FUNCTION public.slice7_proxy_plan_decision_guard_v1()"
    )
    _function(
        "slice7_authority_v1",
        "value_operation VARCHAR,value_target UUID,value_actor BIGINT,value_role VARCHAR,value_tenant BIGINT",
        "JSONB",
        f"IF session_user NOT IN {all_runtime!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value_operation='AUTHORIZE_PROXY_MAJOR' THEN "
        "SELECT jsonb_build_object('actor_user_id',u.id,'proxy_grant_id',g.grant_id,'principal_member_id',g.principal_member_id,'proxy_member_id',g.proxy_member_id,'authorization_document_version_id',g.authorization_document_version_id,'witness_decision_id',g.witness_decision_id) INTO result "
        "FROM public.\"user\" u JOIN public.proxy_grant g ON g.grant_id=value_target JOIN identity.member p ON p.member_id=g.principal_member_id AND p.status='created' JOIN identity.member x ON x.member_id=g.proxy_member_id AND x.status='created' "
        "WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND value_role IN ('super_admin','sys_admin') AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.authorization_document_version_id IS NOT NULL AND g.witness_decision_id IS NOT NULL FOR SHARE OF u,g,p,x; RETURN result; END IF; "
        "IF value_operation='REVOKE_PROXY_MAJOR' THEN "
        "SELECT jsonb_build_object('actor_user_id',u.id,'authorization_id',a.authorization_id,'proxy_grant_id',a.proxy_grant_id,'principal_member_id',a.principal_member_id,'proxy_member_id',a.proxy_member_id,'authorization_document_version_id',a.authorization_document_version_id,'witness_decision_id',a.witness_decision_id,'permission_codes',a.permission_codes,'granted_by',a.granted_by,'valid_from',a.valid_from,'valid_until',a.valid_until,'revoked_at',a.revoked_at,'authorization_version',a.version) INTO result "
        "FROM public.\"user\" u JOIN public.proxy_major_authorization a ON a.authorization_id=value_target WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND value_role IN ('super_admin','sys_admin') AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) AND a.revoked_at IS NULL FOR SHARE OF u,a; RETURN result; END IF; "
        "IF value_operation='VALIDATE_CONTINUATION_CASE' THEN "
        "SELECT jsonb_build_object('new_service_case_id',c.case_id,'new_enrollment_id',c.enrollment_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_ready',sr.readiness_status='SERVICE_READY','assignment_current',pa.status='ACCEPTED','therapist_current',tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED'))) INTO result "
        "FROM public.\"user\" u JOIN public.service_case c ON c.case_id=value_target JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id AND se.tenant_id=c.tenant_id AND se.subject_member_id=c.subject_member_id AND se.status='CASE_CREATED' AND se.service_case_id=c.case_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id AND pa.enrollment_id=c.enrollment_id AND pa.tenant_id=c.tenant_id AND pa.subject_member_id=c.subject_member_id AND pa.therapist_id=c.primary_therapist_id AND pa.status='ACCEPTED' AND pa.service_case_id=c.case_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.institution_application ia ON ia.tenant_internal_id=c.tenant_id AND ia.status='APPROVED' "
        "WHERE u.id=value_actor AND u.role='org_admin' AND u.status='active' AND u.tenant_id=c.tenant_id AND sr.readiness_status='SERVICE_READY' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND NOT EXISTS(SELECT 1 FROM public.health_plan_version hp WHERE hp.service_case_id=c.case_id AND hp.status='ACTIVE') FOR SHARE OF u,c,se,pa,tp,sr,ia; RETURN result; END IF; "
        "IF value_operation IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','CONFIRM_TRANSFER_SCOPE','CANCEL_TRANSFER','COORDINATE_TRANSFER_CLOSE','LINK_CONTINUATION_CASE','READ_TRANSFER','READ_HANDOFF') THEN "
        "SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',source_ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_case_version',c.version,'service_ready',source_sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')),'transfer_target_tenant_id',tr.target_tenant_id,'transfer_requested_scope',tr.requested_scope,'transfer_target_decision',tr.target_decision,'transfer_source_closure_status',tr.source_closure_status,'transfer_scope_confirmed_at',tr.scope_confirmed_at,'transfer_transferred_at',tr.transferred_at,'transfer_status',tr.status,'transfer_version',tr.version,'target_service_ready',target_sr.readiness_status='SERVICE_READY','target_service_tags',COALESCE(target_ia.draft_payload->'service_tags','[]'::jsonb),'handoff_id',h.handoff_id,'handoff_created_at',h.created_at,'handoff_status',h.status,'handoff_version',h.version) INTO result "
        "FROM public.service_transfer_request tr JOIN public.service_case c ON c.case_id=tr.source_service_case_id JOIN public.institution_application source_ia ON source_ia.tenant_public_id=tr.source_tenant_id AND source_ia.tenant_internal_id=c.tenant_id AND source_ia.status='APPROVED' JOIN public.institution_service_readiness source_sr ON source_sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.institution_application target_ia ON target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.status='APPROVED' JOIN public.institution_service_readiness target_sr ON target_sr.tenant_id=target_ia.tenant_internal_id LEFT JOIN public.service_transfer_continuation_handoff h ON h.transfer_id=tr.transfer_id JOIN public.\"user\" u ON u.id=value_actor AND u.status='active' "
        "WHERE tr.transfer_id=value_target AND ((value_operation IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','LINK_CONTINUATION_CASE','READ_HANDOFF') AND u.role='org_admin' AND u.tenant_id=target_ia.tenant_internal_id) OR (value_operation='SOURCE_CLOSE_TRANSFER' AND u.role='org_admin' AND u.tenant_id=c.tenant_id) OR (value_operation='COORDINATE_TRANSFER_CLOSE' AND u.role::text=value_role AND value_role IN ('super_admin','sys_admin')) OR (value_operation IN ('CONFIRM_TRANSFER_SCOPE','CANCEL_TRANSFER') AND u.role='member' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_TRANSFER') IS NOT NULL)) OR (value_operation='READ_TRANSFER' AND ((u.role IN ('org_admin','org_operator') AND u.tenant_id IN (c.tenant_id,target_ia.tenant_internal_id)) OR u.role::text IN ('super_admin','sys_admin') OR (u.role='member' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=c.subject_member_id AND g.status='ACTIVE')))))) "
        "AND (value_operation IN ('LINK_CONTINUATION_CASE','READ_HANDOFF','READ_TRANSFER') OR (source_sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')) AND COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING') NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED'))) FOR UPDATE OF tr,c; RETURN result; END IF; "
        "IF value_operation='CREATE_TRANSFER' THEN "
        "SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING'),'service_case_version',c.version,'service_ready',sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags,'consent_current',true) INTO result FROM public.service_case c JOIN public.institution_application ia ON ia.tenant_internal_id=c.tenant_id AND ia.status='APPROVED' JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.\"user\" u ON u.id=value_actor AND u.role='member' AND u.status='active' WHERE c.case_id=value_target AND sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.tenant_id=c.tenant_id AND tp.service_tags @> c.service_scope_tags AND COALESCE((SELECT le.to_status FROM public.service_case_lifecycle_event le WHERE le.service_case_id=c.case_id ORDER BY le.version DESC LIMIT 1),'PLAN_PENDING') NOT IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED') AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=c.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_TRANSFER') IS NOT NULL) FOR UPDATE OF c; RETURN result; END IF; "
        "IF value_operation='CREATE_EXPORT_FOR_SUBJECT' THEN SELECT CASE WHEN EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=m.member_id) OR public.slice7_proxy_major_current_v1(u.id,m.member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL THEN jsonb_build_object('subject_member_id',m.member_id) ELSE jsonb_build_object('error_code','PROXY_PERMISSION_FORBIDDEN') END INTO result FROM public.\"user\" u JOIN identity.member m ON m.member_id=value_target AND m.status='created' WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=m.member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=m.member_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.revoked_at IS NULL)) FOR SHARE OF u,m; RETURN result; END IF; "
        "IF value_operation='CREATE_EXPORT' THEN SELECT jsonb_build_object('subject_member_id',l.member_id) INTO result FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id AND m.status='created' WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL FOR SHARE OF u,l,m; RETURN result; END IF; "
        "IF value_operation IN ('CANCEL_EXPORT','EXPORT_DOWNLOAD_ACCESS','READ_EXPORT') THEN IF value_role='member' THEN SELECT CASE WHEN EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL THEN jsonb_build_object('subject_member_id',e.subject_member_id,'export_requested_scope',e.requested_scope,'export_status',e.status,'export_requested_at',e.requested_at,'export_ready_at',e.ready_at,'export_expires_at',e.expires_at,'export_downloaded_at',e.downloaded_at,'export_version',e.version,'private_file_id',a.private_file_id,'artifact_digest',encode(a.artifact_digest,'hex')) ELSE jsonb_build_object('error_code','PROXY_PERMISSION_FORBIDDEN') END INTO result FROM public.\"user\" u JOIN public.personal_data_export_request e ON e.export_id=value_target JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' LEFT JOIN public.personal_data_export_artifact a ON a.export_id=e.export_id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR EXISTS(SELECT 1 FROM identity.user_member_self_link l JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id WHERE l.user_ref=u.id AND g.principal_member_id=e.subject_member_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.revoked_at IS NULL)) FOR SHARE OF u,e,m; ELSIF value_role IN ('super_admin','sys_admin') AND value_operation='READ_EXPORT' THEN SELECT jsonb_build_object('subject_member_id',e.subject_member_id,'export_requested_scope',e.requested_scope,'export_status',e.status,'export_requested_at',e.requested_at,'export_ready_at',e.ready_at,'export_expires_at',e.expires_at,'export_downloaded_at',e.downloaded_at,'export_version',e.version) INTO result FROM public.\"user\" u JOIN public.personal_data_export_request e ON e.export_id=value_target WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' FOR SHARE OF u; END IF; RETURN result; END IF; "
        "PERFORM 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' FOR SHARE; IF NOT FOUND THEN RETURN NULL; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_target::text,7)); "
        "SELECT jsonb_build_object('service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,'tenant_public_id',ia.tenant_public_id,'case_status',COALESCE((SELECT e.to_status FROM public.service_case_lifecycle_event e WHERE e.service_case_id=c.case_id ORDER BY e.version DESC LIMIT 1),CASE WHEN sc.schedule_id IS NULL THEN 'PLAN_PENDING' ELSE 'ACTIVE' END),'service_case_version',c.version,'service_ready',sr.readiness_status='SERVICE_READY','primary_therapist_current',pa.status='ACCEPTED' AND current_tp.status='APPROVED_ACTIVE' AND current_tp.current_qualification_version_id IS NOT NULL AND current_tp.qualification_valid_until>=CURRENT_DATE AND current_tp.tenant_id=c.tenant_id AND current_tp.service_tags @> c.service_scope_tags,'consent_current',NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')),'high_risk_count',(SELECT count(*) FROM public.high_risk_task h WHERE h.service_case_id=c.case_id AND h.status IN ('OPEN','CLAIMED','ESCALATED')),'active_plan_id',hp.plan_id,'plan_activated_at',hp.updated_at,'schedule_id',sc.schedule_id,'cycle_anchor_at',sc.cycle_anchor_at,'schedule_version',sc.version_no,'milestone_id',m.milestone_id,'milestone_code',m.code,'window_start',m.window_start,'window_end',m.window_end,'milestone_status',m.status,'milestone_version',m.version,'milestones',COALESCE((SELECT jsonb_agg(jsonb_build_object('milestone_id',all_m.milestone_id,'service_case_id',all_m.service_case_id,'code',all_m.code,'window_start',all_m.window_start,'window_end',all_m.window_end,'status',all_m.status,'completed_at',all_m.completed_at,'record_summary',all_m.record_summary,'version',all_m.version) ORDER BY all_m.code) FROM public.service_milestone all_m WHERE all_m.service_case_id=c.case_id),'[]'::jsonb),'latest_assessment_id',(SELECT h.assessment_id FROM public.health_assessment h WHERE h.service_case_id=c.case_id AND h.status='COMPLETED' ORDER BY h.sequence_no DESC LIMIT 1),'closing_assessment_complete',EXISTS(SELECT 1 FROM public.service_closing_assessment ca WHERE ca.service_case_id=c.case_id),'summary_complete',EXISTS(SELECT 1 FROM public.service_summary sx WHERE sx.service_case_id=c.case_id),'summary_acknowledged',EXISTS(SELECT 1 FROM public.service_summary sx JOIN public.service_summary_acknowledgement ack ON ack.summary_id=sx.summary_id WHERE sx.service_case_id=c.case_id),'transfer_target_tenant_id',tr.target_tenant_id,'transfer_requested_scope',tr.requested_scope,'transfer_target_decision',tr.target_decision,'transfer_source_closure_status',tr.source_closure_status,'transfer_scope_confirmed_at',tr.scope_confirmed_at,'transfer_transferred_at',tr.transferred_at,'transfer_status',tr.status,'transfer_version',tr.version,'summary_assessment_id',sm.assessment_id,'summary_content',sm.content,'summary_created_at',sm.created_at,'summary_version',sm.version) INTO result FROM public.service_case c JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.institution_application ia ON ia.tenant_internal_id=c.tenant_id AND ia.status='APPROVED' LEFT JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id LEFT JOIN public.therapist_profile current_tp ON current_tp.therapist_id=c.primary_therapist_id LEFT JOIN public.health_plan_version hp ON hp.service_case_id=c.case_id AND hp.status='ACTIVE' LEFT JOIN public.service_cycle_schedule sc ON sc.service_case_id=c.case_id AND sc.is_current LEFT JOIN public.service_milestone m ON m.milestone_id=value_target AND m.service_case_id=c.case_id LEFT JOIN public.service_transfer_request tr ON tr.transfer_id=value_target AND tr.source_service_case_id=c.case_id LEFT JOIN public.service_summary sm ON sm.summary_id=value_target AND sm.service_case_id=c.case_id WHERE (c.case_id=value_target OR m.milestone_id IS NOT NULL OR tr.transfer_id IS NOT NULL OR sm.summary_id IS NOT NULL) AND (value_tenant IS NULL OR c.tenant_id=value_tenant OR value_role IN ('super_admin','sys_admin') OR (tr.transfer_id IS NOT NULL AND EXISTS(SELECT 1 FROM public.institution_application target_ia WHERE target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.tenant_internal_id=value_tenant AND target_ia.status='APPROVED'))) AND (((value_role IN ('org_admin','org_operator')) AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active' AND (u.tenant_id=c.tenant_id OR EXISTS(SELECT 1 FROM public.institution_application target_ia WHERE tr.transfer_id IS NOT NULL AND target_ia.tenant_public_id=tr.target_tenant_id AND target_ia.tenant_internal_id=u.tenant_id AND target_ia.status='APPROVED')))) OR (value_role='therapist' AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.therapist_profile tp ON tp.user_id=u.id WHERE u.id=value_actor AND u.role='therapist' AND u.status='active' AND u.tenant_id=c.tenant_id AND tp.therapist_id=c.primary_therapist_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND pa.status='ACCEPTED')) OR (value_role='member' AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND (l.member_id=c.subject_member_id OR (value_operation='WITHDRAW_CASE' AND public.slice7_proxy_major_current_v1(u.id,c.subject_member_id,'SERVICE_WITHDRAW') IS NOT NULL)))) OR (value_role IN ('super_admin','sys_admin') AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.role::text=value_role AND u.status='active'))) FOR UPDATE OF c; IF result IS NOT NULL AND value_operation NOT LIKE 'READ_%' AND ((result->>'service_ready')::boolean IS NOT TRUE OR (result->>'primary_therapist_current')::boolean IS NOT TRUE OR (result->>'consent_current')::boolean IS NOT TRUE OR result->>'case_status' IN ('COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED')) THEN RETURN NULL; END IF; RETURN result;",
        all_runtime,
        declarations="result JSONB;",
    )
    _function(
        "slice7_mutation_replay_v1",
        "value_actor BIGINT,value_operation VARCHAR,value_key VARCHAR,value_digest BYTEA",
        "JSONB",
        f"IF session_user NOT IN {writers!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.service_fulfillment_receipt WHERE actor_scope=value_actor::text AND operation=value_operation AND idempotency_key=value_key FOR SHARE; IF stored_response IS NULL THEN RETURN NULL; END IF; IF stored_digest<>value_digest THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response;",
        writers,
        declarations="stored_digest BYTEA; stored_response JSONB;",
    )
    _function(
        "slice7_mutation_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user NOT IN {mutation_runtime!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "operation_name:=value->>'operation'; IF operation_name IS NULL OR value->>'request_digest' !~ '^[0-9a-f]{64}$' OR value->>'expected_response_digest' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(value->'response')<>'object' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        f"IF (operation_name IN ('ACTIVATE_CYCLE','COMPLETE_MILESTONE') AND session_user<>'{milestone}') OR (operation_name='MARK_MISSED' AND session_user<>'{export}') OR (operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','CREATE_CLOSING_ASSESSMENT','CREATE_SUMMARY','ACK_SUMMARY','COMPLETE_CASE') AND session_user<>'{case}') OR (operation_name IN ('CREATE_TRANSFER','CANCEL_TRANSFER','CONFIRM_TRANSFER_SCOPE','START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','COORDINATE_TRANSFER_CLOSE','LINK_CONTINUATION_CASE','AUTHORIZE_PROXY_MAJOR','REVOKE_PROXY_MAJOR','CREATE_EXPORT','CANCEL_EXPORT','EXPORT_DOWNLOAD_ACCESS') AND session_user<>'{transfer}') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.service_fulfillment_receipt WHERE actor_scope=value->>'actor_scope' AND operation=operation_name AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'target_id',7)); "
        "IF operation_name<>'MARK_MISSED' THEN current_authority:=public.slice7_authority_v1(COALESCE(value->>'authority_operation',operation_name),COALESCE((value->>'authority_target_id')::uuid,(value->>'target_id')::uuid),(value->>'actor_user_id')::bigint,value->>'actor_role',(value->>'actor_tenant_id')::bigint); IF current_authority IS NULL OR current_authority<>value->'authority' THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; END IF; "
        "IF operation_name='MARK_MISSED' THEN UPDATE public.service_milestone SET status='MISSED',version=version+1 WHERE milestone_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('PENDING','DUE') AND window_end<((value->>'occurred_at')::timestamptz AT TIME ZONE 'Asia/Shanghai')::date RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_milestone_revision(revision_id,milestone_id,version_no,status,body,body_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,'MISSED',jsonb_build_object('reason_code','MILESTONE_MISSED','worker_id',value->>'worker_id'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); "
        "ELSIF operation_name='ACTIVATE_CYCLE' THEN INSERT INTO public.service_cycle_schedule(schedule_id,service_case_id,subject_member_id,tenant_id,active_plan_id,version_no,cycle_anchor_at,is_current,created_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'subject_member_id')::uuid,(current_authority->>'tenant_id')::bigint,(value->'response'->>'active_plan_id')::uuid,1,(value->>'activated_at')::timestamptz,true,(value->>'occurred_at')::timestamptz,1); INSERT INTO public.service_milestone(milestone_id,schedule_id,service_case_id,code,window_start,window_end,status,completed_at,record_summary,version) SELECT (value->'milestone_ids'->>code)::uuid,(value->>'operation_id')::uuid,(value->>'target_id')::uuid,code,(value->'windows'->code->>'start')::date,(value->'windows'->code->>'end')::date,CASE code WHEN 'D0' THEN 'DUE' ELSE 'PENDING' END,NULL,NULL,1 FROM unnest(ARRAY['D0','D7','D14','D21','D28']) code; "
        "ELSIF operation_name='COMPLETE_MILESTONE' THEN UPDATE public.service_milestone SET status='COMPLETED',completed_at=(value->>'occurred_at')::timestamptz,record_summary=value->'record_summary',version=version+1 WHERE milestone_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('PENDING','DUE') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_milestone_revision(revision_id,milestone_id,version_no,status,body,body_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,'COMPLETED',jsonb_build_object('record_summary',value->'record_summary','evidence_refs',value->'evidence_refs'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); IF (SELECT count(*) FROM public.service_milestone m WHERE m.service_case_id=(current_authority->>'service_case_id')::uuid)=5 AND NOT EXISTS(SELECT 1 FROM public.service_milestone m WHERE m.service_case_id=(current_authority->>'service_case_id')::uuid AND m.status<>'COMPLETED') THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,'ACTIVE','CLOSING','MILESTONES_COMPLETED',(value->>'occurred_at')::timestamptz,case_version); END IF; "
        "ELSIF operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','COMPLETE_CASE') THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,COALESCE(value->'authority'->>'case_status','ACTIVE'),value->'response'->>'lifecycle_status',COALESCE(value->>'reason_code',operation_name),(value->>'occurred_at')::timestamptz,affected); "
        "ELSIF operation_name='CREATE_CLOSING_ASSESSMENT' THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_closing_assessment(closing_assessment_id,service_case_id,assessment_id,version_no,final_retest_evidence,created_at,version) SELECT (value->>'operation_id')::uuid,(value->>'target_id')::uuid,(value->>'assessment_id')::uuid,COALESCE(max(version_no),0)+1,value->'final_retest_evidence',(value->>'occurred_at')::timestamptz,1 FROM public.service_closing_assessment WHERE service_case_id=(value->>'target_id')::uuid; "
        "ELSIF operation_name='CREATE_SUMMARY' THEN UPDATE public.service_case SET version=version+1 WHERE case_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_summary(summary_id,service_case_id,assessment_id,version_no,content,content_digest,created_at,version) SELECT (value->>'operation_id')::uuid,(value->>'target_id')::uuid,(value->>'assessment_id')::uuid,COALESCE(max(version_no),0)+1,jsonb_build_object('final_retest_evidence',value->'final_retest_evidence','milestone_outcomes',value->'milestone_outcomes','safety_follow_up',value->'safety_follow_up','next_step',value->'next_step'),decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz,1 FROM public.service_summary WHERE service_case_id=(value->>'target_id')::uuid; "
        "ELSIF operation_name='ACK_SUMMARY' THEN PERFORM 1 FROM public.service_summary s WHERE s.summary_id=(value->>'target_id')::uuid AND s.version=(value->>'expected_version')::bigint FOR UPDATE; IF NOT FOUND THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_summary_acknowledgement(acknowledgement_id,summary_id,subject_member_id,actor_user_id,proxy_grant_id,viewed_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'subject_member_id')::uuid,(value->>'actor_user_id')::bigint,NULL,(value->>'occurred_at')::timestamptz,1); readiness:=public.slice7_closing_readiness_v1((current_authority->>'service_case_id')::uuid); IF readiness<>'READY_TO_CLOSE' THEN RAISE EXCEPTION 'CLOSURE_PREREQUISITE_MISSING'; END IF; UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint AND current_authority->>'case_status'='CLOSING' RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,'CLOSING','COMPLETED','SUMMARY_ACKNOWLEDGED',(value->>'occurred_at')::timestamptz,case_version); "
        "ELSIF operation_name='CREATE_TRANSFER' THEN INSERT INTO public.service_transfer_request(transfer_id,source_service_case_id,source_tenant_id,target_tenant_id,subject_member_id,status,requested_scope,target_decision,source_closure_status,scope_confirmed_at,transferred_at,created_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'tenant_public_id')::uuid,(value->>'target_tenant_id')::uuid,(current_authority->>'subject_member_id')::uuid,'REQUESTED_BY_USER',value->'requested_scope',NULL,NULL,NULL,NULL,(value->>'occurred_at')::timestamptz,1); INSERT INTO public.service_transfer_scope_revision(scope_revision_id,transfer_id,version_no,scope,scope_digest,created_at) VALUES ((value->>'receipt_id')::uuid,(value->>'operation_id')::uuid,1,value->'requested_scope',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); "
        "ELSIF operation_name='START_REVIEW_TRANSFER' THEN UPDATE public.service_transfer_request SET status='NEW_INSTITUTION_REVIEWING',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='REQUESTED_BY_USER' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; "
        "ELSIF operation_name IN ('ACCEPT_TRANSFER','REJECT_TRANSFER') THEN IF operation_name='ACCEPT_TRANSFER' AND ((current_authority->>'target_service_ready')::boolean IS NOT TRUE OR NOT (current_authority->'target_service_tags' ? (value->>'service_label'))) THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; UPDATE public.service_transfer_request SET status=CASE operation_name WHEN 'ACCEPT_TRANSFER' THEN 'ACCEPTED' ELSE 'REJECTED_BY_NEW_INSTITUTION' END,target_decision=value->>'reason_code',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='NEW_INSTITUTION_REVIEWING' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; "
        "ELSIF operation_name='SOURCE_CLOSE_TRANSFER' THEN UPDATE public.service_transfer_request SET status='OLD_INSTITUTION_CLOSING',source_closure_status=value->>'risk_disposition',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='ACCEPTED' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; "
        "ELSIF operation_name='CONFIRM_TRANSFER_SCOPE' THEN UPDATE public.service_transfer_request SET status='USER_SCOPE_CONFIRMED',requested_scope=value->'exact_scope',scope_confirmed_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='OLD_INSTITUTION_CLOSING' AND requested_scope=value->'exact_scope' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; INSERT INTO public.service_transfer_scope_revision(scope_revision_id,transfer_id,version_no,scope,scope_digest,created_at) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,affected,value->'exact_scope',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); "
        "ELSIF operation_name='CANCEL_TRANSFER' THEN UPDATE public.service_transfer_request SET status='CANCELLED_BY_USER',version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('REQUESTED_BY_USER','NEW_INSTITUTION_REVIEWING') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; "
        "ELSIF operation_name='COORDINATE_TRANSFER_CLOSE' THEN UPDATE public.service_transfer_request SET status='TRANSFERRED',transferred_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='USER_SCOPE_CONFIRMED' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; UPDATE public.service_case SET version=version+1 WHERE case_id=(current_authority->>'service_case_id')::uuid AND version=(current_authority->>'service_case_version')::bigint RETURNING version INTO case_version; IF case_version IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; UPDATE public.service_enrollment SET status='REVOKED',updated_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE enrollment_id=(SELECT c.enrollment_id FROM public.service_case c WHERE c.case_id=(current_authority->>'service_case_id')::uuid) AND status='CASE_CREATED'; GET DIAGNOSTICS changed=ROW_COUNT; IF changed<>1 THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; INSERT INTO public.service_case_lifecycle_event(lifecycle_event_id,service_case_id,from_status,to_status,reason_code,occurred_at,version) VALUES ((value->>'lifecycle_event_id')::uuid,(current_authority->>'service_case_id')::uuid,current_authority->>'case_status','TRANSFERRED','SERVICE_TRANSFERRED',(value->>'occurred_at')::timestamptz,case_version); INSERT INTO public.service_transfer_continuation_handoff(handoff_id,transfer_id,source_service_case_id,source_tenant_id,target_tenant_id,subject_member_id,authorized_scope,scope_digest,status,created_at,linked_enrollment_id,linked_service_case_id,linked_at,version) VALUES ((value->>'handoff_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'service_case_id')::uuid,(current_authority->>'tenant_public_id')::uuid,(current_authority->>'transfer_target_tenant_id')::uuid,(current_authority->>'subject_member_id')::uuid,current_authority->'transfer_requested_scope',decode(value->>'handoff_scope_digest','hex'),'PENDING_TARGET_ENROLLMENT',(value->>'occurred_at')::timestamptz,NULL,NULL,NULL,1); "
        "ELSIF operation_name='LINK_CONTINUATION_CASE' THEN PERFORM 1 FROM public.service_transfer_continuation_handoff h JOIN public.service_transfer_request tr ON tr.transfer_id=h.transfer_id AND tr.status='TRANSFERRED' JOIN public.institution_application target_ia ON target_ia.tenant_public_id=h.target_tenant_id AND target_ia.status='APPROVED' JOIN public.service_case c ON c.case_id=(value->>'new_service_case_id')::uuid AND c.enrollment_id=(value->>'new_enrollment_id')::uuid AND c.subject_member_id=h.subject_member_id AND c.tenant_id=target_ia.tenant_internal_id JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id AND se.status='CASE_CREATED' AND se.service_case_id=c.case_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id AND pa.enrollment_id=c.enrollment_id AND pa.status='ACCEPTED' AND pa.service_case_id=c.case_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id AND tp.tenant_id=c.tenant_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND tp.service_tags @> c.service_scope_tags JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id AND sr.readiness_status='SERVICE_READY' WHERE h.transfer_id=(value->>'target_id')::uuid AND h.version=(value->>'expected_version')::bigint AND h.status='PENDING_TARGET_ENROLLMENT' AND NOT EXISTS(SELECT 1 FROM public.health_plan_version hp WHERE hp.service_case_id=c.case_id AND hp.status='ACTIVE') AND NOT EXISTS(SELECT required.document_type FROM unnest(ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id AND cd.status='PUBLISHED' WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED')) FOR UPDATE OF h,c,se,pa,tp,sr; IF NOT FOUND THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; UPDATE public.service_transfer_continuation_handoff SET status='CONTINUATION_CASE_LINKED',linked_enrollment_id=(value->>'new_enrollment_id')::uuid,linked_service_case_id=(value->>'new_service_case_id')::uuid,linked_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE transfer_id=(value->>'target_id')::uuid AND version=(value->>'expected_version')::bigint AND status='PENDING_TARGET_ENROLLMENT' RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'TRANSFER_STATE_CONFLICT'; END IF; "
        "ELSIF operation_name='AUTHORIZE_PROXY_MAJOR' THEN IF current_authority->>'authorization_document_version_id'<>value->>'authorization_document_version_id' OR current_authority->>'witness_decision_id'<>value->>'witness_decision_id' OR jsonb_typeof(value->'permission_codes')<>'array' OR jsonb_array_length(value->'permission_codes') NOT BETWEEN 1 AND 4 OR NOT value->'permission_codes' <@ '[\"PLAN_DECISION\",\"SERVICE_WITHDRAW\",\"SERVICE_TRANSFER\",\"PERSONAL_DATA_EXPORT\"]'::jsonb OR (value->>'valid_until') IS NOT NULL AND (value->>'valid_until')::timestamptz<=(value->>'occurred_at')::timestamptz OR EXISTS(SELECT 1 FROM public.proxy_major_authorization a WHERE a.proxy_grant_id=(value->>'target_id')::uuid AND NOT EXISTS(SELECT 1 FROM public.proxy_major_authorization newer WHERE newer.authorization_id=a.authorization_id AND newer.version>a.version) AND a.revoked_at IS NULL) THEN RAISE EXCEPTION 'CURRENTNESS_FORBIDDEN'; END IF; INSERT INTO public.proxy_major_authorization(authorization_revision_id,authorization_id,proxy_grant_id,principal_member_id,proxy_member_id,authorization_document_version_id,witness_decision_id,permission_codes,granted_by,valid_from,valid_until,revoked_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'principal_member_id')::uuid,(current_authority->>'proxy_member_id')::uuid,(value->>'authorization_document_version_id')::uuid,(value->>'witness_decision_id')::uuid,value->'permission_codes',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,(value->>'valid_until')::timestamptz,NULL,1); "
        "ELSIF operation_name='REVOKE_PROXY_MAJOR' THEN IF (current_authority->>'authorization_version')::bigint<>(value->>'expected_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.proxy_major_authorization(authorization_revision_id,authorization_id,proxy_grant_id,principal_member_id,proxy_member_id,authorization_document_version_id,witness_decision_id,permission_codes,granted_by,valid_from,valid_until,revoked_at,version) VALUES ((value->>'operation_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'proxy_grant_id')::uuid,(current_authority->>'principal_member_id')::uuid,(current_authority->>'proxy_member_id')::uuid,(current_authority->>'authorization_document_version_id')::uuid,(current_authority->>'witness_decision_id')::uuid,current_authority->'permission_codes',(current_authority->>'granted_by')::bigint,(current_authority->>'valid_from')::timestamptz,(current_authority->>'valid_until')::timestamptz,(value->>'occurred_at')::timestamptz,(current_authority->>'authorization_version')::bigint+1); "
        "ELSIF operation_name='CREATE_EXPORT' THEN INSERT INTO public.personal_data_export_request(export_id,subject_member_id,requested_scope,status,requested_by,requested_at,ready_at,expires_at,downloaded_at,version) VALUES ((value->>'operation_id')::uuid,(current_authority->>'subject_member_id')::uuid,value->'requested_scope','REQUESTED',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,NULL,NULL,NULL,1); "
        "ELSIF operation_name='CANCEL_EXPORT' THEN UPDATE public.personal_data_export_request SET status='CANCELLED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE export_id=(value->>'target_id')::uuid AND subject_member_id=(current_authority->>'subject_member_id')::uuid AND version=(value->>'expected_version')::bigint AND status IN ('REQUESTED','GENERATING') RETURNING version INTO affected; IF affected IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; "
        "ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS' THEN IF current_authority->>'private_file_id' IS NULL OR current_authority->>'artifact_digest' IS NULL THEN RAISE EXCEPTION 'EXPORT_NOT_READY'; END IF; IF (current_authority->>'export_version')::bigint<>(value->>'expected_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; INSERT INTO public.personal_data_export_download_access(access_id,export_id,private_file_id,actor_user_id,reason_code,expires_at,consumed_at,created_at,version) VALUES ((value->'response'->>'access_id')::uuid,(value->>'target_id')::uuid,(current_authority->>'private_file_id')::uuid,(value->>'actor_user_id')::bigint,value->>'reason',(value->'response'->>'expires_at')::timestamptz,NULL,(value->>'occurred_at')::timestamptz,1); "
        "ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) VALUES ((value->>'audit_id')::uuid,operation_name,(value->>'actor_user_id')::bigint,value->>'actor_role',(value->>'target_id')::uuid,decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,(value->>'target_id')::uuid,operation_name,jsonb_build_object('operation',operation_name,'target_id',value->>'target_id'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); INSERT INTO public.service_fulfillment_receipt(receipt_id,actor_scope,operation,target_id,idempotency_key,request_digest,response_json,postimage_digest,created_at) VALUES ((value->>'receipt_id')::uuid,value->>'actor_scope',operation_name,(value->>'target_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'expected_response_digest','hex'),(value->>'occurred_at')::timestamptz); RETURN value->'response';",
        mutation_runtime,
        declarations="operation_name VARCHAR; stored_digest BYTEA; stored_response JSONB; affected BIGINT; changed BIGINT; case_version BIGINT; readiness VARCHAR; current_authority JSONB;",
    )
    _function(
        "slice7_mutation_confirm_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user NOT IN {writers!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "operation_name:=value->>'operation'; IF operation_name IS NULL OR value->>'request_digest' !~ '^[0-9a-f]{64}$' OR value->>'postimage_digest' !~ '^[0-9a-f]{64}$' OR jsonb_typeof(value->'response')<>'object' THEN RETURN jsonb_build_object('outcome','UNKNOWN'); END IF; "
        "SELECT EXISTS(SELECT 1 FROM public.service_fulfillment_receipt r JOIN public.service_fulfillment_audit a ON a.audit_id=(value->>'audit_id')::uuid JOIN public.service_fulfillment_outbox o ON o.event_id=(value->>'event_id')::uuid WHERE r.receipt_id=(value->>'receipt_id')::uuid AND r.actor_scope=value->>'actor_scope' AND r.operation=operation_name AND r.target_id=(value->>'target_id')::uuid AND r.idempotency_key=value->>'idempotency_key' AND r.request_digest=decode(value->>'request_digest','hex') AND r.response_json=value->'response' AND r.postimage_digest=decode(value->>'postimage_digest','hex') AND r.created_at=(value->>'occurred_at')::timestamptz AND a.action=operation_name AND a.actor_user_id=(value->>'actor_user_id')::bigint AND a.actor_role=value->>'actor_role' AND a.target_id=(value->>'target_id')::uuid AND a.evidence_digest=decode(value->>'request_digest','hex') AND a.occurred_at=(value->>'occurred_at')::timestamptz AND o.aggregate_ref=(value->>'target_id')::uuid AND o.event_type=operation_name AND o.payload_json=jsonb_build_object('operation',operation_name,'target_id',value->>'target_id') AND o.payload_digest=decode(value->>'request_digest','hex') AND o.created_at=(value->>'occurred_at')::timestamptz) INTO core_confirmed; "
        "artifact_confirmed:=false; IF core_confirmed THEN "
        "IF operation_name='ACTIVATE_CYCLE' THEN SELECT EXISTS(SELECT 1 FROM public.service_cycle_schedule s WHERE s.schedule_id=(value->>'operation_id')::uuid AND s.service_case_id=(value->>'target_id')::uuid AND s.active_plan_id=(value->'response'->>'active_plan_id')::uuid AND s.created_at=(value->>'occurred_at')::timestamptz AND (SELECT count(*) FROM public.service_milestone m WHERE m.schedule_id=s.schedule_id)=5) INTO artifact_confirmed; "
        "ELSIF operation_name IN ('COMPLETE_MILESTONE','MARK_MISSED') THEN SELECT EXISTS(SELECT 1 FROM public.service_milestone_revision r WHERE r.revision_id=(value->>'operation_id')::uuid AND r.milestone_id=(value->>'target_id')::uuid AND r.version_no=(value->'response'->>'version')::bigint AND r.status=value->'response'->>'status' AND r.body_digest=decode(value->>'request_digest','hex') AND r.created_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name IN ('PAUSE_CASE','RESUME_CASE','WITHDRAW_CASE','TERMINATE_CASE','UNABLE_TO_CONTACT','SAFETY_TERMINATE','COMPLETE_CASE') THEN SELECT EXISTS(SELECT 1 FROM public.service_case_lifecycle_event e WHERE e.lifecycle_event_id=(value->>'operation_id')::uuid AND e.service_case_id=(value->>'target_id')::uuid AND e.to_status=value->'response'->>'lifecycle_status' AND e.version=(value->'response'->>'version')::bigint AND e.occurred_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name='CREATE_CLOSING_ASSESSMENT' THEN SELECT EXISTS(SELECT 1 FROM public.service_closing_assessment a WHERE a.closing_assessment_id=(value->>'operation_id')::uuid AND a.service_case_id=(value->>'target_id')::uuid AND a.assessment_id=(value->'response'->>'assessment_id')::uuid AND a.created_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name='CREATE_SUMMARY' THEN SELECT EXISTS(SELECT 1 FROM public.service_summary s WHERE s.summary_id=(value->>'operation_id')::uuid AND s.service_case_id=(value->>'target_id')::uuid AND s.assessment_id=(value->'response'->>'assessment_id')::uuid AND s.content_digest=decode(value->>'request_digest','hex') AND s.created_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name='ACK_SUMMARY' THEN SELECT EXISTS(SELECT 1 FROM public.service_summary_acknowledgement a JOIN public.service_case_lifecycle_event e ON e.lifecycle_event_id=(value->>'lifecycle_event_id')::uuid WHERE a.acknowledgement_id=(value->>'operation_id')::uuid AND a.summary_id=(value->>'target_id')::uuid AND a.actor_user_id=(value->>'actor_user_id')::bigint AND a.viewed_at=(value->>'occurred_at')::timestamptz AND e.to_status='COMPLETED' AND e.occurred_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name='CREATE_TRANSFER' THEN SELECT EXISTS(SELECT 1 FROM public.service_transfer_request t JOIN public.service_transfer_scope_revision r ON r.transfer_id=t.transfer_id AND r.scope_revision_id=(value->>'receipt_id')::uuid WHERE t.transfer_id=(value->>'operation_id')::uuid AND t.source_service_case_id=(value->>'target_id')::uuid AND t.status=value->'response'->>'status' AND t.version=(value->'response'->>'version')::bigint AND t.created_at=(value->>'occurred_at')::timestamptz AND r.scope_digest=decode(value->>'request_digest','hex')) INTO artifact_confirmed; "
        "ELSIF operation_name IN ('START_REVIEW_TRANSFER','ACCEPT_TRANSFER','REJECT_TRANSFER','SOURCE_CLOSE_TRANSFER','CANCEL_TRANSFER') THEN SELECT EXISTS(SELECT 1 FROM public.service_transfer_request t WHERE t.transfer_id=(value->>'target_id')::uuid AND t.status=value->'response'->>'status' AND t.version>=(value->'response'->>'version')::bigint) INTO artifact_confirmed; "
        "ELSIF operation_name='CONFIRM_TRANSFER_SCOPE' THEN SELECT EXISTS(SELECT 1 FROM public.service_transfer_request t JOIN public.service_transfer_scope_revision r ON r.transfer_id=t.transfer_id WHERE t.transfer_id=(value->>'target_id')::uuid AND t.status=value->'response'->>'status' AND t.version>=(value->'response'->>'version')::bigint AND r.scope_revision_id=(value->>'operation_id')::uuid AND r.version_no=(value->'response'->>'version')::bigint AND r.scope=value->'response'->'requested_scope' AND r.scope_digest=decode(value->>'request_digest','hex') AND r.created_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name='COORDINATE_TRANSFER_CLOSE' THEN SELECT EXISTS(SELECT 1 FROM public.service_transfer_request t JOIN public.service_transfer_continuation_handoff h ON h.transfer_id=t.transfer_id JOIN public.service_case c ON c.case_id=h.source_service_case_id JOIN public.service_enrollment se ON se.enrollment_id=c.enrollment_id JOIN public.service_case_lifecycle_event le ON le.lifecycle_event_id=(value->>'lifecycle_event_id')::uuid AND le.service_case_id=c.case_id WHERE t.transfer_id=(value->>'target_id')::uuid AND t.source_service_case_id=(value->'response'->>'source_service_case_id')::uuid AND t.status='TRANSFERRED' AND t.version>=(value->'response'->>'version')::bigint AND h.handoff_id=(value->>'handoff_id')::uuid AND h.source_service_case_id=(value->'response'->>'source_service_case_id')::uuid AND h.source_tenant_id=(value->'response'->>'source_tenant_id')::uuid AND h.target_tenant_id=(value->'response'->>'target_tenant_id')::uuid AND h.authorized_scope=value->'response'->'requested_scope' AND h.scope_digest=decode(value->>'handoff_scope_digest','hex') AND h.created_at=(value->>'occurred_at')::timestamptz AND h.status IN ('PENDING_TARGET_ENROLLMENT','ENROLLMENT_CREATED','ASSIGNMENT_PENDING','CONTINUATION_CASE_LINKED') AND se.status='REVOKED' AND c.version>=le.version AND le.to_status='TRANSFERRED' AND le.reason_code='SERVICE_TRANSFERRED' AND le.occurred_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; "
        "ELSIF operation_name='LINK_CONTINUATION_CASE' THEN SELECT EXISTS(SELECT 1 FROM public.service_transfer_continuation_handoff h WHERE h.transfer_id=(value->>'target_id')::uuid AND h.handoff_id=(value->'response'->>'handoff_id')::uuid AND h.linked_service_case_id=(value->'response'->>'linked_service_case_id')::uuid AND h.status='CONTINUATION_CASE_LINKED' AND h.version>=(value->'response'->>'version')::bigint) INTO artifact_confirmed; "
        "ELSIF operation_name IN ('AUTHORIZE_PROXY_MAJOR','REVOKE_PROXY_MAJOR') THEN SELECT EXISTS(SELECT 1 FROM public.proxy_major_authorization a WHERE a.authorization_revision_id=(value->>'operation_id')::uuid AND a.authorization_id=(value->'response'->>'authorization_id')::uuid AND a.version=(value->'response'->>'version')::bigint AND ((operation_name='AUTHORIZE_PROXY_MAJOR' AND a.revoked_at IS NULL) OR (operation_name='REVOKE_PROXY_MAJOR' AND a.revoked_at=(value->>'occurred_at')::timestamptz))) INTO artifact_confirmed; "
        "ELSIF operation_name='CREATE_EXPORT' THEN SELECT EXISTS(SELECT 1 FROM public.personal_data_export_request e WHERE e.export_id=(value->>'operation_id')::uuid AND e.export_id=(value->>'target_id')::uuid AND e.subject_member_id=(value->'response'->>'subject_member_id')::uuid AND e.requested_by=(value->>'actor_user_id')::bigint AND e.requested_at=(value->>'occurred_at')::timestamptz AND e.requested_scope=value->'response'->'requested_scope' AND e.version>=(value->'response'->>'version')::bigint) INTO artifact_confirmed; "
        "ELSIF operation_name='CANCEL_EXPORT' THEN SELECT EXISTS(SELECT 1 FROM public.personal_data_export_request e WHERE e.export_id=(value->>'target_id')::uuid AND e.status='CANCELLED' AND e.version>=(value->'response'->>'version')::bigint) INTO artifact_confirmed; "
        "ELSIF operation_name='EXPORT_DOWNLOAD_ACCESS' THEN SELECT EXISTS(SELECT 1 FROM public.personal_data_export_download_access a WHERE a.access_id=(value->'response'->>'access_id')::uuid AND a.export_id=(value->>'target_id')::uuid AND a.actor_user_id=(value->>'actor_user_id')::bigint AND a.expires_at=(value->'response'->>'expires_at')::timestamptz AND a.created_at=(value->>'occurred_at')::timestamptz) INTO artifact_confirmed; END IF; "
        "IF artifact_confirmed THEN RETURN jsonb_build_object('outcome','COMMITTED'); END IF; RETURN jsonb_build_object('outcome','UNKNOWN'); END IF; "
        "SELECT (SELECT count(*) FROM public.service_fulfillment_receipt r WHERE r.receipt_id=(value->>'receipt_id')::uuid)+(SELECT count(*) FROM public.service_fulfillment_audit a WHERE a.audit_id=(value->>'audit_id')::uuid)+(SELECT count(*) FROM public.service_fulfillment_outbox o WHERE o.event_id=(value->>'event_id')::uuid) INTO evidence_count; IF evidence_count=0 THEN RETURN jsonb_build_object('outcome','NOT_COMMITTED'); END IF; RETURN jsonb_build_object('outcome','UNKNOWN');",
        mutation_runtime,
        declarations="operation_name VARCHAR; core_confirmed BOOLEAN; artifact_confirmed BOOLEAN; evidence_count BIGINT;",
    )
    _function(
        "slice7_read_one_v1",
        "value_resource VARCHAR,value_target UUID,value_actor BIGINT,value_role VARCHAR",
        "JSONB",
        f"IF session_user NOT IN {readers!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "authority:=public.slice7_authority_v1('READ_'||value_resource,value_target,value_actor,value_role,NULL); IF authority IS NULL THEN RETURN NULL; END IF; "
        "IF value_resource='MILESTONE' THEN SELECT to_jsonb(m.*) INTO result FROM public.service_milestone m WHERE m.milestone_id=value_target; ELSIF value_resource='TRANSFER' THEN SELECT to_jsonb(t.*) INTO result FROM public.service_transfer_request t WHERE t.transfer_id=value_target; ELSIF value_resource='HANDOFF' THEN SELECT jsonb_build_object('handoff_id',h.handoff_id,'transfer_id',h.transfer_id,'source_service_case_id',h.source_service_case_id,'source_tenant_id',h.source_tenant_id,'target_tenant_id',h.target_tenant_id,'subject_member_id',h.subject_member_id,'authorized_scope',h.authorized_scope,'status',h.status,'created_at',h.created_at,'linked_enrollment_id',h.linked_enrollment_id,'linked_service_case_id',h.linked_service_case_id,'linked_at',h.linked_at,'version',h.version) INTO result FROM public.service_transfer_continuation_handoff h WHERE h.transfer_id=value_target; ELSIF value_resource='EXPORT' THEN SELECT to_jsonb(e.*) INTO result FROM public.personal_data_export_request e WHERE e.export_id=value_target; ELSIF value_resource='SUMMARY' THEN SELECT jsonb_build_object('summary_id',s.summary_id,'service_case_id',s.service_case_id,'assessment_id',s.assessment_id,'final_retest_evidence',s.content->'final_retest_evidence','milestone_outcomes',s.content->'milestone_outcomes','safety_follow_up',s.content->'safety_follow_up','next_step',s.content->'next_step','created_at',s.created_at,'viewed_at',(SELECT a.viewed_at FROM public.service_summary_acknowledgement a WHERE a.summary_id=s.summary_id),'version',s.version) INTO result FROM public.service_summary s WHERE s.service_case_id=value_target ORDER BY s.version_no DESC LIMIT 1; ELSIF value_resource='FULFILLMENT' THEN SELECT jsonb_build_object('service_case_id',c.case_id,'lifecycle_status',COALESCE((SELECT e.to_status FROM public.service_case_lifecycle_event e WHERE e.service_case_id=c.case_id ORDER BY e.version DESC LIMIT 1),CASE WHEN s.schedule_id IS NULL THEN 'PLAN_PENDING' ELSE 'ACTIVE' END),'risk_flag',CASE WHEN EXISTS(SELECT 1 FROM (SELECT m.status,lag(m.status) OVER (ORDER BY m.window_start,m.code) AS previous_status FROM public.service_milestone m WHERE m.service_case_id=c.case_id) ordered_milestones WHERE ordered_milestones.status='MISSED' AND ordered_milestones.previous_status='MISSED') THEN 'AT_RISK' ELSE NULL END,'active_plan_id',s.active_plan_id,'cycle_anchor_at',s.cycle_anchor_at,'current_schedule_version',s.version_no,'milestones',COALESCE((SELECT jsonb_agg(jsonb_build_object('milestone_id',m.milestone_id,'service_case_id',m.service_case_id,'code',m.code,'window_start',m.window_start,'window_end',m.window_end,'status',m.status,'completed_at',m.completed_at,'record_summary',m.record_summary,'version',m.version) ORDER BY m.window_start,m.code) FROM public.service_milestone m WHERE m.schedule_id=s.schedule_id),'[]'::jsonb),'open_high_risk_count',(SELECT count(*) FROM public.high_risk_task h WHERE h.service_case_id=c.case_id AND h.status IN ('OPEN','CLAIMED','ESCALATED')),'closing_readiness',public.slice7_closing_readiness_v1(c.case_id),'version',c.version) INTO result FROM public.service_case c LEFT JOIN public.service_cycle_schedule s ON s.service_case_id=c.case_id AND s.is_current WHERE c.case_id=value_target; ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; RETURN result;",
        readers,
        declarations="result JSONB; authority JSONB;",
    )
    _function(
        "slice7_read_many_v1",
        "value_resource VARCHAR,value_scope UUID,value_actor BIGINT,value_role VARCHAR,value_cursor UUID,value_ceiling UUID,value_limit BIGINT,value_status VARCHAR,value_risk VARCHAR",
        "JSONB",
        f"IF session_user NOT IN {readers!r} THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value_limit<1 OR value_limit>100 OR (value_cursor IS NOT NULL AND value_ceiling IS NOT NULL AND value_cursor>value_ceiling) THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "IF value_risk IS NOT NULL AND (value_resource<>'FULFILLMENT' OR value_risk<>'AT_RISK') THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "IF (value_resource='MILESTONE' AND value_status IS NOT NULL AND value_status NOT IN ('PENDING','DUE','COMPLETED','MISSED','INVALIDATED')) OR (value_resource='TRANSFER' AND value_status IS NOT NULL AND value_status NOT IN ('REQUESTED_BY_USER','NEW_INSTITUTION_REVIEWING','ACCEPTED','OLD_INSTITUTION_CLOSING','USER_SCOPE_CONFIRMED','TRANSFERRED','REJECTED_BY_NEW_INSTITUTION','CANCELLED_BY_USER')) OR (value_resource='EXPORT' AND value_status IS NOT NULL AND value_status NOT IN ('REQUESTED','GENERATING','READY','DOWNLOADED','EXPIRED','FAILED','CANCELLED')) OR (value_resource='FULFILLMENT' AND value_status IS NOT NULL AND value_status NOT IN ('PLAN_PENDING','ACTIVE','PAUSED','CLOSING','COMPLETED','WITHDRAWN_BY_USER','TERMINATED_BY_INSTITUTION','TRANSFERRED','UNABLE_TO_CONTACT','SAFETY_TERMINATED')) THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "IF value_resource='MILESTONE' THEN "
        "IF public.slice7_authority_v1('READ_MILESTONE',value_scope,value_actor,value_role,NULL) IS NULL THEN RETURN jsonb_build_object('items','[]'::jsonb,'ceiling',NULL); END IF; "
        "WITH eligible AS MATERIALIZED (SELECT m.milestone_id,m.service_case_id,m.code,m.window_start,m.window_end,m.status,m.completed_at,m.record_summary,m.version FROM public.service_milestone m WHERE m.service_case_id=value_scope AND (value_status IS NULL OR m.status=value_status)), bounded AS (SELECT e.* FROM eligible e WHERE e.milestone_id<=COALESCE(value_ceiling,(SELECT x.milestone_id FROM eligible x ORDER BY x.milestone_id DESC LIMIT 1)) AND (value_cursor IS NULL OR e.milestone_id>value_cursor) ORDER BY e.milestone_id LIMIT value_limit) SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(bounded.*) ORDER BY bounded.milestone_id),'[]'::jsonb),'ceiling',COALESCE(value_ceiling,(SELECT x.milestone_id FROM eligible x ORDER BY x.milestone_id DESC LIMIT 1))) INTO result FROM bounded; "
        "ELSIF value_resource='TRANSFER' THEN "
        "WITH eligible AS MATERIALIZED (SELECT t.* FROM public.service_transfer_request t WHERE (value_status IS NULL OR t.status=value_status) AND public.slice7_authority_v1('READ_TRANSFER',t.transfer_id,value_actor,value_role,NULL) IS NOT NULL), bounded AS (SELECT e.* FROM eligible e WHERE e.transfer_id<=COALESCE(value_ceiling,(SELECT x.transfer_id FROM eligible x ORDER BY x.transfer_id DESC LIMIT 1)) AND (value_cursor IS NULL OR e.transfer_id>value_cursor) ORDER BY e.transfer_id LIMIT value_limit) SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(bounded.*) ORDER BY bounded.transfer_id),'[]'::jsonb),'ceiling',COALESCE(value_ceiling,(SELECT x.transfer_id FROM eligible x ORDER BY x.transfer_id DESC LIMIT 1))) INTO result FROM bounded; "
        "ELSIF value_resource='EXPORT' THEN "
        "WITH eligible AS MATERIALIZED (SELECT e.* FROM public.personal_data_export_request e WHERE (value_status IS NULL OR e.status=value_status) AND public.slice7_authority_v1('READ_EXPORT',e.export_id,value_actor,value_role,NULL) IS NOT NULL), bounded AS (SELECT e.* FROM eligible e WHERE e.export_id<=COALESCE(value_ceiling,(SELECT x.export_id FROM eligible x ORDER BY x.export_id DESC LIMIT 1)) AND (value_cursor IS NULL OR e.export_id>value_cursor) ORDER BY e.export_id LIMIT value_limit) SELECT jsonb_build_object('items',COALESCE(jsonb_agg(to_jsonb(bounded.*) ORDER BY bounded.export_id),'[]'::jsonb),'ceiling',COALESCE(value_ceiling,(SELECT x.export_id FROM eligible x ORDER BY x.export_id DESC LIMIT 1))) INTO result FROM bounded; "
        "ELSIF value_resource='FULFILLMENT' THEN "
        "WITH eligible AS MATERIALIZED (SELECT c.case_id,item.value FROM public.service_case c CROSS JOIN LATERAL (SELECT public.slice7_read_one_v1('FULFILLMENT',c.case_id,value_actor,value_role) AS value) item WHERE item.value IS NOT NULL AND (value_status IS NULL OR item.value->>'lifecycle_status'=value_status) AND (value_risk IS NULL OR item.value->>'risk_flag'=value_risk)), bounded AS (SELECT e.* FROM eligible e WHERE e.case_id<=COALESCE(value_ceiling,(SELECT x.case_id FROM eligible x ORDER BY x.case_id DESC LIMIT 1)) AND (value_cursor IS NULL OR e.case_id>value_cursor) ORDER BY e.case_id LIMIT value_limit) SELECT jsonb_build_object('items',COALESCE(jsonb_agg(bounded.value ORDER BY bounded.case_id),'[]'::jsonb),'ceiling',COALESCE(value_ceiling,(SELECT x.case_id FROM eligible x ORDER BY x.case_id DESC LIMIT 1))) INTO result FROM bounded; "
        "ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; RETURN result;",
        readers,
        declarations="result JSONB;",
    )
    _function(
        "slice7_worker_claim_v1",
        "value_kind VARCHAR,value_worker VARCHAR,value_limit BIGINT",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value_limit<1 OR value_limit>100 OR length(value_worker) NOT BETWEEN 1 AND 128 THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; IF value_kind='EXPORT' THEN WITH candidates AS (SELECT e.export_id FROM public.personal_data_export_request e WHERE (e.status='REQUESTED' OR (e.status='GENERATING' AND e.lease_until<clock_timestamp())) AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' WHERE u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL)) ORDER BY e.requested_at,e.export_id LIMIT value_limit FOR UPDATE SKIP LOCKED), changed AS (UPDATE public.personal_data_export_request e SET status='GENERATING',lease_owner=value_worker,lease_until=clock_timestamp()+interval '5 minutes',version=version+1 FROM candidates c WHERE e.export_id=c.export_id RETURNING e.*) SELECT COALESCE(jsonb_agg(to_jsonb(changed.*)),'[]'::jsonb) INTO result FROM changed; ELSIF value_kind='MILESTONE_OVERDUE' THEN SELECT COALESCE(jsonb_agg(to_jsonb(q.*)),'[]'::jsonb) INTO result FROM (SELECT m.* FROM public.service_milestone m WHERE m.status IN ('PENDING','DUE') AND m.window_end<CURRENT_DATE ORDER BY m.window_end,m.milestone_id LIMIT value_limit FOR UPDATE SKIP LOCKED) q; ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_claim_v1",
        "value_export UUID,value_worker VARCHAR",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF length(value_worker) NOT BETWEEN 1 AND 128 THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "UPDATE public.personal_data_export_request e SET status='GENERATING',lease_owner=value_worker,lease_until=clock_timestamp()+interval '5 minutes',version=e.version+1 WHERE e.export_id=value_export AND (e.status='REQUESTED' OR (e.status='GENERATING' AND e.lease_until<clock_timestamp())) AND EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' WHERE u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL)) RETURNING to_jsonb(e.*) INTO result; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_snapshot_v1",
        "value_export UUID",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT jsonb_build_object("
        "'export_id',e.export_id,'subject_member_id',e.subject_member_id,'requested_scope',e.requested_scope,"
        "'source_versions',jsonb_build_object("
        "'profile',COALESCE((SELECT p.version FROM public.health_profile p WHERE p.subject_member_id=e.subject_member_id),0),"
        "'report',COALESCE((SELECT max(d.version) FROM public.detection_report d WHERE d.subject_member_id=e.subject_member_id),0),"
        "'canonical_fact',COALESCE((SELECT max(f.id) FROM public.canonical_health_fact f WHERE f.subject_member_id=e.subject_member_id),0),"
        "'assessment',COALESCE((SELECT max(a.sequence_no) FROM public.health_assessment a WHERE a.subject_member_id=e.subject_member_id AND a.status='COMPLETED'),0),"
        "'approved_plan',COALESCE((SELECT max(p.version_no) FROM public.health_plan_version p WHERE p.subject_member_id=e.subject_member_id AND p.status IN ('ACTIVE','SUPERSEDED')),0),"
        "'milestone',COALESCE((SELECT max(m.version) FROM public.service_milestone m JOIN public.service_case c ON c.case_id=m.service_case_id WHERE c.subject_member_id=e.subject_member_id),0),"
        "'service_summary',COALESCE((SELECT max(s.version_no) FROM public.service_summary s JOIN public.service_case sc ON sc.case_id=s.service_case_id WHERE sc.subject_member_id=e.subject_member_id),0)),"
        "'profile_internal',CASE WHEN e.requested_scope ? 'PROFILE' THEN (SELECT jsonb_build_object('profile_revision_id',r.profile_revision_id,'tenant_public_id',r.tenant_public_id,'identity_source_version',r.identity_source_version,'snapshot_key_id',r.snapshot_key_id,'snapshot_ciphertext_hex',encode(r.snapshot_ciphertext,'hex')) FROM public.health_profile p JOIN public.health_profile_revision r ON r.profile_revision_id=p.current_revision_id WHERE p.subject_member_id=e.subject_member_id) END,"
        "'data',jsonb_strip_nulls(jsonb_build_object("
        "'PROFILE',CASE WHEN e.requested_scope ? 'PROFILE' THEN '{}'::jsonb END,"
        "'REPORT',CASE WHEN e.requested_scope ? 'REPORT' THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('report_id',d.report_id,'report_type',d.report_type,'measured_at',d.measured_at,'received_at',d.received_at,'status',d.report_status,'source_type',d.source_type,'schema_version',d.schema_version,'version',d.version,'report_data',d.report_data,'attachments',COALESCE((SELECT jsonb_agg(jsonb_build_object('file_id',x.private_file_id,'position',x.position) ORDER BY x.position) FROM public.detection_report_attachment x WHERE x.report_id=d.report_id),'[]'::jsonb)) ORDER BY d.measured_at,d.report_id) FROM public.detection_report d WHERE d.subject_member_id=e.subject_member_id AND d.report_id IS NOT NULL),'[]'::jsonb) END,"
        "'CANONICAL_FACT',CASE WHEN e.requested_scope ? 'CANONICAL_FACT' THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('fact_ref',f.fact_ref,'indicator_code',f.indicator_code,'numeric_value',f.numeric_value,'unit',f.unit,'measured_at',f.measured_at,'received_at',f.received_at,'source_type',f.source_type,'report_id',f.report_id,'correction_reason_code',f.correction_reason_code,'state',COALESCE((SELECT se.state FROM public.health_fact_status_event se WHERE se.fact_id=f.id ORDER BY se.event_no DESC LIMIT 1),'UNKNOWN')) ORDER BY f.measured_at,f.fact_ref) FROM public.canonical_health_fact f WHERE f.subject_member_id=e.subject_member_id AND f.fact_ref IS NOT NULL),'[]'::jsonb) END,"
        "'ASSESSMENT',CASE WHEN e.requested_scope ? 'ASSESSMENT' THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('assessment_id',a.assessment_id,'service_case_id',a.service_case_id,'sequence_no',a.sequence_no,'overall_risk',a.overall_risk,'completed_at',a.completed_at,'version',a.version,'modules',COALESCE((SELECT jsonb_agg(jsonb_build_object('module_code',mr.module_code,'risk_level',mr.risk_level,'reason_codes',mr.reason_codes,'message_codes',mr.message_codes) ORDER BY mr.module_code) FROM public.assessment_module_result mr WHERE mr.assessment_id=a.assessment_id),'[]'::jsonb)) ORDER BY a.sequence_no,a.assessment_id) FROM public.health_assessment a WHERE a.subject_member_id=e.subject_member_id AND a.status='COMPLETED'),'[]'::jsonb) END,"
        "'APPROVED_PLAN',CASE WHEN e.requested_scope ? 'APPROVED_PLAN' THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('plan_id',p.plan_id,'service_case_id',p.service_case_id,'version_no',p.version_no,'status',p.status,'content',p.content,'created_at',p.created_at,'updated_at',p.updated_at,'version',p.version) ORDER BY p.created_at,p.plan_id) FROM public.health_plan_version p WHERE p.subject_member_id=e.subject_member_id AND p.status IN ('ACTIVE','SUPERSEDED')),'[]'::jsonb) END,"
        "'MILESTONE',CASE WHEN e.requested_scope ? 'MILESTONE' THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('milestone_id',m.milestone_id,'service_case_id',m.service_case_id,'code',m.code,'window_start',m.window_start,'window_end',m.window_end,'status',m.status,'completed_at',m.completed_at,'record_summary',m.record_summary,'version',m.version) ORDER BY m.window_start,m.milestone_id) FROM public.service_milestone m JOIN public.service_case c ON c.case_id=m.service_case_id WHERE c.subject_member_id=e.subject_member_id),'[]'::jsonb) END,"
        "'SERVICE_SUMMARY',CASE WHEN e.requested_scope ? 'SERVICE_SUMMARY' THEN COALESCE((SELECT jsonb_agg(jsonb_build_object('summary_id',s.summary_id,'service_case_id',s.service_case_id,'assessment_id',s.assessment_id,'version_no',s.version_no,'content',s.content,'created_at',s.created_at,'version',s.version) ORDER BY s.created_at,s.summary_id) FROM public.service_summary s JOIN public.service_case sc ON sc.case_id=s.service_case_id WHERE sc.subject_member_id=e.subject_member_id),'[]'::jsonb) END))) INTO result FROM public.personal_data_export_request e JOIN public.\"user\" u ON u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL JOIN identity.member current_member ON current_member.member_id=e.subject_member_id AND current_member.status='created' WHERE e.export_id=value_export AND e.status='GENERATING' AND e.lease_owner IS NOT NULL AND e.lease_until>=clock_timestamp() AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL) FOR SHARE OF e,u,current_member; IF result IS NULL THEN RAISE EXCEPTION 'EXPORT_LEASE_NOT_CURRENT'; END IF; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_source_file_v1",
        "value_export UUID,value_file UUID",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT jsonb_build_object('file_id',f.file_id,'created_at',f.created_at,'size',f.actual_size,'sha256',f.actual_sha256,'mime_type',f.actual_mime_type) INTO result FROM public.personal_data_export_request e JOIN public.\"user\" u ON u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL JOIN identity.member current_member ON current_member.member_id=e.subject_member_id AND current_member.status='created' JOIN public.detection_report d ON d.subject_member_id=e.subject_member_id AND d.report_id IS NOT NULL JOIN public.detection_report_attachment a ON a.report_id=d.report_id JOIN public.private_file f ON f.file_id=a.private_file_id WHERE e.export_id=value_export AND e.status='GENERATING' AND e.requested_scope ? 'REPORT' AND a.private_file_id=value_file AND d.report_status='CLEAN' AND f.status='CLEAN' AND f.actual_size BETWEEN 1 AND 10485760 AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL) FOR SHARE OF e,u,current_member,d,a,f; IF result IS NULL THEN RAISE EXCEPTION 'EXPORT_SOURCE_FILE_FORBIDDEN'; END IF; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_private_file_register_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'sha256' !~ '^[0-9a-f]{64}$' OR (value->>'size')::bigint NOT BETWEEN 1 AND 10485760 OR (value->>'expires_at')::timestamptz<=(value->>'created_at')::timestamptz THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(value->>'export_id',17)); "
        "SELECT jsonb_build_object('file_id',f.file_id,'owner_user_id',f.owner_user_id,'size',f.actual_size,'sha256',f.actual_sha256,'mime_type',f.actual_mime_type,'status',f.status) INTO result FROM public.private_file f JOIN public.personal_data_export_request e ON e.export_id=(value->>'export_id')::uuid WHERE f.file_id=(value->>'file_id')::uuid AND f.owner_user_id=e.requested_by AND f.purpose='PERSONAL_DATA_EXPORT' AND f.actual_size=(value->>'size')::bigint AND f.actual_sha256=value->>'sha256' AND f.status='CLEAN'; IF result IS NOT NULL THEN RETURN result; END IF; "
        "INSERT INTO public.private_file(file_id,purpose,owner_user_id,declared_size,declared_mime_type,declared_sha256,actual_size,actual_mime_type,actual_sha256,object_key,status,bound_application_id,created_at,expires_at,scanned_at,bound_at,deleted_at) SELECT (value->>'file_id')::uuid,'PERSONAL_DATA_EXPORT',e.requested_by,(value->>'size')::bigint,'application/zip',value->>'sha256',(value->>'size')::bigint,'application/zip',value->>'sha256','slice1/'||to_char((value->>'created_at')::timestamptz AT TIME ZONE 'UTC','YYYY/MM')||'/'||(value->>'file_id'),'CLEAN',NULL,(value->>'created_at')::timestamptz,(value->>'expires_at')::timestamptz,(value->>'created_at')::timestamptz,NULL,NULL FROM public.personal_data_export_request e JOIN public.\"user\" u ON u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL JOIN identity.member current_member ON current_member.member_id=e.subject_member_id AND current_member.status='created' WHERE e.export_id=(value->>'export_id')::uuid AND e.status='GENERATING' AND e.lease_owner=value->>'worker_id' AND e.lease_until>=clock_timestamp() AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL) RETURNING jsonb_build_object('file_id',file_id,'owner_user_id',owner_user_id,'size',actual_size,'sha256',actual_sha256,'mime_type',actual_mime_type,'status',status) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'EXPORT_LEASE_NOT_CURRENT'; END IF; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_private_file_snapshot_v1",
        "value_export UUID,value_file UUID",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; SELECT jsonb_build_object('file_id',f.file_id,'owner_user_id',f.owner_user_id,'size',f.actual_size,'sha256',f.actual_sha256,'mime_type',f.actual_mime_type,'status',f.status) INTO result FROM public.private_file f JOIN public.personal_data_export_request e ON e.requested_by=f.owner_user_id WHERE e.export_id=value_export AND f.file_id=value_file AND f.purpose='PERSONAL_DATA_EXPORT' AND f.actual_mime_type='application/zip'; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_artifact_bind_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'artifact_digest' !~ '^[0-9a-f]{64}$' OR value->>'manifest_digest' !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'export_id',7)); "
        "SELECT to_jsonb(e.*) INTO result FROM public.personal_data_export_request e JOIN public.personal_data_export_artifact a ON a.export_id=e.export_id WHERE e.export_id=(value->>'export_id')::uuid AND a.private_file_id=(value->>'private_file_id')::uuid AND a.artifact_digest=decode(value->>'artifact_digest','hex') AND a.manifest_digest=decode(value->>'manifest_digest','hex'); IF result IS NOT NULL THEN RETURN result; END IF; "
        "PERFORM 1 FROM public.personal_data_export_request e JOIN public.\"user\" u ON u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL JOIN identity.member current_member ON current_member.member_id=e.subject_member_id AND current_member.status='created' JOIN public.private_file f ON f.file_id=(value->>'private_file_id')::uuid WHERE e.export_id=(value->>'export_id')::uuid AND e.status='GENERATING' AND f.owner_user_id=e.requested_by AND f.purpose='PERSONAL_DATA_EXPORT' AND f.declared_mime_type='application/zip' AND f.actual_mime_type='application/zip' AND f.status='CLEAN' AND f.actual_size=(value->>'artifact_size')::bigint AND f.actual_sha256=value->>'artifact_digest' AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL) FOR UPDATE OF e,u,current_member,f; IF NOT FOUND THEN RAISE EXCEPTION 'EXPORT_ARTIFACT_INVALID'; END IF; "
        "INSERT INTO public.personal_data_export_artifact(artifact_id,export_id,private_file_id,manifest_digest,artifact_digest,created_at) VALUES ((value->>'artifact_id')::uuid,(value->>'export_id')::uuid,(value->>'private_file_id')::uuid,decode(value->>'manifest_digest','hex'),decode(value->>'artifact_digest','hex'),(value->>'created_at')::timestamptz); UPDATE public.personal_data_export_request SET status='READY',ready_at=(value->>'created_at')::timestamptz,expires_at=(value->>'expires_at')::timestamptz,lease_owner=NULL,lease_until=NULL,version=version+1 WHERE export_id=(value->>'export_id')::uuid AND lease_owner=value->>'worker_id' RETURNING to_jsonb(personal_data_export_request.*) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'EXPORT_LEASE_NOT_CURRENT'; END IF; "
        "INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) SELECT (value->>'audit_id')::uuid,'EXPORT_READY',e.requested_by,'export_worker',e.export_id,decode(value->>'manifest_digest','hex'),(value->>'created_at')::timestamptz FROM public.personal_data_export_request e WHERE e.export_id=(value->>'export_id')::uuid; INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,(value->>'export_id')::uuid,'EXPORT_READY',jsonb_build_object('export_id',value->>'export_id'),decode(value->>'manifest_digest','hex'),'PENDING',0,NULL,NULL,(value->>'created_at')::timestamptz,NULL,1); RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_ready_confirm_v1",
        "value JSONB",
        "BOOLEAN",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'artifact_digest' !~ '^[0-9a-f]{64}$' OR value->>'manifest_digest' !~ '^[0-9a-f]{64}$' OR (value->>'artifact_size')::bigint NOT BETWEEN 1 AND 10485760 THEN RETURN false; END IF; "
        "SELECT EXISTS(SELECT 1 FROM public.personal_data_export_request e JOIN public.personal_data_export_artifact a ON a.export_id=e.export_id JOIN public.private_file f ON f.file_id=a.private_file_id JOIN public.service_fulfillment_audit au ON au.audit_id=(value->>'audit_id')::uuid JOIN public.service_fulfillment_outbox o ON o.event_id=(value->>'event_id')::uuid WHERE e.export_id=(value->>'export_id')::uuid AND e.status IN ('READY','DOWNLOADED','EXPIRED') AND e.ready_at=(value->>'created_at')::timestamptz AND e.expires_at=(value->>'expires_at')::timestamptz AND a.artifact_id=(value->>'artifact_id')::uuid AND a.private_file_id=(value->>'private_file_id')::uuid AND a.manifest_digest=decode(value->>'manifest_digest','hex') AND a.artifact_digest=decode(value->>'artifact_digest','hex') AND a.created_at=(value->>'created_at')::timestamptz AND f.purpose='PERSONAL_DATA_EXPORT' AND f.actual_size=(value->>'artifact_size')::bigint AND f.actual_mime_type='application/zip' AND f.actual_sha256=value->>'artifact_digest' AND f.status='CLEAN' AND au.action='EXPORT_READY' AND au.actor_role='export_worker' AND au.target_id=e.export_id AND au.evidence_digest=decode(value->>'manifest_digest','hex') AND au.occurred_at=(value->>'created_at')::timestamptz AND o.aggregate_ref=e.export_id AND o.event_type='EXPORT_READY' AND o.payload_json=jsonb_build_object('export_id',value->>'export_id') AND o.payload_digest=decode(value->>'manifest_digest','hex') AND o.created_at=(value->>'created_at')::timestamptz) INTO confirmed; RETURN confirmed;",
        (export,),
        declarations="confirmed BOOLEAN;",
    )
    _function(
        "slice7_export_download_consume_v1",
        "value JSONB",
        "BOOLEAN",
        f"IF session_user <> '{transfer}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT a.export_id INTO export_ref FROM public.personal_data_export_download_access a WHERE a.access_id=(value->>'access_id')::uuid AND a.private_file_id=(value->>'private_file_id')::uuid AND a.actor_user_id=(value->>'actor_user_id')::bigint; IF export_ref IS NULL THEN RETURN false; END IF; "
        "authority:=public.slice7_authority_v1('EXPORT_DOWNLOAD_ACCESS',export_ref,(value->>'actor_user_id')::bigint,'member',NULL); IF authority IS NULL OR authority->>'private_file_id'<>value->>'private_file_id' OR authority->>'export_status'<>'READY' THEN RETURN false; END IF; "
        "UPDATE public.personal_data_export_download_access SET consumed_at=(value->>'consumed_at')::timestamptz,version=version+1 WHERE access_id=(value->>'access_id')::uuid AND consumed_at IS NULL AND expires_at>=(value->>'consumed_at')::timestamptz; IF NOT FOUND THEN RETURN false; END IF; UPDATE public.personal_data_export_request SET status='DOWNLOADED',downloaded_at=(value->>'consumed_at')::timestamptz,version=version+1 WHERE export_id=export_ref AND status='READY'; IF NOT FOUND THEN RAISE EXCEPTION 'EXPORT_NOT_READY'; END IF; INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) VALUES ((value->>'access_id')::uuid,'EXPORT_DOWNLOADED',(value->>'actor_user_id')::bigint,'member',export_ref,decode(value->>'evidence_digest','hex'),(value->>'consumed_at')::timestamptz); RETURN true;",
        (transfer,),
        declarations="export_ref UUID; authority JSONB;",
    )
    _function(
        "slice7_export_download_confirm_v1",
        "value JSONB",
        "BOOLEAN",
        f"IF session_user <> '{transfer}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT EXISTS(SELECT 1 FROM public.personal_data_export_download_access a JOIN public.personal_data_export_request e ON e.export_id=a.export_id JOIN public.service_fulfillment_audit au ON au.audit_id=a.access_id AND au.action='EXPORT_DOWNLOADED' AND au.target_id=e.export_id WHERE a.access_id=(value->>'access_id')::uuid AND a.private_file_id=(value->>'private_file_id')::uuid AND a.actor_user_id=(value->>'actor_user_id')::bigint AND a.consumed_at=(value->>'consumed_at')::timestamptz AND e.status='DOWNLOADED' AND e.downloaded_at=(value->>'consumed_at')::timestamptz AND au.actor_user_id=(value->>'actor_user_id')::bigint AND au.evidence_digest=decode(value->>'evidence_digest','hex') AND au.occurred_at=(value->>'consumed_at')::timestamptz) INTO confirmed; RETURN confirmed;",
        (transfer,),
        declarations="confirmed BOOLEAN;",
    )
    _function(
        "slice7_export_fail_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'failure_code' NOT IN ('EXPORT_ARCHIVE_TOO_LARGE','EXPORT_PROFILE_UNAVAILABLE','EXPORT_SOURCE_INVALID','EXPORT_SNAPSHOT_INVALID') OR value->>'evidence_digest' !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "UPDATE public.personal_data_export_request SET status='FAILED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE export_id=(value->>'export_id')::uuid AND status='GENERATING' AND lease_owner=value->>'worker_id' RETURNING to_jsonb(personal_data_export_request.*) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'EXPORT_LEASE_NOT_CURRENT'; END IF; "
        "INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) SELECT (value->>'audit_id')::uuid,'EXPORT_FAILED',e.requested_by,'export_worker',e.export_id,decode(value->>'evidence_digest','hex'),(value->>'occurred_at')::timestamptz FROM public.personal_data_export_request e WHERE e.export_id=(value->>'export_id')::uuid; "
        "INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,(value->>'export_id')::uuid,'EXPORT_FAILED',jsonb_build_object('export_id',value->>'export_id','failure_code',value->>'failure_code'),decode(value->>'evidence_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_recover_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'evidence_digest' !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "SELECT e.export_id INTO export_ref FROM public.personal_data_export_request e JOIN public.\"user\" u ON u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' WHERE e.status='GENERATING' AND e.lease_until<(value->>'cutoff')::timestamptz AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL) ORDER BY e.lease_until,e.export_id LIMIT 1 FOR UPDATE OF e SKIP LOCKED; IF export_ref IS NULL THEN RETURN NULL; END IF; "
        "UPDATE public.personal_data_export_request SET status='REQUESTED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE export_id=export_ref RETURNING to_jsonb(personal_data_export_request.*) INTO result; "
        "INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) SELECT (value->>'audit_id')::uuid,'EXPORT_RECOVERY_REQUESTED',e.requested_by,'export_worker',e.export_id,decode(value->>'evidence_digest','hex'),(value->>'occurred_at')::timestamptz FROM public.personal_data_export_request e WHERE e.export_id=export_ref; "
        "INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,export_ref,'EXPORT_RETRY_REQUESTED',jsonb_build_object('export_id',export_ref),decode(value->>'evidence_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); RETURN result;",
        (export,),
        declarations="result JSONB; export_ref UUID;",
    )
    _function(
        "slice7_export_cleanup_claim_v1",
        "value_cutoff TIMESTAMP WITH TIME ZONE,value_limit BIGINT",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value_limit NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "WITH candidates AS (SELECT e.export_id,(e.status IN ('REQUESTED','GENERATING') AND e.requested_at<=value_cutoff-interval '24 hours' AND NOT EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' WHERE u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL))) AS stale_requester FROM public.personal_data_export_request e WHERE (e.status IN ('CANCELLED','FAILED') OR (e.status IN ('READY','DOWNLOADED') AND e.expires_at<=value_cutoff) OR (e.status IN ('REQUESTED','GENERATING') AND e.requested_at<=value_cutoff-interval '24 hours' AND NOT EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.member m ON m.member_id=e.subject_member_id AND m.status='created' WHERE u.id=e.requested_by AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND (EXISTS(SELECT 1 FROM identity.user_member_self_link l WHERE l.user_ref=u.id AND l.member_id=e.subject_member_id) OR public.slice7_proxy_major_current_v1(u.id,e.subject_member_id,'PERSONAL_DATA_EXPORT') IS NOT NULL)))) AND NOT EXISTS (SELECT 1 FROM public.service_fulfillment_audit a WHERE a.target_id=e.export_id AND a.action='EXPORT_FILE_CLEANED') ORDER BY COALESCE(e.expires_at,e.requested_at),e.export_id LIMIT value_limit FOR UPDATE OF e SKIP LOCKED), changed AS (UPDATE public.personal_data_export_request e SET status=CASE WHEN c.stale_requester THEN 'CANCELLED' WHEN e.status IN ('READY','DOWNLOADED') THEN 'EXPIRED' ELSE e.status END,lease_owner=NULL,lease_until=NULL,version=CASE WHEN c.stale_requester OR e.status IN ('READY','DOWNLOADED') THEN e.version+1 ELSE e.version END FROM candidates c WHERE e.export_id=c.export_id RETURNING e.export_id,e.status) SELECT COALESCE(jsonb_agg(jsonb_build_object('export_id',c.export_id,'file_id',f.file_id,'created_at',f.created_at) ORDER BY c.export_id),'[]'::jsonb) INTO result FROM changed c LEFT JOIN public.private_file f ON f.file_id=c.export_id AND f.purpose='PERSONAL_DATA_EXPORT' AND f.status<>'DELETED'; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_export_cleanup_complete_v1",
        "value JSONB",
        "BOOLEAN",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'evidence_digest' !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'export_id',29)); PERFORM 1 FROM public.personal_data_export_request e WHERE e.export_id=(value->>'export_id')::uuid AND (e.status IN ('EXPIRED','CANCELLED','FAILED') OR (e.status='DOWNLOADED' AND e.expires_at<=(value->>'deleted_at')::timestamptz)) FOR UPDATE; IF NOT FOUND THEN RETURN false; END IF; "
        "UPDATE public.private_file SET status='DELETED',deleted_at=(value->>'deleted_at')::timestamptz WHERE file_id=(value->>'export_id')::uuid AND purpose='PERSONAL_DATA_EXPORT' AND status<>'DELETED'; "
        "INSERT INTO public.service_fulfillment_audit(audit_id,action,actor_user_id,actor_role,target_id,evidence_digest,occurred_at) SELECT (value->>'audit_id')::uuid,'EXPORT_FILE_CLEANED',e.requested_by,'export_worker',e.export_id,decode(value->>'evidence_digest','hex'),(value->>'deleted_at')::timestamptz FROM public.personal_data_export_request e WHERE e.export_id=(value->>'export_id')::uuid; "
        "INSERT INTO public.service_fulfillment_outbox(event_id,aggregate_ref,event_type,payload_json,payload_digest,status,attempts,lease_owner,lease_until,created_at,delivered_at,version) VALUES ((value->>'event_id')::uuid,(value->>'export_id')::uuid,'EXPORT_FILE_CLEANED',jsonb_build_object('export_id',value->>'export_id'),decode(value->>'evidence_digest','hex'),'PENDING',0,NULL,NULL,(value->>'deleted_at')::timestamptz,NULL,1); RETURN true;",
        (export,),
        declarations="",
    )
    _function(
        "slice7_outbox_claim_v1",
        "value_event UUID,value_seconds BIGINT",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value_event IS NULL THEN SELECT event_id INTO value_event FROM public.service_fulfillment_outbox WHERE status IN ('PENDING','FAILED') ORDER BY created_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED; END IF; UPDATE public.service_fulfillment_outbox SET status='PROCESSING',attempts=attempts+1,lease_owner=session_user,lease_until=clock_timestamp()+make_interval(secs=>value_seconds::int),version=version+1 WHERE event_id=value_event AND status IN ('PENDING','FAILED') RETURNING to_jsonb(service_fulfillment_outbox.*) INTO result; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_outbox_consume_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT to_jsonb(o.*) INTO result FROM public.service_fulfillment_delivery d JOIN public.service_fulfillment_outbox o ON o.event_id=d.event_id WHERE d.event_id=(value->>'event_id')::uuid AND d.target_type=value->>'target_type' AND d.target_ref=(value->>'target_ref')::uuid FOR SHARE OF d,o; IF result IS NOT NULL THEN RETURN result; END IF; INSERT INTO public.service_fulfillment_delivery(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at) VALUES ((value->>'delivery_id')::uuid,(value->>'event_id')::uuid,value->>'target_type',(value->>'target_ref')::uuid,decode(value->>'target_digest','hex'),(value->>'delivered_at')::timestamptz); UPDATE public.service_fulfillment_outbox SET status='DELIVERED',delivered_at=(value->>'delivered_at')::timestamptz,lease_owner=NULL,lease_until=NULL,version=version+1 WHERE event_id=(value->>'event_id')::uuid AND status='PROCESSING' AND lease_owner=session_user RETURNING to_jsonb(service_fulfillment_outbox.*) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'OUTBOX_LEASE_NOT_CURRENT'; END IF; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    _function(
        "slice7_outbox_recover_v1",
        "value_cutoff TIMESTAMP WITH TIME ZONE",
        "JSONB",
        f"IF session_user <> '{export}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; WITH changed AS (UPDATE public.service_fulfillment_outbox SET status='FAILED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE status='PROCESSING' AND lease_until<value_cutoff RETURNING event_id) SELECT jsonb_build_object('recovered',count(*)) INTO result FROM changed; RETURN result;",
        (export,),
        declarations="result JSONB;",
    )
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")
    for role in all_runtime:
        op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')


def upgrade() -> None:
    roles = _roles()
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    op.drop_constraint("ck_private_file_mime_type", "private_file", schema="public", type_="check")
    op.create_check_constraint(
        "ck_private_file_mime_type",
        "private_file",
        "declared_mime_type IN ('application/pdf','image/jpeg','image/png','application/zip')",
        schema="public",
    )
    _create_tables()
    op.execute("DROP INDEX public.uq_service_case_active_subject")
    _create_functions(*roles)


def downgrade() -> None:
    roles = _roles()
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    union = " UNION ALL ".join(f"SELECT 1 FROM public.{table}" for table in _TABLES)
    nonempty = connection.execute(sa.text(f"SELECT EXISTS({union})")).scalar_one()
    if nonempty:
        raise RuntimeError("Slice 7 downgrade requires empty module tables") from None
    generated_exports = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM public.private_file "
            "WHERE purpose='PERSONAL_DATA_EXPORT' OR declared_mime_type='application/zip')"
        )
    ).scalar_one()
    if generated_exports:
        raise RuntimeError(
            "Slice 7 downgrade requires no generated export private files"
        ) from None
    op.execute("DROP TRIGGER trg_slice7_plan_activation_v1 ON public.health_plan_version")
    op.execute("DROP TRIGGER trg_slice7_service_case_current_guard_v1 ON public.service_case")
    op.execute("DROP TRIGGER trg_slice7_proxy_plan_decision_guard_v1 ON public.health_plan_user_decision")
    op.execute(
        "DROP FUNCTION identity.slice7_identity_claim_reuse_v2("
        "UUID,UUID,CHAR,VARCHAR,UUID,UUID,BIGINT,CHAR,BOOLEAN,TIMESTAMPTZ)"
    )
    for name, signature in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{name}({signature})")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="public")
    op.execute(
        "CREATE UNIQUE INDEX uq_service_case_active_subject "
        "ON public.service_case(subject_member_id) WHERE status='PREPARING'"
    )
    op.drop_constraint("ck_private_file_mime_type", "private_file", schema="public", type_="check")
    op.create_check_constraint(
        "ck_private_file_mime_type",
        "private_file",
        "declared_mime_type IN ('application/pdf','image/jpeg','image/png')",
        schema="public",
    )
    for role in roles[:6]:
        op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
