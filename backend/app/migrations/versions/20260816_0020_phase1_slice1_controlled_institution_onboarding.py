"""Phase 1 Slice 1 controlled institution onboarding.

Revision ID: 20260816_0020
Revises: 20260815_0019
"""
from __future__ import annotations

import os
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url


revision = "20260816_0020"
down_revision = "20260815_0019"
branch_labels = None
depends_on = None

_ROLE_VARS = (
    "KG_INSTITUTION_ONBOARDING_WRITER_ROLE",
    "KG_INSTITUTION_REVIEW_WRITER_ROLE",
    "KG_PRIVATE_FILE_WRITER_ROLE",
    "KG_INSTITUTION_ONBOARDING_READER_ROLE",
)
_RUNTIME_IDENTITIES = (
    ("KG_DATABASE_USER", "KG_IDENTITY_APPLICATION_DATABASE_URL"),
    ("KG_READONLY_ROLE", "KG_READONLY_DATABASE_URL"),
    ("KG_VERIFICATION_WRITER_ROLE", "KG_VERIFICATION_WRITER_DATABASE_URL"),
    ("KG_DELIVERY_WORKER_ROLE", "KG_DELIVERY_WORKER_DATABASE_URL"),
    ("KG_OUTBOX_AUDIT_ROLE", "KG_OUTBOX_AUDIT_DATABASE_URL"),
    ("KG_HEALTH_FACT_WRITER_ROLE", "KG_HEALTH_FACT_WRITER_DATABASE_URL"),
    ("KG_ORGANIZATION_MAPPING_WRITER_ROLE", "KG_ORGANIZATION_MAPPING_WRITER_DATABASE_URL"),
    ("KG_HEALTH_MAPPING_WRITER_ROLE", "KG_HEALTH_MAPPING_WRITER_DATABASE_URL"),
    ("KG_MAPPING_AUDIT_ROLE", "KG_MAPPING_AUDIT_DATABASE_URL"),
    ("KG_MAPPING_SHADOW_ROLE", "KG_MAPPING_SHADOW_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_BUILDER_ROLE", "KG_ORGANIZATION_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_BUILDER_ROLE", "KG_HEALTH_PROJECTION_BUILDER_DATABASE_URL"),
    ("KG_PROJECTION_CONFIRMATION_ROLE", "KG_PROJECTION_CONFIRMATION_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_SHADOW_ROLE", "KG_ORGANIZATION_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_SHADOW_ROLE", "KG_HEALTH_PROJECTION_SHADOW_DATABASE_URL"),
    ("KG_PROJECTION_READY_GATE_ROLE", "KG_PROJECTION_READY_GATE_DATABASE_URL"),
    ("KG_PROJECTION_SHADOW_CONFIRMATION_ROLE", "KG_PROJECTION_SHADOW_CONFIRMATION_DATABASE_URL"),
    ("KG_ORGANIZATION_PROJECTION_READER_ROLE", "KG_ORGANIZATION_PROJECTION_READER_DATABASE_URL"),
    ("KG_HEALTH_PROJECTION_READER_ROLE", "KG_HEALTH_PROJECTION_READER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_WRITER_ROLE", "KG_INSTITUTION_ONBOARDING_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_REVIEW_WRITER_ROLE", "KG_INSTITUTION_REVIEW_WRITER_DATABASE_URL"),
    ("KG_PRIVATE_FILE_WRITER_ROLE", "KG_PRIVATE_FILE_WRITER_DATABASE_URL"),
    ("KG_INSTITUTION_ONBOARDING_READER_ROLE", "KG_INSTITUTION_ONBOARDING_READER_DATABASE_URL"),
)


def _configuration_error() -> None:
    raise RuntimeError("Slice 1 database role configuration is invalid") from None


def _slice_role_membership_is_unsafe(connection, slice_roles: tuple[str, ...]) -> bool:
    role_oids = dict(
        connection.execute(
            sa.text("SELECT rolname, oid FROM pg_roles WHERE rolname = ANY(:roles)"),
            {"roles": sorted(slice_roles)},
        ).all()
    )
    if set(role_oids) != set(slice_roles):
        _configuration_error()
    slice_oids = list(role_oids.values())
    return bool(
        connection.execute(
            sa.text(
                "WITH RECURSIVE role_paths(source_oid,target_oid,path) AS ("
                "SELECT member,roleid,ARRAY[member,roleid] FROM pg_auth_members "
                "UNION ALL SELECT role_paths.source_oid,membership.roleid,"
                "role_paths.path||membership.roleid FROM role_paths "
                "JOIN pg_auth_members AS membership "
                "ON membership.member=role_paths.target_oid "
                "WHERE NOT membership.roleid=ANY(role_paths.path)) "
                "SELECT EXISTS (SELECT 1 FROM role_paths WHERE "
                "source_oid=ANY(:slice_roles) OR target_oid=ANY(:slice_roles))"
            ),
            {"slice_roles": slice_oids},
        ).scalar_one()
    )


