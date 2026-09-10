"""Add bounded therapist business-currentness authorities.

Revision ID: 20260912_0043
Revises: 20260911_0042
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260912_0043"
down_revision = "20260911_0042"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212043
_ROLE_CONFIG = (
    ("therapist_onboarding_writer_role", "KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"),
    ("therapist_review_writer_role", "KG_THERAPIST_REVIEW_WRITER_ROLE", "KG_THERAPIST_REVIEW_WRITER_DATABASE_URL"),
    ("therapist_reader_role", "KG_THERAPIST_READER_ROLE", "KG_THERAPIST_READER_DATABASE_URL"),
    ("member_enrollment_reader_role", "KG_MEMBER_ENROLLMENT_READER_ROLE", "KG_MEMBER_ENROLLMENT_READER_DATABASE_URL"),
    ("member_case_writer_role", "KG_MEMBER_CASE_WRITER_ROLE", "KG_MEMBER_CASE_WRITER_DATABASE_URL"),
)
_SIGNATURES = {
    "institution": "public.slice2_institution_business_currentness_v1(BIGINT,BIGINT,TEXT)",
    "activation": "public.slice2_therapist_activation_currentness_v1(UUID)",
    "onboarding": "public.slice2_therapist_onboarding_currentness_v1(BIGINT,BIGINT)",
    "self_exit": "public.slice2_therapist_self_exit_currentness_v1(BIGINT,BIGINT,TEXT,TEXT)",
    "service": "public.slice3_therapist_service_currentness_v1(BIGINT,BIGINT)",
    "reviewer": "public.slice2_therapist_reviewer_currentness_v1(BIGINT)",
    "target": "public.slice2_therapist_review_target_currentness_v1(BIGINT,UUID)",
    "item": "public.slice2_therapist_review_item_currentness_v1(BIGINT,UUID)",
}


def _configuration_error() -> None:
    raise RuntimeError("THERAPIST_CURRENTNESS_CONFIGURATION_INVALID") from None


def _roles() -> dict[str, str]:
    values: dict[str, str] = {}
    targets: set[tuple[object, object, object]] = set()
    for name, role_var, url_var in _ROLE_CONFIG:
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
        values[name] = role
        targets.add((url.host, url.port, url.database))
    if len(set(values.values())) != len(values) or len(targets) != 1:
        _configuration_error()

    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    rows = connection.execute(
        sa.text(
            "SELECT rolname,oid,rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(:roles)"
        ),
        {"roles": sorted(values.values())},
    ).mappings().all()
    if len(rows) != len(values) or current_user in values.values() or any(
        row[flag]
        for row in rows
        for flag in (
            "rolsuper", "rolcreaterole", "rolcreatedb", "rolinherit",
            "rolreplication", "rolbypassrls",
        )
    ):
        _configuration_error()
    role_oids = [row["oid"] for row in rows]
    membership_exists = bool(
        connection.execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_auth_members "
                "WHERE member=ANY(:oids) OR roleid=ANY(:oids))"
            ),
            {"oids": role_oids},
        ).scalar_one()
    )
    if membership_exists:
        _configuration_error()
    return values


def _lock() -> None:
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})


def _create_functions(roles: dict[str, str]) -> None:
    onboarding = roles["therapist_onboarding_writer_role"]
    reviewer = roles["therapist_review_writer_role"]
    reader = roles["therapist_reader_role"]
    member_reader = roles["member_enrollment_reader_role"]
    case_writer = roles["member_case_writer_role"]

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_institution_business_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT,p_claimed_role TEXT
) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ;
  tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user NOT IN ('{onboarding}','{reader}') THEN
    RAISE EXCEPTION 'SLICE2_INSTITUTION_BUSINESS_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_claimed_tenant_id IS NULL
     OR p_claimed_role NOT IN ('org_admin','org_operator') THEN RETURN NULL; END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>p_claimed_role OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN NULL; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status
    FROM public.tenant tenant_row WHERE tenant_row.id=p_claimed_tenant_id
    FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN NULL; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_INSTITUTION_BUSINESS_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_therapist_activation_currentness_v1(p_invitation_id UUID)
RETURNS TABLE(tenant_id BIGINT,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE candidate_tenant BIGINT; locked_tenant BIGINT; tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user <> '{onboarding}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_ACTIVATION_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT invitation_row.tenant_id INTO candidate_tenant
    FROM public.therapist_invitation invitation_row
    WHERE invitation_row.invitation_id=p_invitation_id;
  IF NOT FOUND THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT invitation_row.tenant_id INTO locked_tenant
    FROM public.therapist_invitation invitation_row
    WHERE invitation_row.invitation_id=p_invitation_id
    FOR UPDATE OF invitation_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=candidate_tenant
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_ACTIVATION_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT candidate_tenant,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_therapist_onboarding_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT; totp_current BOOLEAN; public_id UUID;
BEGIN
  IF session_user NOT IN ('{onboarding}','{reviewer}','{reader}') THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_ONBOARDING_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  IF session_user IN ('{onboarding}','{reviewer}') THEN
    SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,profile_row.totp_enabled
      INTO profile_id,profile_tenant,profile_status,totp_current
      FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
      FOR UPDATE OF profile_row;
  ELSE
    SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,profile_row.totp_enabled
      INTO profile_id,profile_tenant,profile_status,totp_current
      FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
      FOR SHARE OF profile_row;
  END IF;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR profile_status NOT IN ('ACTIVATED','DRAFT','SUBMITTED','UNDER_REVIEW',
       'NEEDS_CORRECTION','RESUBMITTED','APPROVED_ACTIVE','SUSPENDED')
     OR totp_current IS NOT TRUE THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_ONBOARDING_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_therapist_self_exit_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT,
  p_idempotency_key TEXT,p_request_digest TEXT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT; profile_version BIGINT;
  profile_exited_at TIMESTAMPTZ; profile_cases INTEGER; totp_current BOOLEAN;
  public_id UUID; actor_scope_value TEXT; receipt_count BIGINT;
  status_count BIGINT; audit_count BIGINT; outbox_count BIGINT;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_SELF_EXIT_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_claimed_tenant_id IS NULL
     OR NOT (length(p_idempotency_key) BETWEEN 1 AND 128)
     OR p_request_digest !~ '^[0-9a-f]{{64}}$' THEN RETURN; END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,
         profile_row.version,profile_row.exited_at,profile_row.active_case_count,
         profile_row.totp_enabled
    INTO profile_id,profile_tenant,profile_status,profile_version,
         profile_exited_at,profile_cases,totp_current
    FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
    FOR UPDATE OF profile_row;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR totp_current IS NOT TRUE THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_SELF_EXIT_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  IF profile_status IN ('APPROVED_ACTIVE','SUSPENDED') THEN
    RETURN QUERY SELECT profile_id,public_id;
    RETURN;
  END IF;
  IF profile_status='EXITED' AND profile_exited_at IS NOT NULL THEN
    IF profile_cases<>0 THEN RETURN; END IF;
  ELSE
    RETURN;
  END IF;

  actor_scope_value := 'actor:' || p_actor_user_id::TEXT || ':therapist:' || profile_id::TEXT;
  SELECT count(*) INTO receipt_count
    FROM public.therapist_workflow_idempotency idempotency_row
    WHERE idempotency_row.actor_scope=actor_scope_value
      AND idempotency_row.operation='EXITED'
      AND idempotency_row.idempotency_key=p_idempotency_key
      AND idempotency_row.request_digest=p_request_digest;
  SELECT count(*) INTO status_count
    FROM public.therapist_status_decision status_row
    WHERE status_row.therapist_id=profile_id
      AND status_row.decision='EXITED'
      AND status_row.actor_kind='USER'
      AND status_row.actor_user_id=p_actor_user_id
      AND status_row.request_digest=p_request_digest
      AND status_row.expected_profile_version+1=profile_version;
  SELECT count(*) INTO audit_count
    FROM public.therapist_workflow_audit audit_row
    JOIN public.therapist_workflow_idempotency idempotency_row
      ON idempotency_row.actor_scope=audit_row.actor_scope
     AND idempotency_row.operation='EXITED'
     AND idempotency_row.idempotency_key=p_idempotency_key
     AND idempotency_row.request_digest=p_request_digest
    WHERE audit_row.actor_scope=actor_scope_value
      AND audit_row.action='THERAPIST_EXITED'
      AND audit_row.object_id=profile_id
      AND audit_row.result='SUCCESS'
      AND audit_row.postimage_digest=idempotency_row.postimage_digest;
  SELECT count(*) INTO outbox_count
    FROM public.therapist_workflow_outbox outbox_row
    WHERE outbox_row.event_type='THERAPIST_EXITED'
      AND outbox_row.aggregate_id=profile_id
      AND outbox_row.tenant_id=p_claimed_tenant_id;
  IF receipt_count<>1 OR status_count<>1 OR audit_count<>1 OR outbox_count<>1 THEN RETURN; END IF;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice3_therapist_service_currentness_v1(
  p_actor_user_id BIGINT,p_claimed_tenant_id BIGINT
) RETURNS TABLE(therapist_id UUID,tenant_public_id UUID)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE
  value_role TEXT; value_status TEXT; value_tenant BIGINT;
  value_exited TIMESTAMPTZ; value_deletion TIMESTAMPTZ; tenant_status TEXT;
  profile_id UUID; profile_tenant BIGINT; profile_status TEXT;
  qualification_id UUID; valid_until DATE; public_id UUID;
BEGIN
  IF session_user NOT IN ('{member_reader}','{case_writer}') THEN
    RAISE EXCEPTION 'SLICE3_THERAPIST_SERVICE_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role::TEXT,actor_user.status::TEXT,actor_user.tenant_id,
         actor_user.exited_at,actor_user.deletion_requested_at
    INTO value_role,value_status,value_tenant,value_exited,value_deletion
    FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF NOT FOUND OR value_role<>'therapist' OR value_status<>'active'
     OR value_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR value_exited IS NOT NULL OR value_deletion IS NOT NULL THEN RETURN; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=p_claimed_tenant_id FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN; END IF;
  SELECT profile_row.therapist_id,profile_row.tenant_id,profile_row.status,
         profile_row.current_qualification_version_id,profile_row.qualification_valid_until
    INTO profile_id,profile_tenant,profile_status,qualification_id,valid_until
    FROM public.therapist_profile profile_row WHERE profile_row.user_id=p_actor_user_id
    FOR SHARE OF profile_row;
  IF NOT FOUND OR profile_tenant IS DISTINCT FROM p_claimed_tenant_id
     OR profile_status<>'APPROVED_ACTIVE' OR qualification_id IS NULL
     OR valid_until IS NULL
     OR valid_until < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai')::DATE THEN RETURN; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=p_claimed_tenant_id
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE3_THERAPIST_SERVICE_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN QUERY SELECT profile_id,public_id;
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_therapist_reviewer_currentness_v1(p_actor_user_id BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER STABLE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE value_current BOOLEAN;
BEGIN
  IF session_user <> '{reader}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEWER_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO value_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id;
  RETURN COALESCE(value_current,false);
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_therapist_review_target_currentness_v1(
  p_actor_user_id BIGINT,p_subject_therapist_id UUID
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE reviewer_current BOOLEAN; candidate_tenant BIGINT; tenant_status TEXT;
        locked_tenant BIGINT; public_id UUID;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO reviewer_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF COALESCE(reviewer_current,false) IS NOT TRUE THEN RETURN false; END IF;
  SELECT profile_row.tenant_id INTO candidate_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=p_subject_therapist_id;
  IF NOT FOUND THEN RETURN false; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN false; END IF;
  SELECT profile_row.tenant_id INTO locked_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=p_subject_therapist_id FOR UPDATE OF profile_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN false; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=candidate_tenant
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN false;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_TARGET_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN true;
END $$
"""))

    op.execute(sa.text(f"""
CREATE FUNCTION public.slice2_therapist_review_item_currentness_v1(
  p_actor_user_id BIGINT,p_review_item_id UUID
) RETURNS UUID
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path=pg_catalog,pg_temp AS $$
DECLARE reviewer_current BOOLEAN; candidate_therapist UUID; candidate_tenant BIGINT;
        locked_tenant BIGINT; locked_therapist UUID; tenant_status TEXT; public_id UUID;
BEGIN
  IF session_user <> '{reviewer}' THEN
    RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  SELECT review_item_row.therapist_id,profile_row.tenant_id
    INTO candidate_therapist,candidate_tenant
    FROM public.therapist_review_item review_item_row
    JOIN public.therapist_profile profile_row ON profile_row.therapist_id=review_item_row.therapist_id
    WHERE review_item_row.review_item_id=p_review_item_id;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT actor_user.role='super_admin' AND actor_user.status='active'
         AND actor_user.tenant_id IS NULL
         AND actor_user.exited_at IS NULL AND actor_user.deletion_requested_at IS NULL
    INTO reviewer_current FROM public."user" actor_user WHERE actor_user.id=p_actor_user_id
    FOR SHARE OF actor_user;
  IF COALESCE(reviewer_current,false) IS NOT TRUE THEN RETURN NULL; END IF;
  SELECT tenant_row.status::TEXT INTO tenant_status FROM public.tenant tenant_row
    WHERE tenant_row.id=candidate_tenant FOR SHARE OF tenant_row;
  IF NOT FOUND OR tenant_status<>'active' THEN RETURN NULL; END IF;
  SELECT profile_row.tenant_id INTO locked_tenant FROM public.therapist_profile profile_row
    WHERE profile_row.therapist_id=candidate_therapist FOR UPDATE OF profile_row;
  IF NOT FOUND OR locked_tenant IS DISTINCT FROM candidate_tenant THEN RETURN NULL; END IF;
  SELECT review_item_row.therapist_id INTO locked_therapist
    FROM public.therapist_review_item review_item_row
    WHERE review_item_row.review_item_id=p_review_item_id
    FOR UPDATE OF review_item_row;
  IF NOT FOUND OR locked_therapist IS DISTINCT FROM candidate_therapist THEN RETURN NULL; END IF;
  BEGIN
    SELECT institution_application.tenant_public_id INTO STRICT public_id
      FROM public.institution_application institution_application
      WHERE institution_application.tenant_internal_id=candidate_tenant
        AND institution_application.status='APPROVED'
        AND institution_application.tenant_public_id IS NOT NULL;
  EXCEPTION WHEN NO_DATA_FOUND THEN RETURN NULL;
    WHEN TOO_MANY_ROWS THEN
      RAISE EXCEPTION 'SLICE2_THERAPIST_REVIEW_ITEM_CURRENTNESS_AMBIGUOUS' USING ERRCODE='21000';
  END;
  RETURN candidate_therapist;
END $$
"""))


