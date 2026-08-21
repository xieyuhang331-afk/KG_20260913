"""Add the Slice 3 member currentness authority.

Revision ID: 20260821_0024
Revises: 20260821_0023
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260821_0024"
down_revision = "20260821_0023"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212022
_ROLE_VAR = "KG_DATABASE_USER"
_URL_VAR = "KG_IDENTITY_APPLICATION_DATABASE_URL"
_SIGNATURE = "public.slice3_member_currentness_authority_v1(BIGINT,VARCHAR)"


def _configuration_error() -> None:
    raise RuntimeError(
        "Slice 3 member currentness authority configuration is invalid"
    ) from None


def _application_role() -> str:
    role = os.getenv(_ROLE_VAR, "").strip()
    raw_url = os.getenv(_URL_VAR, "").strip()
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
            CREATE FUNCTION public.slice3_member_currentness_authority_v1(
              value_user BIGINT,value_expected_phone VARCHAR
            ) RETURNS UUID
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE value_member UUID;
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'SLICE3_MEMBER_CURRENTNESS_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              SELECT l.member_id INTO value_member
              FROM public.\"user\" u
              JOIN identity.user_member_self_link l ON l.user_ref=u.id
              JOIN identity.member m ON m.member_id=l.member_id
              WHERE u.id=value_user
                AND u.role='member'
                AND u.status='active'
                AND u.tenant_id IS NULL
                AND m.status='created'
                AND (value_expected_phone IS NULL OR u.phone=value_expected_phone)
              FOR SHARE OF u,l,m;
              RETURN value_member;
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
