"""Converge private-file scan attempts, leases, terminal failure and cleanup.

Revision ID: 20260904_0037
Revises: 20260903_0036
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

revision = "20260904_0037"
down_revision = "20260903_0036"
branch_labels = None
depends_on = None

_IDENTITIES = (
    ("KG_PRIVATE_FILE_WRITER_ROLE", "KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
    (
        "KG_INSTITUTION_ONBOARDING_READER_ROLE",
        "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL",
    ),
)
_REFERENCE_FUNCTION = "public.a3_private_file_referenced_v1(uuid)"
_SCAN_COLUMNS = (
    "scan_attempt_count",
    "scan_last_error_code",
    "scan_next_retry_at",
    "scan_lease_token",
    "scan_lease_until",
    "scan_operation_ref_digest",
    "scan_version",
)


def _configuration_error() -> None:
    raise RuntimeError("A3 private file role configuration is invalid") from None


def _roles() -> tuple[str, str]:
    configured: list[str] = []
    targets: set[tuple[str | None, int | None, str | None]] = set()
    for role_env, url_env in _IDENTITIES:
        role = os.getenv(role_env, "").strip()
        raw_url = os.getenv(url_env, "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
            _configuration_error()
        try:
            url = make_url(raw_url)
        except Exception:
            _configuration_error()
        if (
            url.drivername != "postgresql+asyncpg"
            or url.username != role
            or not url.password
            or not url.database
        ):
            _configuration_error()
        configured.append(role)
        targets.add((url.host, url.port, url.database))
    if len(set(configured)) != 2 or len(targets) != 1:
        _configuration_error()

    connection = op.get_bind()
    current_user = str(
        connection.execute(sa.text("SELECT current_user")).scalar_one()
    )
    if current_user in configured:
        _configuration_error()
    rows = connection.execute(
        sa.text(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolinherit,rolreplication,rolbypassrls FROM pg_roles "
            "WHERE rolname=ANY(:roles)"
        ),
        {"roles": configured},
    ).mappings().all()
    unsafe = (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
    )
    if (
        len(rows) != 2
        or any(row["rolcanlogin"] is not True for row in rows)
        or any(row[name] for row in rows for name in unsafe)
    ):
        _configuration_error()
    memberships = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles member_role ON member_role.oid=membership.member "
            "JOIN pg_roles granted_role ON granted_role.oid=membership.roleid "
            "WHERE member_role.rolname=ANY(:roles) "
            "OR granted_role.rolname=ANY(:roles))"
        ),
        {"roles": configured},
    ).scalar_one()
    if memberships:
        _configuration_error()
    return configured[0], configured[1]


def upgrade() -> None:
    writer, reader = _roles()
    op.add_column(
        "private_file",
        sa.Column(
            "scan_attempt_count", sa.SmallInteger(), nullable=False, server_default="0"
        ),
        schema="public",
    )
    op.add_column(
        "private_file", sa.Column("scan_last_error_code", sa.String(48)), schema="public"
    )
    op.add_column(
        "private_file",
        sa.Column("scan_next_retry_at", sa.DateTime(timezone=True)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("scan_lease_token", postgresql.UUID(as_uuid=False)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("scan_lease_until", sa.DateTime(timezone=True)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("scan_operation_ref_digest", sa.CHAR(64)),
        schema="public",
    )
    op.add_column(
        "private_file",
        sa.Column("scan_version", sa.BigInteger(), nullable=False, server_default="1"),
        schema="public",
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE public.private_file SET scan_next_retry_at=clock_timestamp() "
            "WHERE status='PENDING_SCAN'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE public.private_file SET scan_attempt_count=1 WHERE status IN ('CLEAN','REJECTED')"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE public.private_file SET scan_attempt_count=4, scan_last_error_code='LEGACY_SCAN_FAILED' WHERE status='SCAN_FAILED'"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE public.private_file SET scanned_at=COALESCE(scanned_at,created_at) "
            "WHERE status IN ('CLEAN','REJECTED','SCAN_FAILED')"
        )
    )
    op.create_check_constraint(
        "ck_private_file_scan_attempt",
        "private_file",
        "scan_attempt_count BETWEEN 0 AND 4 AND scan_version>=1",
        schema="public",
    )
    op.create_check_constraint(
        "ck_private_file_scan_lease_pair",
        "private_file",
        "(scan_lease_token IS NULL)=(scan_lease_until IS NULL)",
        schema="public",
    )
    op.create_check_constraint(
        "ck_private_file_scan_state",
        "private_file",
        "((status='UPLOAD_INITIATED' AND scan_attempt_count=0 "
        "AND scan_last_error_code IS NULL AND scan_next_retry_at IS NULL "
        "AND scan_lease_token IS NULL AND scan_lease_until IS NULL "
        "AND scan_operation_ref_digest IS NULL) OR "
        "(status='PENDING_SCAN' AND scan_attempt_count<4 AND NOT "
        "(scan_next_retry_at IS NOT NULL AND scan_lease_token IS NOT NULL)) OR "
        "(status IN ('CLEAN','REJECTED') AND scan_last_error_code IS NULL "
        "AND scanned_at IS NOT NULL AND scan_next_retry_at IS NULL "
        "AND scan_lease_token IS NULL AND scan_lease_until IS NULL) OR "
        "(status='SCAN_FAILED' AND scan_last_error_code IS NOT NULL "
        "AND scan_last_error_code IN "
        "('OBJECT_MISSING','EVIDENCE_MISMATCH','SCAN_SERVICE_UNAVAILABLE',"
        "'WORKER_LOST','SCAN_STATE_UNKNOWN','COMMIT_OUTCOME_UNKNOWN',"
        "'LEGACY_SCAN_FAILED') AND scanned_at IS NOT NULL "
        "AND scan_next_retry_at IS NULL AND scan_lease_token IS NULL "
        "AND scan_lease_until IS NULL) OR "
        "(status='DELETED' AND scan_next_retry_at IS NULL "
        "AND scan_lease_token IS NULL AND scan_lease_until IS NULL "
        "AND scan_operation_ref_digest IS NULL))",
        schema="public",
    )
    op.create_index(
        "ix_private_file_scan_recovery",
        "private_file",
        ["status", "scan_next_retry_at", "scan_lease_until", "created_at"],
        schema="public",
    )

    op.execute(
        f"""
