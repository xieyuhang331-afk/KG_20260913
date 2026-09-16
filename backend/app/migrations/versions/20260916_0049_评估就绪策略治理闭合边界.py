"""评估就绪策略治理闭合边界。

Revision ID: 20260916_0049
Revises: 20260915_0048
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260916_0049"
down_revision = "20260915_0048"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052315115572212049
_ROLE_ENV = "KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE"
_URL_ENV = "KG_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL"


def _configuration_error() -> None:
    raise RuntimeError("Readiness policy governance database role configuration is invalid") from None


def _governance_role() -> str:
    role = os.getenv(_ROLE_ENV, "").strip()
    raw_url = os.getenv(_URL_ENV, "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
        _configuration_error()
    try:
        url = make_url(raw_url)
    except Exception:
        _configuration_error()
    if url.drivername != "postgresql+asyncpg" or url.username != role or not url.password:
        _configuration_error()
    connection = op.get_bind()
    if str(connection.execute(sa.text("SELECT current_user")).scalar_one()) == role:
        _configuration_error()
    row = connection.execute(
        sa.text(
            "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    if row is None or any(
        row[name]
        for name in (
            "rolsuper",
            "rolcreaterole",
            "rolcreatedb",
            "rolinherit",
            "rolreplication",
            "rolbypassrls",
        )
    ):
        _configuration_error()
    if connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles a ON a.oid=m.member "
            "JOIN pg_roles b ON b.oid=m.roleid WHERE a.rolname=:role OR b.rolname=:role)"
        ),
        {"role": role},
    ).scalar_one():
        _configuration_error()
    return role


def _add_policy_columns() -> None:
    for column in (
        sa.Column("author_user_id", sa.BigInteger(), nullable=True),
        sa.Column("reviewer_user_id", sa.BigInteger(), nullable=True),
        sa.Column("approval_evidence_ref", sa.String(128), nullable=True),
        sa.Column("approval_package_digest", sa.LargeBinary(), nullable=True),
        sa.Column("reviewed_content_digest", sa.LargeBinary(), nullable=True),
        sa.Column("reviewed_package_digest", sa.LargeBinary(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.BigInteger(), nullable=False, server_default="1"),
    ):
        op.add_column("assessment_readiness_policy_version", column, schema="public")
    op.drop_constraint(
        "ck_assessment_readiness_policy_truth",
        "assessment_readiness_policy_version",
        schema="public",
        type_="check",
    )
    op.create_check_constraint(
        "ck_assessment_readiness_policy_truth",
        "assessment_readiness_policy_version",
        "status IN ('DRAFT','IN_REVIEW','NEEDS_CORRECTION','APPROVED','PUBLISHED',"
        "'SUSPENDED','RETIRED') AND projection_version>=2 AND ("
        "(author_user_id IS NULL AND status IN ('DRAFT','PUBLISHED','SUSPENDED','RETIRED')) OR ("
        "author_user_id IS NOT NULL AND row_version>=1 AND ((status='APPROVED' AND "
        "NOT professionally_approved AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL "
        "AND reviewed_content_digest IS NOT NULL AND reviewed_package_digest IS NOT NULL) OR "
        "(status IN ('PUBLISHED','SUSPENDED','RETIRED') AND professionally_approved "
        "AND reviewer_user_id IS NOT NULL AND reviewed_at IS NOT NULL "
        "AND reviewed_content_digest IS NOT NULL AND reviewed_package_digest IS NOT NULL) OR "
        "status NOT IN ('APPROVED','PUBLISHED','SUSPENDED','RETIRED')) "
        "AND (status<>'PUBLISHED' OR (activated_at IS NOT NULL AND effective_from=activated_at))))",
        schema="public",
        postgresql_not_valid=True,
    )
    op.drop_index(
        "uq_assessment_readiness_policy_current",
        table_name="assessment_readiness_policy_version",
        schema="public",
    )
    op.create_index(
        "uq_assessment_readiness_policy_current",
        "assessment_readiness_policy_version",
        [sa.text("(1)")],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status IN ('PUBLISHED','SUSPENDED')"),
    )
    op.create_index(
        "uq_readiness_policy_propagation_operation_case",
        "slice4_outbox",
        [
            sa.text("(payload_json->>'operation_receipt_id')"),
            sa.text("(payload_json->>'service_case_id')"),
        ],
        unique=True,
        schema="public",
        postgresql_where=sa.text("event_type='READINESS_POLICY_CHANGED'"),
    )


def _create_boundaries(role: str) -> None:
    owner = str(op.get_bind().execute(sa.text("SELECT current_user")).scalar_one())
    op.execute(
        f"""
        CREATE FUNCTION public.readiness_policy_governance_v1(
          value_operation VARCHAR,value_payload JSONB
        ) RETURNS JSONB
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE
          value_policy public.assessment_readiness_policy_version%ROWTYPE;
          value_response JSONB;
          value_existing JSONB;
          value_existing_count BIGINT;
          value_target_cases UUID[] := ARRAY[]::UUID[];
          value_target_count BIGINT := 0;
          value_target_set_digest TEXT := encode(sha256(convert_to('', 'UTF8')),'hex');
          value_event_payload JSONB;
          value_policy_preimage JSONB;
          value_policy_postimage JSONB;
          value_audit_postimage JSONB;
          value_target_postimage JSONB := '[]'::JSONB;
          value_receipt_postimage JSONB;
          value_response_postimage JSONB;
          value_preimage_digest TEXT;
          value_computed_postimage BYTEA;
          value_recorded_at TIMESTAMPTZ := transaction_timestamp();
        BEGIN
          IF session_user<>'{role}' THEN
            RAISE EXCEPTION 'READINESS_POLICY_GOVERNANCE_FORBIDDEN';
          END IF;
          IF value_operation NOT IN ('CREATE','UPDATE_DRAFT','SUBMIT','REVIEW_APPROVE',
             'REVIEW_CORRECTION','PUBLISH','SUSPEND','RESUME','RETIRE')
             OR value_payload - ARRAY[
               'actor_user_id','actor_role','policy_version_id','expected_version','version_no',
               'content','policy_digest','approval_evidence_ref','approval_package_digest',
               'reason_code','idempotency_key','request_digest',
               'digest_key_id','operation_receipt_id','audit_id','occurred_at'
             ]::TEXT[] <> '{{}}'::JSONB
          THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
          IF value_payload->>'actor_user_id' IS NULL
             OR value_payload->>'actor_role' IS NULL
             OR value_payload->>'policy_version_id' IS NULL
             OR value_payload->>'idempotency_key' IS NULL
             OR value_payload->>'request_digest' IS NULL
             OR value_payload->>'digest_key_id' IS NULL
             OR value_payload->>'operation_receipt_id' IS NULL
             OR value_payload->>'audit_id' IS NULL
             OR value_payload->>'occurred_at' IS NULL
             OR (value_operation<>'CREATE' AND value_payload->>'expected_version' IS NULL)
             OR (value_operation IN ('CREATE','UPDATE_DRAFT') AND (
                   value_payload->>'version_no' IS NULL AND value_operation='CREATE'
                   OR value_payload->'content' IS NULL
                   OR value_payload->>'policy_digest' IS NULL
                   OR value_payload->>'approval_evidence_ref' IS NULL
                   OR value_payload->>'approval_package_digest' IS NULL
                 ))
          THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
          IF value_operation IN ('REVIEW_APPROVE','REVIEW_CORRECTION','PUBLISH',
               'SUSPEND','RESUME','RETIRE') AND value_payload->>'reason_code' IS NULL
          THEN RAISE EXCEPTION 'READINESS_POLICY_REASON_INVALID'; END IF;

          PERFORM pg_advisory_xact_lock({ _LOCK_KEY });
          PERFORM pg_advisory_xact_lock(
            hashtextextended(
              (value_payload->>'actor_user_id')||':'||value_operation||':'||
              (value_payload->>'idempotency_key'),0
            )
          );
          PERFORM 1 FROM public."user" u
          WHERE u.id=(value_payload->>'actor_user_id')::BIGINT
            AND u.status='active' AND u.exited_at IS NULL
            AND u.deletion_requested_at IS NULL
            AND u.role::TEXT=(value_payload->>'actor_role')
            AND (
              (value_operation IN ('CREATE','UPDATE_DRAFT','SUBMIT','REVIEW_APPROVE',
                 'REVIEW_CORRECTION') AND u.role='expert')
              OR (value_operation IN ('PUBLISH','SUSPEND','RESUME','RETIRE')
                  AND u.role IN ('sys_admin','super_admin'))
            )
          FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'READINESS_POLICY_GOVERNANCE_FORBIDDEN'; END IF;

          IF value_operation IN ('PUBLISH','RESUME') THEN
            RAISE EXCEPTION 'POLICY_MEDICAL_APPROVAL_REQUIRED';
          END IF;

          SELECT convert_from(i.response_ciphertext,'UTF8')::JSONB,count(*) OVER()
          INTO value_existing,value_existing_count
          FROM public.slice4_idempotency i
          WHERE i.operation='READINESS_POLICY_'||value_operation
            AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID
            AND (
              (value_operation='CREATE' AND EXISTS(
                SELECT 1 FROM public.slice4_audit a
                WHERE a.aggregate_ref=i.scope_ref AND a.event_type=i.operation
                  AND a.actor_user_id=(value_payload->>'actor_user_id')::BIGINT
                  AND a.event_digest=i.request_digest
              ))
              OR (value_operation<>'CREATE'
                  AND i.scope_ref=(value_payload->>'policy_version_id')::UUID)
            );
          IF COALESCE(value_existing_count,0)>1 THEN
            RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT';
          END IF;
          IF FOUND THEN
            IF NOT EXISTS(
              SELECT 1 FROM public.slice4_idempotency i
              WHERE i.operation='READINESS_POLICY_'||value_operation
                AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID
                AND i.request_digest=decode(value_payload->>'request_digest','hex')
                AND (
                  (value_operation='CREATE' AND EXISTS(
                    SELECT 1 FROM public.slice4_audit a
                    WHERE a.aggregate_ref=i.scope_ref AND a.event_type=i.operation
                      AND a.actor_user_id=(value_payload->>'actor_user_id')::BIGINT
                      AND a.event_digest=i.request_digest
                  ))
                  OR (value_operation<>'CREATE'
                      AND i.scope_ref=(value_payload->>'policy_version_id')::UUID)
                )
            ) THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF;
            RETURN value_existing;
          END IF;

          IF value_operation IN ('CREATE','UPDATE_DRAFT') THEN
            IF jsonb_typeof(value_payload->'content')<>'object'
               OR (value_payload->'content') - ARRAY[
                 'required_profile_sections','required_indicators','allowed_states',
                 'projection_version','rule_version'
               ]::TEXT[] <> '{{}}'::JSONB
               OR jsonb_typeof(value_payload->'content'->'required_profile_sections')<>'array'
               OR jsonb_array_length(value_payload->'content'->'required_profile_sections')<1
               OR jsonb_array_length(value_payload->'content'->'required_profile_sections')>64
               OR jsonb_typeof(value_payload->'content'->'required_indicators')<>'array'
               OR jsonb_array_length(value_payload->'content'->'required_indicators')<1
               OR jsonb_array_length(value_payload->'content'->'required_indicators')>128
               OR jsonb_typeof(value_payload->'content'->'allowed_states')<>'array'
               OR jsonb_array_length(value_payload->'content'->'allowed_states')<1
               OR jsonb_array_length(value_payload->'content'->'allowed_states')>3
               OR (value_payload->'content'->>'projection_version') !~ '^[0-9]{{1,5}}$'
               OR (value_payload->'content'->>'rule_version') !~ '^[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$'
               OR (value_payload->>'policy_digest') !~ '^[0-9a-f]{{64}}$'
               OR (value_payload->>'approval_package_digest') !~ '^[0-9a-f]{{64}}$'
               OR (value_payload->>'approval_evidence_ref') !~ '^[A-Za-z0-9][A-Za-z0-9_.:/-]{{7,127}}$'
            THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
            IF (value_payload->'content'->>'projection_version')::INT NOT BETWEEN 2 AND 32767
            THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
            IF EXISTS(
              SELECT 1 FROM jsonb_array_elements(value_payload->'content'->'required_profile_sections') s
              WHERE jsonb_typeof(s)<>'string'
                 OR trim(BOTH '"' FROM s::TEXT) !~ '^[A-Za-z0-9][A-Za-z0-9_-]{{0,63}}$'
            ) OR (SELECT count(*) FROM jsonb_array_elements(value_payload->'content'->'required_profile_sections'))
                 <>(SELECT count(DISTINCT s::TEXT) FROM jsonb_array_elements(value_payload->'content'->'required_profile_sections') s)
            THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
            IF EXISTS(
              SELECT 1 FROM jsonb_array_elements(value_payload->'content'->'required_indicators') i
              WHERE jsonb_typeof(i)<>'object'
                 OR i - ARRAY['indicator_code','max_age_days']::TEXT[] <> '{{}}'::JSONB
                 OR (i->>'indicator_code') !~ '^[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$'
                 OR CASE WHEN i ? 'max_age_days' AND i->'max_age_days'<>'null'::JSONB
                         THEN jsonb_typeof(i->'max_age_days')<>'number'
                           OR (i->>'max_age_days') !~ '^[0-9]{{1,4}}$'
                           OR (i->>'max_age_days')::INT>3650
                         ELSE FALSE END
            ) OR (SELECT count(*) FROM jsonb_array_elements(value_payload->'content'->'required_indicators'))
                 <>(SELECT count(DISTINCT i->>'indicator_code') FROM jsonb_array_elements(value_payload->'content'->'required_indicators') i)
            THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
            IF EXISTS(
              SELECT 1 FROM jsonb_array_elements_text(value_payload->'content'->'allowed_states') s
              WHERE s NOT IN ('SELF_REPORTED','VERIFIED','REVIEWED')
            ) OR (SELECT count(*) FROM jsonb_array_elements(value_payload->'content'->'allowed_states'))
                 <>(SELECT count(DISTINCT s) FROM jsonb_array_elements_text(value_payload->'content'->'allowed_states') s)
            THEN RAISE EXCEPTION 'READINESS_POLICY_INVALID'; END IF;
          END IF;

          IF value_operation='CREATE' THEN
            value_policy_preimage=jsonb_build_array(
              'ABSENT',value_payload->>'policy_version_id'
            );
            INSERT INTO public.assessment_readiness_policy_version(
              policy_version_id,version_no,status,required_profile_sections,
              required_indicators,allowed_states,projection_version,rule_version,
              professionally_approved,approved_by,approved_at,effective_from,retired_at,
              policy_digest,digest_key_id,created_at,author_user_id,reviewer_user_id,
              approval_evidence_ref,approval_package_digest,reviewed_content_digest,
              reviewed_package_digest,submitted_at,reviewed_at,activated_at,suspended_at,row_version
            ) VALUES (
              (value_payload->>'policy_version_id')::UUID,
              (value_payload->>'version_no')::BIGINT,'DRAFT',
              value_payload->'content'->'required_profile_sections',
              value_payload->'content'->'required_indicators',
              value_payload->'content'->'allowed_states',
              (value_payload->'content'->>'projection_version')::SMALLINT,
              value_payload->'content'->>'rule_version',false,NULL,NULL,
              transaction_timestamp(),NULL,decode(value_payload->>'policy_digest','hex'),
              value_payload->>'digest_key_id',transaction_timestamp(),
              (value_payload->>'actor_user_id')::BIGINT,NULL,
              value_payload->>'approval_evidence_ref',
              decode(value_payload->>'approval_package_digest','hex'),NULL,NULL,
              NULL,NULL,NULL,NULL,1
            );
          ELSE
            SELECT * INTO value_policy
            FROM public.assessment_readiness_policy_version p
            WHERE p.policy_version_id=(value_payload->>'policy_version_id')::UUID
            FOR UPDATE;
            IF NOT FOUND OR value_policy.row_version IS DISTINCT FROM
               (value_payload->>'expected_version')::BIGINT
            THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
            value_policy_preimage=jsonb_build_array(
              value_policy.policy_version_id::TEXT,value_policy.version_no,value_policy.status,
              value_policy.required_profile_sections,value_policy.required_indicators,
              value_policy.allowed_states,value_policy.projection_version,value_policy.rule_version,
              value_policy.professionally_approved,value_policy.approved_by,
              CASE WHEN value_policy.approved_at IS NULL THEN NULL ELSE
                to_char(value_policy.approved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              to_char(value_policy.effective_from AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              CASE WHEN value_policy.retired_at IS NULL THEN NULL ELSE
                to_char(value_policy.retired_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              encode(value_policy.policy_digest,'hex'),value_policy.digest_key_id,
              to_char(value_policy.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              value_policy.author_user_id,value_policy.reviewer_user_id,
              value_policy.approval_evidence_ref,
              CASE WHEN value_policy.approval_package_digest IS NULL THEN NULL ELSE
                encode(value_policy.approval_package_digest,'hex') END,
              CASE WHEN value_policy.reviewed_content_digest IS NULL THEN NULL ELSE
                encode(value_policy.reviewed_content_digest,'hex') END,
              CASE WHEN value_policy.reviewed_package_digest IS NULL THEN NULL ELSE
                encode(value_policy.reviewed_package_digest,'hex') END,
              CASE WHEN value_policy.submitted_at IS NULL THEN NULL ELSE
                to_char(value_policy.submitted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              CASE WHEN value_policy.reviewed_at IS NULL THEN NULL ELSE
                to_char(value_policy.reviewed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              CASE WHEN value_policy.activated_at IS NULL THEN NULL ELSE
                to_char(value_policy.activated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              CASE WHEN value_policy.suspended_at IS NULL THEN NULL ELSE
                to_char(value_policy.suspended_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              value_policy.row_version
            );
            IF value_operation='UPDATE_DRAFT' THEN
              IF value_policy.author_user_id IS DISTINCT FROM
                    (value_payload->>'actor_user_id')::BIGINT
                 OR value_policy.status NOT IN ('DRAFT','NEEDS_CORRECTION')
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='DRAFT',required_profile_sections=value_payload->'content'->'required_profile_sections',
                required_indicators=value_payload->'content'->'required_indicators',
                allowed_states=value_payload->'content'->'allowed_states',
                projection_version=(value_payload->'content'->>'projection_version')::SMALLINT,
                rule_version=value_payload->'content'->>'rule_version',
                policy_digest=decode(value_payload->>'policy_digest','hex'),
                approval_evidence_ref=value_payload->>'approval_evidence_ref',
                approval_package_digest=decode(value_payload->>'approval_package_digest','hex'),
                professionally_approved=false,approved_by=NULL,approved_at=NULL,
                reviewer_user_id=NULL,reviewed_content_digest=NULL,reviewed_package_digest=NULL,
                submitted_at=NULL,reviewed_at=NULL,activated_at=NULL,suspended_at=NULL,
                retired_at=NULL,row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='SUBMIT' THEN
              IF value_policy.author_user_id IS DISTINCT FROM
                    (value_payload->>'actor_user_id')::BIGINT
                 OR value_policy.status<>'DRAFT'
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='IN_REVIEW',submitted_at=transaction_timestamp(),row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='REVIEW_APPROVE' THEN
              IF value_policy.status<>'IN_REVIEW'
                 OR value_policy.author_user_id=(value_payload->>'actor_user_id')::BIGINT
                 OR value_payload->>'reason_code' IS DISTINCT FROM 'PROFESSIONAL_POLICY_APPROVED'
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='APPROVED',professionally_approved=false,
                reviewer_user_id=(value_payload->>'actor_user_id')::BIGINT,
                approved_by=NULL,
                reviewed_content_digest=policy_digest,
                reviewed_package_digest=approval_package_digest,
                reviewed_at=transaction_timestamp(),approved_at=NULL,
                row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='REVIEW_CORRECTION' THEN
              IF value_policy.status<>'IN_REVIEW'
                 OR value_policy.author_user_id=(value_payload->>'actor_user_id')::BIGINT
                 OR value_payload->>'reason_code' NOT IN (
                   'POLICY_CONTENT_CORRECTION_REQUIRED','APPROVAL_EVIDENCE_CORRECTION_REQUIRED'
                 )
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='NEEDS_CORRECTION',professionally_approved=false,
                reviewer_user_id=(value_payload->>'actor_user_id')::BIGINT,
                reviewed_at=transaction_timestamp(),row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='PUBLISH' THEN
              IF value_policy.status<>'APPROVED'
                 OR value_policy.policy_digest<>value_policy.reviewed_content_digest
                 OR value_policy.approval_package_digest<>value_policy.reviewed_package_digest
                 OR value_payload->>'reason_code' IS DISTINCT FROM 'APPROVED_POLICY_RELEASE'
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='RETIRED',retired_at=transaction_timestamp(),row_version=row_version+1
              WHERE policy_version_id<>value_policy.policy_version_id
                AND status IN ('PUBLISHED','SUSPENDED');
              UPDATE public.assessment_readiness_policy_version SET
                status='PUBLISHED',effective_from=transaction_timestamp(),
                activated_at=transaction_timestamp(),suspended_at=NULL,retired_at=NULL,
                row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='SUSPEND' THEN
              IF value_policy.status<>'PUBLISHED'
                 OR value_payload->>'reason_code' IS DISTINCT FROM 'POLICY_SAFETY_REVIEW_REQUIRED'
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='SUSPENDED',suspended_at=transaction_timestamp(),row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='RESUME' THEN
              IF value_policy.status<>'SUSPENDED'
                 OR value_payload->>'reason_code' IS DISTINCT FROM 'POLICY_SAFETY_REVIEW_CLEARED'
                 OR EXISTS(SELECT 1 FROM public.assessment_readiness_policy_version p
                           WHERE p.policy_version_id<>value_policy.policy_version_id
                             AND p.status='PUBLISHED')
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='PUBLISHED',effective_from=transaction_timestamp(),
                activated_at=transaction_timestamp(),suspended_at=NULL,row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            ELSIF value_operation='RETIRE' THEN
              IF value_policy.status NOT IN ('PUBLISHED','SUSPENDED')
                 OR value_payload->>'reason_code' NOT IN (
                   'SUPERSEDED_BY_APPROVED_POLICY','POLICY_WITHDRAWN'
                 )
              THEN RAISE EXCEPTION 'READINESS_POLICY_STATE_CONFLICT'; END IF;
              UPDATE public.assessment_readiness_policy_version SET
                status='RETIRED',retired_at=transaction_timestamp(),suspended_at=NULL,
                row_version=row_version+1
              WHERE policy_version_id=value_policy.policy_version_id;
            END IF;
          END IF;

          IF value_operation IN ('PUBLISH','SUSPEND','RESUME','RETIRE') THEN
            SELECT COALESCE(array_agg(c.case_id ORDER BY c.case_id),ARRAY[]::UUID[])
            INTO value_target_cases
            FROM public.service_case c WHERE c.status='PREPARING';
            value_target_count=cardinality(value_target_cases);
            value_target_set_digest=encode(sha256(convert_to(
              COALESCE(array_to_string(value_target_cases,','),''),'UTF8')),'hex');
            INSERT INTO public.slice4_outbox(
              event_id,aggregate_type,aggregate_ref,event_type,payload_digest,payload_json,
              status,attempts,lease_owner,lease_until,created_at,delivered_at
            )
            SELECT (
              substr(md5((value_payload->>'operation_receipt_id')||case_id::TEXT),1,8)||'-'||
              substr(md5((value_payload->>'operation_receipt_id')||case_id::TEXT),9,4)||'-'||
              '7'||substr(md5((value_payload->>'operation_receipt_id')||case_id::TEXT),14,3)||'-'||
              '8'||substr(md5((value_payload->>'operation_receipt_id')||case_id::TEXT),18,3)||'-'||
              substr(md5((value_payload->>'operation_receipt_id')||case_id::TEXT),21,12)
            )::UUID,'READINESS_POLICY',(value_payload->>'policy_version_id')::UUID,
            'READINESS_POLICY_CHANGED',
            sha256(convert_to(jsonb_build_object(
              'operation_receipt_id',value_payload->>'operation_receipt_id',
              'service_case_id',case_id::TEXT,
              'policy_version_id',value_payload->>'policy_version_id',
              'operation',value_operation
            )::TEXT,'UTF8')),
            jsonb_build_object(
              'operation_receipt_id',value_payload->>'operation_receipt_id',
              'service_case_id',case_id::TEXT,
              'policy_version_id',value_payload->>'policy_version_id',
              'operation',value_operation
            ),'PENDING',0,NULL,NULL,value_recorded_at,NULL
            FROM unnest(value_target_cases) AS case_id;
          END IF;

          SELECT * INTO value_policy FROM public.assessment_readiness_policy_version p
          WHERE p.policy_version_id=(value_payload->>'policy_version_id')::UUID;
          value_policy_postimage=jsonb_build_array(
            value_policy.policy_version_id::TEXT,value_policy.version_no,value_policy.status,
            value_policy.required_profile_sections,value_policy.required_indicators,
            value_policy.allowed_states,value_policy.projection_version,value_policy.rule_version,
            value_policy.professionally_approved,value_policy.approved_by,
            CASE WHEN value_policy.approved_at IS NULL THEN NULL ELSE
              to_char(value_policy.approved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            to_char(value_policy.effective_from AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            CASE WHEN value_policy.retired_at IS NULL THEN NULL ELSE
              to_char(value_policy.retired_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            encode(value_policy.policy_digest,'hex'),value_policy.digest_key_id,
            to_char(value_policy.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            value_policy.author_user_id,value_policy.reviewer_user_id,
            value_policy.approval_evidence_ref,
            CASE WHEN value_policy.approval_package_digest IS NULL THEN NULL ELSE
              encode(value_policy.approval_package_digest,'hex') END,
            CASE WHEN value_policy.reviewed_content_digest IS NULL THEN NULL ELSE
              encode(value_policy.reviewed_content_digest,'hex') END,
            CASE WHEN value_policy.reviewed_package_digest IS NULL THEN NULL ELSE
              encode(value_policy.reviewed_package_digest,'hex') END,
            CASE WHEN value_policy.submitted_at IS NULL THEN NULL ELSE
              to_char(value_policy.submitted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            CASE WHEN value_policy.reviewed_at IS NULL THEN NULL ELSE
              to_char(value_policy.reviewed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            CASE WHEN value_policy.activated_at IS NULL THEN NULL ELSE
              to_char(value_policy.activated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            CASE WHEN value_policy.suspended_at IS NULL THEN NULL ELSE
              to_char(value_policy.suspended_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
            value_policy.row_version
          );
          SELECT COALESCE(jsonb_agg(jsonb_build_array(
                   o.event_id::TEXT,o.aggregate_type,o.aggregate_ref::TEXT,o.event_type,
                   encode(o.payload_digest,'hex'),o.payload_json,
                   to_char(o.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                 ) ORDER BY o.event_id),'[]'::JSONB)
          INTO value_target_postimage
          FROM public.slice4_outbox o
          WHERE o.payload_json->>'operation_receipt_id'=value_payload->>'operation_receipt_id';
          value_target_count=jsonb_array_length(value_target_postimage);
          value_target_set_digest=encode(sha256(convert_to(
            'ASSESSMENT_READINESS_POLICY_TARGET_SET_V1;targets=V'||
            octet_length(convert_to(value_target_postimage::TEXT,'UTF8'))::TEXT||':'||
            value_target_postimage::TEXT||';','UTF8')),'hex');
          value_preimage_digest=encode(sha256(convert_to(
            'ASSESSMENT_READINESS_POLICY_PREIMAGE_V1;policy=V'||
            octet_length(convert_to(value_policy_preimage::TEXT,'UTF8'))::TEXT||':'||
            value_policy_preimage::TEXT||';','UTF8')),'hex');
          value_response=jsonb_build_object(
            'policy_version_id',value_policy.policy_version_id,
            'version_no',value_policy.version_no,'status',value_policy.status,
            'required_profile_sections',value_policy.required_profile_sections,
            'required_indicators',value_policy.required_indicators,
            'allowed_states',value_policy.allowed_states,
            'projection_version',value_policy.projection_version,
            'rule_version',value_policy.rule_version,
            'approval_evidence_ref',value_policy.approval_evidence_ref,
            'approval_package_digest',CASE WHEN value_policy.approval_package_digest IS NULL
              THEN NULL ELSE encode(value_policy.approval_package_digest,'hex') END,
            'medical_approval_verified',false,
            'policy_digest',encode(value_policy.policy_digest,'hex'),
            'effective_from',CASE WHEN value_policy.activated_at IS NULL THEN NULL
              ELSE value_policy.effective_from END,
            'suspended_at',value_policy.suspended_at,'retired_at',value_policy.retired_at,
            'row_version',value_policy.row_version,
            'operation_receipt_id',value_payload->>'operation_receipt_id',
            'target_count',value_target_count,'target_set_digest',value_target_set_digest,
            'preimage_digest',value_preimage_digest,
            '_policy_preimage',value_policy_preimage,
            '_policy_postimage',value_policy_postimage,
            '_audit_id',value_payload->>'audit_id'
          );
          INSERT INTO public.slice4_audit(
            audit_id,event_type,aggregate_ref,actor_user_id,event_digest,digest_key_id,created_at
          ) VALUES (
            (value_payload->>'audit_id')::UUID,'READINESS_POLICY_'||value_operation,
            value_policy.policy_version_id,(value_payload->>'actor_user_id')::BIGINT,
            decode(value_payload->>'request_digest','hex'),value_payload->>'digest_key_id',
            value_recorded_at
          );
          value_audit_postimage=jsonb_build_array(
            value_payload->>'audit_id','READINESS_POLICY_'||value_operation,
            value_policy.policy_version_id::TEXT,(value_payload->>'actor_user_id')::BIGINT,
            value_payload->>'request_digest',value_payload->>'digest_key_id',
            to_char(value_recorded_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          );
          value_receipt_postimage=jsonb_build_array(
            value_payload->>'operation_receipt_id','READINESS_POLICY_'||value_operation,
            value_policy.policy_version_id::TEXT,value_payload->>'idempotency_key',
            value_payload->>'request_digest',value_payload->>'digest_key_id',
            to_char(value_recorded_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          );
          value_response_postimage=value_response;
          value_computed_postimage=sha256(convert_to(
            'ASSESSMENT_READINESS_POLICY_POSTIMAGE_V1;'||
            'policy=V'||octet_length(convert_to(value_policy_postimage::TEXT,'UTF8'))::TEXT||':'||
              value_policy_postimage::TEXT||';'||
            'receipt=V'||octet_length(convert_to(value_receipt_postimage::TEXT,'UTF8'))::TEXT||':'||
              value_receipt_postimage::TEXT||';'||
            'audit=V'||octet_length(convert_to(value_audit_postimage::TEXT,'UTF8'))::TEXT||':'||
              value_audit_postimage::TEXT||';'||
            'response=V'||octet_length(convert_to(value_response_postimage::TEXT,'UTF8'))::TEXT||':'||
              value_response_postimage::TEXT||';'||
            'targets=V'||octet_length(convert_to(value_target_postimage::TEXT,'UTF8'))::TEXT||':'||
              value_target_postimage::TEXT||';','UTF8'));
          value_response=value_response||jsonb_build_object(
            'postimage_digest',encode(value_computed_postimage,'hex')
          );
          INSERT INTO public.slice4_idempotency(
            receipt_id,operation,scope_ref,idempotency_key,request_digest,postimage_digest,
            response_ciphertext,response_key_id,created_at
          ) VALUES (
            (value_payload->>'operation_receipt_id')::UUID,
            'READINESS_POLICY_'||value_operation,value_policy.policy_version_id,
            (value_payload->>'idempotency_key')::UUID,
            decode(value_payload->>'request_digest','hex'),
            value_computed_postimage,
            convert_to(value_response::TEXT,'UTF8'),value_payload->>'digest_key_id',
            value_recorded_at
          );
          RETURN value_response;
        EXCEPTION
          WHEN unique_violation THEN
            IF EXISTS(
              SELECT 1 FROM public.slice4_idempotency i
              WHERE i.operation='READINESS_POLICY_'||value_operation
                AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID
                AND i.request_digest<>decode(value_payload->>'request_digest','hex')
                AND (
                  (value_operation='CREATE' AND EXISTS(
                    SELECT 1 FROM public.slice4_audit a
                    WHERE a.aggregate_ref=i.scope_ref AND a.event_type=i.operation
                      AND a.actor_user_id=(value_payload->>'actor_user_id')::BIGINT
                      AND a.event_digest=i.request_digest
                  ))
                  OR (value_operation<>'CREATE'
                      AND i.scope_ref=(value_payload->>'policy_version_id')::UUID)
                )
            ) THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF;
            RAISE;
        END;
        $fn$;
        """
    )
    op.execute(
        f'ALTER FUNCTION public.readiness_policy_governance_v1(VARCHAR,JSONB) OWNER TO "{owner}"'
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.readiness_policy_governance_v1(VARCHAR,JSONB) FROM PUBLIC"
    )
    op.execute(
        f'GRANT EXECUTE ON FUNCTION public.readiness_policy_governance_v1(VARCHAR,JSONB) TO "{role}"'
    )
    op.execute(
        f"""
        CREATE FUNCTION public.readiness_policy_governance_confirm_v1(value_payload JSONB)
        RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE value_receipt JSONB; value_receipt_count BIGINT; value_key_count BIGINT;
        DECLARE value_receipt_scope UUID; value_receipt_id UUID; value_receipt_postimage BYTEA;
        DECLARE value_receipt_operation VARCHAR; value_receipt_key UUID;
        DECLARE value_receipt_request BYTEA; value_receipt_key_id VARCHAR;
        DECLARE value_receipt_created_at TIMESTAMPTZ; value_target_ref UUID;
        DECLARE value_audit_postimage JSONB; value_audit_count BIGINT;
        DECLARE value_target_postimage JSONB; value_target_postimage_digest TEXT;
        DECLARE value_receipt_core JSONB; value_response_postimage JSONB;
        DECLARE value_policy_current public.assessment_readiness_policy_version%ROWTYPE;
        DECLARE value_policy_current_snapshot JSONB; value_computed_postimage BYTEA;
        DECLARE value_computed_preimage TEXT; value_target_trace_count BIGINT;
        DECLARE value_chain_receipt RECORD; value_chain_snapshot JSONB;
        DECLARE value_chain_audit JSONB; value_chain_audit_count BIGINT;
        DECLARE value_chain_targets JSONB; value_chain_target_count BIGINT;
        DECLARE value_chain_target_digest TEXT; value_chain_receipt_core JSONB;
        DECLARE value_chain_response JSONB; value_chain_computed BYTEA;
        DECLARE value_chain_expected_version BIGINT; value_chain_count BIGINT := 0;
        DECLARE value_chain_valid BOOLEAN := TRUE;
        BEGIN
          IF session_user<>'{role}' THEN
            RAISE EXCEPTION 'READINESS_POLICY_GOVERNANCE_FORBIDDEN';
          END IF;
          IF (value_payload->>'operation')='CREATE' THEN
            SELECT count(*) INTO value_key_count
            FROM public.slice4_idempotency i
            WHERE i.operation='READINESS_POLICY_CREATE'
              AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID
              AND EXISTS(
                SELECT 1 FROM public.slice4_audit a
                WHERE a.aggregate_ref=i.scope_ref AND a.event_type=i.operation
                  AND a.actor_user_id=(value_payload->>'actor_user_id')::BIGINT
                  AND a.event_digest=i.request_digest
              );
            SELECT convert_from(i.response_ciphertext,'UTF8')::JSONB,count(*) OVER(),
                   i.scope_ref,i.receipt_id,i.postimage_digest,i.operation,
                   i.idempotency_key,i.request_digest,i.response_key_id,i.created_at
            INTO value_receipt,value_receipt_count,value_receipt_scope,
                 value_receipt_id,value_receipt_postimage,value_receipt_operation,
                 value_receipt_key,value_receipt_request,value_receipt_key_id,
                 value_receipt_created_at
            FROM public.slice4_idempotency i
            WHERE i.operation='READINESS_POLICY_CREATE'
              AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID
              AND i.request_digest=decode(value_payload->>'request_digest','hex')
              AND EXISTS(
                SELECT 1 FROM public.slice4_audit a
                WHERE a.aggregate_ref=i.scope_ref AND a.event_type=i.operation
                  AND a.actor_user_id=(value_payload->>'actor_user_id')::BIGINT
                  AND a.event_digest=i.request_digest
              );
            value_target_ref := COALESCE(
              value_receipt_scope,(value_payload->>'policy_version_id')::UUID
            );
          ELSE
            SELECT convert_from(i.response_ciphertext,'UTF8')::JSONB,count(*) OVER(),
                   i.scope_ref,i.receipt_id,i.postimage_digest,i.operation,
                   i.idempotency_key,i.request_digest,i.response_key_id,i.created_at
            INTO value_receipt,value_receipt_count,value_receipt_scope,
                 value_receipt_id,value_receipt_postimage,value_receipt_operation,
                 value_receipt_key,value_receipt_request,value_receipt_key_id,
                 value_receipt_created_at
            FROM public.slice4_idempotency i
            WHERE i.receipt_id=(value_payload->>'operation_receipt_id')::UUID
              AND i.operation='READINESS_POLICY_'||(value_payload->>'operation')
              AND i.scope_ref=(value_payload->>'policy_version_id')::UUID
              AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID
              AND i.request_digest=decode(value_payload->>'request_digest','hex');
            SELECT count(*) INTO value_key_count FROM public.slice4_idempotency i
            WHERE i.operation='READINESS_POLICY_'||(value_payload->>'operation')
              AND i.scope_ref=(value_payload->>'policy_version_id')::UUID
              AND i.idempotency_key=(value_payload->>'idempotency_key')::UUID;
            value_target_ref := (value_payload->>'policy_version_id')::UUID;
          END IF;

          SELECT x.snapshot,count(*) OVER()
          INTO value_audit_postimage,value_audit_count
          FROM (
            SELECT jsonb_build_array(
              a.audit_id::TEXT,a.event_type,a.aggregate_ref::TEXT,a.actor_user_id,
              encode(a.event_digest,'hex'),a.digest_key_id,
              to_char(a.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            ) AS snapshot
            FROM public.slice4_audit a
            WHERE a.audit_id=COALESCE((value_receipt->>'_audit_id')::UUID,
                                      (value_payload->>'audit_id')::UUID)
          ) x;
          SELECT COALESCE(jsonb_agg(jsonb_build_array(
                   o.event_id::TEXT,o.aggregate_type,o.aggregate_ref::TEXT,o.event_type,
                   encode(o.payload_digest,'hex'),o.payload_json,
                   to_char(o.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                 ) ORDER BY o.event_id),'[]'::JSONB),count(*)
          INTO value_target_postimage,value_target_trace_count
          FROM public.slice4_outbox o
          WHERE o.payload_json->>'operation_receipt_id'=COALESCE(
            value_receipt->>'operation_receipt_id',value_payload->>'operation_receipt_id'
          );
          value_target_postimage_digest=encode(sha256(convert_to(
            'ASSESSMENT_READINESS_POLICY_TARGET_SET_V1;targets=V'||
            octet_length(convert_to(value_target_postimage::TEXT,'UTF8'))::TEXT||':'||
            value_target_postimage::TEXT||';','UTF8')),'hex');

          IF value_receipt_count=1 THEN
            value_receipt_core=jsonb_build_array(
              value_receipt_id::TEXT,value_receipt_operation,value_receipt_scope::TEXT,
              value_receipt_key::TEXT,encode(value_receipt_request,'hex'),value_receipt_key_id,
              to_char(value_receipt_created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
            );
            value_response_postimage=value_receipt-'postimage_digest';
            value_computed_postimage=sha256(convert_to(
              'ASSESSMENT_READINESS_POLICY_POSTIMAGE_V1;'||
              'policy=V'||octet_length(convert_to((value_receipt->'_policy_postimage')::TEXT,'UTF8'))::TEXT||':'||
                (value_receipt->'_policy_postimage')::TEXT||';'||
              'receipt=V'||octet_length(convert_to(value_receipt_core::TEXT,'UTF8'))::TEXT||':'||
                value_receipt_core::TEXT||';'||
              'audit=V'||octet_length(convert_to(value_audit_postimage::TEXT,'UTF8'))::TEXT||':'||
                value_audit_postimage::TEXT||';'||
              'response=V'||octet_length(convert_to(value_response_postimage::TEXT,'UTF8'))::TEXT||':'||
                value_response_postimage::TEXT||';'||
              'targets=V'||octet_length(convert_to(value_target_postimage::TEXT,'UTF8'))::TEXT||':'||
                value_target_postimage::TEXT||';','UTF8'));
          END IF;

          SELECT * INTO value_policy_current
          FROM public.assessment_readiness_policy_version p
          WHERE p.policy_version_id=value_target_ref;
          IF FOUND THEN
            value_policy_current_snapshot=jsonb_build_array(
              value_policy_current.policy_version_id::TEXT,value_policy_current.version_no,
              value_policy_current.status,value_policy_current.required_profile_sections,
              value_policy_current.required_indicators,value_policy_current.allowed_states,
              value_policy_current.projection_version,value_policy_current.rule_version,
              value_policy_current.professionally_approved,value_policy_current.approved_by,
              CASE WHEN value_policy_current.approved_at IS NULL THEN NULL ELSE
                to_char(value_policy_current.approved_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              to_char(value_policy_current.effective_from AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              CASE WHEN value_policy_current.retired_at IS NULL THEN NULL ELSE
                to_char(value_policy_current.retired_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              encode(value_policy_current.policy_digest,'hex'),value_policy_current.digest_key_id,
              to_char(value_policy_current.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
              value_policy_current.author_user_id,value_policy_current.reviewer_user_id,
              value_policy_current.approval_evidence_ref,
              CASE WHEN value_policy_current.approval_package_digest IS NULL THEN NULL ELSE
                encode(value_policy_current.approval_package_digest,'hex') END,
              CASE WHEN value_policy_current.reviewed_content_digest IS NULL THEN NULL ELSE
                encode(value_policy_current.reviewed_content_digest,'hex') END,
              CASE WHEN value_policy_current.reviewed_package_digest IS NULL THEN NULL ELSE
                encode(value_policy_current.reviewed_package_digest,'hex') END,
              CASE WHEN value_policy_current.submitted_at IS NULL THEN NULL ELSE
                to_char(value_policy_current.submitted_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              CASE WHEN value_policy_current.reviewed_at IS NULL THEN NULL ELSE
                to_char(value_policy_current.reviewed_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              CASE WHEN value_policy_current.activated_at IS NULL THEN NULL ELSE
                to_char(value_policy_current.activated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              CASE WHEN value_policy_current.suspended_at IS NULL THEN NULL ELSE
                to_char(value_policy_current.suspended_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') END,
              value_policy_current.row_version
            );
          END IF;

          IF value_receipt_count=1
             AND value_policy_current.row_version>(value_receipt->>'row_version')::BIGINT
          THEN
            value_chain_snapshot=value_receipt->'_policy_postimage';
            value_chain_expected_version=(value_receipt->>'row_version')::BIGINT;
            FOR value_chain_receipt IN
              SELECT i.*,convert_from(i.response_ciphertext,'UTF8')::JSONB AS response
              FROM public.slice4_idempotency i
              WHERE i.scope_ref=value_target_ref
                AND i.operation LIKE 'READINESS_POLICY_%'
                AND (convert_from(i.response_ciphertext,'UTF8')::JSONB->>'row_version')::BIGINT
                    >(value_receipt->>'row_version')::BIGINT
              ORDER BY
                (convert_from(i.response_ciphertext,'UTF8')::JSONB->>'row_version')::BIGINT,
                i.created_at,i.receipt_id
            LOOP
              value_chain_expected_version=value_chain_expected_version+1;
              value_chain_response=value_chain_receipt.response;
              IF (value_chain_response->>'row_version')::BIGINT
                    IS DISTINCT FROM value_chain_expected_version
                 OR value_chain_response->'_policy_preimage'
                    IS DISTINCT FROM value_chain_snapshot
                 OR (CASE value_chain_receipt.operation
                   WHEN 'READINESS_POLICY_UPDATE_DRAFT' THEN
                     value_chain_snapshot->>2 IN ('DRAFT','NEEDS_CORRECTION')
                     AND value_chain_response->'_policy_postimage'->>2='DRAFT'
                   WHEN 'READINESS_POLICY_SUBMIT' THEN
                     value_chain_snapshot->>2='DRAFT'
                     AND value_chain_response->'_policy_postimage'->>2='IN_REVIEW'
                   WHEN 'READINESS_POLICY_REVIEW_APPROVE' THEN
                     value_chain_snapshot->>2='IN_REVIEW'
                     AND value_chain_response->'_policy_postimage'->>2='APPROVED'
                   WHEN 'READINESS_POLICY_REVIEW_CORRECTION' THEN
                     value_chain_snapshot->>2='IN_REVIEW'
                     AND value_chain_response->'_policy_postimage'->>2='NEEDS_CORRECTION'
                   WHEN 'READINESS_POLICY_PUBLISH' THEN
                     value_chain_snapshot->>2='APPROVED'
                     AND value_chain_response->'_policy_postimage'->>2='PUBLISHED'
                   WHEN 'READINESS_POLICY_SUSPEND' THEN
                     value_chain_snapshot->>2='PUBLISHED'
                     AND value_chain_response->'_policy_postimage'->>2='SUSPENDED'
                   WHEN 'READINESS_POLICY_RESUME' THEN
                     value_chain_snapshot->>2='SUSPENDED'
                     AND value_chain_response->'_policy_postimage'->>2='PUBLISHED'
                   WHEN 'READINESS_POLICY_RETIRE' THEN
                     value_chain_snapshot->>2 IN ('PUBLISHED','SUSPENDED')
                     AND value_chain_response->'_policy_postimage'->>2='RETIRED'
                   ELSE FALSE END) IS NOT TRUE
              THEN value_chain_valid=FALSE; EXIT; END IF;

              SELECT x.snapshot,count(*) OVER()
              INTO value_chain_audit,value_chain_audit_count
              FROM (
                SELECT jsonb_build_array(
                  a.audit_id::TEXT,a.event_type,a.aggregate_ref::TEXT,a.actor_user_id,
                  encode(a.event_digest,'hex'),a.digest_key_id,
                  to_char(a.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                ) AS snapshot
                FROM public.slice4_audit a
                WHERE a.audit_id=(value_chain_response->>'_audit_id')::UUID
              ) x;
              SELECT COALESCE(jsonb_agg(jsonb_build_array(
                       o.event_id::TEXT,o.aggregate_type,o.aggregate_ref::TEXT,o.event_type,
                       encode(o.payload_digest,'hex'),o.payload_json,
                       to_char(o.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                     ) ORDER BY o.event_id),'[]'::JSONB),count(*)
              INTO value_chain_targets,value_chain_target_count
              FROM public.slice4_outbox o
              WHERE o.payload_json->>'operation_receipt_id'=
                    value_chain_response->>'operation_receipt_id';
              value_chain_target_digest=encode(sha256(convert_to(
                'ASSESSMENT_READINESS_POLICY_TARGET_SET_V1;targets=V'||
                octet_length(convert_to(value_chain_targets::TEXT,'UTF8'))::TEXT||':'||
                value_chain_targets::TEXT||';','UTF8')),'hex');
              value_chain_receipt_core=jsonb_build_array(
                value_chain_receipt.receipt_id::TEXT,value_chain_receipt.operation,
                value_chain_receipt.scope_ref::TEXT,value_chain_receipt.idempotency_key::TEXT,
                encode(value_chain_receipt.request_digest,'hex'),
                value_chain_receipt.response_key_id,
                to_char(value_chain_receipt.created_at AT TIME ZONE 'UTC',
                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
              );
              value_chain_computed=sha256(convert_to(
                'ASSESSMENT_READINESS_POLICY_POSTIMAGE_V1;'||
                'policy=V'||octet_length(convert_to(
                  (value_chain_response->'_policy_postimage')::TEXT,'UTF8'))::TEXT||':'||
                  (value_chain_response->'_policy_postimage')::TEXT||';'||
                'receipt=V'||octet_length(convert_to(value_chain_receipt_core::TEXT,'UTF8'))::TEXT||':'||
                  value_chain_receipt_core::TEXT||';'||
                'audit=V'||octet_length(convert_to(value_chain_audit::TEXT,'UTF8'))::TEXT||':'||
                  value_chain_audit::TEXT||';'||
                'response=V'||octet_length(convert_to(
                  (value_chain_response-'postimage_digest')::TEXT,'UTF8'))::TEXT||':'||
                  (value_chain_response-'postimage_digest')::TEXT||';'||
                'targets=V'||octet_length(convert_to(value_chain_targets::TEXT,'UTF8'))::TEXT||':'||
                  value_chain_targets::TEXT||';','UTF8'));
              IF COALESCE(value_chain_audit_count,0)<>1
                 OR value_chain_response->>'policy_version_id'
                    IS DISTINCT FROM value_target_ref::TEXT
                 OR value_chain_response->>'operation_receipt_id' IS DISTINCT FROM
                    value_chain_receipt.receipt_id::TEXT
                 OR value_chain_response->>'postimage_digest' IS DISTINCT FROM
                    encode(value_chain_receipt.postimage_digest,'hex')
                 OR value_chain_computed IS DISTINCT FROM value_chain_receipt.postimage_digest
                 OR (value_chain_response->>'target_count')::BIGINT IS DISTINCT FROM
                    value_chain_target_count
                 OR value_chain_response->>'target_set_digest' IS DISTINCT FROM
                    value_chain_target_digest
              THEN value_chain_valid=FALSE; EXIT; END IF;
              value_chain_snapshot=value_chain_response->'_policy_postimage';
              value_chain_count=value_chain_count+1;
            END LOOP;
            IF value_chain_count IS DISTINCT FROM
                 value_policy_current.row_version-(value_receipt->>'row_version')::BIGINT
               OR value_chain_snapshot IS DISTINCT FROM value_policy_current_snapshot
            THEN value_chain_valid=FALSE; END IF;
          END IF;

          IF value_receipt_count=1 AND value_audit_count=1
             AND value_receipt->>'policy_version_id'=value_target_ref::TEXT
             AND value_receipt->>'operation_receipt_id'=value_receipt_id::TEXT
             AND value_receipt->>'postimage_digest'=encode(value_receipt_postimage,'hex')
             AND value_payload->>'postimage_digest'=encode(value_receipt_postimage,'hex')
             AND value_computed_postimage=value_receipt_postimage
             AND (value_receipt->>'target_count')::BIGINT=value_target_trace_count
             AND value_receipt->>'target_set_digest'=value_target_postimage_digest
             AND value_policy_current.row_version>=(value_receipt->>'row_version')::BIGINT
             AND (value_policy_current_snapshot=value_receipt->'_policy_postimage'
                  OR (value_policy_current.row_version>
                        (value_receipt->>'row_version')::BIGINT
                      AND value_chain_valid AND value_chain_count>0))
          THEN RETURN jsonb_build_object('outcome','COMMITTED'); END IF;

          value_computed_preimage=encode(sha256(convert_to(
            'ASSESSMENT_READINESS_POLICY_PREIMAGE_V1;policy=V'||
            octet_length(convert_to((value_payload->'policy_preimage')::TEXT,'UTF8'))::TEXT||':'||
            (value_payload->'policy_preimage')::TEXT||';','UTF8')),'hex');
          IF COALESCE(value_key_count,0)=0 AND COALESCE(value_receipt_count,0)=0
             AND COALESCE(value_audit_count,0)=0 AND value_target_trace_count=0
             AND value_payload->>'preimage_digest'=value_computed_preimage
             AND (
               ((value_payload->>'operation')='CREATE'
                AND value_policy_current.policy_version_id IS NULL
                AND value_payload->'policy_preimage'=jsonb_build_array(
                  'ABSENT',value_payload->>'policy_version_id'))
               OR ((value_payload->>'operation')<>'CREATE'
                   AND value_policy_current.row_version=(value_payload->>'expected_version')::BIGINT
                   AND value_policy_current_snapshot=value_payload->'policy_preimage')
             )
          THEN RETURN jsonb_build_object('outcome','NOT_COMMITTED'); END IF;
          RETURN jsonb_build_object('outcome','UNKNOWN');
        END;
        $fn$;
        """
    )
    op.execute(
        f'ALTER FUNCTION public.readiness_policy_governance_confirm_v1(JSONB) OWNER TO "{owner}"'
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.readiness_policy_governance_confirm_v1(JSONB) FROM PUBLIC"
    )
    op.execute(
        f'GRANT EXECUTE ON FUNCTION public.readiness_policy_governance_confirm_v1(JSONB) TO "{role}"'
    )
    op.execute(
        "CREATE VIEW public.readiness_policy_governance_read_v1 WITH (security_barrier=true) AS "
        "SELECT policy_version_id,version_no,status,required_profile_sections,required_indicators,"
        "allowed_states,projection_version,rule_version,approval_evidence_ref,"
        "CASE WHEN approval_package_digest IS NULL THEN NULL ELSE encode(approval_package_digest,'hex') END "
        "AS approval_package_digest,false AS medical_approval_verified,"
        "encode(policy_digest,'hex') AS policy_digest,"
        "CASE WHEN activated_at IS NULL THEN NULL ELSE effective_from END AS effective_from,"
        "suspended_at,retired_at,row_version FROM public.assessment_readiness_policy_version"
    )
    op.execute("REVOKE ALL ON public.readiness_policy_governance_read_v1 FROM PUBLIC")
    op.execute(f'GRANT SELECT ON public.readiness_policy_governance_read_v1 TO "{role}"')


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    role = _governance_role()
    _add_policy_columns()
    _create_boundaries(role)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    role = _governance_role()
    unsafe = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM public.assessment_readiness_policy_version p "
            "WHERE p.author_user_id IS NOT NULL OR p.reviewer_user_id IS NOT NULL "
            "OR p.row_version<>1 OR p.status IN ('IN_REVIEW','NEEDS_CORRECTION','APPROVED','SUSPENDED')) "
            "OR EXISTS(SELECT 1 FROM public.slice4_idempotency i "
            "WHERE i.operation LIKE 'READINESS_POLICY_%') "
            "OR EXISTS(SELECT 1 FROM public.slice4_audit a "
            "WHERE a.event_type LIKE 'READINESS_POLICY_%') "
            "OR EXISTS(SELECT 1 FROM public.slice4_outbox o "
            "WHERE o.event_type='READINESS_POLICY_CHANGED')"
        )
    ).scalar_one()
    if unsafe:
        raise RuntimeError("ASSESSMENT_READINESS_GOVERNANCE_DOWNGRADE_BLOCKED") from None
    op.execute(f'REVOKE SELECT ON public.readiness_policy_governance_read_v1 FROM "{role}"')
    op.execute("DROP VIEW public.readiness_policy_governance_read_v1")
    op.execute(
        f'REVOKE EXECUTE ON FUNCTION public.readiness_policy_governance_confirm_v1(JSONB) FROM "{role}"'
    )
    op.execute("DROP FUNCTION public.readiness_policy_governance_confirm_v1(JSONB)")
    op.execute(
        f'REVOKE EXECUTE ON FUNCTION public.readiness_policy_governance_v1(VARCHAR,JSONB) FROM "{role}"'
    )
    op.execute("DROP FUNCTION public.readiness_policy_governance_v1(VARCHAR,JSONB)")
    op.drop_index(
        "uq_readiness_policy_propagation_operation_case",
        table_name="slice4_outbox",
        schema="public",
    )
    op.drop_index(
        "uq_assessment_readiness_policy_current",
        table_name="assessment_readiness_policy_version",
        schema="public",
    )
    op.drop_constraint(
        "ck_assessment_readiness_policy_truth",
        "assessment_readiness_policy_version",
        schema="public",
        type_="check",
    )
    for name in (
        "row_version",
        "suspended_at",
        "activated_at",
        "reviewed_at",
        "submitted_at",
        "reviewed_package_digest",
        "reviewed_content_digest",
        "approval_package_digest",
        "approval_evidence_ref",
        "reviewer_user_id",
        "author_user_id",
    ):
        op.drop_column("assessment_readiness_policy_version", name, schema="public")
    op.create_check_constraint(
        "ck_assessment_readiness_policy_truth",
        "assessment_readiness_policy_version",
        "status IN ('DRAFT','PUBLISHED','RETIRED') AND projection_version>=2",
        schema="public",
    )
    op.create_index(
        "uq_assessment_readiness_policy_current",
        "assessment_readiness_policy_version",
        ["status"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status='PUBLISHED'"),
    )
