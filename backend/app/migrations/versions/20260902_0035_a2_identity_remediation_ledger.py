"""Add the A2.2 identity remediation ledger and closed ACL boundary.

Revision ID: 20260902_0035
Revises: 20260901_0034
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

revision = "20260902_0035"
down_revision = "20260901_0034"
branch_labels = None
depends_on = None

_WRITER_ROLE_ENV = "KG_A2_IDENTITY_REMEDIATION_WRITER_ROLE"
_WRITER_DATABASE_URL_ENV = "KG_A2_IDENTITY_REMEDIATION_WRITER_DATABASE_URL"
_CONFIRMATION_ROLE_ENV = "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_ROLE"
_CONFIRMATION_DATABASE_URL_ENV = "KG_A2_IDENTITY_REMEDIATION_CONFIRMATION_DATABASE_URL"
_WRITER_FUNCTION = (
    "identity.a2_identity_remediation_ledger_v1("
    "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,"
    "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
    "timestamp with time zone)"
)
_CONFIRMATION_FUNCTION = (
    "identity.a2_identity_remediation_confirm_v1("
    "varchar,uuid,uuid,bigint,varchar,smallint,bigint,varchar,varchar,varchar,"
    "bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid,"
    "timestamp with time zone,varchar,bigint,varchar)"
)
_REQUEST_DIGEST_FUNCTION = _WRITER_FUNCTION.replace(
    "a2_identity_remediation_ledger_v1", "_a2_identity_remediation_request_digest_v2"
)
_INPUT_VALID_FUNCTION = _WRITER_FUNCTION.replace(
    "a2_identity_remediation_ledger_v1", "_a2_identity_remediation_inputs_valid_v2"
)
_PART_FUNCTION = "identity._a2_identity_remediation_digest_part_v1(text)"
_BATCH_DIGEST_FUNCTION = (
    "identity._a2_identity_remediation_batch_row_digest_v1("
    "uuid,varchar,varchar,bigint,bigint,bigint,bigint,bigint,bigint,bigint,"
    "bigint,bigint,varchar,bigint,varchar,timestamp with time zone,"
    "timestamp with time zone,timestamp with time zone,timestamp with time zone)"
)
_ITEM_DIGEST_FUNCTION = (
    "identity._a2_identity_remediation_item_row_digest_v1("
    "uuid,uuid,bigint,varchar,smallint,varchar,varchar,varchar,varchar,"
    "varchar,integer,bigint,varchar,timestamp with time zone,timestamp with time zone)"
)
_INTERNAL_FUNCTIONS = (
    _REQUEST_DIGEST_FUNCTION,
    _INPUT_VALID_FUNCTION,
    _PART_FUNCTION,
    _BATCH_DIGEST_FUNCTION,
    _ITEM_DIGEST_FUNCTION,
)
_TABLES = (
    "identity.identity_remediation_batch",
    "identity.identity_remediation_item",
    "identity.identity_remediation_receipt",
    "identity.identity_remediation_audit",
)
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_ACTOR_PATTERN = r"^[a-z][a-z0-9_-]{0,63}$"


def _configuration_error() -> None:
    raise RuntimeError(
        "A2 identity remediation database role configuration is invalid"
    ) from None


def _closed_role(role_environment: str, url_environment: str) -> str:
    role = os.getenv(role_environment, "").strip()
    raw_url = os.getenv(url_environment, "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
        _configuration_error()
    try:
        url = make_url(raw_url)
    except Exception:
        _configuration_error()
    if (
        url.drivername != "postgresql+asyncpg"
        or url.username != role
        or not url.password
        or not url.database
    ):
        _configuration_error()
    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    if current_user == role:
        _configuration_error()
    row = connection.execute(
        sa.text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    if row is None or row["rolcanlogin"] is not True:
        _configuration_error()
    if any(
        row[name]
        for name in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolinherit",
            "rolreplication",
            "rolbypassrls",
        )
    ):
        _configuration_error()
    has_membership = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles member_role ON member_role.oid=membership.member "
            "JOIN pg_roles granted_role ON granted_role.oid=membership.roleid "
            "WHERE member_role.rolname=:role OR granted_role.rolname=:role)"
        ),
        {"role": role},
    ).scalar_one()
    if has_membership:
        _configuration_error()
    return role


def _create_tables() -> None:
    op.create_table(
        "identity_remediation_batch",
        sa.Column("batch_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("classification_rule_hash", sa.String(64), nullable=False),
        sa.Column("snapshot_ref_hash", sa.String(64), nullable=False),
        sa.Column("input_count", sa.BigInteger(), nullable=False),
        *(sa.Column(f"h{index}_count", sa.BigInteger(), nullable=False) for index in range(8)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("initiated_by_public_ref", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("batch_ref", name="pk_identity_remediation_batch"),
        sa.UniqueConstraint(
            "snapshot_ref_hash", name="uq_identity_remediation_batch_snapshot"
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED','RUNNING','PAUSED','COMPLETED','FAILED')",
            name="ck_identity_remediation_batch_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_identity_remediation_batch_version"),
        sa.CheckConstraint(
            "input_count >= 0 AND h0_count >= 0 AND h1_count >= 0 "
            "AND h2_count >= 0 AND h3_count >= 0 AND h4_count >= 0 "
            "AND h5_count >= 0 AND h6_count >= 0 AND h7_count >= 0 "
            "AND input_count=h0_count+h1_count+h2_count+h3_count+"
            "h4_count+h5_count+h6_count+h7_count",
            name="ck_identity_remediation_batch_counts",
        ),
        sa.CheckConstraint(
            "classification_rule_hash ~ '^[0-9a-f]{64}$' "
            "AND snapshot_ref_hash ~ '^[0-9a-f]{64}$' "
            "AND state_digest ~ '^[0-9a-f]{64}$'",
            name="ck_identity_remediation_batch_digests",
        ),
        schema="identity",
    )
    op.create_index(
        "uq_identity_remediation_single_running_batch",
        "identity_remediation_batch",
        ["status"],
        unique=True,
        schema="identity",
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_table(
        "identity_remediation_item",
        sa.Column("item_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subject_user_ref", sa.BigInteger(), nullable=False),
        sa.Column("primary_class", sa.String(2), nullable=False),
        sa.Column("secondary_flags", sa.SmallInteger(), nullable=False),
        sa.Column("preimage_digest", sa.String(64), nullable=False),
        sa.Column("eligibility_action_gate_digest", sa.String(64), nullable=True),
        sa.Column("mutation_digest", sa.String(64), nullable=True),
        sa.Column("postimage_digest", sa.String(64), nullable=True),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("last_reason_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("item_ref", name="pk_identity_remediation_item"),
        sa.ForeignKeyConstraint(
            ["batch_ref"],
            ["identity.identity_remediation_batch.batch_ref"],
            name="fk_identity_remediation_item_batch",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "batch_ref", "subject_user_ref", name="uq_identity_remediation_item_subject"
        ),
        sa.CheckConstraint(
            "primary_class IN ('H0','H1','H2','H3','H4','H5','H6','H7')",
            name="ck_identity_remediation_item_class",
        ),
        sa.CheckConstraint(
            "secondary_flags BETWEEN 0 AND 15",
            name="ck_identity_remediation_item_secondary_flags",
        ),
        sa.CheckConstraint(
            "status IN ('DISCOVERED','READY','PROCESSING','REMEDIATED',"
            "'REMEDIATION_REQUIRED','FAILED_TERMINAL','EXCLUDED')",
            name="ck_identity_remediation_item_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_identity_remediation_item_attempt"),
        sa.CheckConstraint("version >= 1", name="ck_identity_remediation_item_version"),
        sa.CheckConstraint(
            "preimage_digest ~ '^[0-9a-f]{64}$' "
            "AND (eligibility_action_gate_digest IS NULL OR "
            "eligibility_action_gate_digest ~ '^[0-9a-f]{64}$') "
            "AND (mutation_digest IS NULL OR mutation_digest ~ '^[0-9a-f]{64}$') "
            "AND (postimage_digest IS NULL OR postimage_digest ~ '^[0-9a-f]{64}$') "
            "AND state_digest ~ '^[0-9a-f]{64}$'",
            name="ck_identity_remediation_digest_lengths",
        ),
        schema="identity",
    )
    op.create_table(
        "identity_remediation_audit",
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_public_ref", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("batch_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_type", sa.String(8), nullable=False),
        sa.Column("target_state", sa.String(24), nullable=False),
        sa.Column("target_version", sa.BigInteger(), nullable=False),
        sa.Column("result_code", sa.String(32), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("ledger_row_digest", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("audit_id", name="pk_identity_remediation_audit"),
        sa.ForeignKeyConstraint(
            ["batch_ref"],
            ["identity.identity_remediation_batch.batch_ref"],
            name="fk_identity_remediation_audit_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["item_ref"],
            ["identity.identity_remediation_item.item_ref"],
            name="fk_identity_remediation_audit_item",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$' "
            "AND ledger_row_digest ~ '^[0-9a-f]{64}$'",
            name="ck_identity_remediation_audit_digests",
        ),
        schema="identity",
    )
    op.create_table(
        "identity_remediation_receipt",
        sa.Column("receipt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_scope", sa.String(64), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(8), nullable=False),
        sa.Column("target_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_state", sa.String(24), nullable=False),
        sa.Column("target_version", sa.BigInteger(), nullable=False),
        sa.Column("result_code", sa.String(32), nullable=False),
        sa.Column("ledger_row_digest", sa.String(64), nullable=False),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_identity_remediation_receipt"),
        sa.ForeignKeyConstraint(
            ["audit_id"],
            ["identity.identity_remediation_audit.audit_id"],
            name="fk_identity_remediation_receipt_audit",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "actor_scope",
            "operation",
            "idempotency_key_digest",
            name="uq_identity_remediation_receipt_idempotency",
        ),
        sa.CheckConstraint(
            "idempotency_key_digest ~ '^[0-9a-f]{64}$' "
            "AND request_digest ~ '^[0-9a-f]{64}$' "
            "AND ledger_row_digest ~ '^[0-9a-f]{64}$'",
            name="ck_identity_remediation_receipt_digests",
        ),
        schema="identity",
    )


def _typed_parameters() -> str:
    return """
      value_operation VARCHAR,
      value_batch_ref UUID,
      value_item_ref UUID,
      value_subject_user_ref BIGINT,
      value_primary_class VARCHAR,
      value_secondary_flags SMALLINT,
      value_expected_version BIGINT,
      value_expected_target_state_digest VARCHAR,
      value_classification_rule_hash VARCHAR,
      value_snapshot_ref_hash VARCHAR,
      value_input_count BIGINT,
      value_h0_count BIGINT,
      value_h1_count BIGINT,
      value_h2_count BIGINT,
      value_h3_count BIGINT,
      value_h4_count BIGINT,
      value_h5_count BIGINT,
      value_h6_count BIGINT,
      value_h7_count BIGINT,
      value_preimage_digest VARCHAR,
      value_eligibility_action_gate_digest VARCHAR,
      value_mutation_digest VARCHAR,
      value_business_postimage_digest VARCHAR,
      value_actor_scope VARCHAR,
      value_reason_code VARCHAR,
      value_idempotency_key_digest VARCHAR,
      value_receipt_id UUID,
      value_audit_id UUID,
      value_occurred_at TIMESTAMP WITH TIME ZONE
    """


def _typed_arguments() -> str:
    return """
      value_operation,value_batch_ref,value_item_ref,value_subject_user_ref,
      value_primary_class,value_secondary_flags,value_expected_version,
      value_expected_target_state_digest,value_classification_rule_hash,
      value_snapshot_ref_hash,value_input_count,value_h0_count,value_h1_count,
      value_h2_count,value_h3_count,value_h4_count,value_h5_count,value_h6_count,
      value_h7_count,value_preimage_digest,value_eligibility_action_gate_digest,
      value_mutation_digest,value_business_postimage_digest,value_actor_scope,
      value_reason_code,value_idempotency_key_digest,value_receipt_id,value_audit_id,
      value_occurred_at
    """


def _create_internal_functions() -> None:
    parameters = _typed_parameters()
    op.execute(
        """
        CREATE FUNCTION identity._a2_identity_remediation_digest_part_v1(value TEXT)
        RETURNS TEXT LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp
        AS $$ SELECT CASE WHEN value IS NULL THEN 'N;'
          ELSE 'V'||octet_length(value)::TEXT||':'||value||';' END $$
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION identity._a2_identity_remediation_inputs_valid_v2({parameters})
        RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp AS $fn$
        BEGIN
          IF value_operation NOT IN (
              'PLAN_BATCH','START_BATCH','PAUSE_BATCH','RESUME_BATCH','COMPLETE_BATCH',
              'FAIL_BATCH','REGISTER_ITEM','MARK_ITEM_READY','START_ITEM','RECOVER_ITEM',
              'COMPLETE_ITEM','REQUIRE_ITEM','FAIL_ITEM','EXCLUDE_ITEM')
            OR value_batch_ref IS NULL
            OR value_actor_scope !~ '^[a-z][a-z0-9_-]{{0,63}}$'
            OR value_idempotency_key_digest !~ '^[0-9a-f]{{64}}$'
            OR value_receipt_id IS NULL OR value_audit_id IS NULL
            OR value_occurred_at IS NULL
          THEN RETURN FALSE; END IF;
          IF value_operation='PLAN_BATCH' THEN
            RETURN value_item_ref IS NULL AND value_subject_user_ref IS NULL
              AND value_primary_class IS NULL AND value_secondary_flags IS NULL
              AND value_expected_version IS NULL
              AND value_expected_target_state_digest IS NULL
              AND value_classification_rule_hash ~ '^[0-9a-f]{{64}}$'
              AND value_snapshot_ref_hash ~ '^[0-9a-f]{{64}}$'
              AND value_input_count>=0 AND value_h0_count>=0 AND value_h1_count>=0
              AND value_h2_count>=0 AND value_h3_count>=0 AND value_h4_count>=0
              AND value_h5_count>=0 AND value_h6_count>=0 AND value_h7_count>=0
              AND value_input_count=value_h0_count+value_h1_count+value_h2_count+
                value_h3_count+value_h4_count+value_h5_count+value_h6_count+value_h7_count
              AND value_preimage_digest IS NULL
              AND value_eligibility_action_gate_digest IS NULL
              AND value_mutation_digest IS NULL
              AND value_business_postimage_digest IS NULL
              AND value_reason_code='A2_BATCH_PLANNED';
          ELSIF value_operation='REGISTER_ITEM' THEN
            RETURN value_item_ref IS NOT NULL AND value_subject_user_ref IS NOT NULL
              AND value_subject_user_ref>0
              AND value_primary_class IN ('H0','H1','H2','H3','H4','H5','H6','H7')
              AND value_secondary_flags BETWEEN 0 AND 15
              AND value_expected_version IS NULL
              AND value_expected_target_state_digest IS NULL
              AND value_classification_rule_hash IS NULL AND value_snapshot_ref_hash IS NULL
              AND value_input_count IS NULL AND value_h0_count IS NULL AND value_h1_count IS NULL
              AND value_h2_count IS NULL AND value_h3_count IS NULL AND value_h4_count IS NULL
              AND value_h5_count IS NULL AND value_h6_count IS NULL AND value_h7_count IS NULL
              AND value_preimage_digest ~ '^[0-9a-f]{{64}}$'
              AND value_eligibility_action_gate_digest IS NULL
              AND value_mutation_digest IS NULL AND value_business_postimage_digest IS NULL
              AND value_reason_code='A2_ITEM_DISCOVERED';
          ELSE
            IF value_expected_version IS NULL OR value_expected_version<1
              OR value_expected_target_state_digest !~ '^[0-9a-f]{{64}}$'
              OR value_subject_user_ref IS NOT NULL OR value_primary_class IS NOT NULL
              OR value_secondary_flags IS NOT NULL OR value_classification_rule_hash IS NOT NULL
              OR value_snapshot_ref_hash IS NOT NULL OR value_input_count IS NOT NULL
              OR value_h0_count IS NOT NULL OR value_h1_count IS NOT NULL
              OR value_h2_count IS NOT NULL OR value_h3_count IS NOT NULL
              OR value_h4_count IS NOT NULL OR value_h5_count IS NOT NULL
              OR value_h6_count IS NOT NULL OR value_h7_count IS NOT NULL
              OR value_preimage_digest IS NOT NULL
            THEN RETURN FALSE; END IF;
            IF value_operation LIKE '%_BATCH' THEN
              RETURN value_item_ref IS NULL
                AND value_eligibility_action_gate_digest IS NULL
                AND value_mutation_digest IS NULL AND value_business_postimage_digest IS NULL
                AND ((value_operation='FAIL_BATCH' AND value_reason_code='A2_TERMINAL_FAILURE')
                  OR (value_operation<>'FAIL_BATCH' AND value_reason_code='A2_BATCH_CONTROLLED'));
            END IF;
            IF value_item_ref IS NULL THEN RETURN FALSE; END IF;
            IF value_operation='MARK_ITEM_READY' THEN
              RETURN value_eligibility_action_gate_digest ~ '^[0-9a-f]{{64}}$'
                AND value_mutation_digest IS NULL AND value_business_postimage_digest IS NULL
                AND value_reason_code IN ('A2_CANONICAL_MATCH_APPROVED','A2_TOKEN_WINDOW_PROVED');
            ELSIF value_operation='COMPLETE_ITEM' THEN
              RETURN value_eligibility_action_gate_digest IS NULL
                AND value_mutation_digest ~ '^[0-9a-f]{{64}}$'
                AND value_business_postimage_digest ~ '^[0-9a-f]{{64}}$'
                AND value_reason_code='A2_APPROVED_REMEDIATION';
            END IF;
            RETURN value_eligibility_action_gate_digest IS NULL
              AND value_mutation_digest IS NULL AND value_business_postimage_digest IS NULL
              AND ((value_operation='RECOVER_ITEM' AND value_reason_code='A2_CONTROLLED_RECOVERY')
                OR (value_operation='REQUIRE_ITEM' AND value_reason_code='A2_HUMAN_REVIEW_REQUIRED')
                OR (value_operation='FAIL_ITEM' AND value_reason_code='A2_TERMINAL_FAILURE')
                OR (value_operation='EXCLUDE_ITEM' AND value_reason_code='A2_EXCLUDED')
                OR (value_operation='START_ITEM' AND value_reason_code='A2_APPROVED_REMEDIATION'));
          END IF;
        END $fn$
        """
    )
    request_parts = "||".join(
        f"identity._a2_identity_remediation_digest_part_v1({expression})"
        for expression in (
            "value_operation::TEXT",
            "value_batch_ref::TEXT",
            "value_item_ref::TEXT",
            "value_subject_user_ref::TEXT",
            "value_primary_class::TEXT",
            "value_secondary_flags::TEXT",
            "value_expected_version::TEXT",
            "value_expected_target_state_digest::TEXT",
            "value_classification_rule_hash::TEXT",
            "value_snapshot_ref_hash::TEXT",
            "value_input_count::TEXT",
            "value_h0_count::TEXT",
            "value_h1_count::TEXT",
            "value_h2_count::TEXT",
            "value_h3_count::TEXT",
            "value_h4_count::TEXT",
            "value_h5_count::TEXT",
            "value_h6_count::TEXT",
            "value_h7_count::TEXT",
            "value_preimage_digest::TEXT",
            "value_eligibility_action_gate_digest::TEXT",
            "value_mutation_digest::TEXT",
            "value_business_postimage_digest::TEXT",
            "value_actor_scope::TEXT",
            "value_reason_code::TEXT",
            "value_idempotency_key_digest::TEXT",
            "value_receipt_id::TEXT",
            "value_audit_id::TEXT",
            "to_char(value_occurred_at AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')",
        )
    )
    op.execute(
        f"""
        CREATE FUNCTION identity._a2_identity_remediation_request_digest_v2({parameters})
        RETURNS VARCHAR LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp AS $fn$
          SELECT encode(sha256(convert_to({request_parts},'UTF8')),'hex')
        $fn$
        """
    )
    op.execute(
        """
        CREATE FUNCTION identity._a2_identity_remediation_batch_row_digest_v1(
          value_batch_ref UUID,value_classification_rule_hash VARCHAR,value_snapshot_ref_hash VARCHAR,
          value_input_count BIGINT,value_h0_count BIGINT,value_h1_count BIGINT,value_h2_count BIGINT,
          value_h3_count BIGINT,value_h4_count BIGINT,value_h5_count BIGINT,value_h6_count BIGINT,
          value_h7_count BIGINT,value_status VARCHAR,value_version BIGINT,value_actor VARCHAR,
          value_created TIMESTAMPTZ,value_updated TIMESTAMPTZ,value_started TIMESTAMPTZ,
          value_completed TIMESTAMPTZ) RETURNS VARCHAR LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp AS $fn$
          SELECT encode(sha256(convert_to(
            identity._a2_identity_remediation_digest_part_v1(value_batch_ref::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_classification_rule_hash)||
            identity._a2_identity_remediation_digest_part_v1(value_snapshot_ref_hash)||
            identity._a2_identity_remediation_digest_part_v1(value_input_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h0_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h1_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h2_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h3_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h4_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h5_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h6_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_h7_count::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_status)||
            identity._a2_identity_remediation_digest_part_v1(value_version::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_actor)||
            identity._a2_identity_remediation_digest_part_v1(to_char(value_created AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))||
            identity._a2_identity_remediation_digest_part_v1(to_char(value_updated AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))||
            identity._a2_identity_remediation_digest_part_v1(to_char(value_started AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))||
            identity._a2_identity_remediation_digest_part_v1(to_char(value_completed AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')),
            'UTF8')),'hex')
        $fn$
        """
    )
    op.execute(
        """
        CREATE FUNCTION identity._a2_identity_remediation_item_row_digest_v1(
          value_item_ref UUID,value_batch_ref UUID,value_subject BIGINT,value_class VARCHAR,
          value_flags SMALLINT,value_preimage VARCHAR,value_eligibility VARCHAR,value_mutation VARCHAR,
          value_postimage VARCHAR,value_status VARCHAR,value_attempt INTEGER,value_version BIGINT,
          value_reason VARCHAR,value_created TIMESTAMPTZ,value_updated TIMESTAMPTZ)
        RETURNS VARCHAR LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog, pg_temp AS $fn$
          SELECT encode(sha256(convert_to(
            identity._a2_identity_remediation_digest_part_v1(value_item_ref::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_batch_ref::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_subject::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_class)||
            identity._a2_identity_remediation_digest_part_v1(value_flags::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_preimage)||
            identity._a2_identity_remediation_digest_part_v1(value_eligibility)||
            identity._a2_identity_remediation_digest_part_v1(value_mutation)||
            identity._a2_identity_remediation_digest_part_v1(value_postimage)||
            identity._a2_identity_remediation_digest_part_v1(value_status)||
            identity._a2_identity_remediation_digest_part_v1(value_attempt::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_version::TEXT)||
            identity._a2_identity_remediation_digest_part_v1(value_reason)||
            identity._a2_identity_remediation_digest_part_v1(to_char(value_created AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))||
            identity._a2_identity_remediation_digest_part_v1(to_char(value_updated AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')),
            'UTF8')),'hex')
        $fn$
        """
    )


def _create_writer_function(writer_role: str) -> None:
    parameters = _typed_parameters()
    arguments = _typed_arguments()
    op.execute(
        f"""
        CREATE FUNCTION identity.a2_identity_remediation_ledger_v1({parameters})
        RETURNS TABLE(target_type VARCHAR,target_ref UUID,state VARCHAR,version BIGINT,
          result_code VARCHAR,receipt_id UUID,state_digest VARCHAR)
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER PARALLEL UNSAFE
        SET search_path = pg_catalog, pg_temp AS $fn$
        DECLARE
          stored_receipt identity.identity_remediation_receipt%ROWTYPE;
          batch identity.identity_remediation_batch%ROWTYPE;
          item identity.identity_remediation_item%ROWTYPE;
          internal_request_digest VARCHAR;
          current_row_digest VARCHAR;
          next_state VARCHAR;
          next_version BIGINT;
          next_attempt INTEGER;
          manifest_count BIGINT;
          mh0 BIGINT; mh1 BIGINT; mh2 BIGINT; mh3 BIGINT;
          mh4 BIGINT; mh5 BIGINT; mh6 BIGINT; mh7 BIGINT;
          terminal_mismatch_count BIGINT;
          failed_terminal_count BIGINT;
        BEGIN
          IF session_user <> '{writer_role}' THEN RAISE EXCEPTION 'A2_REMEDIATION_WRITER_FORBIDDEN'; END IF;
          IF NOT identity._a2_identity_remediation_inputs_valid_v2({arguments}) THEN
            RAISE EXCEPTION 'A2_REMEDIATION_INPUT_INVALID';
          END IF;
          internal_request_digest:=identity._a2_identity_remediation_request_digest_v2({arguments});
          PERFORM pg_advisory_xact_lock(hashtextextended(
            value_actor_scope || ':' || value_operation || ':' || value_idempotency_key_digest,22001));
          SELECT * INTO stored_receipt FROM identity.identity_remediation_receipt receipt
          WHERE receipt.actor_scope=value_actor_scope AND receipt.operation=value_operation
            AND receipt.idempotency_key_digest=value_idempotency_key_digest;
          IF FOUND THEN
            IF stored_receipt.request_digest<>internal_request_digest THEN
              RAISE EXCEPTION 'A2_REMEDIATION_IDEMPOTENCY_CONFLICT';
            END IF;
            RETURN QUERY SELECT stored_receipt.target_type,stored_receipt.target_ref,
              stored_receipt.target_state,stored_receipt.target_version,stored_receipt.result_code,
              stored_receipt.receipt_id,stored_receipt.ledger_row_digest;
            RETURN;
          END IF;

          IF value_operation='PLAN_BATCH' THEN
            PERFORM pg_advisory_xact_lock(hashtextextended(value_snapshot_ref_hash,22002));
            PERFORM 1 FROM identity.identity_remediation_batch existing_batch
              WHERE existing_batch.snapshot_ref_hash=value_snapshot_ref_hash;
            IF FOUND THEN RAISE EXCEPTION 'A2_REMEDIATION_SNAPSHOT_CONFLICT'; END IF;
            INSERT INTO identity.identity_remediation_batch(
              batch_ref,classification_rule_hash,snapshot_ref_hash,input_count,
              h0_count,h1_count,h2_count,h3_count,h4_count,h5_count,h6_count,h7_count,
              status,version,state_digest,initiated_by_public_ref,created_at,updated_at)
            VALUES(value_batch_ref,value_classification_rule_hash,value_snapshot_ref_hash,value_input_count,
              value_h0_count,value_h1_count,value_h2_count,value_h3_count,value_h4_count,
              value_h5_count,value_h6_count,value_h7_count,'PLANNED',1,repeat('0',64),
              value_actor_scope,value_occurred_at,value_occurred_at);
            SELECT * INTO batch FROM identity.identity_remediation_batch target
              WHERE target.batch_ref=value_batch_ref;
            current_row_digest:=identity._a2_identity_remediation_batch_row_digest_v1(
              batch.batch_ref,batch.classification_rule_hash,batch.snapshot_ref_hash,batch.input_count,
              batch.h0_count,batch.h1_count,batch.h2_count,batch.h3_count,batch.h4_count,
              batch.h5_count,batch.h6_count,batch.h7_count,batch.status,batch.version,
              batch.initiated_by_public_ref,batch.created_at,batch.updated_at,batch.started_at,batch.completed_at);
            UPDATE identity.identity_remediation_batch target SET state_digest=current_row_digest
              WHERE target.batch_ref=value_batch_ref;
            target_type:='BATCH'; target_ref:=value_batch_ref; next_state:='PLANNED';
            next_version:=1; result_code:='PLANNED';
          ELSIF value_operation='REGISTER_ITEM' THEN
            SELECT * INTO batch FROM identity.identity_remediation_batch target
              WHERE target.batch_ref=value_batch_ref FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'A2_REMEDIATION_TARGET_NOT_FOUND'; END IF;
            IF batch.status<>'PLANNED' THEN RAISE EXCEPTION 'A2_REMEDIATION_ILLEGAL_TRANSITION'; END IF;
            INSERT INTO identity.identity_remediation_item(
              item_ref,batch_ref,subject_user_ref,primary_class,secondary_flags,preimage_digest,
              eligibility_action_gate_digest,mutation_digest,postimage_digest,state_digest,status,
              attempt_count,version,last_reason_code,created_at,updated_at)
            VALUES(value_item_ref,value_batch_ref,value_subject_user_ref,value_primary_class,
              value_secondary_flags,value_preimage_digest,NULL,NULL,NULL,repeat('0',64),'DISCOVERED',
              0,1,value_reason_code,value_occurred_at,value_occurred_at);
            SELECT * INTO item FROM identity.identity_remediation_item target
              WHERE target.item_ref=value_item_ref;
            current_row_digest:=identity._a2_identity_remediation_item_row_digest_v1(
              item.item_ref,item.batch_ref,item.subject_user_ref,item.primary_class,item.secondary_flags,
              item.preimage_digest,item.eligibility_action_gate_digest,item.mutation_digest,
              item.postimage_digest,item.status,item.attempt_count,item.version,item.last_reason_code,
              item.created_at,item.updated_at);
            UPDATE identity.identity_remediation_item target SET state_digest=current_row_digest
              WHERE target.item_ref=value_item_ref;
            target_type:='ITEM'; target_ref:=value_item_ref; next_state:='DISCOVERED';
            next_version:=1; result_code:='DISCOVERED';
          ELSIF value_operation LIKE '%_BATCH' THEN
            SELECT * INTO batch FROM identity.identity_remediation_batch target
              WHERE target.batch_ref=value_batch_ref FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'A2_REMEDIATION_TARGET_NOT_FOUND'; END IF;
            IF batch.version<>value_expected_version THEN RAISE EXCEPTION 'A2_REMEDIATION_STALE_VERSION'; END IF;
            IF batch.state_digest<>value_expected_target_state_digest THEN
              RAISE EXCEPTION 'A2_REMEDIATION_STALE_PREIMAGE';
            END IF;
            IF value_operation IN ('START_BATCH','COMPLETE_BATCH') THEN
              SELECT count(*),count(*) FILTER(WHERE primary_class='H0'),
                count(*) FILTER(WHERE primary_class='H1'),count(*) FILTER(WHERE primary_class='H2'),
                count(*) FILTER(WHERE primary_class='H3'),count(*) FILTER(WHERE primary_class='H4'),
                count(*) FILTER(WHERE primary_class='H5'),count(*) FILTER(WHERE primary_class='H6'),
                count(*) FILTER(WHERE primary_class='H7')
              INTO manifest_count,mh0,mh1,mh2,mh3,mh4,mh5,mh6,mh7
              FROM identity.identity_remediation_item target WHERE target.batch_ref=value_batch_ref;
              IF manifest_count<>batch.input_count OR mh0<>batch.h0_count OR mh1<>batch.h1_count
                OR mh2<>batch.h2_count OR mh3<>batch.h3_count OR mh4<>batch.h4_count
                OR mh5<>batch.h5_count OR mh6<>batch.h6_count OR mh7<>batch.h7_count
              THEN RAISE EXCEPTION 'A2_REMEDIATION_MANIFEST_MISMATCH'; END IF;
            END IF;
            IF batch.status='PLANNED' AND value_operation='START_BATCH' THEN next_state:='RUNNING';
            ELSIF batch.status='RUNNING' AND value_operation='PAUSE_BATCH' THEN next_state:='PAUSED';
            ELSIF batch.status='PAUSED' AND value_operation='RESUME_BATCH' THEN next_state:='RUNNING';
            ELSIF batch.status='RUNNING' AND value_operation='COMPLETE_BATCH' THEN
              SELECT count(*) FILTER(WHERE status='FAILED_TERMINAL'),
                count(*) FILTER(WHERE NOT (
                  (primary_class IN ('H0','H7') AND status='EXCLUDED') OR
                  (primary_class IN ('H1','H2','H6') AND status='REMEDIATION_REQUIRED') OR
                  (primary_class IN ('H3','H4','H5') AND status IN ('REMEDIATED','REMEDIATION_REQUIRED'))))
              INTO failed_terminal_count,terminal_mismatch_count
              FROM identity.identity_remediation_item target WHERE target.batch_ref=value_batch_ref;
              IF failed_terminal_count<>0 OR terminal_mismatch_count<>0 THEN
                RAISE EXCEPTION 'A2_REMEDIATION_MANIFEST_MISMATCH';
              END IF;
              next_state:='COMPLETED';
            ELSIF batch.status IN ('PLANNED','RUNNING','PAUSED') AND value_operation='FAIL_BATCH' THEN
              next_state:='FAILED';
            ELSE RAISE EXCEPTION 'A2_REMEDIATION_ILLEGAL_TRANSITION'; END IF;
            UPDATE identity.identity_remediation_batch target SET status=next_state,
              version=target.version+1,updated_at=value_occurred_at,
              started_at=CASE WHEN value_operation='START_BATCH' THEN value_occurred_at ELSE target.started_at END,
              completed_at=CASE WHEN next_state IN ('COMPLETED','FAILED') THEN value_occurred_at ELSE target.completed_at END
              WHERE target.batch_ref=value_batch_ref;
            SELECT * INTO batch FROM identity.identity_remediation_batch target
              WHERE target.batch_ref=value_batch_ref;
            current_row_digest:=identity._a2_identity_remediation_batch_row_digest_v1(
              batch.batch_ref,batch.classification_rule_hash,batch.snapshot_ref_hash,batch.input_count,
              batch.h0_count,batch.h1_count,batch.h2_count,batch.h3_count,batch.h4_count,
              batch.h5_count,batch.h6_count,batch.h7_count,batch.status,batch.version,
              batch.initiated_by_public_ref,batch.created_at,batch.updated_at,batch.started_at,batch.completed_at);
            UPDATE identity.identity_remediation_batch target SET state_digest=current_row_digest
              WHERE target.batch_ref=value_batch_ref;
            target_type:='BATCH'; target_ref:=value_batch_ref; next_version:=batch.version;
            result_code:=next_state;
          ELSE
            SELECT * INTO batch FROM identity.identity_remediation_batch target
              WHERE target.batch_ref=value_batch_ref FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'A2_REMEDIATION_TARGET_NOT_FOUND'; END IF;
            IF batch.status<>'RUNNING' THEN RAISE EXCEPTION 'A2_REMEDIATION_ILLEGAL_TRANSITION'; END IF;
            SELECT * INTO item FROM identity.identity_remediation_item target
              WHERE target.item_ref=value_item_ref AND target.batch_ref=value_batch_ref FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'A2_REMEDIATION_TARGET_NOT_FOUND'; END IF;
            IF item.version<>value_expected_version THEN RAISE EXCEPTION 'A2_REMEDIATION_STALE_VERSION'; END IF;
            IF item.state_digest<>value_expected_target_state_digest THEN
              RAISE EXCEPTION 'A2_REMEDIATION_STALE_PREIMAGE';
            END IF;
            IF item.primary_class IN ('H0','H7') AND item.status='DISCOVERED'
              AND value_operation='EXCLUDE_ITEM' THEN next_state:='EXCLUDED';
            ELSIF item.primary_class IN ('H1','H2','H6') AND item.status='DISCOVERED'
              AND value_operation='REQUIRE_ITEM' THEN next_state:='REMEDIATION_REQUIRED';
            ELSIF item.primary_class IN ('H3','H4','H5')
              AND item.status IN ('DISCOVERED','READY','PROCESSING')
              AND value_operation='REQUIRE_ITEM' THEN next_state:='REMEDIATION_REQUIRED';
            ELSIF item.primary_class IN ('H3','H4','H5') AND item.status='DISCOVERED'
              AND value_operation='MARK_ITEM_READY'
              AND ((item.primary_class='H3' AND value_reason_code='A2_CANONICAL_MATCH_APPROVED')
                OR (item.primary_class IN ('H4','H5') AND value_reason_code='A2_TOKEN_WINDOW_PROVED'))
              THEN next_state:='READY';
            ELSIF item.primary_class IN ('H3','H4','H5') AND item.status='READY'
              AND value_operation='START_ITEM' THEN next_state:='PROCESSING';
            ELSIF item.primary_class IN ('H3','H4','H5') AND item.status='PROCESSING'
              AND value_operation='RECOVER_ITEM' THEN next_state:='READY';
            ELSIF item.primary_class IN ('H3','H4','H5') AND item.status='PROCESSING'
              AND value_operation='COMPLETE_ITEM' THEN next_state:='REMEDIATED';
            ELSIF item.primary_class IN ('H3','H4','H5') AND item.status='PROCESSING'
              AND value_operation='FAIL_ITEM' THEN next_state:='FAILED_TERMINAL';
            ELSE RAISE EXCEPTION 'A2_REMEDIATION_ILLEGAL_TRANSITION'; END IF;
            next_attempt:=item.attempt_count+CASE WHEN value_operation='START_ITEM' THEN 1 ELSE 0 END;
            UPDATE identity.identity_remediation_item target SET status=next_state,
              version=target.version+1,attempt_count=next_attempt,
              eligibility_action_gate_digest=CASE WHEN value_operation='MARK_ITEM_READY'
                THEN value_eligibility_action_gate_digest ELSE target.eligibility_action_gate_digest END,
              mutation_digest=CASE WHEN value_operation='COMPLETE_ITEM' THEN value_mutation_digest ELSE target.mutation_digest END,
              postimage_digest=CASE WHEN value_operation='COMPLETE_ITEM' THEN value_business_postimage_digest ELSE target.postimage_digest END,
              last_reason_code=value_reason_code,updated_at=value_occurred_at
              WHERE target.item_ref=value_item_ref;
            SELECT * INTO item FROM identity.identity_remediation_item target
              WHERE target.item_ref=value_item_ref;
            current_row_digest:=identity._a2_identity_remediation_item_row_digest_v1(
              item.item_ref,item.batch_ref,item.subject_user_ref,item.primary_class,item.secondary_flags,
              item.preimage_digest,item.eligibility_action_gate_digest,item.mutation_digest,
              item.postimage_digest,item.status,item.attempt_count,item.version,item.last_reason_code,
              item.created_at,item.updated_at);
            UPDATE identity.identity_remediation_item target SET state_digest=current_row_digest
              WHERE target.item_ref=value_item_ref;
            target_type:='ITEM'; target_ref:=value_item_ref; next_version:=item.version;
            result_code:=next_state;
          END IF;
          INSERT INTO identity.identity_remediation_audit(
            audit_id,actor_public_ref,action,reason_code,batch_ref,item_ref,target_type,
            target_state,target_version,result_code,request_digest,ledger_row_digest,occurred_at)
          VALUES(value_audit_id,value_actor_scope,value_operation,value_reason_code,value_batch_ref,
            value_item_ref,target_type,next_state,next_version,result_code,internal_request_digest,
            current_row_digest,value_occurred_at);
          INSERT INTO identity.identity_remediation_receipt(
            receipt_id,actor_scope,operation,idempotency_key_digest,request_digest,target_type,
            target_ref,target_state,target_version,result_code,ledger_row_digest,audit_id,created_at)
          VALUES(value_receipt_id,value_actor_scope,value_operation,value_idempotency_key_digest,
            internal_request_digest,target_type,target_ref,next_state,next_version,result_code,
            current_row_digest,value_audit_id,value_occurred_at);
          state:=next_state; version:=next_version; receipt_id:=value_receipt_id;
          state_digest:=current_row_digest;
          RETURN NEXT;
        END $fn$
        """
    )


def _create_confirmation_function(confirmation_role: str) -> None:
    parameters = _typed_parameters()
    arguments = _typed_arguments()
    op.execute(
        f"""
        CREATE FUNCTION identity.a2_identity_remediation_confirm_v1(
          {parameters},value_expected_result_state VARCHAR,
          value_expected_result_version BIGINT,value_expected_result_code VARCHAR)
        RETURNS VARCHAR LANGUAGE plpgsql STABLE SECURITY DEFINER PARALLEL RESTRICTED
        SET search_path = pg_catalog, pg_temp AS $fn$
        DECLARE
          receipt_present BOOLEAN;
          receipt_ok BOOLEAN;
          audit_present BOOLEAN;
          audit_ok BOOLEAN;
          target_present BOOLEAN:=FALSE;
          target_ok BOOLEAN:=FALSE;
          internal_request_digest VARCHAR;
          current_row_digest VARCHAR;
          batch identity.identity_remediation_batch%ROWTYPE;
          item identity.identity_remediation_item%ROWTYPE;
        BEGIN
          IF session_user <> '{confirmation_role}' THEN
            RAISE EXCEPTION 'A2_REMEDIATION_CONFIRMATION_FORBIDDEN';
          END IF;
          IF NOT identity._a2_identity_remediation_inputs_valid_v2({arguments})
            OR value_expected_result_state NOT IN (
              'PLANNED','RUNNING','PAUSED','COMPLETED','FAILED','DISCOVERED','READY',
              'PROCESSING','REMEDIATED','REMEDIATION_REQUIRED','FAILED_TERMINAL','EXCLUDED')
            OR value_expected_result_version<1
            OR value_expected_result_code<>value_expected_result_state
          THEN RAISE EXCEPTION 'A2_REMEDIATION_INPUT_INVALID'; END IF;
          internal_request_digest:=identity._a2_identity_remediation_request_digest_v2({arguments});
          IF value_item_ref IS NULL THEN
            SELECT * INTO batch FROM identity.identity_remediation_batch target
              WHERE target.batch_ref=value_batch_ref;
            target_present:=FOUND;
            IF target_present THEN
              current_row_digest:=identity._a2_identity_remediation_batch_row_digest_v1(
                batch.batch_ref,batch.classification_rule_hash,batch.snapshot_ref_hash,batch.input_count,
                batch.h0_count,batch.h1_count,batch.h2_count,batch.h3_count,batch.h4_count,
                batch.h5_count,batch.h6_count,batch.h7_count,batch.status,batch.version,
                batch.initiated_by_public_ref,batch.created_at,batch.updated_at,batch.started_at,batch.completed_at);
              target_ok:=batch.status=value_expected_result_state
                AND batch.version=value_expected_result_version
                AND batch.state_digest=current_row_digest;
            END IF;
          ELSE
            SELECT * INTO item FROM identity.identity_remediation_item target
              WHERE target.item_ref=value_item_ref AND target.batch_ref=value_batch_ref;
            target_present:=FOUND;
            IF target_present THEN
              current_row_digest:=identity._a2_identity_remediation_item_row_digest_v1(
                item.item_ref,item.batch_ref,item.subject_user_ref,item.primary_class,item.secondary_flags,
                item.preimage_digest,item.eligibility_action_gate_digest,item.mutation_digest,
                item.postimage_digest,item.status,item.attempt_count,item.version,item.last_reason_code,
                item.created_at,item.updated_at);
              target_ok:=item.status=value_expected_result_state
                AND item.version=value_expected_result_version
                AND item.state_digest=current_row_digest;
            END IF;
          END IF;
          SELECT EXISTS(SELECT 1 FROM identity.identity_remediation_receipt receipt
              WHERE receipt.receipt_id=value_receipt_id OR
                (receipt.actor_scope=value_actor_scope AND receipt.operation=value_operation
                 AND receipt.idempotency_key_digest=value_idempotency_key_digest)),
            EXISTS(SELECT 1 FROM identity.identity_remediation_receipt receipt
              WHERE receipt.receipt_id=value_receipt_id AND receipt.actor_scope=value_actor_scope
                AND receipt.operation=value_operation
                AND receipt.idempotency_key_digest=value_idempotency_key_digest
                AND receipt.request_digest=internal_request_digest
                AND receipt.target_type=CASE WHEN value_item_ref IS NULL THEN 'BATCH' ELSE 'ITEM' END
                AND receipt.target_ref=COALESCE(value_item_ref,value_batch_ref)
                AND receipt.target_state=value_expected_result_state
                AND receipt.target_version=value_expected_result_version
                AND receipt.result_code=value_expected_result_code
                AND receipt.ledger_row_digest=current_row_digest
                AND receipt.audit_id=value_audit_id AND receipt.created_at=value_occurred_at)
          INTO receipt_present,receipt_ok;
          SELECT EXISTS(SELECT 1 FROM identity.identity_remediation_audit audit
              WHERE audit.audit_id=value_audit_id),
            EXISTS(SELECT 1 FROM identity.identity_remediation_audit audit
              WHERE audit.audit_id=value_audit_id AND audit.actor_public_ref=value_actor_scope
                AND audit.action=value_operation AND audit.reason_code=value_reason_code
                AND audit.batch_ref=value_batch_ref
                AND audit.item_ref IS NOT DISTINCT FROM value_item_ref
                AND audit.target_type=CASE WHEN value_item_ref IS NULL THEN 'BATCH' ELSE 'ITEM' END
                AND audit.target_state=value_expected_result_state
                AND audit.target_version=value_expected_result_version
                AND audit.result_code=value_expected_result_code
                AND audit.request_digest=internal_request_digest
                AND audit.ledger_row_digest=current_row_digest
                AND audit.occurred_at=value_occurred_at)
          INTO audit_present,audit_ok;
          IF receipt_ok AND audit_ok AND target_ok THEN RETURN 'COMMITTED'; END IF;
          IF NOT receipt_present AND NOT audit_present THEN
            IF value_operation IN ('PLAN_BATCH','REGISTER_ITEM') AND NOT target_present THEN
              RETURN 'NOT_COMMITTED';
            END IF;
            IF value_operation NOT IN ('PLAN_BATCH','REGISTER_ITEM') AND target_present
              AND ((value_item_ref IS NULL AND batch.version=value_expected_version
                    AND batch.state_digest=value_expected_target_state_digest
                    AND batch.state_digest=current_row_digest)
                OR (value_item_ref IS NOT NULL AND item.version=value_expected_version
                    AND item.state_digest=value_expected_target_state_digest
                    AND item.state_digest=current_row_digest))
            THEN RETURN 'NOT_COMMITTED'; END IF;
          END IF;
          RETURN 'UNKNOWN';
        END $fn$
        """
    )


def upgrade() -> None:
    writer_role = _closed_role(_WRITER_ROLE_ENV, _WRITER_DATABASE_URL_ENV)
    confirmation_role = _closed_role(
        _CONFIRMATION_ROLE_ENV, _CONFIRMATION_DATABASE_URL_ENV
    )
    if writer_role == confirmation_role:
        _configuration_error()
    _create_tables()
    _create_internal_functions()
    _create_writer_function(writer_role)
    _create_confirmation_function(confirmation_role)
    quoted_writer = f'"{writer_role}"'
    quoted_confirmation = f'"{confirmation_role}"'
    for table in _TABLES:
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {table} "
            f"FROM PUBLIC, {quoted_writer}, {quoted_confirmation}"
        )
    for function in (*_INTERNAL_FUNCTIONS, _WRITER_FUNCTION, _CONFIRMATION_FUNCTION):
        op.execute(
            f"REVOKE ALL ON FUNCTION {function} "
            f"FROM PUBLIC, {quoted_writer}, {quoted_confirmation}"
        )
    op.execute(
        f"REVOKE CREATE ON SCHEMA public, identity FROM {quoted_writer}, {quoted_confirmation}"
    )
    op.execute(
        f"GRANT USAGE ON SCHEMA identity TO {quoted_writer}, {quoted_confirmation}"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {_WRITER_FUNCTION} TO {quoted_writer}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {_CONFIRMATION_FUNCTION} TO {quoted_confirmation}"
    )


def downgrade() -> None:
    writer_role = _closed_role(_WRITER_ROLE_ENV, _WRITER_DATABASE_URL_ENV)
    confirmation_role = _closed_role(
        _CONFIRMATION_ROLE_ENV, _CONFIRMATION_DATABASE_URL_ENV
    )
    quoted_writer = f'"{writer_role}"'
    quoted_confirmation = f'"{confirmation_role}"'
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_WRITER_FUNCTION} FROM {quoted_writer}")
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION {_CONFIRMATION_FUNCTION} FROM {quoted_confirmation}"
    )
    op.execute(
        f"REVOKE USAGE ON SCHEMA identity FROM {quoted_writer}, {quoted_confirmation}"
    )
    op.execute(f"DROP FUNCTION {_CONFIRMATION_FUNCTION}")
    op.execute(f"DROP FUNCTION {_WRITER_FUNCTION}")
    for function in _INTERNAL_FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    op.drop_table("identity_remediation_receipt", schema="identity")
    op.drop_table("identity_remediation_audit", schema="identity")
    op.drop_table("identity_remediation_item", schema="identity")
    op.drop_index(
        "uq_identity_remediation_single_running_batch",
        table_name="identity_remediation_batch",
        schema="identity",
    )
    op.drop_table("identity_remediation_batch", schema="identity")
