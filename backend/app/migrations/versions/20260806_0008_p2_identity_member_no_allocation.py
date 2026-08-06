"""Create the P2 identity member number allocation table.

Revision ID: 20260806_0008
Revises: 20260803_0007
Create Date: 2026-08-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260806_0008"
down_revision = "20260803_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "member_no_allocation",
        sa.Column(
            "allocation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "allocation_scope",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "source_system",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("source_ref", sa.BigInteger(), nullable=False),
        sa.Column("member_no", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "request_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint(
            "allocation_id",
            name=op.f("pk_member_no_allocation"),
        ),
        sa.UniqueConstraint(
            "allocation_scope",
            "source_system",
            "source_ref",
            name=op.f("uq_member_no_allocation_source"),
        ),
        sa.UniqueConstraint(
            "member_no",
            name=op.f("uq_member_no_allocation_member_no"),
        ),
        sa.CheckConstraint(
            "allocation_scope = 'registration_bootstrap'",
            name=op.f(
                "ck_member_no_allocation_scope_registration_bootstrap"
            ),
        ),
        sa.CheckConstraint(
            "source_system = 'p1_user'",
            name=op.f("ck_member_no_allocation_source_system_p1_user"),
        ),
        sa.CheckConstraint(
            "source_ref > 0",
            name=op.f("ck_member_no_allocation_source_ref_positive"),
        ),
        sa.CheckConstraint(
            "member_no ~ "
            "'^M[0123456789ABCDEFGHJKMNPQRSTVWXYZ]{20}$'",
            name=op.f("ck_member_no_allocation_member_no_format"),
        ),
        sa.CheckConstraint(
            "state = 'allocated'",
            name=op.f("ck_member_no_allocation_state_allocated"),
        ),
        sa.CheckConstraint(
            "version = 1",
            name=op.f("ck_member_no_allocation_version_one"),
        ),
        sa.CheckConstraint(
            "updated_at = created_at",
            name=op.f("ck_member_no_allocation_timestamps_immutable"),
        ),
        schema="identity",
    )


def downgrade() -> None:
    has_rows = op.get_bind().execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM identity.member_no_allocation LIMIT 1"
            ")"
        )
    ).scalar_one()
    if has_rows:
        raise RuntimeError(
            "refusing to downgrade: "
            "identity.member_no_allocation is not empty"
        )

    op.drop_table("member_no_allocation", schema="identity")
