"""Add bounded compatibility functions for the legacy user-health routes.

Revision ID: 20260914_0047
Revises: 20260914_0046
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260914_0047"
down_revision = "20260914_0046"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212047
_ROLE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,62}")
_SIGNATURES = (
    "public.r4_member_health_currentness_v1(BIGINT)",
    "public.r4_member_legacy_health_profile_read_v1(BIGINT,BIGINT)",
    "public.r4_member_legacy_health_profile_create_v1(BIGINT,BIGINT,VARCHAR,DATE,NUMERIC,NUMERIC,VARCHAR,JSONB,JSONB,JSONB,VARCHAR,VARCHAR,JSONB,VARCHAR,VARCHAR,TIMESTAMPTZ)",
    "public.r4_member_legacy_health_profile_update_v1(BIGINT,BIGINT,TIMESTAMPTZ,VARCHAR,DATE,NUMERIC,NUMERIC,VARCHAR,TIMESTAMPTZ)",
    "public.r4_member_self_health_indicator_history_v1(BIGINT,VARCHAR,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,INTEGER)",
    "public.r4_member_legacy_health_indicator_create_v1(BIGINT,BIGINT,VARCHAR,VARCHAR,NUMERIC,VARCHAR,VARCHAR,TIMESTAMPTZ)",
    "public.r4_member_legacy_health_indicator_history_v1(BIGINT,BIGINT,VARCHAR,TIMESTAMPTZ,TIMESTAMPTZ,INTEGER)",
    "public.r4_member_legacy_health_indicator_latest_v1(BIGINT,BIGINT)",
    "public.r4_member_self_detection_report_history_v1(BIGINT,VARCHAR,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,BIGINT,INTEGER)",
    "public.r4_member_self_detection_report_read_v1(BIGINT,BIGINT)",
)


def _configuration_error() -> None:
    raise RuntimeError("R4_LEGACY_HEALTH_BOUNDARY_CONFIGURATION_INVALID") from None


def _application_role() -> str:
    role = os.getenv("KG_DATABASE_USER", "").strip()
    raw_url = os.getenv("KG_IDENTITY_APPLICATION_DATABASE_URL", "").strip()
    if not _ROLE_PATTERN.fullmatch(role) or not raw_url:
        _configuration_error()
    try:
        url = make_url(raw_url)
    except Exception:
        _configuration_error()
    if url.drivername != "postgresql+asyncpg" or url.username != role:
        _configuration_error()

    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    row = connection.execute(
        sa.text(
            "SELECT oid,rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    if row is None or current_user == role or any(
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
    membership_exists = bool(
        connection.execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_auth_members "
                "WHERE member=:oid OR roleid=:oid)"
            ),
            {"oid": row["oid"]},
        ).scalar_one()
    )
    if membership_exists:
        _configuration_error()
    return role


def _lock() -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )


def _create_functions(role: str) -> None:
    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_health_currentness_v1(p_actor_user_id BIGINT)
RETURNS TABLE (
  id BIGINT,
  role public.user_role,
  status public.user_status,
  verify_status VARCHAR(20)
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0 THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  RETURN QUERY
  SELECT actor.id,actor.role,actor.status,actor.verify_status
  FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id
  FOR SHARE OF actor;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_legacy_health_profile_read_v1(
  p_actor_user_id BIGINT,p_target_user_id BIGINT
)
RETURNS TABLE (
  id BIGINT,user_id BIGINT,gender VARCHAR(5),birth_date DATE,
  height NUMERIC(5,1),weight NUMERIC(5,1),blood_type VARCHAR(5),
  medical_history JSONB,allergy_history JSONB,family_history JSONB,
  smoking VARCHAR(10),drinking VARCHAR(10),symptoms JSONB,
  sleep_quality VARCHAR(50),bowel_urination VARCHAR(100),
  created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_PROFILE_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_target_user_id IS DISTINCT FROM p_actor_user_id THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_SCOPE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  SELECT profile.id,profile.user_id,profile.gender,profile.birth_date,
         profile.height,profile.weight,profile.blood_type,
         profile.medical_history,profile.allergy_history,profile.family_history,
         profile.smoking,profile.drinking,profile.symptoms,
         profile.sleep_quality,profile.bowel_urination,
         profile.created_at,profile.updated_at
  FROM public.health_profile AS profile
  WHERE profile.user_id=p_target_user_id;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_legacy_health_profile_create_v1(
  p_actor_user_id BIGINT,p_target_user_id BIGINT,
  p_gender VARCHAR,p_birth_date DATE,p_height NUMERIC,p_weight NUMERIC,
  p_blood_type VARCHAR,p_medical_history JSONB,p_allergy_history JSONB,
  p_family_history JSONB,p_smoking VARCHAR,p_drinking VARCHAR,p_symptoms JSONB,
  p_sleep_quality VARCHAR,p_bowel_urination VARCHAR,p_updated_at TIMESTAMPTZ
)
RETURNS TABLE (
  id BIGINT,user_id BIGINT,gender VARCHAR(5),birth_date DATE,
  height NUMERIC(5,1),weight NUMERIC(5,1),blood_type VARCHAR(5),
  medical_history JSONB,allergy_history JSONB,family_history JSONB,
  smoking VARCHAR(10),drinking VARCHAR(10),symptoms JSONB,
  sleep_quality VARCHAR(50),bowel_urination VARCHAR(100),
  created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_PROFILE_CREATE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_target_user_id IS DISTINCT FROM p_actor_user_id
     OR p_gender IS NULL OR p_birth_date IS NULL OR p_updated_at IS NULL THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'r4-legacy-health-profile:' || p_actor_user_id::TEXT,47
    )
  );
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  INSERT INTO public.health_profile AS profile (
    user_id,gender,birth_date,height,weight,blood_type,
    medical_history,allergy_history,family_history,smoking,drinking,
    symptoms,sleep_quality,bowel_urination,updated_at
  ) VALUES (
    p_target_user_id,p_gender,p_birth_date,p_height,p_weight,p_blood_type,
    p_medical_history,p_allergy_history,p_family_history,p_smoking,p_drinking,
    p_symptoms,p_sleep_quality,p_bowel_urination,p_updated_at
  )
  RETURNING profile.id,profile.user_id,profile.gender,profile.birth_date,
    profile.height,profile.weight,profile.blood_type,
    profile.medical_history,profile.allergy_history,profile.family_history,
    profile.smoking,profile.drinking,profile.symptoms,
    profile.sleep_quality,profile.bowel_urination,
    profile.created_at,profile.updated_at;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_legacy_health_profile_update_v1(
  p_actor_user_id BIGINT,p_target_user_id BIGINT,p_expected_updated_at TIMESTAMPTZ,
  p_gender VARCHAR,p_birth_date DATE,p_height NUMERIC,p_weight NUMERIC,
  p_blood_type VARCHAR,p_updated_at TIMESTAMPTZ
)
RETURNS TABLE (
  id BIGINT,user_id BIGINT,gender VARCHAR(5),birth_date DATE,
  height NUMERIC(5,1),weight NUMERIC(5,1),blood_type VARCHAR(5),
  medical_history JSONB,allergy_history JSONB,family_history JSONB,
  smoking VARCHAR(10),drinking VARCHAR(10),symptoms JSONB,
  sleep_quality VARCHAR(50),bowel_urination VARCHAR(100),
  created_at TIMESTAMPTZ,updated_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_PROFILE_UPDATE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_target_user_id IS DISTINCT FROM p_actor_user_id
     OR p_expected_updated_at IS NULL OR p_gender IS NULL
     OR p_birth_date IS NULL OR p_updated_at IS NULL THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'r4-legacy-health-profile:' || p_actor_user_id::TEXT,47
    )
  );
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  UPDATE public.health_profile AS profile
  SET gender=p_gender,birth_date=p_birth_date,height=p_height,weight=p_weight,
      blood_type=p_blood_type,updated_at=p_updated_at
  WHERE profile.user_id=p_target_user_id
    AND profile.updated_at=p_expected_updated_at
  RETURNING profile.id,profile.user_id,profile.gender,profile.birth_date,
    profile.height,profile.weight,profile.blood_type,
    profile.medical_history,profile.allergy_history,profile.family_history,
    profile.smoking,profile.drinking,profile.symptoms,
    profile.sleep_quality,profile.bowel_urination,
    profile.created_at,profile.updated_at;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_self_health_indicator_history_v1(
  p_actor_user_id BIGINT,p_indicator_type VARCHAR,p_start_at TIMESTAMPTZ,
  p_end_at TIMESTAMPTZ,p_cursor_recorded_at TIMESTAMPTZ,
  p_cursor_id BIGINT,p_limit INTEGER
)
RETURNS TABLE (
  id BIGINT,batch_id VARCHAR(36),indicator_type VARCHAR(30),
  value NUMERIC(10,2),unit VARCHAR(10),source VARCHAR(20),recorded_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_INDICATOR_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_limit IS NULL OR p_limit < 1 OR p_limit > 201
     OR (p_start_at IS NOT NULL AND p_end_at IS NOT NULL AND p_start_at > p_end_at)
     OR ((p_cursor_recorded_at IS NULL) <> (p_cursor_id IS NULL))
     OR (p_cursor_id IS NOT NULL AND p_cursor_id <= 0) THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  SELECT item.id,item.batch_id,item.indicator_type,item.value,item.unit,
         item.source,item.recorded_at
  FROM public.health_indicator AS item
  WHERE item.user_id=p_actor_user_id
    AND (p_indicator_type IS NULL OR item.indicator_type=p_indicator_type)
    AND (p_start_at IS NULL OR item.recorded_at >= p_start_at)
    AND (p_end_at IS NULL OR item.recorded_at <= p_end_at)
    AND (p_cursor_recorded_at IS NULL OR item.recorded_at < p_cursor_recorded_at
      OR (item.recorded_at=p_cursor_recorded_at AND item.id < p_cursor_id))
  ORDER BY item.recorded_at DESC,item.id DESC
  LIMIT p_limit;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_legacy_health_indicator_create_v1(
  p_actor_user_id BIGINT,p_target_user_id BIGINT,p_batch_id VARCHAR,
  p_indicator_type VARCHAR,p_value NUMERIC,p_unit VARCHAR,p_source VARCHAR,
  p_recorded_at TIMESTAMPTZ
)
RETURNS TABLE (
  id BIGINT,batch_id VARCHAR(36),indicator_type VARCHAR(30),
  value NUMERIC(10,2),unit VARCHAR(10),source VARCHAR(20),
  recorded_at TIMESTAMPTZ,created_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_INDICATOR_CREATE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_target_user_id IS DISTINCT FROM p_actor_user_id
     OR p_indicator_type IS NULL OR p_value IS NULL OR p_unit IS NULL
     OR p_source IS DISTINCT FROM 'APP' OR p_recorded_at IS NULL THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.health_profile AS profile
    WHERE profile.user_id=p_target_user_id
  ) THEN
    RAISE EXCEPTION 'R4_HEALTH_PROFILE_REQUIRED' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  INSERT INTO public.health_indicator AS item (
    user_id,batch_id,indicator_type,value,unit,source,recorded_at
  ) VALUES (
    p_target_user_id,p_batch_id,p_indicator_type,p_value,p_unit,p_source,p_recorded_at
  )
  RETURNING item.id,item.batch_id,item.indicator_type,item.value,item.unit,
            item.source,item.recorded_at,item.created_at;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_legacy_health_indicator_history_v1(
  p_actor_user_id BIGINT,p_target_user_id BIGINT,p_indicator_type VARCHAR,
  p_start_at TIMESTAMPTZ,p_end_at TIMESTAMPTZ,p_limit INTEGER
)
RETURNS TABLE (
  id BIGINT,batch_id VARCHAR(36),indicator_type VARCHAR(30),
  value NUMERIC(10,2),unit VARCHAR(10),source VARCHAR(20),
  recorded_at TIMESTAMPTZ,created_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_INDICATOR_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_target_user_id IS DISTINCT FROM p_actor_user_id
     OR p_limit IS NULL OR p_limit < 1 OR p_limit > 200
     OR (p_start_at IS NOT NULL AND p_end_at IS NOT NULL AND p_start_at > p_end_at) THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  SELECT item.id,item.batch_id,item.indicator_type,item.value,item.unit,
         item.source,item.recorded_at,item.created_at
  FROM public.health_indicator AS item
  WHERE item.user_id=p_target_user_id
    AND (p_indicator_type IS NULL OR item.indicator_type=p_indicator_type)
    AND (p_start_at IS NULL OR item.recorded_at >= p_start_at)
    AND (p_end_at IS NULL OR item.recorded_at <= p_end_at)
  ORDER BY item.recorded_at DESC
  LIMIT p_limit;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_legacy_health_indicator_latest_v1(
  p_actor_user_id BIGINT,p_target_user_id BIGINT
)
RETURNS TABLE (
  id BIGINT,batch_id VARCHAR(36),indicator_type VARCHAR(30),
  value NUMERIC(10,2),unit VARCHAR(10),source VARCHAR(20),
  recorded_at TIMESTAMPTZ,created_at TIMESTAMPTZ
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_HEALTH_INDICATOR_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_target_user_id IS DISTINCT FROM p_actor_user_id THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_SCOPE_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  SELECT DISTINCT ON (item.indicator_type)
         item.id,item.batch_id,item.indicator_type,item.value,item.unit,
         item.source,item.recorded_at,item.created_at
  FROM public.health_indicator AS item
  WHERE item.user_id=p_target_user_id
  ORDER BY item.indicator_type,item.recorded_at DESC,item.id DESC;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_self_detection_report_history_v1(
  p_actor_user_id BIGINT,p_report_type VARCHAR,p_start_at TIMESTAMPTZ,
  p_end_at TIMESTAMPTZ,p_cursor_detection_time TIMESTAMPTZ,
  p_cursor_id BIGINT,p_limit INTEGER
)
RETURNS TABLE (
  id BIGINT,report_type VARCHAR(32),detection_time TIMESTAMPTZ,
  view_status VARCHAR(16),summary TEXT,report_schema_version INTEGER,
  is_initial_baseline BOOLEAN
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_DETECTION_REPORT_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_limit IS NULL OR p_limit < 1 OR p_limit > 101
     OR (p_start_at IS NOT NULL AND p_end_at IS NOT NULL AND p_start_at > p_end_at)
     OR ((p_cursor_detection_time IS NULL) <> (p_cursor_id IS NULL))
     OR (p_cursor_id IS NOT NULL AND p_cursor_id <= 0) THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  SELECT report.id,report.report_type,report.detection_time,report.view_status,
         report.summary,report.report_schema_version,
         report.id=(
           SELECT first_report.id FROM public.detection_report AS first_report
           WHERE first_report.user_id=report.user_id
           ORDER BY first_report.detection_time ASC,first_report.id ASC LIMIT 1
         ) AS is_initial_baseline
  FROM public.detection_report AS report
  WHERE report.user_id=p_actor_user_id
    AND (p_report_type IS NULL OR report.report_type=p_report_type)
    AND (p_start_at IS NULL OR report.detection_time >= p_start_at)
    AND (p_end_at IS NULL OR report.detection_time <= p_end_at)
    AND (p_cursor_detection_time IS NULL OR report.detection_time < p_cursor_detection_time
      OR (report.detection_time=p_cursor_detection_time AND report.id < p_cursor_id))
  ORDER BY report.detection_time DESC,report.id DESC
  LIMIT p_limit;
END $$
"""
        )
    )

    op.execute(
        sa.text(
            f"""
CREATE FUNCTION public.r4_member_self_detection_report_read_v1(
  p_actor_user_id BIGINT,p_report_id BIGINT
)
RETURNS TABLE (
  id BIGINT,report_type VARCHAR(32),detection_time TIMESTAMPTZ,
  view_status VARCHAR(16),summary TEXT,report_schema_version INTEGER,
  is_initial_baseline BOOLEAN,report_data JSONB
)
LANGUAGE plpgsql SECURITY DEFINER VOLATILE
SET search_path = pg_catalog AS $$
BEGIN
  IF session_user <> '{role}' THEN
    RAISE EXCEPTION 'R4_LEGACY_DETECTION_REPORT_READ_FORBIDDEN' USING ERRCODE='42501';
  END IF;
  IF p_actor_user_id IS NULL OR p_actor_user_id <= 0
     OR p_report_id IS NULL OR p_report_id <= 0 THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_INPUT_INVALID' USING ERRCODE='22023';
  END IF;
  PERFORM 1 FROM public."user" AS actor
  WHERE actor.id=p_actor_user_id AND actor.role='member'
    AND actor.status='active' AND actor.exited_at IS NULL
    AND actor.deletion_requested_at IS NULL
  FOR SHARE OF actor;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'R4_MEMBER_HEALTH_CURRENTNESS_INVALID' USING ERRCODE='P0001';
  END IF;
  RETURN QUERY
  SELECT report.id,report.report_type,report.detection_time,report.view_status,
         report.summary,report.report_schema_version,
         report.id=(
           SELECT first_report.id FROM public.detection_report AS first_report
           WHERE first_report.user_id=report.user_id
           ORDER BY first_report.detection_time ASC,first_report.id ASC LIMIT 1
         ) AS is_initial_baseline,
         report.report_data
  FROM public.detection_report AS report
  WHERE report.user_id=p_actor_user_id AND report.id=p_report_id;
END $$
"""
        )
    )


def upgrade() -> None:
    role = _application_role()
    _lock()
    _create_functions(role)
    for signature in _SIGNATURES:
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {signature} TO "{role}"'))


def _assert_empty_for_downgrade() -> None:
    has_legacy_rows = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM public.health_profile) "
            "OR EXISTS (SELECT 1 FROM public.health_indicator) "
            "OR EXISTS (SELECT 1 FROM public.detection_report)"
        )
    ).scalar_one()
    if has_legacy_rows:
        raise RuntimeError("R4 downgrade requires empty legacy tables")


def downgrade() -> None:
    role = _application_role()
    _lock()
    _assert_empty_for_downgrade()
    for signature in reversed(_SIGNATURES):
        op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {signature} FROM "{role}"'))
        op.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        op.execute(sa.text(f"DROP FUNCTION {signature}"))
