"""Add F001 tenant onboarding audit tables.

Revision ID: 20260728_0003
Revises: 20260728_0002_core_baseline
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0003"
down_revision = "20260728_0002_core_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_review_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewer_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("grade", sa.String(length=20), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("action IN ('approved', 'rejected')", name="ck_tenant_review_log_action"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tenant_review_log_reviewer", "tenant_review_log", ["reviewer_id"])
    op.create_index("idx_tenant_review_log_tenant", "tenant_review_log", ["tenant_id"])

    op.create_table(
        "operation_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("operator_id", sa.BigInteger(), nullable=True),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("object_type", sa.String(length=50), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["operator_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_operation_log_module_object", "operation_log", ["module", "object_type", "object_id"])
    op.create_index("idx_operation_log_operator", "operation_log", ["operator_id"])


def downgrade() -> None:
    op.drop_index("idx_operation_log_operator", table_name="operation_log")
    op.drop_index("idx_operation_log_module_object", table_name="operation_log")
    op.drop_table("operation_log")

    op.drop_index("idx_tenant_review_log_tenant", table_name="tenant_review_log")
    op.drop_index("idx_tenant_review_log_reviewer", table_name="tenant_review_log")
    op.drop_table("tenant_review_log")
