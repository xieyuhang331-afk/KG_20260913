"""Add the Slice 3 reviewer claim authority.

Revision ID: 20260822_0026
Revises: 20260822_0025
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260822_0026"
down_revision = "20260822_0025"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212026
_SIGNATURE = "public.slice3_reviewer_claim_authority_v1(UUID,BIGINT)"


def _configuration_error() -> None:
    raise RuntimeError(
        "Slice 3 reviewer claim authority configuration is invalid"
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
            CREATE FUNCTION public.slice3_reviewer_claim_authority_v1(
              value_verification UUID,value_reviewer BIGINT
            ) RETURNS BOOLEAN
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE claim_count BIGINT; matching_count BIGINT;
            BEGIN
              IF session_user <> '{review}' THEN
                RAISE EXCEPTION 'SLICE3_REVIEWER_CLAIM_AUTHORITY_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              PERFORM 1 FROM public.user u
              WHERE u.id=value_reviewer AND u.role='super_admin'
                AND u.status='active' AND u.tenant_id IS NULL
              FOR SHARE;
              IF NOT FOUND THEN RETURN FALSE; END IF;
              PERFORM 1 FROM public.member_identity_verification v
              WHERE v.verification_id=value_verification
                AND v.status='PLATFORM_REVIEWING'
              FOR SHARE;
              IF NOT FOUND THEN RETURN FALSE; END IF;
              PERFORM 1 FROM public.member_enrollment_audit a
              WHERE a.object_id=value_verification
                AND a.action='IDENTITY_REVIEW_CLAIMED'
                AND a.result='SUCCESS'
              FOR SHARE;
              SELECT count(*),count(*) FILTER (
                WHERE a.actor_scope=(
                  'user'||chr(58)||value_reviewer::text||chr(58)||'platform'
                )
              ) INTO claim_count,matching_count
              FROM public.member_enrollment_audit a
              WHERE a.object_id=value_verification
                AND a.action='IDENTITY_REVIEW_CLAIMED'
                AND a.result='SUCCESS';
              RETURN claim_count=1 AND matching_count=1;
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
