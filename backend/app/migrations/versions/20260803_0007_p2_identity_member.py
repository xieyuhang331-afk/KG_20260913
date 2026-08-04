"""Create the P2 identity member table.

Revision ID: 20260803_0007
Revises: 20260728_0006
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260803_0007"
down_revision = "20260728_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA identity")
    op.create_table(
        "member",
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("member_no", sa.String(length=64), nullable=False),
        sa.Column("creation_source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("member_id", name=op.f("pk_member")),
        sa.UniqueConstraint(
            "member_no",
            name=op.f("uq_member_member_no"),
        ),
        schema="identity",
    )


def downgrade() -> None:
    has_rows = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM identity.member LIMIT 1"
            ")"
        )
    ).scalar_one()
    if has_rows:
        raise RuntimeError(
            "refusing to downgrade: identity.member is not empty"
        )

    op.drop_table("member", schema="identity")
    op.execute("DROP SCHEMA identity")
