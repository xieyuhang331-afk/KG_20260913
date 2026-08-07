"""Create immutable P1 registration eligibility evidence tables.

Revision ID: 20260807_0009
Revises: 20260806_0008
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260807_0009"
down_revision = "20260806_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_verification_decision",
        sa.Column("decision_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_ref", sa.BigInteger(), nullable=False),
        sa.Column("facts_version", sa.BigInteger(), nullable=False),
        sa.Column("verification_epoch", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("evidence_digest", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_ref", sa.String(length=64), nullable=False),
        sa.Column("supersedes_ref", postgresql.UUID(as_uuid=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "decision_ref", name=op.f("pk_identity_verification_decision")
        ),
        sa.UniqueConstraint(
            "user_ref",
            "verification_epoch",
            name=op.f("uq_identity_verification_user_epoch"),
        ),
        sa.UniqueConstraint(
            "user_ref",
            "facts_version",
            name=op.f("uq_identity_verification_user_facts"),
        ),
        sa.UniqueConstraint(
            "supersedes_ref",
            name=op.f("uq_identity_verification_single_successor"),
        ),
        sa.CheckConstraint(
            "user_ref > 0",
            name=op.f("ck_identity_verification_user_positive"),
        ),
        sa.CheckConstraint(
            "facts_version > 0",
            name=op.f("ck_identity_verification_facts_positive"),
        ),
        sa.CheckConstraint(
            "verification_epoch > 0",
            name=op.f("ck_identity_verification_epoch_positive"),
        ),
        sa.CheckConstraint(
            "outcome IN ('verified', 'failed')",
            name=op.f("ck_identity_verification_outcome_closed"),
        ),
        schema="public",
    )
    op.create_table(
        "user_account_classification_decision",
        sa.Column("decision_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_ref", sa.BigInteger(), nullable=False),
        sa.Column("facts_version", sa.BigInteger(), nullable=False),
        sa.Column("classification_version", sa.BigInteger(), nullable=False),
        sa.Column("account_class", sa.String(length=24), nullable=False),
        sa.Column("decision_basis_code", sa.String(length=64), nullable=False),
        sa.Column("supersedes_ref", postgresql.UUID(as_uuid=True)),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "decision_ref",
            name=op.f("pk_user_account_classification_decision"),
        ),
        sa.UniqueConstraint(
            "user_ref",
            "classification_version",
            name=op.f("uq_account_classification_user_version"),
        ),
        sa.UniqueConstraint(
            "user_ref",
            "facts_version",
            name=op.f("uq_account_classification_user_facts"),
        ),
        sa.UniqueConstraint(
            "supersedes_ref",
            name=op.f("uq_account_classification_single_successor"),
        ),
        sa.CheckConstraint(
            "user_ref > 0",
            name=op.f("ck_account_classification_user_positive"),
        ),
        sa.CheckConstraint(
            "facts_version > 0",
            name=op.f("ck_account_classification_facts_positive"),
        ),
        sa.CheckConstraint(
            "classification_version > 0",
            name=op.f("ck_account_classification_version_positive"),
        ),
        sa.CheckConstraint(
            "account_class IN ('natural_person', 'staff', 'service', "
            "'test', 'automation', 'unknown')",
            name=op.f("ck_account_classification_class_closed"),
        ),
        schema="public",
    )
    op.create_table(
        "registration_eligibility_decision",
        sa.Column("decision_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_ref", sa.BigInteger(), nullable=False),
        sa.Column("facts_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "verification_decision_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "classification_decision_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("facts_digest", sa.String(length=128), nullable=False),
        sa.Column(
            "p1_projection_digest", sa.String(length=64), nullable=False
        ),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "decision_ref",
            name=op.f("pk_registration_eligibility_decision"),
        ),
        sa.UniqueConstraint(
            "user_ref",
            "facts_version",
            "policy_version",
            name=op.f("uq_registration_eligibility_canonical_key"),
        ),
        sa.CheckConstraint(
            "user_ref > 0",
            name=op.f("ck_registration_eligibility_user_positive"),
        ),
        sa.CheckConstraint(
            "facts_version > 0",
            name=op.f("ck_registration_eligibility_facts_positive"),
        ),
        sa.CheckConstraint(
            "decision IN ('eligible', 'ineligible', 'indeterminate')",
            name=op.f("ck_registration_eligibility_decision_closed"),
        ),
        sa.CheckConstraint(
            "char_length(p1_projection_digest) = 64",
            name=op.f(
                "ck_registration_eligibility_p1_projection_digest_sha256"
            ),
        ),
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table_name in (
        "registration_eligibility_decision",
        "user_account_classification_decision",
        "identity_verification_decision",
    ):
        has_rows = connection.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM public."
                + table_name
                + " LIMIT 1)"
            )
        ).scalar_one()
        if has_rows:
            raise RuntimeError(
                "refusing to downgrade: registration eligibility "
                "evidence tables are not empty"
            )

    op.drop_table("registration_eligibility_decision", schema="public")
    op.drop_table(
        "user_account_classification_decision", schema="public"
    )
    op.drop_table("identity_verification_decision", schema="public")
