"""Add core tenant and user baseline tables.

Revision ID: 20260728_0002_core_baseline
Revises: 20260727_0001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0002_core_baseline"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


user_role = postgresql.ENUM(
    "super_admin",
    "province_admin",
    "city_admin",
    "sys_admin",
    "expert",
    "org_admin",
    "org_operator",
    "therapist",
    "host",
    "member",
    name="user_role",
    create_type=False,
)
tenant_status = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    "active",
    "closed",
    name="tenant_status",
    create_type=False,
)
user_status = postgresql.ENUM("active", "disabled", "suspended", name="user_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    user_role.create(bind, checkfirst=True)
    tenant_status.create(bind, checkfirst=True)
    user_status.create(bind, checkfirst=True)

    op.create_table(
        "platform_org",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("org_name", sa.String(length=100), nullable=False),
        sa.Column("org_code", sa.String(length=50), nullable=False),
        sa.Column("org_type", sa.String(length=20), nullable=False),
        sa.Column("org_path", sa.String(length=500), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("contact_name", sa.String(length=50), nullable=True),
        sa.Column("contact_phone", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'active'"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["platform_org.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_code"),
    )

    op.create_table(
        "tenant",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("org_id", sa.BigInteger(), nullable=True),
        sa.Column("tenant_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("short_name", sa.String(length=50), nullable=True),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("credit_code", sa.String(length=18), nullable=True),
        sa.Column("license_no", sa.String(length=50), nullable=True),
        sa.Column("license_image", sa.String(length=500), nullable=True),
        sa.Column("legal_person_name", sa.String(length=50), nullable=True),
        sa.Column("province", sa.String(length=30), nullable=False),
        sa.Column("city", sa.String(length=30), nullable=False),
        sa.Column("district", sa.String(length=30), nullable=True),
        sa.Column("address", sa.String(length=200), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("contact_name", sa.String(length=50), nullable=True),
        sa.Column("contact_phone", sa.String(length=11), nullable=True),
        sa.Column("contact_email", sa.String(length=100), nullable=True),
        sa.Column("logo_url", sa.String(length=500), nullable=True),
        sa.Column("grade", sa.String(length=10), server_default=sa.text("'standard'"), nullable=True),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", tenant_status, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("business_hours", sa.String(length=50), nullable=True),
        sa.Column("photos", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["platform_org.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credit_code"),
        sa.UniqueConstraint("tenant_code"),
    )
    op.create_index("idx_tenant_city", "tenant", ["city"])
    op.create_index("idx_tenant_grade", "tenant", ["grade"])
    op.create_index("idx_tenant_type_status", "tenant", ["type", "status"])

    op.create_table(
        "user",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("phone", sa.String(length=11), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("real_name", sa.String(length=50), nullable=True),
        sa.Column("id_card", sa.String(length=18), nullable=True),
        sa.Column("role", user_role, nullable=False),
        sa.Column("user_status", sa.String(length=20), server_default=sa.text("'customer'"), nullable=True),
        sa.Column("tenant_id", sa.BigInteger(), nullable=True),
        sa.Column("gender", sa.String(length=5), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("verify_status", sa.String(length=20), nullable=True),
        sa.Column("therapist_level", sa.String(length=10), nullable=True),
        sa.Column("specialties", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("violation_count", sa.Integer(), server_default=sa.text("0"), nullable=True),
        sa.Column("status", user_status, server_default=sa.text("'active'"), nullable=True),
        sa.Column("therapist_status", sa.String(length=20), server_default=sa.text("'available'"), nullable=True),
        sa.Column("exited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_reason", sa.String(length=50), nullable=True),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("idx_user_role", "user", ["role"])
    op.create_index("idx_user_status", "user", ["user_status"])
    op.create_index("idx_user_tenant", "user", ["tenant_id"])

    op.create_foreign_key("fk_tenant_reviewed_by_user", "tenant", "user", ["reviewed_by"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_tenant_reviewed_by_user", "tenant", type_="foreignkey")

    op.drop_index("idx_user_tenant", table_name="user")
    op.drop_index("idx_user_status", table_name="user")
    op.drop_index("idx_user_role", table_name="user")
    op.drop_table("user")

    op.drop_index("idx_tenant_type_status", table_name="tenant")
    op.drop_index("idx_tenant_grade", table_name="tenant")
    op.drop_index("idx_tenant_city", table_name="tenant")
    op.drop_table("tenant")

    op.drop_table("platform_org")

    bind = op.get_bind()
    user_status.drop(bind, checkfirst=True)
    tenant_status.drop(bind, checkfirst=True)
    user_role.drop(bind, checkfirst=True)
