"""Add bounded reads for Slice 3 member identity revisions.

Revision ID: 20260822_0025
Revises: 20260821_0024
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine import make_url


revision = "20260822_0025"
down_revision = "20260821_0024"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212022
_SUMMARY_SIGNATURE = "public.slice3_identity_revision_summary_v1(UUID,UUID)"
_CORRECTION_SIGNATURE = "public.slice3_identity_revision_correction_v1(UUID,UUID)"


def _configuration_error() -> None:
    raise RuntimeError(
        "Slice 3 identity revision authority configuration is invalid"
    ) from None


def _runtime_role(role_var: str, url_var: str) -> str:
    role = os.getenv(role_var, "").strip()
    raw_url = os.getenv(url_var, "").strip()
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


def _roles() -> tuple[str, str]:
    enrollment = _runtime_role(
        "KG_MEMBER_ENROLLMENT_WRITER_ROLE",
        "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL",
    )
    review = _runtime_role(
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE",
        "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL",
    )
    if enrollment == review:
        _configuration_error()
    return enrollment, review


def _lock() -> None:
    op.get_bind().execute(
        sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY}
    )


def upgrade() -> None:
    enrollment, review = _roles()
    _lock()
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.slice3_identity_revision_summary_v1(
              value_verification UUID,value_revision UUID
            ) RETURNS TABLE(
              revision_id UUID,verification_id UUID,revision_no INTEGER,
              document_type VARCHAR,id_masked VARCHAR,identity_fingerprint CHAR(64),
              fingerprint_key_id VARCHAR,input_digest CHAR(64),created_at TIMESTAMPTZ
            )
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,pg_temp AS $$
            BEGIN
              IF session_user NOT IN ('{enrollment}','{review}') THEN
                RAISE EXCEPTION 'SLICE3_IDENTITY_REVISION_SUMMARY_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              RETURN QUERY
              SELECT r.revision_id,r.verification_id,r.revision_no,r.document_type,
                     r.id_masked,r.identity_fingerprint,r.fingerprint_key_id,
                     r.input_digest,r.created_at
              FROM public.member_identity_verification v
              JOIN public.member_identity_revision r
                ON r.verification_id=v.verification_id
               AND r.revision_id=v.current_revision_id
              WHERE v.verification_id=value_verification
                AND r.revision_id=value_revision
              FOR SHARE OF v,r;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SUMMARY_SIGNATURE} FROM PUBLIC"))
    op.execute(
        sa.text(
            f'GRANT EXECUTE ON FUNCTION {_SUMMARY_SIGNATURE} '
            f'TO "{enrollment}","{review}"'
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE FUNCTION public.slice3_identity_revision_correction_v1(
              value_verification UUID,value_revision UUID
            ) RETURNS TABLE(
              revision_id UUID,verification_id UUID,revision_no INTEGER,
              document_type VARCHAR,real_name_ciphertext BYTEA,real_name_key_id VARCHAR,
              id_ciphertext BYTEA,id_key_id VARCHAR,birth_date_ciphertext BYTEA,
              birth_date_key_id VARCHAR
            )
            LANGUAGE plpgsql SECURITY DEFINER
            SET search_path=pg_catalog,pg_temp AS $$
            DECLARE current_revision UUID; current_status VARCHAR;
            BEGIN
              IF session_user <> '{enrollment}' THEN
                RAISE EXCEPTION 'SLICE3_IDENTITY_REVISION_CORRECTION_FORBIDDEN'
                  USING ERRCODE='42501';
              END IF;
              SELECT v.current_revision_id,v.status
                INTO current_revision,current_status
              FROM public.member_identity_verification v
              WHERE v.verification_id=value_verification
              FOR UPDATE;
              IF current_status IS DISTINCT FROM 'NEEDS_CORRECTION'
                 OR current_revision IS DISTINCT FROM value_revision THEN
                RETURN;
              END IF;
              RETURN QUERY
              SELECT r.revision_id,r.verification_id,r.revision_no,r.document_type,
                     r.real_name_ciphertext,r.real_name_key_id,r.id_ciphertext,
                     r.id_key_id,r.birth_date_ciphertext,r.birth_date_key_id
              FROM public.member_identity_revision r
              WHERE r.verification_id=value_verification
                AND r.revision_id=value_revision
              FOR SHARE OF r;
            END $$
            """
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_CORRECTION_SIGNATURE} FROM PUBLIC"))
    op.execute(
        sa.text(
            f'GRANT EXECUTE ON FUNCTION {_CORRECTION_SIGNATURE} TO "{enrollment}"'
        )
    )
    op.execute(
        sa.text(
            'GRANT UPDATE (platform_decision_id,platform_decided_at) '
            'ON TABLE public.member_identity_verification '
            f'TO "{enrollment}"'
        )
    )


def downgrade() -> None:
    enrollment, review = _roles()
    _lock()
    op.execute(
        sa.text(
            'REVOKE UPDATE (platform_decision_id,platform_decided_at) '
            'ON TABLE public.member_identity_verification '
            f'FROM "{enrollment}"'
        )
    )
    op.execute(
        sa.text(
            f'REVOKE EXECUTE ON FUNCTION {_CORRECTION_SIGNATURE} FROM "{enrollment}"'
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_CORRECTION_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_CORRECTION_SIGNATURE}"))
    op.execute(
        sa.text(
            f'REVOKE EXECUTE ON FUNCTION {_SUMMARY_SIGNATURE} '
            f'FROM "{enrollment}","{review}"'
        )
    )
    op.execute(sa.text(f"REVOKE ALL ON FUNCTION {_SUMMARY_SIGNATURE} FROM PUBLIC"))
    op.execute(sa.text(f"DROP FUNCTION {_SUMMARY_SIGNATURE}"))