def _roles() -> tuple[str, str, str, str]:
    configured: dict[str, str] = {}
    for role_var, url_var in _RUNTIME_IDENTITIES:
        role = os.getenv(role_var, "").strip()
        raw_url = os.getenv(url_var, "").strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", role) or not raw_url:
            _configuration_error()
        try:
            url_role = make_url(raw_url).username
        except Exception:
            _configuration_error()
        if url_role != role:
            _configuration_error()
        configured[role_var] = role
    if len(set(configured.values())) != len(configured):
        _configuration_error()
    values = tuple(configured[name] for name in _ROLE_VARS)
    connection = op.get_bind()
    current_user = str(
        connection.execute(sa.text("SELECT current_user")).scalar_one()
    )
    if current_user in configured.values():
        _configuration_error()
    if _slice_role_membership_is_unsafe(connection, values):
        _configuration_error()
    rows = connection.execute(
        sa.text(
            "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolinherit, "
            "rolreplication, rolbypassrls FROM pg_roles WHERE rolname = ANY(:roles)"
        ),
        {"roles": sorted(configured.values())},
    ).mappings().all()
    if len(rows) != len(configured) or any(
        row["rolsuper"]
        or row["rolcreaterole"]
        or row["rolcreatedb"]
        or row["rolinherit"]
        or row["rolreplication"]
        or row["rolbypassrls"]
        for row in rows
    ):
        _configuration_error()
    return values  # type: ignore[return-value]


def _uuid(name: str, *, primary_key: bool = False, nullable: bool = False):
    return sa.Column(name, postgresql.UUID(as_uuid=False), primary_key=primary_key, nullable=nullable)


def _grant_columns(connection, role: str, privilege: str, table: str, columns: tuple[str, ...]) -> None:
    rendered = ", ".join(f'"{column}"' for column in columns)
    connection.execute(sa.text(f'GRANT {privilege} ({rendered}) ON TABLE public."{table}" TO "{role}"'))


def _revoke_columns(connection, role: str, privilege: str, table: str, columns: tuple[str, ...]) -> None:
    rendered = ", ".join(f'"{column}"' for column in columns)
    connection.execute(sa.text(f'REVOKE {privilege} ({rendered}) ON TABLE public."{table}" FROM "{role}"'))


