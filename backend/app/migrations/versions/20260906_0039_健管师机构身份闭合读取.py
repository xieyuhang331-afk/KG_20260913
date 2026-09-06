"""Add the closed Slice 2 institution identity lock authority."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260906_0039"
down_revision = "20260904_0038"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212039
_ROLE_VAR = "KG_DATABASE_USER"
_URL_VAR = "KG_IDENTITY_APPLICATION_DATABASE_URL"
_SIGNATURE = "public.slice2_institution_identity_authority_v1(BIGINT)"
_CORRECTION_SIGNATURE = "public.slice2_therapist_correction_authority_v1(BIGINT,BIGINT,UUID,UUID,UUID)"


def _configuration_error() -> None:
    raise RuntimeError(
        "SLICE2_INSTITUTION_AUTHORITY_CONFIGURATION_INVALID"
    ) from None


def _application_role(role_variable=_ROLE_VAR, url_variable=_URL_VAR) -> str:
    role = os.getenv(role_variable, "").strip()
    raw_url = os.getenv(url_variable, "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
        _configuration_error()
    try:
        url = make_url(raw_url)
    except Exception:
        _configuration_error()
    if url.drivername != "postgresql+asyncpg" or url.username != role:
        _configuration_error()

    connection = op.get_bind()
    current_user = str(
        connection.execute(sa.text("SELECT current_user")).scalar_one()
    )
    row = connection.execute(
        sa.text(
            "SELECT rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    membership_exists = bool(
        connection.execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_auth_members m "
                "JOIN pg_roles member_role ON member_role.oid=m.member "
                "JOIN pg_roles parent_role ON parent_role.oid=m.roleid "
                "WHERE member_role.rolname=:role OR parent_role.rolname=:role)"
            ),
            {"role": role},
        ).scalar_one()
    )
    if current_user == role or row is None or any(row.values()) or membership_exists:
        _configuration_error()
    return role


def _lock() -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )


def upgrade() -> None:
    role = _application_role()
    writer = _application_role("KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL")
    _lock()
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.slice2_institution_identity_authority_v1(
              value_tenant BIGINT
            ) RETURNS UUID
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE value_public UUID;
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF value_tenant IS NULL OR value_tenant <= 0 THEN
                RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;
              BEGIN
                SELECT a.tenant_public_id INTO STRICT value_public
                FROM public.institution_application a
                JOIN public.tenant t ON t.id=a.tenant_internal_id
                WHERE a.tenant_internal_id=value_tenant AND a.status='APPROVED'
                  AND a.tenant_public_id IS NOT NULL AND t.status='active'
                FOR SHARE OF a,t;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN RETURN NULL;
                WHEN TOO_MANY_ROWS THEN
                  RAISE EXCEPTION 'SLICE2_INSTITUTION_AUTHORITY_AMBIGUOUS'
                    USING ERRCODE='21000';
              END;
              RETURN value_public;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO "{role}"'))
    op.execute(sa.text(f"""
        CREATE FUNCTION public.slice2_therapist_correction_authority_v1(
          actor_user_id BIGINT, actor_tenant_id BIGINT, subject_therapist_id UUID,
          correction_decision_id UUID, renewal_review_item_id UUID
        ) RETURNS TABLE (
          decision_therapist_id UUID, decision_kind VARCHAR, decision_revision_id UUID,
          decision_review_item_id UUID, allow_real_name BOOLEAN, allow_display_name BOOLEAN,
          allow_practice_summary BOOLEAN, allow_service_tags BOOLEAN, qualification_target UUID,
          item_therapist_id UUID, item_review_kind VARCHAR, item_status VARCHAR,
          item_version BIGINT, item_qualification_version_id UUID
        ) LANGUAGE plpgsql SECURITY DEFINER VOLATILE
        SET search_path=pg_catalog,pg_temp AS $$
        DECLARE
          v_decision RECORD;
          v_item RECORD;
          v_qualification UUID;
          v_field JSONB;
          v_name TEXT;
          v_count BIGINT;
        BEGIN
          IF session_user <> '{writer}' THEN
            RAISE EXCEPTION 'SLICE2_CORRECTION_AUTHORITY_FORBIDDEN' USING ERRCODE='42501';
          END IF;
          IF actor_user_id IS NULL OR actor_user_id <= 0 OR actor_tenant_id IS NULL
            OR actor_tenant_id <= 0 OR subject_therapist_id IS NULL OR correction_decision_id IS NULL THEN
            RAISE EXCEPTION 'SLICE2_CORRECTION_AUTHORITY_INPUT_INVALID' USING ERRCODE='22023';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM public.therapist_profile p
            WHERE p.therapist_id=subject_therapist_id AND p.user_id=actor_user_id
              AND p.tenant_id=actor_tenant_id) THEN RETURN; END IF;
          IF renewal_review_item_id IS NOT NULL THEN
            SELECT ri.therapist_id,ri.review_item_id,ri.revision_id,ri.review_kind,
              ri.status,ri.version,ri.qualification_version_id INTO v_item
            FROM public.therapist_review_item ri
            WHERE ri.review_item_id=renewal_review_item_id AND ri.therapist_id=subject_therapist_id
            FOR UPDATE OF ri;
            IF NOT FOUND THEN RETURN; END IF;
          END IF;
          SELECT rd.therapist_id,rd.decision,rd.revision_id,rd.review_item_id,rd.correction_fields
            INTO v_decision FROM public.therapist_review_decision rd
          WHERE rd.decision_id=correction_decision_id AND rd.therapist_id=subject_therapist_id
          FOR UPDATE OF rd;
          IF NOT FOUND THEN RETURN; END IF;
          IF renewal_review_item_id IS NULL THEN
            SELECT ri.therapist_id,ri.review_item_id,ri.revision_id,ri.review_kind,
              ri.status,ri.version,ri.qualification_version_id INTO v_item
            FROM public.therapist_review_item ri
            WHERE ri.review_item_id=v_decision.review_item_id AND ri.therapist_id=subject_therapist_id;
            IF NOT FOUND THEN RETURN; END IF;
          END IF;
          IF v_decision.decision <> 'NEEDS_CORRECTION' OR v_item.status <> 'DECIDED'
            OR v_item.review_item_id <> v_decision.review_item_id
            OR v_item.revision_id <> v_decision.revision_id
            OR v_item.review_kind <> (CASE WHEN renewal_review_item_id IS NULL THEN 'INITIAL' ELSE 'RENEWAL' END)
            THEN RETURN; END IF;
          SELECT count(*) INTO v_count FROM public.therapist_profile_revision_qualification q
            JOIN public.therapist_profile_revision r ON r.revision_id=q.revision_id AND r.therapist_id=q.therapist_id
          WHERE q.therapist_id=subject_therapist_id AND q.revision_id=v_decision.revision_id AND q.position=1;
          IF v_count <> 1 THEN RETURN; END IF;
          SELECT q.qualification_version_id INTO v_qualification
            FROM public.therapist_profile_revision_qualification q
          WHERE q.therapist_id=subject_therapist_id AND q.revision_id=v_decision.revision_id AND q.position=1;
          IF renewal_review_item_id IS NOT NULL AND v_item.qualification_version_id IS DISTINCT FROM v_qualification THEN RETURN; END IF;
          IF jsonb_typeof(v_decision.correction_fields) IS DISTINCT FROM 'array' THEN RETURN; END IF;
          IF jsonb_array_length(v_decision.correction_fields) NOT BETWEEN 1 AND 5 THEN RETURN; END IF;
          allow_real_name:=false; allow_display_name:=false; allow_practice_summary:=false; allow_service_tags:=false;
          qualification_target:=NULL;
          FOR v_field IN SELECT f.value FROM jsonb_array_elements(v_decision.correction_fields) f(value) LOOP
            IF jsonb_typeof(v_field) <> 'string' THEN RETURN; END IF;
            v_name:=v_field #>> '{{}}';
            IF length(v_name)>50 THEN RETURN; END IF;
            CASE v_name
              WHEN 'real_name' THEN IF allow_real_name THEN RETURN; END IF; allow_real_name:=true;
              WHEN 'display_name' THEN IF allow_display_name THEN RETURN; END IF; allow_display_name:=true;
              WHEN 'practice_summary' THEN IF allow_practice_summary THEN RETURN; END IF; allow_practice_summary:=true;
              WHEN 'service_tags' THEN IF allow_service_tags THEN RETURN; END IF; allow_service_tags:=true;
              ELSE
                IF v_name !~ '^qualification:[0-9a-fA-F]{{8}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{4}}-[0-9a-fA-F]{{12}}$'
                  OR qualification_target IS NOT NULL THEN RETURN; END IF;
                qualification_target:=substring(v_name FROM 15)::uuid;
                IF qualification_target <> v_qualification THEN RETURN; END IF;
            END CASE;
          END LOOP;
          IF renewal_review_item_id IS NOT NULL AND qualification_target IS NULL THEN RETURN; END IF;
          decision_therapist_id:=v_decision.therapist_id;
          decision_kind:=v_decision.decision;
          decision_revision_id:=v_decision.revision_id;
          decision_review_item_id:=v_decision.review_item_id;
          item_therapist_id:=v_item.therapist_id;
          item_review_kind:=v_item.review_kind;
          item_status:=v_item.status;
          item_version:=v_item.version;
          item_qualification_version_id:=v_item.qualification_version_id;
          RETURN NEXT;
        END $$
    """))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_CORRECTION_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {_CORRECTION_SIGNATURE} TO "{writer}"'))


def downgrade() -> None:
    role = _application_role()
    writer = _application_role("KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL")
    _lock()
    op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {_CORRECTION_SIGNATURE} FROM "{writer}"'))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_CORRECTION_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_CORRECTION_SIGNATURE}"))
    op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM "{role}"'))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_SIGNATURE}"))
