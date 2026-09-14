"""Restrict execution of Slice 2 integrity trigger functions.

Revision ID: 20260914_0046
Revises: 20260913_0044
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_0046"
down_revision = "20260913_0044"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212046
_FUNCTIONS = (
    "public.enforce_therapist_revision_qualification_v1()",
    "public.enforce_therapist_qualification_attachments_v1()",
    "public.enforce_therapist_review_decision_v1()",
)


def _configuration_error() -> None:
    raise RuntimeError("SLICE2_TRIGGER_EXECUTE_ACL_CONFIGURATION_INVALID") from None


def _lock() -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )


def _validate_functions(*, expect_public_execute: bool) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT p.oid::regprocedure::text AS signature,"
            "p.prosecdef,p.pronargs,p.prorettype='trigger'::regtype AS returns_trigger,"
            "owner_role.rolname=current_user AS owned_by_migration_role,"
            "p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[] "
            "AS fixed_search_path,"
            "EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee=0 AND a.privilege_type='EXECUTE') AS public_execute,"
            "EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
            "WHERE a.grantee NOT IN (0,p.proowner)) AS unexpected_grantee "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "JOIN pg_roles owner_role ON owner_role.oid=p.proowner "
            "WHERE p.oid=ANY(CAST(:functions AS regprocedure[])) "
            "ORDER BY signature"
        ),
        {"functions": list(_FUNCTIONS)},
    ).mappings().all()
    if len(rows) != len(_FUNCTIONS):
        _configuration_error()
    for row in rows:
        if (
            not row["prosecdef"]
            or row["pronargs"] != 0
            or not row["returns_trigger"]
            or not row["owned_by_migration_role"]
            or not row["fixed_search_path"]
            or row["public_execute"] is not expect_public_execute
            or row["unexpected_grantee"]
        ):
            _configuration_error()


def upgrade() -> None:
    _lock()
    _validate_functions(expect_public_execute=True)
    for signature in _FUNCTIONS:
        op.execute(sa.text(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC"))
    _validate_functions(expect_public_execute=False)


def downgrade() -> None:
    _lock()
    _validate_functions(expect_public_execute=False)
    for signature in _FUNCTIONS:
        op.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO PUBLIC"))
    _validate_functions(expect_public_execute=True)
