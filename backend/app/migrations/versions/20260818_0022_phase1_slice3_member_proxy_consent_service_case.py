"""Phase 1 Slice 3 member/proxy consent and PREPARING service case.

Revision ID: 20260818_0022
Revises: 20260817_0021
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260818_0022"
down_revision = "20260817_0021"
branch_labels = None
depends_on = None

_LOCK_KEY = 6052316115572212022
_NEW_IDENTITIES = (
    ("KG_MEMBER_ENROLLMENT_WRITER_ROLE", "KG_MEMBER_ENROLLMENT_WRITER_DATABASE_URL"),
    ("KG_MEMBER_IDENTITY_REVIEW_WRITER_ROLE", "KG_MEMBER_IDENTITY_REVIEW_WRITER_DATABASE_URL"),
    ("KG_MEMBER_CASE_WRITER_ROLE", "KG_MEMBER_CASE_WRITER_DATABASE_URL"),
    ("KG_MEMBER_WORKFLOW_WORKER_ROLE", "KG_MEMBER_WORKFLOW_WORKER_DATABASE_URL"),
    ("KG_MEMBER_ENROLLMENT_READER_ROLE", "KG_MEMBER_ENROLLMENT_READER_DATABASE_URL"),
)
_EXISTING_IDENTITIES = (
    ("KG_DATABASE_USER", "KG_IDENTITY_APPLICATION_DATABASE_URL"),
    ("KG_READONLY_ROLE", "KG_READONLY_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_WRITER_ROLE", "KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_REVIEW_WRITER_ROLE", "KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL"),
    ("KG_PRIVATE_FILE_WRITER_ROLE", "KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_READER_ROLE", "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"),
    ("KG_THERAPIST_ONBOARDING_WRITER_ROLE", "KG_THERAPIST_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_THERAPIST_REVIEW_WRITER_ROLE", "KG_THERAPIST_REVIEW_WRITER_DATABASE_URL"),
    ("KG_THERAPIST_READINESS_WORKER_ROLE", "KG_THERAPIST_READINESS_WORKER_DATABASE_URL"),
    ("KG_THERAPIST_READER_ROLE", "KG_THERAPIST_READER_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_BUILDER_ROLE", "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_PROJECTION_CONFIRMATION_ROLE", "KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_READY_GATE_DATABASE_URL"),
    ("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_READER_ROLE", "KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
)
_TABLES = (
    "member_service_invitation", "service_enrollment", "controlled_member_bootstrap",
    "member_identity_verification", "member_identity_revision",
    "member_identity_review_decision", "member_identity_pii_access", "proxy_grant",
    "consent_document_version", "consent_document_rendition", "consent_record",
    "primary_therapist_assignment", "service_case", "member_enrollment_idempotency",
    "member_enrollment_audit", "member_enrollment_outbox", "member_enrollment_delivery",
)
_VIEWS = (
    "slice3_institution_enrollment_read_v1", "slice3_family_enrollment_read_v1",
    "slice3_platform_identity_review_read_v1", "slice3_therapist_assignment_read_v1",
    "slice3_service_case_read_v1",
)
_EVENT_TYPES = (
    "MEMBER_INVITATION_CREATED", "MEMBER_INVITATION_RESENT",
    "MEMBER_INVITATION_REVOKED", "MEMBER_INVITATION_EXPIRED",
    "MEMBER_ENROLLMENT_ACCEPTED", "MEMBER_IDENTITY_SUBMITTED",
    "MEMBER_IDENTITY_RESUBMITTED", "MEMBER_IDENTITY_INSTITUTION_CHECKED",
    "MEMBER_IDENTITY_CORRECTION_REQUESTED", "MEMBER_IDENTITY_REJECTED",
    "MEMBER_IDENTITY_VERIFIED", "PROXY_GRANT_ACTIVATED",
    "PROXY_GRANT_REVOKED", "PROXY_GRANT_EXPIRED",
    "CONSENT_DOCUMENT_PUBLISHED", "CONSENT_DOCUMENT_RETIRED",
    "CONSENT_ACCEPTED", "CONSENT_DECLINED", "CONSENT_WITHDRAWN",
    "CONSENT_SUPERSEDED", "PRIMARY_ASSIGNMENT_CREATED",
    "PRIMARY_ASSIGNMENT_DECLINED", "PRIMARY_ASSIGNMENT_CANCELLED",
    "SERVICE_CASE_PREPARING_CREATED",
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 3 database role configuration is invalid") from None


def _single_key(current_var: str, keyring_var: str) -> tuple[str, bytes]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _configuration_error()
            result[key] = value
        return result

    try:
        current = os.environ[current_var]
        source = json.loads(os.environ[keyring_var], object_pairs_hook=unique_pairs)
        if type(source) is not dict or set(source) != {current}:
            _configuration_error()
        material = base64.b64decode(source[current], validate=True)
        if len(material) != 32:
            _configuration_error()
        return current, material
    except Exception:
        _configuration_error()


def _digest_configuration() -> tuple[str, ...]:
    try:
        fingerprint_key_id = os.environ["KG_IDENTITY_PII_KEY_ID"]
        fingerprint_material = base64.b64decode(
            os.environ["KG_IDENTITY_PII_HMAC_KEY_B64"], validate=True
        )
        if not fingerprint_key_id or len(fingerprint_material) < 32:
            _configuration_error()
    except Exception:
        _configuration_error()
    configured = [
        _single_key(
            "KG_MEMBER_ENROLLMENT_REQUEST_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_REQUEST_DIGEST_KEYRING_JSON",
        ),
        _single_key(
            "KG_MEMBER_ENROLLMENT_AUDIT_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_AUDIT_DIGEST_KEYRING_JSON",
        ),
        _single_key(
            "KG_MEMBER_ENROLLMENT_OUTBOX_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_OUTBOX_DIGEST_KEYRING_JSON",
        ),
        _single_key(
            "KG_MEMBER_ENROLLMENT_CONSENT_DIGEST_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_CONSENT_DIGEST_KEYRING_JSON",
        ),
        _single_key(
            "KG_MEMBER_ENROLLMENT_DELIVERY_CURRENT_KEY_ID",
            "KG_MEMBER_ENROLLMENT_DELIVERY_KEYRING_JSON",
        ),
    ]
    materials = [fingerprint_material, *(material for _, material in configured)]
    if len(set(materials)) != len(materials):
        _configuration_error()
    checks = [
        hashlib.sha256(b"SLICE3_KEY_CHECK_V1\0" + material).hexdigest()
        for _, material in configured
    ]
    return (
        fingerprint_key_id,
        configured[0][0], checks[0],
        configured[1][0], checks[1],
        configured[2][0], checks[2],
        configured[3][0], checks[3],
        configured[4][0], checks[4],
        "SLICE3_IDENTITY_FINGERPRINT_P1_V1",
    )


def _membership_is_unsafe(connection, roles: tuple[str, ...]) -> bool:
    rows = dict(connection.execute(
        sa.text("SELECT rolname,oid FROM pg_roles WHERE rolname=ANY(:roles)"),
        {"roles": list(roles)},
    ).all())
    if set(rows) != set(roles):
        _configuration_error()
    return bool(connection.execute(sa.text(
        "WITH RECURSIVE paths(source_oid,target_oid,path) AS ("
        "SELECT member,roleid,ARRAY[member,roleid] FROM pg_auth_members UNION ALL "
        "SELECT paths.source_oid,m.roleid,paths.path||m.roleid FROM paths "
        "JOIN pg_auth_members m ON m.member=paths.target_oid "
        "WHERE NOT m.roleid=ANY(paths.path)) "
        "SELECT EXISTS(SELECT 1 FROM paths WHERE source_oid=ANY(:oids) OR target_oid=ANY(:oids))"
    ), {"oids": list(rows.values())}).scalar_one())


def _roles() -> tuple[str, str, str, str, str]:
    configured: dict[str, str] = {}
    targets = set()
    for role_var, url_var in (*_EXISTING_IDENTITIES, *_NEW_IDENTITIES):
        role = os.getenv(role_var, "").strip()
        raw = os.getenv(url_var, "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw:
            _configuration_error()
        try:
            url = make_url(raw)
        except Exception:
            _configuration_error()
        if url.drivername != "postgresql+asyncpg" or url.username != role:
            _configuration_error()
        configured[role_var] = role
        targets.add((url.host, url.port, url.database))
    if len(set(configured.values())) != len(configured) or len(targets) != 1:
        _configuration_error()
    connection = op.get_bind()
    current_user = str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    if current_user in configured.values() or _membership_is_unsafe(connection, tuple(configured.values())):
        _configuration_error()
    rows = connection.execute(sa.text(
        "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolinherit,rolreplication,rolbypassrls "
        "FROM pg_roles WHERE rolname=ANY(:roles)"
    ), {"roles": sorted(configured.values())}).mappings().all()
    if len(rows) != len(configured) or any(
        row[flag] for row in rows
        for flag in ("rolsuper", "rolcreaterole", "rolcreatedb", "rolinherit", "rolreplication", "rolbypassrls")
    ):
        _configuration_error()
    return tuple(configured[name] for name, _ in _NEW_IDENTITIES)  # type: ignore[return-value]


def _uuid(name: str, *, nullable: bool = False):
    return sa.Column(name, postgresql.UUID(as_uuid=False), nullable=nullable)


def _ts(name: str, *, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _create_tables() -> None:
    op.create_table("member_service_invitation",
        _uuid("invitation_id"), sa.Column("tenant_id",sa.BigInteger(),nullable=False), sa.Column("mode",sa.String(16),nullable=False), sa.Column("phone_ciphertext",sa.LargeBinary(),nullable=False), sa.Column("phone_key_id",sa.String(64),nullable=False), sa.Column("phone_digest",sa.CHAR(64),nullable=False), sa.Column("phone_digest_key_id",sa.String(64),nullable=False), sa.Column("phone_masked",sa.String(16),nullable=False), sa.Column("code_digest",sa.CHAR(64),nullable=False), sa.Column("code_key_id",sa.String(64),nullable=False), sa.Column("status",sa.String(16),nullable=False), sa.Column("failed_attempts",sa.SmallInteger(),nullable=False,server_default="0"), _ts("expires_at"), sa.Column("issued_by",sa.BigInteger(),nullable=False), _ts("issued_at"), _ts("accepted_at",nullable=True), _ts("revoked_at",nullable=True), sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("invitation_id",name="pk_member_service_invitation"), sa.ForeignKeyConstraint(["tenant_id"],["tenant.id"],name="fk_member_service_invitation_tenant_id_tenant"), sa.ForeignKeyConstraint(["issued_by"],["user.id"],name="fk_member_service_invitation_issued_by_user"), sa.CheckConstraint("mode IN ('SELF','PROXY_ELDER')",name="ck_member_service_invitation_mode"), sa.CheckConstraint("failed_attempts BETWEEN 0 AND 5 AND version>=1",name="ck_member_service_invitation_attempts"), sa.CheckConstraint("(status='INVITED' AND accepted_at IS NULL AND revoked_at IS NULL AND failed_attempts<5) OR (status='ACCEPTED' AND accepted_at IS NOT NULL AND revoked_at IS NULL) OR (status='REVOKED' AND accepted_at IS NULL AND revoked_at IS NOT NULL) OR (status='EXPIRED' AND accepted_at IS NULL AND revoked_at IS NULL)",name="ck_member_service_invitation_truth"), schema="public")
    op.create_index("uq_member_service_invitation_open_phone","member_service_invitation",["tenant_id","phone_digest_key_id","phone_digest"],unique=True,schema="public",postgresql_where=sa.text("status='INVITED'"))

    op.create_table("service_enrollment",
        _uuid("enrollment_id"), _uuid("invitation_id"), sa.Column("tenant_id",sa.BigInteger(),nullable=False), _uuid("subject_member_id",nullable=True), _uuid("proxy_member_id",nullable=True), sa.Column("mode",sa.String(16),nullable=False), sa.Column("status",sa.String(32),nullable=False), sa.Column("service_scope_tags",postgresql.JSONB(),nullable=False), _uuid("current_identity_verification_id",nullable=True), _uuid("current_assignment_id",nullable=True), _uuid("service_case_id",nullable=True), _ts("accepted_at",nullable=True), _ts("identity_verified_at",nullable=True), _ts("case_created_at",nullable=True), _ts("created_at"), _ts("updated_at"), sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("enrollment_id",name="pk_service_enrollment"), sa.UniqueConstraint("invitation_id",name="uq_service_enrollment_invitation"), sa.ForeignKeyConstraint(["invitation_id"],["public.member_service_invitation.invitation_id"],name="fk_service_enrollment_invitation_id_member_service_invitation"), sa.ForeignKeyConstraint(["tenant_id"],["tenant.id"],name="fk_service_enrollment_tenant_id_tenant"), sa.ForeignKeyConstraint(["subject_member_id"],["identity.member.member_id"],name="fk_service_enrollment_subject_member_id_member"), sa.ForeignKeyConstraint(["proxy_member_id"],["identity.member.member_id"],name="fk_service_enrollment_proxy_member_id_member"), sa.CheckConstraint("mode IN ('SELF','PROXY_ELDER') AND version>=1",name="ck_service_enrollment_mode_version"), sa.CheckConstraint("(mode='SELF' AND proxy_member_id IS NULL) OR (mode='PROXY_ELDER' AND proxy_member_id IS NOT NULL AND proxy_member_id<>subject_member_id)",name="ck_service_enrollment_subject_proxy"), sa.CheckConstraint("status IN ('ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED','PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED','IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING','CASE_CREATED','REJECTED','REVOKED','EXPIRED')",name="ck_service_enrollment_status"), schema="public")
    op.create_index("uq_service_enrollment_active_subject","service_enrollment",["subject_member_id"],unique=True,schema="public",postgresql_where=sa.text("status IN ('ACCEPTED','IDENTITY_SUBMITTED','INSTITUTION_CHECKED','PLATFORM_REVIEWING','NEEDS_CORRECTION','RESUBMITTED','IDENTITY_VERIFIED','CONSENT_PENDING','THERAPIST_PENDING','CASE_CREATED')"))

    op.create_table("controlled_member_bootstrap",
        _uuid("bootstrap_id"), _uuid("enrollment_id"), _uuid("member_id"), sa.Column("member_no",sa.String(64),nullable=False), sa.Column("creation_source",sa.String(32),nullable=False), _ts("created_at"), sa.Column("request_digest",sa.CHAR(64),nullable=False),
        sa.PrimaryKeyConstraint("bootstrap_id",name="pk_controlled_member_bootstrap"),sa.UniqueConstraint("enrollment_id",name="uq_controlled_member_bootstrap_enrollment"),sa.UniqueConstraint("member_id",name="uq_controlled_member_bootstrap_member"),sa.UniqueConstraint("member_no",name="uq_controlled_member_bootstrap_member_no"),sa.ForeignKeyConstraint(["enrollment_id"],["public.service_enrollment.enrollment_id"],name="fk_controlled_member_bootstrap_enrollment_id_service_enrollment"),sa.ForeignKeyConstraint(["member_id"],["identity.member.member_id"],name="fk_controlled_member_bootstrap_member_id_member"),sa.CheckConstraint("creation_source='controlled_proxy_enrollment'",name="ck_controlled_member_bootstrap_source"),schema="public")

    op.create_table("member_identity_verification",
        _uuid("verification_id"),_uuid("enrollment_id"),_uuid("member_id"),_uuid("current_revision_id",nullable=True),sa.Column("status",sa.String(24),nullable=False),_uuid("institution_decision_id",nullable=True),_uuid("platform_decision_id",nullable=True),_ts("submitted_at"),_ts("institution_checked_at",nullable=True),_ts("platform_decided_at",nullable=True),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("verification_id",name="pk_member_identity_verification"),sa.UniqueConstraint("enrollment_id",name="uq_member_identity_verification_enrollment"),sa.UniqueConstraint("verification_id","current_revision_id",name="uq_member_identity_verification_current_revision"),sa.ForeignKeyConstraint(["enrollment_id"],["public.service_enrollment.enrollment_id"],name="fk_member_identity_verification_enrollment"),sa.ForeignKeyConstraint(["member_id"],["identity.member.member_id"],name="fk_member_identity_verification_member_id_member"),sa.CheckConstraint("version>=1",name="ck_member_identity_verification_version"),schema="public")
    op.create_table("member_identity_revision",
        _uuid("revision_id"),_uuid("verification_id"),sa.Column("revision_no",sa.Integer(),nullable=False),sa.Column("document_type",sa.String(24),nullable=False),sa.Column("real_name_ciphertext",sa.LargeBinary(),nullable=False),sa.Column("real_name_key_id",sa.String(64),nullable=False),sa.Column("id_ciphertext",sa.LargeBinary(),nullable=False),sa.Column("id_key_id",sa.String(64),nullable=False),sa.Column("birth_date_ciphertext",sa.LargeBinary(),nullable=False),sa.Column("birth_date_key_id",sa.String(64),nullable=False),sa.Column("id_masked",sa.String(24),nullable=False),sa.Column("identity_fingerprint",sa.CHAR(64),nullable=False),sa.Column("fingerprint_key_id",sa.String(64),nullable=False),sa.Column("input_digest",sa.CHAR(64),nullable=False),_uuid("submitted_by_member_id"),_ts("created_at"),
        sa.PrimaryKeyConstraint("revision_id",name="pk_member_identity_revision"),sa.UniqueConstraint("verification_id","revision_no",name="uq_member_identity_revision_no"),sa.UniqueConstraint("verification_id","revision_id",name="uq_member_identity_revision_identity"),sa.ForeignKeyConstraint(["verification_id"],["public.member_identity_verification.verification_id"],name="fk_member_identity_revision_verification"),sa.ForeignKeyConstraint(["submitted_by_member_id"],["identity.member.member_id"],name="fk_member_identity_revision_submitted_by_member_id_member"),sa.CheckConstraint("revision_no>=1 AND document_type='PRC_RESIDENT_ID'",name="ck_member_identity_revision_truth"),schema="public")
    op.create_foreign_key("fk_member_identity_verification_current_revision","member_identity_verification","member_identity_revision",["verification_id","current_revision_id"],["verification_id","revision_id"],source_schema="public",referent_schema="public",deferrable=True,initially="DEFERRED")

    op.create_table("member_identity_review_decision",
        _uuid("decision_id"),_uuid("verification_id"),_uuid("revision_id"),sa.Column("phase",sa.String(16),nullable=False),sa.Column("reviewer_user_id",sa.BigInteger(),nullable=False),sa.Column("decision",sa.String(24),nullable=False),sa.Column("reason_code",sa.String(64)),sa.Column("correction_fields",postgresql.JSONB()),sa.Column("attestation_code",sa.String(64)),sa.Column("represented_elder_eligible",sa.Boolean()),sa.Column("request_digest",sa.CHAR(64),nullable=False),sa.Column("evidence_digest",sa.CHAR(64),nullable=False),_ts("created_at"),
        sa.PrimaryKeyConstraint("decision_id",name="pk_member_identity_review_decision"),sa.UniqueConstraint("verification_id","revision_id","phase",name="uq_member_identity_review_phase"),sa.ForeignKeyConstraint(["verification_id"],["public.member_identity_verification.verification_id"],name="fk_member_identity_decision_verification"),sa.ForeignKeyConstraint(["revision_id"],["public.member_identity_revision.revision_id"],name="fk_member_identity_decision_revision"),sa.ForeignKeyConstraint(["reviewer_user_id"],["user.id"],name="fk_member_identity_review_decision_reviewer_user_id_user"),sa.CheckConstraint("phase IN ('INSTITUTION','PLATFORM') AND decision IN ('CHECKED','APPROVED','NEEDS_CORRECTION','REJECTED')",name="ck_member_identity_review_decision_truth"),schema="public")

    op.create_table("member_identity_pii_access",
        _uuid("access_id"),_uuid("verification_id"),_uuid("current_revision_id"),sa.Column("reviewer_user_id",sa.BigInteger(),nullable=False),_uuid("nonce"),sa.Column("status",sa.String(16),nullable=False),sa.Column("reason_code",sa.String(64),nullable=False),sa.Column("access_token_digest",sa.CHAR(64),nullable=False),sa.Column("currentness_digest",sa.CHAR(64),nullable=False),sa.Column("idempotency_key",sa.String(128),nullable=False),sa.Column("request_digest",sa.CHAR(64),nullable=False),sa.Column("postimage_digest",sa.CHAR(64),nullable=False),_ts("issued_at"),_ts("consumed_at",nullable=True),_ts("expires_at"),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("access_id",name="pk_member_identity_pii_access"),sa.UniqueConstraint("nonce",name="uq_member_identity_pii_access_nonce"),sa.UniqueConstraint("reviewer_user_id","idempotency_key",name="uq_member_identity_pii_access_idempotency"),sa.ForeignKeyConstraint(["verification_id","current_revision_id"],["public.member_identity_verification.verification_id","public.member_identity_verification.current_revision_id"],name="fk_member_identity_pii_access_current_revision"),sa.ForeignKeyConstraint(["reviewer_user_id"],["user.id"],name="fk_member_identity_pii_access_reviewer_user_id_user"),sa.CheckConstraint("(status='ISSUED' AND consumed_at IS NULL) OR (status='CONSUMED' AND consumed_at IS NOT NULL)",name="ck_member_identity_pii_access_truth"),schema="public")

    op.create_table("proxy_grant",
        _uuid("grant_id"),_uuid("enrollment_id"),_uuid("principal_member_id"),_uuid("proxy_member_id"),sa.Column("slot_no",sa.SmallInteger(),nullable=False),sa.Column("permission_codes",postgresql.JSONB(),nullable=False),_uuid("authorization_document_version_id",nullable=True),_uuid("witness_decision_id",nullable=True),sa.Column("status",sa.String(24),nullable=False),_ts("valid_from",nullable=True),_ts("valid_until",nullable=True),_ts("revoked_at",nullable=True),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("grant_id",name="pk_proxy_grant"),sa.UniqueConstraint("enrollment_id",name="uq_proxy_grant_enrollment"),sa.ForeignKeyConstraint(["enrollment_id"],["public.service_enrollment.enrollment_id"],name="fk_proxy_grant_enrollment_id_service_enrollment"),sa.ForeignKeyConstraint(["principal_member_id"],["identity.member.member_id"],name="fk_proxy_grant_principal_member_id_member"),sa.ForeignKeyConstraint(["proxy_member_id"],["identity.member.member_id"],name="fk_proxy_grant_proxy_member_id_member"),sa.ForeignKeyConstraint(["witness_decision_id"],["public.member_identity_review_decision.decision_id"],name="fk_proxy_grant_witness_decision"),sa.CheckConstraint("slot_no IN (1,2) AND principal_member_id<>proxy_member_id AND version>=1",name="ck_proxy_grant_subject"),sa.CheckConstraint("(status='CONSENT_PENDING' AND authorization_document_version_id IS NULL AND witness_decision_id IS NULL AND valid_from IS NULL AND valid_until IS NULL AND revoked_at IS NULL) OR (status='ACTIVE' AND authorization_document_version_id IS NOT NULL AND witness_decision_id IS NOT NULL AND valid_from IS NOT NULL AND revoked_at IS NULL) OR (status IN ('REVOKED','EXPIRED') AND revoked_at IS NOT NULL)",name="ck_proxy_grant_truth"),schema="public")
    op.create_index("uq_proxy_grant_active_principal","proxy_grant",["principal_member_id"],unique=True,schema="public",postgresql_where=sa.text("status IN ('CONSENT_PENDING','ACTIVE')"))
    op.create_index("uq_proxy_grant_active_slot","proxy_grant",["proxy_member_id","slot_no"],unique=True,schema="public",postgresql_where=sa.text("status IN ('CONSENT_PENDING','ACTIVE')"))

    op.create_table("consent_document_version",
        _uuid("document_version_id"),sa.Column("document_type",sa.String(40),nullable=False),sa.Column("semantic_version",sa.String(32),nullable=False),sa.Column("status",sa.String(16),nullable=False),sa.Column("requires_reconsent",sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column("manifest_digest",sa.CHAR(64),nullable=False),_ts("effective_at",nullable=True),_ts("retired_at",nullable=True),sa.Column("published_by",sa.BigInteger()),_ts("created_at"),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("document_version_id",name="pk_consent_document_version"),sa.UniqueConstraint("document_type","semantic_version",name="uq_consent_document_semantic_version"),sa.ForeignKeyConstraint(["published_by"],["user.id"],name="fk_consent_document_version_published_by_user"),sa.CheckConstraint("requires_reconsent=true AND version>=1",name="ck_consent_document_version_version"),sa.CheckConstraint("(status='DRAFT' AND effective_at IS NULL AND retired_at IS NULL AND published_by IS NULL) OR (status='PUBLISHED' AND effective_at IS NOT NULL AND retired_at IS NULL AND published_by IS NOT NULL) OR (status='RETIRED' AND effective_at IS NOT NULL AND retired_at IS NOT NULL AND published_by IS NOT NULL)",name="ck_consent_document_version_truth"),schema="public")
    op.create_index("uq_consent_document_published_type","consent_document_version",["document_type"],unique=True,schema="public",postgresql_where=sa.text("status='PUBLISHED'"))
    op.create_table("consent_document_rendition",
        _uuid("rendition_id"),_uuid("document_version_id"),sa.Column("locale",sa.String(35),nullable=False),sa.Column("title",sa.String(160),nullable=False),sa.Column("body",sa.Text(),nullable=False),sa.Column("content_sha256",sa.CHAR(64),nullable=False),_ts("created_at"),
        sa.PrimaryKeyConstraint("rendition_id",name="pk_consent_document_rendition"),sa.UniqueConstraint("document_version_id","locale",name="uq_consent_document_rendition_locale"),sa.ForeignKeyConstraint(["document_version_id"],["public.consent_document_version.document_version_id"],name="fk_consent_rendition_document_version"),schema="public")
    op.create_table("consent_record",
        _uuid("consent_record_id"),_uuid("enrollment_id"),_uuid("subject_member_id"),_uuid("proxy_member_id",nullable=True),sa.Column("document_type",sa.String(40),nullable=False),_uuid("document_version_id"),_uuid("rendition_id"),sa.Column("purpose_codes",postgresql.JSONB(),nullable=False),sa.Column("choice",sa.String(16),nullable=False),sa.Column("status",sa.String(16),nullable=False),_uuid("predecessor_id",nullable=True),_ts("presented_at"),_ts("accepted_at",nullable=True),_ts("withdrawn_at",nullable=True),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("consent_record_id",name="pk_consent_record"),sa.ForeignKeyConstraint(["enrollment_id"],["public.service_enrollment.enrollment_id"],name="fk_consent_record_enrollment_id_service_enrollment"),sa.ForeignKeyConstraint(["subject_member_id"],["identity.member.member_id"],name="fk_consent_record_subject_member_id_member"),sa.ForeignKeyConstraint(["proxy_member_id"],["identity.member.member_id"],name="fk_consent_record_proxy_member_id_member"),sa.ForeignKeyConstraint(["document_version_id"],["public.consent_document_version.document_version_id"],name="fk_consent_record_document_version_id_consent_document_version"),sa.ForeignKeyConstraint(["rendition_id"],["public.consent_document_rendition.rendition_id"],name="fk_consent_record_rendition_id_consent_document_rendition"),sa.ForeignKeyConstraint(["predecessor_id"],["public.consent_record.consent_record_id"],name="fk_consent_record_predecessor_id_consent_record"),sa.CheckConstraint("choice IN ('ACCEPTED','DECLINED') AND version>=1",name="ck_consent_record_choice"),sa.CheckConstraint("(status='PRESENTED' AND accepted_at IS NULL AND withdrawn_at IS NULL) OR (status='ACCEPTED' AND choice='ACCEPTED' AND accepted_at IS NOT NULL AND withdrawn_at IS NULL) OR (status='DECLINED' AND choice='DECLINED' AND accepted_at IS NULL AND withdrawn_at IS NULL) OR (status IN ('SUPERSEDED','WITHDRAWN') AND accepted_at IS NOT NULL)",name="ck_consent_record_truth"),schema="public")
    op.create_index("uq_consent_record_current","consent_record",["enrollment_id","document_type"],unique=True,schema="public",postgresql_where=sa.text("status='ACCEPTED'"))

    op.create_table("primary_therapist_assignment",
        _uuid("assignment_id"),_uuid("enrollment_id"),sa.Column("tenant_id",sa.BigInteger(),nullable=False),_uuid("subject_member_id"),_uuid("therapist_id"),sa.Column("status",sa.String(24),nullable=False),sa.Column("service_scope_tags",postgresql.JSONB(),nullable=False),sa.Column("reason_code",sa.String(64)),_uuid("service_case_id",nullable=True),sa.Column("created_by",sa.BigInteger(),nullable=False),_ts("created_at"),_ts("decided_at",nullable=True),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("assignment_id",name="pk_primary_therapist_assignment"),sa.UniqueConstraint("service_case_id",name="uq_primary_therapist_assignment_case"),sa.ForeignKeyConstraint(["enrollment_id"],["public.service_enrollment.enrollment_id"],name="fk_primary_assignment_enrollment"),sa.ForeignKeyConstraint(["tenant_id"],["tenant.id"],name="fk_primary_therapist_assignment_tenant_id_tenant"),sa.ForeignKeyConstraint(["subject_member_id"],["identity.member.member_id"],name="fk_primary_therapist_assignment_subject_member_id_member"),sa.ForeignKeyConstraint(["therapist_id"],["public.therapist_profile.therapist_id"],name="fk_primary_therapist_assignment_therapist_id_therapist_profile"),sa.ForeignKeyConstraint(["created_by"],["user.id"],name="fk_primary_therapist_assignment_created_by_user"),sa.CheckConstraint("version>=1 AND ((status='PENDING_ACCEPTANCE' AND reason_code IS NULL AND service_case_id IS NULL AND decided_at IS NULL) OR (status='ACCEPTED' AND reason_code IS NULL AND service_case_id IS NOT NULL AND decided_at IS NOT NULL) OR (status IN ('DECLINED','CANCELLED') AND reason_code IS NOT NULL AND service_case_id IS NULL AND decided_at IS NOT NULL))",name="ck_primary_therapist_assignment_truth"),schema="public")
    op.create_index("uq_primary_assignment_current_enrollment","primary_therapist_assignment",["enrollment_id"],unique=True,schema="public",postgresql_where=sa.text("status='PENDING_ACCEPTANCE'"))
    op.create_table("service_case",
        _uuid("case_id"),_uuid("enrollment_id"),_uuid("subject_member_id"),sa.Column("tenant_id",sa.BigInteger(),nullable=False),_uuid("primary_therapist_id"),_uuid("assignment_id"),sa.Column("status",sa.String(16),nullable=False),_uuid("identity_verification_id"),_uuid("identity_revision_id"),sa.Column("consent_set_digest",sa.CHAR(64),nullable=False),sa.Column("readiness_evidence_version",sa.BigInteger(),nullable=False),sa.Column("readiness_result_digest",sa.CHAR(64),nullable=False),sa.Column("service_scope_tags",postgresql.JSONB(),nullable=False),_ts("created_at"),_ts("updated_at"),sa.Column("version",sa.BigInteger(),nullable=False),
        sa.PrimaryKeyConstraint("case_id",name="pk_service_case"),sa.UniqueConstraint("enrollment_id",name="uq_service_case_enrollment"),sa.UniqueConstraint("assignment_id",name="uq_service_case_assignment"),sa.ForeignKeyConstraint(["enrollment_id"],["public.service_enrollment.enrollment_id"],name="fk_service_case_enrollment_id_service_enrollment"),sa.ForeignKeyConstraint(["subject_member_id"],["identity.member.member_id"],name="fk_service_case_subject_member_id_member"),sa.ForeignKeyConstraint(["tenant_id"],["tenant.id"],name="fk_service_case_tenant_id_tenant"),sa.ForeignKeyConstraint(["primary_therapist_id"],["public.therapist_profile.therapist_id"],name="fk_service_case_primary_therapist_id_therapist_profile"),sa.ForeignKeyConstraint(["assignment_id"],["public.primary_therapist_assignment.assignment_id"],name="fk_service_case_assignment_id_primary_therapist_assignment",deferrable=True,initially="DEFERRED"),sa.ForeignKeyConstraint(["identity_verification_id"],["public.member_identity_verification.verification_id"],name="fk_service_case_identity_verification"),sa.ForeignKeyConstraint(["identity_revision_id"],["public.member_identity_revision.revision_id"],name="fk_service_case_identity_revision_id_member_identity_revision"),sa.CheckConstraint("status='PREPARING' AND version>=1",name="ck_service_case_truth"),schema="public")
    op.create_index("uq_service_case_active_subject","service_case",["subject_member_id"],unique=True,schema="public",postgresql_where=sa.text("status='PREPARING'"))
    for name, table, columns in (
        ("uq_service_enrollment_enrollment_subject_member","service_enrollment",["enrollment_id","subject_member_id"]),
        ("uq_service_enrollment_enrollment_proxy_member","service_enrollment",["enrollment_id","proxy_member_id"]),
        ("uq_service_enrollment_enrollment_tenant_subject","service_enrollment",["enrollment_id","tenant_id","subject_member_id"]),
        ("uq_service_enrollment_current_case_inputs","service_enrollment",["enrollment_id","current_identity_verification_id","current_assignment_id"]),
        ("uq_member_identity_verification_enrollment_verification","member_identity_verification",["enrollment_id","verification_id"]),
        ("uq_member_identity_review_decision_verification_decision","member_identity_review_decision",["verification_id","decision_id"]),
        ("uq_primary_therapist_assignment_enrollment_assignment","primary_therapist_assignment",["enrollment_id","assignment_id"]),
        ("uq_primary_therapist_assignment_scope","primary_therapist_assignment",["enrollment_id","assignment_id","tenant_id","subject_member_id"]),
        ("uq_service_case_enrollment_case","service_case",["enrollment_id","case_id"]),
        ("uq_service_case_assignment_case","service_case",["assignment_id","case_id"]),
        ("uq_consent_document_rendition_document_rendition","consent_document_rendition",["document_version_id","rendition_id"]),
    ):
        op.create_unique_constraint(name,table,columns,schema="public")
    for name, source, target, local, remote, deferred in (
        ("fk_service_enrollment_current_identity_scope","service_enrollment","member_identity_verification",["enrollment_id","current_identity_verification_id"],["enrollment_id","verification_id"],False),
        ("fk_service_enrollment_current_assignment_scope","service_enrollment","primary_therapist_assignment",["enrollment_id","current_assignment_id"],["enrollment_id","assignment_id"],False),
        ("fk_service_enrollment_service_case_scope","service_enrollment","service_case",["enrollment_id","service_case_id"],["enrollment_id","case_id"],False),
        ("fk_member_identity_verification_institution_decision_scope","member_identity_verification","member_identity_review_decision",["verification_id","institution_decision_id"],["verification_id","decision_id"],False),
        ("fk_member_identity_verification_platform_decision_scope","member_identity_verification","member_identity_review_decision",["verification_id","platform_decision_id"],["verification_id","decision_id"],False),
        ("fk_member_identity_review_decision_revision_scope","member_identity_review_decision","member_identity_revision",["verification_id","revision_id"],["verification_id","revision_id"],False),
        ("fk_proxy_grant_principal_scope","proxy_grant","service_enrollment",["enrollment_id","principal_member_id"],["enrollment_id","subject_member_id"],False),
        ("fk_proxy_grant_proxy_scope","proxy_grant","service_enrollment",["enrollment_id","proxy_member_id"],["enrollment_id","proxy_member_id"],False),
        ("fk_consent_record_subject_scope","consent_record","service_enrollment",["enrollment_id","subject_member_id"],["enrollment_id","subject_member_id"],False),
        ("fk_consent_record_proxy_scope","consent_record","service_enrollment",["enrollment_id","proxy_member_id"],["enrollment_id","proxy_member_id"],False),
        ("fk_consent_record_rendition_scope","consent_record","consent_document_rendition",["document_version_id","rendition_id"],["document_version_id","rendition_id"],False),
        ("fk_primary_assignment_enrollment_scope","primary_therapist_assignment","service_enrollment",["enrollment_id","tenant_id","subject_member_id"],["enrollment_id","tenant_id","subject_member_id"],False),
        ("fk_primary_assignment_service_case_scope","primary_therapist_assignment","service_case",["assignment_id","service_case_id"],["assignment_id","case_id"],True),
        ("fk_service_case_enrollment_scope","service_case","service_enrollment",["enrollment_id","tenant_id","subject_member_id"],["enrollment_id","tenant_id","subject_member_id"],False),
        # service_case(identity_verification_id,identity_revision_id) ->
        # member_identity_verification(verification_id,current_revision_id)
        ("fk_service_case_identity_revision_scope","service_case","member_identity_verification",["identity_verification_id","identity_revision_id"],["verification_id","current_revision_id"],False),
        ("fk_service_case_current_inputs_scope","service_case","service_enrollment",["enrollment_id","identity_verification_id","assignment_id"],["enrollment_id","current_identity_verification_id","current_assignment_id"],True),
        ("fk_service_case_assignment_scope","service_case","primary_therapist_assignment",["enrollment_id","assignment_id","tenant_id","subject_member_id"],["enrollment_id","assignment_id","tenant_id","subject_member_id"],True),
    ):
        op.create_foreign_key(
            name,source,target,local,remote,source_schema="public",referent_schema="public",
            deferrable=True if deferred else None,initially="DEFERRED" if deferred else None,
        )
    op.create_foreign_key(
        "fk_proxy_grant_authorization_document_version","proxy_grant","consent_document_version",
        ["authorization_document_version_id"],["document_version_id"],
        source_schema="public",referent_schema="public",
    )

    op.create_table("member_enrollment_idempotency",sa.Column("actor_scope",sa.String(160),nullable=False),sa.Column("operation",sa.String(64),nullable=False),_uuid("target_id"),sa.Column("idempotency_key",sa.String(128),nullable=False),sa.Column("request_digest",sa.CHAR(64),nullable=False),sa.Column("response_ciphertext",sa.LargeBinary(),nullable=False),sa.Column("response_key_id",sa.String(64),nullable=False),sa.Column("postimage_digest",sa.CHAR(64),nullable=False),_ts("created_at"),sa.PrimaryKeyConstraint("actor_scope","operation","target_id","idempotency_key",name="pk_member_enrollment_idempotency"),schema="public")
    op.create_table("member_enrollment_audit",sa.Column("audit_id",sa.BigInteger(),sa.Identity(),primary_key=True),sa.Column("actor_scope",sa.String(160),nullable=False),sa.Column("action",sa.String(64),nullable=False),_uuid("object_id"),sa.Column("result",sa.String(16),nullable=False),sa.Column("reason_code",sa.String(64)),_uuid("request_id"),sa.Column("preimage_digest",sa.CHAR(64),nullable=False),sa.Column("postimage_digest",sa.CHAR(64),nullable=False),_ts("created_at"),schema="public")
    op.create_table("member_enrollment_outbox",_uuid("event_id"),sa.Column("event_type",sa.String(64),nullable=False),_uuid("aggregate_id"),sa.Column("tenant_id",sa.BigInteger()),sa.Column("payload",postgresql.JSONB(),nullable=False),sa.Column("payload_digest",sa.CHAR(64),nullable=False),sa.Column("status",sa.String(16),nullable=False),sa.Column("attempts",sa.SmallInteger(),nullable=False,server_default="0"),_ts("processing_at",nullable=True),_uuid("lease_owner",nullable=True),_ts("delivered_at",nullable=True),_ts("failed_at",nullable=True),_ts("created_at"),sa.Column("version",sa.BigInteger(),nullable=False),sa.PrimaryKeyConstraint("event_id",name="pk_member_enrollment_outbox"),sa.ForeignKeyConstraint(["tenant_id"],["tenant.id"],name="fk_member_enrollment_outbox_tenant_id_tenant"),sa.CheckConstraint("attempts BETWEEN 0 AND 3 AND version>=1",name="ck_member_enrollment_outbox_attempts"),sa.CheckConstraint("(status='PENDING' AND attempts BETWEEN 0 AND 2 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NULL) OR (status='PROCESSING' AND attempts BETWEEN 1 AND 3 AND processing_at IS NOT NULL AND lease_owner IS NOT NULL AND delivered_at IS NULL AND failed_at IS NULL) OR (status='DELIVERED' AND attempts BETWEEN 1 AND 3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NOT NULL AND failed_at IS NULL) OR (status='FAILED' AND attempts=3 AND processing_at IS NULL AND lease_owner IS NULL AND delivered_at IS NULL AND failed_at IS NOT NULL)",name="ck_member_enrollment_outbox_truth"),sa.CheckConstraint("event_type IN ("+",".join(repr(value) for value in _EVENT_TYPES)+")",name="ck_member_enrollment_outbox_event_type"),sa.CheckConstraint("tenant_id IS NOT NULL OR event_type IN ('CONSENT_DOCUMENT_PUBLISHED','CONSENT_DOCUMENT_RETIRED')",name="ck_member_enrollment_outbox_tenant_scope"),schema="public")
    op.create_table("member_enrollment_delivery",_uuid("delivery_id"),_uuid("event_id"),sa.Column("recipient_scope",sa.String(24),nullable=False),sa.Column("recipient_user_id",sa.BigInteger()),sa.Column("recipient_tenant_id",sa.BigInteger()),sa.Column("recipient_platform_code",sa.String(48)),sa.Column("target_digest",sa.CHAR(64),nullable=False),sa.Column("target_key_id",sa.String(64),nullable=False),sa.Column("payload",postgresql.JSONB(),nullable=False),sa.Column("payload_digest",sa.CHAR(64),nullable=False),_ts("created_at"),sa.PrimaryKeyConstraint("delivery_id",name="pk_member_enrollment_delivery"),sa.UniqueConstraint("event_id","target_key_id","target_digest",name="uq_member_enrollment_delivery_target"),sa.ForeignKeyConstraint(["event_id"],["public.member_enrollment_outbox.event_id"],name="fk_member_enrollment_delivery_event_id_member_enrollment_outbox"),sa.CheckConstraint("((recipient_user_id IS NOT NULL)::int+(recipient_tenant_id IS NOT NULL)::int+(recipient_platform_code IS NOT NULL)::int)=1",name="ck_member_enrollment_delivery_recipient"),schema="public")

    op.create_table("identity_claim_algorithm_state",sa.Column("singleton",sa.SmallInteger(),primary_key=True,server_default="1"),sa.Column("fingerprint_domain",sa.String(64),nullable=False),sa.Column("fingerprint_key_id",sa.String(64),nullable=False),sa.Column("version",sa.SmallInteger(),nullable=False,server_default="1"),_ts("created_at"),sa.CheckConstraint("singleton=1 AND version=1 AND fingerprint_domain='SLICE3_IDENTITY_FINGERPRINT_P1_V1'",name="ck_identity_claim_algorithm_state_singleton"),schema="identity")
    op.create_table("identity_subject_claim_registry",_uuid("claim_id"),sa.Column("identity_fingerprint",sa.CHAR(64),nullable=False),sa.Column("fingerprint_key_id",sa.String(64),nullable=False),sa.Column("user_ref",sa.BigInteger()),_uuid("member_id",nullable=True),sa.Column("source_kind",sa.String(16),nullable=False),_uuid("p1_submission_id",nullable=True),_uuid("p1_decision_ref",nullable=True),_uuid("slice3_revision_id",nullable=True),_uuid("slice3_decision_id",nullable=True),sa.Column("source_facts_version",sa.BigInteger(),nullable=False),sa.Column("source_evidence_digest",sa.CHAR(64),nullable=False),sa.Column("adult_eligible",sa.Boolean()),sa.Column("represented_elder_eligible",sa.Boolean()),_ts("claimed_at"),sa.Column("version",sa.BigInteger(),nullable=False),sa.PrimaryKeyConstraint("claim_id",name="pk_identity_subject_claim_registry"),sa.UniqueConstraint("identity_fingerprint",name="uq_identity_subject_claim_registry_fingerprint"),sa.UniqueConstraint("user_ref",name="uq_identity_subject_claim_registry_user"),sa.UniqueConstraint("member_id",name="uq_identity_subject_claim_registry_member"),sa.ForeignKeyConstraint(["member_id"],["identity.member.member_id"],name="fk_identity_subject_claim_registry_member_id_member"),sa.ForeignKeyConstraint(["p1_submission_id"],["public.identity_verification_submission.submission_id"],name="fk_identity_claim_p1_submission"),sa.ForeignKeyConstraint(["p1_decision_ref"],["public.identity_verification_decision.decision_ref"],name="fk_identity_claim_p1_decision"),sa.ForeignKeyConstraint(["slice3_revision_id"],["public.member_identity_revision.revision_id"],name="fk_identity_claim_slice3_revision"),sa.ForeignKeyConstraint(["slice3_decision_id"],["public.member_identity_review_decision.decision_id"],name="fk_identity_claim_slice3_decision"),sa.CheckConstraint("source_facts_version>=1 AND version>=1 AND char_length(identity_fingerprint)=64 AND char_length(source_evidence_digest)=64 AND ((source_kind='P1' AND user_ref IS NOT NULL AND p1_submission_id IS NOT NULL AND p1_decision_ref IS NOT NULL AND slice3_revision_id IS NULL AND slice3_decision_id IS NULL AND adult_eligible IS NULL AND represented_elder_eligible IS NULL) OR (source_kind='SLICE3' AND member_id IS NOT NULL AND user_ref IS NULL AND p1_submission_id IS NULL AND p1_decision_ref IS NULL AND slice3_revision_id IS NOT NULL AND slice3_decision_id IS NOT NULL))",name="ck_identity_subject_claim_source_truth"),schema="identity")
    op.create_table(
        "slice3_digest_algorithm_state",
        sa.Column("singleton",sa.SmallInteger(),primary_key=True,server_default="1"),
        sa.Column("request_key_id",sa.String(64),nullable=False),
        sa.Column("request_material_check",sa.CHAR(64),nullable=False),
        sa.Column("audit_key_id",sa.String(64),nullable=False),
        sa.Column("audit_material_check",sa.CHAR(64),nullable=False),
        sa.Column("outbox_key_id",sa.String(64),nullable=False),
        sa.Column("outbox_material_check",sa.CHAR(64),nullable=False),
        sa.Column("delivery_key_id",sa.String(64),nullable=False),
        sa.Column("delivery_material_check",sa.CHAR(64),nullable=False),
        sa.Column("consent_key_id",sa.String(64),nullable=False),
        sa.Column("consent_material_check",sa.CHAR(64),nullable=False),
        sa.Column("version",sa.SmallInteger(),nullable=False,server_default="1"),
        _ts("created_at"),
        sa.CheckConstraint(
            "singleton=1 AND version=1 AND request_key_id<>audit_key_id AND request_key_id<>outbox_key_id AND request_key_id<>consent_key_id AND request_key_id<>delivery_key_id AND audit_key_id<>outbox_key_id AND audit_key_id<>consent_key_id AND audit_key_id<>delivery_key_id AND outbox_key_id<>consent_key_id AND outbox_key_id<>delivery_key_id AND consent_key_id<>delivery_key_id AND request_material_check~'^[0-9a-f]{64}$' AND audit_material_check~'^[0-9a-f]{64}$' AND outbox_material_check~'^[0-9a-f]{64}$' AND consent_material_check~'^[0-9a-f]{64}$' AND delivery_material_check~'^[0-9a-f]{64}$' AND request_material_check<>audit_material_check AND request_material_check<>outbox_material_check AND request_material_check<>consent_material_check AND request_material_check<>delivery_material_check AND audit_material_check<>outbox_material_check AND audit_material_check<>consent_material_check AND audit_material_check<>delivery_material_check AND outbox_material_check<>consent_material_check AND outbox_material_check<>delivery_material_check AND consent_material_check<>delivery_material_check",
            name="ck_slice3_digest_algorithm_state_truth",
        ),
        schema="public",
    )


def _bootstrap_algorithm_state() -> None:
    (
        fingerprint_key_id,
        request_key_id, request_check,
        audit_key_id, audit_check,
        outbox_key_id, outbox_check,
        consent_key_id, consent_check,
        delivery_key_id, delivery_check,
        fingerprint_domain,
    ) = _digest_configuration()
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO identity.identity_claim_algorithm_state("
            "singleton,fingerprint_domain,fingerprint_key_id,version,created_at) "
            "VALUES(1,:domain,:key_id,1,clock_timestamp())"
        ),
        {"domain": fingerprint_domain, "key_id": fingerprint_key_id},
    )
    connection.execute(
        sa.text(
            "INSERT INTO public.slice3_digest_algorithm_state("
            "singleton,request_key_id,request_material_check,audit_key_id,"
            "audit_material_check,outbox_key_id,outbox_material_check,"
            "consent_key_id,consent_material_check,delivery_key_id,delivery_material_check,version,created_at) "
            "VALUES(1,:request_id,:request_check,:audit_id,:audit_check,"
            ":outbox_id,:outbox_check,:consent_id,:consent_check,:delivery_id,:delivery_check,1,clock_timestamp())"
        ),
        {
            "request_id": request_key_id, "request_check": request_check,
            "audit_id": audit_key_id, "audit_check": audit_check,
            "outbox_id": outbox_key_id, "outbox_check": outbox_check,
            "consent_id": consent_key_id, "consent_check": consent_check,
            "delivery_id": delivery_key_id, "delivery_check": delivery_check,
        },
    )


def _confirmation_row_lookup_sql() -> str:
    return """
          actual_row=NULL;
          CASE expected_row->>'table'
            WHEN 'member_service_invitation' THEN SELECT to_jsonb(x) INTO actual_row FROM public.member_service_invitation x WHERE x.invitation_id=(expected_row->'key'->>'invitation_id')::uuid;
            WHEN 'service_enrollment' THEN SELECT to_jsonb(x) INTO actual_row FROM public.service_enrollment x WHERE x.enrollment_id=(expected_row->'key'->>'enrollment_id')::uuid;
            WHEN 'controlled_member_bootstrap' THEN SELECT to_jsonb(x) INTO actual_row FROM public.controlled_member_bootstrap x WHERE x.bootstrap_id=(expected_row->'key'->>'bootstrap_id')::uuid;
            WHEN 'member' THEN SELECT to_jsonb(x) INTO actual_row FROM identity.member x WHERE x.member_id=(expected_row->'key'->>'member_id')::uuid;
            WHEN 'member_identity_verification' THEN SELECT to_jsonb(x) INTO actual_row FROM public.member_identity_verification x WHERE x.verification_id=(expected_row->'key'->>'verification_id')::uuid;
            WHEN 'member_identity_revision' THEN SELECT to_jsonb(x) INTO actual_row FROM public.member_identity_revision x WHERE x.revision_id=(expected_row->'key'->>'revision_id')::uuid;
            WHEN 'member_identity_review_decision' THEN SELECT to_jsonb(x) INTO actual_row FROM public.member_identity_review_decision x WHERE x.decision_id=(expected_row->'key'->>'decision_id')::uuid;
            WHEN 'member_identity_pii_access' THEN SELECT to_jsonb(x) INTO actual_row FROM public.member_identity_pii_access x WHERE x.access_id=(expected_row->'key'->>'access_id')::uuid;
            WHEN 'identity_subject_claim_registry' THEN SELECT to_jsonb(x) INTO actual_row FROM identity.identity_subject_claim_registry x WHERE x.claim_id=(expected_row->'key'->>'claim_id')::uuid;
            WHEN 'proxy_grant' THEN SELECT to_jsonb(x) INTO actual_row FROM public.proxy_grant x WHERE x.grant_id=(expected_row->'key'->>'grant_id')::uuid;
            WHEN 'consent_document_version' THEN SELECT to_jsonb(x) INTO actual_row FROM public.consent_document_version x WHERE x.document_version_id=(expected_row->'key'->>'document_version_id')::uuid;
            WHEN 'consent_document_rendition' THEN SELECT to_jsonb(x) INTO actual_row FROM public.consent_document_rendition x WHERE x.rendition_id=(expected_row->'key'->>'rendition_id')::uuid;
            WHEN 'consent_record' THEN SELECT to_jsonb(x) INTO actual_row FROM public.consent_record x WHERE x.consent_record_id=(expected_row->'key'->>'consent_record_id')::uuid;
            WHEN 'primary_therapist_assignment' THEN SELECT to_jsonb(x) INTO actual_row FROM public.primary_therapist_assignment x WHERE x.assignment_id=(expected_row->'key'->>'assignment_id')::uuid;
            WHEN 'service_case' THEN SELECT to_jsonb(x) INTO actual_row FROM public.service_case x WHERE x.case_id=(expected_row->'key'->>'case_id')::uuid;
            WHEN 'therapist_profile' THEN SELECT jsonb_build_object(
              'therapist_id',x.therapist_id,'tenant_id',x.tenant_id,'status',x.status,
              'service_tags',x.service_tags,'capacity_limit',x.capacity_limit,
              'active_case_count',x.active_case_count,
              'current_qualification_version_id',x.current_qualification_version_id,
              'qualification_valid_until',x.qualification_valid_until,
              'updated_at',x.updated_at,'version',x.version) INTO actual_row
              FROM public.therapist_profile x WHERE x.therapist_id=(expected_row->'key'->>'therapist_id')::uuid;
            WHEN 'member_enrollment_audit' THEN SELECT to_jsonb(x)-'audit_id' INTO actual_row FROM public.member_enrollment_audit x
              WHERE x.request_id=(expected_row->'key'->>'request_id')::uuid
                AND x.action=expected_row->'key'->>'action'
                AND x.object_id=(expected_row->'key'->>'object_id')::uuid;
            WHEN 'member_enrollment_outbox' THEN SELECT to_jsonb(x) INTO actual_row FROM public.member_enrollment_outbox x WHERE x.event_id=(expected_row->'key'->>'event_id')::uuid;
            ELSE RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_INVALID';
          END CASE;
    """


def _confirmation_collection_lookup_sql() -> str:
    return """
          actual_keys='[]'::jsonb;
          CASE expected_collection->>'table'
            WHEN 'member_identity_revision' THEN SELECT COALESCE(jsonb_agg(x.revision_id::text ORDER BY x.revision_id::text),'[]'::jsonb) INTO actual_keys FROM public.member_identity_revision x WHERE x.verification_id=(expected_collection->'scope'->>'verification_id')::uuid;
            WHEN 'member_identity_review_decision' THEN SELECT COALESCE(jsonb_agg(x.decision_id::text ORDER BY x.decision_id::text),'[]'::jsonb) INTO actual_keys FROM public.member_identity_review_decision x WHERE x.verification_id=(expected_collection->'scope'->>'verification_id')::uuid;
            WHEN 'member_identity_pii_access' THEN SELECT COALESCE(jsonb_agg(x.access_id::text ORDER BY x.access_id::text),'[]'::jsonb) INTO actual_keys FROM public.member_identity_pii_access x WHERE x.verification_id=(expected_collection->'scope'->>'verification_id')::uuid;
            WHEN 'identity_subject_claim_registry' THEN SELECT COALESCE(jsonb_agg(x.claim_id::text ORDER BY x.claim_id::text),'[]'::jsonb) INTO actual_keys FROM identity.identity_subject_claim_registry x WHERE x.slice3_revision_id=(expected_collection->'scope'->>'slice3_revision_id')::uuid;
            WHEN 'consent_document_rendition' THEN SELECT COALESCE(jsonb_agg(x.rendition_id::text ORDER BY x.rendition_id::text),'[]'::jsonb) INTO actual_keys FROM public.consent_document_rendition x WHERE x.document_version_id=(expected_collection->'scope'->>'document_version_id')::uuid;
            WHEN 'consent_record' THEN SELECT COALESCE(jsonb_agg(x.consent_record_id::text ORDER BY x.consent_record_id::text),'[]'::jsonb) INTO actual_keys FROM public.consent_record x WHERE x.enrollment_id=(expected_collection->'scope'->>'enrollment_id')::uuid;
            ELSE RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_INVALID';
          END CASE;
    """


_CONFIRMATION_MANIFEST = {
    "INVITATION_CREATE": (("member_service_invitation", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "INVITATION_RESEND": (("member_service_invitation", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "INVITATION_REVOKE": (("member_service_invitation", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "ENROLLMENT_ACCEPT": (("member_service_invitation", "service_enrollment", "member_enrollment_audit", "member_enrollment_outbox"), ("controlled_member_bootstrap", "member", "proxy_grant")),
    "IDENTITY_SUBMIT": (("service_enrollment", "member_identity_verification", "member_identity_revision", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "IDENTITY_RESUBMIT": (("service_enrollment", "member_identity_verification", "member_identity_revision", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "INSTITUTION_IDENTITY_CHECK": (("service_enrollment", "member_identity_verification", "member_identity_review_decision", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "PROXY_REVOKE": (("proxy_grant", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "CONSENT_RECORD": (("service_enrollment", "consent_record", "member_enrollment_audit", "member_enrollment_outbox"), ("proxy_grant",)),
    "CONSENT_WITHDRAW": (("consent_record", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "ASSIGNMENT_CREATE": (("service_enrollment", "primary_therapist_assignment", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "ASSIGNMENT_CANCEL": (("primary_therapist_assignment", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "IDENTITY_REVIEW_CLAIM": (("member_identity_verification", "member_enrollment_audit"), ()),
    "IDENTITY_REVIEW_DECIDE": (("service_enrollment", "member_identity_verification", "member_identity_review_decision", "member_enrollment_audit", "member_enrollment_outbox"), ("identity_subject_claim_registry", "proxy_grant")),
    "CONSENT_DOCUMENT_CREATE": (("consent_document_version", "consent_document_rendition"), ()),
    "CONSENT_DOCUMENT_PUBLISH": (("consent_document_version", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "CONSENT_DOCUMENT_RETIRE": (("consent_document_version", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "OUTBOX_REOPEN": (("member_enrollment_outbox", "member_enrollment_audit"), ()),
    "ASSIGNMENT_ACCEPT": (("service_enrollment", "primary_therapist_assignment", "service_case", "therapist_profile", "member_enrollment_audit", "member_enrollment_outbox"), ()),
    "ASSIGNMENT_DECLINE": (("primary_therapist_assignment", "member_enrollment_audit", "member_enrollment_outbox"), ()),
}


def _sql_text_array(values: tuple[str, ...]) -> str:
    return "ARRAY[" + ",".join(repr(value) for value in values) + "]::text[]"


def _confirmation_manifest_sql(error_code: str) -> str:
    cases = []
    for operation, (required, optional) in _CONFIRMATION_MANIFEST.items():
        required_sql = _sql_text_array(required)
        allowed_sql = _sql_text_array(required + optional)
        cases.append(
            f"WHEN '{operation}' THEN required_tables:={required_sql}; "
            f"allowed_tables:={allowed_sql};"
        )
    pii_operation = "IDENTITY_PII_ACCESS"
    cases.append(f"""WHEN '{pii_operation}' THEN
              IF expected_postimage->'postimage'->>'result_variant'='REPLAYED' THEN
                required_tables:=ARRAY[]::text[]; allowed_tables:=ARRAY[]::text[];
              ELSIF expected_postimage->'postimage'->>'result_variant' IN ('FAILED','RATE_LIMITED') THEN
                required_tables:=ARRAY['member_enrollment_audit']; allowed_tables:=required_tables;
              ELSE
                required_tables:=ARRAY['member_identity_pii_access','member_enrollment_audit']; allowed_tables:=required_tables;
              END IF;""")
    manifest_cases = "\n            ".join(cases)
    enrollment_accept = "ENROLLMENT_ACCEPT"
    review_decide = "IDENTITY_REVIEW_DECIDE"
    consent_record = "CONSENT_RECORD"
    return f"""
          SELECT array_agg(DISTINCT r->>'table' ORDER BY r->>'table'),
                 array_agg((r->>'table')||':'||(r->'key')::text
                   ORDER BY (r->>'table')||':'||(r->'key')::text),
                 count(*),count(DISTINCT (r->>'table')||':'||(r->'key')::text)
            INTO present_tables,post_keys,post_row_count,unique_post_row_count
          FROM jsonb_array_elements(expected_postimage->'postimage'->'rows') r;
          SELECT array_agg((r->>'table')||':'||(r->'key')::text
                   ORDER BY (r->>'table')||':'||(r->'key')::text),
                 count(*),count(DISTINCT (r->>'table')||':'||(r->'key')::text)
            INTO pre_keys,pre_row_count,unique_pre_row_count
          FROM jsonb_array_elements(expected_postimage->'preimage'->'rows') r;
          CASE operation
            {manifest_cases}
            ELSE RAISE EXCEPTION '{error_code}';
          END CASE;
          IF COALESCE(post_row_count,0)<>COALESCE(unique_post_row_count,0)
             OR COALESCE(pre_row_count,0)<>COALESCE(unique_pre_row_count,0)
             OR COALESCE(post_keys,ARRAY[]::text[])<>COALESCE(pre_keys,ARRAY[]::text[])
             OR NOT required_tables <@ COALESCE(present_tables,ARRAY[]::text[])
             OR NOT COALESCE(present_tables,ARRAY[]::text[]) <@ allowed_tables
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_postimage->'postimage'->'rows') r
               WHERE jsonb_typeof(r->'key')<>'object' OR jsonb_typeof(r->'value')<>'object')
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_postimage->'preimage'->'rows') r
               WHERE jsonb_typeof(r->'key')<>'object'
                 OR (r->'value'<>'null'::jsonb AND jsonb_typeof(r->'value')<>'object'))
             OR (SELECT count(*) FROM jsonb_array_elements(expected_postimage->'postimage'->'rows') r
                   WHERE r->>'table'='member_enrollment_audit')
                <>(expected_postimage->'audit'->>'expected_count')::integer
             OR (SELECT count(*) FROM jsonb_array_elements(expected_postimage->'postimage'->'rows') r
                   WHERE r->>'table'='member_enrollment_outbox')
                <>(expected_postimage->'outbox'->>'expected_count')::integer
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(expected_postimage->'postimage'->'rows') r
                   WHERE r->>'table'='member_enrollment_outbox'
                     AND r->'value'->'payload'->>'request_id'<>expected_postimage->>'request_id') THEN
            RAISE EXCEPTION '{error_code}';
          END IF;
          IF operation='{enrollment_accept}' THEN
            IF (SELECT r->'value'->>'mode' FROM jsonb_array_elements(
                  expected_postimage->'postimage'->'rows') r
                  WHERE r->>'table'='service_enrollment')='SELF' THEN
              IF present_tables && ARRAY['controlled_member_bootstrap','member','proxy_grant'] THEN
                RAISE EXCEPTION '{error_code}';
              END IF;
            ELSIF NOT ARRAY['controlled_member_bootstrap','member','proxy_grant'] <@ present_tables THEN
              RAISE EXCEPTION '{error_code}';
            END IF;
          ELSIF operation='{review_decide}' THEN
            IF expected_postimage->'postimage'->>'decision'='APPROVED' THEN
              IF NOT ARRAY['identity_subject_claim_registry'] <@ present_tables THEN
                RAISE EXCEPTION '{error_code}';
              END IF;
              IF (SELECT r->'value'->>'mode' FROM jsonb_array_elements(
                    expected_postimage->'postimage'->'rows') r
                    WHERE r->>'table'='service_enrollment')='PROXY_ELDER' THEN
                IF NOT ARRAY['proxy_grant'] <@ present_tables THEN
                  RAISE EXCEPTION '{error_code}';
                END IF;
              ELSIF 'proxy_grant'=ANY(present_tables) THEN
                RAISE EXCEPTION '{error_code}';
              END IF;
            ELSIF present_tables && ARRAY['identity_subject_claim_registry','proxy_grant'] THEN
              RAISE EXCEPTION '{error_code}';
            END IF;
          ELSIF operation='{consent_record}' THEN
            IF EXISTS (SELECT 1 FROM jsonb_array_elements(
                  expected_postimage->'postimage'->'rows') r
                  WHERE r->>'table'='consent_record'
                    AND r->'value'->>'document_type'='PROXY_AUTHORIZATION'
                    AND r->'value'->>'status'='ACCEPTED') THEN
              IF NOT ARRAY['proxy_grant'] <@ present_tables THEN
                RAISE EXCEPTION '{error_code}';
              END IF;
            ELSIF 'proxy_grant'=ANY(present_tables) THEN
              RAISE EXCEPTION '{error_code}';
            END IF;
          END IF;
          IF jsonb_typeof(expected_postimage->'preimage'->'collections')<>'array'
             OR jsonb_typeof(expected_postimage->'postimage'->'collections')<>'array' THEN
            RAISE EXCEPTION '{error_code}';
          END IF;
          SELECT array_agg((c->>'table')||':'||(c->'scope')::text||':'||(c->>'key_field')
                   ORDER BY (c->>'table')||':'||(c->'scope')::text||':'||(c->>'key_field')),
                 count(*),count(DISTINCT (c->>'table')||':'||(c->'scope')::text||':'||(c->>'key_field'))
            INTO pre_collection_ids,pre_collection_count,unique_pre_collection_count
          FROM jsonb_array_elements(expected_postimage->'preimage'->'collections') c;
          SELECT array_agg((c->>'table')||':'||(c->'scope')::text||':'||(c->>'key_field')
                   ORDER BY (c->>'table')||':'||(c->'scope')::text||':'||(c->>'key_field')),
                 count(*),count(DISTINCT (c->>'table')||':'||(c->'scope')::text||':'||(c->>'key_field'))
            INTO post_collection_ids,post_collection_count,unique_post_collection_count
          FROM jsonb_array_elements(expected_postimage->'postimage'->'collections') c;
          IF COALESCE(pre_collection_count,0)<>COALESCE(unique_pre_collection_count,0)
             OR COALESCE(post_collection_count,0)<>COALESCE(unique_post_collection_count,0)
             OR COALESCE(pre_collection_ids,ARRAY[]::text[])<>
                COALESCE(post_collection_ids,ARRAY[]::text[])
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(
                   expected_postimage->'preimage'->'collections') c
                 WHERE c->>'table' NOT IN ('member_identity_revision',
                   'member_identity_review_decision','member_identity_pii_access',
                   'identity_subject_claim_registry','consent_document_rendition','consent_record')
                   OR jsonb_typeof(c->'scope')<>'object'
                   OR jsonb_typeof(c->'keys')<>'array'
                   OR jsonb_array_length(c->'keys')<>(SELECT count(DISTINCT value)
                     FROM jsonb_array_elements_text(c->'keys')))
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(
                   expected_postimage->'postimage'->'collections') c
                 WHERE c->>'table' NOT IN ('member_identity_revision',
                   'member_identity_review_decision','member_identity_pii_access',
                   'identity_subject_claim_registry','consent_document_rendition','consent_record')
                   OR jsonb_typeof(c->'scope')<>'object'
                   OR jsonb_typeof(c->'keys')<>'array'
                   OR jsonb_array_length(c->'keys')<>(SELECT count(DISTINCT value)
                     FROM jsonb_array_elements_text(c->'keys')))
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(
                   expected_postimage->'postimage'->'collections') c
                 WHERE NOT c->>'table'=ANY(COALESCE(present_tables,ARRAY[]::text[])))
             OR EXISTS (SELECT 1 FROM jsonb_array_elements(
                   expected_postimage->'postimage'->'rows') r
                 WHERE r->>'table' IN ('member_identity_revision',
                   'member_identity_review_decision','member_identity_pii_access',
                   'identity_subject_claim_registry','consent_document_rendition','consent_record')
                   AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(
                     expected_postimage->'postimage'->'collections') c
                     WHERE c->>'table'=r->>'table'
                       AND ((r->'key')->>(c->>'key_field')) IN (
                         SELECT value FROM jsonb_array_elements_text(c->'keys')))) THEN
            RAISE EXCEPTION '{error_code}';
          END IF;
    """


def _create_mutation_confirmation_function(
    name: str, role: str, operations: tuple[str, ...]
) -> None:
    allowed = ",".join(f"'{value}'" for value in operations)
    row_lookup = _confirmation_row_lookup_sql()
    collection_lookup = _confirmation_collection_lookup_sql()
    confirm_manifest = _confirmation_manifest_sql("SLICE3_MUTATION_CONFIRM_INVALID")
    expected_manifest = _confirmation_manifest_sql("SLICE3_MUTATION_EXPECTED_INVALID")
    op.execute(f"""
        CREATE FUNCTION public.{name}(
          actor_scope VARCHAR,operation VARCHAR,target_id UUID,
          idempotency_key VARCHAR,request_digest CHAR(64),
          expected_postimage JSONB
        ) RETURNS TABLE(outcome VARCHAR(16),confirmed_postimage_digest CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE receipt_row RECORD; aggregate_value JSONB; audit_value JSONB;
        DECLARE outbox_value JSONB; actual_value JSONB; actual_digest CHAR(64);
        DECLARE expected_aggregate_id UUID; expected_audit_action VARCHAR;
        DECLARE expected_event_type VARCHAR; expected_require_outbox BOOLEAN;
        DECLARE expected_response_digest CHAR(64); expected_confirmed_digest CHAR(64);
        DECLARE expected_request_id UUID;
        DECLARE has_aggregate BOOLEAN; operation_valid BOOLEAN;
        DECLARE expected_row JSONB; actual_row JSONB; post_rows_match BOOLEAN:=TRUE;
        DECLARE pre_rows_match BOOLEAN:=TRUE; calculated_expected_digest CHAR(64);
        DECLARE actual_rows JSONB:='[]'::jsonb; actual_envelope JSONB;
        DECLARE actual_receipt JSONB;
        DECLARE present_tables TEXT[]; required_tables TEXT[]; allowed_tables TEXT[];
        DECLARE pre_keys TEXT[]; post_keys TEXT[];
        DECLARE pre_row_count BIGINT; post_row_count BIGINT;
        DECLARE unique_pre_row_count BIGINT; unique_post_row_count BIGINT;
        DECLARE pre_collection_ids TEXT[]; post_collection_ids TEXT[];
        DECLARE pre_collection_count BIGINT; post_collection_count BIGINT;
        DECLARE unique_pre_collection_count BIGINT; unique_post_collection_count BIGINT;
        DECLARE expected_collection JSONB; actual_keys JSONB;
        DECLARE actual_collections JSONB:='[]'::jsonb;
        DECLARE pre_collections_match BOOLEAN:=TRUE; post_collections_match BOOLEAN:=TRUE;
        BEGIN
          IF session_user<>'{role}' OR $2 NOT IN ({allowed}) THEN
            RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_FORBIDDEN';
          END IF;
          IF expected_postimage IS NULL OR jsonb_typeof(expected_postimage)<>'object'
             OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(expected_postimage) key)
                <> ARRAY['audit','operation','outbox','postimage','preimage','receipt',
                         'request_id','target_id']::text[] THEN
            RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_INVALID';
          END IF;
          BEGIN
            expected_aggregate_id=(expected_postimage->>'target_id')::uuid;
            expected_request_id=(expected_postimage->>'request_id')::uuid;
            expected_audit_action=expected_postimage->'audit'->>'action';
            expected_event_type=NULLIF(expected_postimage->'outbox'->>'event_type','');
            expected_require_outbox=(expected_postimage->'outbox'->>'expected_count')::integer>0;
            expected_response_digest=(expected_postimage->'receipt'->>'response_digest')::char(64);
            expected_confirmed_digest=NULLIF(expected_postimage->'receipt'->>'expected_confirmed_digest','')::char(64);
          EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_INVALID';
          END;
          IF expected_aggregate_id IS NULL OR expected_request_id IS NULL
             OR expected_postimage->>'operation'<>$2
             OR jsonb_typeof(expected_postimage->'preimage')<>'object'
             OR jsonb_typeof(expected_postimage->'postimage')<>'object'
             OR jsonb_typeof(expected_postimage->'preimage'->'rows')<>'array'
             OR jsonb_typeof(expected_postimage->'postimage'->'rows')<>'array'
             OR (expected_postimage->'audit'->>'expected_count')::integer<0
             OR (expected_postimage->'outbox'->>'expected_count')::integer<0
             OR (expected_postimage->'receipt'->>'expected_count')::integer<>1
             OR expected_audit_action IS NULL OR char_length(expected_response_digest)<>64
             OR COALESCE(expected_confirmed_digest::text,'') !~ '^[0-9a-f]{{64}}$' THEN
            RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_INVALID';
          END IF;
{confirm_manifest}
          calculated_expected_digest=pg_catalog.encode(pg_catalog.sha256(
            pg_catalog.convert_to(jsonb_set(expected_postimage,
              '{{receipt,expected_confirmed_digest}}','null'::jsonb,true)::text,'UTF8')),'hex');
          IF expected_confirmed_digest<>calculated_expected_digest THEN
            RAISE EXCEPTION 'SLICE3_MUTATION_CONFIRM_INVALID';
          END IF;
          SELECT * INTO receipt_row FROM public.member_enrollment_idempotency i
          WHERE i.actor_scope=$1 AND i.operation=$2
            AND i.target_id=$3 AND i.idempotency_key=$4
            AND i.request_digest=$5;
          aggregate_value=jsonb_strip_nulls(jsonb_build_object(
            'invitation',(SELECT to_jsonb(x) FROM public.member_service_invitation x WHERE x.invitation_id=expected_aggregate_id),
            'enrollment',(SELECT to_jsonb(x) FROM public.service_enrollment x WHERE x.enrollment_id=expected_aggregate_id),
            'bootstrap',(SELECT to_jsonb(x) FROM public.controlled_member_bootstrap x WHERE x.enrollment_id=expected_aggregate_id),
            'member',(SELECT to_jsonb(x) FROM identity.member x WHERE x.member_id=(
              SELECT e.subject_member_id FROM public.service_enrollment e WHERE e.enrollment_id=expected_aggregate_id)),
            'verification',(SELECT to_jsonb(x) FROM public.member_identity_verification x WHERE x.verification_id=expected_aggregate_id),
            'revisions',(SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.revision_no),'[]'::jsonb) FROM public.member_identity_revision x WHERE x.verification_id=expected_aggregate_id),
            'decisions',(SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.created_at,x.decision_id),'[]'::jsonb) FROM public.member_identity_review_decision x WHERE x.verification_id=expected_aggregate_id),
            'pii_access',(SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.issued_at,x.access_id),'[]'::jsonb) FROM public.member_identity_pii_access x WHERE x.access_id=expected_aggregate_id OR x.verification_id=expected_aggregate_id),
            'registry',(SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.claimed_at,x.claim_id),'[]'::jsonb)
              FROM identity.identity_subject_claim_registry x
              WHERE x.slice3_revision_id IN (SELECT r.revision_id FROM public.member_identity_revision r WHERE r.verification_id=expected_aggregate_id)),
            'proxy',(SELECT to_jsonb(x) FROM public.proxy_grant x WHERE x.grant_id=expected_aggregate_id),
            'document',(SELECT to_jsonb(x) FROM public.consent_document_version x WHERE x.document_version_id=expected_aggregate_id),
            'renditions',(SELECT COALESCE(jsonb_agg(to_jsonb(x) ORDER BY x.locale),'[]'::jsonb) FROM public.consent_document_rendition x WHERE x.document_version_id=expected_aggregate_id),
            'consent',(SELECT to_jsonb(x) FROM public.consent_record x WHERE x.consent_record_id=expected_aggregate_id),
            'assignment',(SELECT to_jsonb(x) FROM public.primary_therapist_assignment x WHERE x.assignment_id=expected_aggregate_id),
            'case',(SELECT to_jsonb(x) FROM public.service_case x WHERE x.case_id=expected_aggregate_id),
            'therapist',(SELECT to_jsonb(t) FROM public.therapist_profile t WHERE t.therapist_id=(
              SELECT c.primary_therapist_id FROM public.service_case c WHERE c.case_id=expected_aggregate_id)),
            'outbox_target',(SELECT to_jsonb(x) FROM public.member_enrollment_outbox x WHERE x.event_id=expected_aggregate_id)
          ));
          SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY a.audit_id),'[]'::jsonb)
            INTO audit_value FROM public.member_enrollment_audit a
            WHERE a.actor_scope=$1 AND a.request_id=expected_request_id;
          SELECT COALESCE(jsonb_agg(to_jsonb(o) ORDER BY o.event_id),'[]'::jsonb)
            INTO outbox_value FROM public.member_enrollment_outbox o
            WHERE o.payload->>'request_id'=expected_request_id::text;
          actual_value=jsonb_build_object(
            'receipt',CASE WHEN receipt_row.actor_scope IS NULL THEN NULL ELSE
              jsonb_build_object('actor_scope',receipt_row.actor_scope,'operation',receipt_row.operation,
                'target_id',receipt_row.target_id,'idempotency_key',receipt_row.idempotency_key,
                'request_digest',receipt_row.request_digest,'postimage_digest',receipt_row.postimage_digest,
                'response_key_id',receipt_row.response_key_id,
                'response_ciphertext_length',octet_length(receipt_row.response_ciphertext)) END,
            'aggregate',aggregate_value,'audit',audit_value,'outbox',outbox_value);
          FOR expected_row IN SELECT value FROM jsonb_array_elements(
              expected_postimage->'postimage'->'rows') LOOP
{row_lookup}
            actual_rows=actual_rows||jsonb_build_array(jsonb_build_object(
              'table',expected_row->>'table','key',expected_row->'key','value',actual_row));
            IF actual_row IS DISTINCT FROM expected_row->'value' THEN
              post_rows_match=FALSE;
            END IF;
          END LOOP;
          FOR expected_row IN SELECT value FROM jsonb_array_elements(
              expected_postimage->'preimage'->'rows') LOOP
{row_lookup}
            IF COALESCE(actual_row,'null'::jsonb) IS DISTINCT FROM expected_row->'value' THEN
              pre_rows_match=FALSE;
            END IF;
          END LOOP;
          FOR expected_collection IN SELECT value FROM jsonb_array_elements(
              expected_postimage->'postimage'->'collections') LOOP
{collection_lookup}
            actual_collections=actual_collections||jsonb_build_array(jsonb_build_object(
              'table',expected_collection->>'table','scope',expected_collection->'scope',
              'key_field',expected_collection->>'key_field','keys',actual_keys));
            IF actual_keys IS DISTINCT FROM expected_collection->'keys' THEN
              post_collections_match=FALSE;
            END IF;
          END LOOP;
          FOR expected_collection IN SELECT value FROM jsonb_array_elements(
              expected_postimage->'preimage'->'collections') LOOP
{collection_lookup}
            IF actual_keys IS DISTINCT FROM expected_collection->'keys' THEN
              pre_collections_match=FALSE;
            END IF;
          END LOOP;
          actual_receipt=CASE WHEN receipt_row.actor_scope IS NULL THEN 'null'::jsonb ELSE
            jsonb_build_object(
              'expected_count',1,'response_digest',receipt_row.postimage_digest,
              'actor_scope',receipt_row.actor_scope,'operation',receipt_row.operation,
              'target_id',receipt_row.target_id,'idempotency_key',receipt_row.idempotency_key,
              'request_digest',receipt_row.request_digest,
              'response_key_id',receipt_row.response_key_id,
              'response_ciphertext_sha256',pg_catalog.encode(
                pg_catalog.sha256(receipt_row.response_ciphertext),'hex'),
              'created_at',receipt_row.created_at,
              'expected_confirmed_digest',NULL) END;
          actual_envelope=jsonb_set(expected_postimage,'{{postimage,rows}}',actual_rows,true);
          actual_envelope=jsonb_set(actual_envelope,'{{postimage,collections}}',actual_collections,true);
          actual_envelope=jsonb_set(actual_envelope,'{{receipt}}',actual_receipt,true);
          actual_digest=pg_catalog.encode(pg_catalog.sha256(
            pg_catalog.convert_to(actual_envelope::text,'UTF8')),'hex');
          has_aggregate=(aggregate_value-'revisions'-'decisions'-'pii_access'-'registry'-'renditions')<>'{{}}'::jsonb
            OR aggregate_value->'revisions'<>'[]'::jsonb
            OR aggregate_value->'decisions'<>'[]'::jsonb
            OR aggregate_value->'pii_access'<>'[]'::jsonb
            OR aggregate_value->'registry'<>'[]'::jsonb
            OR aggregate_value->'renditions'<>'[]'::jsonb;
          operation_valid=CASE $2
            WHEN 'INVITATION_CREATE' THEN aggregate_value?'invitation'
              AND aggregate_value->'invitation'->>'status'='INVITED'
            WHEN 'INVITATION_RESEND' THEN aggregate_value?'invitation'
              AND aggregate_value->'invitation'->>'status'='INVITED'
            WHEN 'INVITATION_REVOKE' THEN aggregate_value?'invitation'
              AND aggregate_value->'invitation'->>'status'='REVOKED'
            WHEN 'ENROLLMENT_ACCEPT' THEN aggregate_value?'invitation'
              AND aggregate_value?'enrollment'
              AND aggregate_value->'invitation'->>'status'='ACCEPTED'
              AND (aggregate_value->'enrollment'->>'mode'='SELF'
                OR (aggregate_value?'bootstrap' AND aggregate_value?'member'))
            WHEN 'IDENTITY_SUBMIT' THEN aggregate_value?'verification'
              AND jsonb_array_length(aggregate_value->'revisions')>=1
            WHEN 'IDENTITY_RESUBMIT' THEN aggregate_value?'verification'
              AND jsonb_array_length(aggregate_value->'revisions')>=2
            WHEN 'INSTITUTION_IDENTITY_CHECK' THEN aggregate_value?'verification'
              AND jsonb_array_length(aggregate_value->'revisions')>=1
              AND jsonb_array_length(aggregate_value->'decisions')>=1
            WHEN 'PROXY_REVOKE' THEN aggregate_value?'proxy'
              AND aggregate_value->'proxy'->>'status'='REVOKED'
            WHEN 'CONSENT_RECORD' THEN aggregate_value?'consent'
            WHEN 'CONSENT_WITHDRAW' THEN aggregate_value?'consent'
              AND aggregate_value->'consent'->>'status'='WITHDRAWN'
            WHEN 'ASSIGNMENT_CREATE' THEN aggregate_value?'assignment'
              AND aggregate_value->'assignment'->>'status'='PENDING_ACCEPTANCE'
            WHEN 'ASSIGNMENT_CANCEL' THEN aggregate_value?'assignment'
              AND aggregate_value->'assignment'->>'status'='CANCELLED'
            WHEN 'IDENTITY_REVIEW_CLAIM' THEN aggregate_value?'verification'
            WHEN 'IDENTITY_PII_ACCESS' THEN aggregate_value?'verification'
              AND ((expected_postimage->'postimage')?'error_code'
                OR jsonb_array_length(aggregate_value->'pii_access')=1)
            WHEN 'IDENTITY_REVIEW_DECIDE' THEN aggregate_value?'verification'
              AND jsonb_array_length(aggregate_value->'revisions')>=1
              AND jsonb_array_length(aggregate_value->'decisions')>=1
            WHEN 'CONSENT_DOCUMENT_CREATE' THEN aggregate_value?'document'
              AND jsonb_array_length(aggregate_value->'renditions')>=1
            WHEN 'CONSENT_DOCUMENT_PUBLISH' THEN aggregate_value?'document'
              AND aggregate_value->'document'->>'status'='PUBLISHED'
              AND jsonb_array_length(aggregate_value->'renditions')>=1
            WHEN 'CONSENT_DOCUMENT_RETIRE' THEN aggregate_value?'document'
              AND aggregate_value->'document'->>'status'='RETIRED'
            WHEN 'OUTBOX_REOPEN' THEN aggregate_value?'outbox_target'
              AND aggregate_value->'outbox_target'->>'status'='PENDING'
            WHEN 'ASSIGNMENT_ACCEPT' THEN aggregate_value?'case'
              AND aggregate_value?'therapist'
              AND aggregate_value->'case'->>'status'='PREPARING'
            WHEN 'ASSIGNMENT_DECLINE' THEN aggregate_value?'assignment'
              AND aggregate_value->'assignment'->>'status'='DECLINED'
            ELSE FALSE END;
          IF receipt_row.actor_scope IS NULL AND pre_rows_match AND pre_collections_match
             AND audit_value='[]'::jsonb AND outbox_value='[]'::jsonb THEN
            RETURN QUERY SELECT 'NOT_COMMITTED'::varchar,actual_digest; RETURN;
          END IF;
          IF receipt_row.actor_scope IS NOT NULL
             AND post_rows_match AND post_collections_match
             AND receipt_row.postimage_digest=expected_response_digest
             AND receipt_row.actor_scope=expected_postimage->'receipt'->>'actor_scope'
             AND receipt_row.operation=expected_postimage->'receipt'->>'operation'
             AND receipt_row.target_id::text=expected_postimage->'receipt'->>'target_id'
             AND receipt_row.idempotency_key=expected_postimage->'receipt'->>'idempotency_key'
             AND receipt_row.request_digest::text=expected_postimage->'receipt'->>'request_digest'
             AND receipt_row.response_key_id=expected_postimage->'receipt'->>'response_key_id'
             AND pg_catalog.encode(pg_catalog.sha256(receipt_row.response_ciphertext),'hex')=
                 expected_postimage->'receipt'->>'response_ciphertext_sha256'
             AND receipt_row.created_at=(expected_postimage->'receipt'->>'created_at')::timestamptz
             AND jsonb_array_length(audit_value)=
                 (expected_postimage->'audit'->>'expected_count')::integer
             AND jsonb_array_length(outbox_value)=
                 (expected_postimage->'outbox'->>'expected_count')::integer
             AND actual_digest=expected_confirmed_digest
             AND expected_confirmed_digest::text~'^[0-9a-f]{{64}}$' THEN
            RETURN QUERY SELECT 'COMMITTED'::varchar,expected_confirmed_digest; RETURN;
          END IF;
          RETURN QUERY SELECT 'UNKNOWN'::varchar,actual_digest;
        END $$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION public.{name}(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) FROM PUBLIC")
    expected_name = name.replace("_confirm_v1", "_expected_v1")
    op.execute(f"""
        CREATE FUNCTION public.{expected_name}(actor_scope VARCHAR,operation VARCHAR,target_id UUID,
          idempotency_key VARCHAR,request_digest CHAR(64),expected_postimage JSONB)
        RETURNS TABLE(expected_confirmed_digest CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE canonical JSONB; calculated CHAR(64);
        DECLARE present_tables TEXT[]; required_tables TEXT[]; allowed_tables TEXT[];
        DECLARE pre_keys TEXT[]; post_keys TEXT[];
        DECLARE pre_row_count BIGINT; post_row_count BIGINT;
        DECLARE unique_pre_row_count BIGINT; unique_post_row_count BIGINT;
        DECLARE pre_collection_ids TEXT[]; post_collection_ids TEXT[];
        DECLARE pre_collection_count BIGINT; post_collection_count BIGINT;
        DECLARE unique_pre_collection_count BIGINT; unique_post_collection_count BIGINT;
        BEGIN
          IF session_user<>'{role}' OR operation NOT IN ({allowed})
             OR expected_postimage IS NULL OR jsonb_typeof(expected_postimage)<>'object'
             OR char_length(expected_postimage::text)=0
             OR expected_postimage->>'operation'<>operation
             OR expected_postimage->>'target_id'<>target_id::text
             OR expected_postimage->'preimage'->>'request_digest'<>request_digest::text
             OR jsonb_typeof(expected_postimage->'preimage'->'rows')<>'array'
             OR jsonb_typeof(expected_postimage->'postimage'->'rows')<>'array'
             OR expected_postimage->'receipt'->'expected_confirmed_digest'<>'null'::jsonb THEN
            RAISE EXCEPTION 'SLICE3_MUTATION_EXPECTED_INVALID';
          END IF;
{expected_manifest}
          canonical=jsonb_set(expected_postimage,
            '{{receipt,expected_confirmed_digest}}','null'::jsonb,true);
          calculated=pg_catalog.encode(pg_catalog.sha256(
            pg_catalog.convert_to(canonical::text,'UTF8')),'hex');
          RETURN QUERY SELECT calculated;
        END $$
    """)
    op.execute(f"REVOKE ALL ON FUNCTION public.{expected_name}(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) FROM PUBLIC")