CREATE FUNCTION public.a3_private_file_referenced_v1(p_file_id UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
BEGIN
  IF session_user <> '{writer}' THEN
    RAISE EXCEPTION 'A3_PRIVATE_FILE_REFERENCE_FORBIDDEN';
  END IF;
  IF p_file_id IS NULL THEN
    RAISE EXCEPTION 'A3_PRIVATE_FILE_REFERENCE_INVALID';
  END IF;
  RETURN EXISTS(
    SELECT 1 FROM public.private_file file
    WHERE file.file_id=p_file_id AND file.bound_application_id IS NOT NULL
  ) OR EXISTS(
    SELECT 1 FROM public.institution_license license
    WHERE license.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.therapist_qualification_attachment attachment
    WHERE attachment.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.detection_report_attachment attachment
    WHERE attachment.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.personal_data_export_artifact artifact
    WHERE artifact.private_file_id=p_file_id
  ) OR EXISTS(
    SELECT 1 FROM public.personal_data_export_download_access access
    WHERE access.private_file_id=p_file_id
  );
END;
$function$
"""
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_REFERENCE_FUNCTION} FROM PUBLIC")
    op.execute(
        f'GRANT EXECUTE ON FUNCTION {_REFERENCE_FUNCTION} TO "{writer}"'
    )
    rendered = ",".join(f'"{name}"' for name in _SCAN_COLUMNS)
    op.execute(
        f'GRANT SELECT ({rendered}), INSERT ({rendered}), UPDATE ({rendered}) '
        f'ON TABLE public.private_file TO "{writer}"'
    )
    op.execute(
        f'GRANT SELECT ("scan_attempt_count","scan_last_error_code",'
        f'"scan_next_retry_at") '
        f'ON TABLE public.private_file TO "{reader}"'
    )


def downgrade() -> None:
    writer, reader = _roles()
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE public.private_file IN ACCESS EXCLUSIVE MODE")
    )
    blocked = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM public.private_file WHERE "
            "status='PENDING_SCAN' OR scan_next_retry_at IS NOT NULL OR "
            "scan_lease_until>clock_timestamp())"
        )
    ).scalar_one()
    if blocked:
        raise RuntimeError("A3_PRIVATE_FILE_DOWNGRADE_NOT_QUIESCENT") from None
    op.execute(
        f'REVOKE SELECT ("scan_attempt_count","scan_last_error_code",'
        f'"scan_next_retry_at") '
        f'ON TABLE public.private_file FROM "{reader}"'
    )
    rendered = ",".join(f'"{name}"' for name in _SCAN_COLUMNS)
    op.execute(
        f'REVOKE SELECT ({rendered}), INSERT ({rendered}), UPDATE ({rendered}) '
        f'ON TABLE public.private_file FROM "{writer}"'
    )
    op.execute(f'REVOKE EXECUTE ON FUNCTION {_REFERENCE_FUNCTION} FROM "{writer}"')
    op.execute(f"REVOKE ALL ON FUNCTION {_REFERENCE_FUNCTION} FROM PUBLIC")
    op.execute(f"DROP FUNCTION {_REFERENCE_FUNCTION}")
    op.drop_index("ix_private_file_scan_recovery", table_name="private_file", schema="public")
    for constraint in (
        "ck_private_file_scan_state",
        "ck_private_file_scan_lease_pair",
        "ck_private_file_scan_attempt",
    ):
        op.drop_constraint(constraint, "private_file", schema="public", type_="check")
    for column in reversed(_SCAN_COLUMNS):
        op.drop_column("private_file", column, schema="public")
