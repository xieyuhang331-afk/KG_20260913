"""Create immutable member detection reports for self-service reads.

Revision ID: 20260809_0013
Revises: 20260809_0012
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260809_0013"
down_revision = "20260809_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "detection_report",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("store_id", sa.BigInteger()),
        sa.Column("report_type", sa.String(length=32), nullable=False),
        sa.Column("detection_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("view_status", sa.String(length=16), server_default="unread", nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("report_schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("report_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_detection_report"),
        sa.ForeignKeyConstraint(("user_id",), ("public.user.id",), name="fk_detection_report_user"),
        sa.ForeignKeyConstraint(("store_id",), ("public.tenant.id",), name="fk_detection_report_store"),
        sa.CheckConstraint(
            "report_type IN ('initial_screening','store_retest','home_self_test')",
            name="ck_detection_report_type",
        ),
        sa.CheckConstraint(
            "view_status IN ('unread','read')",
            name="ck_detection_report_view_status",
        ),
        sa.CheckConstraint(
            "report_schema_version > 0",
            name="ck_detection_report_schema_version_positive",
        ),
        schema="public",
    )
    op.create_index(
        "idx_detection_report_user_time",
        "detection_report",
        ("user_id", sa.text("detection_time DESC"), sa.text("id DESC")),
        schema="public",
    )
    op.create_index(
        "idx_detection_report_user_type_time",
        "detection_report",
        ("user_id", "report_type", sa.text("detection_time DESC"), sa.text("id DESC")),
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE public.detection_report IN ACCESS EXCLUSIVE MODE"))
    has_rows = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM public.detection_report LIMIT 1)")
    ).scalar_one()
    if has_rows:
        raise RuntimeError("refusing to downgrade: detection report is not empty")
    op.drop_table("detection_report", schema="public")
