from __future__ import annotations

from app.core.model_specs import ColumnSpec, TableSpec, register_core_table_specs


TENANT_TABLE = TableSpec(
    name="tenant",
    module="tenant",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("org_id", "BIGINT", foreign_key="platform_org.id"),
        ColumnSpec("tenant_code", "VARCHAR(32)", nullable=False, unique=True),
        ColumnSpec("name", "VARCHAR(100)", nullable=False),
        ColumnSpec("short_name", "VARCHAR(50)"),
        ColumnSpec("type", "VARCHAR(20)", nullable=False),
        ColumnSpec("credit_code", "VARCHAR(18)", unique=True),
        ColumnSpec("license_no", "VARCHAR(50)"),
        ColumnSpec("license_image", "VARCHAR(500)"),
        ColumnSpec("legal_person_name", "VARCHAR(50)"),
        ColumnSpec("province", "VARCHAR(30)", nullable=False),
        ColumnSpec("city", "VARCHAR(30)", nullable=False),
        ColumnSpec("district", "VARCHAR(30)"),
        ColumnSpec("address", "VARCHAR(200)"),
        ColumnSpec("longitude", "DECIMAL(10,7)"),
        ColumnSpec("latitude", "DECIMAL(10,7)"),
        ColumnSpec("contact_name", "VARCHAR(50)"),
        ColumnSpec("contact_phone", "VARCHAR(11)"),
        ColumnSpec("contact_email", "VARCHAR(100)"),
        ColumnSpec("logo_url", "VARCHAR(500)"),
        ColumnSpec("grade", "VARCHAR(10)", default="standard"),
        ColumnSpec("graded_at", "TIMESTAMPTZ"),
        ColumnSpec("status", "tenant_status", nullable=False, default="pending"),
        ColumnSpec("business_hours", "VARCHAR(50)"),
        ColumnSpec("photos", "JSONB"),
        ColumnSpec("description", "TEXT"),
        ColumnSpec("reviewed_by", "BIGINT", foreign_key="user.id"),
        ColumnSpec("reviewed_at", "TIMESTAMPTZ"),
        ColumnSpec("reject_reason", "TEXT"),
        ColumnSpec("approved_at", "TIMESTAMPTZ"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
        ColumnSpec("updated_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_tenant_type_status", ("type", "status")),
        ("idx_tenant_city", ("city",)),
        ("idx_tenant_grade", ("grade",)),
    ),
)

TENANT_ATTACHMENT_TABLE = TableSpec(
    name="tenant_attachment",
    module="tenant",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("tenant_id", "BIGINT", foreign_key="tenant.id"),
        ColumnSpec("file_type", "VARCHAR(20)", nullable=False),
        ColumnSpec("file_url", "VARCHAR(500)", nullable=False),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
)

TENANT_REVIEW_LOG_TABLE = TableSpec(
    name="tenant_review_log",
    module="tenant",
    columns=(
        ColumnSpec("id", "BIGSERIAL", nullable=False, primary_key=True),
        ColumnSpec("tenant_id", "BIGINT", nullable=False, foreign_key="tenant.id"),
        ColumnSpec("reviewer_id", "BIGINT", foreign_key="user.id"),
        ColumnSpec("action", "VARCHAR(20)", nullable=False),
        ColumnSpec("grade", "VARCHAR(20)"),
        ColumnSpec("comment", "TEXT"),
        ColumnSpec("created_at", "TIMESTAMPTZ", nullable=False, default="NOW()"),
    ),
    indexes=(
        ("idx_tenant_review_log_tenant", ("tenant_id",)),
        ("idx_tenant_review_log_reviewer", ("reviewer_id",)),
    ),
)


class Tenant:
    __tablename__ = TENANT_TABLE.name
    __table_spec__ = TENANT_TABLE


class TenantAttachment:
    __tablename__ = TENANT_ATTACHMENT_TABLE.name
    __table_spec__ = TENANT_ATTACHMENT_TABLE


class TenantReviewLog:
    __tablename__ = TENANT_REVIEW_LOG_TABLE.name
    __table_spec__ = TENANT_REVIEW_LOG_TABLE


register_core_table_specs(TENANT_TABLE, TENANT_ATTACHMENT_TABLE, TENANT_REVIEW_LOG_TABLE)
