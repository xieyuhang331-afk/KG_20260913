"""Create the durable P1 registration verified outbox.

Revision ID: 20260808_0011
Revises: 20260807_0010
Create Date: 2026-08-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260808_0011"
down_revision = "20260807_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_verification_decision",
        sa.Column("authority_decision_key", sa.CHAR(length=64)),
        schema="public",
    )
    op.add_column(
        "identity_verification_decision",
        sa.Column(
            "registration_event_id",
            postgresql.UUID(as_uuid=True),
        ),
        schema="public",
    )
    op.create_check_constraint(
        op.f(
            "ck_identity_verification_decision_"
            "identity_verification_event_identity_complete"
        ),
        "identity_verification_decision",
        "(authority_decision_key IS NULL AND registration_event_id IS NULL) "
        "OR (authority_decision_key IS NOT NULL "
        "AND registration_event_id IS NOT NULL)",
        schema="public",
    )
    op.create_check_constraint(
        op.f(
            "ck_identity_verification_decision_"
            "identity_verification_authority_key_sha256"
        ),
        "identity_verification_decision",
        "authority_decision_key IS NULL OR "
        "authority_decision_key ~ '^[0-9a-f]{64}$'",
        schema="public",
    )
    op.create_unique_constraint(
        "uq_identity_verification_event_identity",
        "identity_verification_decision",
        (
            "decision_ref",
            "authority_decision_key",
            "registration_event_id",
            "facts_version",
        ),
        schema="public",
    )
    op.create_index(
        "uq_identity_verification_authority_key_present",
        "identity_verification_decision",
        ("authority_decision_key",),
        unique=True,
        schema="public",
        postgresql_where=sa.text("authority_decision_key IS NOT NULL"),
    )
    op.create_index(
        "uq_identity_verification_registration_event_present",
        "identity_verification_decision",
        ("registration_event_id",),
        unique=True,
        schema="public",
        postgresql_where=sa.text("registration_event_id IS NOT NULL"),
    )

    op.create_table(
        "registration_verified_outbox",
        sa.Column(
            "outbox_record_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "event_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "semantic_idempotency_key",
            sa.String(length=192),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("event_schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.BigInteger(), nullable=False),
        sa.Column(
            "verification_decision_ref",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "authority_decision_key", sa.CHAR(length=64), nullable=False
        ),
        sa.Column("facts_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("trace_ref", postgresql.UUID(as_uuid=True)),
        sa.Column("payload_digest", sa.CHAR(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column(
            "lease_generation",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error_category", sa.String(length=40)),
        sa.Column("last_error_code", sa.String(length=64)),
        sa.Column("last_error_digest", sa.CHAR(length=64)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "outbox_record_id",
            name=op.f("pk_registration_verified_outbox"),
        ),
        sa.UniqueConstraint(
            "event_id", name="uq_registration_verified_outbox_event"
        ),
        sa.UniqueConstraint(
            "semantic_idempotency_key",
            name="uq_registration_verified_outbox_semantic_key",
        ),
        sa.UniqueConstraint(
            "verification_decision_ref",
            name="uq_registration_verified_outbox_verification",
        ),
        sa.UniqueConstraint(
            "authority_decision_key",
            name="uq_registration_verified_outbox_authority",
        ),
        sa.UniqueConstraint(
            "event_type",
            "source_system",
            "source_ref",
            "verification_decision_ref",
            name="uq_registration_verified_outbox_source_decision",
        ),
        sa.ForeignKeyConstraint(
            (
                "verification_decision_ref",
                "authority_decision_key",
                "event_id",
                "facts_version",
            ),
            (
                "public.identity_verification_decision.decision_ref",
                "public.identity_verification_decision.authority_decision_key",
                "public.identity_verification_decision.registration_event_id",
                "public.identity_verification_decision.facts_version",
            ),
            name="fk_registration_verified_outbox_verification_identity",
        ),
        sa.CheckConstraint(
            "event_type = 'identity.registration.verification_verified'",
            name=op.f("ck_registration_verified_outbox_event_type"),
        ),
        sa.CheckConstraint(
            "event_schema_version = 1",
            name=op.f("ck_registration_verified_outbox_schema_version"),
        ),
        sa.CheckConstraint(
            "source_system = 'P1_USER'",
            name=op.f("ck_registration_verified_outbox_source_system"),
        ),
        sa.CheckConstraint(
            "source_ref > 0",
            name=op.f("ck_registration_verified_outbox_source_positive"),
        ),
        sa.CheckConstraint(
            "facts_version > 0",
            name=op.f("ck_registration_verified_outbox_facts_positive"),
        ),
        sa.CheckConstraint(
            "authority_decision_key ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_registration_verified_outbox_authority_sha256"),
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_registration_verified_outbox_payload_sha256"),
        ),
        sa.CheckConstraint(
            "semantic_idempotency_key = "
            "'identity.registration.verification_verified:v1:p1_user:' "
            "|| source_ref::text || ':authority:' "
            "|| authority_decision_key",
            name=op.f("ck_registration_verified_outbox_semantic_key"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'delivered', "
            "'review_required', 'dead_letter')",
            name=op.f("ck_registration_verified_outbox_status_closed"),
        ),
        sa.CheckConstraint(
            "attempt_count BETWEEN 0 AND 8",
            name=op.f("ck_registration_verified_outbox_attempt_budget"),
        ),
        sa.CheckConstraint(
            "lease_generation >= 0",
            name=op.f("ck_registration_verified_outbox_lease_generation"),
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_owner IS NOT NULL "
            "AND locked_until IS NOT NULL AND lease_generation > 0) OR "
            "(status <> 'processing' AND lease_owner IS NULL "
            "AND locked_until IS NULL)",
            name=op.f("ck_registration_verified_outbox_lease_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'delivered' AND delivered_at IS NOT NULL) OR "
            "(status <> 'delivered' AND delivered_at IS NULL)",
            name=op.f("ck_registration_verified_outbox_delivered_shape"),
        ),
        sa.CheckConstraint(
            "(last_error_category IS NULL AND last_error_code IS NULL "
            "AND last_error_digest IS NULL) OR "
            "(last_error_category IS NOT NULL "
            "AND last_error_code IS NOT NULL "
            "AND last_error_digest IS NOT NULL)",
            name=op.f("ck_registration_verified_outbox_error_shape"),
        ),
        sa.CheckConstraint(
            "last_error_category IS NULL OR last_error_category IN "
            "('RETRYABLE', 'REVIEW_REQUIRED', 'PERMANENT', "
            "'INTERNAL_UNKNOWN', 'LEASE')",
            name=op.f("ck_registration_verified_outbox_error_category"),
        ),
        sa.CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN "
            "('DEPENDENCY_UNAVAILABLE', 'RATE_LIMITED', "
            "'TRANSIENT_TRANSPORT', 'ORCHESTRATOR_RETRYABLE', "
            "'RETRY_EXHAUSTED', 'ELIGIBILITY_PROOF_MISSING', "
            "'ELIGIBILITY_STALE', 'ELIGIBILITY_INCONSISTENT', "
            "'BOOTSTRAP_CONFLICT', 'OUTCOME_UNKNOWN_UNCONFIRMED', "
            "'P1_VERIFICATION_OUTBOX_INCONSISTENT', "
            "'UNSUPPORTED_EVENT_TYPE', 'UNSUPPORTED_SCHEMA_VERSION', "
            "'INVALID_ENVELOPE', 'UNEXPECTED_INTERNAL', 'LEASE_LOST')",
            name=op.f("ck_registration_verified_outbox_error_code"),
        ),
        sa.CheckConstraint(
            "last_error_digest IS NULL OR "
            "last_error_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_registration_verified_outbox_error_digest"),
        ),
        sa.CheckConstraint(
            "updated_at >= created_at",
            name=op.f("ck_registration_verified_outbox_timestamps"),
        ),
        schema="public",
    )
    op.create_index(
        "ix_registration_verified_outbox_schedule",
        "registration_verified_outbox",
        ("status", "available_at", "created_at"),
        schema="public",
    )
    op.create_index(
        "ix_registration_verified_outbox_expired_lease",
        "registration_verified_outbox",
        ("status", "locked_until"),
        schema="public",
    )
    op.create_index(
        "ix_registration_verified_outbox_source_verification",
        "registration_verified_outbox",
        ("source_ref", "verification_decision_ref"),
        schema="public",
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE public.identity_verification_decision, "
            "public.registration_verified_outbox "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    outbox_has_rows = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM "
            "public.registration_verified_outbox LIMIT 1)"
        )
    ).scalar_one()
    if outbox_has_rows:
        raise RuntimeError(
            "refusing to downgrade: registration durable outbox "
            "is not empty"
        )

    verification_identity_exists = connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM "
            "public.identity_verification_decision WHERE "
            "authority_decision_key IS NOT NULL OR "
            "registration_event_id IS NOT NULL LIMIT 1)"
        )
    ).scalar_one()
    if verification_identity_exists:
        raise RuntimeError(
            "refusing to downgrade: verification event identity "
            "is populated"
        )

    op.drop_table("registration_verified_outbox", schema="public")
    op.drop_index(
        "uq_identity_verification_registration_event_present",
        table_name="identity_verification_decision",
        schema="public",
    )
    op.drop_index(
        "uq_identity_verification_authority_key_present",
        table_name="identity_verification_decision",
        schema="public",
    )
    op.drop_constraint(
        "uq_identity_verification_event_identity",
        "identity_verification_decision",
        type_="unique",
        schema="public",
    )
    op.drop_constraint(
        op.f(
            "ck_identity_verification_decision_"
            "identity_verification_authority_key_sha256"
        ),
        "identity_verification_decision",
        type_="check",
        schema="public",
    )
    op.drop_constraint(
        op.f(
            "ck_identity_verification_decision_"
            "identity_verification_event_identity_complete"
        ),
        "identity_verification_decision",
        type_="check",
        schema="public",
    )
    op.drop_column(
        "identity_verification_decision",
        "registration_event_id",
        schema="public",
    )
    op.drop_column(
        "identity_verification_decision",
        "authority_decision_key",
        schema="public",
    )
