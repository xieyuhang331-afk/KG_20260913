"""Create encrypted P1 identity verification submissions.

Revision ID: 20260809_0012
Revises: 20260808_0011
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260809_0012"
down_revision = "20260808_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identity_verification_submission",
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_ref", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("real_name_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("real_name_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("id_card_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("id_card_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("id_card_masked", sa.String(length=18), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=64), nullable=False),
        sa.Column("content_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("id_card_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("idempotency_key_digest", sa.CHAR(length=64), nullable=False),
        sa.Column("consent_version", sa.String(length=64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.BigInteger()),
        sa.Column("decision_basis_code", sa.String(length=64)),
        sa.Column("evidence_digest", sa.CHAR(length=64)),
        sa.Column("rejection_reason_code", sa.String(length=64)),
        sa.PrimaryKeyConstraint("submission_id", name="pk_identity_verification_submission"),
        sa.ForeignKeyConstraint(("user_ref",), ("public.user.id",), name="fk_identity_submission_user"),
        sa.ForeignKeyConstraint(("reviewed_by",), ("public.user.id",), name="fk_identity_submission_reviewer"),
        sa.UniqueConstraint("user_ref", "version", name="uq_identity_submission_user_version"),
        sa.UniqueConstraint("user_ref", "idempotency_key_digest", name="uq_identity_submission_user_idempotency"),
        sa.CheckConstraint("user_ref > 0", name="ck_identity_submission_user_positive"),
        sa.CheckConstraint("version > 0", name="ck_identity_submission_version_positive"),
        sa.CheckConstraint("status IN ('submitted','verified','rejected')", name="ck_identity_submission_status_closed"),
        sa.CheckConstraint("octet_length(real_name_nonce) = 12", name="ck_identity_submission_name_nonce"),
        sa.CheckConstraint("octet_length(id_card_nonce) = 12", name="ck_identity_submission_card_nonce"),
        sa.CheckConstraint("char_length(content_digest) = 64", name="ck_identity_submission_content_digest"),
        sa.CheckConstraint("char_length(id_card_digest) = 64", name="ck_identity_submission_card_digest"),
        sa.CheckConstraint("char_length(idempotency_key_digest) = 64", name="ck_identity_submission_idempotency_digest"),
        sa.CheckConstraint(
            "(status = 'submitted' AND decided_at IS NULL AND reviewed_by IS NULL "
            "AND decision_basis_code IS NULL AND evidence_digest IS NULL "
            "AND rejection_reason_code IS NULL) OR "
            "(status = 'verified' AND decided_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND decision_basis_code = 'APPROVED_OFFLINE_IDENTITY_CHECK' "
            "AND evidence_digest IS NOT NULL AND rejection_reason_code IS NULL) OR "
            "(status = 'rejected' AND decided_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND decision_basis_code = 'REJECTED_OFFLINE_IDENTITY_CHECK' "
            "AND evidence_digest IS NOT NULL AND rejection_reason_code IS NOT NULL)",
            name="ck_identity_submission_terminal_shape",
        ),
        schema="public",
    )
    op.create_index(
        "uq_identity_submission_current",
        "identity_verification_submission",
        ("user_ref",), unique=True, schema="public",
        postgresql_where=sa.text("status IN ('submitted', 'verified')"),
    )
    op.create_index(
        "ix_identity_submission_review_queue",
        "identity_verification_submission",
        ("status", "submitted_at"), schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE public.identity_verification_submission IN ACCESS EXCLUSIVE MODE"
    ))
    has_rows = connection.execute(sa.text(
        "SELECT EXISTS (SELECT 1 FROM public.identity_verification_submission LIMIT 1)"
    )).scalar_one()
    if has_rows:
        raise RuntimeError(
            "refusing to downgrade: identity verification submission is not empty"
        )
    op.drop_table("identity_verification_submission", schema="public")
