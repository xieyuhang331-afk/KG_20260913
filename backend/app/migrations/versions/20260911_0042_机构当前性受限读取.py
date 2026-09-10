"""Add the bounded Slice 3 institution-currentness authority."""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260911_0042"
down_revision = "20260910_0041"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212042
_SIGNATURE = (
    "public.slice3_institution_currentness_authority_v1(BIGINT,BIGINT)"
)


def _configuration_error() -> None:
    raise RuntimeError(
        "SLICE3_INSTITUTION_CURRENTNESS_CONFIGURATION_INVALID"
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
            CREATE FUNCTION public.slice3_institution_currentness_authority_v1(
              actor_user_id BIGINT,
              claimed_tenant_id BIGINT
            )
            RETURNS TABLE (
              actor_current BOOLEAN,
              institution_current BOOLEAN,
              tenant_public_id UUID
            )
            LANGUAGE plpgsql SECURITY DEFINER VOLATILE
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE
              value_role TEXT;
              value_status TEXT;
              value_user_tenant BIGINT;
              value_exited_at TIMESTAMPTZ;
              value_deletion_requested_at TIMESTAMPTZ;
              value_tenant_status TEXT;
              value_public UUID;
            BEGIN
              IF session_user <> '{role}' THEN
                RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              IF actor_user_id IS NULL OR actor_user_id <= 0
                OR claimed_tenant_id IS NULL OR claimed_tenant_id <= 0
              THEN
                RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_INPUT_INVALID'
                  USING ERRCODE='22023';
              END IF;

              BEGIN
                SELECT u.role::TEXT,u.status::TEXT,u.tenant_id,
                       u.exited_at,u.deletion_requested_at
                  INTO STRICT value_role,value_status,value_user_tenant,
                              value_exited_at,value_deletion_requested_at
                FROM public."user" u
                WHERE u.id=actor_user_id
                FOR SHARE OF u;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN
                  RETURN QUERY SELECT false,false,NULL::UUID;
                  RETURN;
              END;

              IF value_role NOT IN ('org_admin','org_operator')
                OR value_status <> 'active'
                OR value_exited_at IS NOT NULL
                OR value_deletion_requested_at IS NOT NULL
                OR value_user_tenant IS DISTINCT FROM claimed_tenant_id
              THEN
                RETURN QUERY SELECT false,false,NULL::UUID;
                RETURN;
              END IF;

              BEGIN
                SELECT t.status::TEXT INTO STRICT value_tenant_status
                FROM public.tenant t
                WHERE t.id=claimed_tenant_id
                FOR SHARE OF t;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN
                  RETURN QUERY SELECT false,false,NULL::UUID;
                  RETURN;
              END;
              IF value_tenant_status <> 'active' THEN
                RETURN QUERY SELECT false,false,NULL::UUID;
                RETURN;
              END IF;

              BEGIN
                SELECT a.tenant_public_id INTO STRICT value_public
                FROM public.institution_application a
                WHERE a.tenant_internal_id=claimed_tenant_id
                  AND a.status='APPROVED'
                  AND a.tenant_public_id IS NOT NULL;
              EXCEPTION
                WHEN NO_DATA_FOUND THEN
                  RETURN QUERY SELECT true,false,NULL::UUID;
                  RETURN;
                WHEN TOO_MANY_ROWS THEN
                  RAISE EXCEPTION 'SLICE3_INSTITUTION_CURRENTNESS_AMBIGUOUS'
                    USING ERRCODE='21000';
              END;

              RETURN QUERY SELECT true,true,value_public;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f'GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO "{role}"'))


def downgrade() -> None:
    role = _application_role()
    _lock()
    op.execute(
        sa.text(f'REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM "{role}"')
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_SIGNATURE}"))
