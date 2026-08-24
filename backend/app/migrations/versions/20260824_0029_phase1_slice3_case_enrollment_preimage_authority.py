"""Add the Slice 3 case enrollment preimage authority.

Revision ID: 20260824_0029
Revises: 20260823_0028
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260824_0029"
down_revision = "20260823_0028"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212029
_SIGNATURE = (
    "public.slice3_case_enrollment_preimage_authority_v1(UUID,UUID,UUID,BIGINT)"
)


def _configuration_error() -> None:
    raise RuntimeError(
        "Slice 3 case enrollment preimage authority configuration is invalid"
    ) from None


def _case_role() -> str:
    role = os.getenv("KG_MEMBER_CASE_WRITER_ROLE", "").strip()
    raw_url = os.getenv("KG_MEMBER_CASE_WRITER_DATABASE_URL", "").strip()
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
    case = _case_role()
    _lock()
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.slice3_case_enrollment_preimage_authority_v1(
              value_assignment UUID,value_enrollment UUID,value_therapist UUID,
              value_actor_user BIGINT
            ) RETURNS JSONB
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE
              preimage JSONB;
            BEGIN
              IF session_user <> '{case}' THEN
                RAISE EXCEPTION 'SLICE3_CASE_ENROLLMENT_PREIMAGE_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF value_assignment IS NULL OR value_enrollment IS NULL
                 OR value_therapist IS NULL OR value_actor_user IS NULL THEN
                RETURN NULL;
              END IF;
              SELECT to_jsonb(e) INTO preimage
              FROM public.service_enrollment e
              JOIN public.primary_therapist_assignment a
                ON a.assignment_id=value_assignment
               AND a.enrollment_id=e.enrollment_id
               AND a.tenant_id=e.tenant_id
               AND a.subject_member_id=e.subject_member_id
              JOIN public.therapist_profile p
                ON p.therapist_id=a.therapist_id
               AND p.tenant_id=a.tenant_id
              JOIN public."user" u
                ON u.id=p.user_id
               AND u.tenant_id=p.tenant_id
              WHERE u.id=value_actor_user
                AND u.role='therapist'
                AND u.status='active'
                AND p.user_id=value_actor_user
                AND p.therapist_id=value_therapist
                AND p.status='APPROVED_ACTIVE'
                AND a.assignment_id=value_assignment
                AND a.enrollment_id=value_enrollment
                AND a.therapist_id=value_therapist
                AND a.status='PENDING_ACCEPTANCE'
                AND a.service_case_id IS NULL
                AND e.enrollment_id=value_enrollment
                AND e.current_assignment_id=value_assignment
                AND e.status='THERAPIST_PENDING'
                AND e.service_case_id IS NULL
              FOR UPDATE OF e;
              IF NOT FOUND THEN RETURN NULL; END IF;
              RETURN preimage;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO "{case}"'))


def downgrade() -> None:
    case = _case_role()
    _lock()
    op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM "{case}"'))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_SIGNATURE}"))
