"""Phase 1 Slice 6 deterministic health plan generation and review.

Revision ID: 20260826_0031
Revises: 20260825_0030
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260826_0031"
down_revision = "20260825_0030"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212031
_ELIGIBILITY_CODES = (
    "HIGH_RISK_BLOCKING",
    "CONSENT_NOT_CURRENT",
    "PRIMARY_THERAPIST_NOT_CURRENT",
)
_IDENTITIES = (
    ("KG_SLICE6_INSTITUTION_WRITER_ROLE", "KG_SLICE6_INSTITUTION_WRITER_DATABASE_URL"),
    ("KG_SLICE6_TEMPLATE_WRITER_ROLE", "KG_SLICE6_TEMPLATE_WRITER_DATABASE_URL"),
    ("KG_SLICE6_REVIEW_WRITER_ROLE", "KG_SLICE6_REVIEW_WRITER_DATABASE_URL"),
    ("KG_SLICE6_WORKFLOW_WORKER_ROLE", "KG_SLICE6_WORKFLOW_WORKER_DATABASE_URL"),
    ("KG_SLICE6_CLINICAL_READER_ROLE", "KG_SLICE6_CLINICAL_READER_DATABASE_URL"),
    ("KG_SLICE6_FAMILY_READER_ROLE", "KG_SLICE6_FAMILY_READER_DATABASE_URL"),
)
_TABLES = (
    "health_plan_template_version",
    "health_plan_generation_request",
    "health_plan_version",
    "health_plan_review",
    "health_plan_explanation",
    "health_plan_user_decision",
    "health_plan_receipt",
    "health_plan_audit",
    "health_plan_outbox",
    "health_plan_delivery",
)
_VIEWS = (
    "slice6_template_governance_read_v1",
    "slice6_generation_read_v1",
    "slice6_plan_read_v1",
    "slice6_review_read_v1",
)
_FUNCTIONS = (
    ("slice6_generation_authority_v1", "UUID,BIGINT,VARCHAR"),
    ("slice6_mutation_replay_v1", "BIGINT,VARCHAR,VARCHAR,BYTEA"),
    ("slice6_mutation_expected_v1", "JSONB"),
    ("slice6_mutation_confirm_v1", "JSONB"),
    ("slice6_generation_request_v1", "JSONB"),
    ("slice6_generation_worker_v1", "JSONB"),
    ("slice6_generation_input_v1", "UUID"),
    ("slice6_generation_complete_v1", "JSONB"),
    ("slice6_review_transition_v1", "JSONB"),
    ("slice6_plan_explanation_v1", "JSONB"),
    ("slice6_user_decision_v1", "JSONB"),
    ("slice6_template_governance_v1", "JSONB"),
    ("slice6_case_read_authority_v1", "BIGINT,VARCHAR,UUID"),
    ("slice6_actor_read_authority_v1", "BIGINT,VARCHAR,UUID,UUID,BIGINT"),
    ("slice6_outbox_claim_v1", "UUID,BIGINT"),
    ("slice6_outbox_consume_v1", "JSONB"),
    ("slice6_outbox_recover_v1", "TIMESTAMP WITH TIME ZONE"),
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 6 database role configuration is invalid") from None


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


def _uuid(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=False), nullable=nullable)


def _ts(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _create_tables() -> None:
    op.create_table(
        "health_plan_template_version",
        _uuid("template_version_id"),
        sa.Column("template_code", sa.String(64), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False),
        sa.Column("medical_approval_ref", sa.String(256), nullable=False),
        sa.Column("author_user_id", sa.BigInteger(), nullable=False),
        _ts("created_at"),
        _ts("published_at", nullable=True),
        _ts("retired_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("template_version_id", name="pk_health_plan_template_version"),
        sa.UniqueConstraint("template_code", "version_no", name="uq_health_plan_template_version"),
        sa.UniqueConstraint("template_code", "content_digest", name="uq_health_plan_template_content"),
        sa.CheckConstraint(
            "status IN ('DRAFT','PUBLISHED','RETIRED') AND version_no>=1 AND version>=1",
            name="ck_health_plan_template_truth",
        ),
        schema="public",
    )
    op.create_index(
        "uq_health_plan_template_published",
        "health_plan_template_version",
        ["template_code"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status='PUBLISHED'"),
    )
    op.create_table(
        "health_plan_generation_request",
        _uuid("request_id"),
        _uuid("service_case_id"),
        _uuid("subject_member_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        _uuid("assessment_id"),
        sa.Column("assessment_version", sa.BigInteger(), nullable=False),
        _uuid("assembly_id"),
        _uuid("template_version_id"),
        sa.Column("template_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        _uuid("current_plan_id", nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("authority_digest", sa.LargeBinary(), nullable=False),
        sa.Column("initiated_by", sa.BigInteger(), nullable=False),
        sa.Column("initiated_role", sa.String(32), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        _ts("lease_until", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("request_id", name="pk_health_plan_generation_request"),
        sa.UniqueConstraint("request_id", "service_case_id", name="uq_health_plan_generation_request_case"),
        sa.ForeignKeyConstraint(["service_case_id"], ["public.service_case.case_id"], name="fk_health_plan_generation_case"),
        sa.ForeignKeyConstraint(["assessment_id"], ["public.health_assessment.assessment_id"], name="fk_health_plan_generation_assessment"),
        sa.ForeignKeyConstraint(["assembly_id"], ["public.assessment_input_assembly.assembly_id"], name="fk_health_plan_generation_assembly"),
        sa.ForeignKeyConstraint(["template_version_id"], ["public.health_plan_template_version.template_version_id"], name="fk_health_plan_generation_template"),
        sa.CheckConstraint(
            "status IN ('REQUESTED','GENERATING','GENERATION_FAILED','IN_REVIEW','NEEDS_CORRECTION','USER_DECISION_PENDING','NEEDS_EXPLANATION','DECLINED','REJECTED','ACTIVE','SUPERSEDED') AND version>=1",
            name="ck_health_plan_generation_truth",
        ),
        schema="public",
    )
    op.create_index(
        "uq_health_plan_generation_active_case",
        "health_plan_generation_request",
        ["service_case_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status IN ('REQUESTED','GENERATING','IN_REVIEW','NEEDS_CORRECTION','USER_DECISION_PENDING','NEEDS_EXPLANATION','ACTIVE')"),
    )
    op.create_table(
        "health_plan_version",
        _uuid("plan_id"),
        _uuid("request_id"),
        _uuid("service_case_id"),
        _uuid("subject_member_id"),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.BigInteger(), nullable=False),
        _uuid("template_version_id"),
        _uuid("assessment_id"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.LargeBinary(), nullable=False),
        _uuid("supersedes_plan_id", nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("plan_id", name="pk_health_plan_version"),
        sa.UniqueConstraint("request_id", "version_no", name="uq_health_plan_request_version"),
        sa.UniqueConstraint("plan_id", "service_case_id", name="uq_health_plan_case"),
        sa.ForeignKeyConstraint(
            ["request_id", "service_case_id"],
            ["public.health_plan_generation_request.request_id", "public.health_plan_generation_request.service_case_id"],
            name="fk_health_plan_request_scope",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["supersedes_plan_id"], ["public.health_plan_version.plan_id"], name="fk_health_plan_supersedes"),
        sa.CheckConstraint(
            "status IN ('IN_REVIEW','USER_DECISION_PENDING','NEEDS_EXPLANATION','DECLINED','REJECTED','ACTIVE','SUPERSEDED') AND version_no>=1 AND version>=1",
            name="ck_health_plan_version_truth",
        ),
        schema="public",
    )
    op.create_index(
        "uq_health_plan_active_case",
        "health_plan_version",
        ["service_case_id"],
        unique=True,
        schema="public",
        postgresql_where=sa.text("status='ACTIVE'"),
    )
    op.create_table(
        "health_plan_review",
        _uuid("review_id"),
        _uuid("request_id"),
        _uuid("plan_id"),
        _uuid("service_case_id"),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("claimed_by", sa.BigInteger(), nullable=True),
        _ts("claim_until", nullable=True),
        sa.Column("decision_codes", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
        _ts("claimed_at", nullable=True),
        _ts("decided_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("review_id", name="pk_health_plan_review"),
        sa.UniqueConstraint("plan_id", name="uq_health_plan_review_plan"),
        sa.ForeignKeyConstraint(["plan_id", "service_case_id"], ["public.health_plan_version.plan_id", "public.health_plan_version.service_case_id"], name="fk_health_plan_review_scope"),
        sa.CheckConstraint("status IN ('PENDING','CLAIMED','APPROVED','NEEDS_CORRECTION','REJECTED') AND version>=1", name="ck_health_plan_review_truth"),
        schema="public",
    )
    op.create_table(
        "health_plan_explanation",
        _uuid("explanation_id"),
        _uuid("plan_id"),
        _uuid("service_case_id"),
        sa.Column("therapist_user_id", sa.BigInteger(), nullable=False),
        sa.Column("explanation_codes", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("explanation_id", name="pk_health_plan_explanation"),
        sa.ForeignKeyConstraint(["plan_id", "service_case_id"], ["public.health_plan_version.plan_id", "public.health_plan_version.service_case_id"], name="fk_health_plan_explanation_scope"),
        schema="public",
    )
    op.create_table(
        "health_plan_user_decision",
        _uuid("decision_id"),
        _uuid("plan_id"),
        _uuid("service_case_id"),
        _uuid("subject_member_id"),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_context", sa.String(32), nullable=False),
        _uuid("proxy_grant_id", nullable=True),
        sa.Column("decision", sa.String(24), nullable=False),
        _ts("created_at"),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("decision_id", name="pk_health_plan_user_decision"),
        sa.UniqueConstraint("plan_id", "decision_id", name="uq_health_plan_user_decision_plan"),
        sa.ForeignKeyConstraint(["plan_id", "service_case_id"], ["public.health_plan_version.plan_id", "public.health_plan_version.service_case_id"], name="fk_health_plan_user_decision_scope"),
        sa.CheckConstraint("decision IN ('ACCEPT','NEEDS_EXPLANATION','DECLINE') AND version>=1", name="ck_health_plan_user_decision_truth"),
        schema="public",
    )
    op.create_table(
        "health_plan_receipt",
        _uuid("receipt_id"),
        sa.Column("actor_scope", sa.String(160), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        _uuid("target_id"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.LargeBinary(), nullable=False),
        sa.Column("response_json", postgresql.JSONB(), nullable=False),
        sa.Column("postimage_digest", sa.LargeBinary(), nullable=False),
        sa.Column("expected_confirmed_digest", sa.String(64), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("receipt_id", name="pk_health_plan_receipt"),
        sa.UniqueConstraint("actor_scope", "operation", "idempotency_key", name="uq_health_plan_receipt_operation"),
        sa.CheckConstraint(
            "expected_confirmed_digest ~ '^[0-9a-f]{64}$'",
            name="ck_health_plan_receipt_confirmed_digest",
        ),
        schema="public",
    )
    op.create_table(
        "health_plan_audit",
        _uuid("audit_id"),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        _uuid("target_id"),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("evidence_digest", sa.LargeBinary(), nullable=False),
        _ts("occurred_at"),
        sa.PrimaryKeyConstraint("audit_id", name="pk_health_plan_audit"),
        schema="public",
    )
    op.create_table(
        "health_plan_outbox",
        _uuid("event_id"),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        _uuid("aggregate_ref"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("payload_digest", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.BigInteger(), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        _ts("lease_until", nullable=True),
        _ts("created_at"),
        _ts("delivered_at", nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_health_plan_outbox"),
        sa.CheckConstraint("status IN ('PENDING','PROCESSING','DELIVERED','FAILED') AND attempts>=0 AND version>=1", name="ck_health_plan_outbox_truth"),
        schema="public",
    )
    op.create_table(
        "health_plan_delivery",
        _uuid("delivery_id"),
        _uuid("event_id"),
        sa.Column("target_type", sa.String(32), nullable=False),
        _uuid("target_ref"),
        sa.Column("target_digest", sa.LargeBinary(), nullable=False),
        _ts("delivered_at"),
        sa.PrimaryKeyConstraint("delivery_id", name="pk_health_plan_delivery"),
        sa.UniqueConstraint("event_id", "target_type", "target_ref", name="uq_health_plan_delivery_target"),
        sa.ForeignKeyConstraint(["event_id"], ["public.health_plan_outbox.event_id"], name="fk_health_plan_delivery_event"),
        schema="public",
    )


def _function(name: str, arguments: str, returns: str, body: str, roles: tuple[str, ...], *, declarations: str = "") -> None:
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


def _create_functions(institution: str, template: str, review: str, worker: str, clinical: str, family: str) -> None:
    _function(
        "slice6_generation_authority_v1",
        "value_case UUID,value_actor BIGINT,value_role VARCHAR",
        "JSONB",
        f"IF session_user <> '{institution}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value_case::text,6)); "
        "SELECT jsonb_build_object("
        "'service_case_id',c.case_id,'subject_member_id',c.subject_member_id,'tenant_id',c.tenant_id,"
        "'service_case_version',c.version,'tenant_service_ready',sr.readiness_status='SERVICE_READY',"
        "'service_case_current',c.status='PREPARING' AND e.status='CASE_CREATED' AND e.service_case_id=c.case_id,"
        "'consent_current',NOT EXISTS(SELECT 1 FROM unnest(CASE WHEN e.mode='PROXY_ELDER' "
        "THEN ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK','PROXY_AUTHORIZATION']::text[] "
        "ELSE ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']::text[] END) required(document_type) "
        "WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id "
        "WHERE cr.enrollment_id=c.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED' AND cd.status='PUBLISHED')) "
        "AND (e.mode='SELF' OR EXISTS(SELECT 1 FROM public.proxy_grant pg WHERE pg.enrollment_id=e.enrollment_id "
        "AND pg.principal_member_id=e.subject_member_id AND pg.proxy_member_id=e.proxy_member_id AND pg.status='ACTIVE' "
        "AND pg.valid_from<=clock_timestamp() AND (pg.valid_until IS NULL OR pg.valid_until>clock_timestamp()) "
        "AND pg.permission_codes @> '[\"IDENTITY_SUBMIT\",\"CONSENT_ACCEPT\",\"DAILY_VIEW\",\"DAILY_INPUT\",\"REPORT_UPLOAD\"]'::jsonb)),"
        "'primary_therapist_current',pa.status='ACCEPTED' AND pa.assignment_id=c.assignment_id "
        "AND pa.therapist_id=c.primary_therapist_id AND tp.status='APPROVED_ACTIVE' "
        "AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE "
        "AND pa.service_scope_tags=c.service_scope_tags AND c.service_scope_tags<@tp.service_tags,"
        "'assembly_ready',a.status='ASSESSMENT_READY' AND rp.current_assembly_id=a.assembly_id,"
        "'assembly_id',a.assembly_id,'assessment_id',ha.assessment_id,'assessment_version',ha.version,"
        "'assessment_completed',ha.status='COMPLETED','assessment_disputed',EXISTS(SELECT 1 FROM public.assessment_dispute d WHERE d.assessment_id=ha.assessment_id AND d.status='OPEN'),"
        "'assessment_superseded',ha.status='SUPERSEDED','high_risk_blocking',NOT (ha.status='COMPLETED' "
        "AND ha.overall_risk IN ('WITHIN_RANGE','ATTENTION') "
        "AND EXISTS(SELECT 1 FROM public.assessment_module_result mr WHERE mr.assessment_id=ha.assessment_id AND mr.risk_level<>'NOT_ASSESSED') "
        "AND NOT EXISTS(SELECT 1 FROM public.high_risk_task ht WHERE ht.service_case_id=c.case_id AND ht.status IN ('OPEN','CLAIMED','ESCALATED')) "
        "AND NOT EXISTS(SELECT 1 FROM public.health_assessment blocked WHERE blocked.service_case_id=c.case_id "
        "AND blocked.overall_risk='HIGH_RISK' AND NOT EXISTS(SELECT 1 FROM public.health_assessment cleared "
        "WHERE cleared.service_case_id=blocked.service_case_id AND cleared.sequence_no>blocked.sequence_no "
        "AND cleared.status='COMPLETED' AND cleared.overall_risk IN ('WITHIN_RANGE','ATTENTION')))),"
        "'published_template_available',tv.template_version_id IS NOT NULL,'template_version_id',tv.template_version_id,'template_version',tv.version_no,"
        "'active_generation_exists',gr.request_id IS NOT NULL,'active_generation_request_id',gr.request_id,"
        "'active_plan_conflict',pv.plan_id IS NOT NULL,'active_plan_id',pv.plan_id) INTO value_result "
        "FROM public.service_case c JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id "
        "JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id "
        "JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id "
        "JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id "
        "LEFT JOIN public.assessment_readiness_case_pointer rp ON rp.service_case_id=c.case_id "
        "LEFT JOIN public.assessment_input_assembly a ON a.assembly_id=rp.current_assembly_id "
        "LEFT JOIN LATERAL (SELECT h.* FROM public.health_assessment h WHERE h.service_case_id=c.case_id ORDER BY h.sequence_no DESC LIMIT 1) ha ON true "
        "LEFT JOIN LATERAL (SELECT t.* FROM public.health_plan_template_version t WHERE t.status='PUBLISHED' ORDER BY t.version_no DESC LIMIT 1) tv ON true "
        "LEFT JOIN public.health_plan_generation_request gr ON gr.service_case_id=c.case_id AND gr.status IN ('REQUESTED','GENERATING','IN_REVIEW','NEEDS_CORRECTION','USER_DECISION_PENDING','NEEDS_EXPLANATION','ACTIVE') "
        "LEFT JOIN public.health_plan_version pv ON pv.service_case_id=c.case_id AND pv.status='ACTIVE' "
        "WHERE c.case_id=value_case AND value_role IN ('org_admin','org_operator') AND EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=value_actor AND u.tenant_id=c.tenant_id AND u.role::text=value_role AND u.status='active') "
        "FOR SHARE OF c,e,sr,pa,tp; "
        "RETURN value_result;",
        (institution,),
        declarations="value_result JSONB;",
    )
    _function(
        "slice6_mutation_replay_v1",
        "value_actor BIGINT,value_operation VARCHAR,value_key VARCHAR,value_digest BYTEA",
        "JSONB",
        f"IF session_user NOT IN ('{institution}','{template}','{review}') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.health_plan_receipt "
        "WHERE actor_scope=value_actor::text AND operation=value_operation AND idempotency_key=value_key FOR SHARE; "
        "IF stored_response IS NULL THEN RETURN NULL; END IF; IF stored_digest<>value_digest THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response;",
        (institution, template, review),
        declarations="stored_digest BYTEA; stored_response JSONB;",
    )
    _function(
        "slice6_mutation_expected_v1",
        "value JSONB",
        "CHAR(64)",
        f"IF session_user NOT IN ('{institution}','{template}','{review}') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value IS NULL OR jsonb_typeof(value)<>'object' OR value->>'operation' NOT IN ('PLAN_GENERATION_REQUEST','PLAN_TEMPLATE_CREATE','PLAN_TEMPLATE_PUBLISH','PLAN_TEMPLATE_RETIRE','PLAN_REVIEW_CLAIM','PLAN_REVIEW_DECISION','PLAN_EXPLANATION','PLAN_USER_DECISION') OR jsonb_typeof(value->'payload')<>'object' OR value->'expected_confirmed_digest'<>'null'::jsonb OR value->'payload'->>'request_digest' !~ '^[0-9a-f]{64}$' OR value->'payload'->>'postimage_digest' !~ '^[0-9a-f]{64}$' OR value->'payload'->>'audit_id' IS NULL OR value->'payload'->>'event_id' IS NULL OR value->'payload'->>'receipt_id' IS NULL OR jsonb_typeof(value->'payload'->'response')<>'object' THEN RAISE EXCEPTION 'SLICE6_MUTATION_EXPECTED_INVALID'; END IF; "
        "RETURN encode(sha256(convert_to(value::text,'UTF8')),'hex');",
        (institution, template, review),
    )
    _function(
        "slice6_mutation_confirm_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user NOT IN ('{institution}','{template}','{review}') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "expected:=value->'expected_postimage'; p:=expected->'payload'; operation_name:=expected->>'operation'; supplied_digest:=value->>'expected_confirmed_digest'; IF expected IS NULL OR expected->'expected_confirmed_digest'<>'null'::jsonb OR supplied_digest !~ '^[0-9a-f]{64}$' THEN RAISE EXCEPTION 'SLICE6_MUTATION_CONFIRM_INVALID'; END IF; calculated_digest:=encode(sha256(convert_to(expected::text,'UTF8')),'hex'); IF supplied_digest<>calculated_digest THEN RAISE EXCEPTION 'SLICE6_MUTATION_CONFIRM_INVALID'; END IF; "
        "expected_target_id:=CASE operation_name WHEN 'PLAN_GENERATION_REQUEST' THEN (p->>'request_id')::uuid WHEN 'PLAN_TEMPLATE_CREATE' THEN (p->>'template_version_id')::uuid WHEN 'PLAN_TEMPLATE_PUBLISH' THEN (p->>'template_version_id')::uuid WHEN 'PLAN_TEMPLATE_RETIRE' THEN (p->>'template_version_id')::uuid WHEN 'PLAN_REVIEW_CLAIM' THEN (p->>'review_id')::uuid WHEN 'PLAN_REVIEW_DECISION' THEN (p->>'review_id')::uuid ELSE (p->>'plan_id')::uuid END; expected_actor_scope:=COALESCE(p->>'initiated_by',p->>'actor_user_id'); expected_occurred_at:=COALESCE(p->>'created_at',p->>'occurred_at')::timestamptz; action_name:=CASE operation_name WHEN 'PLAN_GENERATION_REQUEST' THEN 'PLAN_GENERATION_REQUESTED' WHEN 'PLAN_REVIEW_CLAIM' THEN 'PLAN_REVIEW_CLAIMED' WHEN 'PLAN_REVIEW_DECISION' THEN 'PLAN_REVIEW_DECIDED' WHEN 'PLAN_EXPLANATION' THEN 'PLAN_EXPLANATION_ADDED' WHEN 'PLAN_USER_DECISION' THEN 'PLAN_USER_DECIDED' ELSE operation_name END; expected_target_type:=CASE operation_name WHEN 'PLAN_GENERATION_REQUEST' THEN 'GENERATION' WHEN 'PLAN_REVIEW_CLAIM' THEN 'REVIEW' WHEN 'PLAN_REVIEW_DECISION' THEN 'REVIEW' WHEN 'PLAN_TEMPLATE_CREATE' THEN 'TEMPLATE' WHEN 'PLAN_TEMPLATE_PUBLISH' THEN 'TEMPLATE' WHEN 'PLAN_TEMPLATE_RETIRE' THEN 'TEMPLATE' ELSE 'PLAN' END; "
        "SELECT count(*)=1 AND bool_and(r.target_id=expected_target_id AND r.request_digest=decode(p->>'request_digest','hex') AND r.response_json=p->'response' AND r.postimage_digest=decode(p->>'postimage_digest','hex') AND r.expected_confirmed_digest=supplied_digest AND r.created_at=expected_occurred_at) INTO receipt_ok FROM public.health_plan_receipt r WHERE r.actor_scope=expected_actor_scope AND r.operation=operation_name AND r.idempotency_key=p->>'idempotency_key'; "
        "SELECT count(*)=1 AND bool_and(a.action=action_name AND a.actor_user_id=expected_actor_scope::bigint AND a.actor_role=COALESCE(p->>'initiated_role',p->>'actor_role') AND a.target_type=expected_target_type AND a.target_id=expected_target_id AND a.result='SUCCESS' AND a.evidence_digest=decode(p->>'request_digest','hex') AND a.occurred_at=expected_occurred_at) INTO audit_ok FROM public.health_plan_audit a WHERE a.audit_id=(p->>'audit_id')::uuid; "
        "expected_event_payload:=CASE operation_name WHEN 'PLAN_GENERATION_REQUEST' THEN jsonb_build_object('request_id',p->>'request_id') WHEN 'PLAN_REVIEW_CLAIM' THEN jsonb_build_object('review_id',p->>'review_id','status',p->'response'->>'status') WHEN 'PLAN_REVIEW_DECISION' THEN jsonb_build_object('review_id',p->>'review_id','status',p->'response'->>'status') WHEN 'PLAN_EXPLANATION' THEN jsonb_build_object('plan_id',p->>'plan_id','explanation_id',p->>'explanation_id') WHEN 'PLAN_USER_DECISION' THEN jsonb_build_object('plan_id',p->>'plan_id','decision',p->>'decision') ELSE jsonb_build_object('template_version_id',p->>'template_version_id','operation',p->>'operation') END; "
        "SELECT count(*)=1 AND bool_and(o.aggregate_type=expected_target_type AND o.aggregate_ref=expected_target_id AND o.event_type=action_name AND o.payload_json=expected_event_payload AND o.payload_digest=decode(p->>'request_digest','hex') AND o.status IN ('PENDING','PROCESSING','DELIVERED','FAILED') AND o.created_at=expected_occurred_at AND o.version>=1) INTO outbox_ok FROM public.health_plan_outbox o WHERE o.event_id=(p->>'event_id')::uuid; "
        "aggregate_ok:=CASE operation_name WHEN 'PLAN_GENERATION_REQUEST' THEN EXISTS(SELECT 1 FROM public.health_plan_generation_request r WHERE r.request_id=(p->>'request_id')::uuid AND r.service_case_id=(p->>'service_case_id')::uuid AND r.subject_member_id=(p->>'subject_member_id')::uuid AND r.tenant_id=(p->>'tenant_id')::bigint AND r.assessment_id=(p->>'assessment_id')::uuid AND r.assessment_version=(p->>'assessment_version')::bigint AND r.assembly_id=(p->>'assembly_id')::uuid AND r.template_version_id=(p->>'template_version_id')::uuid AND r.template_version=(p->>'template_version')::bigint AND r.status=p->'response'->>'status' AND r.current_plan_id IS NULL AND r.failure_code IS NULL AND r.authority_digest=decode(p->>'authority_digest','hex') AND r.initiated_by=(p->>'initiated_by')::bigint AND r.initiated_role=p->>'initiated_role' AND r.created_at=expected_occurred_at AND r.updated_at=expected_occurred_at AND r.version=(p->'response'->>'version')::bigint) WHEN 'PLAN_TEMPLATE_CREATE' THEN EXISTS(SELECT 1 FROM public.health_plan_template_version t WHERE t.template_version_id=(p->>'template_version_id')::uuid AND t.template_code=p->>'template_code' AND t.version_no=(p->'response'->>'version_no')::bigint AND t.status='DRAFT' AND t.content=p->'response'->'content' AND t.content_digest=decode(p->>'content_digest','hex') AND t.medical_approval_ref=p->>'medical_approval_ref' AND t.author_user_id=(p->>'actor_user_id')::bigint AND t.created_at=expected_occurred_at AND t.published_at IS NULL AND t.retired_at IS NULL AND t.version=1) WHEN 'PLAN_TEMPLATE_PUBLISH' THEN EXISTS(SELECT 1 FROM public.health_plan_template_version t WHERE t.template_version_id=(p->>'template_version_id')::uuid AND t.status='PUBLISHED' AND t.published_at=expected_occurred_at AND t.version=(p->'response'->>'version')::bigint AND to_jsonb(t.*)->'content'=p->'response'->'content') WHEN 'PLAN_TEMPLATE_RETIRE' THEN EXISTS(SELECT 1 FROM public.health_plan_template_version t WHERE t.template_version_id=(p->>'template_version_id')::uuid AND t.status='RETIRED' AND t.retired_at=expected_occurred_at AND t.version=(p->'response'->>'version')::bigint AND to_jsonb(t.*)->'content'=p->'response'->'content') WHEN 'PLAN_REVIEW_CLAIM' THEN EXISTS(SELECT 1 FROM public.health_plan_review r WHERE r.review_id=(p->>'review_id')::uuid AND r.status='CLAIMED' AND r.claimed_by=(p->>'actor_user_id')::bigint AND r.claimed_at=expected_occurred_at AND r.claim_until=expected_occurred_at+interval '15 minutes' AND r.version=(p->'response'->>'version')::bigint) WHEN 'PLAN_REVIEW_DECISION' THEN EXISTS(SELECT 1 FROM public.health_plan_review r JOIN public.health_plan_version v ON v.plan_id=r.plan_id JOIN public.health_plan_generation_request g ON g.request_id=r.request_id WHERE r.review_id=(p->>'review_id')::uuid AND r.status=p->>'decision' AND r.decision_codes=p->'reason_codes' AND r.decided_at=expected_occurred_at AND r.version=(p->'response'->>'version')::bigint AND v.status=CASE p->>'decision' WHEN 'APPROVED' THEN 'USER_DECISION_PENDING' WHEN 'REJECTED' THEN 'REJECTED' ELSE 'SUPERSEDED' END AND g.status=CASE p->>'decision' WHEN 'APPROVED' THEN 'USER_DECISION_PENDING' WHEN 'REJECTED' THEN 'REJECTED' ELSE 'NEEDS_CORRECTION' END) WHEN 'PLAN_EXPLANATION' THEN EXISTS(SELECT 1 FROM public.health_plan_explanation e JOIN public.health_plan_version v ON v.plan_id=e.plan_id JOIN public.health_plan_generation_request g ON g.current_plan_id=v.plan_id WHERE e.explanation_id=(p->>'explanation_id')::uuid AND e.plan_id=(p->>'plan_id')::uuid AND e.therapist_user_id=(p->>'actor_user_id')::bigint AND e.explanation_codes=p->'explanation_codes' AND e.created_at=expected_occurred_at AND v.status='USER_DECISION_PENDING' AND v.version=(p->'response'->>'version')::bigint AND g.status='USER_DECISION_PENDING') WHEN 'PLAN_USER_DECISION' THEN EXISTS(SELECT 1 FROM public.health_plan_user_decision d JOIN public.health_plan_version v ON v.plan_id=d.plan_id JOIN public.health_plan_generation_request g ON g.current_plan_id=v.plan_id WHERE d.decision_id=(p->>'decision_id')::uuid AND d.plan_id=(p->>'plan_id')::uuid AND d.actor_user_id=(p->>'actor_user_id')::bigint AND d.actor_context IN ('SELF','PROXY') AND d.decision=p->>'decision' AND d.created_at=expected_occurred_at AND d.version=1 AND v.status=p->'response'->>'status' AND v.version=(p->'response'->>'version')::bigint AND g.status=p->'response'->>'status') ELSE FALSE END; "
        "IF receipt_ok AND audit_ok AND outbox_ok AND aggregate_ok THEN RETURN jsonb_build_object('outcome','COMMITTED','confirmed_postimage_digest',supplied_digest); END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.health_plan_receipt r WHERE r.actor_scope=expected_actor_scope AND r.operation=operation_name AND r.idempotency_key=p->>'idempotency_key') THEN preimage_ok:=CASE operation_name WHEN 'PLAN_GENERATION_REQUEST' THEN NOT EXISTS(SELECT 1 FROM public.health_plan_generation_request r WHERE r.request_id=(p->>'request_id')::uuid) WHEN 'PLAN_TEMPLATE_CREATE' THEN NOT EXISTS(SELECT 1 FROM public.health_plan_template_version t WHERE t.template_version_id=(p->>'template_version_id')::uuid) WHEN 'PLAN_TEMPLATE_PUBLISH' THEN EXISTS(SELECT 1 FROM public.health_plan_template_version t WHERE t.template_version_id=(p->>'template_version_id')::uuid AND t.status='DRAFT' AND t.version=(p->>'expected_version')::bigint) WHEN 'PLAN_TEMPLATE_RETIRE' THEN EXISTS(SELECT 1 FROM public.health_plan_template_version t WHERE t.template_version_id=(p->>'template_version_id')::uuid AND t.status='PUBLISHED' AND t.version=(p->>'expected_version')::bigint) WHEN 'PLAN_REVIEW_CLAIM' THEN EXISTS(SELECT 1 FROM public.health_plan_review r WHERE r.review_id=(p->>'review_id')::uuid AND r.status='PENDING' AND r.version=(p->>'expected_version')::bigint) WHEN 'PLAN_REVIEW_DECISION' THEN EXISTS(SELECT 1 FROM public.health_plan_review r WHERE r.review_id=(p->>'review_id')::uuid AND r.status='CLAIMED' AND r.version=(p->>'expected_version')::bigint) WHEN 'PLAN_EXPLANATION' THEN EXISTS(SELECT 1 FROM public.health_plan_version v WHERE v.plan_id=(p->>'plan_id')::uuid AND v.status='NEEDS_EXPLANATION' AND v.version=(p->>'expected_version')::bigint) WHEN 'PLAN_USER_DECISION' THEN EXISTS(SELECT 1 FROM public.health_plan_version v WHERE v.plan_id=(p->>'plan_id')::uuid AND v.status='USER_DECISION_PENDING' AND v.version=(p->>'expected_version')::bigint) ELSE FALSE END; IF preimage_ok THEN RETURN jsonb_build_object('outcome','NOT_COMMITTED','confirmed_postimage_digest',NULL); END IF; END IF; RETURN jsonb_build_object('outcome','UNKNOWN','confirmed_postimage_digest',NULL);",
        (institution, template, review),
        declarations="expected JSONB; p JSONB; operation_name VARCHAR; supplied_digest CHAR(64); calculated_digest CHAR(64); expected_target_id UUID; expected_actor_scope VARCHAR; expected_occurred_at TIMESTAMPTZ; action_name VARCHAR; expected_target_type VARCHAR; expected_event_payload JSONB; receipt_ok BOOLEAN; audit_ok BOOLEAN; outbox_ok BOOLEAN; aggregate_ok BOOLEAN; preimage_ok BOOLEAN;",
    )
    _function(
        "slice6_generation_request_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{institution}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.health_plan_receipt WHERE actor_scope=value->>'initiated_by' AND operation='PLAN_GENERATION_REQUEST' AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'service_case_id',6)); "
        "authority:=public.slice6_generation_authority_v1((value->>'service_case_id')::uuid,(value->>'initiated_by')::bigint,value->>'initiated_role'); IF authority IS NULL THEN RAISE EXCEPTION 'SERVICE_CASE_NOT_FOUND'; END IF; IF (authority->>'service_case_version')::bigint<>(value->>'expected_service_case_version')::bigint THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; IF authority->>'tenant_service_ready'<>'true' THEN RAISE EXCEPTION 'TENANT_NOT_SERVICE_READY'; END IF; IF authority->>'service_case_current'<>'true' THEN RAISE EXCEPTION 'SERVICE_CASE_NOT_CURRENT'; END IF; IF authority->>'consent_current'<>'true' THEN RAISE EXCEPTION 'CONSENT_NOT_CURRENT'; END IF; IF authority->>'primary_therapist_current'<>'true' THEN RAISE EXCEPTION 'PRIMARY_THERAPIST_NOT_CURRENT'; END IF; IF authority->>'assembly_ready'<>'true' OR authority->>'assessment_completed'<>'true' THEN RAISE EXCEPTION 'ASSESSMENT_INPUT_NOT_READY'; END IF; IF authority->>'assessment_disputed'='true' THEN RAISE EXCEPTION 'ASSESSMENT_DISPUTED'; END IF; IF authority->>'assessment_superseded'='true' THEN RAISE EXCEPTION 'ASSESSMENT_SUPERSEDED'; END IF; IF authority->>'high_risk_blocking'='true' THEN RAISE EXCEPTION 'HIGH_RISK_BLOCKING'; END IF; IF authority->>'published_template_available'<>'true' THEN RAISE EXCEPTION 'TEMPLATE_NOT_AVAILABLE'; END IF; IF authority->>'active_generation_exists'='true' THEN RAISE EXCEPTION 'ACTIVE_GENERATION_EXISTS'; END IF; IF authority->>'active_plan_conflict'='true' THEN RAISE EXCEPTION 'ACTIVE_PLAN_CONFLICT'; END IF; IF authority->>'subject_member_id'<>value->>'subject_member_id' OR (authority->>'tenant_id')::bigint<>(value->>'tenant_id')::bigint OR authority->>'assessment_id'<>value->>'assessment_id' OR (authority->>'assessment_version')::bigint<>(value->>'assessment_version')::bigint OR authority->>'assembly_id'<>value->>'assembly_id' OR authority->>'template_version_id'<>value->>'template_version_id' OR (authority->>'template_version')::bigint<>(value->>'template_version')::bigint THEN RAISE EXCEPTION 'ASSESSMENT_INPUT_NOT_READY'; END IF; "
        "INSERT INTO public.health_plan_generation_request(request_id,service_case_id,subject_member_id,tenant_id,assessment_id,assessment_version,assembly_id,template_version_id,template_version,status,current_plan_id,failure_code,authority_digest,initiated_by,initiated_role,created_at,updated_at,version) VALUES ((value->>'request_id')::uuid,(value->>'service_case_id')::uuid,(value->>'subject_member_id')::uuid,(value->>'tenant_id')::bigint,(value->>'assessment_id')::uuid,(value->>'assessment_version')::bigint,(value->>'assembly_id')::uuid,(value->>'template_version_id')::uuid,(value->>'template_version')::bigint,'REQUESTED',NULL,NULL,decode(value->>'authority_digest','hex'),(value->>'initiated_by')::bigint,value->>'initiated_role',(value->>'created_at')::timestamptz,(value->>'created_at')::timestamptz,1); "
        "INSERT INTO public.health_plan_audit VALUES ((value->>'audit_id')::uuid,'PLAN_GENERATION_REQUESTED',(value->>'initiated_by')::bigint,value->>'initiated_role','GENERATION',(value->>'request_id')::uuid,'SUCCESS',decode(value->>'request_digest','hex'),(value->>'created_at')::timestamptz); "
        "INSERT INTO public.health_plan_outbox VALUES ((value->>'event_id')::uuid,'GENERATION',(value->>'request_id')::uuid,'PLAN_GENERATION_REQUESTED',jsonb_build_object('request_id',value->>'request_id'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'created_at')::timestamptz,NULL,1); "
        "INSERT INTO public.health_plan_receipt VALUES ((value->>'receipt_id')::uuid,(value->>'initiated_by'),'PLAN_GENERATION_REQUEST',(value->>'request_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'postimage_digest','hex'),value->>'expected_confirmed_digest',(value->>'created_at')::timestamptz); RETURN value->'response';",
        (institution,),
        declarations="stored_digest BYTEA; stored_response JSONB; authority JSONB;",
    )
    _function(
        "slice6_generation_worker_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{worker}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'request_id',6)); "
        "IF value->>'operation'='CLAIM' THEN UPDATE public.health_plan_generation_request SET status='GENERATING',lease_owner=value->>'lease_owner',lease_until=clock_timestamp()+interval '60 seconds',updated_at=clock_timestamp(),version=version+1 WHERE request_id=(value->>'request_id')::uuid AND status IN ('REQUESTED','GENERATION_FAILED','NEEDS_CORRECTION') RETURNING to_jsonb(health_plan_generation_request.*) INTO result; "
        "ELSIF value->>'operation'='FAIL' THEN UPDATE public.health_plan_generation_request SET status='GENERATION_FAILED',failure_code=value->>'failure_code',lease_owner=NULL,lease_until=NULL,updated_at=clock_timestamp(),version=version+1 WHERE request_id=(value->>'request_id')::uuid AND status='GENERATING' RETURNING to_jsonb(health_plan_generation_request.*) INTO result; ELSE RAISE EXCEPTION 'INVALID_REQUEST'; END IF; RETURN result;",
        (worker,),
        declarations="result JSONB;",
    )
    _function(
        "slice6_generation_input_v1",
        "value_request UUID",
        "JSONB",
        f"IF session_user <> '{worker}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT jsonb_build_object('request_id',r.request_id,'service_case_id',r.service_case_id,'assessment_id',r.assessment_id,'assessment_version',r.assessment_version,'template_version_id',r.template_version_id,'next_version_no',COALESCE((SELECT max(p.version_no)+1 FROM public.health_plan_version p WHERE p.request_id=r.request_id),1),'supersedes_plan_id',r.current_plan_id,'template',t.content||jsonb_build_object('template_code',t.template_code,'template_version',t.version_no),'module_results',(SELECT jsonb_object_agg(m.module_code,m.risk_level) FROM public.assessment_module_result m WHERE m.assessment_id=r.assessment_id)) INTO result FROM public.health_plan_generation_request r JOIN public.health_plan_template_version t ON t.template_version_id=r.template_version_id WHERE r.request_id=value_request AND r.status='GENERATING' AND t.status='PUBLISHED' FOR SHARE OF r,t; RETURN result;",
        (worker,),
        declarations="result JSONB;",
    )
    _function(
        "slice6_generation_complete_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{worker}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'request_id',6)); "
        "SELECT jsonb_build_object('request_id',r.request_id,'status',r.status,'current_plan_id',r.current_plan_id,'current_plan_version',p.version_no,'failure_code',r.failure_code,'updated_at',r.updated_at) INTO result FROM public.health_plan_generation_request r JOIN public.health_plan_version p ON p.plan_id=r.current_plan_id WHERE r.request_id=(value->>'request_id')::uuid AND r.status='IN_REVIEW'; IF result IS NOT NULL THEN RETURN result; END IF; "
        "INSERT INTO public.health_plan_version(plan_id,request_id,service_case_id,subject_member_id,tenant_id,version_no,template_version_id,assessment_id,status,content,content_digest,supersedes_plan_id,created_at,updated_at,version) SELECT (value->>'plan_id')::uuid,r.request_id,r.service_case_id,r.subject_member_id,r.tenant_id,(value->>'version_no')::bigint,r.template_version_id,r.assessment_id,'IN_REVIEW',value->'content',decode(value->>'content_digest','hex'),(value->>'supersedes_plan_id')::uuid,(value->>'created_at')::timestamptz,(value->>'created_at')::timestamptz,1 FROM public.health_plan_generation_request r WHERE r.request_id=(value->>'request_id')::uuid AND r.status='GENERATING' AND COALESCE((value->>'version_no')::bigint,0)=COALESCE((SELECT max(p.version_no)+1 FROM public.health_plan_version p WHERE p.request_id=r.request_id),1) AND (value->>'supersedes_plan_id')::uuid IS NOT DISTINCT FROM r.current_plan_id; GET DIAGNOSTICS affected = ROW_COUNT; IF affected<>1 THEN RAISE EXCEPTION 'GENERATION_NOT_CLAIMED'; END IF; "
        "INSERT INTO public.health_plan_review(review_id,request_id,plan_id,service_case_id,status,decision_codes,created_at,version) SELECT (value->>'review_id')::uuid,r.request_id,(value->>'plan_id')::uuid,r.service_case_id,'PENDING','[]'::jsonb,(value->>'created_at')::timestamptz,1 FROM public.health_plan_generation_request r WHERE r.request_id=(value->>'request_id')::uuid; "
        "UPDATE public.health_plan_generation_request SET status='IN_REVIEW',current_plan_id=(value->>'plan_id')::uuid,lease_owner=NULL,lease_until=NULL,updated_at=(value->>'created_at')::timestamptz,version=version+1 WHERE request_id=(value->>'request_id')::uuid AND status='GENERATING' RETURNING value->'response' INTO result; "
        "INSERT INTO public.health_plan_audit VALUES ((value->>'audit_id')::uuid,'HEALTH_PLAN_GENERATED',0,'workflow_worker','GENERATION',(value->>'request_id')::uuid,'SUCCESS',decode(value->>'event_digest','hex'),(value->>'created_at')::timestamptz); "
        "INSERT INTO public.health_plan_outbox VALUES ((value->>'event_id')::uuid,'GENERATION',(value->>'request_id')::uuid,'HEALTH_PLAN_GENERATED',jsonb_build_object('request_id',value->>'request_id','plan_id',value->>'plan_id'),decode(value->>'event_digest','hex'),'PENDING',0,NULL,NULL,(value->>'created_at')::timestamptz,NULL,1); RETURN result;",
        (worker,),
        declarations="result JSONB; affected BIGINT;",
    )
    _function(
        "slice6_review_transition_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{review}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'operation' NOT IN ('CLAIM','DECIDE') THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "operation_name:=CASE value->>'operation' WHEN 'CLAIM' THEN 'PLAN_REVIEW_CLAIM' ELSE 'PLAN_REVIEW_DECISION' END; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.health_plan_receipt WHERE actor_scope=value->>'actor_user_id' AND operation=operation_name AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=(value->>'actor_user_id')::bigint AND u.role='expert' AND u.status='active' FOR SHARE) THEN RAISE EXCEPTION 'FORBIDDEN'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'review_id',6)); "
        "IF value->>'operation'='CLAIM' THEN UPDATE public.health_plan_review SET status='CLAIMED',claimed_by=(value->>'actor_user_id')::bigint,claimed_at=(value->>'occurred_at')::timestamptz,claim_until=(value->>'occurred_at')::timestamptz+interval '30 minutes',version=version+1 WHERE review_id=(value->>'review_id')::uuid AND status='PENDING' AND version=(value->>'expected_version')::bigint RETURNING to_jsonb(health_plan_review.*) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; "
        "ELSE PERFORM 1 FROM public.health_plan_review rr JOIN public.health_plan_version p ON p.plan_id=rr.plan_id JOIN public.health_plan_generation_request g ON g.request_id=rr.request_id AND g.current_plan_id=p.plan_id JOIN public.service_case c ON c.case_id=rr.service_case_id JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment a ON a.assignment_id=c.assignment_id JOIN public.therapist_profile t ON t.therapist_id=c.primary_therapist_id JOIN public.health_assessment h ON h.assessment_id=p.assessment_id JOIN public.health_plan_template_version tv ON tv.template_version_id=p.template_version_id WHERE rr.review_id=(value->>'review_id')::uuid AND rr.status='CLAIMED' AND rr.claimed_by=(value->>'actor_user_id')::bigint AND rr.claim_until>clock_timestamp() AND c.status='PREPARING' AND e.status='CASE_CREATED' AND sr.readiness_status='SERVICE_READY' AND a.status='ACCEPTED' AND a.therapist_id=t.therapist_id AND t.status='APPROVED_ACTIVE' AND t.current_qualification_version_id IS NOT NULL AND t.qualification_valid_until>=CURRENT_DATE AND h.status='COMPLETED' AND h.overall_risk IN ('WITHIN_RANGE','ATTENTION') AND tv.status='PUBLISHED' AND EXISTS(SELECT 1 FROM public.assessment_readiness_case_pointer rp WHERE rp.service_case_id=c.case_id AND rp.current_assembly_id=g.assembly_id) AND NOT EXISTS(SELECT 1 FROM public.health_assessment newer WHERE newer.service_case_id=h.service_case_id AND newer.sequence_no>h.sequence_no) AND NOT EXISTS(SELECT 1 FROM public.assessment_dispute d WHERE d.assessment_id=h.assessment_id AND d.status='OPEN') AND NOT EXISTS(SELECT 1 FROM public.high_risk_task ht WHERE ht.service_case_id=c.case_id AND ht.status IN ('OPEN','CLAIMED','ESCALATED')) AND NOT EXISTS(SELECT 1 FROM unnest(CASE WHEN e.mode='PROXY_ELDER' THEN ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK','PROXY_AUTHORIZATION']::text[] ELSE ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']::text[] END) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id WHERE cr.enrollment_id=e.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED' AND cd.status='PUBLISHED')) FOR SHARE OF rr,p,g,c,e,sr,a,t,h,tv; IF NOT FOUND THEN RAISE EXCEPTION 'REVIEW_DECISION_CONFLICT'; END IF; UPDATE public.health_plan_review SET status=value->>'decision',decision_codes=value->'reason_codes',decided_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE review_id=(value->>'review_id')::uuid AND status='CLAIMED' AND claimed_by=(value->>'actor_user_id')::bigint AND claim_until>clock_timestamp() AND version=(value->>'expected_version')::bigint RETURNING to_jsonb(health_plan_review.*) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'REVIEW_NOT_CLAIMED'; END IF; UPDATE public.health_plan_version p SET status=CASE value->>'decision' WHEN 'APPROVED' THEN 'USER_DECISION_PENDING' WHEN 'REJECTED' THEN 'REJECTED' ELSE 'SUPERSEDED' END,updated_at=(value->>'occurred_at')::timestamptz,version=p.version+1 FROM public.health_plan_review r WHERE r.review_id=(value->>'review_id')::uuid AND p.plan_id=r.plan_id; UPDATE public.health_plan_generation_request g SET status=CASE value->>'decision' WHEN 'APPROVED' THEN 'USER_DECISION_PENDING' WHEN 'REJECTED' THEN 'REJECTED' ELSE 'NEEDS_CORRECTION' END,updated_at=(value->>'occurred_at')::timestamptz,version=g.version+1 FROM public.health_plan_review r WHERE r.review_id=(value->>'review_id')::uuid AND g.request_id=r.request_id; END IF; "
        "INSERT INTO public.health_plan_audit VALUES ((value->>'audit_id')::uuid,CASE value->>'operation' WHEN 'CLAIM' THEN 'PLAN_REVIEW_CLAIMED' ELSE 'PLAN_REVIEW_DECIDED' END,(value->>'actor_user_id')::bigint,value->>'actor_role','REVIEW',(value->>'review_id')::uuid,'SUCCESS',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); "
        "INSERT INTO public.health_plan_outbox VALUES ((value->>'event_id')::uuid,'REVIEW',(value->>'review_id')::uuid,CASE value->>'operation' WHEN 'CLAIM' THEN 'PLAN_REVIEW_CLAIMED' ELSE 'PLAN_REVIEW_DECIDED' END,jsonb_build_object('review_id',value->>'review_id','status',value->'response'->>'status'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); "
        "INSERT INTO public.health_plan_receipt VALUES ((value->>'receipt_id')::uuid,value->>'actor_user_id',operation_name,(value->>'review_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'postimage_digest','hex'),value->>'expected_confirmed_digest',(value->>'occurred_at')::timestamptz); RETURN value->'response';",
        (review,),
        declarations="result JSONB; operation_name VARCHAR; stored_digest BYTEA; stored_response JSONB;",
    )
    _function(
        "slice6_plan_explanation_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{institution}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.health_plan_receipt WHERE actor_scope=value->>'actor_user_id' AND operation='PLAN_EXPLANATION' AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; "
        "SELECT p.service_case_id INTO target_case_id FROM public.health_plan_version p WHERE p.plan_id=(value->>'plan_id')::uuid; IF target_case_id IS NULL THEN RAISE EXCEPTION 'PLAN_NOT_FOUND'; END IF; IF NOT EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.therapist_profile t ON t.user_id=u.id JOIN public.service_case c ON c.primary_therapist_id=t.therapist_id JOIN public.primary_therapist_assignment a ON a.assignment_id=c.assignment_id WHERE u.id=(value->>'actor_user_id')::bigint AND u.status='active' AND u.role='therapist' AND u.tenant_id=c.tenant_id AND c.case_id=target_case_id AND c.status='PREPARING' AND a.status='ACCEPTED' AND a.therapist_id=t.therapist_id AND t.status='APPROVED_ACTIVE' AND t.current_qualification_version_id IS NOT NULL AND t.qualification_valid_until>=CURRENT_DATE FOR SHARE OF u,c,a,t) THEN RAISE EXCEPTION 'FORBIDDEN'; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(value->>'plan_id',6)); "
        "INSERT INTO public.health_plan_explanation(explanation_id,plan_id,service_case_id,therapist_user_id,explanation_codes,created_at) SELECT (value->>'explanation_id')::uuid,p.plan_id,p.service_case_id,(value->>'actor_user_id')::bigint,value->'explanation_codes',(value->>'occurred_at')::timestamptz FROM public.health_plan_version p JOIN public.health_plan_generation_request r ON r.request_id=p.request_id AND r.current_plan_id=p.plan_id JOIN public.service_case c ON c.case_id=p.service_case_id JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment a ON a.assignment_id=c.assignment_id JOIN public.therapist_profile t ON t.therapist_id=c.primary_therapist_id JOIN public.\"user\" u ON u.id=t.user_id JOIN public.health_assessment h ON h.assessment_id=p.assessment_id JOIN public.health_plan_template_version tv ON tv.template_version_id=p.template_version_id WHERE p.plan_id=(value->>'plan_id')::uuid AND p.status='NEEDS_EXPLANATION' AND p.version=(value->>'expected_version')::bigint AND c.status='PREPARING' AND e.status='CASE_CREATED' AND sr.readiness_status='SERVICE_READY' AND a.status='ACCEPTED' AND a.therapist_id=t.therapist_id AND t.user_id=(value->>'actor_user_id')::bigint AND t.status='APPROVED_ACTIVE' AND t.current_qualification_version_id IS NOT NULL AND t.qualification_valid_until>=CURRENT_DATE AND u.status='active' AND u.role='therapist' AND u.tenant_id=c.tenant_id AND h.status='COMPLETED' AND h.overall_risk IN ('WITHIN_RANGE','ATTENTION') AND tv.status='PUBLISHED' AND EXISTS(SELECT 1 FROM public.assessment_readiness_case_pointer rp WHERE rp.service_case_id=c.case_id AND rp.current_assembly_id=r.assembly_id) AND NOT EXISTS(SELECT 1 FROM public.health_assessment newer WHERE newer.service_case_id=h.service_case_id AND newer.sequence_no>h.sequence_no) AND NOT EXISTS(SELECT 1 FROM public.assessment_dispute d WHERE d.assessment_id=h.assessment_id AND d.status='OPEN') AND NOT EXISTS(SELECT 1 FROM public.high_risk_task ht WHERE ht.service_case_id=c.case_id AND ht.status IN ('OPEN','CLAIMED','ESCALATED')) AND NOT EXISTS(SELECT 1 FROM unnest(CASE WHEN e.mode='PROXY_ELDER' THEN ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK','PROXY_AUTHORIZATION']::text[] ELSE ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']::text[] END) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id WHERE cr.enrollment_id=e.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED' AND cd.status='PUBLISHED')) FOR SHARE OF p,r,c,e,sr,a,t,u,h,tv; GET DIAGNOSTICS affected = ROW_COUNT; IF affected<>1 THEN RAISE EXCEPTION 'FORBIDDEN'; END IF; "
        "UPDATE public.health_plan_version SET status='USER_DECISION_PENDING',version=version+1,updated_at=(value->>'occurred_at')::timestamptz WHERE plan_id=(value->>'plan_id')::uuid AND status='NEEDS_EXPLANATION' AND version=(value->>'expected_version')::bigint RETURNING value->'response' INTO result; IF result IS NULL THEN RAISE EXCEPTION 'FORBIDDEN'; END IF; "
        "UPDATE public.health_plan_generation_request SET status='USER_DECISION_PENDING',updated_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE current_plan_id=(value->>'plan_id')::uuid; "
        "INSERT INTO public.health_plan_audit VALUES ((value->>'audit_id')::uuid,'PLAN_EXPLANATION_ADDED',(value->>'actor_user_id')::bigint,value->>'actor_role','PLAN',(value->>'plan_id')::uuid,'SUCCESS',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); "
        "INSERT INTO public.health_plan_outbox VALUES ((value->>'event_id')::uuid,'PLAN',(value->>'plan_id')::uuid,'PLAN_EXPLANATION_ADDED',jsonb_build_object('plan_id',value->>'plan_id','explanation_id',value->>'explanation_id'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); "
        "INSERT INTO public.health_plan_receipt VALUES ((value->>'receipt_id')::uuid,value->>'actor_user_id','PLAN_EXPLANATION',(value->>'plan_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'postimage_digest','hex'),value->>'expected_confirmed_digest',(value->>'occurred_at')::timestamptz); RETURN result;",
        (institution,),
        declarations="result JSONB; stored_digest BYTEA; stored_response JSONB; affected BIGINT; target_case_id UUID;",
    )
    _function(
        "slice6_user_decision_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{institution}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.health_plan_receipt WHERE actor_scope=value->>'actor_user_id' AND operation='PLAN_USER_DECISION' AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; "
        "SELECT p.subject_member_id,p.service_case_id INTO subject_id,target_case_id FROM public.health_plan_version p WHERE p.plan_id=(value->>'plan_id')::uuid; IF subject_id IS NULL THEN RAISE EXCEPTION 'PLAN_NOT_FOUND'; END IF; "
        "SELECT 'SELF',NULL::uuid INTO actual_context,actual_grant FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id WHERE u.id=(value->>'actor_user_id')::bigint AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND l.member_id=subject_id AND m.status='created' FOR SHARE OF u,l,m; "
        "IF actual_context IS NULL THEN SELECT 'PROXY',g.grant_id INTO actual_context,actual_grant FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id JOIN identity.member pm ON pm.member_id=g.principal_member_id JOIN public.service_case c ON c.enrollment_id=g.enrollment_id WHERE u.id=(value->>'actor_user_id')::bigint AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND m.status='created' AND pm.status='created' AND c.case_id=target_case_id AND c.subject_member_id=subject_id AND g.principal_member_id=subject_id AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.permission_codes ? 'DAILY_VIEW' FOR SHARE OF u,l,m,g,pm,c; END IF; IF actual_context IS NULL THEN RAISE EXCEPTION 'USER_DECISION_FORBIDDEN'; END IF; "
        "PERFORM 1 FROM public.service_case c WHERE c.case_id=target_case_id FOR SHARE; IF NOT FOUND THEN RAISE EXCEPTION 'USER_DECISION_CONFLICT'; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(value->>'plan_id',6)); "
        "PERFORM 1 FROM public.health_plan_version p JOIN public.health_plan_generation_request r ON r.request_id=p.request_id AND r.current_plan_id=p.plan_id JOIN public.service_case c ON c.case_id=p.service_case_id JOIN public.service_enrollment e ON e.enrollment_id=c.enrollment_id JOIN public.institution_service_readiness sr ON sr.tenant_id=c.tenant_id JOIN public.primary_therapist_assignment pa ON pa.assignment_id=c.assignment_id JOIN public.therapist_profile tp ON tp.therapist_id=c.primary_therapist_id JOIN public.health_assessment a ON a.assessment_id=p.assessment_id JOIN public.health_plan_template_version tv ON tv.template_version_id=p.template_version_id WHERE p.plan_id=(value->>'plan_id')::uuid AND p.version=(value->>'expected_version')::bigint AND p.status='USER_DECISION_PENDING' AND c.status='PREPARING' AND c.subject_member_id=p.subject_member_id AND e.status='CASE_CREATED' AND sr.readiness_status='SERVICE_READY' AND pa.status='ACCEPTED' AND pa.therapist_id=tp.therapist_id AND tp.status='APPROVED_ACTIVE' AND tp.current_qualification_version_id IS NOT NULL AND tp.qualification_valid_until>=CURRENT_DATE AND a.status='COMPLETED' AND a.overall_risk IN ('WITHIN_RANGE','ATTENTION') AND tv.status='PUBLISHED' AND EXISTS(SELECT 1 FROM public.assessment_readiness_case_pointer rp WHERE rp.service_case_id=c.case_id AND rp.current_assembly_id=r.assembly_id) AND NOT EXISTS(SELECT 1 FROM public.health_assessment newer WHERE newer.service_case_id=a.service_case_id AND newer.sequence_no>a.sequence_no) AND NOT EXISTS(SELECT 1 FROM public.assessment_dispute d WHERE d.assessment_id=a.assessment_id AND d.status='OPEN') AND NOT EXISTS(SELECT 1 FROM public.high_risk_task h WHERE h.service_case_id=c.case_id AND h.status IN ('OPEN','CLAIMED','ESCALATED')) AND NOT EXISTS(SELECT 1 FROM unnest(CASE WHEN e.mode='PROXY_ELDER' THEN ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK','PROXY_AUTHORIZATION']::text[] ELSE ARRAY['USER_AGREEMENT','PRIVACY_POLICY','HEALTH_DATA_PROCESSING','INSTITUTION_SERVICE','NON_MEDICAL_RISK']::text[] END) required(document_type) WHERE NOT EXISTS(SELECT 1 FROM public.consent_record cr JOIN public.consent_document_version cd ON cd.document_version_id=cr.document_version_id WHERE cr.enrollment_id=e.enrollment_id AND cr.document_type=required.document_type AND cr.status='ACCEPTED' AND cd.status='PUBLISHED')) FOR UPDATE OF p,r,c,e,sr,pa,tp,a,tv; IF NOT FOUND THEN RAISE EXCEPTION 'USER_DECISION_CONFLICT'; END IF; "
        "INSERT INTO public.health_plan_user_decision(decision_id,plan_id,service_case_id,subject_member_id,actor_user_id,actor_context,proxy_grant_id,decision,created_at,version) SELECT (value->>'decision_id')::uuid,p.plan_id,p.service_case_id,p.subject_member_id,(value->>'actor_user_id')::bigint,actual_context,actual_grant,value->>'decision',(value->>'occurred_at')::timestamptz,1 FROM public.health_plan_version p WHERE p.plan_id=(value->>'plan_id')::uuid; "
        "UPDATE public.health_plan_version SET status=CASE value->>'decision' WHEN 'ACCEPT' THEN 'ACTIVE' WHEN 'DECLINE' THEN 'DECLINED' ELSE 'NEEDS_EXPLANATION' END,updated_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE plan_id=(value->>'plan_id')::uuid AND version=(value->>'expected_version')::bigint RETURNING value->'response' INTO result; IF result IS NULL THEN RAISE EXCEPTION 'USER_DECISION_CONFLICT'; END IF; "
        "UPDATE public.health_plan_generation_request SET status=CASE value->>'decision' WHEN 'ACCEPT' THEN 'ACTIVE' WHEN 'DECLINE' THEN 'DECLINED' ELSE 'NEEDS_EXPLANATION' END,updated_at=(value->>'occurred_at')::timestamptz,version=version+1 WHERE current_plan_id=(value->>'plan_id')::uuid; "
        "INSERT INTO public.health_plan_audit VALUES ((value->>'audit_id')::uuid,'PLAN_USER_DECIDED',(value->>'actor_user_id')::bigint,value->>'actor_role','PLAN',(value->>'plan_id')::uuid,'SUCCESS',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); "
        "INSERT INTO public.health_plan_outbox VALUES ((value->>'event_id')::uuid,'PLAN',(value->>'plan_id')::uuid,'PLAN_USER_DECIDED',jsonb_build_object('plan_id',value->>'plan_id','decision',value->>'decision'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); "
        "INSERT INTO public.health_plan_receipt VALUES ((value->>'receipt_id')::uuid,value->>'actor_user_id','PLAN_USER_DECISION',(value->>'plan_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'postimage_digest','hex'),value->>'expected_confirmed_digest',(value->>'occurred_at')::timestamptz); RETURN result;",
        (institution,),
        declarations="result JSONB; stored_digest BYTEA; stored_response JSONB; subject_id UUID; target_case_id UUID; actual_context VARCHAR; actual_grant UUID;",
    )
    _function(
        "slice6_template_governance_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{template}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value->>'operation' NOT IN ('CREATE','PUBLISH','RETIRE') THEN RAISE EXCEPTION 'INVALID_REQUEST'; END IF; "
        "operation_name:='PLAN_TEMPLATE_'||(value->>'operation'); "
        "IF NOT EXISTS(SELECT 1 FROM public.\"user\" u WHERE u.id=(value->>'actor_user_id')::bigint AND u.status='active' AND u.role::text=(value->>'actor_role') AND u.role::text IN ('expert','sys_admin','super_admin') FOR SHARE) THEN RAISE EXCEPTION 'FORBIDDEN'; END IF; "
        "SELECT request_digest,response_json INTO stored_digest,stored_response FROM public.health_plan_receipt WHERE actor_scope=value->>'actor_user_id' AND operation=operation_name AND idempotency_key=value->>'idempotency_key' FOR SHARE; IF stored_response IS NOT NULL THEN IF stored_digest<>decode(value->>'request_digest','hex') THEN RAISE EXCEPTION 'IDEMPOTENCY_CONFLICT'; END IF; RETURN stored_response; END IF; "
        "PERFORM pg_advisory_xact_lock(hashtextextended(COALESCE(value->>'template_code',value->>'template_version_id'),6)); "
        "IF value->>'operation'='CREATE' THEN INSERT INTO public.health_plan_template_version(template_version_id,template_code,version_no,status,content,content_digest,medical_approval_ref,author_user_id,created_at,version) SELECT (value->>'template_version_id')::uuid,value->>'template_code',(value->'response'->>'version_no')::bigint,'DRAFT',jsonb_build_object('applicable_modules',value->'applicable_modules','goals_by_module',value->'goals_by_module','stage_codes',value->'stage_codes','milestone_codes',value->'milestone_codes','sop_codes',value->'sop_codes','contraindication_codes',value->'contraindication_codes','user_message_codes',value->'user_message_codes','therapist_action_codes',value->'therapist_action_codes'),decode(value->>'content_digest','hex'),value->>'medical_approval_ref',(value->>'actor_user_id')::bigint,(value->>'occurred_at')::timestamptz,1 WHERE (value->'response'->>'version_no')::bigint=COALESCE((SELECT max(version_no)+1 FROM public.health_plan_template_version WHERE template_code=value->>'template_code'),1) RETURNING to_jsonb(health_plan_template_version.*) INTO result; "
        "ELSIF value->>'operation' IN ('PUBLISH','RETIRE') THEN UPDATE public.health_plan_template_version SET status=CASE value->>'operation' WHEN 'PUBLISH' THEN 'PUBLISHED' ELSE 'RETIRED' END,published_at=CASE WHEN value->>'operation'='PUBLISH' THEN (value->>'occurred_at')::timestamptz ELSE published_at END,retired_at=CASE WHEN value->>'operation'='RETIRE' THEN (value->>'occurred_at')::timestamptz ELSE retired_at END,version=version+1 WHERE template_version_id=(value->>'template_version_id')::uuid AND version=(value->>'expected_version')::bigint AND status=CASE value->>'operation' WHEN 'PUBLISH' THEN 'DRAFT' ELSE 'PUBLISHED' END RETURNING to_jsonb(health_plan_template_version.*) INTO result; END IF; IF result IS NULL THEN RAISE EXCEPTION 'STALE_VERSION'; END IF; "
        "actual_public:=jsonb_build_object('template_version_id',result->'template_version_id','template_code',result->'template_code','version_no',result->'version_no','status',result->'status','content',result->'content','medical_approval_ref',result->'medical_approval_ref','version',result->'version'); expected_public:=jsonb_build_object('template_version_id',value->'response'->'template_version_id','template_code',value->'response'->'template_code','version_no',value->'response'->'version_no','status',value->'response'->'status','content',value->'response'->'content','medical_approval_ref',value->'response'->'medical_approval_ref','version',value->'response'->'version'); IF actual_public<>expected_public THEN RAISE EXCEPTION 'EXPECTED_POSTIMAGE_MISMATCH'; END IF; "
        "INSERT INTO public.health_plan_audit VALUES ((value->>'audit_id')::uuid,operation_name,(value->>'actor_user_id')::bigint,value->>'actor_role','TEMPLATE',(value->>'template_version_id')::uuid,'SUCCESS',decode(value->>'request_digest','hex'),(value->>'occurred_at')::timestamptz); INSERT INTO public.health_plan_outbox VALUES ((value->>'event_id')::uuid,'TEMPLATE',(value->>'template_version_id')::uuid,operation_name,jsonb_build_object('template_version_id',value->>'template_version_id','operation',value->>'operation'),decode(value->>'request_digest','hex'),'PENDING',0,NULL,NULL,(value->>'occurred_at')::timestamptz,NULL,1); INSERT INTO public.health_plan_receipt VALUES ((value->>'receipt_id')::uuid,value->>'actor_user_id',operation_name,(value->>'template_version_id')::uuid,value->>'idempotency_key',decode(value->>'request_digest','hex'),value->'response',decode(value->>'postimage_digest','hex'),value->>'expected_confirmed_digest',(value->>'occurred_at')::timestamptz); RETURN value->'response';",
        (template,),
        declarations="result JSONB; actual_public JSONB; expected_public JSONB; stored_digest BYTEA; stored_response JSONB; operation_name VARCHAR;",
    )
    _function(
        "slice6_actor_read_authority_v1",
        "value_actor BIGINT,value_role VARCHAR,value_case UUID,value_member UUID,value_tenant BIGINT",
        "BOOLEAN",
        f"IF session_user NOT IN ('{clinical}','{family}') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF NOT EXISTS(SELECT 1 FROM public.service_case c WHERE c.case_id=value_case AND c.subject_member_id=value_member AND c.tenant_id=value_tenant AND c.status='PREPARING' FOR SHARE) THEN RETURN FALSE; END IF; "
        "IF value_role IN ('org_admin','org_operator') THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.institution_service_readiness r ON r.tenant_id=u.tenant_id WHERE u.id=value_actor AND u.status='active' AND u.role::text=value_role AND u.tenant_id=value_tenant AND r.readiness_status='SERVICE_READY' FOR SHARE OF u,r); "
        "ELSIF value_role='therapist' THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u JOIN public.therapist_profile t ON t.user_id=u.id JOIN public.service_case c ON c.primary_therapist_id=t.therapist_id JOIN public.primary_therapist_assignment a ON a.assignment_id=c.assignment_id WHERE u.id=value_actor AND u.status='active' AND u.role='therapist' AND u.tenant_id=value_tenant AND t.status='APPROVED_ACTIVE' AND t.current_qualification_version_id IS NOT NULL AND t.qualification_valid_until>=CURRENT_DATE AND a.status='ACCEPTED' AND a.therapist_id=t.therapist_id AND c.case_id=value_case AND c.subject_member_id=value_member AND c.tenant_id=value_tenant FOR SHARE OF u,t,c,a); "
        "ELSIF value_role='member' THEN RETURN EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND l.member_id=value_member AND m.status='created' FOR SHARE OF u,l,m) OR EXISTS(SELECT 1 FROM public.\"user\" u JOIN identity.user_member_self_link l ON l.user_ref=u.id JOIN identity.member m ON m.member_id=l.member_id JOIN public.proxy_grant g ON g.proxy_member_id=l.member_id JOIN identity.member pm ON pm.member_id=g.principal_member_id JOIN public.service_case c ON c.enrollment_id=g.enrollment_id WHERE u.id=value_actor AND u.role='member' AND u.status='active' AND u.tenant_id IS NULL AND m.status='created' AND pm.status='created' AND c.case_id=value_case AND c.subject_member_id=value_member AND c.tenant_id=value_tenant AND g.principal_member_id=value_member AND g.status='ACTIVE' AND g.valid_from<=clock_timestamp() AND (g.valid_until IS NULL OR g.valid_until>clock_timestamp()) AND g.permission_codes ? 'DAILY_VIEW' FOR SHARE OF u,l,m,g,pm,c); END IF; RETURN FALSE;",
        (clinical, family),
    )
    _function(
        "slice6_case_read_authority_v1",
        "value_actor BIGINT,value_role VARCHAR,value_case UUID",
        "JSONB",
        f"IF session_user NOT IN ('{clinical}','{family}') THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT c.subject_member_id,c.tenant_id INTO member_id,tenant_id FROM public.service_case c WHERE c.case_id=value_case FOR SHARE; IF NOT FOUND THEN RETURN jsonb_build_object('exists',FALSE,'allowed',FALSE); END IF; allowed:=public.slice6_actor_read_authority_v1(value_actor,value_role,value_case,member_id,tenant_id); RETURN jsonb_build_object('exists',TRUE,'allowed',allowed);",
        (clinical, family),
        declarations="member_id UUID; tenant_id BIGINT; allowed BOOLEAN;",
    )
    _function(
        "slice6_outbox_claim_v1",
        "value_event UUID,value_seconds BIGINT",
        "JSONB",
        f"IF session_user <> '{worker}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "IF value_event IS NULL THEN SELECT event_id INTO value_event FROM public.health_plan_outbox WHERE status IN ('PENDING','FAILED') ORDER BY created_at,event_id LIMIT 1 FOR UPDATE SKIP LOCKED; END IF; "
        "UPDATE public.health_plan_outbox SET status='PROCESSING',attempts=attempts+1,lease_owner=session_user,lease_until=clock_timestamp()+make_interval(secs=>value_seconds::int),version=version+1 WHERE event_id=value_event AND status IN ('PENDING','FAILED') RETURNING to_jsonb(health_plan_outbox.*) INTO result; RETURN result;",
        (worker,),
        declarations="result JSONB;",
    )
    _function(
        "slice6_outbox_consume_v1",
        "value JSONB",
        "JSONB",
        f"IF session_user <> '{worker}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; "
        "SELECT to_jsonb(o.*) INTO result FROM public.health_plan_delivery d JOIN public.health_plan_outbox o ON o.event_id=d.event_id WHERE d.event_id=(value->>'event_id')::uuid AND d.target_type=value->>'target_type' AND d.target_ref=(value->>'target_ref')::uuid FOR SHARE OF d,o; IF result IS NOT NULL THEN RETURN result; END IF; "
        "INSERT INTO public.health_plan_delivery(delivery_id,event_id,target_type,target_ref,target_digest,delivered_at) VALUES ((value->>'delivery_id')::uuid,(value->>'event_id')::uuid,value->>'target_type',(value->>'target_ref')::uuid,decode(value->>'target_digest','hex'),(value->>'delivered_at')::timestamptz); UPDATE public.health_plan_outbox SET status='DELIVERED',delivered_at=(value->>'delivered_at')::timestamptz,lease_owner=NULL,lease_until=NULL,version=version+1 WHERE event_id=(value->>'event_id')::uuid AND status='PROCESSING' AND lease_owner=session_user RETURNING to_jsonb(health_plan_outbox.*) INTO result; IF result IS NULL THEN RAISE EXCEPTION 'OUTBOX_LEASE_NOT_CURRENT'; END IF; RETURN result;",
        (worker,),
        declarations="result JSONB;",
    )
    _function(
        "slice6_outbox_recover_v1",
        "value_cutoff TIMESTAMP WITH TIME ZONE",
        "JSONB",
        f"IF session_user <> '{worker}' THEN RAISE EXCEPTION 'FORBIDDEN' USING ERRCODE='42501'; END IF; WITH changed AS (UPDATE public.health_plan_outbox SET status='FAILED',lease_owner=NULL,lease_until=NULL,version=version+1 WHERE status='PROCESSING' AND lease_until<value_cutoff RETURNING event_id) SELECT jsonb_build_object('recovered',count(*)) INTO result FROM changed; RETURN result;",
        (worker,),
        declarations="result JSONB;",
    )


def _create_views(template: str, review: str, clinical: str, family: str) -> None:
    op.execute("CREATE VIEW public.slice6_template_governance_read_v1 WITH (security_barrier=true) AS SELECT template_version_id,template_code,version_no,status,content,medical_approval_ref,created_at,published_at,retired_at,version FROM public.health_plan_template_version")
    op.execute("CREATE VIEW public.slice6_generation_read_v1 WITH (security_barrier=true) AS SELECT r.request_id,r.service_case_id,r.subject_member_id,r.tenant_id,r.status,r.current_plan_id,p.version_no AS current_plan_version,r.failure_code,r.created_at,r.updated_at,r.version FROM public.health_plan_generation_request r LEFT JOIN public.health_plan_version p ON p.plan_id=r.current_plan_id")
    op.execute("CREATE VIEW public.slice6_review_read_v1 WITH (security_barrier=true) AS SELECT r.review_id,r.request_id,r.plan_id,r.service_case_id,r.status,p.version_no AS plan_version_no,r.claimed_at,r.decided_at,r.version FROM public.health_plan_review r JOIN public.health_plan_version p ON p.plan_id=r.plan_id")
    op.execute("CREATE VIEW public.slice6_plan_read_v1 WITH (security_barrier=true) AS SELECT p.plan_id,p.service_case_id,p.subject_member_id,p.tenant_id,p.version_no,p.status,t.template_code,t.version_no AS template_version,COALESCE(a.overall_risk,'NOT_ASSESSED') AS overall_risk_level,p.created_at,p.updated_at,p.version,p.content->'module_summaries' AS module_summaries,p.content->'goals' AS goals,p.content->'stages' AS stages,p.content->'milestones' AS milestones,p.content->'sop_items' AS sop_items,p.content->'contraindication_codes' AS contraindication_codes,p.content->'user_message_codes' AS user_message_codes,p.content->'therapist_action_codes' AS therapist_action_codes,jsonb_build_object('status',r.status,'decision_codes',r.decision_codes,'decided_at',r.decided_at) AS review_summary,COALESCE((SELECT jsonb_build_object('decision',d.decision,'decided_at',d.created_at) FROM public.health_plan_user_decision d WHERE d.plan_id=p.plan_id ORDER BY d.created_at DESC LIMIT 1),jsonb_build_object('decision',NULL,'decided_at',NULL)) AS user_decision_summary,COALESCE((SELECT jsonb_agg(jsonb_build_object('explanation_id',e.explanation_id,'explanation_codes',e.explanation_codes,'created_at',e.created_at) ORDER BY e.created_at) FROM public.health_plan_explanation e WHERE e.plan_id=p.plan_id),'[]'::jsonb) AS explanations FROM public.health_plan_version p JOIN public.health_plan_template_version t ON t.template_version_id=p.template_version_id JOIN public.health_assessment a ON a.assessment_id=p.assessment_id LEFT JOIN public.health_plan_review r ON r.plan_id=p.plan_id")
    for view in _VIEWS:
        op.execute(f"REVOKE ALL ON public.{view} FROM PUBLIC")
    op.execute(f'GRANT SELECT ON public.slice6_template_governance_read_v1 TO "{template}"')
    op.execute(f'GRANT SELECT ON public.slice6_review_read_v1 TO "{review}"')
    op.execute(f'GRANT SELECT ON public.slice6_generation_read_v1,public.slice6_plan_read_v1 TO "{clinical}"')
    op.execute(f'GRANT SELECT ON public.slice6_plan_read_v1 TO "{family}"')


def _acl(roles: tuple[str, str, str, str, str, str]) -> None:
    for role in roles:
        op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    for table in _TABLES:
        op.execute(f"REVOKE ALL ON public.{table} FROM PUBLIC")


def upgrade() -> None:
    roles = _roles()
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    _create_tables()
    _create_functions(*roles)
    _create_views(roles[1], roles[2], roles[4], roles[5])
    _acl(roles)


def downgrade() -> None:
    roles = _roles()
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    union = " UNION ALL ".join(f"SELECT 1 FROM public.{table}" for table in _TABLES)
    nonempty = connection.execute(sa.text(f"SELECT EXISTS({union})")).scalar_one()
    if nonempty:
        raise RuntimeError("Slice 6 downgrade requires empty module tables") from None
    for view in reversed(_VIEWS):
        op.execute(f"DROP VIEW public.{view}")
    for name, signature in reversed(_FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{name}({signature})")
    for table in reversed(_TABLES):
        op.drop_table(table, schema="public")
    for role in roles:
        op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{role}"')