def upgrade() -> None:
    onboarding, reviewer, file_writer, reader = _roles()
    op.create_table("institution_invitation",
        _uuid("invitation_id", primary_key=True), sa.Column("institution_name", sa.String(100), nullable=False), sa.Column("institution_type", sa.String(32), nullable=False), sa.Column("applicant_phone_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("applicant_phone_digest", sa.String(64), nullable=False), sa.Column("pilot_batch_code", sa.String(32), nullable=False), sa.Column("administrative_region_id", sa.BigInteger(), nullable=False), sa.Column("code_digest", sa.String(64), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("issued_by", sa.BigInteger(), nullable=False), sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False), sa.Column("activated_at", sa.DateTime(timezone=True)), sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"),
        sa.CheckConstraint("institution_type IN ('HEALTH_STORE','LICENSED_CLINIC')", name="ck_institution_invitation_institution_type"), sa.CheckConstraint("status IN ('ISSUED','ACTIVATED','REVOKED') AND failed_attempts BETWEEN 0 AND 5 AND version>=1", name="ck_institution_invitation_state"), schema="public")
    op.create_table("institution_onboarding_account", sa.Column("user_id", sa.BigInteger(), primary_key=True), _uuid("invitation_id"), sa.Column("totp_secret_ciphertext", sa.LargeBinary(), nullable=False), sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("invitation_id", name="uq_institution_onboarding_account_invitation_id"), schema="public")
    op.create_table("institution_application", _uuid("application_id", primary_key=True), _uuid("invitation_id"), sa.Column("applicant_user_id", sa.BigInteger(), nullable=False), sa.Column("institution_type", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("draft_payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")), sa.Column("correction_fields", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")), sa.Column("correction_reason_code", sa.String(64)), sa.Column("current_revision_no", sa.Integer(), nullable=False, server_default="0"), sa.Column("tenant_internal_id", sa.BigInteger()), _uuid("tenant_public_id", nullable=True), sa.Column("service_ready", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("submitted_at", sa.DateTime(timezone=True)), sa.Column("reviewed_at", sa.DateTime(timezone=True)), sa.Column("version", sa.BigInteger(), nullable=False, server_default="1"), sa.UniqueConstraint("invitation_id", name="uq_institution_application_invitation"), sa.UniqueConstraint("tenant_public_id", name="uq_institution_application_tenant_public_id"), sa.CheckConstraint("institution_type IN ('HEALTH_STORE','LICENSED_CLINIC')", name="ck_institution_application_institution_type"), sa.CheckConstraint("status IN ('DRAFT','SUBMITTED','UNDER_REVIEW','NEEDS_CORRECTION','APPROVED','REJECTED') AND version>=1 AND service_ready = false", name="ck_institution_application_state"), schema="public")
    op.create_table("institution_application_revision", _uuid("revision_id", primary_key=True), _uuid("application_id"), sa.Column("revision_no", sa.Integer(), nullable=False), sa.Column("snapshot", postgresql.JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("application_id", "revision_no", name="uq_institution_application_revision_no"), schema="public")
    op.create_table("institution_license", _uuid("license_id", primary_key=True), _uuid("application_id"), sa.Column("license_type", sa.String(48), nullable=False), sa.Column("license_no_ciphertext", sa.LargeBinary()), sa.Column("license_no_digest", sa.String(64)), _uuid("private_file_id"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("application_id", "license_type", name="uq_institution_license_application_type"), sa.CheckConstraint("license_type IN ('BUSINESS_LICENSE','MEDICAL_INSTITUTION_LICENSE')", name="ck_institution_license_license_type"), schema="public")
    op.create_table("private_file", _uuid("file_id", primary_key=True), sa.Column("purpose", sa.String(48), nullable=False), sa.Column("owner_user_id", sa.BigInteger(), nullable=False), sa.Column("declared_size", sa.Integer(), nullable=False), sa.Column("declared_mime_type", sa.String(64), nullable=False), sa.Column("declared_sha256", sa.String(64), nullable=False), sa.Column("actual_size", sa.Integer()), sa.Column("actual_mime_type", sa.String(64)), sa.Column("actual_sha256", sa.String(64)), sa.Column("object_key", sa.String(255), nullable=False), sa.Column("status", sa.String(24), nullable=False), _uuid("bound_application_id", nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False), sa.Column("scanned_at", sa.DateTime(timezone=True)), sa.Column("bound_at", sa.DateTime(timezone=True)), sa.Column("deleted_at", sa.DateTime(timezone=True)), sa.UniqueConstraint("object_key", name="uq_private_file_object_key"), sa.CheckConstraint("declared_size BETWEEN 1 AND 10485760 AND (actual_size IS NULL OR actual_size BETWEEN 1 AND 10485760)", name="ck_private_file_size"), sa.CheckConstraint("declared_mime_type IN ('application/pdf','image/jpeg','image/png')", name="ck_private_file_mime_type"), sa.CheckConstraint("status IN ('UPLOAD_INITIATED','PENDING_SCAN','CLEAN','REJECTED','SCAN_FAILED','DELETED')", name="ck_private_file_status"), schema="public")
    op.create_table("institution_onboarding_idempotency", sa.Column("actor_scope", sa.String(80), primary_key=True), sa.Column("operation", sa.String(48), primary_key=True), sa.Column("idempotency_key", sa.String(128), primary_key=True), sa.Column("request_digest", sa.String(64), nullable=False), sa.Column("response_payload", postgresql.JSONB(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), schema="public")
    op.create_table("institution_onboarding_audit", sa.Column("audit_id", sa.BigInteger(), primary_key=True, autoincrement=True), sa.Column("actor_user_id", sa.BigInteger()), sa.Column("actor_role", sa.String(32), nullable=False), sa.Column("action", sa.String(64), nullable=False), sa.Column("object_type", sa.String(48), nullable=False), _uuid("object_id"), sa.Column("result", sa.String(16), nullable=False), sa.Column("reason_code", sa.String(64)), sa.Column("request_id", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), schema="public")
    op.create_table("institution_onboarding_outbox", _uuid("event_id", primary_key=True), sa.Column("event_type", sa.String(64), nullable=False), _uuid("aggregate_id"), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("status", sa.String(16), nullable=False), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("processing_at", sa.DateTime(timezone=True)), sa.Column("delivered_at", sa.DateTime(timezone=True)), sa.CheckConstraint("attempts BETWEEN 0 AND 3 AND ((status='PENDING' AND processing_at IS NULL AND delivered_at IS NULL) OR (status='PROCESSING' AND processing_at IS NOT NULL AND delivered_at IS NULL) OR (status='DELIVERED' AND processing_at IS NOT NULL AND delivered_at IS NOT NULL) OR (status='FAILED' AND processing_at IS NULL AND delivered_at IS NULL))", name="ck_institution_onboarding_outbox_status"), schema="public")
    op.create_table("institution_onboarding_delivery", _uuid("event_id", primary_key=True), sa.Column("event_type", sa.String(64), nullable=False), sa.Column("recipient_user_id", sa.BigInteger(), nullable=False), sa.Column("recipient_scope", sa.String(32), nullable=False), sa.Column("payload", postgresql.JSONB(), nullable=False), sa.Column("payload_digest", sa.String(64), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint("event_type = 'INSTITUTION_APPROVED' AND recipient_scope = 'INSTITUTION_ADMIN'", name="ck_institution_onboarding_delivery_contract"), schema="public")

    op.create_foreign_key("fk_institution_account_user", "institution_onboarding_account", "user", ["user_id"], ["id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_account_invitation", "institution_onboarding_account", "institution_invitation", ["invitation_id"], ["invitation_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_invitation_administrative_region", "institution_invitation", "platform_org", ["administrative_region_id"], ["id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_application_invitation", "institution_application", "institution_invitation", ["invitation_id"], ["invitation_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_application_user", "institution_application", "user", ["applicant_user_id"], ["id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_application_tenant", "institution_application", "tenant", ["tenant_internal_id"], ["id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_revision_application", "institution_application_revision", "institution_application", ["application_id"], ["application_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_license_application", "institution_license", "institution_application", ["application_id"], ["application_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_institution_license_private_file", "institution_license", "private_file", ["private_file_id"], ["file_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_private_file_owner_user", "private_file", "user", ["owner_user_id"], ["id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_private_file_bound_application", "private_file", "institution_application", ["bound_application_id"], ["application_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_onboarding_outbox_application", "institution_onboarding_outbox", "institution_application", ["aggregate_id"], ["application_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_onboarding_delivery_outbox", "institution_onboarding_delivery", "institution_onboarding_outbox", ["event_id"], ["event_id"], source_schema="public", referent_schema="public")
    op.create_foreign_key("fk_onboarding_delivery_user", "institution_onboarding_delivery", "user", ["recipient_user_id"], ["id"], source_schema="public", referent_schema="public")

    connection = op.get_bind()
    all_roles = (onboarding, reviewer, file_writer, reader)
    for role in all_roles:
        connection.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        connection.execute(sa.text(f'REVOKE CREATE ON SCHEMA public FROM "{role}"'))
        connection.execute(sa.text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"'))
        connection.execute(sa.text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{role}"'))
    invitation_columns = ("invitation_id","institution_name","institution_type","applicant_phone_ciphertext","applicant_phone_digest","pilot_batch_code","administrative_region_id","code_digest","status","failed_attempts","expires_at","issued_by","issued_at","activated_at","version")
    application_columns = ("application_id","invitation_id","applicant_user_id","institution_type","status","draft_payload","correction_fields","correction_reason_code","current_revision_no","tenant_internal_id","tenant_public_id","service_ready","created_at","updated_at","submitted_at","reviewed_at","version")
    revision_columns = ("revision_id","application_id","revision_no","snapshot","created_at")
    license_columns = ("license_id","application_id","license_type","license_no_ciphertext","license_no_digest","private_file_id","created_at")
    idempotency_columns = ("actor_scope","operation","idempotency_key","request_digest","response_payload","created_at")
    audit_insert_columns = ("actor_user_id","actor_role","action","object_type","object_id","result","reason_code","request_id","created_at")
    outbox_insert_columns = ("event_id","event_type","aggregate_id","payload","status","attempts","created_at","processing_at","delivered_at")
    delivery_columns = ("event_id","event_type","recipient_user_id","recipient_scope","payload","payload_digest","created_at")
    file_columns = ("file_id","purpose","owner_user_id","declared_size","declared_mime_type","declared_sha256","actual_size","actual_mime_type","actual_sha256","object_key","status","bound_application_id","created_at","expires_at","scanned_at","bound_at","deleted_at")
    file_binding_columns = ("file_id","purpose","owner_user_id","status","bound_application_id","bound_at")
    file_review_columns = ("file_id","purpose","status","bound_application_id","scanned_at")
    invitation_review_columns = ("invitation_id","institution_name","institution_type","administrative_region_id")
    for table, columns in (("institution_invitation", invitation_columns), ("institution_application", application_columns), ("institution_application_revision", revision_columns), ("institution_license", license_columns), ("institution_onboarding_idempotency", idempotency_columns)):
        _grant_columns(connection, onboarding, "SELECT", table, columns)
    for table, columns in (("institution_invitation", invitation_columns), ("institution_onboarding_account", ("user_id","invitation_id","totp_secret_ciphertext","totp_enabled","activated_at")), ("institution_application", application_columns), ("institution_application_revision", revision_columns), ("institution_license", license_columns), ("institution_onboarding_idempotency", idempotency_columns), ("institution_onboarding_audit", audit_insert_columns), ("institution_onboarding_outbox", outbox_insert_columns)):
        _grant_columns(connection, onboarding, "INSERT", table, columns)
    _grant_columns(connection, onboarding, "UPDATE", "institution_invitation", ("status","failed_attempts","expires_at","code_digest","activated_at","version"))
    _grant_columns(connection, onboarding, "UPDATE", "institution_application", ("status","draft_payload","correction_fields","correction_reason_code","current_revision_no","updated_at","submitted_at","version"))
    _grant_columns(connection, onboarding, "SELECT", "private_file", file_binding_columns)
    _grant_columns(connection, onboarding, "UPDATE", "private_file", ("bound_application_id","bound_at"))
    _grant_columns(connection, onboarding, "UPDATE", "institution_license", ("license_no_ciphertext","license_no_digest","private_file_id"))
    _grant_columns(connection, onboarding, "SELECT", "platform_org", ("id","org_type","status"))
    _grant_columns(connection, onboarding, "INSERT", "user", ("phone","password_hash","role","status","tenant_id"))
    _grant_columns(connection, onboarding, "SELECT", "user", ("id",))

    for table, columns in (("institution_application", application_columns), ("institution_application_revision", revision_columns), ("institution_license", license_columns), ("institution_onboarding_idempotency", idempotency_columns)):
        _grant_columns(connection, reviewer, "SELECT", table, columns)
    _grant_columns(connection, reviewer, "SELECT", "institution_invitation", invitation_review_columns)
    _grant_columns(connection, reviewer, "SELECT", "private_file", file_review_columns)
    _grant_columns(connection, reviewer, "UPDATE", "institution_application", ("status","correction_fields","correction_reason_code","tenant_internal_id","tenant_public_id","updated_at","reviewed_at","version"))
    for table, columns in (("institution_onboarding_idempotency", idempotency_columns), ("institution_onboarding_audit", audit_insert_columns), ("institution_onboarding_outbox", outbox_insert_columns)):
        _grant_columns(connection, reviewer, "INSERT", table, columns)
    _grant_columns(connection, reviewer, "SELECT", "institution_onboarding_outbox", outbox_insert_columns)
    _grant_columns(connection, reviewer, "UPDATE", "institution_onboarding_outbox", ("status","attempts","processing_at","delivered_at"))
    _grant_columns(connection, reviewer, "SELECT", "institution_onboarding_delivery", delivery_columns)
    _grant_columns(connection, reviewer, "INSERT", "institution_onboarding_delivery", delivery_columns)
    _grant_columns(connection, reviewer, "SELECT", "user", ("id","tenant_id")); _grant_columns(connection, reviewer, "UPDATE", "user", ("tenant_id",))
    tenant_insert = ("org_id","tenant_code","name","type","credit_code","legal_person_name","province","city","district","address","contact_name","contact_phone","contact_email","status","reviewed_by","reviewed_at","approved_at")
    _grant_columns(connection, reviewer, "INSERT", "tenant", tenant_insert); _grant_columns(connection, reviewer, "SELECT", "tenant", ("id",))

    for privilege in ("SELECT","INSERT","UPDATE"):
        _grant_columns(connection, file_writer, privilege, "private_file", file_columns)
    _grant_columns(connection, reader, "SELECT", "institution_invitation", ("invitation_id","institution_name","institution_type","pilot_batch_code","administrative_region_id","status","expires_at","issued_at","activated_at","version"))
    _grant_columns(connection, reader, "SELECT", "institution_application", application_columns)
    _grant_columns(connection, reader, "SELECT", "institution_application_revision", revision_columns)
    _grant_columns(connection, reader, "SELECT", "institution_license", ("license_id","application_id","license_type","private_file_id","created_at"))
    _grant_columns(connection, reader, "SELECT", "private_file", ("file_id","purpose","owner_user_id","declared_size","declared_mime_type","actual_size","actual_sha256","status","bound_application_id","created_at","expires_at","scanned_at","bound_at"))
    _grant_columns(connection, reader, "SELECT", "institution_onboarding_account", ("user_id","invitation_id","totp_secret_ciphertext","totp_enabled","activated_at"))
    connection.execute(sa.text(f'GRANT USAGE, SELECT ON SEQUENCE public.institution_onboarding_audit_audit_id_seq TO "{onboarding}"'))
    connection.execute(sa.text(f'GRANT USAGE, SELECT ON SEQUENCE public.institution_onboarding_audit_audit_id_seq TO "{reviewer}"'))
    connection.execute(sa.text(f'GRANT USAGE, SELECT ON SEQUENCE public.user_id_seq TO "{onboarding}"'))
    connection.execute(sa.text(f'GRANT USAGE, SELECT ON SEQUENCE public.tenant_id_seq TO "{reviewer}"'))
    for protected_role in (os.environ["KG_DATABASE_USER"], os.environ["KG_READONLY_ROLE"]):
        for table in ("institution_invitation","institution_onboarding_account","institution_application","institution_application_revision","institution_license","private_file","institution_onboarding_idempotency","institution_onboarding_audit","institution_onboarding_outbox","institution_onboarding_delivery"):
            connection.execute(sa.text(f'REVOKE ALL ON TABLE public."{table}" FROM "{protected_role}"'))
        connection.execute(sa.text(f'REVOKE ALL ON SEQUENCE public.institution_onboarding_audit_audit_id_seq FROM "{protected_role}"'))


def downgrade() -> None:
    roles = _roles(); onboarding, reviewer, file_writer, reader = roles; connection = op.get_bind()
    tables = ("institution_onboarding_delivery", "institution_onboarding_outbox", "institution_onboarding_audit", "institution_onboarding_idempotency", "institution_license", "institution_application_revision", "private_file", "institution_application", "institution_onboarding_account", "institution_invitation")
    connection.execute(sa.text("LOCK TABLE " + ", ".join(f"public.{name}" for name in tables) + " IN ACCESS EXCLUSIVE MODE"))
    if any(connection.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM public.{name} LIMIT 1)")).scalar_one() for name in tables):
        raise RuntimeError("Slice 1 downgrade requires empty tables")
    connection.execute(sa.text(f'REVOKE INSERT ("phone", "password_hash", "role", "status", "tenant_id") ON TABLE public."user" FROM "{onboarding}"'))
    _revoke_columns(connection, onboarding, "SELECT", "user", ("id",))
    _revoke_columns(connection, onboarding, "SELECT", "platform_org", ("id","org_type","status"))
    _revoke_columns(connection, reviewer, "SELECT", "user", ("id","tenant_id"))
    connection.execute(sa.text(f'REVOKE UPDATE ("tenant_id") ON TABLE public."user" FROM "{reviewer}"'))
    connection.execute(sa.text(f'REVOKE INSERT ("org_id", "tenant_code", "name", "type", "credit_code", "legal_person_name", "province", "city", "district", "address", "contact_name", "contact_phone", "contact_email", "status", "reviewed_by", "reviewed_at", "approved_at") ON TABLE public."tenant" FROM "{reviewer}"'))
    _revoke_columns(connection, reviewer, "SELECT", "tenant", ("id",))
    connection.execute(sa.text(f'REVOKE USAGE, SELECT ON SEQUENCE public.user_id_seq FROM "{onboarding}"'))
    connection.execute(sa.text(f'REVOKE USAGE, SELECT ON SEQUENCE public.tenant_id_seq FROM "{reviewer}"'))
    for role in roles:
        for table in tables:
            connection.execute(sa.text(f'REVOKE ALL ON public.{table} FROM "{role}"'))
        connection.execute(sa.text(f'REVOKE ALL ON SEQUENCE public.institution_onboarding_audit_audit_id_seq FROM "{role}"'))
        connection.execute(sa.text(f'REVOKE USAGE ON SCHEMA public FROM "{role}"'))
    for table in tables:
        op.drop_table(table, schema="public")