def _safe_interfaces(roles: tuple[str, str, str, str, str]) -> None:
    enrollment, review, case, worker, _reader = roles
    op.execute("""
        CREATE FUNCTION public.slice3_immutable_algorithm_state_v1() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
        BEGIN RAISE EXCEPTION 'SLICE3_ALGORITHM_STATE_IMMUTABLE'; END $$
    """)
    op.execute("CREATE TRIGGER trg_identity_claim_algorithm_state_immutable BEFORE UPDATE OR DELETE ON identity.identity_claim_algorithm_state FOR EACH ROW EXECUTE FUNCTION public.slice3_immutable_algorithm_state_v1()")
    op.execute("CREATE TRIGGER trg_slice3_digest_algorithm_state_immutable BEFORE UPDATE OR DELETE ON public.slice3_digest_algorithm_state FOR EACH ROW EXECUTE FUNCTION public.slice3_immutable_algorithm_state_v1()")
    op.execute(
        "REVOKE ALL ON FUNCTION public.slice3_immutable_algorithm_state_v1() "
        "FROM PUBLIC"
    )
    op.execute(f"""
        CREATE FUNCTION public.slice3_digest_algorithm_guard_v1(value JSONB) RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE expected JSONB;
        BEGIN
          SELECT CASE
            WHEN session_user IN ('{enrollment}','{review}') THEN jsonb_build_object(
              'request_key_id',request_key_id,'request_material_check',request_material_check,
              'audit_key_id',audit_key_id,'audit_material_check',audit_material_check,
              'outbox_key_id',outbox_key_id,'outbox_material_check',outbox_material_check,
              'consent_key_id',consent_key_id,'consent_material_check',consent_material_check)
            WHEN session_user='{case}' THEN jsonb_build_object(
              'request_key_id',request_key_id,'request_material_check',request_material_check,
              'audit_key_id',audit_key_id,'audit_material_check',audit_material_check,
              'outbox_key_id',outbox_key_id,'outbox_material_check',outbox_material_check)
            WHEN session_user='{worker}' THEN jsonb_build_object(
              'audit_key_id',audit_key_id,'audit_material_check',audit_material_check,
              'outbox_key_id',outbox_key_id,'outbox_material_check',outbox_material_check,
              'delivery_key_id',delivery_key_id,'delivery_material_check',delivery_material_check)
            ELSE NULL END INTO expected
          FROM public.slice3_digest_algorithm_state WHERE singleton=1 FOR SHARE;
          IF expected IS NULL OR value IS NULL OR value<>expected THEN
            RAISE EXCEPTION 'SLICE3_DIGEST_ALGORITHM_MISMATCH';
          END IF;
          RETURN TRUE;
        END $$
    """)
    op.execute(f"""
        CREATE FUNCTION identity.sync_p1_identity_claim_registry_v1() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE algorithm_key VARCHAR(64); decision_row RECORD; member_value UUID; existing RECORD;
        DECLARE claim_value UUID;
        BEGIN
          IF NEW.status<>'verified' THEN RETURN NEW; END IF;
          SELECT fingerprint_key_id INTO algorithm_key
          FROM identity.identity_claim_algorithm_state WHERE singleton=1 FOR SHARE;
          IF algorithm_key IS NULL OR NEW.encryption_key_id<>algorithm_key THEN
            RAISE EXCEPTION 'SLICE3_IDENTITY_FINGERPRINT_ALGORITHM_MISMATCH';
          END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id_card_digest,22));
          SELECT d.decision_ref,d.facts_version,d.evidence_digest INTO decision_row
          FROM public.identity_verification_decision d
          WHERE d.user_ref=NEW.user_ref AND d.outcome='verified'
            AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n WHERE n.supersedes_ref=d.decision_ref)
          ORDER BY d.facts_version DESC LIMIT 1 FOR SHARE;
          IF decision_row.decision_ref IS NULL OR char_length(decision_row.evidence_digest)<>64 THEN
            RAISE EXCEPTION 'SLICE3_P1_IDENTITY_SOURCE_INVALID';
          END IF;
          SELECT l.member_id INTO member_value FROM identity.user_member_self_link l
          WHERE l.user_ref=NEW.user_ref FOR SHARE;
          SELECT * INTO existing FROM identity.identity_subject_claim_registry
          WHERE identity_fingerprint=NEW.id_card_digest FOR UPDATE;
          claim_value=(substr(md5(NEW.id_card_digest),1,8)||'-'||substr(md5(NEW.id_card_digest),9,4)||'-7'||substr(md5(NEW.id_card_digest),14,3)||'-8'||substr(md5(NEW.id_card_digest),17,3)||'-'||substr(md5(NEW.id_card_digest),20,12))::uuid;
          IF existing.claim_id IS NULL THEN
            INSERT INTO identity.identity_subject_claim_registry(
              claim_id,identity_fingerprint,fingerprint_key_id,user_ref,member_id,source_kind,
              p1_submission_id,p1_decision_ref,slice3_revision_id,slice3_decision_id,
              source_facts_version,source_evidence_digest,adult_eligible,
              represented_elder_eligible,claimed_at,version)
            VALUES(claim_value,NEW.id_card_digest,algorithm_key,NEW.user_ref,member_value,'P1',
              NEW.submission_id,decision_row.decision_ref,NULL,NULL,decision_row.facts_version,
              decision_row.evidence_digest,NULL,NULL,clock_timestamp(),1);
          ELSIF existing.source_kind='P1' AND existing.user_ref=NEW.user_ref THEN
            UPDATE identity.identity_subject_claim_registry SET
              member_id=COALESCE(member_value,member_id),p1_submission_id=NEW.submission_id,
              p1_decision_ref=decision_row.decision_ref,source_facts_version=decision_row.facts_version,
              source_evidence_digest=decision_row.evidence_digest,version=version+1
            WHERE claim_id=existing.claim_id;
          ELSE
            RAISE EXCEPTION 'SLICE3_IDENTITY_ALREADY_CLAIMED';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION identity.sync_p1_identity_claim_registry_v1() "
        "FROM PUBLIC"
    )
    op.execute("CREATE CONSTRAINT TRIGGER trg_slice3_p1_identity_claim_registry AFTER INSERT OR UPDATE OF status,id_card_digest,encryption_key_id ON public.identity_verification_submission DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION identity.sync_p1_identity_claim_registry_v1()")
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(
            SELECT 1 FROM public.identity_verification_submission s
            CROSS JOIN identity.identity_claim_algorithm_state a
            WHERE s.status='verified' AND a.singleton=1
              AND s.encryption_key_id<>a.fingerprint_key_id
          ) THEN RAISE EXCEPTION 'SLICE3_IDENTITY_FINGERPRINT_ALGORITHM_MISMATCH'; END IF;
        END $$
    """)
    op.execute("""
        INSERT INTO identity.identity_subject_claim_registry(
          claim_id,identity_fingerprint,fingerprint_key_id,user_ref,member_id,source_kind,
          p1_submission_id,p1_decision_ref,slice3_revision_id,slice3_decision_id,
          source_facts_version,source_evidence_digest,adult_eligible,
          represented_elder_eligible,claimed_at,version)
        SELECT
          (substr(md5(s.id_card_digest),1,8)||'-'||substr(md5(s.id_card_digest),9,4)||'-7'||substr(md5(s.id_card_digest),14,3)||'-8'||substr(md5(s.id_card_digest),17,3)||'-'||substr(md5(s.id_card_digest),20,12))::uuid,
          s.id_card_digest,a.fingerprint_key_id,s.user_ref,l.member_id,'P1',
          s.submission_id,d.decision_ref,NULL,NULL,d.facts_version,d.evidence_digest,
          NULL,NULL,clock_timestamp(),1
        FROM public.identity_verification_submission s
        CROSS JOIN identity.identity_claim_algorithm_state a
        LEFT JOIN identity.user_member_self_link l ON l.user_ref=s.user_ref
        JOIN LATERAL (
          SELECT decision_ref,facts_version,evidence_digest
          FROM public.identity_verification_decision d
          WHERE d.user_ref=s.user_ref AND d.outcome='verified'
            AND NOT EXISTS(SELECT 1 FROM public.identity_verification_decision n WHERE n.supersedes_ref=d.decision_ref)
          ORDER BY d.facts_version DESC LIMIT 1
        ) d ON TRUE
        WHERE s.status='verified' AND a.singleton=1
          AND char_length(d.evidence_digest)=64
    """)
    op.execute("CREATE FUNCTION public.lock_slice3_identity_fingerprint_v1(value CHAR(64)) RETURNS void LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$ SELECT pg_advisory_xact_lock(hashtextextended(value,22)) $$")
    op.execute("CREATE FUNCTION public.slice3_readiness_guard_v1(value BIGINT) RETURNS TABLE(evidence_version BIGINT,result_digest CHAR(64)) LANGUAGE sql SECURITY DEFINER VOLATILE SET search_path=pg_catalog,pg_temp AS $$ SELECT evidence_version,result_digest FROM public.institution_service_readiness WHERE tenant_id=value AND readiness_status='SERVICE_READY' FOR SHARE $$")
    op.execute("""
        CREATE FUNCTION public.slice3_assignment_candidate_guard_v1(
          value_therapist UUID,value_tenant BIGINT,value_scope_tags JSONB)
        RETURNS BOOLEAN
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE value_allowed BOOLEAN;
        BEGIN
          IF session_user <> '""" + enrollment + """' THEN
            RAISE EXCEPTION 'SLICE3_ASSIGNMENT_CANDIDATE_FORBIDDEN';
          END IF;
          SELECT true INTO value_allowed
          FROM public.therapist_profile p
          WHERE p.therapist_id=value_therapist
            AND p.tenant_id=value_tenant
            AND p.status='APPROVED_ACTIVE'
            AND p.current_qualification_version_id IS NOT NULL
            AND p.qualification_valid_until >=
              (statement_timestamp() AT TIME ZONE 'Asia/Shanghai')::date
            AND p.service_tags @> value_scope_tags
            AND p.active_case_count < p.capacity_limit
          FOR SHARE OF p;
          RETURN COALESCE(value_allowed,false);
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.slice3_verified_adult_authority_v1(value_user BIGINT,value_member UUID)
        RETURNS TABLE(eligible BOOLEAN,as_of_date DATE,submission_id UUID,decision_ref UUID,evidence_digest CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF session_user <> '""" + enrollment + """' THEN RAISE EXCEPTION 'SLICE3_AUTHORITY_FORBIDDEN'; END IF;
          RETURN QUERY SELECT r.adult_eligible,current_date,r.p1_submission_id,r.p1_decision_ref,r.source_evidence_digest
          FROM identity.identity_subject_claim_registry r
          WHERE r.user_ref=value_user AND r.member_id=value_member AND r.source_kind='P1' AND r.adult_eligible=true
          FOR SHARE;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION identity.claim_identity_subject_v1(
          value_claim UUID,value_user BIGINT,value_member UUID,value_fingerprint CHAR(64),value_key VARCHAR,
          value_source VARCHAR,value_p1_submission UUID,value_p1_decision UUID,value_slice3_revision UUID,
          value_slice3_decision UUID,value_facts_version BIGINT,value_evidence CHAR(64),
          value_adult BOOLEAN,value_elder BOOLEAN,value_claimed_at TIMESTAMPTZ)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE existing identity.identity_subject_claim_registry%ROWTYPE;
        DECLARE algorithm_key VARCHAR(64);
        BEGIN
          IF session_user <> '""" + review + """' THEN RAISE EXCEPTION 'SLICE3_CLAIM_FORBIDDEN'; END IF;
          SELECT fingerprint_key_id INTO algorithm_key
          FROM identity.identity_claim_algorithm_state WHERE singleton=1 FOR SHARE;
          IF algorithm_key IS NULL OR value_key<>algorithm_key OR value_source<>'SLICE3' OR
             value_user IS NOT NULL OR value_member IS NULL OR value_p1_submission IS NOT NULL OR
             value_p1_decision IS NOT NULL OR value_slice3_revision IS NULL OR
             value_slice3_decision IS NULL OR value_facts_version<1 THEN
            RAISE EXCEPTION 'SLICE3_IDENTITY_FINGERPRINT_ALGORITHM_MISMATCH';
          END IF;
          IF NOT EXISTS(
            SELECT 1 FROM public.member_identity_revision r
            JOIN public.member_identity_verification v
              ON v.verification_id=r.verification_id AND v.current_revision_id=r.revision_id
            JOIN public.member_identity_review_decision d
              ON d.verification_id=v.verification_id AND d.revision_id=r.revision_id
            WHERE r.revision_id=value_slice3_revision AND r.identity_fingerprint=value_fingerprint
              AND r.fingerprint_key_id=value_key AND v.member_id=value_member
              AND v.platform_decision_id=value_slice3_decision AND v.version=value_facts_version
              AND v.status='VERIFIED' AND d.decision_id=value_slice3_decision
              AND d.phase='PLATFORM' AND d.decision='APPROVED' AND d.evidence_digest=value_evidence
          ) THEN RAISE EXCEPTION 'SLICE3_IDENTITY_SOURCE_INVALID'; END IF;
          PERFORM pg_advisory_xact_lock(hashtextextended(value_fingerprint,22));
          SELECT * INTO existing FROM identity.identity_subject_claim_registry
          WHERE identity_fingerprint=value_fingerprint FOR UPDATE;
          IF FOUND THEN
            IF existing.claim_id<>value_claim OR existing.user_ref IS DISTINCT FROM value_user OR
               existing.member_id IS DISTINCT FROM value_member OR existing.fingerprint_key_id<>value_key OR
               existing.source_kind<>value_source OR existing.p1_submission_id IS DISTINCT FROM value_p1_submission OR
               existing.p1_decision_ref IS DISTINCT FROM value_p1_decision OR
               existing.slice3_revision_id IS DISTINCT FROM value_slice3_revision OR
               existing.slice3_decision_id IS DISTINCT FROM value_slice3_decision OR
               existing.source_facts_version<>value_facts_version OR existing.source_evidence_digest<>value_evidence OR
               existing.adult_eligible IS DISTINCT FROM value_adult OR
               existing.represented_elder_eligible IS DISTINCT FROM value_elder
            THEN RAISE EXCEPTION 'SLICE3_IDENTITY_CONFLICT'; END IF;
            RETURN;
          END IF;
          INSERT INTO identity.identity_subject_claim_registry(
            claim_id,identity_fingerprint,fingerprint_key_id,user_ref,member_id,source_kind,
            p1_submission_id,p1_decision_ref,slice3_revision_id,slice3_decision_id,
            source_facts_version,source_evidence_digest,adult_eligible,represented_elder_eligible,claimed_at,version)
          VALUES(value_claim,value_fingerprint,value_key,value_user,value_member,value_source,
            value_p1_submission,value_p1_decision,value_slice3_revision,value_slice3_decision,
            value_facts_version,value_evidence,value_adult,value_elder,value_claimed_at,1);
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.slice3_reviewer_step_up_budget_v1(
          value_verification UUID,value_revision UUID,value_reviewer BIGINT,value_actor_scope VARCHAR,
          value_idempotency_key VARCHAR,value_request_id UUID,value_request_digest CHAR(64),
          value_access_token_digest CHAR(64),value_currentness_digest CHAR(64),value_reason_code VARCHAR,
          value_password_valid BOOLEAN,value_user_version BIGINT,value_user_updated_at TIMESTAMPTZ,
          value_proof_issued_at TIMESTAMPTZ,value_proof_expires_at TIMESTAMPTZ,
          value_credential_proof_digest CHAR(64))
        RETURNS TABLE(result_variant VARCHAR,error_code VARCHAR,failure_count SMALLINT,
          window_started_at TIMESTAMPTZ,locked_until TIMESTAMPTZ,budget_postimage_digest CHAR(64),
          audit_action VARCHAR,audit_postimage_digest CHAR(64),audit_created_at TIMESTAMPTZ,
          credential_proof_marker CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE u public.user%ROWTYPE; v public.member_identity_verification%ROWTYPE;
          payload BYTEA=''::bytea; credential_key BYTEA; expected_proof TEXT;
          item TEXT; failures INTEGER; first_failure TIMESTAMPTZ; variant VARCHAR; code VARCHAR;
          digest_value TEXT; proof_marker CHAR(64); audit_digest CHAR(64);
          emitted_action VARCHAR;
        BEGIN
          IF session_user <> '""" + review + """' THEN RAISE EXCEPTION 'SLICE3_STEP_UP_FORBIDDEN'; END IF;
          SELECT * INTO u FROM public.user WHERE id=value_reviewer FOR UPDATE;
          SELECT * INTO v FROM public.member_identity_verification
            WHERE verification_id=value_verification FOR UPDATE;
          IF u.id IS NULL OR u.role<>'super_admin' OR u.status<>'active'
            OR u.tenant_id IS NOT NULL
            OR value_user_version<>1 OR u.updated_at<>value_user_updated_at
            OR v.verification_id IS NULL OR v.current_revision_id<>value_revision
            OR value_actor_scope<>(('user'||chr(58)||value_reviewer::text||chr(58)||'platform'))
            -- proof_expires_at-proof_issued_at is fixed at exactly 15 seconds.
            OR value_proof_expires_at-value_proof_issued_at<>interval '15 seconds'
            OR NOT (value_proof_issued_at<=transaction_timestamp()
              AND transaction_timestamp()<value_proof_expires_at)
          THEN RAISE EXCEPTION 'SLICE3_STEP_UP_FORBIDDEN'; END IF;
          FOREACH item IN ARRAY ARRAY[
            '1',value_reviewer::text,value_user_version::text,
            to_char(value_user_updated_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            value_verification::text,value_revision::text,value_actor_scope,value_idempotency_key,
            value_request_id::text,value_request_digest::text,value_access_token_digest::text,
            value_currentness_digest::text,value_reason_code,
            CASE WHEN value_password_valid THEN '1' ELSE '0' END,
            to_char(value_proof_issued_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            to_char(value_proof_expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
          ] LOOP
            payload:=payload||pg_catalog.convert_to(
              pg_catalog.octet_length(pg_catalog.convert_to(item,'UTF8'))::text||':'||item,'UTF8');
          END LOOP;
          credential_key:=pg_catalog.sha256(pg_catalog.convert_to('slice3-reviewer-credential-key:v1:'||u.password_hash,'UTF8'));
          expected_proof:=pg_catalog.encode(pg_catalog.sha256(credential_key||payload||credential_key),'hex');
          IF expected_proof<>value_credential_proof_digest THEN
            RAISE EXCEPTION 'SLICE3_STEP_UP_FORBIDDEN';
          END IF;
          proof_marker:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
            'slice3-stepup-proof:v1:'||value_credential_proof_digest::text,'UTF8')),'hex');
          SELECT LEAST(
              count(*) FILTER (WHERE action='IDENTITY_PII_STEP_UP_FAILED') +
              CASE WHEN count(*) FILTER (
                WHERE action='IDENTITY_PII_STEP_UP_RATE_LIMITED')>0 THEN 1 ELSE 0 END,
              5
            )::integer,min(created_at) INTO failures,first_failure
          FROM public.member_enrollment_audit
          WHERE actor_scope=value_actor_scope AND action IN (
              'IDENTITY_PII_STEP_UP_FAILED','IDENTITY_PII_STEP_UP_RATE_LIMITED')
            AND created_at>=transaction_timestamp()-interval '15 minutes';
          PERFORM 1 FROM public.member_enrollment_audit
          WHERE actor_scope=value_actor_scope
            AND action IN ('IDENTITY_PII_ACCESSED','IDENTITY_PII_STEP_UP_FAILED',
              'IDENTITY_PII_STEP_UP_RATE_LIMITED')
            AND preimage_digest=proof_marker;
          IF FOUND THEN
            digest_value:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              failures::text||':'||COALESCE(first_failure::text,'')||':REPLAYED:'||
              value_request_id::text,'UTF8')),'hex');
            RETURN QUERY SELECT 'REPLAYED'::varchar,'STEP_UP_REPLAYED'::varchar,
              failures::smallint,first_failure,
              CASE WHEN failures>=5 THEN first_failure+interval '15 minutes' ELSE NULL END,
              digest_value::char(64),NULL::varchar,NULL::char(64),NULL::timestamptz,
              proof_marker;
            RETURN;
          END IF;
          IF value_password_valid AND failures<5 THEN
            variant:='ELIGIBLE'; code:=NULL;
          ELSIF failures>=5 OR failures+1>=5 THEN
            variant:='RATE_LIMITED'; code:='STEP_UP_RATE_LIMITED';
          ELSE
            variant:='FAILED'; code:='STEP_UP_FORBIDDEN';
          END IF;
          IF variant<>'ELIGIBLE' THEN
            emitted_action:=CASE WHEN variant='RATE_LIMITED'
              THEN 'IDENTITY_PII_STEP_UP_RATE_LIMITED'
              ELSE 'IDENTITY_PII_STEP_UP_FAILED' END;
            audit_digest:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
              (CASE WHEN failures>=5 THEN failures ELSE failures+1 END)::text||':'||
              variant||':'||value_request_id::text,'UTF8')),'hex');
            IF failures<5 THEN failures:=failures+1; END IF;
            IF first_failure IS NULL THEN first_failure:=value_proof_issued_at; END IF;
          END IF;
          digest_value:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(failures::text||':'||COALESCE(first_failure::text,'')||':'||variant||':'||value_request_id::text,'UTF8')),'hex');
          RETURN QUERY SELECT variant,code,failures::smallint,first_failure,
            CASE WHEN failures>=5 THEN first_failure+interval '15 minutes' ELSE NULL END,
            digest_value::char(64),emitted_action,audit_digest,
            CASE WHEN emitted_action IS NULL THEN NULL ELSE value_proof_issued_at END,
            proof_marker;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.slice3_reviewer_pii_v1(
          value_access UUID,value_nonce UUID,value_review UUID,value_reviewer BIGINT,value_revision UUID,
          value_access_token_digest CHAR(64),value_currentness_digest CHAR(64),value_request_id UUID,
          value_actor_scope VARCHAR,value_request_digest CHAR(64),value_preimage_digest CHAR(64),
          value_credential_proof_marker CHAR(64),value_postimage_digest CHAR(64),
          value_consumed_at TIMESTAMPTZ)
        RETURNS TABLE(real_name_ciphertext BYTEA,real_name_key_id VARCHAR,id_ciphertext BYTEA,id_key_id VARCHAR,
          birth_date_ciphertext BYTEA,birth_date_key_id VARCHAR,access_id UUID,
          consumed_at TIMESTAMPTZ,postimage_digest CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE a public.member_identity_pii_access%ROWTYPE; consumed TIMESTAMPTZ;
        BEGIN
          IF session_user <> '""" + review + """' THEN RAISE EXCEPTION 'SLICE3_PII_FORBIDDEN'; END IF;
          PERFORM 1 FROM public.user u WHERE u.id=value_reviewer AND u.role='super_admin'
            AND u.status='active' AND u.tenant_id IS NULL FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE3_PII_FORBIDDEN'; END IF;
          PERFORM 1 FROM public.member_identity_verification v
            WHERE v.verification_id=value_review AND v.current_revision_id=value_revision FOR UPDATE;
          IF NOT FOUND THEN RAISE EXCEPTION 'SLICE3_PII_FORBIDDEN'; END IF;
          SELECT * INTO a FROM public.member_identity_pii_access p
            WHERE p.access_id=value_access FOR UPDATE;
          IF a.access_id IS NULL OR a.nonce<>value_nonce OR a.verification_id<>value_review
            OR a.current_revision_id<>value_revision OR a.reviewer_user_id<>value_reviewer
            OR a.status<>'ISSUED' OR a.version<>1 OR a.consumed_at IS NOT NULL
            OR a.expires_at<=transaction_timestamp()
            OR a.access_token_digest<>value_access_token_digest
            OR a.currentness_digest<>value_currentness_digest
            OR a.request_digest<>value_request_digest OR a.postimage_digest<>value_preimage_digest
            OR value_actor_scope<>(('user'||chr(58)||value_reviewer::text||chr(58)||'platform'))
          THEN RAISE EXCEPTION 'SLICE3_PII_FORBIDDEN'; END IF;
          consumed:=value_consumed_at;
          UPDATE public.member_identity_pii_access AS p
            SET status='CONSUMED',consumed_at=consumed,
                postimage_digest=value_postimage_digest,version=2
            WHERE p.access_id=value_access;
          INSERT INTO public.member_enrollment_audit(actor_scope,action,object_id,result,reason_code,
            request_id,preimage_digest,postimage_digest,created_at)
          VALUES(value_actor_scope,'IDENTITY_PII_ACCESSED',value_review,'SUCCESS',a.reason_code,
            value_request_id,value_credential_proof_marker,value_postimage_digest,consumed);
          RETURN QUERY SELECT r.real_name_ciphertext,r.real_name_key_id,r.id_ciphertext,r.id_key_id,
            r.birth_date_ciphertext,r.birth_date_key_id,value_access,consumed,value_postimage_digest
          FROM public.member_identity_verification v JOIN public.member_identity_revision r
            ON r.verification_id=v.verification_id AND r.revision_id=v.current_revision_id
          WHERE v.verification_id=value_review AND r.revision_id=value_revision
          FOR SHARE OF v,r;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.slice3_assignment_subject_v1(value_assignment UUID)
        RETURNS TABLE(assignment_id UUID,enrollment_id UUID,tenant_id BIGINT,subject_member_id UUID,
          therapist_id UUID,status VARCHAR,service_scope_tags JSONB,version BIGINT,
          proxy_authorized BOOLEAN)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE assignment_row RECORD; proxy_allowed BOOLEAN;
        BEGIN
          IF session_user <> '""" + case + """' THEN RAISE EXCEPTION 'SLICE3_ASSIGNMENT_FORBIDDEN'; END IF;
          SELECT a.assignment_id,a.enrollment_id,a.tenant_id,a.subject_member_id,a.therapist_id,
            a.status,a.service_scope_tags,a.version,e.mode,e.proxy_member_id
            INTO assignment_row
          FROM public.primary_therapist_assignment a
          JOIN public.service_enrollment e ON e.enrollment_id=a.enrollment_id
          WHERE a.assignment_id=value_assignment FOR SHARE OF a,e;
          IF assignment_row.assignment_id IS NULL THEN RETURN; END IF;
          IF assignment_row.mode='SELF' THEN
            proxy_allowed=TRUE;
          ELSE
            PERFORM 1 FROM public.proxy_grant pg
            WHERE pg.enrollment_id=assignment_row.enrollment_id
              AND pg.principal_member_id=assignment_row.subject_member_id
              AND pg.proxy_member_id=assignment_row.proxy_member_id
              AND pg.status='ACTIVE' AND pg.valid_from<=transaction_timestamp()
              AND (pg.valid_until IS NULL OR pg.valid_until>=transaction_timestamp())
              AND pg.permission_codes @> '["IDENTITY_SUBMIT","CONSENT_ACCEPT","DAILY_VIEW","DAILY_INPUT","REPORT_UPLOAD"]'::jsonb
            FOR SHARE;
            proxy_allowed=FOUND;
          END IF;
          RETURN QUERY SELECT assignment_row.assignment_id,assignment_row.enrollment_id,
            assignment_row.tenant_id,assignment_row.subject_member_id,assignment_row.therapist_id,
            assignment_row.status,assignment_row.service_scope_tags,assignment_row.version,
            proxy_allowed;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.slice3_idempotency_replay_v1(value_scope VARCHAR,value_operation VARCHAR,
          value_target UUID,value_key VARCHAR,value_digest CHAR(64))
        RETURNS TABLE(found BOOLEAN,response_ciphertext BYTEA,response_key_id VARCHAR,
          postimage_digest CHAR(64),created_at TIMESTAMPTZ)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE row_value public.member_enrollment_idempotency%ROWTYPE;
        BEGIN
          IF NOT (
            (session_user='""" + enrollment + """' AND value_scope LIKE 'user\\:%\\:tenant\\:%' AND value_operation IN (
              'INVITATION_CREATE','INVITATION_RESEND','INVITATION_REVOKE','ENROLLMENT_ACCEPT',
              'IDENTITY_SUBMIT','IDENTITY_RESUBMIT','INSTITUTION_IDENTITY_CHECK','PROXY_REVOKE',
              'CONSENT_RECORD','CONSENT_WITHDRAW','ASSIGNMENT_CREATE','ASSIGNMENT_CANCEL')) OR
            (session_user='""" + review + """' AND value_scope LIKE 'user\\:%\\:platform' AND value_operation IN (
              'IDENTITY_REVIEW_CLAIM','IDENTITY_PII_ACCESS','IDENTITY_REVIEW_DECIDE',
              'CONSENT_DOCUMENT_CREATE','CONSENT_DOCUMENT_PUBLISH','CONSENT_DOCUMENT_RETIRE','OUTBOX_REOPEN')) OR
            (session_user='""" + case + """' AND value_scope LIKE 'user\\:%\\:tenant\\:%' AND value_operation IN (
              'ASSIGNMENT_ACCEPT','ASSIGNMENT_DECLINE'))
          ) THEN RAISE EXCEPTION 'SLICE3_IDEMPOTENCY_FORBIDDEN'; END IF;
          SELECT * INTO row_value FROM public.member_enrollment_idempotency
          WHERE actor_scope=value_scope AND operation=value_operation AND target_id=value_target
            AND idempotency_key=value_key FOR SHARE;
          IF NOT FOUND THEN RETURN QUERY SELECT false,NULL::BYTEA,NULL::VARCHAR,NULL::CHAR(64),NULL::TIMESTAMPTZ; RETURN; END IF;
          IF row_value.request_digest<>value_digest THEN RAISE EXCEPTION 'SLICE3_IDEMPOTENCY_CONFLICT'; END IF;
          RETURN QUERY SELECT true,row_value.response_ciphertext,row_value.response_key_id,
            row_value.postimage_digest,row_value.created_at;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION public.slice3_idempotency_record_v1(value_scope VARCHAR,value_operation VARCHAR,
          value_target UUID,value_key VARCHAR,value_digest CHAR(64),value_response BYTEA,
          value_response_key VARCHAR,value_postimage CHAR(64),value_created_at TIMESTAMPTZ)
        RETURNS TABLE(found BOOLEAN,response_ciphertext BYTEA,response_key_id VARCHAR,
          postimage_digest CHAR(64),created_at TIMESTAMPTZ)
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        BEGIN
          IF NOT (
            (session_user='""" + enrollment + """' AND value_scope LIKE 'user\\:%\\:tenant\\:%' AND value_operation IN (
              'INVITATION_CREATE','INVITATION_RESEND','INVITATION_REVOKE','ENROLLMENT_ACCEPT',
              'IDENTITY_SUBMIT','IDENTITY_RESUBMIT','INSTITUTION_IDENTITY_CHECK','PROXY_REVOKE',
              'CONSENT_RECORD','CONSENT_WITHDRAW','ASSIGNMENT_CREATE','ASSIGNMENT_CANCEL')) OR
            (session_user='""" + review + """' AND value_scope LIKE 'user\\:%\\:platform' AND value_operation IN (
              'IDENTITY_REVIEW_CLAIM','IDENTITY_PII_ACCESS','IDENTITY_REVIEW_DECIDE',
              'CONSENT_DOCUMENT_CREATE','CONSENT_DOCUMENT_PUBLISH','CONSENT_DOCUMENT_RETIRE','OUTBOX_REOPEN')) OR
            (session_user='""" + case + """' AND value_scope LIKE 'user\\:%\\:tenant\\:%' AND value_operation IN (
              'ASSIGNMENT_ACCEPT','ASSIGNMENT_DECLINE'))
          ) THEN RAISE EXCEPTION 'SLICE3_IDEMPOTENCY_FORBIDDEN'; END IF;
          INSERT INTO public.member_enrollment_idempotency(actor_scope,operation,target_id,idempotency_key,
            request_digest,response_ciphertext,response_key_id,postimage_digest,created_at)
          VALUES(value_scope,value_operation,value_target,value_key,value_digest,value_response,
            value_response_key,value_postimage,value_created_at) ON CONFLICT DO NOTHING;
          RETURN QUERY SELECT r.found,r.response_ciphertext,r.response_key_id,r.postimage_digest,r.created_at
          FROM public.slice3_idempotency_replay_v1(value_scope,value_operation,value_target,value_key,value_digest) r;
        END $$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.slice3_delivery_target_digest_internal_v1(
          value_target JSONB,value_key_check CHAR(64)) RETURNS CHAR(64)
        LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
        DECLARE key_value BYTEA; inner_pad BYTEA; outer_pad BYTEA; message_value BYTEA;
        DECLARE index_value INTEGER; canonical_value TEXT; value_hash TEXT;
        BEGIN
          IF value_target IS NULL OR value_key_check!~'^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'SLICE3_DELIVERY_DIGEST_INVALID';
          END IF;
          key_value=decode(value_key_check,'hex');
          inner_pad=decode(repeat('36',64),'hex');
          outer_pad=decode(repeat('5c',64),'hex');
          FOR index_value IN 0..31 LOOP
            inner_pad=set_byte(inner_pad,index_value,get_byte(inner_pad,index_value)#get_byte(key_value,index_value));
            outer_pad=set_byte(outer_pad,index_value,get_byte(outer_pad,index_value)#get_byte(key_value,index_value));
          END LOOP;
          canonical_value=coalesce(value_target->>'recipient_scope','')||'|'||
            coalesce(value_target->>'recipient_user_id','')||'|'||
            coalesce(value_target->>'recipient_tenant_id','')||'|'||
            coalesce(value_target->>'recipient_platform_code','');
          value_hash=encode(sha256(convert_to(canonical_value,'UTF8')),'hex');
          message_value=convert_to('phase1-slice3/delivery-target/v1','UTF8')||
            decode('00','hex')||convert_to(value_hash,'UTF8');
          RETURN encode(sha256(outer_pad||sha256(inner_pad||message_value)),'hex');
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.slice3_delivery_target_digest_internal_v1(JSONB,CHAR) FROM PUBLIC")
    op.execute("""
        CREATE OR REPLACE FUNCTION public.slice3_outbox_recipient_targets_v1(value_event UUID,value_lease UUID)
        RETURNS TABLE(recipient_scope VARCHAR(24),recipient_user_id BIGINT,recipient_tenant_id BIGINT,
          recipient_platform_code VARCHAR(48),target_digest CHAR(64),target_key_id VARCHAR(64),
          payload JSONB,payload_digest CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE event_row RECORD; enrollment_value UUID; member_value UUID;
        DECLARE user_value BIGINT; tenant_value BIGINT; therapist_value UUID;
        DECLARE scope_value VARCHAR(24); platform_value VARCHAR(48); target_value JSONB;
        DECLARE delivery_id VARCHAR(64); delivery_check CHAR(64);
        BEGIN
          IF session_user <> '""" + worker + """' THEN RAISE EXCEPTION 'SLICE3_RECIPIENT_FORBIDDEN'; END IF;
          SELECT o.event_type,o.aggregate_id,o.tenant_id,o.payload,o.payload_digest
          INTO event_row FROM public.member_enrollment_outbox o
          WHERE o.event_id=value_event AND o.status='PROCESSING' AND o.lease_owner=value_lease
          FOR SHARE;
          IF event_row.event_type IS NULL THEN RETURN; END IF;
          SELECT s.delivery_key_id,s.delivery_material_check INTO delivery_id,delivery_check
          FROM public.slice3_digest_algorithm_state s WHERE s.singleton=1 FOR SHARE;

          IF event_row.event_type IN ('MEMBER_IDENTITY_INSTITUTION_CHECKED',
              'CONSENT_DOCUMENT_PUBLISHED','CONSENT_DOCUMENT_RETIRED') THEN
            scope_value='PLATFORM'; platform_value='MEMBER_IDENTITY_REVIEW_QUEUE';
            target_value=jsonb_build_object('recipient_platform_code',platform_value,
              'recipient_scope',scope_value,'recipient_tenant_id',NULL,'recipient_user_id',NULL);
            RETURN QUERY SELECT scope_value,NULL::BIGINT,NULL::BIGINT,platform_value,
              public.slice3_delivery_target_digest_internal_v1(target_value,delivery_check),delivery_id,
              event_row.payload,event_row.payload_digest;
            RETURN;
          END IF;

          IF event_row.event_type IN ('MEMBER_INVITATION_CREATED','MEMBER_INVITATION_RESENT',
              'MEMBER_INVITATION_REVOKED','MEMBER_INVITATION_EXPIRED','MEMBER_ENROLLMENT_ACCEPTED',
              'MEMBER_IDENTITY_SUBMITTED','MEMBER_IDENTITY_RESUBMITTED') THEN
            SELECT t.id INTO tenant_value FROM public.tenant t
            WHERE t.id=event_row.tenant_id AND t.status='active' FOR SHARE;
            IF tenant_value IS NULL THEN RETURN; END IF;
            scope_value='TENANT';
            target_value=jsonb_build_object('recipient_platform_code',NULL,'recipient_scope',scope_value,
              'recipient_tenant_id',tenant_value,'recipient_user_id',NULL);
            RETURN QUERY SELECT scope_value,NULL::BIGINT,tenant_value,NULL::VARCHAR(48),
              public.slice3_delivery_target_digest_internal_v1(target_value,delivery_check),delivery_id,
              event_row.payload,event_row.payload_digest;
            RETURN;
          END IF;

          IF event_row.event_type IN ('PRIMARY_ASSIGNMENT_CREATED','PRIMARY_ASSIGNMENT_DECLINED',
              'PRIMARY_ASSIGNMENT_CANCELLED') THEN
            SELECT p.therapist_id INTO therapist_value FROM public.primary_therapist_assignment p
            WHERE p.assignment_id=event_row.aggregate_id FOR SHARE;
            SELECT u.id INTO user_value FROM public.therapist_profile p JOIN public."user" u ON u.id=p.user_id
            WHERE p.therapist_id=therapist_value AND p.status='APPROVED_ACTIVE'
              AND u.status='active' AND u.role='health_manager' FOR SHARE OF p,u;
            IF user_value IS NULL THEN RETURN; END IF;
            scope_value='THERAPIST';
            target_value=jsonb_build_object('recipient_platform_code',NULL,'recipient_scope',scope_value,
              'recipient_tenant_id',NULL,'recipient_user_id',user_value);
            RETURN QUERY SELECT scope_value,user_value,NULL::BIGINT,NULL::VARCHAR(48),
              public.slice3_delivery_target_digest_internal_v1(target_value,delivery_check),delivery_id,
              event_row.payload,event_row.payload_digest;
            RETURN;
          END IF;

          IF event_row.event_type='SERVICE_CASE_PREPARING_CREATED' THEN
            SELECT c.enrollment_id,c.subject_member_id,c.primary_therapist_id,c.tenant_id
            INTO enrollment_value,member_value,therapist_value,tenant_value
            FROM public.service_case c WHERE c.case_id=event_row.aggregate_id FOR SHARE;
          ELSIF event_row.event_type LIKE 'MEMBER_IDENTITY_%' THEN
            SELECT v.enrollment_id INTO enrollment_value FROM public.member_identity_verification v
            WHERE v.verification_id=event_row.aggregate_id FOR SHARE;
          ELSIF event_row.event_type LIKE 'PROXY_GRANT_%' THEN
            SELECT g.enrollment_id INTO enrollment_value FROM public.proxy_grant g
            WHERE g.grant_id=event_row.aggregate_id FOR SHARE;
          ELSIF event_row.event_type LIKE 'CONSENT_%' THEN
            SELECT c.enrollment_id INTO enrollment_value FROM public.consent_record c
            WHERE c.consent_record_id=event_row.aggregate_id FOR SHARE;
          END IF;
          IF enrollment_value IS NOT NULL AND member_value IS NULL THEN
            SELECT CASE WHEN e.mode='PROXY_ELDER' THEN e.proxy_member_id ELSE e.subject_member_id END,
              e.tenant_id INTO member_value,tenant_value
            FROM public.service_enrollment e WHERE e.enrollment_id=enrollment_value FOR SHARE;
          END IF;
          SELECT u.id INTO user_value FROM identity.user_member_self_link l
          JOIN public."user" u ON u.id=l.user_ref
          WHERE l.member_id=member_value AND u.status='active' FOR SHARE OF u,l;
          IF user_value IS NOT NULL THEN
            scope_value='SUBJECT';
            target_value=jsonb_build_object('recipient_platform_code',NULL,'recipient_scope',scope_value,
              'recipient_tenant_id',NULL,'recipient_user_id',user_value);
            RETURN QUERY SELECT scope_value,user_value,NULL::BIGINT,NULL::VARCHAR(48),
              public.slice3_delivery_target_digest_internal_v1(target_value,delivery_check),delivery_id,
              event_row.payload,event_row.payload_digest;
          END IF;
          IF event_row.event_type='SERVICE_CASE_PREPARING_CREATED' THEN
            SELECT u.id INTO user_value FROM public.therapist_profile p JOIN public."user" u ON u.id=p.user_id
            WHERE p.therapist_id=therapist_value AND p.status='APPROVED_ACTIVE'
              AND u.status='active' AND u.role='health_manager' FOR SHARE OF p,u;
            IF user_value IS NOT NULL THEN
              scope_value='THERAPIST';
              target_value=jsonb_build_object('recipient_platform_code',NULL,'recipient_scope',scope_value,
                'recipient_tenant_id',NULL,'recipient_user_id',user_value);
              RETURN QUERY SELECT scope_value,user_value,NULL::BIGINT,NULL::VARCHAR(48),
                public.slice3_delivery_target_digest_internal_v1(target_value,delivery_check),delivery_id,
                event_row.payload,event_row.payload_digest;
            END IF;
            SELECT t.id INTO tenant_value FROM public.tenant t
            WHERE t.id=tenant_value AND t.status='active' FOR SHARE;
            IF tenant_value IS NOT NULL THEN
              scope_value='TENANT';
              target_value=jsonb_build_object('recipient_platform_code',NULL,'recipient_scope',scope_value,
                'recipient_tenant_id',tenant_value,'recipient_user_id',NULL);
              RETURN QUERY SELECT scope_value,NULL::BIGINT,tenant_value,NULL::VARCHAR(48),
                public.slice3_delivery_target_digest_internal_v1(target_value,delivery_check),delivery_id,
                event_row.payload,event_row.payload_digest;
            END IF;
          END IF;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.slice3_outbox_recipient_targets_v1(UUID,UUID) FROM PUBLIC")
    op.execute("""
        CREATE OR REPLACE FUNCTION public.slice3_outbox_reopen_v1(
          value_event_id UUID,value_reviewer_user_id BIGINT,value_actor_scope VARCHAR(160),
          value_reason_code VARCHAR(64),value_expected_version BIGINT,
          value_idempotency_key VARCHAR(128),value_request_digest CHAR(64),
          value_expected_postimage JSONB
        ) RETURNS TABLE(event_id UUID,status VARCHAR(16),attempts SMALLINT,version BIGINT,
          audit_id BIGINT,postimage_digest CHAR(64))
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        DECLARE event_row public.member_enrollment_outbox%ROWTYPE; user_row RECORD;
        DECLARE actual JSONB; actual_digest CHAR(64); audit_value BIGINT; replay RECORD;
        DECLARE replay_actual JSONB;
        BEGIN
          IF session_user <> '""" + review + """' OR
             value_actor_scope<>(('user'||chr(58)||value_reviewer_user_id::text||chr(58)||'platform')) OR
             value_reason_code<>'MANUAL_RETRY_APPROVED' OR
             value_idempotency_key IS NULL OR char_length(value_idempotency_key)<8 OR
             value_request_digest!~'^[0-9a-f]{64}$' OR value_expected_postimage IS NULL OR
             (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(value_expected_postimage) k)
               <> ARRAY['attempts','event_id','status','version']::text[]
          THEN RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_FORBIDDEN'; END IF;
          SELECT * INTO event_row FROM public.member_enrollment_outbox o
          WHERE o.event_id=value_event_id FOR UPDATE;
          IF event_row.event_id IS NULL THEN
            RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_CONFLICT';
          END IF;
          SELECT u.id,u.role,u.status,u.tenant_id INTO user_row
          FROM public."user" u WHERE u.id=value_reviewer_user_id FOR SHARE;
          IF user_row.id IS NULL OR user_row.role<>'super_admin' OR user_row.status<>'active' OR
             user_row.tenant_id IS NOT NULL
          THEN RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_FORBIDDEN'; END IF;
          SELECT * INTO replay FROM public.member_enrollment_idempotency i
          WHERE i.actor_scope=value_actor_scope AND i.operation='OUTBOX_REOPEN'
            AND i.target_id=value_event_id AND i.idempotency_key=value_idempotency_key FOR SHARE;
          IF FOUND THEN
            IF replay.request_digest<>value_request_digest THEN
              RAISE EXCEPTION 'SLICE3_IDEMPOTENCY_CONFLICT';
            END IF;
            BEGIN
              replay_actual=convert_from(replay.response_ciphertext,'UTF8')::jsonb;
            EXCEPTION WHEN OTHERS THEN
              RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_CONFLICT';
            END;
            IF replay.response_key_id<>'internal-safe-v1'
               OR replay.postimage_digest<>encode(sha256(convert_to(
                    replace(replace(replay_actual::text,': ',':'),', ',','),'UTF8')),'hex')
               OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(replay_actual) k)
                    <> ARRAY['attempts','event_id','status','version']::text[]
            THEN RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_CONFLICT'; END IF;
            SELECT a.audit_id INTO STRICT audit_value FROM public.member_enrollment_audit a
            WHERE a.action='OUTBOX_REOPENED' AND a.object_id=value_event_id
              AND a.actor_scope=value_actor_scope AND a.postimage_digest=replay.postimage_digest;
            RETURN QUERY SELECT (replay_actual->>'event_id')::uuid,
              (replay_actual->>'status')::varchar(16),(replay_actual->>'attempts')::smallint,
              (replay_actual->>'version')::bigint,audit_value,replay.postimage_digest;
            RETURN;
          END IF;
          IF event_row.status<>'FAILED' OR event_row.attempts<>3 OR
             event_row.failed_at IS NULL OR event_row.version<>value_expected_version
          THEN RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_CONFLICT'; END IF;
          actual=jsonb_build_object('attempts',0,'event_id',value_event_id::text,
            'status','PENDING','version',event_row.version+1);
          IF actual<>value_expected_postimage THEN RAISE EXCEPTION 'SLICE3_OUTBOX_REOPEN_CONFLICT'; END IF;
          actual_digest=encode(sha256(convert_to(
            replace(replace(actual::text,': ',':'),', ',','),'UTF8')),'hex');
          UPDATE public.member_enrollment_outbox SET status='PENDING',attempts=0,
            processing_at=NULL,lease_owner=NULL,delivered_at=NULL,failed_at=NULL,
            version=event_row.version+1 WHERE member_enrollment_outbox.event_id=value_event_id;
          INSERT INTO public.member_enrollment_audit(actor_scope,action,object_id,result,reason_code,
            request_id,preimage_digest,postimage_digest,created_at)
          VALUES(value_actor_scope,'OUTBOX_REOPENED',value_event_id,'SUCCESS',value_reason_code,
            value_event_id,value_request_digest,actual_digest,statement_timestamp())
          RETURNING member_enrollment_audit.audit_id INTO audit_value;
          INSERT INTO public.member_enrollment_idempotency(actor_scope,operation,target_id,idempotency_key,
            request_digest,response_ciphertext,response_key_id,postimage_digest,created_at)
          VALUES(value_actor_scope,'OUTBOX_REOPEN',value_event_id,value_idempotency_key,
            value_request_digest,convert_to(actual::text,'UTF8'),'internal-safe-v1',actual_digest,
            statement_timestamp());
          RETURN QUERY SELECT value_event_id,'PENDING'::VARCHAR(16),0::SMALLINT,
            event_row.version+1,audit_value,actual_digest;
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.slice3_outbox_reopen_v1(UUID,BIGINT,VARCHAR,VARCHAR,BIGINT,VARCHAR,CHAR,JSONB) FROM PUBLIC")
    _create_mutation_confirmation_function(
        "slice3_enrollment_mutation_confirm_v1",
        enrollment,
        (
            "INVITATION_CREATE", "INVITATION_RESEND", "INVITATION_REVOKE",
            "ENROLLMENT_ACCEPT", "IDENTITY_SUBMIT", "IDENTITY_RESUBMIT",
            "INSTITUTION_IDENTITY_CHECK", "PROXY_REVOKE", "CONSENT_RECORD",
            "CONSENT_WITHDRAW", "ASSIGNMENT_CREATE", "ASSIGNMENT_CANCEL",
        ),
    )
    _create_mutation_confirmation_function(
        "slice3_identity_review_mutation_confirm_v1",
        review,
        (
            "IDENTITY_REVIEW_CLAIM", "IDENTITY_PII_ACCESS",
            "IDENTITY_REVIEW_DECIDE", "CONSENT_DOCUMENT_CREATE",
            "CONSENT_DOCUMENT_PUBLISH", "CONSENT_DOCUMENT_RETIRE", "OUTBOX_REOPEN",
        ),
    )
    _create_mutation_confirmation_function(
        "slice3_case_mutation_confirm_v1",
        case,
        ("ASSIGNMENT_ACCEPT", "ASSIGNMENT_DECLINE"),
    )
    op.execute(f"""
        CREATE FUNCTION public.slice3_collection_snapshot_v1(
          value_family VARCHAR,value_scope JSONB
        ) RETURNS UUID[]
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path=pg_catalog,pg_temp AS $$
        DECLARE scope_key VARCHAR; scope_id UUID; result UUID[];
        BEGIN
          IF session_user='{enrollment}' THEN
            IF value_family NOT IN (
              'IDENTITY_REVISION','REVIEW_DECISION','CONSENT_RECORD_SET'
            ) THEN
              RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_FORBIDDEN'
                USING ERRCODE='42501';
            END IF;
          ELSIF session_user='{review}' THEN
            IF value_family NOT IN ('REVIEW_DECISION','PII_ACCESS',
              'IDENTITY_REGISTRY','CONSENT_RENDITION') THEN
              RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_FORBIDDEN'
                USING ERRCODE='42501';
            END IF;
          ELSE
            RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_FORBIDDEN'
              USING ERRCODE='42501';
          END IF;
          IF value_scope IS NULL OR jsonb_typeof(value_scope)<>'object' THEN
            RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_INVALID'
              USING ERRCODE='22023';
          END IF;
          CASE value_family
            WHEN 'IDENTITY_REVISION' THEN scope_key:='verification_id';
            WHEN 'REVIEW_DECISION' THEN scope_key:='verification_id';
            WHEN 'PII_ACCESS' THEN scope_key:='verification_id';
            WHEN 'IDENTITY_REGISTRY' THEN scope_key:='slice3_revision_id';
            WHEN 'CONSENT_RENDITION' THEN scope_key:='document_version_id';
            WHEN 'CONSENT_RECORD_SET' THEN scope_key:='enrollment_id';
            ELSE RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_INVALID'
              USING ERRCODE='22023';
          END CASE;
          IF (SELECT count(*) FROM jsonb_object_keys(value_scope))<>1
             OR NOT value_scope ? scope_key
             OR jsonb_typeof(value_scope->scope_key)<>'string' THEN
            RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_INVALID'
              USING ERRCODE='22023';
          END IF;
          scope_id:=(value_scope->>scope_key)::UUID;
          PERFORM pg_advisory_xact_lock(hashtextextended(
            'slice3-collection:'||value_family||':'||scope_id::TEXT,0));
          CASE value_family
            WHEN 'IDENTITY_REVISION' THEN
              SELECT COALESCE(array_agg(revision_id ORDER BY revision_id),ARRAY[]::UUID[])
                INTO result FROM public.member_identity_revision
                WHERE verification_id=scope_id;
            WHEN 'REVIEW_DECISION' THEN
              SELECT COALESCE(array_agg(decision_id ORDER BY decision_id),ARRAY[]::UUID[])
                INTO result FROM public.member_identity_review_decision
                WHERE verification_id=scope_id;
            WHEN 'PII_ACCESS' THEN
              SELECT COALESCE(array_agg(access_id ORDER BY access_id),ARRAY[]::UUID[])
                INTO result FROM public.member_identity_pii_access
                WHERE verification_id=scope_id;
            WHEN 'IDENTITY_REGISTRY' THEN
              SELECT COALESCE(array_agg(claim_id ORDER BY claim_id),ARRAY[]::UUID[])
                INTO result FROM identity.identity_subject_claim_registry
                WHERE slice3_revision_id=scope_id;
            WHEN 'CONSENT_RENDITION' THEN
              SELECT COALESCE(array_agg(rendition_id ORDER BY rendition_id),ARRAY[]::UUID[])
                INTO result FROM public.consent_document_rendition
                WHERE document_version_id=scope_id;
            WHEN 'CONSENT_RECORD_SET' THEN
              SELECT COALESCE(array_agg(consent_record_id ORDER BY consent_record_id),ARRAY[]::UUID[])
                INTO result FROM public.consent_record
                WHERE enrollment_id=scope_id;
          END CASE;
          RETURN result;
        EXCEPTION WHEN invalid_text_representation THEN
          RAISE EXCEPTION 'SLICE3_COLLECTION_SNAPSHOT_INVALID'
            USING ERRCODE='22023';
        END $$
    """)
    op.execute("REVOKE ALL ON FUNCTION public.slice3_collection_snapshot_v1(VARCHAR,JSONB) FROM PUBLIC")
    op.execute("CREATE VIEW public.slice3_institution_enrollment_read_v1 WITH (security_barrier=true,security_invoker=false) AS SELECT i.invitation_id,a.tenant_public_id,i.mode,i.phone_masked,i.expires_at,i.status,i.failed_attempts,i.issued_at,i.accepted_at,i.revoked_at,i.version FROM public.member_service_invitation i JOIN public.institution_application a ON a.tenant_internal_id=i.tenant_id AND a.status='APPROVED'")
    op.execute("""
        CREATE VIEW public.slice3_family_enrollment_read_v1
        WITH (security_barrier=true,security_invoker=false) AS
        SELECT e.enrollment_id,a.tenant_public_id,
          e.subject_member_id,e.proxy_member_id,e.mode,e.status,e.service_scope_tags,
          e.current_identity_verification_id,e.current_assignment_id,e.service_case_id,
          e.accepted_at,e.identity_verified_at,e.case_created_at,e.version,
          CASE WHEN v.verification_id IS NULL THEN NULL ELSE jsonb_build_object(
            'verification_id',v.verification_id,'enrollment_id',v.enrollment_id,
            'member_id',v.member_id,'current_revision_id',v.current_revision_id,
            'status',v.status,'id_masked',ir.id_masked,'submitted_at',v.submitted_at,
            'institution_checked_at',v.institution_checked_at,
            'platform_decided_at',v.platform_decided_at,'reason_codes','[]'::jsonb,
            'version',v.version) END AS identity,
          CASE WHEN pg.grant_id IS NULL OR pg.status<>'ACTIVE'
            OR pg.valid_from>transaction_timestamp()
            OR (pg.valid_until IS NOT NULL AND pg.valid_until<transaction_timestamp())
            OR NOT (pg.permission_codes @> '["DAILY_VIEW"]'::jsonb)
          THEN NULL ELSE jsonb_build_object(
            'grant_id',pg.grant_id,'principal_member_id',pg.principal_member_id,
            'proxy_member_id',pg.proxy_member_id,'permission_codes',pg.permission_codes,
            'authorization_document_version_id',pg.authorization_document_version_id,
            'status',pg.status,'valid_from',pg.valid_from,'valid_until',pg.valid_until,
            'version',pg.version) END AS proxy,
          COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'consent_record_id',c.consent_record_id,'enrollment_id',c.enrollment_id,
            'document_type',c.document_type,'document_version_id',c.document_version_id,
            'rendition_id',c.rendition_id,'locale',r.locale,'choice',c.choice,
            'status',c.status,'presented_at',c.presented_at,'accepted_at',c.accepted_at,
            'withdrawn_at',c.withdrawn_at,'version',c.version)
            ORDER BY c.presented_at,c.consent_record_id)
            FROM public.consent_record c JOIN public.consent_document_rendition r
              ON r.rendition_id=c.rendition_id WHERE c.enrollment_id=e.enrollment_id),
            '[]'::jsonb) AS consents,
          CASE WHEN pa.assignment_id IS NULL THEN NULL ELSE jsonb_build_object(
            'assignment_id',pa.assignment_id,'enrollment_id',pa.enrollment_id,
            'tenant_id',a.tenant_public_id,'subject_member_id',pa.subject_member_id,
            'therapist_id',pa.therapist_id,'status',pa.status,
            'service_scope_tags',pa.service_scope_tags,'reason_code',pa.reason_code,
            'service_case_id',pa.service_case_id,'created_at',pa.created_at,
            'decided_at',pa.decided_at,'version',pa.version) END AS assignment
        FROM public.service_enrollment e
        JOIN public.institution_application a
          ON a.tenant_internal_id=e.tenant_id AND a.status='APPROVED'
        LEFT JOIN public.member_identity_verification v
          ON v.verification_id=e.current_identity_verification_id
        LEFT JOIN public.member_identity_revision ir
          ON ir.revision_id=v.current_revision_id
        LEFT JOIN public.proxy_grant pg ON pg.enrollment_id=e.enrollment_id
        LEFT JOIN public.primary_therapist_assignment pa
          ON pa.assignment_id=e.current_assignment_id
    """)
    op.execute("""
        CREATE VIEW public.slice3_platform_identity_review_read_v1
        WITH (security_barrier=true,security_invoker=false) AS
        SELECT v.verification_id,v.enrollment_id,v.member_id,e.mode,v.status,
          v.current_revision_id,r.revision_no AS current_revision_no,r.id_masked,
          institution.attestation_code AS institution_attestation,
          COALESCE(platform.correction_fields,'[]'::jsonb) AS correction_fields,
          CASE WHEN e.mode='PROXY_ELDER' THEN proxy.status ELSE NULL END AS proxy_witness_status,
          v.submitted_at,v.version
        FROM public.member_identity_verification v
        JOIN public.service_enrollment e ON e.enrollment_id=v.enrollment_id
        JOIN public.member_identity_revision r ON r.revision_id=v.current_revision_id
        LEFT JOIN public.member_identity_review_decision institution
          ON institution.decision_id=v.institution_decision_id
        LEFT JOIN public.member_identity_review_decision platform
          ON platform.decision_id=v.platform_decision_id
        LEFT JOIN public.proxy_grant proxy ON proxy.enrollment_id=e.enrollment_id
    """)
    op.execute("""
        CREATE VIEW public.slice3_therapist_assignment_read_v1
        WITH (security_barrier=true,security_invoker=false) AS
        SELECT p.assignment_id,p.enrollment_id,a.tenant_public_id,
          p.subject_member_id,p.therapist_id,p.status,p.service_scope_tags,
          p.reason_code,p.service_case_id,p.created_at,p.decided_at,p.version,
          r.id_masked AS subject_masked_label,t.display_name AS therapist_display_name
        FROM public.primary_therapist_assignment p
        JOIN public.institution_application a
          ON a.tenant_internal_id=p.tenant_id AND a.status='APPROVED'
        JOIN public.service_enrollment e ON e.enrollment_id=p.enrollment_id
        JOIN public.member_identity_verification v
          ON v.verification_id=e.current_identity_verification_id
        JOIN public.member_identity_revision r ON r.revision_id=v.current_revision_id
        JOIN public.therapist_profile t ON t.therapist_id=p.therapist_id
    """)
    op.execute("""
        CREATE VIEW public.slice3_service_case_read_v1
        WITH (security_barrier=true,security_invoker=false) AS
        SELECT c.case_id,c.enrollment_id,c.subject_member_id,a.tenant_public_id,
          c.primary_therapist_id,c.assignment_id,c.status,c.service_scope_tags,
          c.created_at,c.version
        FROM public.service_case c
        JOIN public.institution_application a
          ON a.tenant_internal_id=c.tenant_id AND a.status='APPROVED'
    """)
    for name in _VIEWS:
        op.execute(f"REVOKE ALL ON TABLE public.{name} FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.lock_slice3_identity_fingerprint_v1(CHAR) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION public.slice3_readiness_guard_v1(BIGINT) FROM PUBLIC")
    for signature in (
        "public.slice3_digest_algorithm_guard_v1(JSONB)",
        "public.slice3_assignment_candidate_guard_v1(UUID,BIGINT,JSONB)",
        "public.slice3_verified_adult_authority_v1(BIGINT,UUID)",
        "identity.claim_identity_subject_v1(UUID,BIGINT,UUID,CHAR,VARCHAR,VARCHAR,UUID,UUID,UUID,UUID,BIGINT,CHAR,BOOLEAN,BOOLEAN,TIMESTAMPTZ)",
        "public.slice3_reviewer_step_up_budget_v1(UUID,UUID,BIGINT,VARCHAR,VARCHAR,UUID,CHAR,CHAR,CHAR,VARCHAR,BOOLEAN,BIGINT,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,CHAR)",
        "public.slice3_reviewer_pii_v1(UUID,UUID,UUID,BIGINT,UUID,CHAR,CHAR,UUID,VARCHAR,CHAR,CHAR,CHAR,CHAR,TIMESTAMPTZ)",
        "public.slice3_assignment_subject_v1(UUID)",
        "public.slice3_idempotency_replay_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR)",
        "public.slice3_idempotency_record_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,BYTEA,VARCHAR,CHAR,TIMESTAMPTZ)",
        "public.slice3_outbox_recipient_targets_v1(UUID,UUID)",
        "public.slice3_outbox_reopen_v1(UUID,BIGINT,VARCHAR,VARCHAR,BIGINT,VARCHAR,CHAR,JSONB)",
        "public.slice3_delivery_target_digest_internal_v1(JSONB,CHAR)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")


def _grant(role: str, privilege: str, table: str, columns: tuple[str, ...], *, schema: str = "public") -> None:
    rendered = ",".join(f'"{column}"' for column in columns)
    op.execute(f'GRANT {privilege} ({rendered}) ON TABLE {schema}."{table}" TO "{role}"')


def _acl(roles: tuple[str, str, str, str, str]) -> None:
    enrollment, review, case, worker, reader = roles
    for role in roles:
        op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    op.execute(f'GRANT USAGE ON SCHEMA identity TO "{enrollment}","{review}"')
    for table in _TABLES:
        op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC')
    op.execute('REVOKE ALL ON TABLE identity.identity_claim_algorithm_state,identity.identity_subject_claim_registry,public.slice3_digest_algorithm_state FROM PUBLIC')
    # Column-level grants are intentionally explicit; no role receives table-level DML.
    invitation = ("invitation_id","tenant_id","mode","phone_ciphertext","phone_key_id","phone_digest","phone_digest_key_id","phone_masked","code_digest","code_key_id","status","failed_attempts","expires_at","issued_by","issued_at","accepted_at","revoked_at","version")
    enrollment_columns = ("enrollment_id","invitation_id","tenant_id","subject_member_id","proxy_member_id","mode","status","service_scope_tags","current_identity_verification_id","current_assignment_id","service_case_id","accepted_at","identity_verified_at","case_created_at","created_at","updated_at","version")
    bootstrap = ("bootstrap_id","enrollment_id","member_id","member_no","creation_source","created_at","request_digest")
    verification = ("verification_id","enrollment_id","member_id","current_revision_id","status","institution_decision_id","platform_decision_id","submitted_at","institution_checked_at","platform_decided_at","version")
    revision = ("revision_id","verification_id","revision_no","document_type","real_name_ciphertext","real_name_key_id","id_ciphertext","id_key_id","birth_date_ciphertext","birth_date_key_id","id_masked","identity_fingerprint","fingerprint_key_id","input_digest","submitted_by_member_id","created_at")
    revision_safe = ("revision_id","verification_id","revision_no","document_type","id_masked","identity_fingerprint","fingerprint_key_id","input_digest","submitted_by_member_id","created_at")
    decision = ("decision_id","verification_id","revision_id","phase","reviewer_user_id","decision","reason_code","correction_fields","attestation_code","represented_elder_eligible","request_digest","evidence_digest","created_at")
    pii_access = ("access_id","verification_id","current_revision_id","reviewer_user_id","nonce","status","reason_code","access_token_digest","currentness_digest","idempotency_key","request_digest","postimage_digest","issued_at","consumed_at","expires_at","version")
    proxy = ("grant_id","enrollment_id","principal_member_id","proxy_member_id","slot_no","permission_codes","authorization_document_version_id","witness_decision_id","status","valid_from","valid_until","revoked_at","version")
    document = ("document_version_id","document_type","semantic_version","status","requires_reconsent","manifest_digest","effective_at","retired_at","published_by","created_at","version")
    rendition = ("rendition_id","document_version_id","locale","title","body","content_sha256","created_at")
    consent = ("consent_record_id","enrollment_id","subject_member_id","proxy_member_id","document_type","document_version_id","rendition_id","purpose_codes","choice","status","predecessor_id","presented_at","accepted_at","withdrawn_at","version")
    assignment = ("assignment_id","enrollment_id","tenant_id","subject_member_id","therapist_id","status","service_scope_tags","reason_code","service_case_id","created_by","created_at","decided_at","version")
    case_columns = ("case_id","enrollment_id","subject_member_id","tenant_id","primary_therapist_id","assignment_id","status","identity_verification_id","identity_revision_id","consent_set_digest","readiness_evidence_version","readiness_result_digest","service_scope_tags","created_at","updated_at","version")
    audit = ("audit_id","actor_scope","action","object_id","result","reason_code","request_id","preimage_digest","postimage_digest","created_at")
    outbox_insert = ("event_id","event_type","aggregate_id","tenant_id","payload","payload_digest","status","attempts","created_at","version")
    outbox = outbox_insert + ("processing_at","lease_owner","delivered_at","failed_at")
    delivery = ("delivery_id","event_id","recipient_scope","recipient_user_id","recipient_tenant_id","recipient_platform_code","target_digest","target_key_id","payload","payload_digest","created_at")
    for table, columns in (("member_service_invitation",invitation),("service_enrollment",enrollment_columns),("proxy_grant",proxy),("consent_record",consent),("primary_therapist_assignment",assignment)):
        _grant(enrollment,"SELECT",table,columns); _grant(enrollment,"INSERT",table,columns)
    _grant(enrollment,"UPDATE","member_service_invitation",("status","failed_attempts","accepted_at","revoked_at","version"))
    _grant(enrollment,"UPDATE","service_enrollment",("subject_member_id","proxy_member_id","status","current_identity_verification_id","current_assignment_id","accepted_at","identity_verified_at","updated_at","version"))
    _grant(enrollment,"UPDATE","proxy_grant",("authorization_document_version_id","status","valid_from","valid_until","revoked_at","version"))
    _grant(enrollment,"UPDATE","consent_record",("status","withdrawn_at","version"))
    _grant(enrollment,"UPDATE","primary_therapist_assignment",("status","reason_code","decided_at","version"))
    for table, columns in (("controlled_member_bootstrap",bootstrap),("member_identity_revision",revision),("member_identity_review_decision",decision),("member_enrollment_audit",audit),("member_enrollment_outbox",outbox_insert)):
        _grant(enrollment,"INSERT",table,columns)
    _grant(enrollment,"SELECT","member_identity_review_decision",decision)
    _grant(enrollment,"SELECT","consent_document_version",document)
    _grant(enrollment,"SELECT","consent_document_rendition",rendition)
    _grant(enrollment,"SELECT","member",("member_id","member_no","creation_source","status","version"),schema="identity")
    _grant(enrollment,"INSERT","member",("member_id","member_no","creation_source","status","version","created_at","updated_at"),schema="identity")
    _grant(enrollment,"SELECT","member_identity_verification",verification); _grant(enrollment,"INSERT","member_identity_verification",verification)
    _grant(enrollment,"UPDATE","member_identity_verification",("current_revision_id","status","institution_decision_id","submitted_at","institution_checked_at","version"))
    _grant(review,"SELECT","member_identity_verification",verification)
    _grant(review,"UPDATE","member_identity_verification",("status","platform_decision_id","platform_decided_at","version"))
    _grant(review,"SELECT","member_identity_revision",revision_safe)
    _grant(review,"SELECT","member_identity_review_decision",decision); _grant(review,"INSERT","member_identity_review_decision",decision)
    _grant(review,"SELECT","service_enrollment",("enrollment_id","status","version"))
    _grant(review,"UPDATE","service_enrollment",("status","identity_verified_at","updated_at","version"))
    _grant(review,"SELECT","member_identity_pii_access",pii_access); _grant(review,"INSERT","member_identity_pii_access",pii_access); _grant(review,"UPDATE","member_identity_pii_access",("status","consumed_at","postimage_digest","version"))
    _grant(review,"SELECT","proxy_grant",proxy); _grant(review,"UPDATE","proxy_grant",("witness_decision_id","version"))
    op.execute(f'GRANT SELECT ON TABLE public.slice3_platform_identity_review_read_v1 TO "{review}"')
    _grant(review,"SELECT","consent_document_version",document); _grant(review,"INSERT","consent_document_version",document); _grant(review,"UPDATE","consent_document_version",("status","effective_at","retired_at","published_by","version"))
    _grant(review,"SELECT","consent_document_rendition",rendition); _grant(review,"INSERT","consent_document_rendition",rendition)
    _grant(case,"SELECT","service_enrollment",("enrollment_id","tenant_id","subject_member_id","proxy_member_id","mode","status","service_scope_tags","current_identity_verification_id","current_assignment_id","service_case_id","version")); _grant(case,"UPDATE","service_enrollment",("status","current_assignment_id","service_case_id","case_created_at","updated_at","version"))
    _grant(case,"SELECT","primary_therapist_assignment",assignment); _grant(case,"UPDATE","primary_therapist_assignment",("status","reason_code","service_case_id","decided_at","version"))
    _grant(case,"SELECT","service_case",case_columns); _grant(case,"INSERT","service_case",case_columns)
    _grant(case,"SELECT","member_identity_verification",verification)
    _grant(case,"SELECT","consent_record",consent)
    _grant(case,"SELECT","consent_document_version",document)
    _grant(case,"SELECT","consent_document_rendition",rendition)
    _grant(case,"SELECT","therapist_profile",("therapist_id","tenant_id","status","service_tags","capacity_limit","active_case_count","current_qualification_version_id","qualification_valid_until","version"))
    _grant(case,"UPDATE","therapist_profile",("active_case_count","updated_at","version"))
    for role in (review,case):
        _grant(role,"INSERT","member_enrollment_audit",audit); _grant(role,"INSERT","member_enrollment_outbox",outbox_insert)
    _grant(worker,"SELECT","member_enrollment_outbox",outbox); _grant(worker,"UPDATE","member_enrollment_outbox",("status","attempts","processing_at","lease_owner","delivered_at","failed_at","version"))
    _grant(worker,"SELECT","member_enrollment_delivery",delivery); _grant(worker,"INSERT","member_enrollment_delivery",delivery)
    for view in (
        "slice3_institution_enrollment_read_v1",
        "slice3_family_enrollment_read_v1",
        "slice3_therapist_assignment_read_v1",
        "slice3_service_case_read_v1",
    ):
        op.execute(f'GRANT SELECT ON TABLE public.{view} TO "{reader}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.lock_slice3_identity_fingerprint_v1(CHAR) TO "{enrollment}","{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_readiness_guard_v1(BIGINT) TO "{enrollment}","{case}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_assignment_candidate_guard_v1(UUID,BIGINT,JSONB) TO "{enrollment}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_collection_snapshot_v1(VARCHAR,JSONB) TO "{enrollment}","{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_verified_adult_authority_v1(BIGINT,UUID) TO "{enrollment}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION identity.claim_identity_subject_v1(UUID,BIGINT,UUID,CHAR,VARCHAR,VARCHAR,UUID,UUID,UUID,UUID,BIGINT,CHAR,BOOLEAN,BOOLEAN,TIMESTAMPTZ) TO "{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_reviewer_step_up_budget_v1(UUID,UUID,BIGINT,VARCHAR,VARCHAR,UUID,CHAR,CHAR,CHAR,VARCHAR,BOOLEAN,BIGINT,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,CHAR) TO "{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_reviewer_pii_v1(UUID,UUID,UUID,BIGINT,UUID,CHAR,CHAR,UUID,VARCHAR,CHAR,CHAR,CHAR,CHAR,TIMESTAMPTZ) TO "{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_assignment_subject_v1(UUID) TO "{case}"')
    for role in (enrollment, review, case):
        op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_idempotency_replay_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR),public.slice3_idempotency_record_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,BYTEA,VARCHAR,CHAR,TIMESTAMPTZ) TO "{role}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_outbox_recipient_targets_v1(UUID,UUID) TO "{worker}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_outbox_reopen_v1(UUID,BIGINT,VARCHAR,VARCHAR,BIGINT,VARCHAR,CHAR,JSONB) TO "{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_digest_algorithm_guard_v1(JSONB) TO "{enrollment}","{review}","{case}","{worker}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_enrollment_mutation_confirm_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) TO "{enrollment}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_enrollment_mutation_expected_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) TO "{enrollment}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_identity_review_mutation_confirm_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) TO "{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_identity_review_mutation_expected_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) TO "{review}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_case_mutation_confirm_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) TO "{case}"')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.slice3_case_mutation_expected_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB) TO "{case}"')
    op.execute(f'GRANT USAGE,SELECT ON SEQUENCE public.member_enrollment_audit_audit_id_seq TO "{enrollment}","{review}","{case}"')
    op.execute(f'REVOKE ALL ON SEQUENCE public.member_enrollment_audit_audit_id_seq FROM "{worker}","{reader}",PUBLIC')


def upgrade() -> None:
    roles = _roles()
    op.get_bind().execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    _create_tables()
    _bootstrap_algorithm_state()
    _safe_interfaces(roles)
    _acl(roles)


def downgrade() -> None:
    roles = _roles()
    connection = op.get_bind()
    connection.execute(sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LOCK_KEY})
    for table in _TABLES:
        connection.execute(sa.text(f'LOCK TABLE public."{table}" IN ACCESS EXCLUSIVE MODE'))
        if connection.execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM public."{table}")')).scalar_one():
            raise RuntimeError("Slice 3 downgrade requires empty module tables") from None
    for table in ("identity_subject_claim_registry", "identity_claim_algorithm_state"):
        connection.execute(sa.text(f'LOCK TABLE identity."{table}" IN ACCESS EXCLUSIVE MODE'))
    if connection.execute(sa.text(
        "SELECT EXISTS(SELECT 1 FROM identity.identity_subject_claim_registry "
        "WHERE source_kind<>'P1')"
    )).scalar_one():
        raise RuntimeError("Slice 3 downgrade requires empty module tables") from None
    connection.execute(
        sa.text(
            'LOCK TABLE public."slice3_digest_algorithm_state" '
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    connection.execute(sa.text(
        "DELETE FROM identity.identity_subject_claim_registry WHERE source_kind='P1'"
    ))
    rendered = ",".join(f'"{role}"' for role in roles)
    for role in roles:
        op.execute(f'REVOKE USAGE ON SCHEMA public,identity FROM "{role}"')
    for table in _TABLES:
        op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public."{table}" FROM PUBLIC,{rendered}')
    for view in _VIEWS:
        op.execute(f'REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM PUBLIC,{rendered}')
        op.execute(f'DROP VIEW public.{view}')
    op.execute(
        "DROP TRIGGER trg_slice3_p1_identity_claim_registry "
        "ON public.identity_verification_submission"
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION identity.sync_p1_identity_claim_registry_v1() "
        f"FROM PUBLIC,{rendered}"
    )
    op.execute("DROP FUNCTION identity.sync_p1_identity_claim_registry_v1()")
    for signature in (
        "public.slice3_case_mutation_expected_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)",
        "public.slice3_collection_snapshot_v1(VARCHAR,JSONB)",
        "public.slice3_case_mutation_confirm_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)",
        "public.slice3_identity_review_mutation_expected_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)",
        "public.slice3_identity_review_mutation_confirm_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)",
        "public.slice3_enrollment_mutation_expected_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)",
        "public.slice3_enrollment_mutation_confirm_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,JSONB)",
        "public.slice3_outbox_recipient_targets_v1(UUID,UUID)",
        "public.slice3_outbox_reopen_v1(UUID,BIGINT,VARCHAR,VARCHAR,BIGINT,VARCHAR,CHAR,JSONB)",
        "public.slice3_delivery_target_digest_internal_v1(JSONB,CHAR)",
        "public.slice3_idempotency_record_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR,BYTEA,VARCHAR,CHAR,TIMESTAMPTZ)",
        "public.slice3_idempotency_replay_v1(VARCHAR,VARCHAR,UUID,VARCHAR,CHAR)",
        "public.slice3_assignment_subject_v1(UUID)",
        "public.slice3_assignment_candidate_guard_v1(UUID,BIGINT,JSONB)",
        "public.slice3_reviewer_step_up_budget_v1(UUID,UUID,BIGINT,VARCHAR,VARCHAR,UUID,CHAR,CHAR,CHAR,VARCHAR,BOOLEAN,BIGINT,TIMESTAMPTZ,TIMESTAMPTZ,TIMESTAMPTZ,CHAR)",
        "public.slice3_reviewer_pii_v1(UUID,UUID,UUID,BIGINT,UUID,CHAR,CHAR,UUID,VARCHAR,CHAR,CHAR,CHAR,CHAR,TIMESTAMPTZ)",
        "identity.claim_identity_subject_v1(UUID,BIGINT,UUID,CHAR,VARCHAR,VARCHAR,UUID,UUID,UUID,UUID,BIGINT,CHAR,BOOLEAN,BOOLEAN,TIMESTAMPTZ)",
        "public.slice3_verified_adult_authority_v1(BIGINT,UUID)",
        "public.slice3_digest_algorithm_guard_v1(JSONB)",
    ):
        op.execute(f'REVOKE ALL ON FUNCTION {signature} FROM PUBLIC,{rendered}')
        op.execute(f'DROP FUNCTION {signature}')
    op.execute(f'REVOKE ALL ON FUNCTION public.slice3_readiness_guard_v1(BIGINT) FROM PUBLIC,{rendered}')
    op.execute(f'REVOKE ALL ON FUNCTION public.lock_slice3_identity_fingerprint_v1(CHAR) FROM PUBLIC,{rendered}')
    op.execute("DROP FUNCTION public.slice3_readiness_guard_v1(BIGINT)")
    op.execute("DROP FUNCTION public.lock_slice3_identity_fingerprint_v1(CHAR)")
    op.execute(
        'REVOKE ALL PRIVILEGES ON TABLE '
        'identity.identity_subject_claim_registry,'
        'identity.identity_claim_algorithm_state,'
        f'public.slice3_digest_algorithm_state FROM PUBLIC,{rendered}'
    )
    op.drop_table("identity_subject_claim_registry", schema="identity")
    op.drop_table("identity_claim_algorithm_state", schema="identity")
    op.drop_table("slice3_digest_algorithm_state", schema="public")
    op.execute(
        f"REVOKE ALL ON FUNCTION public.slice3_immutable_algorithm_state_v1() "
        f"FROM PUBLIC,{rendered}"
    )
    op.execute("DROP FUNCTION public.slice3_immutable_algorithm_state_v1()")
    for table, name in (
        ("service_enrollment","fk_service_enrollment_current_identity_scope"),
        ("service_enrollment","fk_service_enrollment_current_assignment_scope"),
        ("service_enrollment","fk_service_enrollment_service_case_scope"),
        ("member_identity_verification","fk_member_identity_verification_institution_decision_scope"),
        ("member_identity_verification","fk_member_identity_verification_platform_decision_scope"),
        ("member_identity_review_decision","fk_member_identity_review_decision_revision_scope"),
        ("proxy_grant","fk_proxy_grant_principal_scope"),
        ("proxy_grant","fk_proxy_grant_proxy_scope"),
        ("proxy_grant","fk_proxy_grant_authorization_document_version"),
        ("consent_record","fk_consent_record_subject_scope"),
        ("consent_record","fk_consent_record_proxy_scope"),
        ("consent_record","fk_consent_record_rendition_scope"),
        ("primary_therapist_assignment","fk_primary_assignment_enrollment_scope"),
        ("primary_therapist_assignment","fk_primary_assignment_service_case_scope"),
        ("service_case","fk_service_case_enrollment_scope"),
        ("service_case","fk_service_case_identity_revision_scope"),
        ("service_case","fk_service_case_current_inputs_scope"),
        ("service_case","fk_service_case_assignment_scope"),
    ):
        op.drop_constraint(name,table,schema="public",type_="foreignkey")
    op.drop_constraint(
        "fk_member_identity_verification_current_revision",
        "member_identity_verification",
        schema="public",
        type_="foreignkey",
    )
    for table in reversed(_TABLES):
        op.drop_table(table, schema="public")
