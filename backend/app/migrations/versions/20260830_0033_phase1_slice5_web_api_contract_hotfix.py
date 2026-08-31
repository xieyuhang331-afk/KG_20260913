"""Phase 1 Slice 5 Web API contract compatibility hotfix.

Revision ID: 20260830_0033
Revises: 20260827_0032
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260830_0033"
down_revision = "20260827_0032"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052315115572212033
_IDENTITIES = (
    ("KG_SLICE5_ASSESSMENT_WRITER_ROLE", "KG_SLICE5_ASSESSMENT_WRITER_DATABASE_URL"),
    ("KG_SLICE5_RISK_WORKFLOW_WRITER_ROLE", "KG_SLICE5_RISK_WORKFLOW_WRITER_DATABASE_URL"),
    ("KG_SLICE5_RULE_GOVERNANCE_WRITER_ROLE", "KG_SLICE5_RULE_GOVERNANCE_WRITER_DATABASE_URL"),
    ("KG_SLICE5_WORKFLOW_WORKER_ROLE", "KG_SLICE5_WORKFLOW_WORKER_DATABASE_URL"),
    ("KG_SLICE5_CLINICAL_READER_ROLE", "KG_SLICE5_CLINICAL_READER_DATABASE_URL"),
    ("KG_SLICE5_OVERSIGHT_READER_ROLE", "KG_SLICE5_OVERSIGHT_READER_DATABASE_URL"),
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 5 hotfix database role configuration is invalid") from None


def _roles() -> tuple[str, str, str, str, str, str]:
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


def _create_governance_function(rule_role: str) -> None:
    owner = str(op.get_bind().execute(sa.text("SELECT current_user")).scalar_one())
    op.execute(
        f"""
        CREATE FUNCTION public.slice5_rule_governance_v2(value_operation VARCHAR,value_payload JSONB)
        RETURNS JSONB
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
          IF session_user<>'{rule_role}' THEN
            RAISE EXCEPTION 'SLICE5_RULE_WRITER_FORBIDDEN';
          END IF;
          IF value_payload->>'rule_set_code'<>'CN_ADULT_BASELINE_V1' THEN
            RAISE EXCEPTION 'RULE_SET_INVALID';
          END IF;
          IF value_operation='REVIEW_APPROVE' AND value_payload->>'reason_code'<>'MEDICAL_CONTENT_APPROVED' THEN
            RAISE EXCEPTION 'RULE_REASON_INVALID';
          ELSIF value_operation='REVIEW_CORRECTION' AND value_payload->>'reason_code' NOT IN (
            'RULE_CONTENT_CORRECTION_REQUIRED','MEDICAL_EVIDENCE_CORRECTION_REQUIRED',
            'GOLDEN_CASE_CORRECTION_REQUIRED','HIGH_RISK_SAFETY_CORRECTION_REQUIRED') THEN
            RAISE EXCEPTION 'RULE_REASON_INVALID';
          ELSIF value_operation='PUBLISH' AND value_payload->>'reason_code'<>'DOUBLE_SIGNED_BASELINE_RELEASE' THEN
            RAISE EXCEPTION 'RULE_REASON_INVALID';
          ELSIF value_operation='SUSPEND' AND value_payload->>'reason_code' NOT IN (
            'MEDICAL_SAFETY_REVIEW_REQUIRED','APPROVAL_EVIDENCE_INVALIDATED',
            'RULE_IMPLEMENTATION_DEFECT_CONFIRMED') THEN
            RAISE EXCEPTION 'RULE_REASON_INVALID';
          ELSIF value_operation='RESUME' AND value_payload->>'reason_code' NOT IN (
            'MEDICAL_SAFETY_REVIEW_CLEARED','APPROVAL_EVIDENCE_REVALIDATED',
            'RULE_IMPLEMENTATION_DEFECT_REMEDIATED') THEN
            RAISE EXCEPTION 'RULE_REASON_INVALID';
          ELSIF value_operation='RETIRE' AND value_payload->>'reason_code' NOT IN (
            'SUPERSEDED_BY_APPROVED_VERSION','BASELINE_WITHDRAWN') THEN
            RAISE EXCEPTION 'RULE_REASON_INVALID';
          END IF;
          IF value_operation<>'UPDATE_DRAFT' THEN
            RETURN public.slice5_rule_governance_v1(value_operation,value_payload);
          END IF;
          PERFORM 1 FROM public."user" u
          WHERE u.id=(value_payload->>'actor_user_id')::bigint
            AND u.status='active' AND u.role='expert'
          FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'RULE_GOVERNANCE_FORBIDDEN'; END IF;
          IF jsonb_typeof(value_payload->'typed_rule_payload')<>'object'
             OR value_payload->'typed_rule_payload'->>'schema_version'<>'SLICE5_MEDICAL_RULE_PAYLOAD_V1'
             OR value_payload->'typed_rule_payload'->>'rule_set_code'<>'CN_ADULT_BASELINE_V1'
             OR jsonb_typeof(value_payload->'typed_rule_payload'->'modules')<>'array'
             OR jsonb_array_length(value_payload->'typed_rule_payload'->'modules')<>4
             OR EXISTS(
               SELECT 1
               FROM jsonb_array_elements(value_payload->'typed_rule_payload'->'modules') m,
                    jsonb_array_elements(m->'deferred_rules') d
               WHERE NOT (d ? 'enabled') OR d->'enabled'<>'false'::jsonb
             )
          THEN RAISE EXCEPTION 'SLICE5_INVALID_PAYLOAD'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_payload->>'rule_set_version_id',0));
          IF EXISTS(
            SELECT 1 FROM public.slice5_idempotency i
            WHERE i.actor_scope=value_payload->>'actor_user_id'
              AND i.operation='RULE_UPDATE_DRAFT'
              AND i.target_id=(value_payload->>'rule_set_version_id')::uuid
              AND i.idempotency_key=value_payload->>'idempotency_key'
          ) THEN
            IF NOT EXISTS(
              SELECT 1 FROM public.slice5_idempotency i
              WHERE i.actor_scope=value_payload->>'actor_user_id'
                AND i.operation='RULE_UPDATE_DRAFT'
                AND i.target_id=(value_payload->>'rule_set_version_id')::uuid
                AND i.idempotency_key=value_payload->>'idempotency_key'
                AND i.request_digest=decode(value_payload->>'request_digest','hex')
                AND (SELECT count(*) FROM public.slice5_audit a
                     WHERE a.audit_id=(value_payload->>'audit_id')::uuid
                       AND a.target_id=i.target_id
                       AND a.evidence_digest=decode(value_payload->>'evidence_digest','hex'))=1
                AND (SELECT count(*) FROM public.slice5_outbox o
                     WHERE o.event_id=(value_payload->>'event_id')::uuid
                       AND o.aggregate_ref=i.target_id
                       AND o.payload_digest=decode(value_payload->>'outbox_digest','hex'))=1
            ) THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF;
            RETURN (
              SELECT convert_from(i.response_ciphertext,'UTF8')::jsonb
              FROM public.slice5_idempotency i
              WHERE i.actor_scope=value_payload->>'actor_user_id'
                AND i.operation='RULE_UPDATE_DRAFT'
                AND i.target_id=(value_payload->>'rule_set_version_id')::uuid
                AND i.idempotency_key=value_payload->>'idempotency_key'
            );
          END IF;
          UPDATE public.assessment_rule_set_version
          SET typed_rule_payload=value_payload->'typed_rule_payload',
              content_digest=decode(value_payload->>'content_digest','hex'),
              approval_evidence_ref=value_payload->>'approval_evidence_ref',
              version=version+1
          WHERE rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid
            AND author_user_id=(value_payload->>'actor_user_id')::bigint
            AND status IN ('DRAFT','NEEDS_CORRECTION')
            AND version=(value_payload->>'expected_version')::bigint;
          IF NOT FOUND THEN RAISE EXCEPTION 'RULE_STATE_CONFLICT'; END IF;
          INSERT INTO public.slice5_audit VALUES(
            (value_payload->>'audit_id')::uuid,'RULE_UPDATE_DRAFT',
            (value_payload->>'actor_user_id')::bigint,value_payload->>'actor_role',
            'RULE_SET',(value_payload->>'rule_set_version_id')::uuid,
            decode(value_payload->>'evidence_digest','hex'),
            (value_payload->>'created_at')::timestamptz
          );
          INSERT INTO public.slice5_outbox VALUES(
            (value_payload->>'event_id')::uuid,'RULE_SET',
            (value_payload->>'rule_set_version_id')::uuid,'RULE_UPDATE_DRAFT',
            decode(value_payload->>'outbox_digest','hex'),
            jsonb_build_object('rule_set_version_id',value_payload->>'rule_set_version_id','operation',value_operation),
            'PENDING',0,NULL,NULL,(value_payload->>'created_at')::timestamptz,NULL
          );
          INSERT INTO public.slice5_idempotency VALUES(
            (value_payload->>'receipt_id')::uuid,value_payload->>'actor_user_id','RULE_UPDATE_DRAFT',
            (value_payload->>'rule_set_version_id')::uuid,value_payload->>'idempotency_key',
            decode(value_payload->>'request_digest','hex'),
            convert_to((value_payload->'response')::text,'UTF8'),value_payload->>'digest_key_id',
            decode(value_payload->>'postimage_digest','hex'),
            (value_payload->>'created_at')::timestamptz
          );
          RETURN value_payload->'response';
        END
        $fn$
        """
    )
    op.execute(f'ALTER FUNCTION public.slice5_rule_governance_v2(VARCHAR,JSONB) OWNER TO "{owner}"')
    op.execute("REVOKE ALL ON FUNCTION public.slice5_rule_governance_v2(VARCHAR,JSONB) FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice5_rule_governance_v2(VARCHAR,JSONB) TO "{rule_role}"')
    op.execute(
        f"""
        CREATE FUNCTION public.slice5_rule_governance_confirm_v1(value_payload JSONB)
        RETURNS JSONB
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        DECLARE value_receipt BIGINT; value_audit BIGINT; value_outbox BIGINT; value_rule BIGINT;
        DECLARE value_receipt_exists BIGINT; value_audit_exists BIGINT; value_outbox_exists BIGINT;
        DECLARE value_row_version BIGINT; value_row_status VARCHAR; value_expected_version BIGINT;
        BEGIN
          IF session_user<>'{rule_role}' THEN
            RAISE EXCEPTION 'SLICE5_RULE_WRITER_FORBIDDEN';
          END IF;
          SELECT count(*) INTO value_receipt FROM public.slice5_idempotency i
          WHERE i.receipt_id=(value_payload->>'receipt_id')::uuid
            AND i.actor_scope=value_payload->>'actor_user_id'
            AND i.operation='RULE_'||(value_payload->>'operation')
            AND i.target_id=(value_payload->>'rule_set_version_id')::uuid
            AND i.idempotency_key=value_payload->>'idempotency_key'
            AND i.request_digest=decode(value_payload->>'request_digest','hex')
            AND i.postimage_digest=decode(value_payload->>'postimage_digest','hex')
            AND i.response_key_id=value_payload->>'digest_key_id'
            AND convert_from(i.response_ciphertext,'UTF8')::jsonb=value_payload->'response'
            AND (value_payload->>'operation'<>'CREATE'
                 OR i.created_at=(value_payload->>'created_at')::timestamptz);
          SELECT count(*) INTO value_receipt_exists FROM public.slice5_idempotency i
          WHERE i.receipt_id=(value_payload->>'receipt_id')::uuid;
          SELECT count(*) INTO value_audit FROM public.slice5_audit a
          WHERE a.audit_id=(value_payload->>'audit_id')::uuid
            AND a.action='RULE_'||(value_payload->>'operation')
            AND a.actor_user_id=(value_payload->>'actor_user_id')::bigint
            AND a.actor_role=value_payload->>'actor_role'
            AND a.target_type='RULE_SET'
            AND a.target_id=(value_payload->>'rule_set_version_id')::uuid
            AND a.evidence_digest=decode(value_payload->>'evidence_digest','hex')
            AND (value_payload->>'operation'<>'CREATE'
                 OR a.occurred_at=(value_payload->>'created_at')::timestamptz);
          SELECT count(*) INTO value_audit_exists FROM public.slice5_audit a
          WHERE a.audit_id=(value_payload->>'audit_id')::uuid;
          SELECT count(*) INTO value_outbox FROM public.slice5_outbox o
          WHERE o.event_id=(value_payload->>'event_id')::uuid
            AND o.aggregate_type='RULE_SET'
            AND o.aggregate_ref=(value_payload->>'rule_set_version_id')::uuid
            AND o.event_type='RULE_'||(value_payload->>'operation')
            AND o.payload_digest=decode(value_payload->>'outbox_digest','hex')
            AND o.payload_json=jsonb_build_object(
              'rule_set_version_id',value_payload->>'rule_set_version_id',
              'operation',value_payload->>'operation'
            )
            AND (value_payload->>'operation'<>'CREATE'
                 OR o.created_at=(value_payload->>'created_at')::timestamptz);
          SELECT count(*) INTO value_outbox_exists FROM public.slice5_outbox o
          WHERE o.event_id=(value_payload->>'event_id')::uuid;
          SELECT r.version,r.status INTO value_row_version,value_row_status
          FROM public.assessment_rule_set_version r
          WHERE r.rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid;
          value_expected_version=(value_payload->'response'->>'version')::bigint;
          IF value_payload->>'operation'='CREATE' THEN
            SELECT count(*) INTO value_rule
            FROM public.assessment_rule_set_version r
            WHERE r.rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid
              AND r.rule_set_code=value_payload->>'rule_set_code'
              AND r.version_no=(value_payload->>'version_no')::bigint
              AND r.author_user_id=(value_payload->>'author_user_id')::bigint
              AND r.status='DRAFT' AND r.version=1
              AND r.typed_rule_payload=value_payload->'typed_rule_payload'
              AND r.content_digest=decode(value_payload->>'content_digest','hex')
              AND r.digest_key_id='SHA256_V1'
              AND r.reviewer_user_id IS NULL
              AND r.approval_evidence_ref IS NOT DISTINCT FROM value_payload->>'approval_evidence_ref'
              AND r.effective_from IS NULL
              AND r.suspended_at IS NULL
              AND r.retired_at IS NULL
              AND r.created_at=(value_payload->>'created_at')::timestamptz;
          ELSE
            SELECT count(*) INTO value_rule
            FROM public.assessment_rule_set_version r
            WHERE r.rule_set_version_id=(value_payload->>'rule_set_version_id')::uuid
              AND r.version=value_expected_version
              AND r.status=value_payload->'response'->>'status'
              AND (
                value_payload->>'operation'<>'UPDATE_DRAFT'
                OR (
                  r.typed_rule_payload=value_payload->'response'->'typed_rule_payload'
                  AND r.approval_evidence_ref IS NOT DISTINCT FROM value_payload->'response'->>'approval_evidence_ref'
                )
              );
          END IF;
          IF value_receipt=1 AND value_audit=1 AND value_outbox=1 AND value_rule=1
          THEN RETURN jsonb_build_object('outcome','COMMITTED'); END IF;
          IF value_receipt_exists=0 AND value_audit_exists=0 AND value_outbox_exists=0
             AND (
               (value_payload->>'operation'='CREATE' AND value_row_version IS NULL)
               OR (value_payload->>'operation'<>'CREATE'
                   AND value_row_version=(value_payload->>'expected_version')::bigint)
             )
          THEN RETURN jsonb_build_object('outcome','NOT_COMMITTED'); END IF;
          RETURN jsonb_build_object('outcome','UNKNOWN');
        END
        $fn$
        """
    )
    op.execute(f'ALTER FUNCTION public.slice5_rule_governance_confirm_v1(JSONB) OWNER TO "{owner}"')
    op.execute("REVOKE ALL ON FUNCTION public.slice5_rule_governance_confirm_v1(JSONB) FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice5_rule_governance_confirm_v1(JSONB) TO "{rule_role}"')


def _create_safe_views(rule_role: str, clinical_role: str, oversight_role: str) -> None:
    op.execute(
        "CREATE VIEW public.slice5_rule_set_governance_read_v2 WITH (security_barrier=true) AS "
        "SELECT r.rule_set_version_id,r.rule_set_code,r.version_no,r.status,r.typed_rule_payload,"
        "r.author_user_id,au.role::text AS author_role,r.reviewer_user_id,ru.role::text AS reviewer_role,"
        "r.approval_evidence_ref,r.effective_from,r.suspended_at,r.retired_at,r.created_at,r.version "
        "FROM public.assessment_rule_set_version r "
        "JOIN public.\"user\" au ON au.id=r.author_user_id AND au.role='expert' "
        "LEFT JOIN public.\"user\" ru ON ru.id=r.reviewer_user_id AND ru.role='expert'"
    )
    op.execute("REVOKE ALL ON public.slice5_rule_set_governance_read_v2 FROM PUBLIC")
    op.execute(f'GRANT SELECT ON public.slice5_rule_set_governance_read_v2 TO "{rule_role}"')
    op.execute(
        "CREATE VIEW public.slice5_high_risk_task_read_v2 WITH (security_barrier=true) AS "
        "SELECT t.task_id,t.assessment_id,t.service_case_id,t.tenant_id,t.status,t.reason_module_codes,"
        "t.assigned_actor_id AS assignee_user_id,u.role::text AS assignee_role,"
        "CASE WHEN u.role='therapist' AND p.status='APPROVED_ACTIVE' THEN p.display_name ELSE NULL END AS assignee_display_name,"
        "t.due_at,(SELECT max(a.occurred_at) FROM public.high_risk_task_action a WHERE a.task_id=t.task_id) AS last_action_at,"
        "jsonb_build_object('ordinary_plan',true,'case_completion',true) AS blocking,t.version,t.created_at,t.closed_at "
        "FROM public.high_risk_task t LEFT JOIN public.\"user\" u ON u.id=t.assigned_actor_id AND u.status='active' "
        "LEFT JOIN public.therapist_profile p ON p.user_id=u.id"
    )
    op.execute("REVOKE ALL ON public.slice5_high_risk_task_read_v2 FROM PUBLIC")
    op.execute(f'GRANT SELECT ON public.slice5_high_risk_task_read_v2 TO "{clinical_role}"')
    op.execute(f'GRANT SELECT ON public.slice5_high_risk_task_read_v2 TO "{oversight_role}"')


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    _, _, rule_role, _, clinical_role, oversight_role = _roles()
    _create_governance_function(rule_role)
    _create_safe_views(rule_role, clinical_role, oversight_role)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    _, _, rule_role, _, clinical_role, oversight_role = _roles()
    op.execute(f'REVOKE SELECT ON public.slice5_high_risk_task_read_v2 FROM "{clinical_role}"')
    op.execute(f'REVOKE SELECT ON public.slice5_high_risk_task_read_v2 FROM "{oversight_role}"')
    op.execute("DROP VIEW public.slice5_high_risk_task_read_v2")
    op.execute(f'REVOKE SELECT ON public.slice5_rule_set_governance_read_v2 FROM "{rule_role}"')
    op.execute("DROP VIEW public.slice5_rule_set_governance_read_v2")
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.slice5_rule_governance_v2(VARCHAR,JSONB) FROM "{rule_role}"')
    op.execute(f'REVOKE EXECUTE ON FUNCTION public.slice5_rule_governance_confirm_v1(JSONB) FROM "{rule_role}"')
    op.execute("DROP FUNCTION public.slice5_rule_governance_confirm_v1(JSONB)")
    op.execute("DROP FUNCTION public.slice5_rule_governance_v2(VARCHAR,JSONB)")
