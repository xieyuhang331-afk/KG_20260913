"""Add P3 Organization and Health legacy mapping foundations.

Revision ID: 20260812_0016
Revises: 20260811_0015
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260812_0016"
down_revision = "20260811_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_legacy_mapping",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("legacy_tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("legacy_org_id", sa.BigInteger()),
        sa.Column("canonical_organization_id", sa.BigInteger()),
        sa.Column("mapping_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger()),
        sa.CheckConstraint("mapping_version = 1", name="ck_organization_legacy_mapping_version_v1"),
        sa.CheckConstraint(
            "disposition IN ('MAPPED','UNMAPPED','REVIEW_REQUIRED','CONFLICT','BLOCKED')",
            name="ck_organization_legacy_mapping_disposition",
        ),
        sa.CheckConstraint(
            "(disposition='MAPPED' AND reason_code='MAPPED_EXACT') OR "
            "(disposition='UNMAPPED' AND reason_code IN ('SOURCE_ORG_MISSING','TARGET_NOT_FOUND')) OR "
            "(disposition='REVIEW_REQUIRED' AND reason_code IN ('TARGET_LEGACY','TARGET_ARCHIVED')) OR "
            "(disposition='CONFLICT' AND reason_code IN ('SOURCE_DRIFT','TARGET_CONFLICT')) OR "
            "(disposition='BLOCKED' AND reason_code IN ('TARGET_CHAIN_INVALID','SOURCE_CORRUPTED'))",
            name="ck_organization_legacy_mapping_reason",
        ),
        sa.CheckConstraint(
            "source_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_organization_legacy_mapping_fingerprint_hex",
        ),
        sa.CheckConstraint(
            "(disposition='MAPPED' AND legacy_org_id IS NOT NULL "
            "AND canonical_organization_id IS NOT NULL "
            "AND legacy_org_id=canonical_organization_id) OR "
            "(disposition<>'MAPPED' AND canonical_organization_id IS NULL)",
            name="ck_organization_legacy_mapping_target_pair",
        ),
        sa.ForeignKeyConstraint(
            ("legacy_tenant_id",), ("public.tenant.id",),
            name="fk_organization_legacy_mapping_tenant", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("canonical_organization_id",), ("public.platform_org.id",),
            name="fk_organization_legacy_mapping_canonical_org", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("created_by",), ("public.user.id",),
            name="fk_organization_legacy_mapping_created_by", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_legacy_mapping"),
        sa.UniqueConstraint(
            "legacy_tenant_id", "mapping_version",
            name="uq_organization_legacy_mapping_source_version",
        ),
        schema="public",
    )
    op.create_index(
        "idx_organization_legacy_mapping_batch_source",
        "organization_legacy_mapping", ("batch_id", "legacy_tenant_id"), schema="public",
    )
    op.create_index(
        "idx_organization_legacy_mapping_disposition",
        "organization_legacy_mapping", ("mapping_version", "disposition", "legacy_tenant_id"), schema="public",
    )
    op.create_index(
        "idx_organization_legacy_mapping_target",
        "organization_legacy_mapping", ("canonical_organization_id",), schema="public",
        postgresql_where=sa.text("canonical_organization_id IS NOT NULL"),
    )

    op.create_table(
        "health_indicator_legacy_mapping",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("legacy_indicator_id", sa.BigInteger(), nullable=False),
        sa.Column("legacy_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_fact_id", sa.BigInteger()),
        sa.Column("mapping_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_fingerprint", sa.CHAR(64), nullable=False),
        sa.Column("digest_key_id", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.BigInteger()),
        sa.CheckConstraint("mapping_version = 1", name="ck_health_indicator_legacy_mapping_version_v1"),
        sa.CheckConstraint(
            "disposition IN ('MAPPED','UNMAPPED','REVIEW_REQUIRED','CONFLICT','BLOCKED')",
            name="ck_health_indicator_legacy_mapping_disposition",
        ),
        sa.CheckConstraint(
            "(disposition='MAPPED' AND reason_code='MAPPED_EXACT') OR "
            "(disposition='UNMAPPED' AND reason_code IN "
            "('INDICATOR_NOT_OPEN','UNIT_MISMATCH','VALUE_INVALID','SOURCE_INVALID','TIME_INVALID','SUBJECT_MISSING')) OR "
            "(disposition='REVIEW_REQUIRED' AND reason_code='SOURCE_AUTHORITY_UNVERIFIED') OR "
            "(disposition='CONFLICT' AND reason_code IN ('SOURCE_DRIFT','CANONICAL_CONFLICT')) OR "
            "(disposition='BLOCKED' AND reason_code='SOURCE_CORRUPTED')",
            name="ck_health_indicator_legacy_mapping_reason",
        ),
        sa.CheckConstraint(
            "source_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_health_indicator_legacy_mapping_fingerprint_hex",
        ),
        sa.CheckConstraint(
            "(disposition='MAPPED' AND canonical_fact_id IS NOT NULL) OR "
            "(disposition<>'MAPPED' AND canonical_fact_id IS NULL)",
            name="ck_health_indicator_legacy_mapping_target_pair",
        ),
        sa.ForeignKeyConstraint(
            ("canonical_fact_id",), ("public.canonical_health_fact.id",),
            name="fk_health_indicator_legacy_mapping_fact", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("created_by",), ("public.user.id",),
            name="fk_health_indicator_legacy_mapping_created_by", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_health_indicator_legacy_mapping"),
        sa.UniqueConstraint(
            "legacy_indicator_id", "legacy_recorded_at", "mapping_version",
            name="uq_health_indicator_legacy_mapping_source_version",
        ),
        schema="public",
    )
    op.create_index(
        "idx_health_indicator_legacy_mapping_batch_source",
        "health_indicator_legacy_mapping", ("batch_id", "legacy_recorded_at", "legacy_indicator_id"), schema="public",
    )
    op.create_index(
        "idx_health_indicator_legacy_mapping_disposition",
        "health_indicator_legacy_mapping",
        ("mapping_version", "disposition", "legacy_recorded_at", "legacy_indicator_id"), schema="public",
    )
    op.create_index(
        "uq_health_indicator_legacy_mapping_fact_version",
        "health_indicator_legacy_mapping", ("canonical_fact_id", "mapping_version"),
        unique=True, schema="public", postgresql_where=sa.text("canonical_fact_id IS NOT NULL"),
    )

    op.execute("REVOKE ALL ON TABLE public.organization_legacy_mapping FROM PUBLIC")
    op.execute("REVOKE ALL ON SEQUENCE public.organization_legacy_mapping_id_seq FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE public.health_indicator_legacy_mapping FROM PUBLIC")
    op.execute("REVOKE ALL ON SEQUENCE public.health_indicator_legacy_mapping_id_seq FROM PUBLIC")


def _assert_downgrade_safe(connection) -> None:
    connection.execute(sa.text("LOCK TABLE public.organization_legacy_mapping IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE public.health_indicator_legacy_mapping IN ACCESS EXCLUSIVE MODE"))
    connection.execute(sa.text("LOCK TABLE public.operation_log IN SHARE ROW EXCLUSIVE MODE"))
    exists = connection.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM public.organization_legacy_mapping "
            "UNION ALL SELECT 1 FROM public.health_indicator_legacy_mapping "
            "UNION ALL SELECT 1 FROM public.operation_log "
            "WHERE module IN ('organization_mapping','health_fact_mapping')"
            ")"
        )
    ).scalar_one()
    if exists:
        raise RuntimeError("refusing to downgrade: legacy mapping facts exist")


def downgrade() -> None:
    _assert_downgrade_safe(op.get_bind())
    op.drop_index("uq_health_indicator_legacy_mapping_fact_version", table_name="health_indicator_legacy_mapping", schema="public")
    op.drop_index("idx_health_indicator_legacy_mapping_disposition", table_name="health_indicator_legacy_mapping", schema="public")
    op.drop_index("idx_health_indicator_legacy_mapping_batch_source", table_name="health_indicator_legacy_mapping", schema="public")
    op.drop_index("idx_organization_legacy_mapping_target", table_name="organization_legacy_mapping", schema="public")
    op.drop_index("idx_organization_legacy_mapping_disposition", table_name="organization_legacy_mapping", schema="public")
    op.drop_index("idx_organization_legacy_mapping_batch_source", table_name="organization_legacy_mapping", schema="public")
    op.drop_table("health_indicator_legacy_mapping", schema="public")
    op.drop_table("organization_legacy_mapping", schema="public")
