"""Add the Slice 3 review enrollment preimage authority.

Revision ID: 20260822_0027
Revises: 20260822_0026
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260822_0027"
down_revision = "20260822_0026"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212027
_SIGNATURE = (
    "public.slice3_review_enrollment_preimage_authority_v1(UUID,UUID,BIGINT)"
)


def _configuration_error() -> None:
    raise RuntimeError(
        "Slice 3 review enrollment preimage authority configuration is invalid"
    ) from None


def _review_role() -> str:
    role = os.getenv("KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE", "").strip()
    raw_url = os.getenv(
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL", ""
    ).strip()
    if re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) is None or not raw_url:
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
    review = _review_role()
    _lock()
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.slice3_review_enrollment_preimage_authority_v1(
              value_verification UUID,value_enrollment UUID,value_reviewer BIGINT
            ) RETURNS JSONB
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE
              claim_count BIGINT;
              matching_count BIGINT;
              preimage JSONB;
            BEGIN
              IF session_user <> '{review}' THEN
                RAISE EXCEPTION 'SLICE3_REVIEW_ENROLLMENT_PREIMAGE_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              PERFORM 1 FROM public.user u
              WHERE u.id=value_reviewer AND u.role='super_admin'
                AND u.status='active' AND u.tenant_id IS NULL
              FOR SHARE;
              IF NOT FOUND THEN RETURN NULL; END IF;
              PERFORM 1
              FROM public.member_identity_verification v
              JOIN public.member_identity_review_decision d
                ON d.decision_id=v.institution_decision_id
               AND d.verification_id=v.verification_id
               AND d.revision_id=v.current_revision_id
              WHERE v.verification_id=value_verification
                AND v.enrollment_id=value_enrollment
                AND v.current_revision_id IS NOT NULL
                AND v.status='PLATFORM_REVIEWING'
                AND v.platform_decision_id IS NULL
                AND d.phase='INSTITUTION'
                AND d.decision='CHECKED'
              FOR SHARE OF v,d;
              IF NOT FOUND THEN RETURN NULL; END IF;
              SELECT count(*),count(*) FILTER (
                WHERE a.actor_scope=(
                  'user'||chr(58)||value_reviewer::text||chr(58)||'platform'
                )
              ) INTO claim_count,matching_count
              FROM public.member_enrollment_audit a
              WHERE a.object_id=value_verification
                AND a.action='IDENTITY_REVIEW_CLAIMED'
                AND a.result='SUCCESS';
              IF claim_count<>1 OR matching_count<>1 THEN RETURN NULL; END IF;
              SELECT to_jsonb(e) INTO preimage
              FROM public.service_enrollment e
              JOIN public.member_identity_verification v
                ON v.enrollment_id=e.enrollment_id
               AND v.verification_id=value_verification
               AND v.member_id=e.subject_member_id
              WHERE e.enrollment_id=value_enrollment
                AND e.current_identity_verification_id=value_verification
                AND e.status='INSTITUTION_CHECKED'
              FOR UPDATE OF e;
              RETURN preimage;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(
        sa.text(f'GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO "{review}"')
    )


def downgrade() -> None:
    review = _review_role()
    _lock()
    op.execute(
        sa.text(f'REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM "{review}"')
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_SIGNATURE}"))
