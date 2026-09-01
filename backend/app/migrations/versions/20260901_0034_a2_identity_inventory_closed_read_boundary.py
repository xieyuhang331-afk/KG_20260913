"""Add the A2.2 anonymous identity inventory closed read boundary.

Revision ID: 20260901_0034
Revises: 20260830_0033
"""
from __future__ import annotations

import os
import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

revision = "20260901_0034"
down_revision = "20260830_0033"
branch_labels = None
depends_on = None

_ROLE_ENV = "KG_A2_IDENTITY_INVENTORY_ROLE"
_DATABASE_URL_ENV = "KG_A2_IDENTITY_INVENTORY_DATABASE_URL"
_FUNCTION = "identity.a2_identity_inventory_snapshot_v1()"
_BASE_TABLES = (
    'public."user"',
    "public.identity_verification_submission",
    "public.identity_verification_decision",
    "identity.identity_subject_claim_registry",
    "identity.user_member_self_link",
    "public.service_enrollment",
)


def _configuration_error() -> None:
    raise RuntimeError(
        "A2 identity inventory database role configuration is invalid"
    ) from None


def _inventory_role() -> str:
    role = os.getenv(_ROLE_ENV, "").strip()
    raw_url = os.getenv(_DATABASE_URL_ENV, "").strip()
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

    connection = op.get_bind()
    current_user = str(
        connection.execute(sa.text("SELECT current_user")).scalar_one()
    )
    if current_user == role:
        _configuration_error()
    row = connection.execute(
        sa.text(
            "SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    ).mappings().one_or_none()
    if row is None or row["rolcanlogin"] is not True:
        _configuration_error()
    unsafe = (
        "rolsuper",
        "rolcreatedb",
        "rolcreaterole",
        "rolinherit",
        "rolreplication",
        "rolbypassrls",
    )
    if any(row[name] for name in unsafe):
        _configuration_error()
    has_membership = connection.execute(
        sa.text(
            "SELECT EXISTS(SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles member_role ON member_role.oid=membership.member "
            "JOIN pg_roles granted_role ON granted_role.oid=membership.roleid "
            "WHERE member_role.rolname=:role OR granted_role.rolname=:role)"
        ),
        {"role": role},
    ).scalar_one()
    if has_membership:
        _configuration_error()
    return role


def _create_snapshot_function(role: str) -> None:
    op.execute(
        f"""
        CREATE FUNCTION identity.a2_identity_inventory_snapshot_v1()
        RETURNS TABLE(
          role TEXT,
          legacy_pii_present BOOLEAN,
          identity_authority_signal BOOLEAN,
          formal_chain_complete BOOLEAN,
          tenant_present BOOLEAN,
          tenant_relation_known BOOLEAN,
          self_link_count INTEGER,
          enrollment_count INTEGER,
          current_enrollment_count INTEGER,
          tenant_matches_unique_current BOOLEAN,
          enrollment_scope_complete BOOLEAN
        )
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        PARALLEL RESTRICTED
        SET search_path = pg_catalog, pg_temp
        AS $fn$
        BEGIN
          IF session_user <> '{role}' THEN
            RAISE EXCEPTION 'A2_IDENTITY_INVENTORY_READER_FORBIDDEN';
          END IF;
          RETURN QUERY
          SELECT
            u.role::TEXT,
            (u.real_name IS NOT NULL OR u.id_card IS NOT NULL),
            CASE
              WHEN u.verify_status IS NULL THEN NULL
              ELSE (
                u.verify_status = 'verified'
                OR EXISTS(
                  SELECT 1
                  FROM public.identity_verification_submission submission
                  WHERE submission.user_ref = u.id
                )
                OR EXISTS(
                  SELECT 1
                  FROM public.identity_verification_decision decision
                  WHERE decision.user_ref = u.id
                )
                OR EXISTS(
                  SELECT 1
                  FROM identity.identity_subject_claim_registry claim
                  WHERE claim.user_ref = u.id AND claim.source_kind = 'P1'
                )
              )
            END,
            CASE
              WHEN u.verify_status IS NULL THEN NULL
              ELSE EXISTS(
                SELECT 1
                FROM public.identity_verification_submission submission
                JOIN identity.identity_subject_claim_registry claim
                  ON claim.user_ref = submission.user_ref
                 AND claim.source_kind = 'P1'
                 AND claim.p1_submission_id = submission.submission_id
                JOIN public.identity_verification_decision decision
                  ON decision.user_ref = submission.user_ref
                 AND decision.decision_ref = claim.p1_decision_ref
                 AND decision.outcome = 'verified'
                WHERE submission.user_ref = u.id
                  AND submission.status = 'verified'
              )
            END,
            (u.tenant_id IS NOT NULL),
            CASE
              WHEN u.role::TEXT <> 'member' THEN TRUE
              WHEN u.tenant_id IS NULL THEN TRUE
              WHEN links.self_link_count IS NULL THEN FALSE
              ELSE TRUE
            END,
            links.self_link_count,
            enrollments.enrollment_count,
            enrollments.current_enrollment_count,
            enrollments.tenant_matches_unique_current,
            enrollments.enrollment_scope_complete
          FROM public."user" u
          LEFT JOIN LATERAL (
            SELECT
              count(*)::INTEGER AS self_link_count,
              min(link.member_id::TEXT) AS member_key
            FROM identity.user_member_self_link link
            WHERE link.user_ref = u.id
          ) links ON TRUE
          LEFT JOIN LATERAL (
            SELECT
              count(*)::INTEGER AS enrollment_count,
              count(*) FILTER (
                WHERE enrollment.status IN (
                  'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                  'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                  'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING',
                  'CASE_CREATED'
                )
              )::INTEGER AS current_enrollment_count,
              coalesce(
                bool_and(enrollment.tenant_id = u.tenant_id) FILTER (
                  WHERE enrollment.status IN (
                    'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                    'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                    'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING',
                    'CASE_CREATED'
                  )
                ),
                FALSE
              ) AS tenant_matches_unique_current,
              coalesce(
                bool_and(
                  (
                    enrollment.status = 'ACCEPTED'
                    AND enrollment.current_identity_verification_id IS NULL
                    AND enrollment.current_assignment_id IS NULL
                    AND enrollment.service_case_id IS NULL
                  )
                  OR (
                    enrollment.status IN (
                      'IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                      'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                      'IDENTITY_VERIFIED','CONSENT_PENDING'
                    )
                    AND enrollment.current_identity_verification_id IS NOT NULL
                    AND enrollment.current_assignment_id IS NULL
                    AND enrollment.service_case_id IS NULL
                  )
                  OR (
                    enrollment.status = 'THERAPIST_PENDING'
                    AND enrollment.current_identity_verification_id IS NOT NULL
                    AND enrollment.current_assignment_id IS NOT NULL
                    AND enrollment.service_case_id IS NULL
                  )
                  OR (
                    enrollment.status = 'CASE_CREATED'
                    AND enrollment.current_identity_verification_id IS NOT NULL
                    AND enrollment.current_assignment_id IS NOT NULL
                    AND enrollment.service_case_id IS NOT NULL
                  )
                ) FILTER (
                  WHERE enrollment.status IN (
                    'ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED',
                    'PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED',
                    'IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING',
                    'CASE_CREATED'
                  )
                ),
                FALSE
              ) AS enrollment_scope_complete
            FROM public.service_enrollment enrollment
            WHERE enrollment.subject_member_id::TEXT = links.member_key
          ) enrollments ON links.member_key IS NOT NULL;
        END
        $fn$
        """
    )


def upgrade() -> None:
    role = _inventory_role()
    quoted_role = f'"{role}"'
    _create_snapshot_function(role)
    op.execute(f"REVOKE ALL ON FUNCTION {_FUNCTION} FROM PUBLIC, {quoted_role}")
    for relation in _BASE_TABLES:
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {quoted_role}"
        )
    op.execute(f"REVOKE CREATE ON SCHEMA public, identity FROM {quoted_role}")
    op.execute(f"GRANT USAGE ON SCHEMA identity TO {quoted_role}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_FUNCTION} TO {quoted_role}")


def downgrade() -> None:
    role = _inventory_role()
    quoted_role = f'"{role}"'
    op.execute(f"REVOKE EXECUTE ON FUNCTION {_FUNCTION} FROM {quoted_role}")
    op.execute(f"REVOKE USAGE ON SCHEMA identity FROM {quoted_role}")
    op.execute(f"DROP FUNCTION {_FUNCTION}")
