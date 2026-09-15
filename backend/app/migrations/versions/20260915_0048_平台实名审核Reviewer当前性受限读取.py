"""Add closed platform reviewer currentness and credential reads."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260915_0048"
down_revision = "20260914_0047"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212048
_WRITE_SIGNATURE = "public.slice3_platform_reviewer_write_currentness_v1(BIGINT)"
_CREDENTIAL_SIGNATURE = "public.auth_user_credential_material_v1(BIGINT)"


def _configuration_error() -> None:
    raise RuntimeError("PLATFORM_REVIEWER_BOUNDED_READ_CONFIGURATION_INVALID") from None


def _validated_role(role_name: str, url_name: str) -> str:
    role = os.getenv(role_name, "").strip()
    raw_url = os.getenv(url_name, "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
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
            "SELECT rolcanlogin,rolsuper,rolcreaterole,rolcreatedb,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    membership = bool(
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
    if (
        current_user == role
        or row is None
        or not row["rolcanlogin"]
        or any(row[key] for key in row if key != "rolcanlogin")
        or membership
    ):
        _configuration_error()
    return role


def _roles() -> tuple[str, str]:
    application = _validated_role(
        "KG_DATABASE_USER", "KG_IDENTITY_APPLICATION_DATABASE_URL"
    )
    writer = _validated_role(
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL",
    )
    if application == writer:
        _configuration_error()
    return application, writer


def _lock() -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )


def upgrade() -> None:
    application, writer = _roles()
    _lock()
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.slice3_platform_reviewer_write_currentness_v1(
              p_reviewer_user_id BIGINT
            ) RETURNS TABLE (
              id BIGINT,
              role public.user_role,
              status public.user_status,
              tenant_id BIGINT,
              exited_at TIMESTAMPTZ,
              deletion_requested_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ
            )
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path = pg_catalog AS $$
            BEGIN
              IF session_user <> '{writer}' THEN
                RAISE EXCEPTION 'PLATFORM_REVIEWER_WRITE_CURRENTNESS_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF p_reviewer_user_id IS NULL OR p_reviewer_user_id <= 0 THEN
                RAISE EXCEPTION 'PLATFORM_REVIEWER_WRITE_CURRENTNESS_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;
              RETURN QUERY
              SELECT u.id,u.role,u.status,u.tenant_id,u.exited_at,
                     u.deletion_requested_at,u.updated_at
              FROM public."user" AS u
              WHERE u.id=p_reviewer_user_id
                AND u.role='super_admin'::public.user_role
                AND u.status='active'::public.user_status
                AND u.tenant_id IS NULL
                AND u.exited_at IS NULL
                AND u.deletion_requested_at IS NULL
              FOR SHARE OF u;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_WRITE_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {_WRITE_SIGNATURE} TO "{writer}"'))
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.auth_user_credential_material_v1(
              p_reviewer_user_id BIGINT
            ) RETURNS TABLE (
              id BIGINT,
              password_hash VARCHAR(255),
              role public.user_role,
              status public.user_status,
              tenant_id BIGINT,
              exited_at TIMESTAMPTZ,
              deletion_requested_at TIMESTAMPTZ,
              updated_at TIMESTAMPTZ
            )
            LANGUAGE plpgsql SECURITY DEFINER STABLE
            SET search_path = pg_catalog AS $$
            BEGIN
              IF session_user <> '{application}' THEN
                RAISE EXCEPTION 'AUTH_USER_CREDENTIAL_MATERIAL_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF p_reviewer_user_id IS NULL OR p_reviewer_user_id <= 0 THEN
                RAISE EXCEPTION 'AUTH_USER_CREDENTIAL_MATERIAL_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;
              RETURN QUERY
              SELECT u.id,u.password_hash,u.role,u.status,u.tenant_id,u.exited_at,
                     u.deletion_requested_at,u.updated_at
              FROM public."user" AS u
              WHERE u.id=p_reviewer_user_id
                AND u.role='super_admin'::public.user_role
                AND u.status='active'::public.user_status
                AND u.tenant_id IS NULL
                AND u.exited_at IS NULL
                AND u.deletion_requested_at IS NULL;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_CREDENTIAL_SIGNATURE} FROM PUBLIC"))
    op.execute(
        sa.text(f'GRANT EXECUTE ON FUNCTION {_CREDENTIAL_SIGNATURE} TO "{application}"')
    )


def downgrade() -> None:
    application, writer = _roles()
    _lock()
    op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {_CREDENTIAL_SIGNATURE} FROM "{application}"'))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_CREDENTIAL_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_CREDENTIAL_SIGNATURE}"))
    op.execute(sa.text(f'REVOKE EXECUTE ON FUNCTION {_WRITE_SIGNATURE} FROM "{writer}"'))
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_WRITE_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_WRITE_SIGNATURE}"))