def _apply_execute(roles: dict[str, str], *, grant: bool) -> None:
    assignments = (
        ("institution", roles["therapist_onboarding_writer_role"]),
        ("institution", roles["therapist_reader_role"]),
        ("activation", roles["therapist_onboarding_writer_role"]),
        ("onboarding", roles["therapist_onboarding_writer_role"]),
        ("onboarding", roles["therapist_review_writer_role"]),
        ("onboarding", roles["therapist_reader_role"]),
        ("self_exit", roles["therapist_review_writer_role"]),
        ("service", roles["member_enrollment_reader_role"]),
        ("service", roles["member_case_writer_role"]),
        ("reviewer", roles["therapist_reader_role"]),
        ("target", roles["therapist_review_writer_role"]),
        ("item", roles["therapist_review_writer_role"]),
    )
    for function_name, role in assignments:
        action = "GRANT EXECUTE" if grant else "REVOKE EXECUTE"
        direction = "TO" if grant else "FROM"
        op.execute(sa.text(f'{action} ON FUNCTION {_SIGNATURES[function_name]} {direction} "{role}"'))


def upgrade() -> None:
    roles = _roles()
    _lock()
    _create_functions(roles)
    for signature in _SIGNATURES.values():
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
    _apply_execute(roles, grant=True)


def downgrade() -> None:
    roles = _roles()
    _lock()
    _apply_execute(roles, grant=False)
    for signature in reversed(tuple(_SIGNATURES.values())):
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f"DROP FUNCTION {signature}"))
