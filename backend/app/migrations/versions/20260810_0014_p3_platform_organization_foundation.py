"""Add P3 Organization Foundation V1 governance fields.

Revision ID: 20260810_0014
Revises: 20260809_0013
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_0014"
down_revision = "20260809_0013"
branch_labels = None
depends_on = None


_DOWNGRADE_GUARD_SQL = """
SELECT EXISTS (
    SELECT 1
      FROM public.operation_log
     WHERE (module = 'organization' OR object_type = 'platform_org')
       AND action IN (
           'organization_created',
           'organization_updated',
           'organization_children_reordered',
           'organization_active',
           'organization_inactive'
       )
    UNION ALL
    SELECT 1
      FROM public.platform_org
     WHERE admin_id IS NOT NULL
        OR version <> 1
        OR created_by IS NOT NULL
        OR updated_by IS NOT NULL
)
"""


def upgrade() -> None:
    op.add_column(
        "platform_org",
        sa.Column("admin_id", sa.BigInteger()),
        schema="public",
    )
    op.add_column(
        "platform_org",
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        schema="public",
    )
    op.add_column("platform_org", sa.Column("created_by", sa.BigInteger()), schema="public")
    op.add_column("platform_org", sa.Column("updated_by", sa.BigInteger()), schema="public")
    op.create_foreign_key(
        "fk_platform_org_admin_id_user",
        "platform_org",
        "user",
        ("admin_id",),
        ("id",),
        source_schema="public",
        referent_schema="public",
    )
    op.create_foreign_key(
        "fk_platform_org_created_by_user",
        "platform_org",
        "user",
        ("created_by",),
        ("id",),
        source_schema="public",
        referent_schema="public",
    )
    op.create_foreign_key(
        "fk_platform_org_updated_by_user",
        "platform_org",
        "user",
        ("updated_by",),
        ("id",),
        source_schema="public",
        referent_schema="public",
    )
    op.create_unique_constraint(
        "uq_platform_org_admin_id",
        "platform_org",
        ("admin_id",),
        schema="public",
    )
    op.create_index(
        "idx_platform_org_parent_sort",
        "platform_org",
        ("parent_id", "sort_order", "id"),
        schema="public",
    )
    op.create_index(
        "idx_platform_org_type_status",
        "platform_org",
        ("org_type", "status"),
        schema="public",
    )
    op.execute("REVOKE ALL ON TABLE public.platform_org FROM PUBLIC")
    op.execute("REVOKE ALL ON SEQUENCE public.platform_org_id_seq FROM PUBLIC")


def _assert_downgrade_safe(connection) -> None:
    connection.execute(sa.text("LOCK TABLE public.platform_org IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(_DOWNGRADE_GUARD_SQL)).scalar_one():
        raise RuntimeError("refusing to downgrade: Organization Foundation facts exist")


def downgrade() -> None:
    connection = op.get_bind()
    _assert_downgrade_safe(connection)
    op.drop_index("idx_platform_org_type_status", table_name="platform_org", schema="public")
    op.drop_index("idx_platform_org_parent_sort", table_name="platform_org", schema="public")
    op.drop_constraint("uq_platform_org_admin_id", "platform_org", schema="public", type_="unique")
    op.drop_constraint("fk_platform_org_updated_by_user", "platform_org", schema="public", type_="foreignkey")
    op.drop_constraint("fk_platform_org_created_by_user", "platform_org", schema="public", type_="foreignkey")
    op.drop_constraint("fk_platform_org_admin_id_user", "platform_org", schema="public", type_="foreignkey")
    op.drop_column("platform_org", "updated_by", schema="public")
    op.drop_column("platform_org", "created_by", schema="public")
    op.drop_column("platform_org", "version", schema="public")
    op.drop_column("platform_org", "admin_id", schema="public")
