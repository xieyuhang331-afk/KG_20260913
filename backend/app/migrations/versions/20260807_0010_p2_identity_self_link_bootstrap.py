"""Create immutable Identity self-link and registration bootstrap tables.

Revision ID: 20260807_0010
Revises: 20260807_0009
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260807_0010"
down_revision = "20260807_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_member_self_link",
        sa.Column("link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_ref", sa.BigInteger(), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "eligibility_decision_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "establishment_basis", sa.String(length=48), nullable=False
        ),
        sa.Column(
            "establishment_record_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "link_id", name=op.f("pk_user_member_self_link")
        ),
        sa.UniqueConstraint(
            "user_ref", name=op.f("uq_user_member_self_link_user")
        ),
        sa.UniqueConstraint(
            "member_id", name=op.f("uq_user_member_self_link_member")
        ),
        sa.CheckConstraint(
            "source = 'REGISTRATION_VERIFIED'",
            name=op.f("ck_user_member_self_link_source_registration_verified"),
        ),
        sa.CheckConstraint(
            "establishment_basis = 'REGISTRATION_VERIFIED_BOOTSTRAP'",
            name=op.f(
                "ck_user_member_self_link_establishment_basis_registration_verified_bootstrap"
            ),
        ),
        sa.CheckConstraint(
            "user_ref > 0",
            name=op.f("ck_user_member_self_link_user_ref_positive"),
        ),
        schema="identity",
    )
    op.create_table(
        "registration_bootstrap_record",
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_ref", sa.BigInteger(), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("self_link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "registration_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "eligibility_decision_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "member_no_allocation_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("member_no", sa.String(length=64), nullable=False),
        sa.Column("bootstrap_scope", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "record_id", name=op.f("pk_registration_bootstrap_record")
        ),
        sa.UniqueConstraint(
            "bootstrap_scope",
            "source_system",
            "source_ref",
            name=op.f("uq_registration_bootstrap_canonical_source"),
        ),
        sa.UniqueConstraint(
            "member_id", name=op.f("uq_registration_bootstrap_member")
        ),
        sa.UniqueConstraint(
            "self_link_id", name=op.f("uq_registration_bootstrap_self_link")
        ),
        sa.UniqueConstraint(
            "member_no_allocation_ref",
            name=op.f("uq_registration_bootstrap_allocation"),
        ),
        sa.CheckConstraint(
            "source = 'REGISTRATION_VERIFIED'",
            name=op.f(
                "ck_registration_bootstrap_source_registration_verified"
            ),
        ),
        sa.CheckConstraint(
            "user_ref > 0",
            name=op.f("ck_registration_bootstrap_user_ref_positive"),
        ),
        sa.CheckConstraint(
            "bootstrap_scope = 'REGISTRATION_VERIFIED'",
            name=op.f(
                "ck_registration_bootstrap_bootstrap_scope_registration_verified"
            ),
        ),
        sa.CheckConstraint(
            "source_system = 'P1_USER'",
            name=op.f("ck_registration_bootstrap_source_system_p1_user"),
        ),
        sa.CheckConstraint(
            "source_ref = user_ref AND source_ref > 0",
            name=op.f("ck_registration_bootstrap_source_ref_matches_user"),
        ),
        sa.CheckConstraint(
            "decision = 'APPROVED'",
            name=op.f("ck_registration_bootstrap_decision_approved"),
        ),
        schema="identity",
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in (
        "registration_bootstrap_record",
        "user_member_self_link",
    ):
        has_rows = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM identity."
                + table_name
                + " LIMIT 1)"
            )
        ).scalar_one()
        if has_rows:
            raise RuntimeError(
                "refusing to downgrade: Identity bootstrap tables are not empty"
            )

    op.drop_table("registration_bootstrap_record", schema="identity")
    op.drop_table("user_member_self_link", schema="identity")
