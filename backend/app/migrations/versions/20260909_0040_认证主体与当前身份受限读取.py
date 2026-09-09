"""Add closed authentication subject and currentness reads."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260909_0040"
down_revision = "20260906_0039"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212040
_LOGIN_SIGNATURE = "public.auth_login_subject_v1(VARCHAR)"
_CURRENTNESS_SIGNATURE = "public.auth_user_currentness_v1(BIGINT)"


def _configuration_error() -> None:
    raise RuntimeError("AUTH_BOUNDED_READ_CONFIGURATION_INVALID") from None


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
            CREATE FUNCTION public.auth_login_subject_v1(p_phone VARCHAR(11))
            RETURNS TABLE (
              id BIGINT,
              phone VARCHAR(11),
              password_hash VARCHAR(255),
              role public.user_role,
              status public.user_status,
              tenant_id BIGINT,
              exited_at TIMESTAMPTZ,
              deletion_requested_at TIMESTAMPTZ,
              tenant_org_id BIGINT
            )
            LANGUAGE plpgsql SECURITY DEFINER STABLE
            SET search_path = pg_catalog AS $$
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'AUTH_LOGIN_SUBJECT_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF p_phone IS NULL OR length(p_phone) <> 11
                OR p_phone !~ '^1[0-9]{{10}}$' THEN
                RAISE EXCEPTION 'AUTH_LOGIN_SUBJECT_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;
              RETURN QUERY
              SELECT u.id,u.phone,u.password_hash,u.role,u.status,u.tenant_id,
                     u.exited_at,u.deletion_requested_at,t.org_id
              FROM public."user" AS u
              LEFT JOIN public.tenant AS t ON t.id=u.tenant_id
              WHERE u.phone=p_phone;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_LOGIN_SIGNATURE} FROM PUBLIC"))
    op.execute(
        sa.text(f'GRANT EXECUTE ON FUNCTION {_LOGIN_SIGNATURE} TO "{role}"')
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.auth_user_currentness_v1(p_user_id BIGINT)
            RETURNS TABLE (
              id BIGINT,
              role public.user_role,
              tenant_id BIGINT,
              status public.user_status,
              exited_at TIMESTAMPTZ,
              deletion_requested_at TIMESTAMPTZ,
              tenant_org_id BIGINT
            )
            LANGUAGE plpgsql SECURITY DEFINER STABLE
            SET search_path = pg_catalog AS $$
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'AUTH_USER_CURRENTNESS_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF p_user_id IS NULL OR p_user_id <= 0 THEN
                RAISE EXCEPTION 'AUTH_USER_CURRENTNESS_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;
              RETURN QUERY
              SELECT u.id,u.role,u.tenant_id,u.status,u.exited_at,
                     u.deletion_requested_at,t.org_id
              FROM public."user" AS u
              LEFT JOIN public.tenant AS t ON t.id=u.tenant_id
              WHERE u.id=p_user_id;
            END $$
            """
        )
    )
    op.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {_CURRENTNESS_SIGNATURE} FROM PUBLIC")
    )
    op.execute(
        sa.text(f'GRANT EXECUTE ON FUNCTION {_CURRENTNESS_SIGNATURE} TO "{role}"')
    )


def downgrade() -> None:
    role = _application_role()
    _lock()
    op.execute(
        sa.text(f'REVOKE EXECUTE ON FUNCTION {_CURRENTNESS_SIGNATURE} FROM "{role}"')
    )
    op.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {_CURRENTNESS_SIGNATURE} FROM PUBLIC")
    )
    op.execute(sa.text(f"DROP FUNCTION {_CURRENTNESS_SIGNATURE}"))
    op.execute(
        sa.text(f'REVOKE EXECUTE ON FUNCTION {_LOGIN_SIGNATURE} FROM "{role}"')
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_LOGIN_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_LOGIN_SIGNATURE}"))
