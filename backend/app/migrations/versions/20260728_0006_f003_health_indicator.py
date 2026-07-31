"""create health indicator hypertable

Revision ID: 20260728_0006
Revises: 20260728_0005
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260728_0006"
down_revision = "20260728_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.create_table(
        "health_indicator",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=True),
        sa.Column("batch_id", sa.String(length=36), nullable=True),
        sa.Column("indicator_type", sa.String(length=30), nullable=False),
        sa.Column("value", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit", sa.String(length=10), nullable=False),
        sa.Column("source", sa.String(length=20), server_default=sa.text("'APP'"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.CheckConstraint(
            "source IN ('APP', 'STORE', 'DEVICE', 'REPORT')",
            name=op.f("ck_health_indicator_source"),
        ),
        sa.PrimaryKeyConstraint("id", "recorded_at"),
    )
    op.execute(
        """
        SELECT create_hypertable(
            'health_indicator',
            'recorded_at',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
        """
    )
    op.create_index("idx_hi_user_time", "health_indicator", ["user_id", sa.text("recorded_at DESC")])
    op.create_index("idx_hi_type_time", "health_indicator", ["indicator_type", sa.text("recorded_at DESC")])


def downgrade() -> None:
    op.drop_index("idx_hi_type_time", table_name="health_indicator")
    op.drop_index("idx_hi_user_time", table_name="health_indicator")
    op.drop_table("health_indicator")
