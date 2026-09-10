"""Add the bounded technical-member registration write."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260910_0041"
down_revision = "20260909_0040"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212041
_REGISTER_SIGNATURE = "public.auth_register_member_v1(VARCHAR, VARCHAR)"


def _configuration_error() -> None:
    raise RuntimeError("AUTH_REGISTRATION_BOUNDARY_CONFIGURATION_INVALID") from None


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
            CREATE FUNCTION public.auth_register_member_v1(
              p_phone VARCHAR(11),
              p_password_hash VARCHAR(255)
            )
            RETURNS TABLE (
              id BIGINT,
              phone VARCHAR(11),
              role public.user_role,
              status public.user_status,
              verify_status VARCHAR(20),
              tenant_id BIGINT,
              created_at TIMESTAMPTZ
            )
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path = pg_catalog AS $$
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'AUTH_REGISTER_MEMBER_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF p_phone IS NULL OR length(p_phone) <> 11
                OR p_phone !~ '^1[0-9]{{10}}$'
                OR p_password_hash IS NULL OR length(p_password_hash) > 255
                OR p_password_hash
                  !~ '^pbkdf2_sha256[$]200000[$][0-9a-f]{{32}}[$][0-9a-f]{{64}}$'
              THEN
                RAISE EXCEPTION 'AUTH_REGISTER_MEMBER_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;

              RETURN QUERY
              INSERT INTO public."user" AS registered (
                phone,password_hash,role,status,verify_status,tenant_id
              ) VALUES (
                p_phone,p_password_hash,'member'::public.user_role,
                'active'::public.user_status,NULL,NULL
              )
              RETURNING registered.id,registered.phone,registered.role,
                        registered.status,registered.verify_status,
                        registered.tenant_id,registered.created_at;
            END $$
            """
        )
    )
    op.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {_REGISTER_SIGNATURE} FROM PUBLIC")
    )
    op.execute(
        sa.text(f'GRANT EXECUTE ON FUNCTION {_REGISTER_SIGNATURE} TO "{role}"')
    )


def downgrade() -> None:
    role = _application_role()
    _lock()
    op.execute(
        sa.text(f'REVOKE EXECUTE ON FUNCTION {_REGISTER_SIGNATURE} FROM "{role}"')
    )
    op.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {_REGISTER_SIGNATURE} FROM PUBLIC")
    )
    op.execute(sa.text(f"DROP FUNCTION {_REGISTER_SIGNATURE}"))
