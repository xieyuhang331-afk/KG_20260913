"""create health profile

Revision ID: 20260728_0005
Revises: 20260728_0004
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260728_0005"
down_revision = "20260728_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_profile",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("gender", sa.String(length=5), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("height", sa.Numeric(5, 1), nullable=True),
        sa.Column("weight", sa.Numeric(5, 1), nullable=True),
        sa.Column("blood_type", sa.String(length=5), nullable=True),
        sa.Column("medical_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("allergy_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("family_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("smoking", sa.String(length=10), nullable=True),
        sa.Column("drinking", sa.String(length=10), nullable=True),
        sa.Column("symptoms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sleep_quality", sa.String(length=50), nullable=True),
        sa.Column("bowel_urination", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("health_profile")
