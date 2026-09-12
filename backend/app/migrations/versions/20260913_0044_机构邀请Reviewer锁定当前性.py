"""Add the locked institution-onboarding reviewer currentness read."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260913_0044"
down_revision = "20260912_0043"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212044
_SIGNATURE = "public.institution_onboarding_reviewer_currentness_v1(BIGINT)"


def _configuration_error() -> None:
    raise RuntimeError(
        "INSTITUTION_REVIEWER_CURRENTNESS_CONFIGURATION_INVALID"
    ) from None


def _application_role() -> str:
    role = os.getenv("KG_DATABASE_USER", "").strip()
    raw_url = os.getenv("KG_IDENTITY_APPLICATION_DATABASE_URL", "").strip()
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
    _lock()
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.institution_onboarding_reviewer_currentness_v1(p_user_id BIGINT)
            RETURNS TABLE (
              id BIGINT,
              role public.user_role,
              status public.user_status,
              tenant_id BIGINT
            )
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path=pg_catalog,pg_temp AS $$
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'INSTITUTION_REVIEWER_CURRENTNESS_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF p_user_id IS NULL OR p_user_id <= 0 THEN
                RAISE EXCEPTION 'INSTITUTION_REVIEWER_CURRENTNESS_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;

              RETURN QUERY
              SELECT reviewer_user.id,
                     reviewer_user.role,
                     reviewer_user.status,
                     reviewer_user.tenant_id
              FROM public."user" reviewer_user
              WHERE reviewer_user.id=p_user_id
                AND reviewer_user.role='super_admin'
                AND reviewer_user.status='active'
                AND reviewer_user.tenant_id IS NULL
                AND reviewer_user.exited_at IS NULL
                AND reviewer_user.deletion_requested_at IS NULL
              FOR SHARE OF reviewer_user;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO "{role}"'))


def downgrade() -> None:
    role = _application_role()
    _lock()
    op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM "{role}"'))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_SIGNATURE}"))
