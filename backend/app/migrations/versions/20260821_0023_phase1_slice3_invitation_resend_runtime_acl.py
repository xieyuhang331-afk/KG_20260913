"""Grant the Slice 3 invitation-resend column privileges.

Revision ID: 20260821_0023
Revises: 20260818_0022
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260821_0023"
down_revision = "20260818_0022"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212022
_ROLE_VAR = "KG_MEMBER_ENROLLMENT_WRITER_ROLE"
_URL_VAR = "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL"
_COLUMNS = (
    "code_digest",
    "code_key_id",
    "expires_at",
    "issued_at",
)


def _configuration_error() -> None:
    raise RuntimeError(
        "Slice 3 invitation resend ACL role configuration is invalid"
    ) from None


def _writer_role() -> str:
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


def _change_privilege(verb: str, preposition: str) -> None:
    role = _writer_role()
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    rendered = ",".join(f'"{column}"' for column in _COLUMNS)
    if verb == "GRANT" and preposition == "TO":
        statement = (
            f'GRANT UPDATE ({rendered}) ON TABLE public.member_service_invitation '
            f'TO "{role}"'
        )
    elif verb == "REVOKE" and preposition == "FROM":
        statement = (
            f'REVOKE UPDATE ({rendered}) ON TABLE public.member_service_invitation '
            f'FROM "{role}"'
        )
    else:
        raise AssertionError("unsupported privilege change")
    op.execute(sa.text(statement))


def upgrade() -> None:
    _change_privilege("GRANT", "TO")


def downgrade() -> None:
    _change_privilege("REVOKE", "FROM")
